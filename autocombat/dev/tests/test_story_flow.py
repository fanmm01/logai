import unittest
from story_flow import parse_flow,parse_entry_header,build_graph,apply_graph_edit,resolve_target
from story_expression import value,parse_expression
from story_models import StoryError


class StoryFlowTests(unittest.TestCase):
    def test_multi_entries(self):
        expected=[dict(kind='continue',source='XU',tag='CONTINUE-A'),dict(kind='continue',source='SC1',tag='CONTINUE-AR'),dict(kind='start')]
        for header in ['@XU.CONTINUE-A||@SC1.CONTINUE-AR||START','@XU.CONTINUE-A||@SC1.CONTINUE-AR||@START:']:
            self.assertEqual(parse_entry_header(header),expected)
        for header in ['START||START','@XU.BAD','']:
            with self.assertRaises(StoryError):parse_entry_header(header)

    def test_compact_branches_and_expression(self):
        flow=parse_flow('@START:\n->a <x\n->game{\'w\':->;\'l\':->RETRY;}\nif(time(x)==1) tag=1;\n->(time(x)<=3)?x:END;')
        self.assertEqual(flow['labels'],{'x':0})
        branch=flow['instructions'][1];self.assertEqual(set(branch['branches']),{'w','l'})
        self.assertEqual(flow['instructions'][-1]['target'],'(time(x)<=3)?x:END')
        self.assertEqual(value('time(x)==1 && (!flag || 2+3*4==14)',{}, {'time':lambda x:1}),True)
        self.assertEqual(value('true?3:1/0'),3)
        self.assertEqual(value('false && 1/0'),False)
        self.assertEqual(value('"a"+"b"'),'ab')
        for expr in ['a.__class__','__import__("os")','1/0','1 +']:
            with self.subTest(expr=expr),self.assertRaises(StoryError):value(expr)

    def test_nested_if_and_gates(self):
        flow=parse_flow('@START:\nUNLOCK@c[1];\nLOCK@MC2\nif(a){if(b){x=1;}else{x=2;}}else{x=3;}\n->END')
        self.assertEqual(flow['instructions'][0]['target'],'c[1]')
        self.assertEqual(sum(o['kind']=='if' for o in flow['instructions']),2)
        self.assertGreater(len(build_graph(flow)['edges']),3)

    def test_lossless_edits(self):
        source='@START:\n// keep\n->a <first // comment\n->END\n'
        flow=parse_flow(source);node=flow['instructions'][0]
        changed=apply_graph_edit(source,{'op':'replace','id':node['id'],'text':'->b <first'})
        self.assertIn('// keep',changed);self.assertIn('// comment',changed)
        self.assertIn('->b',changed)
        self.assertIn('START',apply_graph_edit(source,{'op':'header','text':'@START:'}))
        self.assertIn('LOCK@a',apply_graph_edit(source,{'op':'append','text':'LOCK@a'}))
        with self.assertRaises(StoryError):apply_graph_edit(source,{'op':'delete','id':'missing'})

    def test_resolution_and_errors(self):
        files={'a.txt':'','a.chos':'','a.gmf':''}
        self.assertEqual(resolve_target('a',files),'a.txt')
        self.assertEqual(resolve_target('a.chos',files),'a.chos')
        for source in ['->END','@START:\nunknown text','@START:\n->a <x\n->b <x','@START:\nif(a {x=1;']:
            with self.subTest(source=source),self.assertRaises((StoryError,IndexError)):parse_flow(source)

    def test_header_position_native_visuals_and_graph_control_edges(self):
        with self.assertRaises(StoryError):parse_flow('x=1\n@START:\n->END')
        flow=parse_flow('@START:\nchangeFigure:a.svg -transform={"alpha":0.5};\n->t <back\n->(time(back)<2)?back:END\nUNLOCK@chapter\n->END')
        graph=build_graph(flow)
        self.assertEqual(graph['nodes'][0]['label'],'START');self.assertTrue(any(e['label']=='是' for e in graph['edges']));self.assertTrue(any(n['label']=='UNLOCK@chapter' for n in graph['nodes']))
