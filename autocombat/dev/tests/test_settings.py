"""Settings persistence, authorization, pinned rules and live tournament pacing."""
import ast
import copy
import importlib.util
import io
import json
import logging
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from flask import Blueprint, Flask, request

from combat_defaults import DEFAULTS
from combat_settings import (SettingsStore, configure, default_store, metadata,
                             settings_scope, snapshot, validate, value)
from ui_management import Management
from ui_settings import Settings
from ui_store import Store
from ui_tournaments import Tournaments, bracket

ROOT = (Path(__file__).resolve().parents[2] / 'backend')


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.previous_store = default_store()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for path in ROOT.glob('*.py'):
            if path.name != 'autocombat_backend.py': shutil.copy2(path, self.root/path.name)
        (self.root/'ai_weights.json').write_text('{}', encoding='utf-8')
        spec = importlib.util.spec_from_file_location('settings_test_data', self.root/'characters_data.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.app = Flask(__name__)
        bp = Blueprint('settings_test', __name__)
        self.context = dict(body=lambda: request.get_json(), session_id=lambda: 'owner',
                            guard=threading.RLock(), ns={'characters_data_pvp': module, '_lock': threading.RLock()})
        self.management = Management(bp, self.context, self.root)
        self.settings = Settings(bp, self.context, self.management, '8889')
        self.store = Store(self.management.runtime/'test.sqlite3')
        self.tournaments = Tournaments(bp, self.context, self.management, self.store)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()

    def tearDown(self):
        configure(self.previous_store)
        self.temp.cleanup()

    def save(self, changes, version=None, **kwargs):
        version = version or self.client.get('/api/ui/settings').get_json()['version']
        return self.client.post('/api/ui/settings', json={'version': version, 'values': changes}, **kwargs)

    def test_roundtrip_conflict_readonly_and_backup_restore(self):
        original = self.client.get('/api/ui/settings').get_json()
        saved = self.save({'TOURNAMENT_EVENT_DELAY': 0.125, 'DELAY_PRELIM_TURN': 0.25, 'BO_N_FINAL': 3})
        self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))
        self.assertEqual(SettingsStore(self.root, '8889').read()['TOURNAMENT_EVENT_DELAY'], 0.125)
        stale = self.save({'BO_N_FINAL': 5}, original['version'])
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.save({'AI_SERVER_PORT': 1234}).status_code, 400)
        backups = self.client.get('/api/ui/backups').get_json()['backups']
        self.assertTrue(backups)
        restored = self.client.post(f"/api/ui/backups/{backups[0]['id']}/restore", json={})
        self.assertEqual(restored.status_code, 200, restored.get_data(as_text=True))
        self.assertEqual(self.settings.store.read(), original['values'])

    def test_permission_and_atomic_validation(self):
        denied = {'REMOTE_ADDR': '192.168.15.19'}
        public = self.client.get('/api/ui/settings', environ_overrides=denied).get_json()
        self.assertFalse(public['can_manage'])
        self.assertNotIn('settings_file', public['runtime'])
        self.assertNotIn('LOG_FILE', public['values'])
        self.assertEqual(self.save({'BO_N_FINAL': 3}, environ_overrides=denied).status_code, 403)
        initial = self.settings.store.read()
        for changes in ({'BO_N_FINAL': 2}, {'TOURNAMENT_EVENT_DELAY': -1}, {'TOURNAMENT_EVENT_DELAY': float('nan')},
                        {'PRELIM_WORKERS': 33}, {'SEASON_AVG_STATUS': 101}, {'SEASON_AVG_STATUS': 'random'},
                        {'KNOCKOUT_DRAW_WEIGHTS': [[1]]}, {'UNKNOWN': 1}, {'AI_SUMMON_STATUS_MIN': 90},
                        {'AI_TEMPERATURE': 0}, {'TEAM_TABLES_2V2': [['Y1', 'Y1']]}, {'TEAM_TABLES_2V2': [['NO_SUCH_CHARACTER']]},
                        {'TEAM_TABLES_2V2': [['Y1','Y2'], ['Y2','Y1']]}):
            with self.subTest(changes=changes):
                result = self.save(changes)
                self.assertEqual(result.status_code, 400, result.get_data(as_text=True))
                self.assertEqual(self.settings.store.read(), initial)

    def test_team_tables_and_future_tournaments_share_saved_values(self):
        presets = [['Y1','Y2'], ['Y3','Y4'], ['Y5','Y6'], ['Y7','Y8']]
        self.assertEqual(self.save({'TEAM_TABLES_2V2': presets, 'RR_ROUNDS_2V2': 1, 'TARGET_TEAM_MATCHES': 3, 'RANDOM_BONUS_2V2': 0,
                                   'MAP_SIZES_2V2': ['8x8'], 'BO_N_FINAL': 3}).status_code, 200)
        import team_tables
        self.assertEqual(team_tables.TEAM_TABLES_2V2, presets)
        created = self.client.post('/api/ui/tournaments', json={'format': '2v2', 'seed': 5})
        self.assertEqual(created.status_code, 200, created.get_data(as_text=True))
        t = created.get_json()
        self.assertEqual(t['teams'], presets)
        self.assertEqual(len(t['schedule']), 6)
        self.assertTrue(all(row['map_size'] == '8x8' for row in t['schedule']))
        self.assertEqual(self.save({'TEAM_TABLES_2V2': [], 'BO_N_FINAL': 9}).status_code, 200)
        persisted = self.store.get('tournament', t['id'])
        self.assertEqual(persisted['settings']['TEAM_TABLES_2V2'], presets)
        self.assertEqual(next(s for s in bracket(persisted) if s['id'] == 'FINAL')['best_of'], 3)
        self.assertEqual(self.client.post('/api/ui/tournaments', json={'format': '2v2'}).status_code, 400)
        with settings_scope(persisted['settings']):
            self.assertEqual(team_tables.TEAM_TABLES_2V2, presets)

    def test_special_team_sizes_and_snapshot_context_are_preserved(self):
        teams = [['Y5','Y6','Y7','Y8'], ['Y9','Y12']]
        self.assertEqual(self.save({'TEAM_TABLES_3V3': teams}).status_code, 200)
        captured = snapshot()
        self.assertEqual(self.save({'BO_N_FINAL': 3}).status_code, 200)
        with settings_scope(captured):
            self.assertEqual(value('BO_N_FINAL'), captured['BO_N_FINAL'])
            self.assertEqual(value('TEAM_TABLES_3V3'), teams)
            returned = value('TEAM_TABLES_3V3'); returned[0].clear()
            self.assertEqual(value('TEAM_TABLES_3V3'), teams)
        self.assertEqual(value('BO_N_FINAL'), 3)

    def test_live_event_wait_uses_current_setting_and_multiplier(self):
        self.save({'TOURNAMENT_EVENT_DELAY': 0.4})
        t = dict(id='pacing', settings=snapshot(), teams=[['Y1'],['Y2']], format='1v1', phase='regular',
                 status='running', speed=2, current={'replay': []}, logs=[], revision=0, char_season={'Y1': 50, 'Y2': 50})
        self.store.put('tournament', t)
        waits = []
        class Process:
            stdin = io.StringIO()
            def poll(self): return None
        proc = Process()
        def output():
            yield json.dumps({'round': 'round 1'})
            self.settings.store.write({**self.settings.store.read(), 'TOURNAMENT_EVENT_DELAY': 0})
            yield json.dumps({'round': 'round 2'})
            yield json.dumps({'result': {'winner': 'Y', 'rounds': 2}})
        proc.stdout = output()
        self.tournaments.workers['pacing'] = proc, None
        with patch('ui_tournaments.time.sleep', side_effect=waits.append):
            result = self.tournaments.simulate('pacing', t, dict(a=0, b=1, map_size='10x10', seed=1), verbose=True)
        self.assertEqual(waits, [0.2, 0])
        self.assertEqual(result['winner'], 'Y')
        sent = json.loads(proc.stdin.getvalue())
        self.assertEqual(sent['settings']['TOURNAMENT_EVENT_DELAY'], 0.4)
        self.assertEqual(sent['max_rounds'], t['settings']['MAX_ROUNDS_PRELIM_BY_FORMAT']['1v1'])

    def test_instance_isolation_and_legacy_partial_settings(self):
        alternate = SettingsStore(self.root, '8999')
        alternate.write({**DEFAULTS, 'BO_N_FINAL': 3})
        self.assertEqual(self.settings.store.read()['BO_N_FINAL'], DEFAULTS['BO_N_FINAL'])
        self.settings.store.path.parent.mkdir(exist_ok=True)
        self.settings.store.path.write_text(json.dumps({'BO_N_FINAL': 5}), encoding='utf-8')
        self.assertEqual(self.settings.store.read()['BO_N_FINAL'], 5)
        self.assertEqual(self.settings.store.read()['TEAM_TABLES_2V2'], DEFAULTS['TEAM_TABLES_2V2'])

    def test_legacy_settings_scope_backfills_new_defaults(self):
        legacy = {key: value for key, value in DEFAULTS.items() if key != 'TARGET_TEAM_MATCHES'}
        with settings_scope(legacy):
            self.assertEqual(value('TARGET_TEAM_MATCHES'), DEFAULTS['TARGET_TEAM_MATCHES'])

    def test_worker_process_receives_rules_without_reading_live_settings(self):
        pinned = {**snapshot(), 'MAX_ROUNDS_PRELIM': 1, 'MAX_ROUNDS_KNOCKOUT': 1, 'DEFAULT_BATTLE_DELAY': 0,
                  'TEAM_TABLES_2V2': [], 'SEASON_AVG_STATUS': {'Y1': 80, 'Y2': 20}}
        folder = self.management.snapshot_task('worker-test')
        self.save({'MAX_ROUNDS_KNOCKOUT': 5, 'SEASON_AVG_STATUS': 10})
        params = dict(mode='game', settings=pinned, seed=37, team_a=['Y1'], team_b=['Y2'], map_size='8x8')
        run = subprocess.run([sys.executable, str(folder/'ui_task_worker.py')], cwd=folder,
                             input=json.dumps(params)+'\n', text=True, encoding='utf-8', capture_output=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr[-1500:])
        rows = [json.loads(line) for line in run.stdout.splitlines()]
        result = next(row['result'] for row in rows if 'result' in row)
        self.assertEqual(result['rounds'], 1)
        self.assertTrue(result['timeout'])

    def test_engine_defaults_and_live_log_levels_use_settings(self):
        import ai_battle
        import battle_engine
        cfg = {**snapshot(), 'DEFAULT_MAP_SIZE': '8x12'}
        with settings_scope(cfg):
            for engine_type in (battle_engine.FullBattleEngine, battle_engine.FastBattleEngine):
                engine = engine_type()
                for uid, character in zip(('a', 'b'), self.management.module().ALL_CHARACTERS[:2]):
                    self.management.module().load_character_to_engine(engine, character, uid)
                engine.setup_battle(['a'], ['b'])
                self.assertEqual((engine._get_map()['width'], engine._get_map()['height']), (8, 12))
        handler = logging.NullHandler()
        handler._combat_setting = 'CONSOLE_LOG_LEVEL'
        with patch.object(ai_battle.logger, 'handlers', [handler]), settings_scope(snapshot()):
            self.settings.store.write({**self.settings.store.read(), 'CONSOLE_LOG_LEVEL': logging.ERROR})
            ai_battle.battle_log('info', 'hidden at ERROR')
            self.assertEqual(handler.level, logging.ERROR)
            self.settings.store.write({**self.settings.store.read(), 'CONSOLE_LOG_LEVEL': logging.DEBUG})
            ai_battle.battle_log('info', 'visible at DEBUG')
            self.assertEqual(handler.level, logging.DEBUG)


class SettingsExtractionTests(unittest.TestCase):
    def test_all_original_config_names_remain_accessible_and_described(self):
        import ai_battle
        self.assertEqual(set(DEFAULTS), set(metadata()))
        with settings_scope(copy.deepcopy(DEFAULTS)):
            self.assertEqual(ai_battle.CUSTOM_BATTLE_DELAY, value('DELAY_CUSTOM_TURN'))
            for name in DEFAULTS:
                self.assertEqual(getattr(ai_battle, name), value(name), name)

    def test_ai_configuration_names_have_no_literal_redefinitions(self):
        tree = ast.parse((ROOT/'ai_battle.py').read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name): self.assertNotIn(target.id, DEFAULTS)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'sleep':
                self.assertFalse(any(isinstance(arg, ast.Constant) and arg.value in (0.4, 0.65, 24, 40) for arg in node.args))
        source = (ROOT/'ui_tournaments.py').read_text(encoding='utf-8')
        self.assertNotIn('time.sleep(.65', source)
        self.assertNotIn('time.sleep(0.65', source)
        defaults = copy.deepcopy(DEFAULTS)
        self.assertEqual(validate(defaults), defaults)


if __name__ == '__main__':
    unittest.main()
