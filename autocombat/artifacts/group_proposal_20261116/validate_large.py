"""Head-to-head validation for the 27/30 proposal."""
from collections import Counter
from concurrent.futures import as_completed
from itertools import combinations
import json
from pathlib import Path
import random
import time
import hashlib
import sys
import argparse

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))

def main():
    from combat_settings import snapshot
    from sim_parallel import Battles
    parser=argparse.ArgumentParser();parser.add_argument('--candidate',default='proposal_27_30.json');args=parser.parse_args()
    data=json.loads((OUT/args.candidate).read_text(encoding='utf-8'))
    tag='refined' if 'refined' in args.candidate else 'v2' if 'v2' in args.candidate else '27_30'
    config=snapshot();season={s:50 for s in [f'Y{i}' for i in range(1,18)]+['Y60']}
    rng=random.Random(2026111607);tasks=[]
    for fmt,teams in data['teams'].items():
        for a,b in combinations(teams,2):
            for _ in range(2):
                seed=rng.getrandbits(50)
                tasks.extend([(fmt,a,b,seed),(fmt,b,a,seed)])
    rows=[];errors=[];start=time.time()
    with Battles(8,config,season) as pool:
        futures={pool.submit(seed,a,b,'10x10',display_level=0,delay=0,
                   max_rounds=30 if fmt=='2v2' else 45):(fmt,a,b,seed)
                 for fmt,a,b,seed in tasks}
        for future in as_completed(futures):
            fmt,a,b,seed=futures[future]
            try:r=future.result()[3]
            except Exception as exc:
                errors.append(dict(format=fmt,a=a,b=b,seed=seed,error=repr(exc)));continue
            rows.append(dict(format=fmt,a=a,b=b,seed=seed,win=r['winner']=='Y',timeout=bool(r.get('timeout')),rounds=r['rounds']))
            if len(rows)%200==0:print(len(rows),'/',len(tasks),flush=True)
    fingerprints={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in
                  ('ai_battle.py','battle_engine.py','ai_trainer.py','characters_data.py','ai_weights.json')}
    (OUT/f'validation_{tag}.json').write_text(json.dumps(dict(rows=rows,errors=errors,settings=config,
        season=season,fingerprints=fingerprints,elapsed=time.time()-start),ensure_ascii=False),encoding='utf-8')
    summary={}
    for fmt,teams in data['teams'].items():
        result=[]
        for team in teams:
            matches=[r for r in rows if r['format']==fmt and frozenset(team) in (frozenset(r['a']),frozenset(r['b']))]
            wins=sum(r['win'] if frozenset(r['a'])==frozenset(team) else not r['win'] for r in matches)
            result.append(dict(team=team,games=len(matches),wins=wins,rate=wins/len(matches) if matches else None))
        summary[fmt]=result
    (OUT/f'summary_{tag}.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print('errors',len(errors),'summary saved')
    for fmt,result in summary.items():
        rates=[r['rate'] for r in result if r['rate'] is not None]
        print(fmt, len(result),min(rates),max(rates))

if __name__=='__main__':main()
