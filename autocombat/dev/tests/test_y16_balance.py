"""Y16 resource conservation, weapon reachability, and training/runtime parity."""
import copy
import logging
import logging.handlers
import random
import unittest
from unittest.mock import patch

import ai_trainer as tr
import battle_engine as be
import characters_data as cd


def setup(cls=be.FullBattleEngine, reverse=False):
    random.seed(73)
    en = cls()
    for uid, card in [('actor', cd.LINBAI), ('enemy', cd.DANIUSI), ('ally', cd.LINGNIU)]:
        cd.load_character_to_engine(en, copy.deepcopy(card), uid)
        en.process_command(uid, '.hs')
    en.setup_battle(['enemy'] if reverse else ['actor', 'ally'],
                    ['actor', 'ally'] if reverse else ['enemy'], '10x10', season_status=50)
    return en


def spell(en, index):
    return next(s for s in en.load_spells('actor') if s['index'] == index)


def moon(en, energy=0):
    en._execute_spell('actor', 'actor', spell(en, 2))
    uid, ch = en._find_summons_by_field(en._battle_team_of('actor'), 'replenish_allies')[0]
    ch.set_attr('魔力', energy)
    return uid, ch


class MoonTests(unittest.TestCase):
    def setUp(self):
        tr._ACTION_CACHE.clear()
        templates = patch.object(be, '_SUMMON_TEMPLATES', cd.SUMMON_TEMPLATES)
        templates.start()
        self.addCleanup(templates.stop)

    def test_weapon_candidates_refresh_across_engines_and_cached_battles(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls)
            self.assertTrue(any(k.startswith('SKILL_11__') for k, _ in tr.get_available_actions(en, 'actor')))
            en._execute_spell('actor', 'actor', spell(en, 8))
            keys = [k for k, _ in tr.get_available_actions(en, 'actor')]
            self.assertTrue(any(k.startswith('SKILL_10__') for k in keys))
            self.assertFalse(any(k.startswith(('SKILL_8__', 'SKILL_11__')) for k in keys))
            en._execute_spell('actor', 'actor', spell(en, 9))
            self.assertFalse(any(k.startswith('SKILL_12__') for k, _ in tr.get_available_actions(en, 'actor')))
            moon(en, 21)
            self.assertTrue(any(k.startswith('SKILL_12__') for k, _ in tr.get_available_actions(en, 'actor')))
            fresh = setup(cls)
            self.assertFalse(any(k.startswith('SKILL_10__') for k, _ in tr.get_available_actions(fresh, 'actor')))

    def test_rejections_preserve_mana_moon_and_actions(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls)
            uid, ch = moon(en, 21)
            before = copy.deepcopy((en.get_char('actor').attrs, en._get_actions()))
            self.assertFalse(be.action_committed(en._execute_spell('actor', 'actor', spell(en, 7))))
            self.assertEqual(before, (en.get_char('actor').attrs, en._get_actions()))
            en._execute_spell('actor', 'actor', spell(en, 9))
            en.get_char('actor').set_attr('魔力', 9)
            result = en._execute_spell('actor', 'enemy', spell(en, 12))
            self.assertFalse(be.action_committed(result))
            self.assertEqual(ch.get_attr('魔力'), 21)
            self.assertGreater(en._get_combat_hp(uid), 0)
            self.assertEqual(en.get_char('actor').get_attr('魔力'), 9)
            en.get_char('actor').set_attr('魔力', 72)
            ch.set_attr('魔力', 20)
            self.assertFalse(be.action_committed(en._execute_spell('actor', 'enemy', spell(en, 12))))

    def test_transformed_cost_self_link_and_no_initial_immunity(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls)
            self.assertFalse(en._consume_immunity('actor'))
            original = en.get_char('actor').get_attr('魔力')
            with patch.object(be, 'roll_dice', return_value=4):
                uid, ch = moon(en, 4)
            self.assertEqual(en.get_char('actor').get_attr('魔力'), original - 4)
            self.assertTrue(en._has_moon_buff('actor'))
            self.assertTrue(any(b.get('auxCode') == 34 for b in en._get_active_buffs('actor')))

    def test_no_direct_moon_regeneration_and_empty_pool_survives(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls)
            uid, ch = moon(en)
            # No beneficiary link: isolate direct regeneration from legitimate overflow.
            en._set_effects([e for e in en._get_effects() if not e.get('linked_summon')])
            with patch.object(be, 'roll_dice', return_value=7):
                en._tick_down()
            self.assertEqual(ch.get_attr('魔力'), 0)
            self.assertGreater(en._get_combat_hp(uid), 0)
            self.assertEqual(en._charge_moon_energy(en._battle_team_of('actor'), 3), 3)
            self.assertEqual(ch.get_attr('魔力'), 3)

    def test_all_overflows_charge_once_on_either_team(self):
        for reverse in (False, True):
            en = setup(reverse=reverse)
            uid, ch = moon(en)
            actor = en.get_char('actor')
            actor.set_attr('魔力', actor.get_attr('魔力上限') - 2)
            actor.set_attr('理智', 98)
            en._set_combat_hp('actor', actor.get_attr('体力上限') - 3)
            self.assertEqual(en._restore_resource('actor', 7), 2)
            self.assertEqual(ch.get_attr('魔力'), 5)
            en._apply_hp_heal_amount('actor', 8)
            self.assertEqual(ch.get_attr('魔力'), 10)
            en._restore_resource('actor', 4, 'san')
            self.assertEqual(ch.get_attr('魔力'), 13)
            self.assertEqual(en._charge_heal_overflow(uid, 999), 0)
            self.assertEqual(ch.get_attr('魔力'), 13)
            en._set_combat_hp(uid, 0)
            self.assertEqual(en._charge_heal_overflow('actor', 5), 0)

    def test_natural_recovery_precedes_paid_topup(self):
        en = setup()
        uid, ch = moon(en, 10)
        for target in ('actor', 'ally'):
            actor = en.get_char(target)
            actor.set_attr('魔力', actor.get_attr('魔力上限') - 7)
        with patch.object(be, 'roll_dice', return_value=7):
            en._tick_down()
        self.assertEqual(ch.get_attr('魔力'), 9)
        self.assertGreater(en._get_combat_hp(uid), 0)

    def test_state_encodes_weapon_and_moon_without_changing_other_roles(self):
        en = setup()
        initial = tr.encode_state(en, 'actor')
        self.assertEqual(len(initial), 25)
        self.assertEqual(len(tr.encode_state(en, 'enemy')), 22)
        en._execute_spell('actor', 'actor', spell(en, 8))
        self.assertNotEqual(initial[-3:], tr.encode_state(en, 'actor')[-3:])
        moon(en, 21)
        self.assertEqual(tr.encode_state(en, 'actor')[-2:], (1, 2))

    def test_staff_charge_and_bow_volley_share_writing_and_charge_once(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls)
            _, ch = moon(en)
            with patch.object(en, '_roll_phantom_bonus', return_value=(2, 'writing\n')) as writing:
                before = en.get_char('actor').get_attr('魔力')
                en._execute_spell('actor', 'enemy', spell(en, 6))
                self.assertEqual(ch.get_attr('魔力'), 5)
                self.assertEqual(en.get_char('actor').get_attr('魔力'), before - 1)
                self.assertEqual(writing.call_count, 1)
            en._execute_spell('actor', 'actor', spell(en, 8))
            ch.set_attr('魔力', 0)
            with patch.object(en, '_roll_phantom_bonus', return_value=(2, 'writing\n')) as writing:
                with patch.object(en, '_handle_spell_damage_effect', wraps=en._handle_spell_damage_effect) as damage:
                    before = en.get_char('actor').get_attr('魔力')
                    en._execute_spell('actor', 'enemy', spell(en, 6))
                    self.assertEqual(damage.call_count, 3)
                self.assertEqual(writing.call_count, 1)
                self.assertEqual(ch.get_attr('魔力'), 4)
                self.assertEqual(en.get_char('actor').get_attr('魔力'), before - 2)
            self.assertEqual(len(spell(en, 6)['effects']), 1)

    def test_inspiration_two_stacks_refund_once_without_recursive_events(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls)
            for _ in range(3):
                en._execute_spell('actor', 'actor', spell(en, 1))
            stacks = [b for b in en._get_active_buffs('actor') if b.get('turn_mp_refund')]
            self.assertEqual(len(stacks), 2)
            self.assertFalse(any(b.get('auxCode') == 6 for b in stacks))
            self.assertTrue(any(k == 'SKILL_1__T0' for k, _ in tr.get_available_actions(setup(cls), 'actor')))
            self.assertFalse(any(k.startswith('SKILL_1__') for k, _ in tr.get_available_actions(en, 'actor')))
            en.get_char('actor').set_attr('魔力', 0)
            en._note_inspiration_event('actor')
            with patch.object(be, 'roll_dice', return_value=4):
                en._settle_inspiration('actor')
                self.assertEqual(en.get_char('actor').get_attr('魔力'), 8)
                en._note_inspiration_event('actor')
                en._settle_inspiration('actor')
                self.assertEqual(en.get_char('actor').get_attr('魔力'), 8)
            self.assertNotIn('actor', en._inspiration_pending)

    def test_shield_charges_only_damage_actually_absorbed(self):
        en = setup()
        _, ch = moon(en)
        en._execute_spell('actor', 'actor', spell(en, 11))
        value, _ = en._get_active_shields('actor')
        self.assertGreater(value, 0)
        result = en._absorb_damage_with_shield('actor', value + 10)
        self.assertEqual(result[:2], (10, value))
        self.assertEqual(ch.get_attr('魔力'), value)
        en._absorb_damage_with_shield('actor', 99)
        self.assertEqual(ch.get_attr('魔力'), value)

    def test_turn_end_refund_requires_committed_event(self):
        en = setup()
        en._execute_spell('actor', 'actor', spell(en, 1))
        en._inspiration_pending.clear()
        self.assertFalse(be.action_committed(en._execute_spell('actor', 'actor', spell(en, 7))))
        self.assertNotIn('actor', en._inspiration_pending)
        en._execute_spell('actor', 'actor', spell(en, 8))
        self.assertIn('actor', en._inspiration_pending)
        en.get_char('actor').set_attr('魔力', 10)
        state = en._get_state()
        state['activeIndex'] = next(i for i, row in enumerate(en._get_initiative()) if row['userId'] == 'actor')
        en._set_state(state)
        with patch.object(en, '_advance_turn', return_value=False), patch.object(be, 'roll_dice', return_value=4):
            en._end_turn('actor')
        self.assertEqual(en.get_char('actor').get_attr('魔力'), 14)

    def test_ultimate_one_round_chant_and_successful_payment(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            en = setup(cls)
            en._execute_spell('actor', 'actor', spell(en, 8))
            en._execute_spell('actor', 'enemy', spell(en, 10))
            chanting = next(e for e in en._get_effects() if e.get('type') == 'chanting')
            self.assertEqual(chanting['remainingRounds'], 1)
            self.assertTrue(chanting['stored_effects'][0].get('幻造'))
            en._tick_down()
            self.assertFalse(any(e.get('type') == 'chanting' for e in en._get_effects()))
            en._execute_spell('actor', 'actor', spell(en, 9))
            uid, ch = moon(en, 21)
            before = en.get_char('actor').get_attr('魔力')
            result = en._execute_spell('actor', 'enemy', spell(en, 12))
            self.assertTrue(be.action_committed(result))
            self.assertEqual(en.get_char('actor').get_attr('魔力'), before - 10)
            self.assertEqual(ch.get_attr('魔力'), 0)
            self.assertEqual(en._get_combat_hp(uid), 0)
            self.assertEqual(en._get_summon_quota('actor', '虚假之月')[0], 0)

    def test_pvp_volley_resumes_without_recharging_or_repeating_writing(self):
        with patch.object(logging.handlers, 'RotatingFileHandler', return_value=logging.NullHandler()):
            import battle_http_server as srv
        en = setup(srv.PvPFullBattleEngine)
        _, ch = moon(en)
        en._execute_spell('actor', 'actor', spell(en, 8))
        card = copy.deepcopy(spell(en, 6))
        card['effects'][0]['可闪避性'] = 1
        en._player_controllers = {'enemy': ['human']}
        before = en.get_char('actor').get_attr('魔力')
        state = None
        with patch.object(en, '_roll_phantom_bonus', return_value=(2, 'writing\n')) as writing:
            with patch.object(en, '_roll_spell_success_bp', return_value=(20, '')):
                try:
                    en._execute_spell('actor', 'enemy', card)
                except srv.ReactionNeeded as event:
                    state = copy.deepcopy(event.data)
                self.assertIsNotNone(state)
                self.assertIn('  消耗 2 MP', state['prefix_lines'])
                self.assertNotIn('  消耗 1 MP', state['prefix_lines'])
                for _ in range(3):
                    try:
                        en.resolve_spell_reaction(state, 'dodge')
                        break
                    except srv.ReactionNeeded as event:
                        state = copy.deepcopy(event.data)
                else:
                    self.fail('volley did not finish after three reactions')
            self.assertEqual(writing.call_count, 1)
        self.assertEqual(en.get_char('actor').get_attr('魔力'), before - 2)
        self.assertEqual(ch.get_attr('魔力'), 4)


if __name__ == '__main__':
    unittest.main()
