"""Refit team-strength estimates on the first 27/30 full-matrix run."""
from collections import Counter
from itertools import combinations
import json, math, random
from pathlib import Path
import numpy as np

OUT=Path(__file__).resolve().parent
ROSTER=[f'Y{i}' for i in range(1,18)]+['Y60']
FIXED=[['Y5','Y60','Y7','Y8'],['Y9','Y12'],['Y9','Y3']]
RNG=random.Random(2026111608)
data=json.loads((OUT/'validation_27_30.json').read_text(encoding='utf-8'))

def fit(fmt):
    keys=[(s,) for s in ROSTER]+list(combinations(ROSTER,2))
    index={frozenset(k):i for i,k in enumerate(keys)}
    def vec(team):
        v=np.zeros(len(keys))
        for s in team:v[index[frozenset((s,))]]=1
        for p in combinations(team,2):v[index[frozenset(p)]]=1
        return v
    rows=[r for r in data['rows'] if r['format']==fmt]
    x=np.array([vec(r['a'])-vec(r['b']) for r in rows])
    y=np.array([.5 if r['win'] else -.5 for r in rows])
    penalty=np.array([8.]*18+[60.]*(len(keys)-18))
    beta=np.linalg.solve(x.T@x+np.diag(penalty),x.T@y)
    scores={frozenset(t):float(vec(t)@beta) for n in (2,3) for t in combinations(ROSTER,n)}
    print(fmt,'fit rows',len(rows),'character effects range',round(beta[:18].min(),3),round(beta[:18].max(),3))
    return scores

def energy(values):return sum(v*v for v in values)+max(abs(v) for v in values)**2

def optimize_pairs(scores, initial):
    teams=[t[:] for t in initial]
    def valid(ts):
        ps=[frozenset(t) for t in ts]
        return all(len(x)==2 for x in ps) and len(set(ps))==len(ps)
    vals=[scores[frozenset(t)] for t in teams];e=energy(vals)
    for step in range(30000):
        i,j=RNG.sample(range(27),2);x,y=RNG.randrange(2),RNG.randrange(2)
        teams[i][x],teams[j][y]=teams[j][y],teams[i][x]
        if valid(teams):
            trial=vals[:];trial[i]=scores[frozenset(teams[i])];trial[j]=scores[frozenset(teams[j])]
            ne=energy(trial);temp=.008*(1-step/30000)**2+.00003
            if ne<e or RNG.random()<math.exp(min(0,(e-ne)/temp)):e=ne;vals=trial
            else:teams[i][x],teams[j][y]=teams[j][y],teams[i][x]
        else:teams[i][x],teams[j][y]=teams[j][y],teams[i][x]
    return teams

def optimize_triples(scores, initial):
    fixed=[t[:] for t in initial[:3]]
    teams=[t[:] for t in initial[3:]]
    pi=[{frozenset(p) for p in combinations(t,2)} for t in teams]
    used=set().union(*pi)
    vals=[scores[frozenset(t)] for t in teams];e=energy(vals)
    for step in range(30000):
        i,j=RNG.sample(range(27),2);x,y=RNG.randrange(3),RNG.randrange(3)
        teams[i][x],teams[j][y]=teams[j][y],teams[i][x]
        ni={frozenset(p) for p in combinations(teams[i],2)}
        nj={frozenset(p) for p in combinations(teams[j],2)}
        remaining=used-pi[i]-pi[j]
        if len(set(teams[i]))==3 and len(set(teams[j]))==3 and len(ni|nj)==6 and not ((ni|nj)&remaining):
            trial=vals[:];trial[i]=scores[frozenset(teams[i])];trial[j]=scores[frozenset(teams[j])]
            ne=energy(trial);temp=.006*(1-step/30000)**2+.00003
            if ne<e or RNG.random()<math.exp(min(0,(e-ne)/temp)):
                e=ne;vals=trial;used=remaining|ni|nj;pi[i],pi[j]=ni,nj
            else:teams[i][x],teams[j][y]=teams[j][y],teams[i][x]
        else:teams[i][x],teams[j][y]=teams[j][y],teams[i][x]
    return fixed+teams

def main():
    scores2=fit('2v2');scores3=fit('3v3')
    proposal=json.loads((OUT/'proposal_27_30.json').read_text(encoding='utf-8'))
    proposal['teams']['2v2']=optimize_pairs(scores2,proposal['teams']['2v2'])
    proposal['teams']['3v3']=optimize_triples(scores3,proposal['teams']['3v3'])
    proposal['appearances']={fmt:dict(Counter(s for t in ts for s in t)) for fmt,ts in proposal['teams'].items()}
    for team in proposal['teams']['2v2']:team.sort(key=ROSTER.index)
    for team in proposal['teams']['3v3'][3:]:team.sort(key=ROSTER.index)
    proposal['teams']['3v3'][:3]=FIXED
    for fmt,number in [('2v2',27),('3v3',30)]:assert len(proposal['teams'][fmt])==number
    triples=[set(t) for t in proposal['teams']['3v3'] if len(t)==3]
    assert all(len(a&b)<=1 for a,b in combinations(triples,2))
    assert Counter(s for t in proposal['teams']['2v2'] for s in t)==Counter({s:3 for s in ROSTER})
    assert Counter(s for t in proposal['teams']['3v3'] for s in t)==Counter({**{s:5 for s in ROSTER},'Y9':4})
    proposal['strength_fit']='refit on initial 27/30 head-to-head matrix'
    (OUT/'proposal_27_30_refined.json').write_text(json.dumps(proposal,ensure_ascii=False,indent=2),encoding='utf-8')
    print('refined candidate saved')

if __name__=='__main__':main()
