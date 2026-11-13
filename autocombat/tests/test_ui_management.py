"""Source roundtrip, backups and independent tournament advancement."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from flask import Flask, Blueprint, request
from ui_management import Management, digest
from ui_store import Store
from ui_tournaments import Tournaments, bracket, participant

ROOT = Path(__file__).resolve().parents[1]


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ('characters_data.py', 'team_tables.py'):
            shutil.copy2(ROOT/name, self.root/name)
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
        with patch.dict(self.c, session_id=lambda: 'someone-else'):
            self.assertEqual(self.client.get(f'/api/ui/tournaments/{t["id"]}/replays/{replay_id}').status_code, 404)
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
