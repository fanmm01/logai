import unittest

import characters_data as cd
import battle_engine as be


class GiantFlightTests(unittest.TestCase):
    def test_flight_is_intrinsic_and_skill_retains_only_extra_movement(self):
        card = cd.GELIYA
        self.assertEqual(card['attrs']['飞行'], 1)
        spell = next(s for s in card['spells'] if s['name'] == '飞行')
        self.assertEqual(len(spell['effects']), 1)
        self.assertEqual(spell['effects'][0]['技能加减值'], '行动力+5')
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                engine = cls()
                cd.load_character_to_engine(engine, card, 'giant')
                self.assertFalse(engine._is_flying('giant'))
                engine.process_command('giant', '.hs')
                self.assertEqual(engine.get_char('giant').get_attr('行动力'), 11)
                self.assertEqual(engine._get_effective_mov('giant'), 19)
                cd.load_character_to_engine(engine, cd.YANYAN, 'enemy')
                engine.process_command('enemy', '.hs')
                engine.setup_battle(['giant'], ['enemy'], '10x10', season_status=50)
                self.assertEqual(engine._get_effective_mov('giant'), 24)
