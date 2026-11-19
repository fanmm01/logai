"""Target selection, sampling, preservation and atomic installation."""
import copy
from concurrent.futures import Future
import json
from pathlib import Path
import random
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from flask import Blueprint, Flask, request
import ai_trainer as tr
import characters_data as data
from ui_store import Store
from ui_tasks import Tasks
from ui_management import digest


class TargetedTrainingTests(unittest.TestCase):
    def test_selection_and_balanced_sampling(self):
        singles = ['Y' + str(i) for i in range(1, 9)]
        self.assertEqual(tr.normalize_training_targets(['Y3', 'Y1', 'Y3'], singles), ['Y1', 'Y3'])
        for invalid in ([], 'Y1', [None], ['Y99'], [{}]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                tr.normalize_training_targets(invalid, singles)
        random.seed(42)
        targets = singles[:3]
        specs = tr.character_battle_specs(singles, targets, 120)
        sizes, sides = set(), set()
        for i, (left, right, _) in enumerate(specs):
            anchor = targets[i % 3]
            self.assertIn(anchor, left + right)
            self.assertEqual(len(set(left + right)), len(left + right))
            self.assertEqual(len(left), len(right))
            sizes.add(len(left))
            sides.add(anchor in left)
        self.assertEqual(sizes, {1, 3})
        self.assertEqual(sides, {True, False})
        for count in (1, len(singles)):
            self.assertEqual(len(tr.character_battle_specs(singles, singles[:count], 8)), 8)

    def test_associated_summons_are_union_and_snapshot_is_frozen(self):
        chars = copy.deepcopy(data.ALL_CHARACTERS[:6])
        for i, ch in enumerate(chars):
            ch['test_reference'] = {'召唤物模板': 'shared' if i < 2 else 'other'}
        with patch.object(data, 'ALL_CHARACTERS', chars), patch.object(data, 'SUMMON_TEMPLATES', {'shared': {}, 'other': {}}):
            trainer = tr.QTrainer()
            trainer.init_characters([chars[1]['serial'], chars[0]['serial']])
            self.assertEqual(trainer.training_summons, ['shared'])
            self.assertEqual(trainer.summon_pool, ['shared', 'other'])
            chars[0]['name'] = 'changed after snapshot'
            self.assertNotEqual(trainer.characters[0]['name'], chars[0]['name'])
        with patch.object(data, 'SUMMON_TEMPLATES', {}):
            trainer = tr.QTrainer()
            trainer.init_characters([data.ALL_CHARACTERS[0]['serial']])
            self.assertEqual(trainer.training_summons, [])

    def run_fake_training(self, fail=False, legacy=False):
        trainer = tr.QTrainer()
        trainer.init_characters([data.ALL_CHARACTERS[0]['serial'], data.ALL_CHARACTERS[1]['serial']])
        targets = trainer.target_serials
        previous = {'Q_solo': {}, 'Q_team': {}, 'Q_summon': {}}
        for kind in ('Q_solo', 'Q_team'):
            previous[kind] = {s: {'old': 12} for s in trainer.char_map}
        previous['Q_summon'] = {s: {'old': 9} for s in trainer.summon_pool + ['unrelated']}
        if legacy:
            previous['Q'] = previous.pop('Q_solo')
            previous.pop('Q_team')
        calls = []

        class Pool:
            def __init__(self, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def submit(self, fn, task):
                calls.append((fn, task))
                f = Future()
                # Include non-target updates deliberately; they must be discarded.
                rows = [(kind, s, (0,) * 22, 'WAIT', 1, None)
                    for kind, serials in [('solo', trainer.char_map), ('team', trainer.char_map),
                                          ('summon', trainer.summon_pool)] for s in serials]
                if fail:
                    rows = [row for row in rows if row[1] != targets[-1]]
                f.set_result(rows)
                return f

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'weights.json'
            path.write_text(json.dumps(previous), encoding='utf-8')
            before = path.read_bytes()
            with patch.object(tr, 'OUTPUT_FILE', str(path)), patch.object(tr, 'GENERATIONS', 1), \
                    patch.object(tr, 'BATTLES_PER_GEN', 8), patch.object(tr, 'ProcessPoolExecutor', Pool):
                if fail:
                    with self.assertRaisesRegex(ValueError, '原权重未替换'):
                        trainer.train()
                    self.assertEqual(path.read_bytes(), before)
                    return
                trainer.train()
            saved = json.loads(path.read_text(encoding='utf-8'))
        for kind in ('Q_solo', 'Q_team'):
            for s in trainer.char_map:
                self.assertEqual('old' in saved[kind][s], s not in targets)
        for s, old in previous['Q_summon'].items():
            self.assertEqual(saved['Q_summon'].get(s) == old, s not in trainer.training_summons)
        self.assertEqual(saved['params']['characters'], targets)
        self.assertTrue(all(s in targets for s in trainer.Q_solo))
        for fn, task in calls:
            if fn is tr._mp_run_summon_battle:
                self.assertTrue(set(task[0] + task[1]) & set(trainer.training_summons))

    def test_preserves_other_tables(self):
        self.run_fake_training()
        self.run_fake_training(legacy=True)

    def test_missing_updates_does_not_replace_weights(self):
        self.run_fake_training(fail=True)

    def test_cli_arguments(self):
        for args, expected in [([], None), (['--character', 'Y1'], ['Y1']),
                               (['--characters', 'Y1', 'Y2'], ['Y1', 'Y2'])]:
            with patch('sys.argv', ['ai_trainer.py'] + args), patch.object(tr, 'main') as main:
                tr.cli()
                main.assert_called_once_with(target_serials=expected)
        with patch('sys.argv', ['ai_trainer.py', '--character', 'Y1', '--characters', 'Y2']):
            with self.assertRaises(SystemExit):
                tr.cli()


class TrainingAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'tasks.sqlite3')
        self.management = Mock(lock=threading.RLock(), root=self.root, runtime=self.root)
        self.management.module.return_value = data
        self.management.snapshot_task.return_value = self.root
        (self.root / 'characters_data.py').write_text('snapshot', encoding='utf-8')
        self.context = dict(body=lambda: request.get_json(), session_id=lambda: 'owner',
                            guard=threading.RLock(), ns={'_lock': threading.RLock(), '_Q_TABLE_PVP_CACHE': 'old'})
        app = Flask(__name__)
        bp = Blueprint('targeted_training', __name__)
        self.tasks = Tasks(bp, self.context, self.management, self.store)
        app.register_blueprint(bp)
        self.client = app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_validation_normalization_and_conflict(self):
        for invalid in ([], None, 'Y1', ['Y99'], [{}]):
            response = self.client.post('/api/ui/training', json={'target_serials': invalid})
            self.assertEqual(response.status_code, 400)
        enabled = [ch['serial'] for ch in data.ALL_CHARACTERS]
        with patch('ui_tasks.threading.Thread'):
            response = self.client.post('/api/ui/training', json={'target_serials': [enabled[1], enabled[0], enabled[1]]})
        self.assertEqual(response.status_code, 200)
        job = response.get_json()
        self.assertEqual(job['params']['target_serials'], enabled[:2])
        self.assertEqual(job['params']['enabled_serials'], enabled)
        self.assertEqual(self.client.post('/api/ui/training', json={}).status_code, 409)

    def test_disabled_and_small_roster_rejected(self):
        chars = data.ALL_CHARACTERS
        with patch.object(data, 'ALL_CHARACTERS', chars[1:]):
            self.assertEqual(self.client.post('/api/ui/training', json={'target_serials': [chars[0]['serial']]}).status_code, 400)
        with patch.object(data, 'ALL_CHARACTERS', chars[:5]):
            self.assertEqual(self.client.post('/api/ui/training', json={'target_serials': [chars[0]['serial']]}).status_code, 400)

    def test_install_backs_up_and_keeps_dirty_marker_for_targeted_run(self):
        folder = self.root / 'job'
        folder.mkdir()
        (folder / 'ai_weights.json').write_text('{"Q_solo": {}}', encoding='utf-8')
        marker = self.root / 'needs-training'
        marker.touch()
        version = digest((self.root / 'characters_data.py').read_bytes())
        self.tasks.install_weights(folder, {'params': {'target_serials': ['Y1']}, 'data_version': version})
        self.assertTrue(marker.exists())
        self.management.make_backup.assert_called_once_with('before training weights')
        self.assertIsNone(self.context['ns']['_Q_TABLE_PVP_CACHE'])
        self.assertEqual((self.root / 'ai_weights.json').read_bytes(), (folder / 'ai_weights.json').read_bytes())
        self.tasks.install_weights(folder, {'params': {}, 'data_version': version})
        self.assertFalse(marker.exists())
