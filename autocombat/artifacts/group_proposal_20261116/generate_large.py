"""Generate 27 balanced pairs and 30 balanced triples without touching live config."""
from collections import Counter
from itertools import combinations
import json
import math
from pathlib import Path
import random

OUT=Path(__file__).resolve().parent
ROSTER=[f'Y{i}' for i in range(1,18)]+['Y60']
FIXED=[['Y5','Y60','Y7','Y8'],['Y9','Y12'],['Y9','Y3']]
MODELS=json.loads((OUT/'models_first.json').read_text(encoding='utf-8'))
RNG=random.Random(2026111606)


def score(fmt, team):
    model=MODELS[fmt]
    return sum(model['character'][s] for s in team)+sum(model['pair']['+'.join(p)] for p in combinations(sorted(team,key=ROSTER.index),2))


def balanced_2v2():
    fmt='2v2'
    scores={frozenset(t):score(fmt,t) for t in combinations(ROSTER,2)}
    best=None
    circle=list(range(18)); rounds=[]
    for _ in range(17):
        rounds.append([(circle[i],circle[-1-i]) for i in range(9)])
        circle=[circle[0],circle[-1],*circle[1:-1]]
    for restart in range(4):
        order=ROSTER[:];RNG.shuffle(order)
        teams=[[order[a],order[b]] for ri in RNG.sample(range(17),3) for a,b in rounds[ri]]
        def valid(ts):
            pairs=[frozenset(t) for t in ts]
            return all(len(p)==2 for p in pairs) and len(set(pairs))==27
        # Reject duplicate partner pairings, preserving exactly 3 appearances per character.
        def energy(ts):
            values=[scores[frozenset(t)] for t in ts]
            return sum(v*v for v in values)+max(abs(v) for v in values)**2
        e=energy(teams)
        for step in range(6000):
            i,j=RNG.sample(range(27),2);x,y=RNG.randrange(2),RNG.randrange(2)
            teams[i][x],teams[j][y]=teams[j][y],teams[i][x]
            if valid(teams):
                ne=energy(teams);temp=.012*(1-step/6000)**2+.00005
                if ne<e or RNG.random()<math.exp(min(0,(e-ne)/temp)): e=ne
                else: teams[i][x],teams[j][y]=teams[j][y],teams[i][x]
            else: teams[i][x],teams[j][y]=teams[j][y],teams[i][x]
        if best is None or e<best[0]: best=(e,[t[:] for t in teams])
    teams=[sorted(t,key=ROSTER.index) for t in best[1]]
    teams.sort(key=lambda t:tuple(ROSTER.index(s) for s in t))
    assert len(teams)==27 and len({frozenset(t) for t in teams})==27
    assert Counter(s for t in teams for s in t)==Counter({s:3 for s in ROSTER})
    return teams


def balanced_3v3():
    fmt='3v3'
    scores={frozenset(t):score(fmt,t) for t in combinations(ROSTER,3)}
    # Fixed teams account for 8 of the 89 roster slots. Y9 appears four times;
    # all other characters appear five times.
    target=Counter({s:5 for s in ROSTER});target['Y9']=4
    fixed_counts=Counter(s for t in FIXED for s in t)
    slot_counts=target-fixed_counts
    slots=[s for s in ROSTER for _ in range(slot_counts[s])]
    assert len(slots)==81
    best=None
    for restart in range(4):
        remaining=slot_counts.copy();teams=[];used_pairs=set();nodes=[0]
        def build():
            nodes[0]+=1
            if nodes[0]>200000:return False
            if not any(remaining.values()):return len(teams)==27
            pivot=max((s for s in ROSTER if remaining[s]),key=lambda s:(remaining[s],RNG.random()))
            others=[s for s in ROSTER if s!=pivot and remaining[s]]
            choices=[(a,b) for a,b in combinations(others,2)
                     if frozenset((pivot,a)) not in used_pairs
                     and frozenset((pivot,b)) not in used_pairs
                     and frozenset((a,b)) not in used_pairs]
            RNG.shuffle(choices)
            for a,b in choices:
                trip=[pivot,a,b]
                ps={frozenset(p) for p in combinations(trip,2)}
                for s in trip:remaining[s]-=1
                teams.append(trip);used_pairs.update(ps)
                if build():return True
                teams.pop();used_pairs.difference_update(ps)
                for s in trip:remaining[s]+=1
            return False
        if not build():
            print('3v3 construction restart',restart,'nodes',nodes[0],flush=True)
            continue
        def energy(ts):
            vals=[scores[frozenset(t)] for t in ts]
            return sum(v*v for v in vals)+max(abs(v) for v in vals)**2
        e=energy(teams)
        pair_index=[{frozenset(p) for p in combinations(t,2)} for t in teams]
        used_pairs=set().union(*pair_index)
        values=[scores[frozenset(t)] for t in teams]
        for step in range(7000):
            i,j=RNG.sample(range(27),2);x,y=RNG.randrange(3),RNG.randrange(3)
            teams[i][x],teams[j][y]=teams[j][y],teams[i][x]
            pi={frozenset(p) for p in combinations(teams[i],2)}
            pj={frozenset(p) for p in combinations(teams[j],2)}
            remaining=used_pairs-pair_index[i]-pair_index[j]
            ok=(len(set(teams[i]))==3 and len(set(teams[j]))==3 and
                len(pi|pj)==6 and not ((pi|pj)&remaining))
            if ok:
                vi,vj=scores[frozenset(teams[i])],scores[frozenset(teams[j])]
                trial=values[:];trial[i]=vi;trial[j]=vj
                ne=sum(v*v for v in trial)+max(abs(v) for v in trial)**2
                temp=.01*(1-step/7000)**2+.00004
                if ne<e or RNG.random()<math.exp(min(0,(e-ne)/temp)):
                    e=ne;values=trial;used_pairs=remaining|pi|pj
                    pair_index[i],pair_index[j]=pi,pj
                else:teams[i][x],teams[j][y]=teams[j][y],teams[i][x]
            else:teams[i][x],teams[j][y]=teams[j][y],teams[i][x]
        if best is None or e<best[0]:best=(e,[t[:] for t in teams])
        print('3v3 restart',restart,'best',best[0],flush=True)
    if best is None:raise RuntimeError('could not build a pair-disjoint triple schedule')
    triples=[sorted(t,key=ROSTER.index) for t in best[1]]
    triples.sort(key=lambda t:tuple(ROSTER.index(s) for s in t))
    teams=FIXED+triples
    counts=Counter(s for t in teams for s in t)
    assert len(teams)==30 and counts==target
    assert all(len(a&b)<=1 for a,b in combinations(map(set,triples),2))
    return teams


def main():
    p={'2v2':balanced_2v2(),'3v3':balanced_3v3()}
    counts={fmt:dict(Counter(s for t in ts for s in t)) for fmt,ts in p.items()}
    data={'teams':p,'appearances':counts,'fixed_3v3':FIXED,'team_games':82,
          'schedule':{'2v2':{'rr_rounds':3,'bonus_matches':54},
                      '3v3':{'rr_rounds':2,'bonus_matches':360}}}
    (OUT/'proposal_27_30.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(counts,ensure_ascii=False))

if __name__=='__main__':main()
