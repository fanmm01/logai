"""Run one isolated tournament benchmark against a selected source snapshot."""
import sys, json, shutil, time
from pathlib import Path
folder=Path(sys.argv[1]).resolve()
sys.path[:0]=[str(folder/'tests'),str(folder)]
from test_ui_management import ManagementTests
f=ManagementTests('runTest'); f.setUp()
try:
    for p in folder.glob('*.py'):
        if p.name not in ('autocombat_backend.py','characters_data.py'):
            shutil.copy2(p,f.root/p.name)
    response=f.client.post('/api/ui/tournaments',json=dict(format='1v1',seed=31415,season=50,
        teams=[[f'Y{i}'] for i in range(1,13)]))
    t=response.get_json(); t['status']='running'
    f.tournaments.save(t)
    start=time.monotonic(); f.tournaments.run(t['id']); elapsed=time.monotonic()-start
    done=f.store.get('tournament',t['id'])
    assert done['completed']==len(t['schedule']),done.get('message')
    print(json.dumps(dict(seconds=elapsed,matches=done['completed'],standings=done['standings'])))
finally:
    f.tearDown()
