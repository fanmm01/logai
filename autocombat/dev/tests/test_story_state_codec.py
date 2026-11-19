import copy
import json
import math
import random
import unittest
from collections import defaultdict,OrderedDict
from battle_engine import Character
from story_state_codec import encode_value,decode_value,capture_room,restore_room_state
from story_models import StoryError


class StoryCodecTests(unittest.TestCase):
    def test_graph_roundtrip_preserves_types_and_aliases(self):
        character=Character('x','X');character._once_consumed={'revive'}
        shared={'character':character,'set':{(1,2)},'frozen':frozenset([3]),'rng':random.Random(42).getstate(),'infinity':float('inf')}
        root={'first':shared,'same':shared,'ordered':OrderedDict([('a',1)]),'default':defaultdict(lambda:defaultdict(float))}
        state=json.loads(json.dumps(encode_value(root)));restored=decode_value(state)
        self.assertIs(restored['first'],restored['same']);self.assertIsInstance(restored['first']['character'],Character)
        self.assertEqual(restored['first']['set'],{(1,2)});self.assertEqual(restored['default']['missing']['key'],0)
        self.assertEqual(restored['first']['rng'],shared['rng']);self.assertTrue(math.isinf(restored['first']['infinity']))
    def test_bad_states_are_rejected(self):
        with self.assertRaises(StoryError):encode_value(object())
        with self.assertRaises(StoryError):decode_value(dict(schema=99))
        with self.assertRaises(StoryError):decode_value(dict(schema=1,root={'$ref':5},nodes=[]))
        with self.assertRaises(StoryError):decode_value(dict(schema=1,root={'$ref':0},nodes=[dict(type='module',data=[])]))
    def test_cycles(self):
        root=[];root.append(root);decoded=decode_value(encode_value(root));self.assertIs(decoded,decoded[0])
