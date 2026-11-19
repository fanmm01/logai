import copy
import json
import secrets
import unittest
import tempfile
from unittest.mock import patch
from pathlib import Path
import battle_http_server as backend
from story_models import StoryError
from story_state_codec import capture_room,restore_room_state,encode_value,decode_value


class StoryUITests(unittest.TestCase):
    def setUp(self):
        self.debug=backend._debug_log;backend._debug_log=lambda *args:None
        self.addCleanup(setattr,backend,'_debug_log',self.debug)
        self.client=backend.app.test_client();self.client.post('/api/ui/session',json={'name':'story-test'})
        response=self.client.get('/api/ui/story/projects');self.assertEqual(response.status_code,200,response.json)
        self.service=backend.app.extensions['combat_ui']['stories'];self.ui=backend.app.extensions['combat_ui'];self.run=None

    def tearDown(self):
        if self.run:
            run=self.service.runtime.lookup(self.run['id'])
            if run.get('room'): self.service.battles.detach(run)

    def start(self,flow='XU'):
        response=self.client.post('/api/ui/story/runs',json={'project':'astral-demo','flow':flow})
        self.assertEqual(response.status_code,200,response.json);self.run=response.json;return self.run

    def command(self,op,payload=None,expected=200):
        response=self.client.post('/api/ui/story/runs/'+self.run['id']+'/commands',json={'commandId':secrets.token_hex(8),'expectedRevision':self.run['revision'],'op':op,'payload':payload or {}})
        self.assertEqual(response.status_code,expected,response.json)
        if expected==200:self.run=response.json
        return response

    def advance_to(self,status):
        for _ in range(100):
            if self.run['status']==status:return
            if self.run['status']=='text':self.command('advance',{'token':self.run['scene']['token']})
            elif self.run['status']=='choice':self.command('choose',{'id':next(o['id'] for o in self.run['choice']['options'] if o['enabled'])})
            else:self.fail('Unexpected status '+self.run['status'])
        self.fail('Story did not reach '+status)

    def test_three_chapters_and_continuation_lock(self):
        chapters=next(p['chapters'] for p in self.client.get('/api/ui/story/projects').json['projects'] if p['id']=='astral-demo')
        self.assertEqual([x['unlocked'] for x in chapters],[True,True,False])
        self.assertEqual(self.client.post('/api/ui/story/runs',json={'project':'astral-demo','flow':'MC2'}).status_code,400)

    def test_choice_unlock_and_idempotence(self):
        self.start();self.advance_to('choice')
        self.assertTrue(all(o['enabled'] for o in self.run['choice']['options']))
        token='same-command';payload=dict(commandId=token,expectedRevision=self.run['revision'],op='choose',payload={'id':'1'})
        first=self.client.post('/api/ui/story/runs/'+self.run['id']+'/commands',json=payload)
        second=self.client.post('/api/ui/story/runs/'+self.run['id']+'/commands',json=payload)
        self.assertEqual(first.status_code,200,first.json);self.assertEqual(second.json['revision'],first.json['revision'])
        payload['commandId']='new-command';self.assertEqual(self.client.post('/api/ui/story/runs/'+self.run['id']+'/commands',json=payload).status_code,409)

    def test_save_load_does_not_restore_gate_profile(self):
        self.start();self.command('save',{'slot':'before'})
        internal=self.service.runtime.lookup(self.run['id']);profile=self.service.runtime.profile(internal['project'],internal['owner'])
        from story_gates import apply_gate_command
        apply_gate_command(self.service.runtime.project(internal),profile,'UNLOCK','crossroads[1]');self.service.runtime.save_profile(profile)
        self.command('load',{'slot':'before'})
        updated=self.service.runtime.profile(internal['project'],internal['owner'])
        self.assertTrue(updated['gates']['option:crossroads.chos:1'])

    def test_isolation_and_file_api(self):
        root=Path(__file__).resolve().parents[2]/'story-projects/demo'
        result=self.client.post('/api/ui/story/workspaces/open',json={'path':str(root)})
        self.assertEqual(result.status_code,200,result.json);wid=result.json['id']
        tree=self.client.get('/api/ui/story/workspaces/'+wid+'/tree');self.assertGreater(len(tree.json['files']),15)
        file=self.client.get('/api/ui/story/workspaces/'+wid+'/files?path=XU.flow');self.assertIn('@START',file.json['content'])
        compile=self.client.post('/api/ui/story/workspaces/'+wid+'/compile',json={});self.assertEqual(compile.status_code,200,compile.json)
        preview=self.client.post('/api/ui/story/workspaces/'+wid+'/preview',json={'flow':'XU.flow','drafts':{}});self.assertEqual(preview.status_code,200,preview.json)
        self.assertTrue(preview.json['project'].startswith('preview-'))
        stranger=backend.app.test_client();stranger.post('/api/ui/session',json={'name':'other'})
        self.assertEqual(stranger.get('/api/ui/story/runs/'+preview.json['id']).status_code,400)

    def test_create_and_switch_workspaces(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'story-projects').mkdir()
            with patch('ui_story.PROJECT_ROOT',root):
                anonymous=backend.app.test_client()
                self.assertEqual(anonymous.post('/api/ui/story/workspaces/create',json={'name':'覆辙'}).status_code,403)
                self.assertEqual(self.client.post('/api/ui/story/workspaces/create',json={'name':' '}).status_code,400)
                created=self.client.post('/api/ui/story/workspaces/create',json={'name':'覆辙'})
                self.assertEqual(created.status_code,200,created.json)
                workspace=created.json
                listing=self.client.get('/api/ui/story/workspaces').json['workspaces']
                self.assertEqual(next(w['name'] for w in listing if w['id']==workspace['id']),'覆辙')
                reopened=self.client.post('/api/ui/story/workspaces/open',json={'path':workspace['path']})
                self.assertEqual(reopened.json['id'],workspace['id'])
                compiled=self.client.post('/api/ui/story/workspaces/'+workspace['id']+'/compile',json={})
                self.assertEqual(compiled.status_code,200,compiled.json)

    def test_workspace_list_discovers_unregistered_stories(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            story=root/'story-projects/fuzhe';story.mkdir(parents=True)
            (story/'story.project.json').write_text(json.dumps({'id':'fuzhe','name':'覆辙'},ensure_ascii=False),encoding='utf-8')
            with patch('ui_story.PROJECT_ROOT',root):
                response=self.client.get('/api/ui/story/workspaces')
                self.assertEqual(response.status_code,200,response.json)
                self.assertTrue(any(w['name']=='覆辙' and Path(w['path'])==story for w in response.json['workspaces']))

    def test_scene_media_sse_and_graph_endpoints(self):
        self.start()
        rid=self.run['id'];token=self.run['scene']['token']
        response=self.client.get('/api/ui/story/runs/'+rid+'/game/scene/'+token+'.txt');self.assertEqual(response.status_code,200);self.assertIn(b'storyHost',response.data);response.close()
        for kind,name in [('background','courtyard.svg'),('figure','guide.svg'),('bgm','starlight.wav'),('video','opening.webm'),('animation','animationTable.json'),('config','config.txt')]:
            response=self.client.get('/api/ui/story/runs/'+rid+'/game/'+kind+'/'+name);self.assertEqual(response.status_code,200,(kind,name));response.close()
        response=self.client.get('/api/ui/story/runs/'+rid+'/game/video/opening.webm',headers={'Range':'bytes=0-31'});self.assertEqual(response.status_code,206);self.assertEqual(len(response.data),32);response.close()
        events=self.client.get('/api/ui/story/runs/'+rid+'/events?after=-1',buffered=False)
        self.assertIn(b'data:',next(events.response));events.close()
        self.assertEqual(self.client.get('/api/ui/story/runs/'+rid+'/events?after=bad').status_code,400)
        self.assertEqual(self.client.get('/api/ui/story/runs/'+rid+'/slots').json['slots'],[])
        response=self.client.get('/ui/story-engine/');self.assertEqual(response.status_code,200);response.close()
        workspace=self.client.post('/api/ui/story/workspaces/open',json={'path':str(Path(__file__).resolve().parents[2]/'story-projects/demo')}).json
        self.assertEqual(self.client.get('/api/ui/story/workspaces').status_code,200)
        graph=self.client.post('/api/ui/story/workspaces/'+workspace['id']+'/graph/patch',json={'path':'XU.flow','operation':{'op':'append','text':'LOCK@MC2'}})
        self.assertEqual(graph.status_code,200,graph.json);self.assertIn('LOCK@MC2',graph.json['content'])

    def test_battle_checkpoint_and_resume(self):
        self.start();self.advance_to('battle_ready');self.command('startBattle')
        code=self.run['room'];room=self.ui['rooms'][code];room['running']=False
        eng=backend._battles[room['battle_id']]
        self.assertEqual(set(room['controls'].values()),{'ai'})
        self.assertIsNotNone(self.service.runtime.lookup(self.run['id']).get('battle_checkpoint'))
        self.run=self.client.get('/api/ui/story/runs/'+self.run['id']).json
        self.command('save',{'slot':'battle'})
        original=copy.deepcopy(self.run)
        response=self.client.post('/api/ui/rooms/'+code+'/control',json={'op':'step'})
        self.assertEqual(response.status_code,200,response.json)
        self.run=self.client.get('/api/ui/story/runs/'+self.run['id']).json
        self.command('load',{'slot':'battle'})
        restored_room=self.ui['rooms'][self.run['room']];restored=backend._battles[restored_room['battle_id']]
        self.assertFalse(restored_room['running'])
        self.assertEqual(restored._get_state()['round'],original.get('round',1))
        self.assertEqual(restored._get_initiative()[0]['userId'],eng._get_initiative()[0]['userId'])
        self.assertEqual(restored_room['rng'],decode_value(self.service.runtime.lookup(self.run['id'])['battle_checkpoint'])['room']['rng'])
        self.service.battles.detach(self.service.runtime.lookup(self.run['id']))

    def test_wave_inherits_resources_and_unlocks_chapter(self):
        self.start('MC1');self.advance_to('battle_ready');self.command('startBattle')
        room=self.ui['rooms'][self.run['room']];room['running']=False;eng=backend._battles[room['battle_id']]
        uid=room['story_rosters']['Y'][0];hp=eng._get_combat_hp(uid);eng._set_combat_hp(uid,max(1,hp-3));eng.get_char(uid).set_attr('魔力',2)
        self.service.c['emit'](room,'test_checkpoint','')
        run=self.service.runtime.lookup(self.run['id']);run['game']['pending']=True;self.service.runtime.commit(run);room['running']=True
        self.service.battles.advance_pending(room);room['running']=False
        self.assertEqual(eng._get_combat_hp(uid),max(1,hp-3));self.assertEqual(eng.get_char(uid).get_attr('魔力'),2)
        self.assertEqual(room['story']['wave'],1);self.assertEqual(len(room['story_rosters']['Z']),1)
        for enemy in room['story_rosters']['Z']:eng._set_combat_hp(enemy,0)
        eng._check_and_mark_end(eng._get_state());self.service.c['emit'](room,'test_win','')
        self.run=self.client.get('/api/ui/story/runs/'+self.run['id']).json
        while self.run['status']=='text':self.command('advance',{'token':self.run['scene']['token']})
        self.assertEqual(self.run['status'],'continue')
        chapter=next(c for c in self.run['successors'] if c['path']=='MC2.flow');self.assertTrue(chapter['unlocked'])
        result=self.client.post('/api/ui/story/runs',json={'project':'astral-demo','flow':'MC2','entry':chapter['entries'][0]['id']})
        self.assertEqual(result.status_code,200,result.json);self.assertEqual(result.json['variables']['signal'],2)

    def test_real_ai_two_waves(self):
        import random
        self.start('MC1');self.advance_to('battle_ready');self.command('startBattle')
        room=self.ui['rooms'][self.run['room']];room['running']=False;room['rng']=random.Random(31415).getstate();waves={0}
        with self.ui['lock']:
            for _ in range(600):
                run=self.service.runtime.lookup(self.run['id'])
                if run['status']!='battle':break
                if run['game'].get('pending'):
                    room['running']=True;self.service.battles.advance_pending(room);room['running']=False
                    waves.add(room['story']['wave'])
                elif not self.service.c['ai_step'](room):
                    self.assertTrue(self.service.c['reactions'].step(room),'AI battle stalled without an automatic reaction')
            else:self.fail('Real story combat did not settle within 600 AI steps')
        self.assertIn(1,waves)
        self.run=self.client.get('/api/ui/story/runs/'+self.run['id']).json
        self.assertIn(self.run['status'],('text','continue','retry'))
        self.assertTrue(any(e['kind']=='story_wave' for e in room['history']))

    def test_checkpoint_failure_rolls_back_and_pauses(self):
        from unittest.mock import patch
        self.start();self.advance_to('battle_ready');self.command('startBattle')
        room=self.ui['rooms'][self.run['room']];room['running']=False;engine=backend._battles[room['battle_id']]
        uid=room['story_rosters']['Y'][0];hp=engine._get_combat_hp(uid);engine._set_combat_hp(uid,hp-5)
        with patch.object(self.service.store,'put_many',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):self.service.c['emit'](room,'test_uncommitted','')
        self.assertEqual(engine._get_combat_hp(uid),hp);self.assertFalse(room['running'])

    def test_carry_groups_preserve_and_reset_independently(self):
        from story_game import normalize_carry_policy
        self.start('MC1');self.advance_to('battle_ready');self.command('startBattle')
        room=self.ui['rooms'][self.run['room']];room['running']=False;engine=backend._battles[room['battle_id']]
        uid=room['story_rosters']['Y'][0];character=engine.get_char(uid);baseline=copy.deepcopy(room['story_baselines'][uid])
        engine._set_combat_hp(uid,7);character.set_attr('魔力',2);character._once_consumed={'once'};character.inventory=[{'item':'test','count':2}];character._story_special_meter=17
        effects=[{'type':'buff','targetUserId':uid,'remainingRounds':3},{'type':'cooldown','sourceUserId':uid,'spellIndex':1,'remainingRounds':4}];engine._set_effects(copy.deepcopy(effects))
        self.service.battles.apply_carry_policy(room,engine,[uid],normalize_carry_policy())
        self.assertEqual(engine._get_effects(),effects);self.assertEqual(character._story_special_meter,17);self.assertEqual(character._once_consumed,{'once'})
        self.service.battles.apply_carry_policy(room,engine,[uid],normalize_carry_policy('RESOURCES'))
        self.assertEqual(engine._get_combat_hp(uid),7);self.assertEqual(character.get_attr('魔力'),2);self.assertEqual(character.inventory,[{'item':'test','count':2}]);self.assertFalse(hasattr(character,'_story_special_meter'));self.assertEqual(character._once_consumed,set());self.assertEqual(engine._get_cooldown(uid,1),0)
        self.service.battles.apply_carry_policy(room,engine,[uid],normalize_carry_policy('NONE'))
        self.assertEqual(engine._get_combat_hp(uid),baseline['attrs']['体力']);self.assertEqual(character.inventory,baseline['inventory'])

    def test_external_rule_endpoint_resumes_flow(self):
        import tempfile,time,sys
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);identifier='external-'+secrets.token_hex(5)
            (root/'story.project.json').write_text(json.dumps(dict(id=identifier)),encoding='utf-8')
            (root/'A.flow').write_text('@START:\n->check{\'w\':ok=1;\'l\':ok=0;}\n->END',encoding='utf-8')
            (root/'check.gmf').write_text('rule CHECK\n{"value":3}',encoding='utf-8')
            rule=dict(syntax='json-v1',returns=['w','l'],executor=dict(kind='exec',argv=[sys.executable,'-c','import json,sys;d=json.load(sys.stdin);print(json.dumps({"result":"w","data":d["input"]}))']))
            (root/'CHECK.rule').write_text(json.dumps(rule),encoding='utf-8')
            self.service.workspaces.publish_version(self.service.workspaces.open_workspace(root))
            response=self.client.post('/api/ui/story/runs',json={'project':identifier,'flow':'A'});self.assertEqual(response.status_code,200,response.json);self.run=response.json
            self.assertEqual(self.run['status'],'external_ready');self.command('executeExternal')
            for _ in range(100):
                self.run=self.client.get('/api/ui/story/runs/'+self.run['id']).json
                if self.run['status']!='external_running':break
                time.sleep(.02)
            self.assertEqual(self.run['status'],'ended');self.assertEqual(self.run['variables']['ok'],1);self.assertEqual(self.run['external_data'],{'value':3})
