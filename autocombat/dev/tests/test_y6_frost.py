"""Deterministic frost state, combat and summon regressions."""
import copy
import json
import random
import unittest
from unittest.mock import patch

import battle_engine as be
import characters_data as cd
import ai_trainer as tr


def setup(cls=be.FullBattleEngine):
    random.seed(6)
    eng = cls()
    for uid, serial in [('snow', 'Y6'), ('enemy', 'Y1'), ('ally', 'Y3')]:
        data = copy.deepcopy(next(c for c in cd.ALL_CHARACTERS if c['serial'] == serial))
        data['spells'] = []  # Isolate frost from unrelated passive damage.
        data['inventory'] = []
        if serial == 'Y6':
            data['attrs']['冰冻跨循环叠加'] = 0
            data['attrs']['冰冻命中层数'] = 1
            data['attrs']['冰冻即时回复层数'] = 6
        cd.load_character_to_engine(eng, data, uid)
        eng.process_command(uid, '.hs')
        eng.get_char(uid).set_attr('体力上限', 10000)
    eng.setup_battle(['snow', 'ally'], ['enemy'], '12x12', season_status=50)
    for uid in ('snow', 'enemy', 'ally'):
        eng._set_combat_hp(uid, 5000)
    place(eng, {'snow': 'D4', 'enemy': 'E4', 'ally': 'C4'})
    return eng


def place(eng, coords):
    il = eng._get_initiative()
    for entry in il:
        if entry['userId'] in coords:
            entry['coord'] = coords[entry['userId']]
    eng._set_initiative(il)
    m = eng._get_map()
    m['occupants'] = {v: k for k, v in coords.items()}
    eng._set_map(m)


def frost(eng):
    return next((e for e in eng._get_effects() if e.get('type') == 'frost'), None)


def advance(eng):
    state = eng._get_state()
    state['round'] += 1
    eng._set_state(state)
    return eng._tick_down()


class FrostTests(unittest.TestCase):
    def setUp(self):
        self.patcher = patch.object(be, '_SUMMON_TEMPLATES', cd.SUMMON_TEMPLATES)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_original_hp_cap(self):
        self.assertLessEqual(cd.XUEREN['attrs']['体力'], 10)
        self.assertLessEqual(cd.XUEREN['attrs']['体力上限'], 10)
        self.assertEqual(cd.XUEREN['attrs']['等级'], 6)

    def test_configured_hit_stacks_shared_by_owner_and_summon(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            eng = setup(cls)
            eng.get_char('snow').set_attr('冰冻命中层数', cd.XUEREN['attrs']['冰冻命中层数'])
            sid = eng._create_summon('snow', '小雪人')
            eng._frost_on_hit('snow', 'enemy', '发送雪球')
            self.assertEqual(frost(eng)['stacks'], 1)
            eng._frost_on_hit(sid, 'enemy', '发送雪球')
            self.assertEqual(frost(eng)['stacks'], 3)
            eng._frost_on_hit('snow', 'enemy')
            self.assertEqual(frost(eng)['stacks'], 5)
            self.assertEqual(len([e for e in eng._get_effects() if e.get('type') == 'frostBurst']), 1)

    def test_two_snowballs_resolve_each_hit_independently(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            for dodges, expected in (((False, False), 2), ((True, False), 1), ((True, True), 0)):
                eng = setup(cls)
                data = copy.deepcopy(cd.XUEREN)
                data['spells'] = [next(s for s in data['spells'] if s['name'] == '发送雪球')]
                cd.load_character_to_engine(eng, data, 'snow')
                spell = eng.load_spells('snow')[0]
                self.assertEqual([e['伤害骰'] for e in spell['effects']], ['1d8+2', '1d8+2'])
                before_mp = eng.get_char('snow').get_attr('魔力')
                with patch.object(eng, '_roll_spell_success_bp', return_value=(20, '')) as success, \
                     patch.object(eng, '_trigger_spell_reaction', side_effect=[(d, False, []) for d in dodges]) as react, \
                     patch.object(eng, '_absorb_damage_with_shield', return_value=(0, 100, [])):
                    eng._execute_spell('snow', 'enemy', spell)
                self.assertEqual(react.call_count, 2)
                self.assertEqual(success.call_count, 2)
                self.assertEqual(eng.get_char('snow').get_attr('魔力'), before_mp - 2)
                self.assertEqual((frost(eng) or {}).get('stacks', 0), expected)

    def test_snowball_override_missing_keeps_legacy_hit_layers(self):
        eng = setup()
        eng.get_char('snow').attrs.pop('冰冻雪球命中层数', None)
        eng.get_char('snow').set_attr('冰冻命中层数', 2)
        eng._frost_on_hit('snow', 'enemy', '发送雪球')
        self.assertEqual(frost(eng)['stacks'], 2)

    def test_early_heal_once_per_cycle_and_regen_still_at_six(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            eng = setup(cls)
            eng.get_char('snow').set_attr('冰冻即时回复层数', 3)
            with patch.object(be, 'roll_dice', return_value=4):
                eng._add_frost('snow', 'enemy', 3)
                self.assertEqual(eng._hp_safe('snow'), 5004)
                self.assertFalse(any(e.get('type') == 'frostRegen' for e in eng._get_effects()))
                advance(eng)  # Decay to two, then cross three again.
                eng._add_frost('snow', 'enemy', 1)
                self.assertEqual(eng._hp_safe('snow'), 5004)
                eng._add_frost('snow', 'enemy', 3)
                self.assertEqual(eng._hp_safe('snow'), 5004)
                self.assertTrue(any(e.get('type') == 'frostRegen' for e in eng._get_effects()))
                eng._add_frost('snow', 'enemy', 5)  # Second cycle's third layer.
                self.assertEqual(eng._hp_safe('snow'), 5008)

    def test_early_heal_caps_hp_when_crossing_multiple_thresholds(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            eng = setup(cls)
            eng.get_char('snow').set_attr('冰冻即时回复层数', 3)
            eng.get_char('snow').set_attr('体力上限', 5002)
            with patch.object(be, 'roll_dice', return_value=20):
                eng._add_frost('snow', 'enemy', 8)
            self.assertEqual(eng._hp_safe('snow'), 5002)
            self.assertEqual(len([e for e in eng._get_effects() if e.get('type') == 'frostBurst']), 2)
            self.assertEqual(len([e for e in eng._get_effects() if e.get('type') == 'frostRegen']), 1)

    def test_legacy_card_without_heal_phase_keeps_sixth_layer_heal(self):
        eng = setup()
        eng.get_char('snow').attrs.pop('冰冻即时回复层数')
        with patch.object(be, 'roll_dice', return_value=4):
            eng._add_frost('snow', 'enemy', 3)
            self.assertEqual(eng._hp_safe('snow'), 5000)
            eng._add_frost('snow', 'enemy', 3)
            self.assertEqual(eng._hp_safe('snow'), 5004)

    def test_zone_ai_skips_empty_and_already_covered_area(self):
        eng = setup()
        cd.load_character_to_engine(eng, cd.XUEREN, 'snow')
        tr._ACTION_CACHE.clear()
        self.addCleanup(tr._ACTION_CACHE.clear)
        zone = next(s for s in eng.load_spells('snow') if s['name'] == '制造雪带')
        key = f'SKILL_{zone["index"]}__T0'
        self.assertIn(key, dict(tr.get_available_actions(eng, 'snow')))
        place(eng, {'snow': 'A1', 'enemy': 'L12', 'ally': 'C4'})
        self.assertNotIn(key, dict(tr.get_available_actions(eng, 'snow')))
        place(eng, {'snow': 'D4', 'enemy': 'E4', 'ally': 'C4'})
        eng._execute_misc_effect('snow', 'snow', zone['effects'][0], zone, False)
        self.assertNotIn(key, dict(tr.get_available_actions(eng, 'snow')))

    def test_enemy_snow_zone_does_not_block_own_cast(self):
        eng = setup()
        cd.load_character_to_engine(eng, cd.XUEREN, 'snow')
        tr._ACTION_CACHE.clear()
        self.addCleanup(tr._ACTION_CACHE.clear)
        zone = next(s for s in eng.load_spells('snow') if s['name'] == '制造雪带')
        eng._execute_misc_effect('enemy', 'enemy', zone['effects'][0], zone, False)
        self.assertIn(f'SKILL_{zone["index"]}__T0', dict(tr.get_available_actions(eng, 'snow')))

    def test_layer_table_both_engines(self):
        expected = [(1,0,0), (1,1,0), (1,1,1), (1,1,1),
                    (2,1,1), (2,1,1), (2,2,1), (0,1,1),
                    (1,1,1), (1,1,1), (1,1,1), (1,1,1),
                    (2,1,1), (2,1,1), (2,2,1), (0,1,1)]
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            eng = setup(cls)
            for n, want in enumerate(expected, 1):
                eng._add_frost('snow', 'enemy')
                self.assertEqual(eng._frost_components(frost(eng)), want, (cls, n))
                json.dumps(eng._get_effects())
            self.assertEqual(len([e for e in eng._get_effects() if e['type'] == 'frostBurst']), 4)
            self.assertEqual(next(e for e in eng._get_effects() if e['type'] == 'frostRegen')['activations'], [2])

    def test_stack_option_adds_retained_effects(self):
        eng = setup()
        eng.get_char('snow').set_attr('冰冻跨循环叠加', 1)
        eng._add_frost('snow', 'enemy', 16)
        self.assertEqual(eng._frost_components(frost(eng)), (0,2,2))
        self.assertEqual(next(e for e in eng._get_effects() if e['type'] == 'frostRegen')['activations'], [2,2])

    def test_decay_and_no_retrigger_until_zero(self):
        eng = setup()
        eng._add_frost('snow', 'enemy', 4)
        advance(eng)
        self.assertEqual(frost(eng)['stacks'], 3)
        self.assertEqual(eng._get_effective_mov('enemy'), 0)
        self.assertFalse(eng._can_move('enemy'))
        eng._add_frost('snow', 'enemy')
        self.assertEqual(len([e for e in eng._get_effects() if e['type'] == 'frostBurst']), 1)
        advance(eng)
        self.assertNotEqual(eng._get_effective_mov('enemy'), 0)
        for _ in range(3):
            advance(eng)
        self.assertIsNone(frost(eng))
        eng._add_frost('snow', 'enemy', 4)
        self.assertEqual(len([e for e in eng._get_effects() if e['type'] == 'frostBurst']), 1)

    def test_cleanup_does_not_return_on_decay(self):
        eng = setup()
        eng._add_frost('snow', 'enemy', 8)
        advance(eng)
        self.assertEqual(eng._frost_components(frost(eng)), (0,1,1))
        eng._add_frost('snow', 'enemy')
        self.assertEqual(len([e for e in eng._get_effects() if e['type'] == 'frostBurst']), 2)

    def test_decay_removes_slow_and_skill_penalty(self):
        eng = setup()
        base_mov = eng._get_effective_mov('enemy')
        eng._add_frost('snow', 'enemy', 3)
        self.assertEqual(eng._get_effective_mov('enemy'), max(0, base_mov - 5))
        self.assertEqual(eng._apply_buff_skill_mod('enemy', 60, '闪避'), 50)
        advance(eng)
        self.assertEqual(eng._get_effective_mov('enemy'), base_mov)
        advance(eng)
        self.assertEqual(eng._apply_buff_skill_mod('enemy', 60, '闪避'), 60)

    def test_regen_starts_next_round_and_survives_zero(self):
        eng = setup()
        eng.get_char('snow').set_attr('魔力', 0)
        with patch.object(be, 'roll_dice', return_value=4):
            eng._add_frost('snow', 'enemy', 6)
            self.assertEqual(eng._hp_safe('snow'), 5004)
            self.assertEqual(eng.get_char('snow').get_attr('魔力'), 0)
            advance(eng)
            self.assertEqual(eng._hp_safe('snow'), 5008)
            for _ in range(5):
                advance(eng)
            self.assertIsNone(frost(eng))
            self.assertEqual(eng._hp_safe('snow'), 5028)
            self.assertLessEqual(eng.get_char('snow').get_attr('魔力'), eng.get_char('snow').get_attr('魔力上限'))

    def test_snowfields_overlap_and_expire_exactly(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            eng = setup(cls)
            for i in range(2):
                eng._execute_misc_effect('snow', 'snow', {'type':8, '客体':45,
                    '作用半径':5, '持续回合':2, '冰冻每轮层数':2},
                    {'name':f'雪带{i}', 'index':i}, False)
            self.assertIsNone(frost(eng))
            advance(eng)
            self.assertEqual(frost(eng)['stacks'], 4)
            # Repeated processing in one round must not duplicate a pulse.
            eng._frost_round_start()
            self.assertEqual(frost(eng)['stacks'], 4)
            advance(eng)
            self.assertEqual(frost(eng)['stacks'], 7)
            advance(eng)
            self.assertEqual(frost(eng)['stacks'], 6)
            self.assertFalse(any(e.get('type') == 'frost' and e.get('targetUserId') == 'ally' for e in eng._get_effects()))

    def test_snowball_hit_after_damage_and_shield_absorption(self):
        eng = setup()
        with patch.object(eng, '_absorb_damage_with_shield', return_value=(0, 100, [])):
            eng._apply_spell_damage('snow', 'enemy', '1', 0, 0, spell_name='发送雪球')
        self.assertEqual(frost(eng)['stacks'], 1)
        self.assertEqual(eng._hp_safe('enemy'), 5000)
        self.assertAlmostEqual(eng._get_buff_dmg_mult('ally', 'enemy'), 1.18)
        self.assertAlmostEqual(eng._get_buff_dmg_mult('enemy', 'enemy'), 1)
        eng._apply_spell_damage('snow', 'enemy', '1', 0, 0, spell_name='雪之法杖')
        self.assertEqual(frost(eng)['stacks'], 1)

    def test_basic_hit_and_miss_both_engines(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            eng = setup(cls)
            eng._allow_failed_reaction = False
            attack = eng._fast_coc7_attack if cls == be.FastBattleEngine else eng._coc7_attack
            with patch.object(be, 'roll_d100', return_value=(100, '100')):
                attack('snow', 'enemy', '斗殴', 50, '1', 0, 0)
            self.assertIsNone(frost(eng))
            with patch.object(be, 'roll_d100', side_effect=[(20, '20'), (100, '100')]), patch.object(be.random, 'random', return_value=0):
                attack('snow', 'enemy', '斗殴', 50, '1', 0, 0)
            self.assertEqual(frost(eng)['stacks'], 1)

    def test_summon_zone_ball_and_quota(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            eng = setup(cls)
            sid = eng._create_summon('snow', '小雪人')
            self.assertEqual(eng._hp_safe(sid), 35)
            self.assertIsNone(eng._create_summon('snow', '小雪人'))
            self.assertEqual(tr.get_summon_actions(eng, sid)[0][0], 'SUMMON_SNOW')
            tr.execute_summon_action(eng, sid, 'SUMMON_SNOW')
            self.assertEqual(eng._get_summon_entry(sid)['summon_mp'], 9)
            self.assertTrue(any(e.get('frostStacks') for e in eng._get_effects()))
            with patch.object(eng, '_handle_spell_damage_effect', return_value='hit') as hit:
                tr.execute_summon_action(eng, sid, 'SUMMON_SNOW')
            hit.assert_called_once()
            self.assertEqual(eng._get_summon_entry(sid)['summon_mp'], 8)
            eng._add_frost(sid, 'enemy', 6)
            self.assertEqual(frost(eng)['sourceUserId'], 'snow')
            self.assertTrue(any(e.get('type') == 'frostRegen' and e['targetUserId'] == 'snow' for e in eng._get_effects()))

    def test_pvp_reaction_resume_and_dodge(self):
        import battle_http_server as http
        for reaction_roll, expected in ((100, 1), (1, 0)):
            eng = setup(http.PvPFullBattleEngine)
            with patch.object(http, 'roll_d100', return_value=(20, '20')):
                with self.assertRaises(http.ReactionNeeded) as paused:
                    eng._coc7_attack('snow', 'enemy', '斗殴', 50, '1', 0, 0)
            self.assertIsNone(frost(eng))
            with patch.object(http, 'roll_d100', return_value=(reaction_roll, str(reaction_roll))):
                eng.resolve_reaction(paused.exception.data, 'dodge')
            self.assertEqual((frost(eng) or {}).get('stacks', 0), expected)

    def test_snowball_dodged_never_adds_frost(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            eng = setup(cls)
            with patch.object(be, 'roll_d100', return_value=(20, '20')), patch.object(eng, '_trigger_spell_reaction', return_value=(True, False, [])):
                eng._handle_spell_damage_effect('snow', 'enemy', {'type':1, '伤害骰':'1',
                    '成功率':75, '可闪避性':1, '可反击性':0}, {'name':'发送雪球', 'index':3})
            self.assertIsNone(frost(eng))

    def test_delayed_burst_fires_once_and_does_not_cancel_at_zero(self):
        eng = setup()
        eng._add_frost('snow', 'enemy', 4)
        frost(eng)['stacks'] = 1
        with patch.object(be, 'roll_dice', return_value=10):
            advance(eng)
            self.assertEqual(eng._hp_safe('enemy'), 4990)
            self.assertIsNone(frost(eng))
            eng._frost_round_start()
            self.assertEqual(eng._hp_safe('enemy'), 4990)
            advance(eng)
            self.assertEqual(eng._hp_safe('enemy'), 4990)

    def test_summon_empty_mp_uses_basic_and_death_allows_replacement(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            eng = setup(cls)
            sid = eng._create_summon('snow', '小雪人')
            eng._set_summon_entry_fields(sid, summon_mp=0)
            attack_name = '_fast_coc7_attack' if cls == be.FastBattleEngine else '_coc7_attack'
            result = (sid, 'enemy', 1, 'hit') if cls == be.FastBattleEngine else (sid, 'enemy', ['hit'])
            with patch.object(eng, attack_name, return_value=result) as attack:
                eng._snow_summon_action(sid)
            attack.assert_called_once()
            self.assertEqual(attack.call_args.args[2:5], ('斗殴', 45, '1d4'))
            eng._set_combat_hp(sid, 0, source_dmg=1000)
            eng._reconcile_summon_groups()
            self.assertIsNotNone(eng._create_summon('snow', '小雪人'))

    def test_summon_melee_values_follow_template(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            eng = setup(cls)
            sid = eng._create_summon('snow', '小雪人')
            eng._set_summon_entry_fields(sid, summon_mp=0)
            template = copy.deepcopy(eng._snow_summon_template(sid))
            template['frost_melee'] = {'success': 83, 'dice': '3d7'}
            attack_name = '_fast_coc7_attack' if cls == be.FastBattleEngine else '_coc7_attack'
            result = (sid, 'enemy', 1, 'hit') if cls == be.FastBattleEngine else (sid, 'enemy', ['hit'])
            with patch.object(eng, '_snow_summon_template', return_value=template), \
                 patch.object(eng, attack_name, return_value=result) as attack:
                eng._snow_summon_action(sid)
            self.assertEqual(attack.call_args.args[2:5], ('斗殴', 83, '3d7'))

    def test_data_loading_and_new_battle_reset(self):
        eng = setup()
        cd.load_character_to_engine(eng, cd.XUEREN, 'snow')
        spells = eng.load_spells('snow')
        zone = next(s for s in spells if s['name'] == '制造雪带')
        self.assertEqual(len(zone['effects']), 1)
        self.assertEqual(zone['effects'][0]['冰冻每轮层数'], 2)
        self.assertEqual(next(s for s in spells if s['name'] == '堆雪人')['cooldown_rounds'], 4)
        eng._add_frost('snow', 'enemy', 8)
        eng.setup_battle(['snow'], ['enemy'], '12x12', season_status=50)
        self.assertFalse(any(e['type'].startswith('frost') for e in eng._get_effects()))


if __name__ == '__main__':
    unittest.main()
