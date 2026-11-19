import json
import tempfile
import unittest
from pathlib import Path
from ui_store import Store
from story_workspace import Workspaces
from story_runtime import StoryRuntime
from story_models import StoryError


class StoryRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.store=Store(self.root/'state.db');self.workspaces=Workspaces(self.root/'runtime',self.store)
    def make(self,files):
        root=self.root/'project';root.mkdir();files={'story.project.json':'{"id":"vm-test"}',**files}
        for path,content in files.items():
            target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content,encoding='utf-8')
        self.workspaces.publish_version(self.workspaces.open_workspace(root));return StoryRuntime(self.workspaces,self.store)
    def command(self,rt,run,operation,payload=None):
        profile=rt.profile(run['project'],run['owner']);rt.apply_command(run,dict(op=operation,payload=payload or {}),profile);rt.commit(run,profile)
    def drain_text(self,rt,run):
        for _ in range(20):
            if run['status']!='text':return
            self.command(rt,run,'advance',{'token':run['scene']['token']})
        self.fail('Too many text steps')
    def test_retry_retains_variables_counts_and_unlocks(self):
        rt=self.make({'A.flow':'@START:\n->c <source\nif(time(source)>=2){done=1;}else{done=0;}\n->END','c.chos':'\\c{\n__LOCK 1“初始锁定”\n2“再试”\n}\n\\c1 ：结束\n\\c2 UNLOCK@c[1]\n\\c\n'})
        run=rt.create_run('vm-test','player','A');self.assertEqual(run['status'],'choice')
        with self.assertRaises(StoryError):self.command(rt,run,'choose',{'id':'1'})
        self.command(rt,run,'choose',{'id':'2'});self.drain_text(rt,run);self.assertEqual(run['status'],'ended')
        self.assertEqual(run['variables']['done'],0)
        run=rt.create_run('vm-test','player','A');self.assertTrue(rt.public(run)['choice']['options'][0]['enabled'])
    def test_retry_source_and_ternary(self):
        rt=self.make({'A.flow':'@START:\n->c <again {1: count=count+1; ->RETRY; 2: ->(time(again)>1)?finish:again;}\n->endtext <finish\n->END','c.chos':'\\c{\n1“重试”\n2“继续”\n}','endtext.txt':'：抵达'})
        run=rt.create_run('vm-test','player','A');self.command(rt,run,'choose',{'id':'1'});self.assertEqual(run['status'],'retry');self.command(rt,run,'retry')
        self.assertEqual(run['variables']['count'],1);self.assertEqual(run['counts']['A.flow:again'],2)
        self.command(rt,run,'choose',{'id':'2'});self.drain_text(rt,run);self.assertEqual(run['status'],'ended')
    def test_start_and_multiple_continuations_require_selection(self):
        rt=self.make({'A.flow':'@START:\nx=7;\n->CONTINUE-A','B.flow':'@A.CONTINUE-A||START\n->text\n->END','text.txt':'：你好'})
        a=rt.create_run('vm-test','player','A');self.assertEqual(a['status'],'continue')
        with self.assertRaises(StoryError):rt.create_run('vm-test','player','B')
        chapter=rt.chapters('vm-test','player')[1];self.assertEqual(len(chapter['entries']),2)
        ticket=next(e for e in chapter['entries'] if e['kind']=='continue')
        b=rt.create_run('vm-test','player','B',ticket['id']);self.assertEqual(b['variables']['x'],7)
        fresh=rt.create_run('vm-test','player','B','START');self.assertNotIn('x',fresh['variables'])
    def test_scene_checkpoint_save_and_restore(self):
        rt=self.make({'A.flow':'@START:\n->t\n->END','t.txt':'：第一句\n：第二句'})
        run=rt.create_run('vm-test','player','A');token=run['scene']['token']
        snapshot=dict(hostToken=token,nowStageState={'GameVar':{'native':9}},sceneData={'sceneUrl':'/scene/'+token+'.txt'})
        self.command(rt,run,'checkpoint',dict(token=token,snapshot=snapshot,user_data={'globalGameVar':{'global':1}}))
        self.command(rt,run,'save',{'slot':'one','snapshot':snapshot});self.command(rt,run,'advance',dict(token=token))
        self.assertEqual(run['variables']['native'],9)
        self.command(rt,run,'load',{'slot':'one'});self.assertNotEqual(run['scene']['token'],token)
        self.assertEqual(run['player_snapshot']['hostToken'],run['scene']['token'])
        self.assertIn(run['scene']['token'],run['player_snapshot']['sceneData']['sceneUrl'])
        self.assertEqual(rt.profile('vm-test','player')['user_data']['globalGameVar']['global'],1)
        with self.assertRaises(StoryError):self.command(rt,run,'advance',{'token':'old'})

    def test_repeated_completion_replaces_entry_and_carries_latest_state(self):
        rt=self.make({'A.flow':'@START:\n->text\n->CONTINUE-A','B.flow':'@A.CONTINUE-A:\n->text\n->END','text.txt':'：接续测试'})
        first=rt.create_run('vm-test','player','A')
        first['variables']['signal']=1;first['player_snapshot']={'marker':'first'}
        self.drain_text(rt,first)
        original_entry=rt.create_run('vm-test','player','B')
        second=rt.create_run('vm-test','player','A')
        second['variables']['signal']=2;second['player_snapshot']={'marker':'second'}
        self.drain_text(rt,second)
        profile=rt.profile('vm-test','player')
        self.assertEqual(len(profile['continuations']),1)
        chapter=next(chapter for chapter in rt.chapters('vm-test','player') if chapter['path']=='B.flow')
        self.assertEqual(len(chapter['entries']),1)
        resumed=rt.create_run('vm-test','player','B',chapter['entries'][0]['id'])
        self.assertEqual(resumed['variables']['signal'],2)
        self.assertEqual(resumed['player_snapshot']['marker'],'second')
        self.assertEqual(rt.lookup(original_entry['id'])['variables']['signal'],1)
        self.assertEqual(rt.lookup(original_entry['id'])['player_snapshot']['marker'],'first')
    def test_loops_pause_and_missing_commands(self):
        rt=self.make({'A.flow':'@START:\n->t <loop\n->loop','t.txt':':test'})
        run=rt.create_run('vm-test','p','A')
        with self.assertRaises(StoryError):self.command(rt,run,'unsupported')
        with self.assertRaises(StoryError):self.command(rt,run,'choose',{'id':1})
        with self.assertRaises(StoryError):self.command(rt,run,'load',{'slot':'none'})

    def test_previous_scene_snapshot_cannot_undo_flow_assignments(self):
        rt=self.make({'A.flow':'@START:\nsignal=1;\n->first\nsignal=signal+1;\n->second\n->CONTINUE-A','first.txt':'：第一句','second.txt':'：第二句'})
        run=rt.create_run('vm-test','player','A');token=run['scene']['token']
        snapshot=dict(hostToken=token,nowStageState={'GameVar':{'signal':1}})
        self.command(rt,run,'checkpoint',dict(token=token,snapshot=snapshot))
        self.command(rt,run,'advance',dict(token=token))
        self.assertEqual(run['variables']['signal'],2)
        self.command(rt,run,'advance',dict(token=run['scene']['token'],snapshot=snapshot))
        self.assertEqual(run['variables']['signal'],2)
        self.assertEqual(rt.profile('vm-test','player')['continuations'][-1]['state']['variables']['signal'],2)

    def test_reading_preferences_persist_while_waiting_for_a_choice(self):
        rt=self.make({'A.flow':'@START:\n->c\n->END','c.txt':'\\c{\n1“继续”\n}\n\\c1 ：后文\n\\c'})
        run=rt.create_run('vm-test','player','A');self.assertEqual(run['status'],'choice')
        self.command(rt,run,'preferences',{'user_data':{'optionData':{'textSize':0}}})
        self.command(rt,run,'choose',{'id':'1'})
        self.assertEqual(rt.public(run)['user_data']['optionData']['textSize'],0)
        with self.assertRaises(StoryError):self.command(rt,run,'preferences',{'user_data':[]})

    def test_noninteractive_loop_pauses_and_single_start_defaults(self):
        rt=self.make({'A.flow':'@START:\n->START'})
        run=rt.create_run('vm-test','p');self.assertEqual(run['status'],'paused');self.assertIn('2000',run['message'])
        self.command(rt,run,'retry');self.assertEqual(run['status'],'paused')

    def test_main_selects_default_and_multiple_starts_need_choice(self):
        rt=self.make({'A.flow':'@START:\n->END','B.flow':'@START:\n->END','MAIN':'B'})
        self.assertEqual(rt.create_run('vm-test','p')['flow'],'B.flow')
