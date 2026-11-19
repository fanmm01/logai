"""Restore an actual pending combat reaction, including in a fresh interpreter."""
import copy
import json
import os
import secrets
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import battle_http_server as backend


class StoryResumeTests(unittest.TestCase):
    def test_pending_reaction_survives_process_restart_and_replays_identically(self):
        client=backend.app.test_client();client.post('/api/ui/session',json={'name':'resume-test'})
        service=backend.app.extensions['combat_ui']['stories'];ui=backend.app.extensions['combat_ui']
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);identifier='resume-'+secrets.token_hex(5)
            (root/'story.project.json').write_text(json.dumps({'id':identifier}),encoding='utf-8')
            (root/'A.flow').write_text('@START:\n->fight{\'w\':->;\'l\':->;}\n->END',encoding='utf-8')
            (root/'fight.gmf').write_text('rule AUTOCOMBAT\nset TEAMS:Y{Y1},X{Y2};\nset CONTROL:Y,X;\nset BIND:Y;\nbattle Y vs X;\nif(isAlive(Y)) return "w";\nelse return "l";',encoding='utf-8')
            (root/'AUTOCOMBAT.rule').write_text(json.dumps(dict(schema=1,syntax='autocombat-v1',executor=dict(kind='builtin',target='AUTOCOMBAT'),returns=['w','l'])),encoding='utf-8')
            service.workspaces.publish_version(service.workspaces.open_workspace(root))
            response=client.post('/api/ui/story/runs',json={'project':identifier,'flow':'A'});self.assertEqual(response.status_code,200,response.json);run=response.json
            def command(op,payload=None):
                nonlocal run
                run=client.get('/api/ui/story/runs/'+run['id']).json
                response=client.post('/api/ui/story/runs/'+run['id']+'/commands',json={'commandId':secrets.token_hex(8),'expectedRevision':run['revision'],'op':op,'payload':payload or {}})
                self.assertEqual(response.status_code,200,response.json);run=response.json
            with patch.object(backend,'_debug_log',lambda *args:None):command('startBattle')
            room=ui['rooms'][run['room']];room['running']=False;eng=backend._battles[room['battle_id']]
            try:
                il=eng._get_initiative();md=eng._get_map();md['occupants']={}
                for i,e in enumerate(il):e['coord']=['B2','C2'][i];e['_anchor_coord']=e['coord'];md['occupants'][e['coord']]=e['userId']
                eng._set_map(md);eng._set_initiative(il)
                with patch.object(backend,'roll_d100',return_value=(10,'')):
                    response=client.post('/api/ui/rooms/'+room['code']+'/action',json={'command':'.s0','revision':room['revision'],'request_id':'create-pending'})
                self.assertEqual(response.status_code,200,response.json)
                pending=copy.deepcopy(backend._pending_reactions[room['battle_id']]);self.assertTrue(pending)
                command('save',{'slot':'pending'})
                state=service.runtime.lookup(run['id'])
                cookie_name=service.c['cookie_name'];cookie=client.get_cookie(cookie_name).value
                program='''import sys,json
sys.path.insert(0,sys.argv[1])
import battle_http_server as b
data=json.load(sys.stdin)
c=b.app.test_client();c.set_cookie(data['name'],data['cookie'])
r=c.get('/api/ui/story/runs/'+data['run']);assert r.status_code==200,r.get_json()
room=c.get('/api/ui/rooms/'+r.get_json()['room']).get_json()
print('RESULT:'+json.dumps({'running':room['running'],'reaction':room['snapshot']['reaction'],'units':room['snapshot']['units']}))
'''
                result=subprocess.run([sys.executable,'-c',program,str(Path(__file__).resolve().parents[2]/'backend')],input=json.dumps({'run':run['id'],'name':cookie_name,'cookie':cookie}),text=True,encoding='utf-8',capture_output=True,timeout=45,env=dict(os.environ,AUTOCOMBAT_NO_FILE_LOG='1'))
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                restored_summary=json.loads(next(x[7:] for x in result.stdout.splitlines() if x.startswith('RESULT:')))
                self.assertFalse(restored_summary['running']);self.assertIsNotNone(restored_summary['reaction'])
                def resolve(current_room):
                    with patch.object(backend,'roll_d100',return_value=(99,'')):
                        response=client.post('/api/ui/rooms/'+current_room['code']+'/action',json={'choice':'dodge','revision':current_room['revision'],'request_id':secrets.token_hex(8)})
                    self.assertEqual(response.status_code,200,response.json)
                    return [(u['uid'],u['hp'],u['mp']) for u in response.json['snapshot']['units']]
                first=resolve(room);command('load',{'slot':'pending'});room=ui['rooms'][run['room']]
                self.assertEqual(backend._pending_reactions[room['battle_id']],pending)
                self.assertEqual(resolve(room),first)
            finally:
                service.battles.detach(service.runtime.lookup(run['id']))
