"""Cartridge regressions: observable combat behavior and resource invariants."""
import copy
import random
import unittest
from unittest.mock import patch

import ai_trainer as tr
import battle_engine as be
import characters_data as cd
from tests.test_summon_groups import battle_classes


def setup(cls=be.FullBattleEngine, phase=2):
    random.seed(73)
    en = cls()
    for uid, serial in [('actor', 'Y14'), ('enemy', 'Y4'), ('ally', 'Y1')]:
        card = copy.deepcopy(next(c for c in cd.ALL_CHARACTERS if c['serial'] == serial))
        if uid == 'actor':
            card['initial_phase'] = phase
        cd.load_character_to_engine(en, card, uid)
        en.process_command(uid, '.hs')
    en.setup_battle(['actor', 'ally'], ['enemy'], '12x12', season_status=50)
    il = en._get_initiative()
    for row in il:
        row['coord'] = {'actor': 'B2', 'ally': 'B4', 'enemy': 'D2'}[en._resolve_uid(row['userId'])]
        row['_anchor_coord'] = row['coord']
        row['_move_used'] = 0
    en._set_initiative(il)
    md = en._get_map()
    md['obstacles'] = {}
    md['occupants'] = {row['coord']: row['userId'] for row in il}
    en._set_map(md)
    en._get_state()['activeIndex'] = next(i for i, row in enumerate(il) if row['userId'] == 'actor')
    return en


def spell(en, index):
    return next(s for s in en.load_spells('actor') if s['index'] == index)


def snapshot(en):
    return copy.deepcopy((en.get_char('actor').attrs, en._get_actions(), en._get_effects(),
                          en._get_initiative(), en._get_map(), en._get_counter('actor', 'limit_gauge')))


class CartridgeTests(unittest.TestCase):
    def test_candidates_are_read_only_deduplicated_and_payable(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                en = setup(cls)
                before = snapshot(en)
                rng = random.getstate()
                keys = [k for k, _ in tr.get_available_actions(en, 'actor')]
                self.assertEqual(snapshot(en), before)
                self.assertEqual(random.getstate(), rng)
                self.assertIn('SKILL_1__T0', keys)
                self.assertIn('SKILL_3__T0', keys)
                self.assertNotIn('SKILL_1__T1', keys)
                self.assertFalse(any(k.startswith(('SKILL_2__', 'SKILL_11__', 'SKILL_10__', 'SKILL_16__')) for k in keys))
                self.assertEqual(len([k for k in keys if k.startswith('SKILL_12__')]), 1)

    def test_empty_switch_rejected_by_all_entrypoints(self):
        for path in ('direct', 'command', 'training'):
            with self.subTest(path=path):
                en = setup()
                sp = spell(en, 2)
                before = snapshot(en)
                rng = random.getstate()
                if path == 'direct':
                    result = en._execute_spell('actor', 'actor', sp)
                elif path == 'command':
                    result = en._execute_ai_command('actor', '.a s2')
                else:
                    result = tr.execute_training_action(en, 'actor', 'SKILL_2__T0')
                self.assertFalse(be.action_committed(result))
                self.assertEqual(before, snapshot(en))
                self.assertEqual(random.getstate(), rng)

    def test_no_legal_enemy_removes_damage_candidates(self):
        en = setup()
        with patch.object(en, '_enemy_target_entries', return_value=[]):
            keys = [k for k, _ in tr.get_available_actions(en, 'actor')]
            self.assertFalse(any(k.startswith(('BASIC_ATTACK', 'SKILL_9__', 'SKILL_10__', 'SKILL_14__', 'SKILL_15__')) for k in keys))
            before = snapshot(en)
            self.assertFalse(be.action_committed(en._execute_spell('actor', None, spell(en, 9))))
            self.assertEqual(before, snapshot(en))

    def test_shared_cooldown_and_single_cost(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                en = setup(cls)
                mp = en.get_char('actor').get_attr('魔力')
                extra = en._get_my_actions('actor')['附加']
                result = tr.execute_training_action(en, 'actor', 'SKILL_1__T0')
                self.assertTrue(be.action_committed(result))
                self.assertEqual(en.get_char('actor').phase, 1)
                self.assertEqual(en.get_char('actor').get_attr('魔力'), mp - 2)
                self.assertEqual(en._get_my_actions('actor')['附加'], extra - 1)
                self.assertEqual([en._get_cooldown('actor', i) for i in (1, 2, 3)], [3, 3, 3])
                self.assertFalse(any(k.startswith(('SKILL_1__', 'SKILL_2__', 'SKILL_3__')) for k, _ in tr.get_available_actions(en, 'actor')))
                before = snapshot(en)
                self.assertFalse(be.action_committed(en._execute_spell('actor', 'actor', spell(en, 3))))
                self.assertEqual(snapshot(en), before)
                en._transition_phase('actor', 3)
                for expected in (2, 1, 0):
                    en._tick_down()
                    en._get_state()['round'] += 1
                    self.assertEqual([en._get_cooldown('actor', i) for i in (1, 2, 3)], [expected] * 3)

    def test_real_round_boundaries_unlock_at_round_four(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls)
            en._execute_spell('actor', 'actor', spell(en, 1))
            for round_number in (2, 3, 4):
                il = en._get_initiative()
                en._get_state()['activeIndex'] = len(il) - 1
                en._end_turn(il[-1]['userId'])
                self.assertEqual(en._get_state()['round'], round_number)
                self.assertEqual(en._get_cooldown('actor', 3), 4 - round_number)

    def test_rejected_limit_keeps_gauge_mp_actions_and_once_flag(self):
        en = setup()
        en._mod_counter('actor', 'limit_gauge', 5)
        en.get_char('actor').set_attr('魔力', 9)
        before = snapshot(en)
        self.assertFalse(be.action_committed(en._execute_spell('actor', 'enemy', spell(en, 16))))
        self.assertEqual(snapshot(en), before)
        self.assertNotIn('极限技·凶斩', getattr(en.get_char('actor'), '_once_consumed', set()))

    def test_limit_commits_once_and_cannot_reappear_in_candidates(self):
        en = setup()
        en._mod_counter('actor', 'limit_gauge', 5)
        mp = en.get_char('actor').get_attr('魔力')
        self.assertTrue(be.action_committed(en._execute_spell('actor', 'enemy', spell(en, 16))))
        self.assertEqual(en._get_counter('actor', 'limit_gauge'), 1)  # successful hit charges this round once
        self.assertEqual(en.get_char('actor').get_attr('魔力'), mp - 10)
        en._mod_counter('actor', 'limit_gauge', 5)
        self.assertFalse(any(k.startswith('SKILL_16__') for k, _ in tr.get_available_actions(en, 'actor')))
        before = snapshot(en)
        self.assertFalse(be.action_committed(en._execute_spell('actor', 'enemy', spell(en, 16))))
        self.assertEqual(before, snapshot(en))

    def test_final_mp_recheck_does_not_consume_gauge(self):
        en = setup()
        en._mod_counter('actor', 'limit_gauge', 5)
        en.get_char('actor').set_attr('魔力', 10)
        before = snapshot(en)
        with patch.object(en, '_get_buff_mp_cost_pct', side_effect=[1, 2]):
            result = en._execute_spell('actor', 'enemy', spell(en, 16))
        self.assertFalse(be.action_committed(result))
        self.assertEqual(before, snapshot(en))

    def test_http_manual_dodge_and_counter_use_resource_hooks(self):
        from tests.test_battle_execution import http_module
        srv = http_module()
        for choice, phase in [('dodge', 3), ('counter', 2)]:
            with self.subTest(choice=choice):
                en = setup(srv.PvPFullBattleEngine, phase=phase)
                if choice == 'counter':
                    en._execute_spell('actor', 'actor', spell(en, 12))
                with patch.object(srv, 'roll_d100', return_value=(50, '')):
                    with self.assertRaises(srv.ReactionNeeded) as pending:
                        en._coc7_attack('enemy', 'actor', '斗殴', 70, '1d4', 1, 0)
                with patch.object(srv, 'roll_d100', return_value=(1, '')):
                    en.resolve_reaction(pending.exception.data, choice)
                self.assertEqual(en._get_counter('actor', 'rings' if choice == 'dodge' else 'limit_gauge'),
                                 1 if choice == 'dodge' else 2)

    def test_http_contra_basic_attack_reaches_ranged_target(self):
        from tests.test_battle_execution import http_module
        srv = http_module()
        en = setup(srv.PvPFullBattleEngine, phase=1)
        with patch.object(en, '_move_to_attack_range', return_value=(True, 'B2')) as move, \
                patch.object(srv, 'roll_d100', return_value=(50, '')):
            with self.assertRaises(srv.ReactionNeeded):
                en._coc7_attack('actor', 'enemy', '斗殴', 70, en._get_damage_dice('actor', '斗殴'), 1, 0)
        self.assertEqual(move.call_args.args, ('actor', 'enemy', 6))

    def test_new_phase_passives_are_free_and_idempotent(self):
        en = setup()
        mp = en.get_char('actor').get_attr('魔力')
        en._mod_counter('actor', 'limit_gauge', 3)
        for phase in (1, 3, 2, 3, 2):
            en._transition_phase('actor', phase)
            en._y14_initialize_phase('actor')
        self.assertEqual(en.get_char('actor').get_attr('魔力'), mp)
        self.assertEqual(en._get_counter('actor', 'limit_gauge'), 3)
        for kind in ('rings', 'limit_gauge'):
            self.assertEqual(sum(e.get('counter_type') == kind for e in en._get_effects()), 1)

    def test_substitute_pays_at_lethal_hit_and_preserves_transition_state(self):
        en = setup(phase=1)
        char = en.get_char('actor')
        char.set_attr('魔力', 10)
        en._y14_start_cooldown('actor')
        en._set_combat_hp('actor', -10, source_dmg=200, damage_source='enemy')
        self.assertEqual(en._get_combat_hp('actor'), 1)
        self.assertEqual(char.get_attr('魔力'), 2)
        self.assertEqual(char.phase, 2)
        self.assertTrue(char.get_attr('永久移除阶段') & (1 << 1))
        self.assertTrue(any(e.get('counter_type') == 'limit_gauge' for e in en._get_effects()))
        self.assertTrue(any(e.get('spellIndex') == 11 for e in en._get_effects()))
        self.assertEqual(en._get_cooldown('actor', 3), 3)
        for i in (1, 2, 3):
            for e in en._get_effects():
                if e.get('type') == 'cooldown':
                    e['remainingRounds'] = 0
        char.set_attr('魔力', 30)
        before = snapshot(en)
        self.assertFalse(be.action_committed(en._execute_spell('actor', 'actor', spell(en, 1))))
        self.assertEqual(before, snapshot(en))

    def test_substitute_requires_eight_mp_and_cannot_rearm(self):
        for mp in (7, 8):
            en = setup(phase=1)
            char = en.get_char('actor')
            char.set_attr('魔力', mp)
            with patch.object(en, '_check_con_save', return_value=False):
                en._set_combat_hp('actor', 0, source_dmg=200, damage_source='enemy')
            self.assertEqual(en._get_combat_hp('actor'), 0 if mp == 7 else 1)
            self.assertEqual(char.get_attr('魔力'), 7 if mp == 7 else 0)
            if mp == 8:
                char.phase = 1
                en.load_spells('actor')
                self.assertFalse(be.action_committed(en._execute_spell('actor', 'actor', spell(en, 8))))

    def test_modes_are_exclusive_and_reset_on_reentry(self):
        en = setup()
        initial_mov = en._get_effective_mov('actor')
        self.assertEqual(en.get_char('actor')._y14_mode, 'swift')
        self.assertTrue(be.action_committed(en._execute_spell('actor', 'actor', spell(en, 12))))
        self.assertEqual(en._get_effective_mov('actor'), initial_mov - 7)
        self.assertFalse(any(e.get('spellIndex') == 11 for e in en._get_effects()))
        before = snapshot(en)
        self.assertFalse(be.action_committed(en._execute_spell('actor', 'actor', spell(en, 12))))
        self.assertEqual(before, snapshot(en))
        en._y14_initialize_phase('actor')
        self.assertEqual(en.get_char('actor')._y14_mode, 'brave')
        en._transition_phase('actor', 3)
        self.assertFalse(any(e.get('spellIndex') in (11, 12) for e in en._get_effects()))
        en._transition_phase('actor', 2)
        self.assertEqual(en.get_char('actor')._y14_mode, 'swift')
        self.assertEqual(en._get_effective_mov('actor'), initial_mov)

    def test_basic_damage_and_range_follow_card_and_mode(self):
        en = setup()
        expected = {1: ('2d6', 6), 2: (be._resolve_db('1d8+1d6+db', be._get_db_str(en.get_char('actor'))), 2),
                    3: (be._resolve_db('1d4+2+db', be._get_db_str(en.get_char('actor'))), 2)}
        for phase in (1, 3, 2):
            en._transition_phase('actor', phase)
            self.assertEqual(en._y14_attack_profile('actor'), expected[phase])
            self.assertEqual(en._get_damage_dice('actor', '斗殴'), expected[phase][0])
        en._execute_spell('actor', 'actor', spell(en, 12))
        self.assertIn('+4+', en._get_damage_dice('actor', '斗殴'))

    def test_sonic_rings_require_real_movement_and_share_dodge_limit(self):
        en = setup(phase=3)
        en._execute_spell('actor', 'actor', spell(en, 19))
        self.assertEqual(en._get_counter('actor', 'rings'), 0)
        self.assertTrue(en._move_explicit('actor', 'I2').committed)
        self.assertEqual(en._get_counter('actor', 'rings'), 0)
        self.assertTrue(en._move_explicit('actor', 'J2').committed)
        self.assertEqual(en._get_counter('actor', 'rings'), 1)
        en._y14_event('actor', 'dodge')
        self.assertEqual(en._get_counter('actor', 'rings'), 1)
        en._get_state()['round'] += 1
        en._y14_event('actor', 'dodge')
        self.assertEqual(en._get_counter('actor', 'rings'), 2)
        hp = en._get_combat_hp('actor')
        en._set_combat_hp('actor', hp - 1, source_dmg=1, damage_source='enemy')
        self.assertEqual(en._get_counter('actor', 'rings'), 1)
        en._transition_phase('actor', 2)
        self.assertEqual(en._get_counter('actor', 'rings'), 0)
        en._transition_phase('actor', 3)
        self.assertEqual(en._get_counter('actor', 'rings'), 0)

    def test_successful_spell_dodge_gains_ring_but_double_failure_does_not(self):
        en = setup(phase=3)
        eff = {'type': 1, '客体': 4}
        with patch.object(be, 'roll_d100', return_value=(1, '')):
            en._resolve_spell_dodge('enemy', 'actor', eff, {}, '1d4', 99, atk_rank=1)
        self.assertEqual(en._get_counter('actor', 'rings'), 1)
        en._get_state()['round'] += 1
        with patch.object(be, 'roll_d100', return_value=(99, '')):
            en._resolve_spell_dodge('enemy', 'actor', eff, {}, '1d4', 1, atk_rank=-1)
        self.assertEqual(en._get_counter('actor', 'rings'), 1)

    def test_unfunded_ring_heal_is_atomic(self):
        en = setup(phase=3)
        keys = [k for k, _ in tr.get_available_actions(en, 'actor')]
        self.assertFalse(any(k.startswith(('SKILL_21__', 'SKILL_22__')) for k in keys))
        before = snapshot(en)
        self.assertFalse(be.action_committed(en._execute_spell('actor', 'actor', spell(en, 22))))
        self.assertEqual(before, snapshot(en))
        en._mod_counter('actor', 'rings', 2)
        en.get_char('actor').set_attr('魔力', 3)
        before = snapshot(en)
        self.assertFalse(be.action_committed(en._execute_spell('actor', 'actor', spell(en, 22))))
        self.assertEqual(before, snapshot(en))

    def test_damage_triggers_are_once_per_round_and_ignore_hp_costs(self):
        en = setup()
        for _ in range(3):
            en._set_combat_hp('enemy', en._get_combat_hp('enemy') - 1, source_dmg=1, damage_source='actor')
            en._set_combat_hp('actor', en._get_combat_hp('actor') - 1, source_dmg=1, damage_source='enemy')
        self.assertEqual(en._get_counter('actor', 'limit_gauge'), 2)
        en._set_combat_hp('actor', en._get_combat_hp('actor') - 1)
        self.assertEqual(en._get_counter('actor', 'limit_gauge'), 2)
        en._get_state()['round'] += 1
        en._set_combat_hp('enemy', en._get_combat_hp('enemy') - 1, source_dmg=1, damage_source='actor')
        self.assertEqual(en._get_counter('actor', 'limit_gauge'), 3)

    def test_basic_and_counter_damage_are_wired_in_both_engines(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            for counter in (False, True):
                with self.subTest(engine=cls.__name__, counter=counter):
                    en = setup(cls)
                    if counter:
                        en._execute_spell('actor', 'actor', spell(en, 12))
                    en._react_dw = {'actor': 0, 'enemy': 100}
                    en._react_cw = {'actor': 100, 'enemy': 0}
                    attack = en._fast_coc7_attack if cls is be.FastBattleEngine else en._coc7_attack
                    src, dst = ('enemy', 'actor') if counter else ('actor', 'enemy')
                    rolls = [(99, ''), (1, '')] if counter else [(1, ''), (99, '')]
                    with patch.object(be, 'roll_d100', side_effect=rolls):
                        attack(src, dst, '斗殴', 70, en._get_damage_dice(src, '斗殴'), 1, 0)
                    self.assertEqual(en._get_counter('actor', 'limit_gauge'), 2 if counter else 1)

    def test_winning_brave_counter_charges_when_shield_absorbs_damage(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls)
            en._execute_spell('actor', 'actor', spell(en, 12))
            effects = en._get_effects()
            effects.append(dict(type='shield', targetUserId='enemy', sourceUserId='enemy',
                                value=1000, remainingRounds=2))
            en._set_effects(effects)
            en._react_dw = {'actor': 0}
            en._react_cw = {'actor': 100}
            attack = en._fast_coc7_attack if cls is be.FastBattleEngine else en._coc7_attack
            hp = en._get_combat_hp('enemy')
            with patch.object(be, 'roll_d100', side_effect=[(99, ''), (1, '')]):
                attack('enemy', 'actor', '斗殴', 70, '1d4', 1, 0)
            self.assertEqual(en._get_combat_hp('enemy'), hp)
            self.assertEqual(en._get_counter('actor', 'limit_gauge'), 1)

    def test_training_sampler_and_action_reset_use_y14_budget(self):
        en = setup(be.FastBattleEngine)
        en._reset_round_actions(character_main=2)
        self.assertEqual(en._get_my_actions('actor')['主动'], en._calc_main_actions('actor'))
        self.assertEqual(en._get_my_actions('enemy')['主动'], 2)
        cards = [copy.deepcopy(next(c for c in cd.ALL_CHARACTERS if c['serial'] == s)) for s in ('Y14', 'Y4')]
        random.seed(1234)
        updates = tr._mp_run_battle((['Y14'], ['Y4'], '10x10', cards,
                                    {'Y14': 'actor', 'Y4': 'enemy'}, cd.SUMMON_TEMPLATES))
        self.assertTrue(updates)

    def test_ally_first_dying_is_physical_unit_scoped(self):
        en = setup()
        il = en._get_initiative()
        clone = copy.deepcopy(next(e for e in il if e['userId'] == 'actor'))
        clone['userId'] = 'actor__act1'
        clone['baseUserId'] = 'actor'
        il.append(clone)
        en._set_initiative(il)
        en._on_ally_dying('ally')
        en._on_ally_dying('ally')
        self.assertEqual(en._get_counter('actor', 'limit_gauge'), 2)

    def test_legacy_self_action_weights_are_read_without_mutation(self):
        table = {((), 'SKILL_1__T1'): 2, ((), 'SKILL_1__T3'): 4}
        old = dict(table)
        self.assertEqual(be.CombatEngine._y14_action_q(table, (), 'SKILL_1__T0'), 3)
        self.assertEqual(table, old)
        table[((), 'SKILL_1__T0')] = 0
        self.assertEqual(be.CombatEngine._y14_action_q(table, (), 'SKILL_1__T0'), 0)

    def test_tournament_and_rule_fallback_do_not_select_unpayable_spells(self):
        classes, _ = battle_classes()
        en = setup(classes.BattleEngine, phase=3)
        data = next(c for c in cd.ALL_CHARACTERS if c['serial'] == 'Y14')
        ai = classes.AIController('actor', data, 'Y')
        en._set_combat_hp('actor', 1)
        with patch.object(classes.AIController, 'Q_SOLO', None), patch.object(classes.AIController, 'Q_TEAM', None):
            with patch.object(random, 'random', return_value=1):
                self.assertNotIn(ai.decide_action(en, 'actor'), ['.s21', '.s22'])


if __name__ == '__main__':
    unittest.main()
