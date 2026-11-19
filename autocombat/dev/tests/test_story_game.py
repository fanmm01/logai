import unittest
from story_game import parse_game,normalize_carry_policy
from story_models import StoryError

SOURCE='''rule AUTOCOMBAT.
set TEAMS:Y{Y5,Y60,Y7,Y8},X{Y9},Z{Y12};
set CONTROL:NONE;
set BIND:Y;
battle Y vs X KEEP_STATUS;
battle Y vs Z;
if(isAlive(Y)) return "w";
else return "l";
DISPLAY:AUTOCOMBAT_DEFAULT
'''


class StoryGameTests(unittest.TestCase):
    def test_request_program(self):
        game=parse_game(SOURCE)
        self.assertEqual(game['teams']['Y'],['Y5','Y60','Y7','Y8']);self.assertEqual(len(game['battles']),2)
        self.assertTrue(game['battles'][0]['keep']);self.assertFalse(game['battles'][1]['keep'])
        self.assertEqual([x['result'] for x in game['returns']],['w','l'])
    def test_presets_and_overrides(self):
        self.assertTrue(all(normalize_carry_policy().values()))
        self.assertFalse(any(normalize_carry_policy('NONE').values()))
        policy=normalize_carry_policy('RESOURCES',{'statuses':True})
        self.assertTrue(policy['resources']);self.assertTrue(policy['inventory']);self.assertTrue(policy['statuses']);self.assertFalse(policy['cooldowns'])
        game=parse_game(SOURCE.replace('battle Y vs X','set CARRY:RESOURCES;\nset CARRY_FIELDS:statuses=true;\nbattle Y vs X'))
        self.assertFalse(game['battles'][0]['carry']['cooldowns'])
    def test_invalid_programs(self):
        for source in [SOURCE.replace('rule AUTOCOMBAT.',''),SOURCE.replace('Y9','UNKNOWN'),SOURCE.replace('BIND:Y','BIND:Q'),SOURCE.replace('CONTROL:NONE','CONTROL:Q'),SOURCE.replace('battle Y vs Z','battle Y vs Q'),SOURCE.replace('set BIND:Y','set WHAT:Y')]:
            with self.subTest(source=source),self.assertRaises(StoryError):parse_game(source,characters={'Y5','Y60','Y7','Y8','Y9','Y12'})
        with self.assertRaises(StoryError):normalize_carry_policy('WHAT')
        with self.assertRaises(StoryError):normalize_carry_policy(overrides={'bad':True})
