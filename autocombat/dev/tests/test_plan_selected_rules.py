"""Selected phase-0 decisions through Full, Fast and HTTP/PvP entry points."""
import copy
import unittest
from unittest.mock import patch
import ai_trainer as tr
import battle_engine as be
import characters_data as cd
from tests.test_y12_summons import setup
from tests.test_y14_balance import setup as rescue_setup, spell as rescue_spell
from tests.test_battle_execution import http_module


class SelectedRulesTests(unittest.TestCase):
    def test_rescue_training_and_tournament_commands_select_a_legal_ally(self):
        from tests.test_summon_groups import battle_classes
        tournament, _ = battle_classes()
        for cls, path in ((be.FastBattleEngine, 'training'), (tournament.BattleEngine, 'command')):
            en = rescue_setup(cls, phase=3)
            spell = next(s for s in en.load_spells('actor') if s['name'] == '光速救援')
            before = en.get_char('actor').get_attr('魔力')
            result = tr.execute_training_action(en, 'actor', f"SKILL_{spell['index']}__T0") if path == 'training' else en._execute_ai_command('actor', f".s{spell['index']}")
            self.assertTrue(be.action_committed(result), str(result))
            self.assertEqual(en.get_char('actor').get_attr('魔力'), before - 5)
            self.assertEqual(en._get_map()['occupants']['B4'], 'ally')

    def test_y14_01_to_04_rescue_preserves_ally_and_syncs_all_slots(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine, http_module().PvPFullBattleEngine):
            en = rescue_setup(cls, phase=3)
            spell = next(s for s in en.load_spells('actor') if s['name'] == '光速救援')
            before = copy.deepcopy((en.get_char('actor').attrs, en._get_map(), en._get_actions()))
            for invalid in ('actor', 'enemy', None):
                self.assertFalse(be.action_committed(en._execute_spell('actor', invalid, spell)))
                self.assertEqual(before, (en.get_char('actor').attrs, en._get_map(), en._get_actions()))
            mp = en.get_char('actor').get_attr('魔力')
            self.assertTrue(be.action_committed(en._execute_spell('actor', 'ally', spell)))
            self.assertEqual(en.get_char('actor').get_attr('魔力'), mp - 5)
            md = en._get_map()
            self.assertEqual(md['occupants']['B4'], 'ally')
            self.assertEqual(md['occupants']['A3'], 'actor')
            self.assertNotIn('B2', md['occupants'])
            rows = [row for row in en._get_initiative() if en._resolve_uid(row['userId']) == 'actor']
            self.assertTrue(all(row['coord'] == row['_anchor_coord'] == 'A3' for row in rows))
            recipients = {e['targetUserId'] for e in en._get_effects() if e.get('spellName') == '光速救援' and e['type'] == 'shield'}
            self.assertEqual(recipients, {'actor', 'ally'})

    def test_y14_03a_no_empty_square_rejects_without_payment(self):
        en = rescue_setup(phase=3)
        md = en._get_map()
        md['obstacles'] = {be.format_coord(c, r): True for c in range(md['width']) for r in range(md['height'])}
        en._set_map(md)
        before = copy.deepcopy((en.get_char('actor').attrs, en._get_actions(), md))
        spell = next(s for s in en.load_spells('actor') if s['name'] == '光速救援')
        self.assertFalse(be.action_committed(en._execute_spell('actor', 'ally', spell)))
        self.assertEqual(before, (en.get_char('actor').attrs, en._get_actions(), en._get_map()))

    def test_y14_05_direct_spell_advantage_not_duplicated_at_hard_rank(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine, http_module().PvPFullBattleEngine):
            en = rescue_setup(cls)
            en._execute_spell('actor', 'actor', rescue_spell(en, 12))
            for rank in (1, 2):
                with patch.object(be, 'roll_dice_detailed', side_effect=[(2, '2'), (7, '7')]) as dice:
                    en._apply_spell_damage('actor', 'enemy', '1d8', 0, 0, atk_rank=rank)
                self.assertEqual(dice.call_count, 2)

    def test_y16_01a_02a_clone_members_share_template_but_own_resources(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls, 'Y16')
            owner = en.get_char('actor')
            owner.set_attr('敏捷', 88)
            first = en._create_summon('actor', '影之克隆', shared_roll=40, shared_rank=1)
            second = en._create_summon('actor', '影之克隆')
            for uid in (first, second):
                self.assertEqual(en._get_combat_hp(uid), 3)
                self.assertEqual(en.get_char(uid).get_attr('体力上限'), 3)
                self.assertEqual(en._get_summon_entry(uid)['dex'], 88)
                self.assertEqual(en._get_summon_entry(uid)['skills'][0]['val'], 75)
                self.assertTrue(any(k.startswith('SKILL_1__') for k, _ in tr.get_summon_actions(en, uid)))
            self.assertEqual(en._get_summon_entry(first)['initRank'], be.success_rank(40, 88))
            old = owner.get_attr('魔力')
            sibling_mp = en.get_char(second).get_attr('魔力')
            spell = en.load_spells(first)[0]
            clone_mp = en.get_char(first).get_attr('魔力')
            self.assertTrue(be.action_committed(en._execute_spell(first, first, spell)))
            self.assertLess(en.get_char(first).get_attr('魔力'), clone_mp)
            self.assertEqual(owner.get_attr('魔力'), old)
            self.assertEqual(en.get_char(second).get_attr('魔力'), sibling_mp)
            self.assertIsNot(en.get_char(first).spells, owner.spells)

    def test_y16_clone_inherits_passive_rules_for_moon_and_bow_costs(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine, http_module().PvPFullBattleEngine):
            en = setup(cls, 'Y16')
            owner = en.get_char('actor')
            moon_uid = en._create_summon('actor', '虚假之月')
            en.get_char(moon_uid).set_attr('魔力', 21)
            clone_uid = en._create_summon('actor', '影之克隆')
            clone = en.get_char(clone_uid)

            self.assertEqual(clone.get_str('_passive_rules'), owner.get_str('_passive_rules'))
            # Clones restored from older saves may have no serialized rules.
            clone.set_str('_passive_rules', '{}')
            self.assertEqual(en._y16_resource_state(clone_uid)[2], 2)

            clone.set_str('_weapon_name', 'bow')
            bow_spell = {'name': '克隆齐射', '消耗mp': 2,
                         'effects': [{'bow_volley': 3, '伤害骰': '1d2'}]}
            self.assertEqual(en._y16_preflight(clone_uid, bow_spell, check_cost=True), '')
            self.assertEqual(en._y16_prepare_spell(clone_uid, bow_spell)['消耗mp'], 3)

    def test_y16_04a_protection_scopes_owner_and_stacks_per_event(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine, http_module().PvPFullBattleEngine):
            en = setup(cls, 'Y16')
            hit = en._create_summon('actor', '影之克隆')
            sibling = en._create_summon('actor', '影之克隆')
            unrelated = en._create_summon('ally', '影之克隆')
            for _ in range(2):
                en._absorb_damage_with_shield(hit, 1)
            for uid in ('actor', sibling):
                buffs = [e for e in en._get_active_buffs(uid) if e.get('auxCode') == 30]
                self.assertEqual(len(buffs), 2)
            self.assertFalse(any(e.get('auxCode') == 30 for e in en._get_active_buffs(unrelated)))
            self.assertFalse(any(e.get('auxCode') == 30 for e in en._get_active_buffs('ally')))

    def test_clone_direct_spell_miss_and_full_shield_hit(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine, http_module().PvPFullBattleEngine):
            en = setup(cls, 'Y16')
            uid = en._create_summon('actor', '影之克隆')
            attack = dict(name='直伤入口', index=90, 消耗mp=1, effects=[dict(type=1, 客体=4,
                成功率=50, 伤害骰='1', 可闪避性=0, 可反击性=0)])
            with patch.object(en, '_roll_spell_success_bp', return_value=(99, '')):
                en._execute_spell('enemy', uid, attack)
            self.assertFalse(any(e.get('auxCode') == 30 for e in en._get_active_buffs('actor')))
            en._set_effects(en._get_effects() + [dict(type='shield', sourceUserId=uid, targetUserId=uid,
                                                   value=100, remainingRounds=3)])
            with patch.object(en, '_roll_spell_success_bp', return_value=(40, '')):
                en._execute_spell('enemy', uid, attack)
            self.assertEqual(en._get_combat_hp(uid), 3)
            self.assertEqual(len([e for e in en._get_active_buffs('actor') if e.get('auxCode') == 30]), 1)

    def test_clone_weapon_switch_refreshes_inherited_spell_candidates(self):
        en = setup(be.FastBattleEngine, 'Y16')
        uid = en._create_summon('actor', '影之克隆')
        switch = next(s for s in en.load_spells(uid) if s['index'] == 8)
        self.assertTrue(be.action_committed(en._execute_spell(uid, uid, switch)))
        names = [s['index'] for s in en.load_spells(uid)]
        self.assertIn(10, names)
        self.assertNotIn(11, names)

    def test_y16_05a_moon_aura_has_no_initial_heal_and_outlives_timer(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls, 'Y16')
            hp = en._get_combat_hp('actor')
            en._set_combat_hp('actor', hp - 5)
            uid = en._create_summon('actor', '虚假之月')
            self.assertEqual(en._get_combat_hp('actor'), hp - 5)
            zone = next(e for e in en._get_effects() if e.get('sourceUserId') == uid and e.get('spellName') == '月能馈赠')
            self.assertEqual((zone['radius'], zone['filter'], zone['tickHealHp'], zone['tickHealMp']), (20, 3, '1d4', '1d4'))
            zone['remainingRounds'] = 1
            en._set_effects(en._get_effects())
            en._tick_down_effects()
            self.assertTrue(any(e.get('sourceUserId') == uid and e.get('sourceBound') for e in en._get_effects()))
            en._set_combat_hp(uid, 0)
            en._tick_down_effects()
            self.assertFalse(any(e.get('sourceUserId') == uid and e.get('sourceBound') for e in en._get_effects()))


if __name__ == '__main__':
    unittest.main()
