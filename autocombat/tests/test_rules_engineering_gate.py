"""Scoped data validation and Y11+ locked/selected rule evidence."""
import copy
import random
import unittest
from unittest.mock import patch
import battle_engine as be
import characters_data as cd
from tests.test_y12_summons import setup
from tests.test_y17_balance import _setup as blood_setup, _spell as blood_spell


class EngineeringGateTests(unittest.TestCase):
    def test_y11_01_enter_drain_reentry_and_nonadditive_debuff(self):
        from tests.test_effect_rules import _place
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls, 'Y11')
            en._set_effects([])
            _place(en, {'B6': 'actor', 'C6': 'enemy', 'N14': 'ally'})
            en.get_char('actor').set_attr('魔力', 5)
            en.get_char('enemy').set_attr('魔力', 19)
            fog = next(s for s in en.load_spells('actor') if s['name'] == '黑雾笼罩')
            with patch.object(be.random, 'random', return_value=0):
                en._execute_spell('actor', 'enemy', fog)
                self.assertEqual(en.get_char('enemy').get_attr('魔力'), 12)
                self.assertEqual(en._zone_attr_mod('enemy', '闪避'), -20)
                en._settle_zone_enters()
                self.assertEqual(en.get_char('enemy').get_attr('魔力'), 12)
                _place(en, {'B6': 'actor', 'N14': 'enemy', 'M14': 'ally'})
                en._settle_zone_enters()
                _place(en, {'B6': 'actor', 'C6': 'enemy', 'N14': 'ally'})
                en._settle_zone_enters()
                self.assertEqual(en.get_char('enemy').get_attr('魔力'), 8)
                self.assertEqual(en._zone_attr_mod('enemy', '闪避'), -20)

    def test_y13_02_actual_hp_write_converts_only_the_difference(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls, 'Y13')
            en._set_effects([dict(type='buff', auxCode=29, sourceUserId='actor', targetUserId='actor', remainingRounds=5)])
            en.get_char('actor').set_attr('魔力', 3)
            hp = en._get_combat_hp('actor')
            en._set_combat_hp('actor', hp - 7, source_dmg=99, damage_source='enemy')
            self.assertEqual(en.get_char('actor').get_attr('魔力'), 0)
            self.assertEqual(en._get_combat_hp('actor'), hp - 4)
            en._set_combat_hp('actor', hp - 2)
            self.assertEqual(en.get_char('actor').get_attr('魔力'), 0)

    def test_y15_04_cloak_tiers_and_05_stage99_unavailable(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls, 'Y15')
            for damage, expected in ((3, 0), (4, 3), (6, 3), (7, 4)):
                en.get_char('actor').set_attr('魔力', 5)
                self.assertEqual(en._cloak_damage('actor', damage), expected)
                self.assertEqual(en.get_char('actor').get_attr('魔力'), 4)
            en.get_char('actor').set_attr('魔力', 0)
            self.assertEqual(en._cloak_damage('actor', 3), 3)
            self.assertFalse(any(s['name'].startswith('燃梦孤灯') for s in en.load_spells('actor')))

    def test_y11_plus_stable_identity_phase_and_template_references(self):
        cards = [c for c in cd.ALL_CHARACTERS if c['serial'] in {*(f'Y{i}' for i in range(11, 18)), 'Y60'}]
        self.assertEqual(len({c['serial'] for c in cards}), 8)
        for card in cards:
            identities = [(s['name'], s.get('phase', 0)) for s in card['spells']]
            self.assertEqual(len(identities), len(set(identities)))
            for spell in card['spells']:
                self.assertIsInstance(spell.get('phase', 0), int)
                for effect in spell['effects']:
                    if effect.get('召唤物模板'):
                        self.assertIn(effect['召唤物模板'], cd.SUMMON_TEMPLATES)
        for template in cd.SUMMON_TEMPLATES.values():
            if template.get('merge_result'):
                self.assertIn(template['merge_result'], cd.SUMMON_TEMPLATES)
            for spell in template.get('spells', []):
                self.assertIsInstance(spell['index'], int)
                for effect in spell['effects']:
                    if effect.get('zone_半径'):
                        self.assertGreater(effect['zone_半径'], 0)

    def test_y17_01_unique_radius_source_zero_one_two_mp(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            for extra in (0, 1, 2):
                en = blood_setup(cls)
                original = blood_spell(en, 6)
                self.assertNotIn('radius_per_extra_mp', original)
                self.assertFalse(any('radius_per_extra_mp' in e for e in original['effects']))
                spell = dict(original, _blood_options={'extra_mp': extra})
                before = copy.deepcopy(en.get_char('blood').attrs)
                rng = random.getstate()
                plan = en._y17_plan('blood', 'enemy', spell, ai=True)
                self.assertEqual(plan['spell']['effects'][0]['作用半径'], 10 + extra * 5)
                self.assertEqual(en.get_char('blood').attrs, before)
                self.assertEqual(random.getstate(), rng)
                self.assertEqual(en._y17_preflight('blood', spell, ai=True), '')
                mp = en.get_char('blood').get_attr('魔力')
                with patch.object(be, 'roll_dice', return_value=1):
                    self.assertTrue(be.action_committed(en._execute_spell('blood', 'enemy', spell)))
                self.assertEqual(mp - en.get_char('blood').get_attr('魔力'), plan['mp'])

    def test_y60_duration_one_has_no_round_start_damage(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls, 'Y60')
            en._set_effects([])
            source = en._unit_il_entry('actor', en._get_initiative())
            field = dict(name='一轮领域', index=91, 消耗mp=1, effects=[
                dict(type=8, 客体=135, 作用半径=99, 每回合伤害骰='3', 持续回合=1)])
            en._execute_spell('actor', 'actor', field)
            before = en._get_combat_hp('enemy')
            entries = en._get_initiative()
            en._get_state()['activeIndex'] = len(entries) - 1
            en._advance_turn(entries[-1]['userId'])
            self.assertEqual(en._get_combat_hp('enemy'), before)
            self.assertFalse(any(e.get('spellName') == '一轮领域' for e in en._get_effects()))

    def test_y11_02_independent_lethality_effect_after_miss(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls, 'Y11')
            spell = next(s for s in en.load_spells('actor') if s['name'] == '挥动镰刀')
            with patch.object(en, '_roll_spell_success_bp', return_value=(99, '')):
                en._execute_spell('actor', 'enemy', spell)
            self.assertTrue(en._get_buff_leth_adv_dis('enemy')[0])


if __name__ == '__main__':
    unittest.main()
