from pathlib import Path

p = Path(__file__).resolve().parents[2] / 'tests' / 'test_support_balance.py'
s = p.read_text(encoding='utf-8')
marker = "\nif __name__ == '__main__':\n    unittest.main()\n"
assert marker in s
new_tests = '''

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
        self.assertNotIn('回复hp', cd.ITEM_TEMPLATES['蛋糕幸运'])
        self.assertNotIn('回复mp', cd.ITEM_TEMPLATES['蛋糕幸运'])

    def test_lucky_buff_duration_is_data_driven(self):
        eng = setup([('cake', 'Y8'), ('ally', 'Y1')], [('enemy', 'Y11')])
        with patch.dict(cd.ITEM_TEMPLATES,
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
        eff = {'type': 'battleSpirit', 'sourceUserId': source,
               'sourceTeam': eng._battle_team_of(source),
               'persistent': 1, 'remainingRounds': 999,
               'spellName': '战意值削弱', 'spellIndex': 1}
        eff.update(cfg)
        effects = eng._get_effects(); effects.append(eff); eng._set_effects(effects)
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
        self.assertEqual([a for a, _ in kept], ['SKILL_5__T0'])

'''
s = s.replace(marker, new_tests + marker)
p.write_text(s, encoding='utf-8')
print('tests appended')
