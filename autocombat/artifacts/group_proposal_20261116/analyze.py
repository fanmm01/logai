"""Exploratory proposal only; never writes live settings or default team tables."""
import argparse
from collections import Counter
from concurrent.futures import as_completed
import hashlib
from itertools import combinations
import json
import math
import os
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent
ROSTER = [f'Y{i}' for i in range(1, 18)] + ['Y60']
FIXED = [['Y5', 'Y6', 'Y7', 'Y8'], ['Y9', 'Y12'], ['Y9', 'Y3']]


def save(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def simulate(tasks, name):
    from combat_settings import snapshot
    from sim_parallel import Battles
    config = snapshot()
    season = {s: 50 for s in ROSTER}
    rows = []
    errors = []
    start = time.time()
    with Battles(8, config, season) as pool:
        jobs = {pool.submit(seed, a, b, '10x10', display_level=0, delay=0,
                            max_rounds=30 if fmt == '2v2' else 45): (fmt, a, b, seed)
                for fmt, a, b, seed in tasks}
        for future in as_completed(jobs):
            fmt, a, b, seed = jobs[future]
            try:
                result = future.result()[3]
            except Exception as exc:
                errors.append(dict(format=fmt,a=a,b=b,seed=seed,error=repr(exc)))
                continue
            rows.append(dict(format=fmt, a=a, b=b, seed=seed,
                             win=result['winner'] == 'Y', timeout=bool(result.get('timeout')),
                             rounds=result['rounds']))
            if len(rows) % 200 == 0:
                print(name, len(rows), '/', len(tasks), round(time.time()-start, 1), flush=True)
    save(name, dict(settings=config, season=season, rows=rows, errors=errors, elapsed=time.time()-start,
                   fingerprints={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in
                                 ('ai_battle.py','battle_engine.py','ai_trainer.py','characters_data.py','ai_weights.json')}))
    return rows


def train():
    rng = random.Random(2026111601)
    tasks = []
    for size in (2, 3):
        for i in range(400):
            sample = rng.sample(ROSTER, size*2)
            a, b = sample[:size], sample[size:]
            seed = rng.getrandbits(50)
            tasks.extend([(f'{size}v{size}', a, b, seed), (f'{size}v{size}', b, a, seed)])
    simulate(tasks, 'training.json')


def optimize():
    import numpy as np
    rows = json.loads((OUT/'training.json').read_text(encoding='utf-8'))['rows']
    if (OUT/'validation.json').exists():
        rows += json.loads((OUT/'validation.json').read_text(encoding='utf-8'))['rows']
    pairs = list(combinations(ROSTER, 2))
    keys = [(s,) for s in ROSTER] + pairs
    idx = {frozenset(k):i for i,k in enumerate(keys)}
    def features(team):
        v = np.zeros(len(keys))
        for s in team: v[idx[frozenset([s])]] = 1
        for pair in combinations(team, 2): v[idx[frozenset(pair)]] = 1
        return v
    proposal = {}
    model = {}
    rng = random.Random(2026111602)
    for size in (2, 3):
        fmt = f'{size}v{size}'
        data = [r for r in rows if r['format'] == fmt]
        X = np.array([features(r['a'])-features(r['b']) for r in data])
        y = np.array([float(r['win'])-.5 for r in data])
        coef = np.linalg.solve(X.T@X + np.diag([4.]*18+[20.]*len(pairs)), X.T@y)
        model[fmt] = dict(character=dict(zip(ROSTER,coef[:18].tolist())),
                          pair={'+'.join(p):float(coef[18+i]) for i,p in enumerate(pairs)})
        score = {frozenset(t):float(features(t)@coef) for t in combinations(ROSTER, size)}
        fixed_counts = Counter(s for t in FIXED for s in t) if size == 3 else Counter()
        best = None
        # Slot swaps keep all appearance counts exact. The missing third slot can move.
        for restart in range(12):
            deficit = 'Y9'
            slots = [s for s in ROSTER for _ in range((2 if size==2 else 3-(s==deficit))-fixed_counts[s])]
            rng.shuffle(slots)
            teams = [slots[i:i+size] for i in range(0,len(slots),size)]
            def objective(ts):
                seen = Counter()
                values = []
                bad = 0
                for t in ts:
                    if len(set(t)) != size:
                        bad += 3
                        values.append(0)
                    else:
                        values.append(score[frozenset(t)])
                        seen.update(frozenset(p) for p in combinations(t,2))
                bad += sum(n-1 for n in seen.values() if n>1)
                return 10*bad + sum(v*v for v in values) + max(abs(v) for v in values)**2
            energy = objective(teams)
            for step in range(18000):
                i,j = rng.sample(range(len(teams)),2)
                x,y = rng.randrange(size),rng.randrange(size)
                old_deficit = deficit
                move_deficit = size==3 and rng.random()<.06 and teams[i][x] != deficit and fixed_counts[teams[i][x]]<3
                if move_deficit:
                    old = teams[i][x]
                    teams[i][x], deficit = deficit, old
                else:
                    teams[i][x],teams[j][y] = teams[j][y],teams[i][x]
                new = objective(teams)
                temp = .015*(1-step/18000)**3 + .00002
                if new < energy or rng.random() < math.exp(min(0,(energy-new)/temp)):
                    energy = new
                elif move_deficit:
                    teams[i][x], deficit = deficit, old_deficit
                else:
                    teams[i][x],teams[j][y] = teams[j][y],teams[i][x]
                if best is None or energy < best[0]:
                    best = (energy, [list(t) for t in teams], deficit)
            print(fmt, restart, best[0], flush=True)
        teams = [sorted(t,key=ROSTER.index) for t in best[1]]
        teams.sort(key=lambda t:tuple(ROSTER.index(s) for s in t))
        if size==3: teams = FIXED + teams
        counts = Counter(s for t in teams for s in t)
        assert len(teams)==18
        assert sorted(counts.values()) == ([2]*18 if size==2 else [2]+[3]*17)
        triples = [set(t) for t in teams if len(t)==3]
        assert all(len(a&b)<=1 for a,b in combinations(triples,2))
        assert all(len(t)==len(set(t)) for t in teams)
        assert len({frozenset(t) for t in teams})==len(teams)
        proposal[fmt] = dict(teams=teams, appearances=dict(counts), objective=best[0],
                             predicted_score={'+'.join(t):score[frozenset(t)] for t in teams if len(t)==size})
    save('models.json',model)
    save('proposal.json',proposal)


def validate(only=None):
    proposal=json.loads((OUT/'proposal.json').read_text(encoding='utf-8'))
    rng=random.Random(2026111603)
    tasks=[]
    for fmt, item in proposal.items():
        if only and fmt != only: continue
        for a,b in combinations(item['teams'],2):
            for k in range(4):
                seed=rng.getrandbits(50)
                tasks.extend([(fmt,a,b,seed),(fmt,b,a,seed)])
    rows=simulate(tasks,'validation2.json' if only else 'validation.json')
    summary={}
    for fmt,item in proposal.items():
        if only and fmt != only: continue
        stats={frozenset(t):dict(team=t,games=0,wins=0,timeouts=0) for t in item['teams']}
        for r in rows:
            if r['format']!=fmt: continue
            for side in ('a','b'):
                s=stats[frozenset(r[side])]
                s['games']+=1
                s['wins']+=int(r['win'] if side=='a' else not r['win'])
                s['timeouts']+=int(r['timeout'])
        for s in stats.values(): s['rate']=s['wins']/s['games']
        summary[fmt]=list(stats.values())
    save('summary2.json' if only else 'summary.json',summary)


def validate2():
    validate('2v2')


def fixed():
    tasks=[]
    for j in (1,2):
        for i in range(50):
            seed=202611169000+j*100+i
            tasks.extend([('3v3',FIXED[0],FIXED[j],seed),('3v3',FIXED[j],FIXED[0],seed)])
    simulate(tasks,'fixed.json')


def refine3():
    proposal=json.loads((OUT/'proposal.json').read_text(encoding='utf-8'))
    old=[['Y1','Y6','Y10'],['Y4','Y14','Y16']]
    new=[['Y6','Y10','Y16'],['Y1','Y4','Y14']]
    teams=proposal['3v3']['teams']
    for a,b in zip(old,new): teams[teams.index(a)]=b
    triples=[set(t) for t in teams if len(t)==3]
    assert all(len(a&b)<=1 for a,b in combinations(triples,2))
    assert dict(Counter(s for t in teams for s in t))==proposal['3v3']['appearances']
    proposal['3v3'].pop('predicted_score',None)
    proposal['3v3'].pop('objective',None)
    save('proposal.json',proposal)
    tasks=[]
    rng=random.Random(2026111604)
    for a,b in combinations(teams,2):
        if a not in new and b not in new: continue
        for k in range(4):
            seed=rng.getrandbits(50)
            tasks.extend([('3v3',a,b,seed),('3v3',b,a,seed)])
    simulate(tasks,'validation3_patch.json')


def refine2():
    proposal=json.loads((OUT/'proposal.json').read_text(encoding='utf-8'))
    proposal['2v2']=json.loads((OUT/'proposal_first.json').read_text(encoding='utf-8'))['2v2']
    old=[['Y5','Y12'],['Y10','Y14']]
    new=[['Y5','Y10'],['Y12','Y14']]
    teams=proposal['2v2']['teams']
    for a,b in zip(old,new): teams[teams.index(a)]=b
    assert set(Counter(s for t in teams for s in t).values())=={2}
    assert len({frozenset(t) for t in teams})==18
    proposal['2v2'].pop('predicted_score',None)
    proposal['2v2'].pop('objective',None)
    save('proposal.json',proposal)
    tasks=[]
    rng=random.Random(2026111605)
    for a,b in combinations(teams,2):
        if a not in new and b not in new: continue
        for k in range(4):
            seed=rng.getrandbits(50)
            tasks.extend([('2v2',a,b,seed),('2v2',b,a,seed)])
    simulate(tasks,'validation2_patch.json')


if __name__=='__main__':
    os.environ['PYTHONHASHSEED']='0'
    p=argparse.ArgumentParser(); p.add_argument('mode',choices=['train','optimize','validate','validate2','fixed','refine3','refine2'])
    args=p.parse_args()
    globals()[args.mode]()
