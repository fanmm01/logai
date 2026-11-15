"""Browser adapter integration: real combat, isolation and multiplayer permissions."""
import copy
import json
import importlib
import io
import os
import random
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_battle_execution import http_module


class BrowserUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = importlib.import_module(os.environ['UI_TEST_BACKEND']) if os.environ.get('UI_TEST_BACKEND') else http_module()
        cls.app = cls.srv.app

    def setUp(self):
        self.ext = self.app.extensions['combat_ui']
        self.log = patch.object(self.srv, '_debug_log')
        self.log.start()
        self.client = self.app.test_client()
        self.me = self.client.post('/api/ui/session', json={'name': '房主'}).get_json()['id']

    def tearDown(self):
        with self.ext['lock']:
            for r in self.ext['rooms'].values():
                self.srv._battles.pop(r['battle_id'], None)
                self.srv._pending_reactions.pop(r['battle_id'], None)
            self.ext['rooms'].clear()
        self.log.stop()

    def create(self, **kwargs):
        params = dict(team_a=['Y1'], team_b=['Y2'], local=True, ai_slots=[])
        params.update(kwargs)
        response = self.client.post('/api/ui/rooms', json=params)
        self.assertEqual(response.status_code, 200, response.get_json())
        room = response.get_json()
        self.code = room['code']
        self.bid = self.ext['rooms'][self.code]['battle_id']
        self.eng = self.srv._battles[self.bid]
        return room

    def control(self, **data):
        return self.client.post(f'/api/ui/rooms/{self.code}/control', json=data)

    def state(self):
        return self.client.get(f'/api/ui/rooms/{self.code}').get_json()

    def submit(self, client=None, **kwargs):
        data = dict(command='.i end', revision=self.state()['revision'], request_id='request-' + str(random.random()))
        data.update(kwargs)
        return (client or self.client).post(f'/api/ui/rooms/{self.code}/action', json=data)

    def close_range(self):
        self.control(op='start')
        self.control(op='pause')
        il = self.eng._get_initiative()
        md = self.eng._get_map()
        md['occupants'] = {}
        for i, e in enumerate(il):
            e['coord'] = ['B2', 'C2'][i]
            e['_anchor_coord'] = e['coord']
            md['occupants'][e['coord']] = e['userId']
        self.eng._set_map(md)
        self.eng._set_initiative(il)

    def test_catalog_and_built_page(self):
        data = self.client.get('/api/ui/catalog').get_json()['characters']
        self.assertGreaterEqual(len(data), 12)
        self.assertTrue(all(c['spells'] and c['attrs'] and c['portrait'] for c in data))
        response = self.client.get('/ui/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<!doctype html>', response.data)
        response.close()

    def test_bare_host_redirects_to_ui_preserving_query(self):
        for path, location in [('/', '/ui/'), ('/index.html', '/ui/'), ('/?room=AB12', '/ui/?room=AB12')]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302, path)
            self.assertEqual(response.headers['Location'], location)
        # Unknown API paths keep the legacy JSON 404 contract.
        self.assertEqual(self.client.get('/api/nope').status_code, 404)

    def test_ui_deep_links_serve_the_same_application(self):
        for page in ('home', 'setup', 'battle', 'sim', 'library', 'editor',
                     'tournament', 'settings', 'help/mechanics', 'help/characters'):
            with self.subTest(page=page):
                response = self.client.get('/ui/' + page + '?character=Y1')
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'<!doctype html>', response.data)
                response.close()

    def test_disabled_roster_blocks_new_operations_but_preserves_existing_room(self):
        self.create()
        self.control(op='start')
        self.control(op='pause')
        before = self.state()['snapshot']
        module = self.srv.characters_data_pvp
        remaining = [ch for ch in module.ALL_CHARACTERS if ch['serial'] != 'Y1']
        with patch.object(module, 'ALL_CHARACTERS', remaining):
            catalog = self.client.get('/api/ui/catalog').get_json()['characters']
            self.assertNotIn('Y1', {ch['serial'] for ch in catalog})
            presets = self.client.get('/api/ui/presets').get_json()['presets']
            self.assertTrue(all(not p['valid'] for p in presets if 'Y1' in p['team']))
            self.assertEqual(self.client.post('/api/ui/rooms', json=dict(team_a=['Y1'], team_b=['Y2'], local=True)).status_code, 400)
            self.assertEqual(self.client.post('/api/ui/simulations', json=dict(mode='single', team_a=['Y1'], team_b=['Y2'])).status_code, 400)
            self.assertEqual(self.client.post('/api/ui/tournaments', json=dict(format='1v1', teams=[['Y1'], ['Y2'], ['Y3'], ['Y4']])).status_code, 400)
            params = self.ext['tasks'].params(dict(mode='table', format='2v2', n=1))
            self.assertTrue(params['teams'])
            self.assertTrue(all('Y1' not in team for team in params['teams']))
            tournament = self.client.post('/api/ui/tournaments', json=dict(format='2v2', seed=77))
            self.assertEqual(tournament.status_code, 200, tournament.get_json())
            self.assertTrue(all('Y1' not in team for team in tournament.get_json()['teams']))
            self.assertEqual(self.state()['snapshot']['units'], before['units'])
        with patch.object(module, 'ALL_CHARACTERS', remaining[:5]):
            self.assertEqual(self.client.post('/api/ui/training', json={}).status_code, 400)
        with patch.object(module, 'ALL_CHARACTERS', remaining[:1]):
            self.assertEqual(self.client.post('/api/ui/simulations', json=dict(mode='prelim', formats=['1v1'])).status_code, 400)

    def test_training_job_records_current_enabled_roster(self):
        module = self.srv.characters_data_pvp
        enabled = module.ALL_CHARACTERS[:6]
        with patch.object(module, 'ALL_CHARACTERS', enabled), patch('ui_tasks.threading.Thread'):
            response = self.client.post('/api/ui/training', json={})
        self.assertEqual(response.status_code, 200, response.get_json())
        job = response.get_json()
        self.assertEqual(job['params']['enabled_serials'], [ch['serial'] for ch in enabled])
        job['status'] = 'cancelled'
        self.ext['store'].put('task', job)

    def test_zero_hp_is_not_replaced_by_maximum(self):
        self.create()
        uid = self.eng._get_initiative()[0]['userId']
        # Test serialization alone; _set_combat_hp intentionally rolls a CON
        # save and may immediately remove the unit after final death.
        self.eng._init_combat_hp(uid, 0)
        self.assertEqual(self.srv._char_summary(self.eng, uid)['hp'], 0)
        self.assertEqual(next(u for u in self.state()['snapshot']['units'] if u['uid'] == uid)['hp'], 0)

    def test_mirror_units_have_independent_controls(self):
        r = self.create(team_b=['Y1'], local=False)
        units = r['snapshot']['units']
        self.assertEqual(len({u['uid'] for u in units}), 2)
        guest = self.app.test_client()
        pid = guest.post('/api/ui/session', json={'name': '伙伴'}).get_json()['id']
        guest.post(f'/api/ui/rooms/{self.code}/join', json={})
        target = next(u for u in units if u['team'] == 'X')
        self.assertEqual(self.control(op='assign', uid=target['uid'], controller=pid).status_code, 200)
        snap = self.state()['snapshot']
        self.assertEqual(next(u for u in snap['units'] if u['team'] == 'X')['controller'], pid)
        self.assertEqual(next(u for u in snap['units'] if u['team'] == 'Y')['controller'], self.me)

    def test_spectator_cannot_control_or_spoof_public_player_id(self):
        r = self.create(local=False)
        guest = self.app.test_client()
        guest.post('/api/ui/session', json={})
        guest.post(f'/api/ui/rooms/{self.code}/join', json={'spectator': True})
        self.control(op='start')
        self.assertEqual(self.submit(guest).status_code, 403)
        self.assertEqual(guest.post(f'/api/ui/rooms/{self.code}/control', json={'op': 'pause'}).status_code, 403)
        attacker = self.app.test_client()
        attacker.set_cookie('combat_ui_session', r['host'])
        self.assertEqual(attacker.get(f'/api/ui/rooms/{self.code}').status_code, 403)
        self.assertEqual(guest.get(f'/api/pvp/{self.bid}/state').status_code, 403)

    def test_readiness_and_local_room_membership(self):
        self.create(local=False)
        guest = self.app.test_client()
        guest.post('/api/ui/session', json={})
        self.assertEqual(guest.post(f'/api/ui/rooms/{self.code}/join', json={}).status_code, 200)
        self.assertEqual(self.control(op='start').status_code, 409)
        guest.post(f'/api/ui/rooms/{self.code}/control', json={'op':'ready'})
        self.assertEqual(self.control(op='start').status_code, 200)
        self.create(local=True)
        self.assertEqual(guest.post(f'/api/ui/rooms/{self.code}/join', json={}).status_code, 403)

    def test_preview_is_atomic_and_preserves_rng(self):
        self.create()
        self.close_range()
        before = copy.deepcopy((self.eng.storage, self.eng._get_actions(), self.eng._get_initiative()))
        rng = random.getstate()
        response = self.client.post(f'/api/ui/rooms/{self.code}/preview', json={'command':'.s0', 'revision':self.state()['revision']})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(rng, random.getstate())
        self.assertEqual(before, (self.eng.storage, self.eng._get_actions(), self.eng._get_initiative()))
        self.assertNotIn(self.bid, self.srv._pending_reactions)
        self.assertNotIn('output', response.get_json())

    def test_reaction_survives_refresh_and_unauthorized_submission(self):
        self.create(local=False)
        guest = self.app.test_client()
        guest.post('/api/ui/session', json={})
        guest.post(f'/api/ui/rooms/{self.code}/join', json={'spectator': True})
        self.close_range()
        with patch.object(self.srv, 'roll_d100', return_value=(10, '')):
            result = self.submit(command='.s0')
        self.assertEqual(result.status_code, 200, result.get_json())
        pending = self.state()['snapshot']['reaction']
        self.assertIsNotNone(pending)
        self.assertEqual(self.submit(guest, choice='dodge').status_code, 403)
        self.assertIsNotNone(self.state()['snapshot']['reaction'])
        self.assertEqual(self.submit(choice=pending['choices'][0]).status_code, 200)
        self.assertIsNone(self.state()['snapshot']['reaction'])

    def test_stale_revision_and_idempotent_action(self):
        self.create()
        self.close_range()
        before = copy.deepcopy(self.eng.storage)
        self.assertEqual(self.submit(revision=-1).status_code, 409)
        self.assertEqual(before, self.eng.storage)
        revision = self.state()['revision']
        self.assertEqual(self.submit(request_id='same', revision=revision).status_code, 200)
        after = copy.deepcopy(self.eng.storage)
        self.assertEqual(self.submit(request_id='same', revision=revision).status_code, 200)
        self.assertEqual(after, self.eng.storage)

    def test_preparation_move_updates_all_positions_and_rejects_occupied(self):
        r = self.create()
        unit = r['snapshot']['units'][0]
        response = self.submit(command='.a m D4', actor=unit['uid'])
        self.assertEqual(response.status_code, 200)
        moved = next(u for u in self.state()['snapshot']['units'] if u['uid'] == unit['uid'])
        self.assertEqual(moved['coord'], 'D4')
        other = next(u for u in self.state()['snapshot']['units'] if u['uid'] != unit['uid'])
        self.assertEqual(self.submit(command='.a m '+other['coord'], actor=unit['uid']).status_code, 400)

    def test_sse_events_are_non_consuming_for_multiple_subscribers(self):
        self.create()
        first = self.client.get(f'/api/ui/rooms/{self.code}/events?after=0', buffered=False)
        payload = next(first.response).decode('utf-8')
        first.close()
        second = self.client.get(f'/api/ui/rooms/{self.code}/events?after=0', buffered=False)
        self.assertEqual(payload, next(second.response).decode('utf-8'))
        second.close()

    def test_movement_options_do_not_mutate_and_are_executable(self):
        self.create()
        self.close_range()
        before = copy.deepcopy(self.eng.storage)
        response = self.client.post(f'/api/ui/rooms/{self.code}/options', json={'command':'move', 'revision':self.state()['revision']})
        self.assertEqual(response.status_code, 200)
        cells = response.get_json()['cells']
        self.assertTrue(cells)
        self.assertEqual(before, self.eng.storage)
        self.assertEqual(self.submit(command='.a m '+cells[0]).status_code, 200)

    def test_unknown_target_and_cross_origin_are_rejected(self):
        self.create()
        self.close_range()
        self.assertEqual(self.submit(command='.s0',target='unknown').status_code, 400)
        response = self.client.post('/api/ui/session', json={}, headers={'Origin':'https://other.invalid'})
        self.assertEqual(response.status_code, 403)

    def test_ai_single_step_reaches_end_with_mirror_match(self):
        self.create(team_a=['Y1'], team_b=['Y1'], ai_slots=['Y:Y1','X:Y1'])
        self.control(op='start')
        self.control(op='pause')
        for _ in range(350):
            if self.state()['snapshot']['state']['phase'] == 'ended':
                break
            response = self.control(op='step')
            self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.state()['snapshot']['state']['phase'], 'ended')
        self.assertIn(self.state()['snapshot']['result']['winner'], ['Y','X'])
        self.assertGreater(len(self.state()['history']), 4)

    def test_isolated_simulation_worker_returns_structured_result(self):
        root = Path(__file__).resolve().parents[1]
        params = dict(team_a=['Y1'],team_b=['Y1'],map_size='10x10',n=2,seed=123,season=50)
        result = subprocess.run([sys.executable,str(root/'ui_sim_worker.py')], input=json.dumps(params)+'\n',
                                capture_output=True,text=True,encoding='utf-8',cwd=root,timeout=45)
        self.assertEqual(result.returncode,0,result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(rows[-1]['completed'],2)
        final = rows[-1]['result']
        self.assertEqual(final['wins_a']+final['wins_b'],2)
        self.assertAlmostEqual(final['rate_a']+final['rate_b'],1)

    def test_http_simulation_job_and_private_exports(self):
        params = dict(team_a=['Y1'],team_b=['Y1'],map_size='10x10',n=2,seed=123,season=50)
        response = self.client.post('/api/ui/simulations', json=params)
        self.assertEqual(response.status_code,200,response.get_json())
        jid = response.get_json()['id']
        for _ in range(150):
            job = self.client.get('/api/ui/simulations/'+jid).get_json()
            if job['status'] != 'running':
                break
            time.sleep(.1)
        self.assertEqual(job['status'],'completed',job)
        self.assertEqual(job['completed'],2)
        export = self.client.get(f'/api/ui/simulations/{jid}/export?format=csv')
        self.assertEqual(export.status_code,200)
        self.assertIn(b'wins_a',export.data)
        self.assertIn('attachment',export.headers['Content-Disposition'])
        other = self.app.test_client()
        other.post('/api/ui/session',json={})
        self.assertEqual(other.get('/api/ui/simulations/'+jid).status_code,404)
        self.assertEqual(other.get(f'/api/ui/simulations/{jid}/export').status_code,404)

    def test_cake_targets_exact_hidden_summon_member(self):
        self.create(team_a=['Y8','Y9'],team_b=['Y1'])
        self.control(op='start')
        self.control(op='pause')
        actor = next(uid for uid,c in self.eng.characters.items() if c.serial=='Y8')
        summoner = next(uid for uid,c in self.eng.characters.items() if c.serial=='Y9')
        spell = next(s for s in self.eng.load_spells(summoner) if any(e.get('type')==5 for e in s['effects']))
        self.eng._execute_spell(summoner,summoner,spell)
        il = self.eng._get_initiative()
        state = self.eng._get_state()
        state['activeIndex'] = next(i for i,e in enumerate(il) if e['userId']==actor)
        self.eng._set_state(state)
        self.eng._set_actions({**self.eng._get_actions(),actor:{'主动':2,'附加':3}})
        self.eng.get_char(actor).inventory = [{'item':'蛋糕HP','count':1}]
        group = next(iter(self.eng._summon_groups.values()))
        hidden = next(uid for uid in group['members'] if uid!=group['representative_uid'])
        self.eng._init_combat_hp(hidden,1)
        self.control(op='assign',uid=actor,controller=self.me)
        with patch.object(self.eng,'_eat_cake',wraps=self.eng._eat_cake) as eat:
            response = self.submit(command='.a give',target=hidden)
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertEqual(eat.call_args.args[1],hidden)

    def test_support_and_summon_ai_battles_finish(self):
        for a,b in [(['Y7','Y8'],['Y9','Y12']), (['Y4','Y10'],['Y2','Y11'])]:
            with self.subTest(team_a=a,team_b=b):
                random.seed(73)
                self.create(team_a=a,team_b=b,ai_slots=['Y:'+s for s in a]+['X:'+s for s in b])
                self.control(op='start')
                self.control(op='pause')
                for _ in range(650):
                    if self.state()['snapshot']['state']['phase'] == 'ended':
                        break
                    response = self.control(op='step')
                    self.assertEqual(response.status_code,200,response.get_json())
                self.assertEqual(self.state()['snapshot']['state']['phase'],'ended')

    def auto(self, uid, op='start', **kwargs):
        unit = next(u for u in self.state()['snapshot']['units'] if u['uid'] == uid)
        data = dict(uid=uid, op=op, choice='dodge', count=3,
                    revision=unit['auto_reaction']['revision'], request_id=str(random.random()))
        data.update(kwargs)
        return self.client.post(f'/api/ui/rooms/{self.code}/auto-reaction', json=data)

    def test_auto_reaction_start_stop_and_remaining_batch_is_manual(self):
        with self.ext['lock']:
            self.create()
            self.close_range()
            with patch.object(self.srv, 'roll_d100', return_value=(10, '')):
                self.assertEqual(self.submit(command='.s0').status_code, 200)
            pending = self.srv._pending_reactions[self.bid]
            first = copy.deepcopy(pending[0] if isinstance(pending, list) else pending)
            first.update(dmg_dice='1', atk_rank=1, leth=0)
            self.srv._pending_reactions[self.bid] = [first, copy.deepcopy(first)]
            defender = first['def_uid']
            self.eng._init_combat_hp(defender, 100)
            self.assertEqual(self.auto(defender).status_code, 200)
            room = self.ext['rooms'][self.code]
            with self.app.app_context(), patch.object(self.srv, 'roll_d100', return_value=(99, '')):
                self.assertTrue(self.ext['reactions'].step(room))
            self.assertEqual(self.eng._get_combat_hp(defender), 99)
            state = self.state()
            self.assertTrue(state['snapshot']['reaction'])
            unit = next(u for u in state['snapshot']['units'] if u['uid'] == defender)
            self.assertEqual(unit['auto_reaction']['remaining'], 2)
            self.assertEqual(self.auto(defender, 'stop', revision=-999).status_code, 200)
            before = self.eng._get_combat_hp(defender)
            self.assertFalse(self.ext['reactions'].step(room))
            self.assertEqual(before, self.eng._get_combat_hp(defender))
            self.assertIsNotNone(self.state()['snapshot']['reaction'])

    def test_stop_prevents_stale_enable_and_mirrors_are_independent(self):
        with self.ext['lock']:
            room = self.create(team_b=['Y1'], local=False)
            a, b = [u['uid'] for u in room['snapshot']['units']]
            self.assertEqual(self.auto(a).status_code, 200)
            self.assertFalse(next(u for u in self.state()['snapshot']['units'] if u['uid'] == b)['auto_reaction']['enabled'])
            self.assertEqual(self.auto(a, 'stop').status_code, 200)
            self.assertEqual(self.auto(a, revision=0).status_code, 409)
            guest = self.app.test_client()
            guest.post('/api/ui/session', json={})
            guest.post(f'/api/ui/rooms/{self.code}/join', json={'spectator': True})
            response = guest.post(f'/api/ui/rooms/{self.code}/auto-reaction', json={'op': 'stop', 'uid': a, 'request_id': 'forged'})
            self.assertEqual(response.status_code, 403)

    def test_summons_default_to_ai_and_can_be_assigned_as_a_group(self):
        with self.ext['lock']:
            self.create(team_a=['Y9'], team_b=['Y1'])
            self.close_range()
            owner = next(u['uid'] for u in self.state()['snapshot']['units'] if u['serial'] == 'Y9')
            spell = next(s for s in self.eng.load_spells(owner) if any(e['type'] == 5 for e in s['effects']))
            self.eng._execute_spell(owner, owner, spell)
            summons = [u for u in self.state()['snapshot']['units'] if u['summon']]
            self.assertTrue(summons)
            self.assertTrue(all(u['controller'] == 'ai' for u in summons))
            self.assertEqual(self.control(op='assign', uid=summons[0]['uid'], controller=self.me).status_code, 200)
            members = [u for u in self.state()['snapshot']['units'] if u['representative'] == summons[0]['representative']]
            self.assertTrue(all(u['controller'] == self.me for u in members))

    def test_domain_snapshot_tracks_center_and_friendly_membership(self):
        with self.ext['lock']:
            self.create(team_a=['Y9', 'Y3'], team_b=['Y1'])
            self.control(op='start')
            self.control(op='pause')
            units = self.state()['snapshot']['units']
            caster = next(u['uid'] for u in units if u['serial'] == 'Y9')
            spell = next(s for s in self.eng.load_spells(caster) if s['name'] == '领域·生')
            self.eng.get_char(caster).set_attr('魔力', 99)
            self.eng._execute_spell(caster, caster, spell)
            il = self.eng._get_initiative()
            entry = next(e for e in il if e['userId'] == caster)
            entry['coord'] = 'H8'
            self.eng._set_initiative(il)
            zones = [e for e in self.state()['snapshot']['effects'] if e['type'] == 'zone']
            self.assertTrue(zones)
            self.assertTrue(all(z['center'] == 'H8' for z in zones))
            enemy = next(u['uid'] for u in units if u['team'] == 'X')
            self.assertTrue(all(enemy not in z['affected'] for z in zones))
            ally = next(u['uid'] for u in units if u['serial'] == 'Y3')
            self.eng._init_combat_hp(ally, 2)
            self.eng._init_combat_hp(enemy, 2)
            self.eng._apply_zone_effects()
            self.assertGreater(self.eng._get_combat_hp(ally), 2)
            self.assertEqual(self.eng._get_combat_hp(enemy), 2)

    def test_summon_basic_attack_uses_selected_enemy(self):
        with self.ext['lock']:
            self.create(team_a=['Y9'], team_b=['Y1', 'Y2'])
            self.control(op='start')
            self.control(op='pause')
            owner = next(uid for uid, ch in self.eng.characters.items() if ch.serial == 'Y9')
            spell = next(s for s in self.eng.load_spells(owner) if any(e['type'] == 5 for e in s['effects']))
            self.eng._execute_spell(owner, owner, spell)
            group = next(iter(self.eng._summon_groups.values()))
            rep = group['representative_uid']
            self.control(op='assign', uid=rep, controller=self.me)
            state = self.eng._get_state()
            state['activeIndex'] = next(i for i, e in enumerate(self.eng._get_initiative()) if e['userId'] == rep)
            self.eng._set_state(state)
            self.eng._set_actions({**self.eng._get_actions(), self.eng._summon_group_key(rep): {'主动': 1, '附加': 3}})
            enemies = self.eng._enemy_targets(rep)
            target = enemies[-1]
            with patch.object(self.eng, '_coc7_attack', return_value=(rep, target, ['selected target'])) as attack:
                response = self.submit(command='.s0', target=target)
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertTrue(attack.called)
            self.assertTrue(all(call.args[1] == target for call in attack.call_args_list))

    def test_tournament_watch_reuses_room_and_commits_manual_result_once(self):
        from ui_tournaments import bracket
        with self.ext['lock']:
            response = self.client.post('/api/ui/tournaments', json={'format': '1v1', 'seed': 77,
                'teams': [['Y1'], ['Y2'], ['Y3'], ['Y4']]})
            self.assertEqual(response.status_code, 200, response.get_json())
            t = response.get_json()
            t.update(phase='playoffs', series=bracket(t), status='paused')
            service = self.ext['tournaments']
            service.save(t)
            data = dict(series='SF1', revision=t['revision'])
            response = self.client.post(f'/api/ui/tournaments/{t["id"]}/watch', json=data)
            self.assertEqual(response.status_code, 200, response.get_json())
            room = response.get_json()['room']
            code = room['code']
            repeat = self.client.post(f'/api/ui/tournaments/{t["id"]}/watch', json=data)
            self.assertEqual(repeat.get_json()['room']['code'], code)
            actor = room['snapshot']['units'][0]['uid']
            self.assertEqual(self.client.post(f'/api/ui/rooms/{code}/control', json={'op': 'assign', 'uid': actor, 'controller': self.me}).status_code, 200)
            internal = self.ext['rooms'][code]
            eng = self.srv._battles[internal['battle_id']]
            eng._battle_result = dict(winner='Y', rounds=1)
            st = eng._get_state()
            st['phase'] = 'ended'
            eng._set_state(st)
            service.room_event(internal, eng)
            service.room_event(internal, eng)
            saved = self.ext['store'].get('tournament', t['id'])
            sf1 = next(s for s in saved['series'] if s['id'] == 'SF1')
            self.assertEqual(sf1['score'], [1, 0])
            self.assertTrue(sf1['games'][0]['manual'])
            replay = self.client.get(f'/api/ui/tournaments/{t["id"]}/replays/{saved["last_replay"]["id"]}').get_json()
            self.assertEqual(len(replay['lines']), 1)
            self.assertIn('第1回合 Y:', replay['lines'][0])
            next_url = f'/api/ui/tournaments/{t["id"]}/next'
            response = self.client.post(next_url, json={'room': code})
            self.assertEqual(response.status_code, 200, response.get_json())
            next_room = response.get_json()['room']
            self.assertNotEqual(next_room['code'], code)
            self.assertEqual(next_room['tournament']['series'], 'SF1')
            self.assertEqual(self.client.post(next_url, json={'room': code}).get_json()['room']['code'], next_room['code'])
            self.assertEqual(self.client.post(next_url, json={'room': next_room['code']}).status_code, 409)
            outsider = self.app.test_client()
            outsider.post('/api/ui/session', json={'name': '访客'})
            # Tournaments are shared; the watched room still denies non-members.
            self.assertEqual(outsider.post(next_url, json={'room': code}).status_code, 403)
            # The deciding game ends SF1; Next must open SF2's first game.
            saved = self.ext['store'].get('tournament', t['id'])
            saved['series'][0]['score'] = [3, 0]
            service.save(saved)
            next_internal = self.ext['rooms'][next_room['code']]
            next_eng = self.srv._battles[next_internal['battle_id']]
            next_eng._battle_result = dict(winner='Y', rounds=1)
            st = next_eng._get_state()
            st['phase'] = 'ended'
            next_eng._set_state(st)
            service.room_event(next_internal, next_eng)
            response = self.client.post(next_url, json={'room': next_room['code']})
            self.assertEqual(response.status_code, 200, response.get_json())
            following = response.get_json()
            self.assertEqual(following['room']['tournament']['series'], 'SF2')
            self.assertEqual(following['tournament']['series'][0]['score'], [4, 0])
            self.assertEqual(following['tournament']['series'][1]['games'], [])


class DetachedConsoleTests(unittest.TestCase):
    """Console teardown must never turn successful gameplay into HTTP 500."""

    @classmethod
    def setUpClass(cls):
        cls.srv = importlib.import_module(os.environ['UI_TEST_BACKEND']) if os.environ.get('UI_TEST_BACKEND') else http_module()

    def test_create_and_start_with_detached_console(self):
        class InvalidHandle:
            def write(self, text):
                raise OSError(22, 'Invalid argument')

            def flush(self):
                raise OSError(22, 'Invalid argument')

        class BrokenPipe:
            def write(self, text):
                raise BrokenPipeError('launching terminal was closed')

            def flush(self):
                pass

        closed = io.StringIO()
        closed.close()
        for label, stream in [('invalid_handle', InvalidHandle()), ('broken_pipe', BrokenPipe()),
                              ('closed_stream', closed), ('no_console', None)]:
            with self.subTest(console=label):
                client = self.srv.app.test_client()
                client.post('/api/ui/session', json={'name': 'console regression'})
                with patch.object(sys, 'stdout', stream), patch.object(self.srv, '_debug_file_logger') as file_log:
                    created = client.post('/api/ui/rooms', json={'team_a': ['Y4', 'Y8'], 'team_b': ['Y1', 'Y7'], 'local': True})
                    self.assertEqual(created.status_code, 200, created.get_json())
                    code = created.get_json()['code']
                    started = client.post(f'/api/ui/rooms/{code}/control', json={'op': 'start'})
                    self.assertEqual(started.status_code, 200, started.get_json())
                    self.assertEqual(started.get_json()['snapshot']['state']['phase'], 'active')
                    self.assertTrue(file_log.debug.called)
                with self.srv.app.extensions['combat_ui']['lock']:
                    room = self.srv.app.extensions['combat_ui']['rooms'].pop(code)
                    self.srv._battles.pop(room['battle_id'], None)
                    self.srv._pending_reactions.pop(room['battle_id'], None)

    def test_unencodable_console_keeps_ascii_fallback(self):
        class AsciiConsole(io.StringIO):
            def write(self, text):
                text.encode('ascii')
                return super().write(text)

        stream = AsciiConsole()
        with patch.object(sys, 'stdout', stream), patch.object(self.srv, '_debug_file_logger') as file_log:
            self.srv._debug_log('变身与战斗日志')
            self.assertTrue(file_log.debug.called)
        self.assertIn('?', stream.getvalue())


if __name__ == '__main__':
    unittest.main()
