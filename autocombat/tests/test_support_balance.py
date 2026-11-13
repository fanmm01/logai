#!/usr/bin/env python3
"""Deterministic support balance regression tests."""
import json
import random
import unittest
from unittest.mock import patch

import battle_engine as be
import characters_data as cd

be._SUMMON_TEMPLATES = cd.SUMMON_TEMPLATES


def char(serial):
    return next(c for c in cd.ALL_CHARACTERS if c['serial'] == serial)


def setup(team_a, team_b, map_size='12x12'):
    eng = be.FullBattleEngine()
    for uid, serial in team_a + team_b:
        cd.load_character_to_engine(eng, char(serial), uid)
        eng.process_command(uid, '.hs')
        eng.get_char(uid).set_attr('魔力', 999)
    eng.setup_battle([u for u, _ in team_a], [u for u, _ in team_b], map_size, season_status=50)
    return eng


class SupportMechanicsTests(unittest.TestCase):
    def test_hp_cake_rescues_dying_character_immediately(self):
        eng = setup([('cake', 'Y8')], [('enemy', 'Y1')])
        cake = eng.get_char('cake')
        cake.inventory = [{'item': '蛋糕HP', 'count': 1,
                           'sourceTeam': 'Y', 'sourceUserId': 'cake',
                           '消费追加回复hp': '2d8+4', 'tasteBonusOwnerSerial': 'Y8'}]
        with patch.object(eng, '_check_con_save', return_value=True):
            eng._set_combat_hp('cake', 0, source_dmg=10)
        self.assertGreater(eng._get_combat_hp('cake'), 0)
        self.assertIsNone(eng._is_dying('cake'))
        self.assertEqual(cake.inventory, [])

    def _party(self, size):
        chars = [('cake', 'Y8')]
        chars += [(f'a{i}', f'Y{i}') for i in range(1, size)]
        return chars

    def _alloc(self, size, seed=17):
        party = self._party(size)
        eng = setup(party, [('enemy', 'Y11')])
        spell = next(s for s in eng.load_spells('cake') if s['name'] == '最后的款待-进攻')
        eff = next(e for e in spell['effects'] if e.get('制造物模板') == '蛋糕轻盈')
        random.seed(seed)
        out = eng._execute_personal_feast('cake', eff, spell, 0)
        return eng, party, out

    def test_personal_feast_two_three_four_party_counts(self):
        expected = {
            2: [1, 2],
            3: [1, 2, 2],
            4: [0, 2, 2, 2],
        }
        for size, want in expected.items():
            with self.subTest(size=size):
                eng, party, _ = self._alloc(size)
                got = [sum(e['count'] for e in eng.get_char(uid).inventory if e['item'] == '蛋糕轻盈')
                       for uid, _ in party]
                self.assertEqual(got, want)
                self.assertFalse(any(e['type'] == 'create' and e.get('template') == '蛋糕轻盈'
                                     for e in eng._get_effects()))

    def test_personal_feast_five_party_cap_and_seed_reproducible(self):
        eng1, party, _ = self._alloc(5, seed=171)
        first = [sum(e['count'] for e in eng1.get_char(uid).inventory if e['item'] == '蛋糕轻盈')
                 for uid, _ in party]
        eng2, party2, _ = self._alloc(5, seed=171)
        second = [sum(e['count'] for e in eng2.get_char(uid).inventory if e['item'] == '蛋糕轻盈')
                  for uid, _ in party2]
        self.assertEqual(first, second)
        self.assertEqual(sum(first), 6)
        self.assertEqual(first[0], 0)
        self.assertTrue(all(0 <= amount <= 2 for amount in first[1:]))

    def test_character_data_distribution_override_changes_generic_engine(self):
        eng, party, _ = self._alloc(4)
        spell = next(s for s in eng.load_spells('cake') if s['name'] == '最后的款待-进攻')
        eff = next(e for e in spell['effects'] if e.get('制造物模板') == '蛋糕幸运')
        eff['分发给总数上限'] = 4
        eff['分发自留'] = 2
        eff['分发队友每人'] = 1
        random.seed(4)
        eng._execute_personal_feast('cake', eff, spell, 0)
        counts = [sum(e['count'] for e in eng.get_char(uid).inventory if e['item'] == '蛋糕幸运')
                  for uid, _ in party]
        self.assertEqual(sum(counts), 4)
        self.assertEqual(counts[0], 1)  # Configured self portion=2; trim removes one cake from self.
        self.assertTrue(all(0 <= n <= 1 for n in counts[1:]))

    def test_zone_pulses_are_json_safe_and_keep_hp_mp_ticks(self):
        eng = setup([('healer', 'Y7'), ('ally', 'Y1')], [('enemy', 'Y11')])
        spell = next(s for s in eng.load_spells('healer') if s['name'] == '治愈领域')
        before_hp = eng._get_combat_hp('ally')
        before_mp = eng.get_char('ally').get_attr('魔力')
        eng._execute_spell('healer', 'healer', spell)
        self.assertGreaterEqual(eng._get_combat_hp('ally'), before_hp)
        self.assertGreaterEqual(eng.get_char('ally').get_attr('魔力'), before_mp)
        for _ in range(3):
            eng._tick_down(); eng._apply_zone_effects()
            json.dumps(eng._get_effects(), ensure_ascii=False)
        zones = [e for e in eng._get_effects() if e.get('type') == 'zone' and e.get('spellName') == '治愈领域']
        self.assertEqual(zones, [])

    def test_snow_curtain_is_source_bound_and_full_duration(self):
        eng = setup([('snow', 'Y6'), ('ally', 'Y1')], [('enemy', 'Y11')])
        snow = next(s for s in eng.load_spells('snow') if s['name'] == '雪幕')
        eng._execute_spell('snow', 'snow', snow)
        curtain = next(e for e in eng._get_effects() if e.get('type') == 'zone' and e.get('sourceBound'))
        self.assertEqual(curtain['filter'], 13)
        self.assertEqual(curtain['auxVal'], '-10')
        for _ in range(8):
            eng._tick_down()
        self.assertTrue(any(e.get('sourceBound') for e in eng._get_effects() if e.get('type') == 'zone'))
        with patch.object(eng, '_check_con_save', return_value=False):
            eng._set_combat_hp('snow', 0, source_dmg=eng._get_combat_hp('snow') + 1)
        eng._tick_down()
        self.assertFalse(any(e.get('sourceBound') for e in eng._get_effects() if e.get('type') == 'zone'))

    def test_revive_once_preflight_and_summon_transfer_to_healer(self):
        eng = setup([('healer', 'Y7'), ('ally', 'Y1'), ('owner', 'Y2')], [('enemy', 'Y11')])
        summon = eng._create_summon('owner', '使魔')
        eng.get_char(summon).spells = []
        with patch.object(eng, '_check_con_save', return_value=False):
            eng._set_combat_hp(summon, 0, source_dmg=eng._get_combat_hp(summon) + 1)
        eng._record_graveyard_entry(summon, reason='test_death')
        with patch.object(eng, '_check_con_save', return_value=False):
            eng._set_combat_hp('owner', 0, source_dmg=eng._get_combat_hp('owner') + 1)
        eng._record_graveyard_entry('owner', reason='test_death')
        spell = next(s for s in eng.load_spells('healer') if s['name'] == '返生之花')
        eff = spell['effects'][0]
        candidates = eng._revive_candidates('healer', eff)
        self.assertEqual({g['uid'] for g in candidates}, {'owner', summon})
        owner_hp = eng._restore_graveyard_unit('healer', next(g for g in candidates if g['uid'] == 'owner'), eff)
        self.assertEqual(owner_hp, int(eng.get_char('owner').get_attr('体力上限') * .4))
        summon_grave = next(g for g in eng._revive_candidates('healer', eff) if g['uid'] == summon)
        summon_hp = eng._restore_graveyard_unit('healer', summon_grave, eff)
        self.assertGreater(summon_hp, 0)
        member = eng._summon_members[summon]
        self.assertEqual(member['ownerId'], 'healer')
        self.assertEqual(eng._summon_group_key(summon), summon)
        self.assertIn(summon, eng._summon_groups[summon]['members'])
        self.assertTrue(eng._graveyard[summon]['revived'])
        self.assertIsNone(eng._restore_graveyard_unit('healer', summon_grave, eff))
        # A real cast commits the once-per-battle ledger and the 99-round cooldown.
        self.assertEqual(eng._get_cooldown('healer', spell['index']), 0)
        eng2 = setup([('healer', 'Y7'), ('ally', 'Y1')], [('enemy', 'Y11')])
        with patch.object(eng2, '_check_con_save', return_value=False):
            eng2._set_combat_hp('ally', 0, source_dmg=eng2._get_combat_hp('ally') + 5)
        loaded = next(s for s in eng2.load_spells('healer') if s['name'] == '返生之花')
        self.assertEqual(loaded.get('cooldown_rounds'), 99)
        eng2._execute_spell('healer', 'healer', loaded)
        self.assertGreater(eng2._get_combat_hp('ally'), 0)
        self.assertEqual(eng2._get_cooldown('healer', loaded['index']), 99)
        msg = eng2._execute_spell('healer', 'healer', loaded)
        self.assertIn('已使用过', msg)
        eng2.setup_battle(['healer', 'ally'], ['enemy'], '12x12', season_status=50)
        self.assertEqual(eng2._get_cooldown('healer', loaded['index']), 0)
        self.assertEqual(eng2._revive_use_ledger, set())



class LuckyCakeAllD100Tests(unittest.TestCase):
    def _eat_lucky(self, eng, uid):
        eng.get_char(uid).inventory = [
            {'item': '蛋糕幸运', 'count': 1, 'sourceTeam': 'Y', 'sourceUserId': uid}]
        text, ok = eng._eat_cake(uid)
        self.assertTrue(ok, text)
        return text

    def test_lucky_cake_five_round_buff_and_refresh_not_stack(self):
        eng = setup([('cake', 'Y8'), ('ally', 'Y1')], [('enemy', 'Y11')])
        self._eat_lucky(eng, 'cake')
        buffs = [b for b in eng._get_active_buffs('cake')
                 if b.get('auxCode') == 34]
        self.assertEqual(len(buffs), 1)
        self.assertEqual(buffs[0]['remainingRounds'], 5)
        # Re-eating refreshes duration, never stacks another bonus die.
        self._eat_lucky(eng, 'cake')
        buffs = [b for b in eng._get_active_buffs('cake') if b.get('auxCode') == 34]
        self.assertEqual(len(buffs), 1)
        self.assertEqual(be._calc_net_bp(eng._get_active_buffs('cake'), '', '斗殴'), 'b')
        for _ in range(5):
            eng._tick_down()
        self.assertFalse([b for b in eng._get_active_buffs('cake')
                          if b.get('auxCode') == 34])

    def test_lucky_bonus_applies_to_con_save_but_not_attack_only_buffs(self):
        eng = setup([('cake', 'Y8'), ('ally', 'Y1')], [('enemy', 'Y11')])
        seen = {}

        def fake_roll(bp=''):
            seen['bp'] = bp
            return (1, '')

        with patch.object(be, 'roll_d100', side_effect=fake_roll):
            eng._check_con_save('ally')   # Y1 carries attack-only AUX16 passive
            self.assertEqual(seen['bp'], '')
            self._eat_lucky(eng, 'cake')
            eng._check_con_save('cake')
            self.assertEqual(seen['bp'], 'b')
            eng._check_con_save('ally')
            self.assertEqual(seen['bp'], '')

    def test_lucky_bonus_reaches_attack_check_once_and_skips_damage_dice(self):
        eng = setup([('cake', 'Y8'), ('ally', 'Y1')], [('enemy', 'Y11')])
        self._eat_lucky(eng, 'cake')
        self.assertEqual(be._calc_net_bp(eng._get_active_buffs('cake'), '', '斗殴'), 'b')
        # Damage dice are rolled elsewhere and never consult bonus-dice buffs.
        self.assertEqual(be.roll_dice('1d1'), 1)
        import sys
        lucky = sys.modules['characters_data'].ITEM_TEMPLATES['蛋糕幸运']
        self.assertNotIn('回复hp', lucky)
        self.assertNotIn('回复mp', lucky)

    def test_lucky_buff_duration_is_data_driven(self):
        # Other test modules may swap sys.modules['characters_data'] to the PvP
        # table; always patch the table the engine's lazy imports will read.
        import sys
        active_templates = sys.modules['characters_data'].ITEM_TEMPLATES
        eng = setup([('cake', 'Y8'), ('ally', 'Y1')], [('enemy', 'Y11')])
        with patch.dict(active_templates,
                        {'蛋糕幸运': {'辅助效果': '全D100检定奖励骰',
                                      '辅助效果值': 'b', 'buff持续回合': 2}}):
            self._eat_lucky(eng, 'cake')
            self.assertTrue([b for b in eng._get_active_buffs('cake')
                             if b.get('auxCode') == 34])
            eng._tick_down(); eng._tick_down()
            self.assertFalse([b for b in eng._get_active_buffs('cake')
                              if b.get('auxCode') == 34])


class BattleSpiritGroupTests(unittest.TestCase):
    def _wire(self, eng, source='healer', **cfg):
        # Y7's passive already auto-cast its own aura at setup; replace it so the
        # test's configured effect is the only source.
        effects = [e for e in eng._get_effects() if e.get('type') != 'battleSpirit']
        eff = {'type': 'battleSpirit', 'sourceUserId': source,
               'sourceTeam': eng._battle_team_of(source),
               'persistent': 1, 'remainingRounds': 999,
               'spellName': '战意值削弱', 'spellIndex': 1}
        eff.update(cfg)
        effects.append(eff); eng._set_effects(effects)
        return eff

    def test_stacks_live_on_group_and_survive_representative_change(self):
        eng = setup([('healer', 'Y7'), ('ally', 'Y1')], [('owner', 'Y2')])
        first = eng._create_summon('owner', '使魔')
        second = eng._create_summon('owner', '使魔')
        group_key = eng._summon_group_key(first)
        self.assertEqual(eng._summon_group_key(second), group_key)
        self._wire(eng)
        with patch.object(be.random, 'randint', return_value=1):
            eng._process_battle_spirit()             # initialize
            eng._process_battle_spirit()             # 5 -> 4
        grp = eng._summon_groups[group_key]
        self.assertEqual(grp['battle_spirit'], 4)
        self.assertEqual(grp['battle_spirit_penalty_dice'], 1)
        with patch.object(eng, '_check_con_save', return_value=False):
            eng._set_combat_hp(first, 0, source_dmg=eng._get_combat_hp(first) + 1)
        eng._ensure_group_representative(group_key)
        self.assertEqual(eng._summon_groups[group_key]['battle_spirit'], 4)
        self.assertEqual(eng._summon_groups[group_key]['battle_spirit_penalty_dice'], 1)

    def test_group_withdraws_at_zero_without_graveyard(self):
        eng = setup([('healer', 'Y7'), ('ally', 'Y1')], [('owner', 'Y2')])
        summon = eng._create_summon('owner', '使魔')
        key = eng._summon_group_key(summon)
        self._wire(eng)
        eng._summon_groups[key]['battle_spirit'] = 1
        with patch.object(be.random, 'randint', return_value=1):
            msgs = eng._process_battle_spirit()
        self.assertTrue(any('撤离' in m for m in msgs))
        self.assertNotIn(key, eng._summon_groups)
        self.assertNotIn(summon, getattr(eng, '_graveyard', {}))
        self.assertFalse(any(e.get('userId') == summon for e in eng._get_initiative()))

    def test_multiple_sources_process_each_group_once(self):
        eng = setup([('healer', 'Y7'), ('healer2', 'Y7')], [('owner', 'Y2')])
        summon = eng._create_summon('owner', '使魔')
        key = eng._summon_group_key(summon)
        self._wire(eng, 'healer')
        self._wire(eng, 'healer2')
        eng._summon_groups[key]['battle_spirit'] = 5
        with patch.object(be.random, 'randint', return_value=1) as dice:
            eng._process_battle_spirit()
        self.assertEqual(dice.call_count, 1)
        self.assertEqual(eng._summon_groups[key]['battle_spirit'], 4)

    def test_dead_or_isolated_source_stops_aura(self):
        eng = setup([('healer', 'Y7'), ('ally', 'Y1')], [('owner', 'Y2')])
        summon = eng._create_summon('owner', '使魔')
        key = eng._summon_group_key(summon)
        self._wire(eng)
        eng._summon_groups[key]['battle_spirit'] = 5
        with patch.object(eng, '_check_con_save', return_value=False):
            eng._set_combat_hp('healer', 0, source_dmg=eng._get_combat_hp('healer') + 1)
        with patch.object(be.random, 'randint', return_value=1) as dice:
            msgs = eng._process_battle_spirit()
        self.assertEqual(msgs, [])
        self.assertEqual(dice.call_count, 0)
        self.assertEqual(eng._summon_groups[key]['battle_spirit'], 5)

    def test_initial_stacks_read_from_character_data_effect(self):
        eng = setup([('healer', 'Y7'), ('ally', 'Y1')], [('owner', 'Y2')])
        summon = eng._create_summon('owner', '使魔')
        key = eng._summon_group_key(summon)
        self._wire(eng, 战意初始层数=2)
        eng._process_battle_spirit()
        self.assertEqual(eng._summon_groups[key]['battle_spirit'], 2)


class ActionBudgetTests(unittest.TestCase):
    def test_dual_timing_skill_prefers_additional_slot(self):
        import ai_trainer
        eng = setup([('cake', 'Y8'), ('ally', 'Y1')], [('enemy', 'Y11')])
        eng._set_actions({'cake': {'主动': 2, '附加': 1}, 'ally': {'主动': 2, '附加': 3}})
        self.assertEqual(ai_trainer.action_slot_for(eng, 'cake', 'SKILL_5__T0'), '附加')
        eng._set_actions({'cake': {'主动': 2, '附加': 0}, 'ally': {'主动': 2, '附加': 3}})
        self.assertEqual(ai_trainer.action_slot_for(eng, 'cake', 'SKILL_5__T0'), '主动')
        self.assertEqual(ai_trainer.action_slot_for(eng, 'cake', 'EAT_CAKE__SELF'), '附加')

    def test_execute_action_uses_additional_action_while_available(self):
        import ai_trainer
        eng = setup([('cake', 'Y8'), ('ally', 'Y1')], [('enemy', 'Y11')])
        eng._set_actions({'cake': {'主动': 2, '附加': 1}})
        calls = []
        orig_add = eng._additional_action
        eng._additional_action = lambda uid, args: calls.append((uid, args)) or 'ok'
        try:
            ai_trainer.execute_action(eng, 'cake', 'SKILL_5__T0')
        finally:
            eng._additional_action = orig_add
        self.assertEqual(calls, [('cake', 's5')])

    def test_affordable_actions_filters_exhausted_slots(self):
        import ai_trainer
        eng = setup([('cake', 'Y8'), ('ally', 'Y1')], [('enemy', 'Y11')])
        eng._set_actions({'cake': {'主动': 0, '附加': 1}})
        acts = [('SKILL_5__T0', '轻盈蛋糕'), ('BASIC_ATTACK__T0', '普攻')]
        kept = ai_trainer.affordable_actions(eng, 'cake', acts)
        self.assertEqual(kept, [])  # Main actions exhausted: the turn ends immediately.



class DyingCleanupGuardTests(unittest.TestCase):
    def test_self_rescued_owner_keeps_summons_alive(self):
        eng = setup([('cake', 'Y8'), ('owner', 'Y1')], [('enemy', 'Y11')])
        summon = eng._create_summon('owner', '使魔')
        eng.get_char('owner').inventory = [
            {'item': '蛋糕HP', 'count': 1, 'sourceTeam': 'Y', 'sourceUserId': 'owner'}]
        with patch.object(eng, '_check_con_save', return_value=True):
            eng._set_combat_hp('owner', 0, source_dmg=eng._get_combat_hp('owner') + 1)
        self.assertGreater(eng._get_combat_hp('owner'), 0)
        self.assertFalse(eng._is_final_dead('owner'))
        self.assertTrue(any(e.get('userId') == summon for e in eng._get_initiative()))

    def test_final_dead_reports_true_only_without_dying(self):
        eng = setup([('cake', 'Y8'), ('owner', 'Y1')], [('enemy', 'Y11')])
        with patch.object(eng, '_check_con_save', return_value=True):
            eng._set_combat_hp('owner', 0, source_dmg=eng._get_combat_hp('owner') + 1)
        # Dying without a cake: HP 0 but still salvageable.
        self.assertTrue(eng._is_dying('owner'))
        self.assertFalse(eng._is_final_dead('owner'))
        eng._set_effects([e for e in eng._get_effects()
                          if not (e.get('type') == 'dying'
                                  and e.get('targetUserId') == 'owner')])
        self.assertTrue(eng._is_final_dead('owner'))



class ReviveSelectionTests(unittest.TestCase):
    def _dead_pair(self, eng):
        summon = eng._create_summon('owner', '使魔')
        eng.get_char(summon).spells = []
        with patch.object(eng, '_check_con_save', return_value=False):
            eng._set_combat_hp(summon, 0, source_dmg=eng._get_combat_hp(summon) + 1)
        eng._record_graveyard_entry(summon, reason='test_death')
        with patch.object(eng, '_check_con_save', return_value=False):
            eng._set_combat_hp('owner', 0, source_dmg=eng._get_combat_hp('owner') + 1)
        eng._record_graveyard_entry('owner', reason='test_death')
        return summon

    def test_owner_chosen_before_summon(self):
        eng = setup([('healer', 'Y7'), ('ally', 'Y1'), ('owner', 'Y2')], [('enemy', 'Y11')])
        summon = self._dead_pair(eng)
        spell = next(s for s in eng.load_spells('healer') if s['name'] == '返生之花')
        eff = spell['effects'][0]
        chosen = eng._select_revive_candidate('healer', eng._revive_candidates('healer', eff))
        self.assertEqual(chosen['uid'], 'owner')
        self.assertNotEqual(chosen['uid'], summon)

    def test_failed_preflight_consumes_nothing(self):
        eng = setup([('healer', 'Y7'), ('ally', 'Y1')], [('enemy', 'Y11')])
        spell = next(s for s in eng.load_spells('healer') if s['name'] == '返生之花')
        before_mp = eng.get_char('healer').get_attr('魔力')
        out = eng._execute_spell('healer', 'healer', spell)
        self.assertIn('没有符合条件的复活目标', out)
        self.assertEqual(eng.get_char('healer').get_attr('魔力'), before_mp)
        self.assertEqual(eng._get_cooldown('healer', spell['index']), 0)
        self.assertEqual(eng._revive_use_ledger, set())
        self.assertNotIn('返生之花', getattr(eng.get_char('healer'), '_once_consumed', set()))

    def test_revive_hp_ratio_is_data_driven(self):
        eng = setup([('healer', 'Y7'), ('ally', 'Y1'), ('owner', 'Y2')], [('enemy', 'Y11')])
        with patch.object(eng, '_check_con_save', return_value=False):
            eng._set_combat_hp('owner', 0, source_dmg=eng._get_combat_hp('owner') + 1)
        eng._record_graveyard_entry('owner', reason='test_death')
        spell = next(s for s in eng.load_spells('healer') if s['name'] == '返生之花')
        eff = dict(spell['effects'][0], 复活回复比例=0.5)
        hp = eng._restore_graveyard_unit('healer', next(iter(eng._revive_candidates('healer', eff))), eff)
        self.assertEqual(hp, int(eng.get_char('owner').get_attr('体力上限') * 0.5))

    def test_revive_window_zero_three_four(self):
        eng = setup([('healer', 'Y7'), ('ally', 'Y1'), ('owner', 'Y2')], [('enemy', 'Y11')])
        with patch.object(eng, '_check_con_save', return_value=False):
            eng._set_combat_hp('owner', 0, source_dmg=eng._get_combat_hp('owner') + 1)
        eng._record_graveyard_entry('owner', reason='test_death')
        spell = next(s for s in eng.load_spells('healer') if s['name'] == '返生之花')
        eff = spell['effects'][0]
        state = eng._get_state(); start = state.get('round', 1)
        def set_round(r):
            st = eng._get_state(); st['round'] = r; eng._set_state(st)
        grave = eng._graveyard['owner']
        set_round(start)
        self.assertTrue(eng._revive_candidates('healer', eff))       # diff 0
        set_round(start + 3)
        self.assertTrue(eng._revive_candidates('healer', eff))       # diff 3
        set_round(start + 4)
        self.assertFalse(eng._revive_candidates('healer', eff))      # diff 4


if __name__ == '__main__':
    unittest.main()
