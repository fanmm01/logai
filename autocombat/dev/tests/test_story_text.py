import unittest
from story_text import parse_text,parse_mem_arguments,compile_scene,walk_ops
from story_models import StoryError


class StoryTextTests(unittest.TestCase):
    def test_natural_context_and_continuation(self):
        ops=parse_text('春晌：旁白\n“你好”\n她摇头。\\\n“帮我”\n：强制旁白\n“仍是旁白”\n葬火：“好”\n\\“引用”')
        self.assertEqual([o['speaker'] for o in ops],['','春晌','春晌','','','葬火',''])
        self.assertEqual(ops[2]['text'],'她摇头。\n“帮我”')
        self.assertEqual(ops[-1]['text'],'“引用”')

    def test_mem_both_commas_and_order(self):
        self.assertEqual(parse_mem_arguments('春晌， 葬火,知夏，环花暖,观测员'),['春晌','葬火','知夏','环花暖','观测员'])
        self.assertEqual(parse_mem_arguments('NONE'),[])
        self.assertEqual(parse_mem_arguments(''),[])
        for invalid in ['a,b,c,d,e,f','a,a','a,,b',',a']:
            with self.subTest(invalid=invalid),self.assertRaises(StoryError):parse_mem_arguments(invalid)
        with self.assertRaises(StoryError):parse_text('\\mem 未知','x.txt',{})

    def test_black_and_media(self):
        ops=parse_text('\\m song.wav\n\\bg bg.svg\n\\mem A，B\n\\b黑屏\\\n第二行\n\\m NONE')
        self.assertTrue(ops[3]['black']);self.assertEqual(ops[3]['text'],'黑屏\n第二行')
        compiled=compile_scene(ops,{'A':'a.svg','B':'b.svg'})
        self.assertIn('bgm:song.wav',compiled['script']);self.assertIn('intro:',compiled['script'])
        self.assertEqual(compiled['script'].count('changeFigure:'),5)
        self.assertIn('"x":-640.0',compiled['script']);self.assertIn('"x":640.0',compiled['script'])

    def test_locked_choice_full_and_omissions(self):
        for suffix in ['', '\n\\c1 ：一\n\\c2 ：二', '\n\\c1 ：一\n\\c2 ：二\n\\c\n：后文']:
            ops=parse_text('\\c{\n__LOCK 1“好”;\n2“否”;\n}'+suffix,'c.chos')
            choice=ops[0];self.assertTrue(choice['options'][0]['locked']);self.assertFalse(choice['options'][1]['locked'])
            if suffix:self.assertEqual(choice['options'][1]['body'][0]['text'],'二')
            if suffix.endswith('后文'):self.assertEqual(ops[-1]['text'],'后文')

    def test_persistent_black_until_close(self):
        ops=parse_text('\\b黑屏\n仍在黑屏\nb\\\n普通文字\n\\b\n再次黑屏\nb\\普通文字')
        self.assertEqual([o['black'] for o in ops],[True,True,False,True,False])
        self.assertEqual([o['text'] for o in ops],['黑屏','仍在黑屏','普通文字','再次黑屏','普通文字'])
        self.assertEqual(compile_scene(ops)['script'].count('intro:'),3)

    def test_combined_state_closers(self):
        ops=parse_text('\\bg room.png\n\\m song.wav\n\\mem A\n\\b黑屏\nb\\mem\\bg\\m\\\n普通文字', figures={'A':'a.png'})
        self.assertEqual([o['kind'] for o in ops],['media','media','mem','say','mem','media','media','say'])
        self.assertEqual(ops[4]['names'],[])
        self.assertEqual([(o['media'],o['asset']) for o in ops[5:7]],[('bg','NONE'),('m','NONE')])
        self.assertFalse(ops[-1]['black'])
        script=compile_scene(ops)['script']
        self.assertIn('changeBg:none -next;',script)
        self.assertIn('bgm:none -next;',script)
        self.assertEqual(script.count('changeFigure:none'),9)

    def test_black_inherited_by_choice_branches(self):
        ops=parse_text('\\b开场\n\\c{\n1“选择”\n}\n\\c1\n黑屏分支\nb\\\n普通分支\n\\c\nb\\\n普通后文')
        self.assertEqual([o['black'] for o in ops[1]['options'][0]['body']],[True,False])
        self.assertFalse(ops[-1]['black'])

    def test_native_and_gate(self):
        ops=parse_text('UNLOCK@c[1];\nLOCK@MC2\nchangeBg:a.svg -next;\n：hello; | :')
        self.assertEqual([x['kind'] for x in ops],['gate','gate','native','say'])
        self.assertEqual(ops[0]['target'],'c[1]')
        self.assertIn('hello\\;',compile_scene(ops)['script'])
        self.assertEqual(parse_text('#!webgal\nsay:a;')[0]['script'],'say:a;')

    def test_bad_choice_and_unfinished_join(self):
        for text in ['\\c{\n1“好”','\\c{\nA“好”\n}','\\c{\n1“好”\n1“坏”\n}','：尾\\']:
            with self.subTest(text=text),self.assertRaises(StoryError):parse_text(text)

    def test_nested_choice(self):
        text='\\c{\n1“外”\n}\n\\c1\n\\c{\n2“内”\n}\n\\c2 ：内文\n\\c\n\\c\n：外后文'
        ops=parse_text(text)
        self.assertEqual(sum(o['kind']=='choice' for o in walk_ops(ops)),2)
        self.assertEqual(ops[-1]['text'],'外后文')
