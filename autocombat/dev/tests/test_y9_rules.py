"""Y9-01..06: real casts, physical members and serialized rule state."""
import copy
import json
import unittest
from unittest.mock import patch

import battle_engine as be
import characters_data as cd
from battle_http_server import PvPFullBattleEngine, ReactionNeeded


CLASSES = (be.FullBattleEngine, be.FastBattleEngine, PvPFullBattleEngine)


def setup(cls=be.FullBattleEngine):
    engine = cls()
    for uid, serial in [('wood', 'Y9'), ('ally', 'Y1'), ('enemy', 'Y1')]:
        cd.load_character_to_engine(engine, next(c for c in cd.ALL_CHARACTERS
                                                if c['serial'] == serial), uid)
        engine.process_command(uid, '.hs')
    engine.setup_battle(['wood', 'ally'], ['enemy'], '12x12', season_status=50)
    return engine


def trigger(engine):
    return next(e for e in engine._get_effects() if e.get('type') == 'hpTrigger')


class ThresholdTests(unittest.TestCase):
    def test_registration_defers_all_costs_and_failure_is_once_per_band(self):
        for cls in CLASSES:
            with self.subTest(engine=cls.__name__):
                e = setup(cls)
                c = e.get_char('wood')
                mp, san = c.get_attr('魔力'), c.get_attr('理智')
                spell = next(s for s in e.load_spells('wood') if s['name'] == '阶段转换')
                e._set_effects([])
                self.assertTrue(be.action_committed(e._execute_spell('wood', 'wood', spell)))
                self.assertEqual((c.get_attr('魔力'), c.get_attr('理智')), (mp, san))
                e._set_combat_hp('wood', int(c.get_attr('体力上限') * .6))
                with patch.object(be.random, 'randint', return_value=99) as dice:
                    e._check_hp_triggers()
                    e._set_effects(json.loads(json.dumps(e._get_effects())))
                    e._check_hp_triggers()
                self.assertEqual(dice.call_count, 1)
                self.assertEqual(len(trigger(e)['attemptedThresholds']), 1)
                self.assertEqual((c.get_attr('魔力'), c.get_attr('理智')), (mp, san))
                e._set_combat_hp('wood', max(1, int(c.get_attr('体力上限') * .1)))
                c.set_attr('理智', 1)
                with patch.object(be.random, 'randint', side_effect=[99, 99, 100]) as dice, \
                        patch.object(be, 'roll_dice', return_value=12):
                    e._check_hp_triggers()
                self.assertEqual(dice.call_count, 3)  # 60%, 80%, 100% in this round
                self.assertEqual(c.phase, 2)
                self.assertEqual(c.get_attr('魔力'), mp - 1)
                self.assertEqual(c.get_attr('理智'), 0)
                self.assertTrue(trigger(e)['triggered'])
                e._check_hp_triggers()
                self.assertEqual(c.get_attr('魔力'), mp - 1)

    def test_crossed_bands_stop_on_first_success(self):
        e = setup()
        e._set_combat_hp('wood', 1)
        with patch.object(be.random, 'randint', side_effect=[99, 50]) as dice, \
                patch.object(be, 'roll_dice', return_value=7):
            e._check_hp_triggers()
        self.assertEqual(dice.call_count, 2)
        self.assertEqual(len(trigger(e)['attemptedThresholds']), 2)

    def test_no_mp_or_dead_owner_does_not_consume_bands(self):
        e = setup()
        e._set_combat_hp('wood', 1)
        e.get_char('wood').set_attr('魔力', 0)
        with patch.object(be.random, 'randint') as dice:
            e._check_hp_triggers()
        dice.assert_not_called()
        self.assertEqual(trigger(e)['attemptedThresholds'], [])


class SpearTests(unittest.TestCase):
    def test_training_tournament_command_and_http_ai_reach_same_formula(self):
        import ai_trainer
        import battle_http_server as srv
        from tests.test_summon_groups import battle_classes
        classes, _ = battle_classes()
        for mode in ('training', 'tournament', 'http_ai'):
            with self.subTest(mode=mode):
                cls = be.FastBattleEngine if mode == 'training' else PvPFullBattleEngine
                e = setup(cls)
                e._set_effects([])
                il = e._get_initiative()
                il.sort(key=lambda entry: entry['userId'] != 'wood')
                e._set_initiative(il)
                state = e._get_state()
                state['activeIndex'] = 0
                e._set_state(state)
                e._set_actions({u: {'主动': 2, '附加': 3} for u in ('wood', 'ally', 'enemy')})
                before_mp = e.get_char('wood').get_attr('魔力')
                with patch.object(e, '_roll_spell_success_bp', return_value=(40, '')), \
                        patch.object(be, 'roll_dice', return_value=6), \
                        patch.object(be, 'roll_dice_detailed', return_value=(10, '10')):
                    if mode == 'training':
                        ai_trainer.execute_action(e, 'wood', 'SKILL_2__T0')
                    elif mode == 'tournament':
                        controller = classes.AIController('wood', cd.MULUO, 'Y')
                        command = controller._command_for_action(e, 'wood', 'SKILL_2__T0')
                        e._execute_ai_command('wood', command)
                    else:
                        e._player_controllers = {'ally': ['a'], 'enemy': ['b']}
                        with patch.object(srv, '_ai_decide_action', side_effect=[('.s2', None), ('.i end', None)]):
                            srv._run_ai_turns(e)
                heals = [h for h in e._get_effects() if h.get('type') == 'delayedHeal']
                self.assertEqual([(h['targetUserId'], h['totalHeal']) for h in heals], [('enemy', 5)])
                self.assertEqual(e.get_char('wood').get_attr('魔力'), before_mp - 3)

    def cast(self, cls, target, shield=0, miss=False):
        e = setup(cls)
        e._set_effects([])
        if shield:
            e._set_effects([dict(type='shield', targetUserId=target, value=shield,
                                remainingRounds=9, persistent=0)])
        spell = copy.deepcopy(next(s for s in e.load_spells('wood') if s['name'] == '生之矛'))
        eff = spell['effects'][0]
        eff.update(伤害骰='10', 友方伤害骰='10', 可闪避性=0, 可反击性=0, 可格挡性=0)
        hp = e._get_combat_hp(target)
        with patch.object(e, '_roll_spell_success_bp', return_value=(100 if miss else 40, '')), \
                patch.object(be, 'roll_dice', return_value=15 if target == 'ally' else 6):
            e._execute_spell('wood', target, spell)
        heals = [h for h in e._get_effects() if h.get('type') == 'delayedHeal']
        return e, hp, heals

    def test_friend_formula_uses_actual_loss_and_preserves_full_shield_floor(self):
        for cls in CLASSES:
            for shield, expected_loss, expected_total in [(0, 10, 20), (6, 4, 15), (100, 0, 15)]:
                with self.subTest(engine=cls.__name__, shield=shield):
                    e, hp, heals = self.cast(cls, 'ally', shield)
                    self.assertEqual(e._get_combat_hp('ally'), hp - expected_loss)
                    self.assertEqual([(h['targetUserId'], h['totalHeal'], h['remainingRounds'])
                                      for h in heals], [('ally', expected_total, 3)])

    def test_enemy_formula_after_shield_without_friendly_duplicate(self):
        for cls in CLASSES:
            for shield, expected in [(0, 5), (6, 2), (100, 0)]:
                with self.subTest(engine=cls.__name__, shield=shield):
                    e, hp, heals = self.cast(cls, 'enemy', shield)
                    self.assertEqual([h['totalHeal'] for h in heals], [expected] if expected else [])
                    self.assertTrue(all(h['targetUserId'] == 'enemy' for h in heals))

    def test_scheduled_friend_heal_has_three_future_pulses_and_expires(self):
        for cls in CLASSES:
            e, hp, heals = self.cast(cls, 'ally', shield=100)
            self.assertEqual(e._get_combat_hp('ally'), hp)  # No immediate recovery.
            e._set_combat_hp('ally', hp - 20)
            increments = []
            for _ in range(4):
                before = e._get_combat_hp('ally')
                e._tick_down_effects()
                increments.append(e._get_combat_hp('ally') - before)
            self.assertEqual(increments, [5, 5, 5, 0])
            self.assertFalse(any(h.get('type') == 'delayedHeal' for h in e._get_effects()))

    def test_miss_never_schedules_floor_heal(self):
        for cls in CLASSES:
            for target in ('ally', 'enemy'):
                e, hp, heals = self.cast(cls, target, miss=True)
                self.assertEqual(e._get_combat_hp(target), hp)
                self.assertEqual(heals, [])

    def test_pvp_pause_resume_preserves_enemy_formula_and_charges_once(self):
        import battle_http_server as srv
        for dodge_roll, expected in [(1, 0), (99, 5)]:
            e = setup(PvPFullBattleEngine)
            e._set_effects([])
            e._player_controllers = {'enemy': ['human']}
            spell = copy.deepcopy(next(s for s in e.load_spells('wood') if s['name'] == '生之矛'))
            spell['effects'][0].update(伤害骰='10', 可闪避性=1, 可反击性=0)
            before_mp = e.get_char('wood').get_attr('魔力')
            with patch.object(e, '_roll_spell_success_bp', return_value=(40, '')), \
                    patch.object(be, 'roll_dice', return_value=6):
                with self.assertRaises(ReactionNeeded) as pending:
                    e._execute_spell('wood', 'enemy', spell)
            self.assertEqual(e.get_char('wood').get_attr('魔力'), before_mp - 3)
            self.assertFalse(any(h.get('type') == 'delayedHeal' for h in e._get_effects()))
            with patch.object(srv, 'roll_d100', return_value=(dodge_roll, '')), \
                    patch.object(be, 'roll_d100', return_value=(dodge_roll, '')), \
                    patch.object(be, 'roll_dice', return_value=6):
                e.resolve_spell_reaction(pending.exception.data, 'dodge')
            heals = [h['totalHeal'] for h in e._get_effects() if h.get('type') == 'delayedHeal']
            self.assertEqual(heals, [expected] if expected else [])
            self.assertEqual(e.get_char('wood').get_attr('魔力'), before_mp - 3)


class IgnitionTests(unittest.TestCase):
    def test_y9_04_zone_expires_and_transition_removes_only_owner_zones(self):
        for cls in CLASSES:
            e = setup(cls)
            e._set_effects([])
            spell = next(s for s in e.load_spells('wood') if s['name'] == '领域·生')
            e._execute_spell('wood', 'wood', spell)
            zone = next(f for f in e._get_effects() if f.get('type') == 'zone')
            self.assertEqual(zone['remainingRounds'], 3)
            for _ in range(3):
                e._tick_down_effects()
            self.assertFalse(any(f.get('type') == 'zone' for f in e._get_effects()))
            e._execute_spell('wood', 'wood', spell)
            effects = e._get_effects()
            other = dict(zone, sourceUserId='ally', remainingRounds=3)
            effects.append(other)
            e._set_effects(effects)
            e._transition_phase('wood', 2)
            self.assertEqual([f['sourceUserId'] for f in e._get_effects()
                              if f.get('type') == 'zone'], ['ally'])

    def test_y9_05_summon_fee_uses_granted_count_and_zero_capacity_is_atomic(self):
        for cls in CLASSES:
            e = setup(cls)
            e._set_effects([])
            spell = next(s for s in e.load_spells('wood') if any(
                f.get('type') == 5 for f in s['effects']))
            # Leave a single simultaneous slot; request more through the real cast.
            capacity, _ = e._get_summon_quota('wood', '生灵')
            for _ in range(capacity - 1):
                e._create_summon('wood', '生灵')
            mp = e.get_char('wood').get_attr('魔力')
            with patch.object(be, 'roll_dice', return_value=8):
                e._execute_spell('wood', 'wood', spell)
            self.assertEqual(e.get_char('wood').get_attr('魔力'), mp - 1)
            snapshot = copy.deepcopy((e._get_effects(), e._summon_members, e._get_actions(),
                                      e.get_char('wood').attrs))
            result = e._execute_spell('wood', 'wood', spell)
            self.assertFalse(be.action_committed(result))
            self.assertEqual(snapshot, (e._get_effects(), e._summon_members, e._get_actions(),
                                        e.get_char('wood').attrs))

    def test_transition_ignites_all_members_and_tick_charges_each_once(self):
        for cls in CLASSES:
            with self.subTest(engine=cls.__name__):
                e = setup(cls)
                members = [e._create_summon('wood', '生灵') for _ in range(3)]
                outsider = e._create_summon('enemy', '生灵')
                e._transition_phase('wood', 2)
                for sid in members:
                    self.assertTrue(e._summon_members[sid]['ignited'])
                    self.assertTrue(e._unit_il_entry(sid, e._get_initiative())['ignited'])
                    self.assertEqual(e._summon_members[sid]['ignite_tick_dmg'], 3)
                self.assertFalse(e._summon_members[outsider].get('ignited', False))
                hp = {sid: e._get_combat_hp(sid) for sid in members}
                e._tick_down_effects()
                self.assertEqual({sid: e._get_combat_hp(sid) for sid in members},
                                 {sid: value - 3 for sid, value in hp.items()})
                # Promotion must retain the new representative's ignition state.
                e._set_combat_hp(members[0], 0)
                e._reconcile_summon_groups()
                slot = e._unit_il_entry(members[1], e._get_initiative())
                self.assertTrue(slot['ignited'])

    def test_phase_two_cast_marks_new_members_and_keeps_99_round_timers(self):
        for cls in CLASSES:
            e = setup(cls)
            e._transition_phase('wood', 2)
            e.get_char('wood').set_attr('魔力', 100)
            spell = next(s for s in e.load_spells('wood') if any(
                f.get('type') == 5 for f in s['effects']))
            with patch.object(be, 'roll_dice', return_value=2):
                self.assertTrue(be.action_committed(e._execute_spell('wood', 'wood', spell)))
            members = list(e._summon_members)
            self.assertTrue(members)
            self.assertTrue(all(e._summon_members[s]['ignited'] for s in members))
            timers = [f for f in e._get_effects() if f.get('type') == 'summon']
            self.assertEqual(len(timers), len(members))
            self.assertTrue(all(f['remainingRounds'] == 99 for f in timers))


if __name__ == '__main__':
    unittest.main()
