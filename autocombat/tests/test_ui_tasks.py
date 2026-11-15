"""Exercise real isolated workers, matrix CLI parity and a small training run."""
import ast
import copy
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for path in ROOT.glob('*.py'):
            if path.name != 'autocombat_backend.py':
                shutil.copy2(path, self.root/path.name)
        shutil.copy2(ROOT/'ai_weights.json', self.root/'ai_weights.json')

    def tearDown(self):
        self.temp.cleanup()

    def run_worker(self, params):
        result = subprocess.run([sys.executable, str(self.root/'ui_task_worker.py')], cwd=self.root,
            input=json.dumps(params)+'\n', text=True, encoding='utf-8', capture_output=True, timeout=60,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertTrue(rows)
        return rows

    def test_table_matches_cli_with_same_data_weights_and_seed(self):
        rows = self.run_worker(dict(mode='table', format='2v2', teams=[['Y1'], ['Y2']], n=2, seed=17, season=50))
        result = rows[-1]['result']
        cli = subprocess.run([sys.executable, str(self.root/'sim.py'), '--mode', 'table', '--format', '2v2',
                              '--teams', 'Y1,Y2', '-n', '2', '-s', '17', '--season', '50'], cwd=self.root,
                             capture_output=True, text=True, encoding='utf-8', timeout=60,
                             env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        self.assertEqual(cli.returncode, 0, cli.stderr)
        matrix = json.loads((self.root/'sim_matrix_2v2.json').read_text(encoding='utf-8'))['matrix']
        names = result['teams']
        self.assertEqual(result['matrix'][0][1], matrix[names[0]][names[1]])
        self.assertAlmostEqual(result['matrix'][0][1] + result['matrix'][1][0], 1)
        self.assertTrue(result['rankings'])

    def test_balanced_matrix_contribution_excludes_diagonal(self):
        from sim import compute_contribution_from_matrix
        result = compute_contribution_from_matrix([[.5, .5], [.5, .5]], [['Y1'], ['Y2']], {'Y1': 'A', 'Y2': 'B'})
        self.assertTrue(all(row[2] == 0 for row in result['algo1']))

    def test_verbose_and_persistent_game_worker_produce_actual_results(self):
        rows = self.run_worker(dict(mode='verbose', team_a=['Y1'], team_b=['Y1'], n=1, seed=4, season=50))
        self.assertIn(rows[-1]['result']['winner'], ('Y', 'X'))
        self.assertTrue(any('D100' in r.get('log', '') for r in rows))
        params = dict(mode='game', team_a=['Y1'], team_b=['Y1'], seed=19, season=50, max_rounds=20)
        run = subprocess.run([sys.executable, str(self.root/'ui_task_worker.py'), '--server'], cwd=self.root,
            input=(json.dumps(params)+'\n')*2, capture_output=True, text=True, encoding='utf-8', timeout=60,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        self.assertEqual(run.returncode, 0, run.stderr)
        results = [json.loads(line)['result'] for line in run.stdout.splitlines() if '"result"' in line]
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])

    def test_training_writes_only_isolated_weights(self):
        # Keep the public/default configuration unchanged; shorten only this
        # disposable snapshot to exercise the real multiprocessing/save path.
        trainer = self.root/'ai_trainer.py'
        trainer.write_text(trainer.read_text(encoding='utf-8').replace('GENERATIONS = 50', 'GENERATIONS = 1')
                           .replace('BATTLES_PER_GEN = 100', 'BATTLES_PER_GEN = 2')
                           .replace('NUM_WORKERS = 12', 'NUM_WORKERS = 1'), encoding='utf-8')
        before = (ROOT/'ai_weights.json').read_bytes()
        saved_weights = json.loads((self.root/'ai_weights.json').read_text(encoding='utf-8'))
        sentinel = '0__old_training_sentinel'
        saved_weights.setdefault('Q_solo', {})['Y13'] = {sentinel: 1.25}
        saved_weights.setdefault('Q_team', {})['Y13'] = {sentinel: -2.5}
        saved_weights.setdefault('Q_solo', {})['Y2'] = {sentinel: 7.5}
        saved_weights.setdefault('Q_summon', {})['虚假之月'] = {sentinel: 4.5}
        (self.root/'ai_weights.json').write_text(json.dumps(saved_weights), encoding='utf-8')
        data_path = self.root/'characters_data.py'
        original = data_path.read_text(encoding='utf-8')
        tree = ast.parse(original)
        definitions = {}
        for n in tree.body:
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict):
                for key, value in zip(n.value.keys, n.value.values):
                    if isinstance(key, ast.Constant) and key.value == 'serial' and isinstance(value, ast.Constant):
                        definitions[value.value] = n.targets[0].id
        active = next(n for n in tree.body if isinstance(n, ast.Assign)
                      and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'ALL_CHARACTERS')
        for enabled, summons in [(['Y1', 'Y3', 'Y4', 'Y5', 'Y6', 'Y7'], ['小雪人']),
                                 (['Y1', 'Y2', 'Y3', 'Y4', 'Y5', 'Y6'], ['使魔', '小雪人'])]:
            with self.subTest(enabled=enabled):
                lines = original.splitlines(keepends=True)
                lines[active.lineno-1:active.end_lineno] = ['ALL_CHARACTERS = [' + ', '.join(definitions[s] for s in enabled) + ']\n']
                data_path.write_text(''.join(lines), encoding='utf-8')
                # Remove only this disposable module's cache; the second pass
                # deliberately changes membership without changing list size.
                for cached in (self.root/'__pycache__').glob('characters_data.*.pyc'):
                    cached.unlink()
                previous = json.loads((self.root/'ai_weights.json').read_text(encoding='utf-8'))
                result = self.run_worker({'mode': 'train', 'seed': 42, 'enabled_serials': enabled})
                self.assertEqual(result[-1]['completed'], 1)
                self.assertEqual(result[-1]['result']['characters'], enabled)
                self.assertEqual(result[-1]['result']['summons'], summons)
                weights = json.loads((self.root/'ai_weights.json').read_text(encoding='utf-8'))
                self.assertEqual(weights['params']['characters'], enabled)
                self.assertEqual(weights['params']['summons'], summons)
                for table, active_roster in [('Q_solo', enabled), ('Q_team', enabled), ('Q_summon', summons)]:
                    for key, entries in previous[table].items():
                        if key not in active_roster:
                            self.assertEqual(weights[table][key], entries)
                    self.assertTrue(set(weights[table]) <= set(previous[table]) | set(active_roster))
                self.assertEqual(weights['Q_solo']['Y13'], {sentinel: 1.25})
                self.assertEqual(weights['Q_team']['Y13'], {sentinel: -2.5})
                self.assertEqual(weights['Q_summon']['虚假之月'], {sentinel: 4.5})
                self.assertIn('Y13', result[-1]['result']['preserved_characters'])
                if 'Y2' in enabled:
                    self.assertNotIn(sentinel, weights['Q_solo'].get('Y2', {}))
        self.assertEqual((ROOT/'ai_weights.json').read_bytes(), before)

    def test_targeted_training_real_worker_preserves_unselected_tables(self):
        trainer = self.root/'ai_trainer.py'
        trainer.write_text(trainer.read_text(encoding='utf-8').replace('GENERATIONS = 50', 'GENERATIONS = 1')
                           .replace('BATTLES_PER_GEN = 100', 'BATTLES_PER_GEN = 4')
                           .replace('NUM_WORKERS = 12', 'NUM_WORKERS = 1'), encoding='utf-8')
        import characters_data
        enabled = [ch['serial'] for ch in characters_data.ALL_CHARACTERS]
        targets = enabled[:2]
        original = (ROOT/'ai_weights.json').read_bytes()
        path = self.root/'ai_weights.json'
        before = json.loads(path.read_text(encoding='utf-8'))
        for kind in ('Q_solo', 'Q_team'):
            for serial in enabled:
                before.setdefault(kind, {})[serial] = {'0__sentinel': 123}
        path.write_text(json.dumps(before), encoding='utf-8')
        rows = self.run_worker({'mode': 'train', 'seed': 42, 'enabled_serials': enabled, 'target_serials': targets})
        saved = json.loads(path.read_text(encoding='utf-8'))
        result = rows[-1]['result']
        self.assertEqual(result['target_serials'], targets)
        self.assertEqual(result['participant_serials'], enabled)
        for kind in ('Q_solo', 'Q_team'):
            for serial in enabled:
                if serial in targets:
                    self.assertNotIn('0__sentinel', saved[kind].get(serial, {}))
                else:
                    self.assertEqual(saved[kind][serial], before[kind][serial])
        for name, table in before.get('Q_summon', {}).items():
            if name not in result['summons']:
                self.assertEqual(saved['Q_summon'][name], table)
        self.assertEqual((ROOT/'ai_weights.json').read_bytes(), original)

    def test_imported_trainer_reads_live_roster_and_freezes_each_run(self):
        import ai_trainer
        import characters_data as data
        lookup = {ch['serial']: ch for ch in vars(data).values() if isinstance(ch, dict) and 'serial' in ch and 'attrs' in ch}
        selected = copy.deepcopy([lookup[s] for s in ['Y1', 'Y2']])
        with patch.object(data, 'ALL_CHARACTERS', selected):
            first = ai_trainer.QTrainer()
            first.init_characters()
            self.assertEqual(list(first.char_map), ['Y1', 'Y2'])
            self.assertEqual(first.training_summons, ['使魔'])
            selected[0]['name'] = 'later edit'
            self.assertNotEqual(first.characters[0]['name'], selected[0]['name'])
        with patch.object(data, 'ALL_CHARACTERS', [lookup['Y1']]):
            second = ai_trainer.QTrainer()
            second.init_characters()
            self.assertEqual(list(second.char_map), ['Y1'])
            self.assertEqual(second.training_summons, [])
            self.assertEqual(list(first.char_map), ['Y1', 'Y2'])
            with patch.object(ai_trainer.QTrainer, 'train') as run:
                with self.assertRaisesRegex(ValueError, '快照不一致'):
                    ai_trainer.main(expected_serials=['Y99'])
                run.assert_not_called()

    def test_legacy_weights_keep_untrained_entries_and_replace_only_active_tables(self):
        from ai_trainer import merge_untrained_tables
        previous = {'Q': {'Y1': {'old': 1}, 'Y13': {'keep': 2}},
                    'Q_summon': {'使魔': {'old': 3}, '虚假之月': {'keep': 4}}}
        original = copy.deepcopy(previous)
        tables = merge_untrained_tables(previous, {'Q_solo': {'Y1': {'new': 5}},
            'Q_team': {'Y1': {'new': 6}}, 'Q_summon': {'使魔': {'new': 7}}}, ['Y1'], ['使魔'])
        self.assertEqual(tables['Q_solo']['Y13'], {'keep': 2})
        self.assertEqual(tables['Q_team']['Y13'], {'keep': 2})
        self.assertEqual(tables['Q_summon']['虚假之月'], {'keep': 4})
        self.assertEqual(tables['Q_solo']['Y1'], {'new': 5})
        self.assertEqual(previous, original)

    def test_game_replay_matches_ai_battle_round_display_and_verbose_keeps_details(self):
        params = dict(mode='game', team_a=['Y1'], team_b=['Y2'], seed=22, season=50, max_rounds=4)
        direct = self.run_worker(params)
        stepped = self.run_worker(dict(params, verbose=True))
        replay = direct[-1]['replay']
        self.assertTrue(replay)
        self.assertEqual(replay, [r['round'] for r in direct if 'round' in r])
        self.assertEqual(replay, stepped[-1]['replay'])
        self.assertEqual(direct[-1]['result'], stepped[-1]['result'])
        terminal = [re.sub(r'^\d{2}:\d{2}:\d{2} ', '', r['log']) for r in direct
                    if re.match(r'^\d{2}:\d{2}:\d{2} +第\d+回合 Y: ', r.get('log', ''))]
        self.assertEqual(replay, terminal)
        self.assertTrue(all(re.match(r'^    第\d+回合 Y: .*\(HP:.* \| X: ', line) for line in replay))
        self.assertFalse(any('D100' in r.get('log', '') for r in direct))
        self.assertTrue(any('D100' in r.get('log', '') for r in stepped))


if __name__ == '__main__':
    unittest.main()
