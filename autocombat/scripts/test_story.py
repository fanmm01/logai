"""Run only story tests, optionally measuring all new server modules."""
import argparse,os,subprocess,sys,tempfile,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--coverage',action='store_true');args=parser.parse_args()
    tests=['tests.'+p.stem for p in sorted((ROOT/'dev/tests').glob('test_story_*.py'))]+['tests.test_ui_story']
    with tempfile.TemporaryDirectory(prefix='story-tests-') as runtime:
        env=dict(os.environ,AUTOCOMBAT_RUNTIME_DIR=runtime,AUTOCOMBAT_NO_FILE_LOG='1',PYTHONIOENCODING='utf-8',PYTHONPATH=os.pathsep.join([str(ROOT/'backend'),str(ROOT/'dev'),str(ROOT/'dev/tests')]))
        command=[sys.executable,'-m']+(['coverage','run','--branch','--include=backend/story_*.py,backend/ui_story.py','-m'] if args.coverage else [])+['unittest',*tests,'-q']
        result=subprocess.run(command,cwd=ROOT,env=env)
        if args.coverage:
            (ROOT/'.tmpwork').mkdir(exist_ok=True)
            subprocess.run([sys.executable,'-m','coverage','report','-m'],cwd=ROOT,env=env)
            subprocess.run([sys.executable,'-m','coverage','json','-o',str(ROOT/'.tmpwork/story-coverage.json')],cwd=ROOT,env=env)
            totals=json.loads((ROOT/'.tmpwork/story-coverage.json').read_text(encoding='utf-8'))['totals']
            if totals['percent_statements_covered']<85 or totals['percent_branches_covered']<80:
                print('Story coverage requires >=85% lines and >=80% branches');return 1
        return result.returncode


if __name__=='__main__':raise SystemExit(main())
