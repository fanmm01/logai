import unittest
from story_workspace import compile_project
from story_gates import apply_gate_command,flow_state,available_entries,option_enabled,resolve_gate_target
from story_models import StoryError


def project():
    return compile_project({'A.flow':'@START:\n->CONTINUE-A','B.flow':'@A.CONTINUE-A||START\n->END','C.flow':'@A.CONTINUE-A:\n->END','choice.chos':'\\c{\n__LOCK 1“锁”\n2“开”\n}','text.txt':'：文字'})


class StoryGateTests(unittest.TestCase):
    def setUp(self):self.project=project();self.profile={'gates':{},'continuations':[]}
    def test_initial_lock_and_explicit_override(self):
        options=self.project['prose']['choice.chos'][0]['options']
        self.assertFalse(option_enabled(self.project,self.profile,'choice.chos',options[0]))
        apply_gate_command(self.project,self.profile,'UNLOCK','choice[1]')
        self.assertTrue(option_enabled(self.project,self.profile,'choice.chos',options[0]))
        apply_gate_command(self.project,self.profile,'LOCK','choice.chos[1]')
        self.assertFalse(option_enabled(self.project,self.profile,'choice.chos',options[0]))
    def test_start_alternative_does_not_need_continuation(self):
        self.assertTrue(flow_state(self.project,self.profile,'B.flow')['unlocked'])
        self.assertFalse(flow_state(self.project,self.profile,'C.flow')['unlocked'])
        apply_gate_command(self.project,self.profile,'UNLOCK','C')
        self.assertFalse(flow_state(self.project,self.profile,'C.flow')['unlocked'])
        self.profile['continuations']=[dict(id='ticket',source='A',tag='CONTINUE-A',state={})]
        self.assertTrue(flow_state(self.project,self.profile,'C.flow')['unlocked'])
        self.assertEqual(len(available_entries(self.project,self.profile,'B.flow')),2)
        apply_gate_command(self.project,self.profile,'LOCK','B')
        self.assertFalse(flow_state(self.project,self.profile,'B.flow')['unlocked'])
        self.assertIn('锁定',flow_state(self.project,self.profile,'B.flow')['reason'])
    def test_invalid_targets_and_rename_identity(self):
        for target in ['missing','choice[9]','text','choice']:
            with self.subTest(target=target),self.assertRaises(StoryError):resolve_gate_target(self.project,target)
        self.project['config']['content_ids']={'B.flow':'old-B'}
        apply_gate_command(self.project,self.profile,'LOCK','B')
        self.assertIn('flow:old-B',self.profile['gates'])

    def test_legacy_duplicate_tickets_use_latest_state_and_keep_start(self):
        self.profile['continuations']=[
            dict(id='old',source='A',tag='CONTINUE-A',time=1,state={'variables':{'signal':1}}),
            dict(id='latest',source='A',tag='CONTINUE-A',time=3,state={'variables':{'signal':3}}),
            dict(id='older-appended-last',source='A',tag='CONTINUE-A',time=2,state={'variables':{'signal':2}}),
        ]
        entries=available_entries(self.project,self.profile,'B.flow')
        self.assertEqual([entry['id'] for entry in entries],['latest','START'])
        self.assertEqual(entries[0]['ticket']['state']['variables']['signal'],3)
        self.assertEqual(len(flow_state(self.project,self.profile,'C.flow')['entries']),1)
        self.profile['continuations'].append(dict(id='same-time-later',source='A',tag='CONTINUE-A',time=3,state={}))
        self.assertEqual(available_entries(self.project,self.profile,'C.flow')[0]['id'],'same-time-later')

    def test_different_continuation_sources_and_tags_remain_independent(self):
        project=compile_project({'A.flow':'@START:\n->CONTINUE-A\n->CONTINUE-B', 'B.flow':'@START:\n->CONTINUE-A',
                                 'C.flow':'@A.CONTINUE-A||@A.CONTINUE-B||@B.CONTINUE-A||START\n->END'})
        self.profile['continuations']=[dict(id=source+tag,source=source,tag=tag,state={}) for source,tag in
                                      [('A','CONTINUE-A'),('A','CONTINUE-B'),('B','CONTINUE-A')]]
        entries=available_entries(project,self.profile,'C.flow')
        self.assertEqual([entry['id'] for entry in entries],['ACONTINUE-A','ACONTINUE-B','BCONTINUE-A','START'])
