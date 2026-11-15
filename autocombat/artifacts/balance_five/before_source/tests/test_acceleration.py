"""Real spawn-process regression checks for accelerated CLI/browser entrypoints."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class AccelerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for path in ROOT.glob('*.py'):
            if path.name != 'autocombat_backend.py':
                shutil.copy2(path, self.root/path.name)
        shutil.copy2(ROOT/'ai_weights.json', self.root/'ai_weights.json')

    def tearDown(self):
        self.temp.cleanup()

    def worker(self, **params):
        p = subprocess.run([sys.executable, 'ui_task_worker.py'], cwd=self.root,
            input=json.dumps(params)+'\n', text=True, encoding='utf-8', capture_output=True,
            timeout=90, env={**os.environ, 'PYTHONIOENCODING':'utf-8'})
        self.assertEqual(p.returncode, 0, p.stderr)
        return [json.loads(line) for line in p.stdout.splitlines()]

    def test_batch_reproducible_across_process_counts(self):
        params = dict(mode='single',team_a=['Y1','Y2'],team_b=['Y3','Y4'],n=12,seed=17,season=50)
        a = self.worker(**params, workers=1)[-1]['result']
        b = self.worker(**params, workers=4)[-1]['result']
        a.pop('elapsed'); b.pop('elapsed')
        self.assertEqual(a,b)
        p = subprocess.run([sys.executable,'sim.py','Y1+Y2','vs','Y3+Y4','-n','12',
            '-s','17','--season','50','--workers','4'],cwd=self.root,capture_output=True,
            text=True,encoding='utf-8',timeout=60,env={**os.environ,'PYTHONIOENCODING':'utf-8'})
        self.assertEqual(p.returncode,0,p.stderr)
        import re
        wins = re.findall(r'胜:\s*(\d+)场', p.stdout)
        self.assertEqual([int(x) for x in wins], [a['wins_a'], a['wins_b']])

    def test_fast_verbose_keeps_all_logs_and_result(self):
        params=dict(mode='verbose',team_a=['Y1'],team_b=['Y2'],n=1,seed=17,season=50)
        normal=self.worker(**params)
        fast=self.worker(**params,fast=True)
        self.assertEqual(normal[-1]['result'],fast[-1]['result'])
        # Timestamps may differ; compare each original log message in order.
        import re
        def logs(rows):
            lines=[]
            for row in rows:
                lines.extend(row.get('logs', [row['log']] if 'log' in row else []))
            return [re.sub(r'^\d\d:\d\d:\d\d ', '', line) for line in lines]
        self.assertEqual(logs(normal),logs(fast))
        self.assertTrue(any('D100' in line for line in logs(fast)))
        persistent = subprocess.run([sys.executable,'ui_task_worker.py','--server'],cwd=self.root,
            input=(json.dumps(dict(params,fast=True))+'\n')*2,text=True,encoding='utf-8',
            capture_output=True,timeout=60,env={**os.environ,'PYTHONIOENCODING':'utf-8'})
        self.assertEqual(persistent.returncode,0,persistent.stderr)
        games=[[]]
        for line in persistent.stdout.splitlines():
            row=json.loads(line)
            games[-1].append(row)
            if 'result' in row:
                games.append([])
        combat = lambda rows: [line for line in logs(rows) if not line.startswith('Q-tables loaded:')]
        self.assertEqual(combat(games[0]),combat(games[1]))

    def test_prelim_all_formats_reproducible(self):
        from combat_defaults import DEFAULTS
        cfg={**DEFAULTS,'MAX_ROUNDS_PRELIM':1,'MAX_ROUNDS_PRELIM_BY_FORMAT':{'1v1':1,'2v2':1,'3v3':1},
             'RR_ROUNDS_1V1':1,'TARGET_1V1_MATCHES':8,'RR_ROUNDS_2V2':1,'RR_ROUNDS_3V3':1,
             'RANDOM_BONUS_2V2':0,'RANDOM_BONUS_3V3':0,
             'TEAM_TABLES_2V2':[['Y1','Y2'],['Y3','Y4']],
             'TEAM_TABLES_3V3':[['Y1','Y2','Y3'],['Y4','Y5','Y6']]}
        params=dict(mode='prelim',formats=['1v1','2v2','3v3'],n=1,seed=17,season=50,settings=cfg)
        a=self.worker(**params,workers=1)[-1]['result']
        b=self.worker(**params,workers=4)[-1]['result']
        self.assertEqual(a,b)
        runtime=self.root/'.ui-runtime'
        runtime.mkdir(exist_ok=True)
        (runtime/'settings.json').write_text(json.dumps(cfg),encoding='utf-8')
        cli=subprocess.run([sys.executable,'sim.py','--mode','prelim','--format','all',
            '-n','1','-s','17','--season','50','--workers','4'],cwd=self.root,
            capture_output=True,text=True,encoding='utf-8',timeout=90,
            env={**os.environ,'PYTHONIOENCODING':'utf-8'})
        self.assertEqual(cli.returncode,0,cli.stderr)
        self.assertTrue((self.root/'prelim_avg_stats_results.txt').exists())

    def test_tournament_real_parallel_prelim(self):
        import test_ui_management as fixtures
        fixture=fixtures.ManagementTests('runTest')
        fixture.setUp()
        try:
            for path in self.root.glob('*.py'):
                if path.name != 'characters_data.py':
                    shutil.copy2(path, fixture.root/path.name)
            response=fixture.client.post('/api/ui/tournaments',json=dict(format='1v1',seed=17,
                teams=[['Y1'],['Y2'],['Y3'],['Y4']]))
            self.assertEqual(response.status_code,200)
            t=response.get_json()
            t['settings']['PRELIM_WORKERS']=2
            t['settings']['MAX_ROUNDS_PRELIM_BY_FORMAT']={'1v1':1}
            t['status']='running'
            fixture.tournaments.save(t)
            fixture.tournaments.run(t['id'])
            done=fixture.store.get('tournament',t['id'])
            self.assertEqual(done['completed'],len(t['schedule']))
            self.assertEqual(done['phase'],'playoffs')
            self.assertEqual(done['status'],'paused')
            self.assertEqual(fixture.tournaments.workers,{})
            self.assertEqual(sum(row['battles'] for row in done['standings']),2*done['completed'])
        finally:
            fixture.tearDown()

if __name__ == '__main__':
    unittest.main()
