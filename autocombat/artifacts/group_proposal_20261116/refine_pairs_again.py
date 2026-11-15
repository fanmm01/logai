"""One final 2v2 pairing search using the refined table's measured matrix."""
from itertools import combinations
from pathlib import Path
import json, math, random
import numpy as np

OUT=Path(__file__).resolve().parent
ROSTER=[f'Y{i}' for i in range(1,18)]+['Y60']
RNG=random.Random(2026111609)
data=json.loads((OUT/'validation_refined.json').read_text(encoding='utf-8'))
rows=[r for r in data['rows'] if r['format']=='2v2']
keys=[(s,) for s in ROSTER]+list(combinations(ROSTER,2));ix={frozenset(k):i for i,k in enumerate(keys)}
def feature(team):
    v=np.zeros(len(keys))
    for s in team:v[ix[frozenset((s,))]]=1
    for p in combinations(team,2):v[ix[frozenset(p)]]=1
    return v
X=np.array([feature(r['a'])-feature(r['b']) for r in rows])
y=np.array([.5 if r['win'] else -.5 for r in rows])
beta=np.linalg.solve(X.T@X+np.diag([8.]*18+[60.]*(len(keys)-18)),X.T@y)
scores={frozenset(t):float(feature(t)@beta) for t in combinations(ROSTER,2)}

circle=list(range(18)); rounds=[]
for _ in range(17):
    rounds.append([(circle[i],circle[-1-i]) for i in range(9)])
    circle=[circle[0],circle[-1],*circle[1:-1]]

def energy(vals):return sum(v*v for v in vals)+max(abs(v) for v in vals)**2
best=None
for restart in range(16):
    order=ROSTER[:];RNG.shuffle(order)
    teams=[[order[a],order[b]] for ri in RNG.sample(range(17),3) for a,b in rounds[ri]]
    vals=[scores[frozenset(t)] for t in teams];e=energy(vals)
    for step in range(9000):
        i,j=RNG.sample(range(27),2);x,y=RNG.randrange(2),RNG.randrange(2)
        teams[i][x],teams[j][y]=teams[j][y],teams[i][x]
        pairs=[frozenset(t) for t in teams]
        if all(len(p)==2 for p in pairs) and len(set(pairs))==27:
            trial=vals[:];trial[i]=scores[pairs[i]];trial[j]=scores[pairs[j]]
            ne=energy(trial);temp=.008*(1-step/9000)**2+.00003
            if ne<e or RNG.random()<math.exp(min(0,(e-ne)/temp)):e=ne;vals=trial
            else:teams[i][x],teams[j][y]=teams[j][y],teams[i][x]
        else:teams[i][x],teams[j][y]=teams[j][y],teams[i][x]
    print('restart',restart,'score',e,flush=True)
    if best is None or e<best[0]:best=(e,[t[:] for t in teams])
proposal=json.loads((OUT/'proposal_final.json').read_text(encoding='utf-8'))
teams=[sorted(t,key=ROSTER.index) for t in best[1]]
teams.sort(key=lambda t:tuple(ROSTER.index(s) for s in t))
proposal['teams']['2v2']=teams
proposal['appearances']['2v2']={s:3 for s in ROSTER}
(OUT/'proposal_final_v2.json').write_text(json.dumps(proposal,ensure_ascii=False,indent=2),encoding='utf-8')
