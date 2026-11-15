import sys,shutil,time,json
from pathlib import Path
folder=Path(sys.argv[1]).resolve()
sys.path[:0]=[str(folder/'tests'),str(folder)]
from test_ui_management import ManagementTests
from ui_tasks import Tasks
from flask import Blueprint
f=ManagementTests('runTest');f.setUp()
try:
 for p in folder.glob('*.py'):
    if p.name not in ('autocombat_backend.py','characters_data.py'):
        shutil.copy2(p,f.root/p.name)
 f.c['valid_teams']=lambda data: None
 bp=Blueprint('perf_tasks',__name__)
 tasks=Tasks(bp,f.c,f.management,f.store);f.app.register_blueprint(bp)
 start=time.monotonic()
 response=f.client.post('/api/ui/simulations',json=dict(mode='verbose',team_a=['Y5','Y6'],team_b=['Y7','Y8'],seed=17,season=50,fast=True))
 assert response.status_code==200,response.get_data(as_text=True)
 jid=response.get_json()['id']
 while True:
    job=f.client.get('/api/ui/simulations/'+jid).get_json()
    if job['status']!='running': break
    assert time.monotonic()-start<120
    time.sleep(.05)
 assert job['status']=='completed',job
 print(json.dumps(dict(seconds=time.monotonic()-start,logs=len(job['logs']),result=job['result'])))
finally:
 f.tearDown()
