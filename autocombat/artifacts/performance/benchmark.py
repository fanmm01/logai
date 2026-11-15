import json, os, subprocess, sys, time
from pathlib import Path
root=Path(__file__).resolve().parents[2]
out={}
env={**os.environ,'PYTHONIOENCODING':'utf-8'}
for label,folder,extra in [('before',root/'artifacts/performance/baseline',[]),('after_1',root,['--workers','1']),('after_8',root,['--workers','8'])]:
    cmd=[sys.executable,'sim.py','Y1+Y2','vs','Y3+Y4','-n','400','-s','31415','--season','50',*extra]
    start=time.monotonic()
    result=subprocess.run(cmd,cwd=folder,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env)
    elapsed=time.monotonic()-start
    (root/f'artifacts/performance/{label}.log').write_bytes(result.stdout)
    out[label]={'seconds':elapsed,'exit_code':result.returncode}
    print(label,out[label],flush=True)
    (root/'artifacts/performance/benchmark.json').write_text(json.dumps(out,indent=2))
