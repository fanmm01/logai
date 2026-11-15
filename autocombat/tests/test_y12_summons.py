"""Y12-01..06: physical summon spell resources, merging and open-rule baseline."""
import copy
import random
import unittest
from unittest.mock import patch

import ai_trainer as tr
import battle_engine as be
import characters_data as cd


def setup(cls=be.FullBattleEngine, serial='Y12'):
    random.seed(891)
    en = cls()
    for uid, role in [('actor', serial), ('ally', serial), ('enemy', 'Y4')]:
        cd.load_character_to_engine(en, copy.deepcopy(next(c for c in cd.ALL_CHARACTERS if c['serial'] == role)), uid)
        en.process_command(uid, '.hs')
    en.setup_battle(['actor', 'ally'], ['enemy'], '14x14', season_status=50)
    return en


class SummonRulesTests(unittest.TestCase):
    def setUp(self):
        tr._ACTION_CACHE.clear()

    def test_y12_01_template_zone_mapping_cost_and_cooldown(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls)
            uid = en._create_summon('actor', '龙虎')
            spell = next(s for s in en.load_spells(uid) if s['name'] == '治愈领域')
            self.assertEqual(spell['effects'][0]['作用半径'], 8)
            self.assertTrue(any(k.startswith('SKILL_2__') for k, _ in tr.get_summon_actions(en, uid)))
            mp = en.get_char(uid).get_attr('魔力')
            owner_mp = en.get_char('actor').get_attr('魔力')
            self.assertTrue(be.action_committed(en._execute_spell(uid, uid, spell)))
            self.assertEqual(en.get_char(uid).get_attr('魔力'), mp - 1)
            self.assertEqual(en.get_char('actor').get_attr('魔力'), owner_mp)
            self.assertEqual(en._get_cooldown(uid, 2), 3)
            self.assertFalse(be.action_committed(en._execute_spell(uid, uid, spell)))
            zone = next(e for e in en._get_effects() if e.get('sourceUserId') == uid and e['type'] == 'zone')
            self.assertEqual((zone['radius'], zone['tickHealHp'], zone['remainingRounds']), (8, '2d6', 3))

    def test_y12_02_turtle_hp_and_block_pool_are_independent(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls)
            uid = en._create_summon('actor', '玄武')
            self.assertEqual(en._get_combat_hp(uid), 120)
            self.assertEqual(en.get_char(uid).get_attr('体力上限'), 120)
            self.assertEqual(en.get_char(uid).get_attr('可格挡'), 1)
            entry = en._get_summon_entry(uid)
            self.assertEqual(entry['shield_block_hp'], 100)
            self.assertTrue(entry['ignore_unreactable_block'])

    def test_y12_03_exhaustion_rejects_before_rng_resources_and_actions(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls)
            en._summoned_once['actor'] = {'玄武', '朱雀', '龙虎'}
            spell = next(s for s in en.load_spells('actor') if any(e.get('召唤物模板') == '随机召唤物' for e in s['effects']))
            before = copy.deepcopy((en.get_char('actor').attrs, en._get_actions(), en._get_effects()))
            rng = random.getstate()
            self.assertFalse(be.action_committed(en._execute_spell('actor', 'actor', spell)))
            self.assertEqual(before, (en.get_char('actor').attrs, en._get_actions(), en._get_effects()))
            self.assertEqual(random.getstate(), rng)
            en._summoned_once['actor'].remove('龙虎')
            with patch.object(be.random, 'choice', side_effect=AssertionError('unique candidate must be deterministic')):
                uid = en._create_summon('actor', '随机召唤物')
            self.assertEqual(en._summon_members[uid]['name'], '龙虎')

    def test_y12_04_05_merge_is_owner_scoped_and_replaces_turtle(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls)
            turtle = en._create_summon('actor', '玄武')
            for name in ('朱雀', '龙虎'):
                uid = en._create_summon('actor', name)
                en._set_combat_hp(uid, 0)
            other = en._create_summon('ally', '龙虎')
            en._check_trinity_merge()
            template = cd.SUMMON_TEMPLATES['三合一']
            self.assertEqual(en._summon_members[turtle]['name'], '三合一')
            self.assertEqual(en._summon_members[other]['name'], '龙虎')
            self.assertEqual(en._get_combat_hp(turtle), template['HP'])
            self.assertEqual(en.get_char(turtle).get_attr('魔力'), template['MP'])
            self.assertEqual(en.get_char(turtle).get_attr('敏捷'), template['DEX'])
            self.assertEqual(en.get_char(turtle).get_attr('力量'), template['STR'])
            self.assertEqual(en.get_char(turtle).get_attr('盾击'), 70)
            self.assertEqual(en.get_char(turtle).get_attr('格挡'), template['格挡'])
            self.assertEqual(sum(occupant == turtle for occupant in en._get_map()['occupants'].values()), 1)
            self.assertEqual(en._get_summon_entry(turtle)['shield_block_hp'], template.get('shield_block', 0))
            self.assertEqual([s['name'] for s in en.load_spells(turtle)], [s['name'] for s in template.get('spells', [])])

    def test_y12_06_open_template_baseline(self):
        skill = next(s for s in cd.SUMMON_TEMPLATES['三合一']['skills'] if s.get('hits'))
        self.assertEqual((skill['hits'], skill['on_whiff_aoe_dmg'], skill['on_whiff_mp_cost']), (2, '4d10', 10))

    def test_y12_06_open_runtime_auto_and_explicit_hit_difference(self):
        for explicit in (False, True):
            en = setup()
            uid = en._create_summon('actor', '三合一')
            entry = en._get_summon_entry(uid)
            water = next(s for s in entry['skills'] if s.get('hits') == 2)
            en._set_summon_entry_fields(uid, skills=[water])
            hp = en._get_combat_hp('enemy')
            with patch.object(en, '_coc7_attack', return_value=('enemy', uid, ['造成 1 点伤害'])), \
                    patch.object(en, '_roll_check_d100', return_value=(1, '')), \
                    patch.object(be, 'roll_dice', return_value=2):
                if explicit:
                    tr.execute_summon_action(en, uid, 'SUMMON_SKILL_0__T0')
                else:
                    en._summon_attack(uid)
            # Legacy auto adds parsed first-hit log damage to its extra-hit write.
            self.assertEqual(hp - en._get_combat_hp('enemy'), 0 if explicit else 3)

    def test_y12_07_summon_reward_is_partly_credited_to_cast(self):
        updates = [('team', 'Y12', (1, 2), 'SKILL_4__T0', 0.02, (2, 2))]
        origins = {'actor_sum_1': 0}

        tr._credit_summon_owner_action(updates, origins, 'actor_sum_1', 0.4)
        tr._credit_summon_owner_action(updates, origins, 'unrelated_sum_1', 0.4)

        self.assertAlmostEqual(updates[0][4], 0.02 + tr.SUMMON_OWNER_CREDIT * 0.4)


if __name__ == '__main__':
    unittest.main()
