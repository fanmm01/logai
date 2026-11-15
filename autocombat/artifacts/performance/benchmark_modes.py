import json, os, subprocess, sys, time
from pathlib import Path
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root))
from combat_defaults import DEFAULTS
out=json.loads((root/'artifacts/performance/modes.json').read_text()) if (root/'artifacts/performance/modes.json').exists() else {}; env={**os.environ,'PYTHONIOENCODING':'utf-8'}
cfg={**DEFAULTS,'RR_ROUNDS_1V1':1,'TARGET_1V1_MATCHES':30,
 'RR_ROUNDS_2V2':1,'RR_ROUNDS_3V3':1,'RANDOM_BONUS_2V2':0,'RANDOM_BONUS_3V3':0,
 'TEAM_TABLES_2V2':[['Y1','Y2'],['Y3','Y4']],
 'TEAM_TABLES_3V3':[['Y1','Y2','Y3'],['Y4','Y5','Y6']]}
for mode in (sys.argv[1:] or ['prelim','verbose_cli','tournament']):
 for label,folder in [('before',root/'artifacts/performance/baseline'),('after',root)]:
    params=None
    if mode=='prelim':
        cmd=[sys.executable,'ui_task_worker.py']
        params=json.dumps(dict(mode='prelim',formats=['1v1','2v2','3v3'],n=1,seed=31415,
            season=50,workers=8,settings=cfg))+'\n'
    elif mode=='verbose_cli':
        cmd=[sys.executable,'sim.py','Y1+Y2','vs','Y3+Y4','--verbose','-s','31415','--season','50']
        if label=='after': cmd+=['--fast']
    else:
        cmd=[sys.executable,str(root/'artifacts/performance/tournament_benchmark.py'),str(folder)]
    start=time.monotonic()
    result=subprocess.run(cmd,cwd=folder,input=params,text=True,encoding='utf-8',capture_output=True,env=env,timeout=240)
    elapsed=time.monotonic()-start
    (root/f'artifacts/performance/{mode}_{label}.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    assert result.returncode==0,(mode,label,result.stderr[-1500:])
    out[mode+'_'+label]={'seconds':elapsed,'exit_code':result.returncode}
    print(mode,label,elapsed,flush=True)
    (root/'artifacts/performance/modes.json').write_text(json.dumps(out,indent=2))
