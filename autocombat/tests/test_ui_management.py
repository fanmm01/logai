"""Source roundtrip, backups and independent tournament advancement."""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from flask import Flask, Blueprint, request
from ui_management import Management, digest, format_allowlist, format_network, parse_allowlist
from ui_store import Store
from ui_effects import START
from ui_tournaments import Tournaments, bracket, participant

ROOT = Path(__file__).resolve().parents[1]


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ('characters_data.py', 'team_tables.py'):
            shutil.copy2(ROOT/name, self.root/name)
        # Exercise the original enable/disable scenarios independently of the
        # user's current browser-edited roster (which may already enable Y13).
        data_path = self.root/'characters_data.py'
        source = data_path.read_text(encoding='utf-8-sig')
        tree = ast.parse(source)
        definitions = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
                for key, value in zip(node.value.keys, node.value.values):
                    if isinstance(key, ast.Constant) and key.value == 'serial' and isinstance(value, ast.Constant):
                        definitions[value.value] = node.targets[0].id
        active = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'ALL_CHARACTERS')
        lines = source.splitlines(keepends=True)
        lines[active.lineno-1:active.end_lineno] = ['ALL_CHARACTERS = [' + ', '.join(definitions[f'Y{i}'] for i in range(1, 13)) + ']\n']
        data_path.write_text(''.join(lines), encoding='utf-8')
        (self.root/'ai_weights.json').write_text('{"Q_solo": {}, "Q_team": {}}', encoding='utf-8')
        spec = importlib.util.spec_from_file_location('test_editor_data', self.root/'characters_data.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.app = Flask(__name__)
        bp = Blueprint('test_management', __name__)
        self.c = dict(body=lambda: request.get_json(), session_id=lambda: 'owner', guard=threading.RLock(),
                      ns={'characters_data_pvp': self.module, '_lock': threading.RLock()})
        self.management = Management(bp, self.c, self.root)
        self.store = Store(self.management.runtime/'test.sqlite3')
        self.tournaments = Tournaments(bp, self.c, self.management, self.store)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_edit_save_backup_restore_and_conflict(self):
        original = (self.root/'characters_data.py').read_bytes()
        data = self.client.get('/api/ui/data').get_json()
        data['characters'][0]['attrs']['体力'] = 43
        data['summons']['血液长枪']['MOV'] = 7
        data['characters'][0]['spells'].append(dict(name='编辑器测试技能', timing='2', category=3,
            effects=[{'type': 3, '客体': 12, '回复hp': '1d6'}]))
        response = self.client.post('/api/ui/data/save', json=data)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        saved = self.client.get('/api/ui/data').get_json()
        self.assertEqual(saved['characters'][0]['attrs']['体力'], 43)
        self.assertEqual(saved['summons']['血液长枪']['MOV'], 7)
        self.assertEqual(saved['characters'][0]['spells'][-1]['effects'][0]['type'], 3)
        self.assertEqual(self.client.post('/api/ui/data/save', json=data).status_code, 409)
        backup = response.get_json()['backup']
        self.assertEqual(self.client.get(f'/api/ui/backups/{backup}/download').status_code, 200)
        self.assertEqual(self.client.post(f'/api/ui/backups/{backup}/restore', json={}).status_code, 200)
        self.assertEqual((self.root/'characters_data.py').read_bytes(), original)
        self.assertEqual(len(self.client.get('/api/ui/backups').get_json()['backups']), 2)

    def test_all_definitions_are_visible_with_separate_enabled_membership(self):
        self.module.STALE_CHARACTER = dict(name='stale', serial='STALE', attrs={})
        data = self.client.get('/api/ui/data').get_json()
        serials = [ch['serial'] for ch in data['characters']]
        self.assertEqual(len(serials), len(set(serials)))
        self.assertTrue({f'Y{i}' for i in range(1, 18)} <= set(serials))
        self.assertNotIn('STALE', serials)
        self.assertEqual(data['enabled_serials'], [ch['serial'] for ch in self.module.ALL_CHARACTERS])
        self.assertTrue(all(f'Y{i}' not in data['enabled_serials'] for i in range(13, 18)))

    def test_disable_edit_enable_reuses_definition_and_survives_reload_and_restore(self):
        original = (self.root/'characters_data.py').read_bytes()
        data = self.client.get('/api/ui/data').get_json()
        data['enabled_serials'].remove('Y1')
        next(ch for ch in data['characters'] if ch['serial'] == 'Y13')['attrs']['体力'] = 44
        response = self.client.post('/api/ui/data/save', json=data)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        backup = response.get_json()['backup']
        saved = self.client.get('/api/ui/data').get_json()
        self.assertNotIn('Y1', saved['enabled_serials'])
        self.assertNotIn('Y13', saved['enabled_serials'])
        self.assertEqual(next(ch for ch in saved['characters'] if ch['serial'] == 'Y13')['attrs']['体力'], 44)
        self.assertNotIn('Y1', [ch['serial'] for ch in self.module.ALL_CHARACTERS])
        saved['enabled_serials'].extend(['Y1', 'Y13'])
        self.assertEqual(self.client.post('/api/ui/data/save', json=saved).status_code, 200)
        self.management.reload()
        enabled = self.client.get('/api/ui/data').get_json()
        self.assertIn('Y13', enabled['enabled_serials'])
        self.assertEqual(sum(ch['serial'] == 'Y13' for ch in enabled['characters']), 1)
        source = (self.root/'characters_data.py').read_text(encoding='utf-8')
        self.assertNotIn('CUSTOM_Y13', source)
        active = next(n for n in ast.parse(source).body if isinstance(n, ast.Assign)
                      and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'ALL_CHARACTERS')
        self.assertIn('GELIYA', [n.id for n in active.value.elts])
        self.assertEqual(self.client.post(f'/api/ui/backups/{backup}/restore', json={}).status_code, 200)
        self.assertEqual((self.root/'characters_data.py').read_bytes(), original)
        self.assertNotIn('Y13', self.client.get('/api/ui/data').get_json()['enabled_serials'])

    def test_invalid_enabled_membership_is_atomic_and_disabled_drafts_are_preserved(self):
        original = (self.root/'characters_data.py').read_bytes()
        data = self.client.get('/api/ui/data').get_json()
        for invalid in ([], ['UNKNOWN'], ['Y1', 'Y1'], 'Y1', [None], [['Y1']]):
            with self.subTest(enabled=invalid):
                payload = dict(data, enabled_serials=invalid)
                self.assertEqual(self.client.post('/api/ui/data/save', json=payload).status_code, 400)
                self.assertEqual((self.root/'characters_data.py').read_bytes(), original)
        data['enabled_serials'].append('Y17')
        rejected = self.client.post('/api/ui/data/save', json=data)
        self.assertEqual(rejected.status_code, 400)
        self.assertIn('Y17', rejected.get_data(as_text=True))
        self.assertEqual((self.root/'characters_data.py').read_bytes(), original)

    def test_legacy_save_preserves_inactive_definitions_and_modern_delete_removes_them(self):
        data = self.client.get('/api/ui/data').get_json()
        active = set(data.pop('enabled_serials'))
        data['characters'] = [ch for ch in data['characters'] if ch['serial'] in active and ch['serial'] != 'Y1']
        self.assertEqual(self.client.post('/api/ui/data/save', json=data).status_code, 200)
        saved = self.client.get('/api/ui/data').get_json()
        self.assertTrue({f'Y{i}' for i in range(13, 18)} <= {ch['serial'] for ch in saved['characters']})
        self.assertNotIn('Y1', {ch['serial'] for ch in saved['characters']})
        saved['characters'] = [ch for ch in saved['characters'] if ch['serial'] != 'Y13']
        saved['characters'].append(dict(name='new', serial='Y99', attrs={}, spells=[]))
        saved['enabled_serials'].append('Y99')
        self.assertEqual(self.client.post('/api/ui/data/save', json=saved).status_code, 200)
        latest = self.client.get('/api/ui/data').get_json()
        self.assertNotIn('Y13', {ch['serial'] for ch in latest['characters']})
        self.assertIn('Y99', latest['enabled_serials'])
        self.assertTrue(hasattr(self.module, 'GELIYA'))
        self.assertNotIn('Y13', latest['reserved'])

    def test_disabled_effects_are_comments_and_round_trip_in_original_order(self):
        baseline_comments = (self.root/'characters_data.py').read_text(encoding='utf-8').count(START)
        data = self.client.get('/api/ui/data').get_json()
        effects = [{'type': 3, '客体': 12, '回复hp': i + 1, '说明': f'效果 {i} 中文🍁 # 注释\n第二行', '_disabled': i in (0, 2, 4)} for i in range(5)]
        data['characters'][0]['spells'].append(dict(name='注释回环测试', timing='2', category=3, effects=effects))
        response = self.client.post('/api/ui/data/save', json=data)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        source = (self.root/'characters_data.py').read_text(encoding='utf-8')
        self.assertEqual(source.count(START), baseline_comments + 3)
        runtime = self.module.ALL_CHARACTERS[0]['spells'][-1]['effects']
        self.assertEqual([effect['回复hp'] for effect in runtime], [2, 4])
        self.assertTrue(all('_disabled' not in effect for effect in runtime))
        saved = self.client.get('/api/ui/data').get_json()
        self.assertEqual(saved['characters'][0]['spells'][-1]['effects'], effects)
        self.management.reload()
        self.assertEqual(self.client.get('/api/ui/data').get_json()['characters'][0]['spells'][-1]['effects'], effects)
        # Editing an unrelated field must retain the commented payloads.
        saved['characters'][0]['attrs']['体力'] += 1
        self.assertEqual(self.client.post('/api/ui/data/save', json=saved).status_code, 200)
        saved = self.client.get('/api/ui/data').get_json()
        restored = saved['characters'][0]['spells'][-1]['effects']
        restored[0]['回复hp'] = 42
        for effect in restored:
            effect['_disabled'] = False
        self.assertEqual(self.client.post('/api/ui/data/save', json=saved).status_code, 200)
        self.assertEqual([e['回复hp'] for e in self.module.ALL_CHARACTERS[0]['spells'][-1]['effects']], [42, 2, 3, 4, 5])
        self.assertEqual((self.root/'characters_data.py').read_text(encoding='utf-8').count(START), baseline_comments)

    def test_all_disabled_effects_are_empty_in_engine_and_excluded_from_training(self):
        import ai_trainer
        import characters_data
        from battle_engine import FullBattleEngine
        data = self.client.get('/api/ui/data').get_json()
        summon = next(ch for ch in data['characters'] if ch['serial'] == 'Y2')
        index = next(i for i, s in enumerate(summon['spells']) if any(e.get('召唤物模板') == '使魔' for e in s['effects']))
        for effect in summon['spells'][index]['effects']:
            effect['_disabled'] = True
        response = self.client.post('/api/ui/data/save', json=data)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        loaded = next(ch for ch in self.module.ALL_CHARACTERS if ch['serial'] == 'Y2')
        self.assertEqual(loaded['spells'][index]['effects'], [])
        engine = FullBattleEngine()
        self.module.load_character_to_engine(engine, loaded, 'commented')
        spell = next(s for s in engine.load_spells('commented') if s['name'] == loaded['spells'][index]['name'])
        self.assertEqual(spell['effects'], [])
        with patch.object(characters_data, 'ALL_CHARACTERS', self.module.ALL_CHARACTERS):
            trainer = ai_trainer.QTrainer()
            trainer.init_characters()
            self.assertNotIn('使魔', trainer.training_summons)
        # Disabled entries consume no engine effect slots and may be drafts.
        saved = self.client.get('/api/ui/data').get_json()
        draft = saved['characters'][0]['spells'][0]
        draft['effects'] = [{'_disabled': True, 'type': 500, '召唤物模板': '未完成模板'} for _ in range(8)]
        self.assertEqual(self.client.post('/api/ui/data/save', json=saved).status_code, 200)
        saved = self.client.get('/api/ui/data').get_json()
        saved['characters'][0]['spells'][0]['effects'][0]['_disabled'] = False
        self.assertEqual(self.client.post('/api/ui/data/save', json=saved).status_code, 400)

    def test_disabled_effect_delete_and_backup_restore_remain_distinct(self):
        baseline_comments = (self.root/'characters_data.py').read_text(encoding='utf-8').count(START)
        data = self.client.get('/api/ui/data').get_json()
        effect = data['characters'][0]['spells'][0]['effects'][0]
        effect['_disabled'] = True
        self.assertEqual(self.client.post('/api/ui/data/save', json=data).status_code, 200)
        backup = self.client.post('/api/ui/backups', json={}).get_json()['id']
        saved = self.client.get('/api/ui/data').get_json()
        saved['characters'][0]['spells'][0]['effects'].pop(0)
        self.assertEqual(self.client.post('/api/ui/data/save', json=saved).status_code, 200)
        self.assertEqual((self.root/'characters_data.py').read_text(encoding='utf-8').count(START), baseline_comments)
        self.assertEqual(self.client.post(f'/api/ui/backups/{backup}/restore', json={}).status_code, 200)
        restored = self.client.get('/api/ui/data').get_json()
        self.assertTrue(restored['characters'][0]['spells'][0]['effects'][0]['_disabled'])

    def test_management_allowlist_defaults_persists_and_can_be_changed_by_allowed_client(self):
        self.assertIn('192.168.1.6', self.client.get('/api/ui/allowlist', environ_overrides={'REMOTE_ADDR': '192.168.1.6'}).get_json()['allowlist'])
        self.assertEqual(self.client.get('/api/ui/allowlist', environ_overrides={'REMOTE_ADDR': '192.168.1.10'}).status_code, 403)
        saved = self.client.post('/api/ui/allowlist', json={'allowlist': '192.168.1.6\n192.168.1.0/24'},
                                 environ_overrides={'REMOTE_ADDR': '192.168.1.6'})
        self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))
        self.assertEqual(saved.get_json()['allowlist'], ['192.168.1.0/24', '192.168.1.6'])
        self.assertTrue((self.management.runtime/'management-allowlist.json').exists())
        self.assertEqual(self.client.get('/api/ui/allowlist', environ_overrides={'REMOTE_ADDR': '192.168.1.10'}).status_code, 200)
        self.assertEqual(self.client.post('/api/ui/allowlist', json={'allowlist': 'not-an-ip'},
                                          environ_overrides={'REMOTE_ADDR': '192.168.1.6'}).status_code, 400)
        removed_default = self.client.post('/api/ui/allowlist', json={'allowlist': '192.168.1.10'},
                                           environ_overrides={'REMOTE_ADDR': '192.168.1.6'})
        self.assertEqual(removed_default.status_code, 200)
        self.assertEqual(removed_default.get_json()['allowlist'], ['192.168.1.10'])
        self.assertEqual(self.client.get('/api/ui/allowlist', environ_overrides={'REMOTE_ADDR': '192.168.1.6'}).status_code, 403)
        reload_bp = Blueprint('test_management_reload', __name__)
        reloaded = Management(reload_bp, self.c, self.root)
        self.assertNotIn('192.168.1.6', [format_network(network) for network in reloaded.allowlist])

    def test_management_allowlist_honors_forwarded_ip_only_from_configured_proxy(self):
        self.management.trusted_proxies = parse_allowlist('127.0.0.1')
        allowed = self.client.get('/api/ui/allowlist', environ_overrides={'REMOTE_ADDR': '127.0.0.1',
            'HTTP_X_FORWARDED_FOR': '192.168.1.6'})
        self.assertEqual(allowed.status_code, 200)
        denied = self.client.get('/api/ui/allowlist', environ_overrides={'REMOTE_ADDR': '192.168.1.10',
            'HTTP_X_FORWARDED_FOR': '192.168.1.6'})
        self.assertEqual(denied.status_code, 403)
        tunneled = self.client.get('/api/ui/allowlist', environ_overrides={'REMOTE_ADDR': '127.0.0.1',
            'HTTP_HOST': 'public-tunnel.example:443'})
        self.assertEqual(tunneled.status_code, 403)

    def test_wildcard_allowlist_allows_ipv4_ipv6_and_tunnel_debug_access(self):
        wildcard = parse_allowlist('*.*.*.*')
        self.assertEqual(format_allowlist(wildcard), ['*.*.*.*'])
        import ipaddress
        for addr in ('192.168.5.23', '203.0.113.99', '2001:db8::50', '::ffff:203.0.113.99'):
            parsed = ipaddress.ip_address(addr)
            if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
                parsed = parsed.ipv4_mapped
            self.assertTrue(any(parsed in network for network in wildcard))
        self.management.allowlist = wildcard
        self.assertEqual(self.client.get('/api/ui/allowlist', environ_overrides={'REMOTE_ADDR': '10.20.30.40'}).status_code, 200)
        self.assertEqual(self.client.get('/api/ui/allowlist', environ_overrides={'REMOTE_ADDR': '127.0.0.1',
            'HTTP_HOST': 'public-tunnel.example:443'}).status_code, 200)
        saved = self.client.post('/api/ui/allowlist', json={'allowlist': '*.*.*.*'},
            environ_overrides={'REMOTE_ADDR': '198.51.100.77'})
        self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))
        self.assertEqual(saved.get_json()['allowlist'], ['*.*.*.*'])
        self.assertEqual(json.loads(self.management.allowlist_path.read_text(encoding='utf-8')), ['*.*.*.*'])
        reload_bp = Blueprint('test_management_wildcard_reload', __name__)
        reloaded = Management(reload_bp, self.c, self.root)
        self.assertEqual(format_allowlist(reloaded.allowlist), ['*.*.*.*'])

    def test_invalid_save_is_atomic_and_remote_management_is_denied(self):
        original = (self.root/'characters_data.py').read_bytes()
        data = self.client.get('/api/ui/data').get_json()
        data['characters'][0]['spells'][0]['effects'][0]['type'] = 500
        self.assertEqual(self.client.post('/api/ui/data/save', json=data).status_code, 400)
        self.assertEqual((self.root/'characters_data.py').read_bytes(), original)
        self.assertEqual(self.client.get('/api/ui/data', environ_overrides={'REMOTE_ADDR': '192.168.1.10'}).status_code, 403)

    def test_legacy_data_star_import_uses_the_canonical_registry(self):
        import characters_data
        namespace = {}
        exec('from characters_data_pvp import *', namespace)
        self.assertIs(namespace['ALL_CHARACTERS'], characters_data.ALL_CHARACTERS)
        self.assertIs(namespace['SUMMON_TEMPLATES'], characters_data.SUMMON_TEMPLATES)

    def tournament(self, fmt='1v1'):
        response = self.client.post('/api/ui/tournaments', json={'format': fmt, 'seed': 12,
            'teams': [['Y1'], ['Y2'], ['Y3'], ['Y4']]})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        t = response.get_json()
        t.update(phase='playoffs', series=bracket(t), status='paused')
        self.tournaments.save(t)
        return t

    def test_single_game_and_series_are_bounded_and_requests_idempotent(self):
        t = self.tournament()
        other = self.tournament('2v2')
        with patch('ui_tournaments.threading.Thread'):
            response = self.client.post(f'/api/ui/tournaments/{t["id"]}/advance', json=dict(op='game', series='SF1', revision=t['revision'], request_id='one'))
            self.assertEqual(response.status_code, 200)
        with patch.object(self.tournaments, 'simulate', return_value={'winner': 'Y', 'rounds': 3}):
            self.tournaments.run(t['id'])
        state = self.store.get('tournament', t['id'])
        self.assertEqual(state['series'][0]['score'], [1, 0])
        self.assertEqual(state['series'][1]['score'], [0, 0])
        self.assertEqual(self.store.get('tournament', other['id'])['series'], other['series'])
        repeat = self.client.post(f'/api/ui/tournaments/{t["id"]}/advance', json=dict(op='game', series='SF1', revision=0, request_id='one'))
        self.assertEqual(repeat.status_code, 200)
        self.assertEqual(repeat.get_json()['series'][0]['score'], [1, 0])
        with patch('ui_tournaments.threading.Thread'):
            response = self.client.post(f'/api/ui/tournaments/{t["id"]}/advance', json=dict(op='series', series='SF1', revision=state['revision'], request_id='series'))
            self.assertEqual(response.status_code, 200)
        with patch.object(self.tournaments, 'simulate', return_value={'winner': 'Y', 'rounds': 3}):
            self.tournaments.run(t['id'])
        state = self.store.get('tournament', t['id'])
        self.assertEqual(state['series'][0]['score'], [4, 0])
        self.assertEqual(state['series'][1]['score'], [0, 0])
        self.assertEqual(state['status'], 'paused')

    def test_tournament_speed_can_change_while_running_without_starting_worker(self):
        t = self.tournament()
        t.update(status='running', operation='turn_game')
        self.tournaments.save(t)
        self.tournaments.threads.add(t['id'])
        try:
            with patch('ui_tournaments.threading.Thread') as thread:
                response = self.client.post(f'/api/ui/tournaments/{t["id"]}/advance',
                    json=dict(op='speed', speed=4, request_id='speed-up'))
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
                thread.assert_not_called()
            state = self.store.get('tournament', t['id'])
            self.assertEqual(state['speed'], 4)
            self.assertEqual(state['status'], 'running')
            invalid = self.client.post(f'/api/ui/tournaments/{t["id"]}/advance',
                json=dict(op='speed', speed=3, request_id='invalid-speed'))
            self.assertEqual(invalid.status_code, 400)
            self.assertEqual(self.store.get('tournament', t['id'])['speed'], 4)
        finally:
            self.tournaments.threads.discard(t['id'])

    def test_step_simulation_delay_uses_saved_tournament_speed(self):
        t = self.tournament()
        t.update(speed=4, status='running', current=dict(replay=[]))
        self.tournaments.save(t)
        proc = type('Process', (), {})()
        proc.stdin = Mock()
        proc.stdout = iter([
            json.dumps({'round': 'round 1'}) + '\n',
            json.dumps({'round': 'round 2'}) + '\n',
            json.dumps({'result': {'winner': 'Y', 'rounds': 2}, 'replay': ['round 1', 'round 2']}) + '\n',
        ])
        try:
            with patch('ui_tournaments.subprocess.Popen', return_value=proc), patch('ui_tournaments.time.sleep') as sleep:
                self.tournaments.simulate(t['id'], t, dict(a=0, b=1, map_size='10x10', seed=1), verbose=True)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [.65 / 4, .65 / 4])
        finally:
            _, err = self.tournaments.workers.pop(t['id'])
            err.close()

    def test_round_only_finishes_current_stage_and_regular_auto_pauses(self):
        t = self.tournament()
        t.update(status='running', operation='round', selected='SF1')
        self.tournaments.save(t)
        with patch.object(self.tournaments, 'simulate', return_value={'winner': 'Y', 'rounds': 2}):
            self.tournaments.run(t['id'])
        state = self.store.get('tournament', t['id'])
        self.assertTrue(all(s['winner'] is not None for s in state['series'][:2]))
        self.assertTrue(all(s['games'] == [] for s in state['series'][2:]))
        t.update(phase='regular', series=[], completed=0, schedule=[dict(a=0, b=1, map_size='10x10', seed=1)])
        self.tournaments.commit(t, t['schedule'][0], None, {'winner': 'Y', 'rounds': 2}, False)
        self.assertEqual(t['phase'], 'playoffs')
        self.assertEqual(t['status'], 'paused')
        self.assertTrue(all(s['games'] == [] for s in t['series']))

    def test_bracket_weighted_playin_dependencies_and_sqlite_recovery(self):
        t = dict(seed=4, standings=[dict(id=i, points=12-i, wins=0) for i in range(12)])
        t['series'] = bracket(t)
        playins = [s for s in t['series'] if s['stage'] == '附加赛']
        self.assertEqual(len(playins), 4)
        self.assertEqual({s['best_of'] for s in playins}, {5})
        self.assertEqual({p for s in playins for p in (s['a'], s['b'])}, set(range(4, 12)))
        qf = next(s for s in t['series'] if s['id'] == 'QF1')
        self.assertIsNone(participant(t, qf['b']))
        first = self.tournament()
        first.update(status='running', current={'series': 'SF1'})
        self.tournaments.save(first)
        bp = Blueprint('recovery', __name__)
        Tournaments(bp, self.c, self.management, Store(self.store.path))
        restored = self.store.get('tournament', first['id'])
        self.assertEqual(restored['status'], 'paused')
        self.assertIsNone(restored['current'])

    def test_direct_game_replay_is_saved_once_separate_from_public_polling_and_private(self):
        t = self.tournament()
        match = dict(a=0, b=3, map_size='10x10', seed=1)
        line = '    第1回合 Y: A(HP:10) | X: B(HP:20)'
        self.tournaments.commit(t, match, 'SF1', dict(winner='Y', rounds=1, _round_replay=[line]), False)
        self.tournaments.save(t)
        record = t['series'][0]['games'][0]
        self.assertNotIn('_round_replay', record['result'])
        public = self.client.get(f'/api/ui/tournaments/{t["id"]}').get_json()
        self.assertNotIn(line, json.dumps(public, ensure_ascii=False))
        replay_id = record['replay_id']
        response = self.client.get(f'/api/ui/tournaments/{t["id"]}/replays/{replay_id}')
        self.assertEqual(response.get_json()['lines'], [line])
        self.assertEqual(response.get_json()['logs'], [])
        other = self.tournament('2v2')
        self.assertEqual(self.client.get(f'/api/ui/tournaments/{other["id"]}/replays/{replay_id}').status_code, 404)
        with patch.dict(self.c, session_id=lambda: 'participant'):
            shared = self.client.get('/api/ui/tournaments').get_json()['tournaments']
            self.assertIn(t['id'], [item['id'] for item in shared])
            fetched = self.client.get(f'/api/ui/tournaments/{t["id"]}')
            self.assertEqual(fetched.status_code, 200)
            self.assertNotIn('owner', fetched.get_json())
            self.assertEqual(self.client.get(f'/api/ui/tournaments/{t["id"]}/replays/{replay_id}').status_code, 200)
            with patch('ui_tournaments.threading.Thread') as thread:
                advanced = self.client.post(f'/api/ui/tournaments/{t["id"]}/advance', json=dict(
                    op='game', series='SF1', revision=t['revision'], request_id='participant-game'))
                self.assertEqual(advanced.status_code, 200, advanced.get_data(as_text=True))
                thread.assert_called_once()
            self.assertIn('participant:participant-game', self.store.get('tournament', t['id'])['requests'])
            self.assertEqual(self.client.get('/api/ui/tournaments').status_code, 200)
        with patch.dict(self.c, session_id=lambda: 'someone-else'):
            with patch('ui_tournaments.threading.Thread') as thread:
                conflicting = self.client.post(f'/api/ui/tournaments/{t["id"]}/advance', json=dict(
                    op='game', series='SF1', revision=self.store.get('tournament', t['id'])['revision'], request_id='second-game'))
                self.assertEqual(conflicting.status_code, 409)
                thread.assert_not_called()
            with patch('ui_tournaments.threading.Thread') as thread:
                speed_change = self.client.post(f'/api/ui/tournaments/{t["id"]}/advance', json=dict(
                    op='speed', speed=2, request_id='participant-speed'))
                self.assertEqual(speed_change.status_code, 200, speed_change.get_data(as_text=True))
                thread.assert_not_called()
            self.assertIn('someone-else:participant-speed', self.store.get('tournament', t['id'])['requests'])
        self.tournaments.commit(t, match, 'SF1', dict(winner='Y', rounds=1,
            _round_replay=[line], _battle_logs=['D100 attack result']), False)
        self.tournaments.save(t)
        detailed = self.client.get(f'/api/ui/tournaments/{t["id"]}/replays/{t["last_replay"]["id"]}').get_json()
        self.assertEqual(detailed['logs'], ['D100 attack result'])

    def test_real_direct_and_step_games_publish_different_live_payloads(self):
        import ai_battle as ab
        for path in ROOT.glob('*.py'):
            if path.name != 'autocombat_backend.py':
                shutil.copy2(path, self.root/path.name)
        for operation in ('game', 'turn_game'):
            with self.subTest(operation=operation):
                t = self.tournament()
                t.update(status='running', operation=operation, selected='SF1')
                self.tournaments.save(t)
                writes = []
                put = self.store.put

                def capture(kind, value):
                    if kind == 'tournament':
                        writes.append(copy.deepcopy(value))
                    return put(kind, value)

                with patch.object(self.store, 'put', side_effect=capture), patch.object(ab, 'MAX_ROUNDS_KNOCKOUT', 2):
                    self.tournaments.run(t['id'])
                saved = self.store.get('tournament', t['id'])
                self.assertEqual(saved['series'][0]['score'][0] + saved['series'][0]['score'][1], 1, saved.get('message'))
                replay = self.store.get('tournament_replay', saved['last_replay']['id'])
                self.assertEqual(len(replay['lines']), 2)
                live_hp = [w['current']['replay'] for w in writes if w.get('current') and w['current'].get('replay')]
                live_logs = [w['logs'] for w in writes if w.get('logs')]
                if operation == 'game':
                    self.assertEqual(live_hp, [])
                    self.assertEqual(live_logs, [])
                    self.assertEqual(replay['logs'], [])
                else:
                    self.assertEqual(live_hp[-1], replay['lines'])
                    self.assertTrue(live_logs)
                    self.assertTrue(any('D100' in line for line in replay['logs']))


if __name__ == '__main__':
    unittest.main()
