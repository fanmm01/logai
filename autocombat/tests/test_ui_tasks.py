"""Exercise real isolated workers, matrix CLI parity and a small training run."""
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

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
        result = self.run_worker({'mode': 'train', 'seed': 42})
        self.assertEqual(result[-1]['completed'], 1)
        weights = json.loads((self.root/'ai_weights.json').read_text(encoding='utf-8'))
        self.assertIn('Q_solo', weights)
        self.assertEqual((ROOT/'ai_weights.json').read_bytes(), before)

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
