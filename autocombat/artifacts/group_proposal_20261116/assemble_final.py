from collections import Counter
from itertools import combinations
import json
from pathlib import Path

OUT=Path(__file__).resolve().parent
def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
initial=read('proposal_27_30.json')
refined=read('proposal_27_30_refined.json')
proposal={'teams':{'2v2':refined['teams']['2v2'],'3v3':initial['teams']['3v3']},
          'appearances':{},'fixed_3v3':[['Y5','Y60','Y7','Y8'],['Y9','Y12'],['Y9','Y3']],
          'team_games':82,'schedule':{'2v2':{'rr_rounds':3,'bonus_matches':54},
                                      '3v3':{'rr_rounds':2,'bonus_matches':360}}}
roster=[f'Y{i}' for i in range(1,18)]+['Y60']
for fmt,teams in proposal['teams'].items():
    proposal['appearances'][fmt]=dict(Counter(s for t in teams for s in t))
    assert len(teams)==(27 if fmt=='2v2' else 30)
assert Counter(proposal['appearances']['2v2'].values())==Counter({3:18})
assert Counter(proposal['appearances']['3v3'].values())==Counter({5:17,4:1})
assert proposal['teams']['3v3'][:3]==proposal['fixed_3v3']
triples=[set(t) for t in proposal['teams']['3v3'] if len(t)==3]
assert len(triples)==27 and all(len(a&b)<=1 for a,b in combinations(triples,2))
(OUT/'proposal_final.json').write_text(json.dumps(proposal,ensure_ascii=False,indent=2),encoding='utf-8')

a=read('summary_refined.json');b=read('summary_27_30.json')
summary={'2v2':a['2v2'],'3v3':b['3v3']}
(OUT/'summary_final.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
for fmt,rows in summary.items():
    normal=[r['rate'] for r in rows if len(r['team'])==(2 if fmt=='2v2' else 3)]
    print(fmt,'normal-range',min(normal),max(normal),'groups',len(rows))
