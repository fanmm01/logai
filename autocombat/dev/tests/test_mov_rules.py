import unittest
from unittest.mock import patch

import battle_engine as be
import characters_data as cd


class MovRulesTests(unittest.TestCase):
    def test_thresholds_extra_actions_and_zero_action_override(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            engine = cls()
            cd.load_character_to_engine(engine, cd.YANYAN, 'actor')
            char = engine.get_char('actor')
            for mov, expected in ((0, 1), (3, 1), (8, 1), (9, 2), (13, 2),
                                  (14, 3), (18, 3), (19, 4), (23, 4), (24, 5), (99, 20)):
                with self.subTest(engine=cls.__name__, mov=mov):
                    with patch.object(engine, '_get_effective_mov', return_value=mov):
                        char.set_attr('额外行动数', 0)
                        self.assertEqual(engine._calc_main_actions('actor'), expected)
                        char.set_attr('额外行动数', 1)
                        self.assertEqual(engine._calc_main_actions('actor'), max(1, (mov + 1) // 5 + 1))
            char.set_attr('无主动行动', 1)
            self.assertEqual(engine._calc_main_actions('actor'), 0)

    def test_all_cards_start_with_one_action_and_transform_adds_three(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            for card in cd.ALL_CHARACTERS:
                with self.subTest(engine=cls.__name__, card=card['serial']):
                    engine = cls()
                    cd.load_character_to_engine(engine, card, 'actor')
                    char = engine.get_char('actor')
                    original = char.get_attr('行动力')
                    self.assertFalse(engine._is_flying('actor'))
                    self.assertEqual(engine._calc_main_actions('actor'), 1)
                    engine.process_command('actor', '.hs')
                    self.assertEqual(char.get_attr('行动力'), original + 3)
                    self.assertEqual(engine._is_flying('actor'), bool(card['attrs'].get('飞行')))
                    engine.process_command('actor', '.unh')
                    self.assertEqual(char.get_attr('行动力'), original)
                    self.assertFalse(engine._is_flying('actor'))
                    self.assertEqual(engine._calc_main_actions('actor'), 1)

    def test_summons_keep_old_action_counts_with_additive_movement(self):
        old_mov = {'三合一': 10, '使魔': 6, '朱雀': 11, '玄武': 3,
                   '生灵': 10, '藤蔓': 8, '蜂鸟': 11, '龙虎': 8,
                   '小雪人': 5, '虚假之月': 0, '影之克隆': 8, '血液长枪': 0}
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            engine = cls()
            cd.load_character_to_engine(engine, cd.YANYAN, 'summon')
            char = engine.get_char('summon')
            for name, old in old_mov.items():
                template = cd.SUMMON_TEMPLATES[name]
                self.assertEqual(template['MOV'], old + 3)
                for flying in (False, True):
                    engine._set_initiative([{'userId': 'summon', 'isSummon': True, 'flying': flying}])
                    self.assertEqual(engine._is_flying('summon'), flying)
                    for bonus in (0, 5, 8, 13):
                        char.set_attr('MOV', template['MOV'] + bonus)
                        char.set_attr('额外行动数', template.get('额外行动数', 0))
                        char.set_attr('无主动行动', template.get('无主动行动', 0))
                        expected = 0 if template.get('无主动行动') else max(
                            1, (old + bonus + 8 * flying + 4) // 5 + template.get('额外行动数', 0))
                        with self.subTest(engine=cls.__name__, summon=name, flying=flying, bonus=bonus):
                            self.assertEqual(engine._calc_main_actions('summon'), expected)
