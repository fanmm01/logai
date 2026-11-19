#!/usr/bin/env python3
"""Per-effect targeting / dice / attribute regression tests (real engine)."""
import copy
import random
import sys
import unittest

import battle_engine as be
import characters_data as cd

be._SUMMON_TEMPLATES = cd.SUMMON_TEMPLATES


def _char(serial):
    return next(c for c in cd.ALL_CHARACTERS if c['serial'] == serial)


def _setup(team_a, team_b):
    eng = be.FastBattleEngine()
    for uid, serial in list(team_a) + list(team_b):
        cd.load_character_to_engine(eng, _char(serial), uid)
        eng.process_command(uid, '.hs')
        eng.get_char(uid).set_attr('魔力', 999)
        eng.get_char(uid).set_attr('理智', 99)
    eng.setup_battle([u for u, _ in team_a], [u for u, _ in team_b], '14x14', season_status=50)
    return eng


def _place(eng, positions):
    """positions: {coord: uid}. Sets both map occupants and initiative coords."""
    mp = eng._get_map()
    mp['occupants'] = dict(positions)
    eng._set_map(mp)
    il = eng._get_initiative()
    coord_of = {uid: coord for coord, uid in positions.items()}
    for e in il:
        if e['userId'] in coord_of:
            e['coord'] = coord_of[e['userId']]
    eng._set_initiative(il)


def _clear_buffs(eng, keep_spell=None):
    """Drop passive buffs so a test only sees the spell under test."""
    effects = eng._get_effects()
    for e in effects:
        if e.get('type') in ('buff', 'debuff') and e.get('spellName') != keep_spell:
            e['remainingRounds'] = 0
    eng._set_effects(effects)


class BpTests(unittest.TestCase):
    def test_token_parse(self):
        self.assertEqual(be._parse_bp_token('b'), 1)
        self.assertEqual(be._parse_bp_token('b2'), 2)
        self.assertEqual(be._parse_bp_token('2b'), 2)
        self.assertEqual(be._parse_bp_token('p'), -1)
        self.assertEqual(be._parse_bp_token('p3'), -3)
        self.assertEqual(be._parse_bp_token('3p'), -3)
        self.assertEqual(be._parse_bp_token(''), 0)
        self.assertEqual(be._parse_bp_token(None), 0)
        self.assertEqual(be._parse_bp_token('xx'), 0)

    def test_net_cancellation(self):
        cases = [
            (['p', 'p'], 'p2'),
            (['p', 'b'], ''),
            (['p2', 'b'], 'p'),
            (['b2', 'p'], 'b'),
            (['b'], 'b'),
            (['b5', 'p3'], 'b2'),
            (['b9'], 'b3'),
            (['p9'], 'p3'),
        ]
        for tokens, expected in cases:
            self.assertEqual(be._calc_net_bp([], '', None, tokens), expected, tokens)

    def test_aux16_and_aux18(self):
        buffs = [
            {'auxCode': 16, 'auxVal': 'b', 'auxType': '伤害成功率奖励惩罚'},
            {'auxCode': 18, 'auxVal': 'p', 'auxType': '技能惩罚骰'},
            {'auxCode': 18, 'auxVal': 'p2', 'auxType': '技能惩罚骰'},
            {'auxCode': 18, 'auxVal': '斗殴', 'auxType': '技能惩罚骰'},
        ]
        # 1 bonus -1 -2 = -2; 技能名值只在斗殴时生效
        self.assertEqual(be._calc_net_bp(buffs, '', '闪避'), 'p2')
        self.assertEqual(be._calc_net_bp(buffs, '', '斗殴'), 'p3')

    def test_no_double_count_code_and_type(self):
        b = {'auxCode': 18, 'auxVal': 'p', 'auxType': '技能惩罚骰'}
        self.assertEqual(be._calc_net_bp([b], '', None), 'p')

    def test_ignore_global(self):
        buffs = [{'auxCode': 16, 'auxVal': 'b'}]
        self.assertEqual(be._calc_net_bp(buffs, '', None, ['p']), '')
        self.assertEqual(be._calc_net_bp(buffs, '', None, ['p'], ignore_global=True), 'p')


class AttrTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(be._parse_attr_mods('MOV+5'), [('MOV', 5)])
        self.assertEqual(be._parse_attr_mods('闪避-30;斗殴+10'),
                         [('闪避', -30), ('斗殴', 10)])

    def test_mov_group(self):
        self.assertEqual(be._attr_group('行动力'), 'MOV')
        self.assertEqual(be._attr_group('移动力'), 'MOV')
        self.assertEqual(be._attr_group('闪避'), '闪避')


class TargetResolutionTests(unittest.TestCase):
    def test_card_loader_preserves_random_target_range_and_duration_without_rolling(self):
        eng = be.FullBattleEngine()
        data = dict(name='加载测试', serial='Z99', attrs={'体力': 10, '魔力': 10}, str_attrs={'伤害值': '1d6'},
                    spells=[dict(name='随机攻击', category=1, timing='2', effects=[
                        {'type': 1, '客体': 'RP', '射程': 7, '持续回合': '2d3', '伤害骰': '1d6', '成功率': 70}])])
        cd.load_character_to_engine(eng, data, 'loader')
        state = random.getstate()
        effect = eng.load_spells('loader')[0]['effects'][0]
        self.assertEqual(random.getstate(), state)
        self.assertEqual(effect['客体'], 'RP')
        self.assertEqual(effect['射程'], 7)
        self.assertEqual(effect['持续回合'], '2d3')

    def setUp(self):
        self.eng = _setup([('LAN', 'Y11'), ('ALLY', 'Y3')], [('E1', 'Y1')])

    def test_mixed_spell_targets_independently(self):
        spell = {'name': '混合', 'index': 90, '消耗mp': 0, 'effects': [
            {'type': 4, '客体': 5, '持续回合': 2, '辅助效果': '伤害成功率奖励惩罚', '辅助效果值': 'p'},
            {'type': 4, '客体': 1, '持续回合': 2, '辅助效果': '伤害成功率奖励惩罚', '辅助效果值': 'b'},
            {'type': 4, '客体': 1, '持续回合': 2, '技能加减值': 'MOV+5'},
        ]}
        out = self.eng._execute_spell('LAN', 'E1', spell)
        self.assertIn('施加辅助效果', out)
        enemy_buffs = self.eng._get_active_buffs('E1')
        # spellName check to ignore passives
        mixed = [b for b in enemy_buffs if b.get('spellName') == '混合']
        self.assertEqual(len(mixed), 1)
        self.assertEqual(mixed[0]['auxVal'], 'p')
        self.assertEqual(mixed[0]['targetUserId'], 'E1')
        lan_buffs = [b for b in self.eng._get_active_buffs('LAN') if b.get('spellName') == '混合']
        self.assertEqual(len(lan_buffs), 2)
        values = sorted((b.get('auxVal') or b.get('skillMod')) for b in lan_buffs)
        self.assertEqual(values, ['MOV+5', 'b'])
        # MOV+5 must not leak into other skills
        c = self.eng.get_char('LAN')
        self.assertEqual(self.eng._apply_buff_skill_mod('LAN', c.get_attr('斗殴'), '斗殴'),
                         c.get_attr('斗殴'))
        self.assertGreater(self.eng._get_effective_mov('LAN'), c.get_attr('行动力'))

    def test_ally_group_excludes_self(self):
        spell = {'name': '群体护盾', 'index': 91, '消耗mp': 0, 'effects': [
            {'type': 2, '客体': 3, '持续回合': 2, '护盾值': '5'}]}
        self.eng._execute_spell('LAN', 'LAN', spell)
        shields = [e for e in self.eng._get_effects() if e.get('type') == 'shield']
        self.assertEqual([e['targetUserId'] for e in shields], ['ALLY'])

    def test_self_plus_ally_group(self):
        spell = {'name': '自身加友方', 'index': 92, '消耗mp': 0, 'effects': [
            {'type': 2, '客体': 13, '持续回合': 2, '护盾值': '5'}]}
        self.eng._execute_spell('LAN', 'LAN', spell)
        shields = [e for e in self.eng._get_effects() if e.get('type') == 'shield']
        self.assertEqual(sorted(e['targetUserId'] for e in shields), ['ALLY', 'LAN'])

    def test_enemy_group_excludes_ally(self):
        spell = {'name': '敌全体', 'index': 93, '消耗mp': 0, 'effects': [
            {'type': 4, '客体': 5, '持续回合': 2, '辅助效果': '伤害成功率奖励惩罚', '辅助效果值': 'p'}]}
        self.eng._execute_spell('LAN', 'E1', spell)
        tids = {b['targetUserId'] for b in self.eng._get_effects()
                if b.get('spellName') == '敌全体'}
        self.assertEqual(tids, {'E1'})


class LanFogTests(unittest.TestCase):
    def setUp(self):
        random.seed(3)
        self.eng = _setup([('LAN', 'Y11'), ('ALLY', 'Y3')], [('E1', 'Y1')])
        _clear_buffs(self.eng)
        _place(self.eng, {'B6': 'LAN', 'C6': 'ALLY', 'E6': 'E1'})

    def test_fog_and_scythe(self):
        eng = self.eng
        fog = next(s for s in eng.load_spells('LAN') if s['name'] == '黑雾笼罩')
        eng._execute_spell('LAN', 'E1', fog)
        lan = eng.get_char('LAN')
        e1 = eng.get_char('E1')
        # The authoritative base data grants LAN a bonus, but does not
        # declare the old PvP enemy penalty effect (it is commented out).
        self.assertEqual(be._calc_net_bp(eng._get_active_buffs('LAN'), '', '挥动镰刀'), 'b')
        self.assertEqual(be._calc_net_bp(eng._get_active_buffs('E1'), '', '闪避'), '')
        # zone 属性削减 applies to enemies only, by name
        eng._apply_zone_effects()
        self.assertEqual(eng._apply_buff_skill_mod('E1', e1.get_attr('闪避'), '闪避'),
                         max(1, e1.get_attr('闪避') - 20))
        self.assertEqual(eng._apply_buff_skill_mod('E1', e1.get_attr('斗殴'), '斗殴'),
                         e1.get_attr('斗殴'))
        self.assertEqual(eng._apply_buff_skill_mod('ALLY', eng.get_char('ALLY').get_attr('闪避'), '闪避'),
                         eng.get_char('ALLY').get_attr('闪避'))
        # MOV+5 only on LAN
        self.assertGreater(eng._get_effective_mov('LAN'), lan.get_attr('行动力'))
        self.assertEqual(eng._apply_buff_skill_mod('LAN', lan.get_attr('闪避'), '闪避'),
                         lan.get_attr('闪避'))

    def test_scythe_reaction_dice(self):
        sc = next(s for s in self.eng.load_spells('LAN') if s['name'] == '挥动镰刀')
        eff = sc['effects'][0]
        self.assertEqual(eff.get('辅助效果'), '反应惩罚骰')
        self.assertEqual(self.eng._spell_reaction_extra(eff), ['p'])
        self.assertEqual(self.eng._calc_spell_reaction_bp('E1', eff, '闪避'), 'p')
        # dodge/counter/block all see it
        for skill in ('闪避', '斗殴', '格挡'):
            self.assertIn(self.eng._calc_spell_reaction_bp('E1', eff, skill), ('p', 'p2'))

    def test_fog_does_not_persist_reaction_penalty(self):
        eng = self.eng
        fog = next(s for s in eng.load_spells('LAN') if s['name'] == '黑雾笼罩')
        eng._execute_spell('LAN', 'E1', fog)
        buffs = eng._get_active_buffs('E1')
        self.assertFalse(any(b.get('auxType') == '反应惩罚骰' for b in buffs))


class IgnoreGlobalTests(unittest.TestCase):
    def test_ignore_flag_only_affects_that_effect(self):
        eng = _setup([('LAN', 'Y11')], [('E1', 'Y1')])
        eng._execute_spell('LAN', 'LAN', {'name': '奖励', 'index': 95, '消耗mp': 0, 'effects': [
            {'type': 4, '客体': 1, '持续回合': 3, '辅助效果': '伤害成功率奖励惩罚', '辅助效果值': 'b2'}]})
        calls = []
        orig = be.roll_d100
        try:
            def fake(bp=''):
                calls.append(bp)
                return orig('')
            be.roll_d100 = fake
            spell = {'name': '双段', 'index': 96, '消耗mp': 0, 'effects': [
                {'type': 1, '客体': 4, '伤害骰': '1', '成功率': 50, '可闪避性': 0, '可反击性': 0,
                 '射程': 99, '成功率奖惩骰': 'p', '忽略全局奖惩骰': 1},
                {'type': 1, '客体': 4, '伤害骰': '1', '成功率': 50, '可闪避性': 0, '可反击性': 0,
                 '射程': 99},
            ]}
            eng._execute_spell('LAN', 'E1', spell)
        finally:
            be.roll_d100 = orig
        # first effect: only inherent p; second: b2 buff + no inherent → b2
        self.assertIn('p', calls)
        self.assertIn('b2', calls)


class ZoneAuraTests(unittest.TestCase):
    """type=8 zones carrying AUX / 技能加减值 (live range membership)."""

    def setUp(self):
        random.seed(11)
        self.eng = _setup([('LAN', 'Y11'), ('ALLY', 'Y3')], [('E1', 'Y1')])
        _clear_buffs(self.eng)
        _place(self.eng, {'F6': 'LAN', 'G6': 'ALLY', 'H6': 'E1'})

    def _zone(self, name, obj=1, radius=5, dur=3, **extra):
        eff = {'type': 8, '客体': obj, '作用半径': radius, '持续回合': dur,
               '领域中心跟随': 0}
        eff.update(extra)
        return {'name': name, 'index': 90, 'effects': [eff]}

    def _bp(self, uid, skill='挥动镰刀'):
        return be._calc_net_bp(self.eng._get_active_buffs(uid), '', skill)

    def test_zone_aura_is_live_and_not_a_stored_buff(self):
        n_effects = len(self.eng._get_effects())
        self.eng._execute_spell('LAN', 'LAN', self._zone(
            '奖励领域', 辅助效果='伤害成功率奖励惩罚', 辅助效果值='b',
            技能加减值='MOV+5;闪避+10'))
        self.assertEqual(self._bp('LAN'), 'b')
        base_dodge = self.eng.get_char('LAN').get_attr('闪避')
        self.assertEqual(self.eng._apply_buff_skill_mod('LAN', base_dodge, '闪避'),
                         base_dodge + 10)
        self.assertEqual(len(self.eng._get_effects()), n_effects + 1)
        # No extra physical buff grows over repeated queries or round ticks.
        for _ in range(2):
            self.assertEqual(self._bp('LAN'), 'b')
        self.eng._tick_down(); self.eng._apply_zone_effects()
        self.assertEqual(self._bp('LAN'), 'b')
        self.eng._tick_down(); self.eng._apply_zone_effects()
        self.assertEqual(self._bp('LAN'), 'b')
        self.eng._tick_down(); self.eng._apply_zone_effects()
        self.assertEqual(self._bp('LAN'), '')
        self.assertEqual(len(self.eng._get_effects()), n_effects)

    def test_zone_aura_follows_current_membership_without_tick(self):
        self.eng._execute_spell('LAN', 'LAN', self._zone(
            '队友增益', obj=3, radius=5, 领域中心跟随=1,
            辅助效果='伤害成功率奖励惩罚', 辅助效果值='b'))
        self.assertEqual(self._bp('ALLY', '闪避'), 'b')
        self.assertEqual(self._bp('LAN', '闪避'), '')
        self.assertEqual(self._bp('E1', '闪避'), '')
        _place(self.eng, {'F6': 'LAN', 'L10': 'ALLY', 'H6': 'E1'})
        self.assertEqual(self._bp('ALLY', '闪避'), '')
        _place(self.eng, {'K10': 'LAN', 'L10': 'ALLY', 'H6': 'E1'})
        self.assertEqual(self._bp('ALLY', '闪避'), 'b')

    def test_zone_aura_stacking_identity(self):
        def cast(caster, name, idx, stack=False):
            eff = {'type': 8, '客体': 1, '作用半径': 8, '持续回合': 3,
                   '辅助效果': '伤害成功率奖励惩罚', '辅助效果值': 'b'}
            if stack:
                eff['可叠加'] = 1
            self.eng._execute_spell(caster, caster,
                                    {'name': name, 'index': idx, 'effects': [eff]})

        cast('LAN', '同名', 90)
        cast('ALLY', '同名', 91)
        self.assertEqual(self._bp('LAN'), 'b')
        cast('LAN', '异名', 92)
        self.assertEqual(self._bp('LAN'), 'b2')
        cast('LAN', '同名', 93, stack=True)
        self.assertEqual(self._bp('LAN'), 'b3')

    def test_same_spell_two_zone_effects_do_not_collapse(self):
        spell = {'name': '双领域', 'index': 99, 'effects': [
            {'type': 8, 'letter': 'a', '客体': 1, '作用半径': 5,
             '持续回合': 3, '辅助效果': '伤害成功率奖励惩罚', '辅助效果值': 'b'},
            {'type': 8, 'letter': 'b', '客体': 1, '作用半径': 5,
             '持续回合': 3, '辅助效果': '伤害成功率奖励惩罚', '辅助效果值': 'b'},
        ]}
        self.eng._execute_spell('LAN', 'LAN', spell)
        self.assertEqual(self._bp('LAN'), 'b2')

    def test_unsupported_zone_aux_is_refused_but_skill_mod_survives(self):
        out = self.eng._execute_spell('LAN', 'LAN', self._zone(
            '免疫领域', 辅助效果='免疫一次伤害', 辅助效果值='1',
            技能加减值='闪避+5'))
        self.assertIn('不生效', out)
        self.assertFalse(any(b.get('auxType') == '免疫一次伤害'
                             for b in self.eng._get_active_buffs('LAN')))
        self.assertEqual(self.eng._apply_buff_attr_mod('LAN', '闪避'), 5)

    def test_zone_aura_is_seen_after_raw_state_restore(self):
        # Raw restores (e.g. resuming a saved battle) bypass _set_effects.
        effects = [e for e in self.eng._get_effects()
                   if e.get('type') not in ('buff', 'debuff')]
        effects.append({'type': 'zone', 'center': 'F6', 'radius': 5,
                        'remainingRounds': 2, 'filter': 1, 'sourceUserId': 'LAN',
                        'spellName': '恢复领域', 'spellIndex': 90, 'stackable': 0,
                        'auxType': '伤害成功率奖励惩罚', 'auxVal': 'b',
                        'auxCode': 16, 'effectKey': 'a', 'skillMod': ''})
        self.eng.set_json(f'combat_effects_{self.eng.group_id}', effects)
        self.assertEqual(self._bp('LAN'), 'b')

    def test_ally_safe_from_card_data_spares_allies(self):
        eng = _setup([('LAN', 'Y11'), ('ALLY', 'Y3')], [('E1', 'Y1')])
        _clear_buffs(eng)
        _place(eng, {'F6': 'LAN', 'G6': 'ALLY', 'H6': 'E1'})
        spell = {'name': '安全领域', 'index': 99, 'effects': [{
            'type': 8, '客体': 35, '作用半径': 5, '持续回合': 3,
            '每回合伤害骰': '4', '领域中心跟随': 0, 'ally_safe': 1}]}
        eng._execute_spell('LAN', 'LAN', spell)
        before = {u: eng._get_combat_hp(u) for u in ('LAN', 'ALLY', 'E1')}
        eng._apply_zone_effects()
        after = {u: eng._get_combat_hp(u) for u in ('LAN', 'ALLY', 'E1')}
        self.assertEqual(before['LAN'] - after['LAN'], 0)
        self.assertEqual(before['ALLY'] - after['ALLY'], 0)
        self.assertEqual(before['E1'] - after['E1'], 4)

    def test_hidden_summon_member_gets_aura_by_team(self):
        eng = _setup([('LAN', 'Y11')], [('E1', 'Y1')])
        _clear_buffs(eng)
        _place(eng, {'F6': 'LAN', 'H6': 'E1'})
        eng._summon_members['SM1'] = {
            'userId': 'SM1', 'name': '使魔', 'display_name': '使魔',
            'team': 'Y', 'coord': 'F6', 'ownerId': 'LAN', 'skills': []}
        spell = {'name': '友方光环', 'index': 90, 'effects': [{
            'type': 8, '客体': 3, '作用半径': 5, '持续回合': 3,
            '辅助效果': '伤害成功率奖励惩罚', '辅助效果值': 'b'}]}
        eng._execute_spell('LAN', 'LAN', spell)
        self.assertEqual(len(eng._zone_buff_views('SM1')), 1)
        self.assertEqual(eng._zone_buff_views('E1'), [])


class ZoneDamageFilterTests(unittest.TestCase):
    """Zone tick damage must honor 客体; filtered units don't consume dedup."""

    EXPECT = {
        # 客体: (LAN self dmg, ALLY ally dmg, E1 enemy dmg)
        1: (4, 0, 0),
        3: (0, 4, 0),
        13: (4, 4, 0),
        5: (0, 0, 4),
        45: (0, 0, 4),
        35: (4, 4, 4),
        135: (4, 4, 4),
    }

    def _run(self, obj, extra=None):
        random.seed(7)
        eng = _setup([('LAN', 'Y11'), ('ALLY', 'Y3')], [('E1', 'Y1')])
        _clear_buffs(eng)
        _place(eng, {'F6': 'LAN', 'G6': 'ALLY', 'H6': 'E1'})
        eff = {'type': 8, '客体': obj, '作用半径': 5, '持续回合': 3,
               '每回合伤害骰': '4', '领域中心跟随': 0}
        if extra:
            eff.update(extra)
        eng._execute_spell('LAN', 'LAN',
                           {'name': f'伤害领域{obj}', 'index': 90, 'effects': [eff]})
        before = {u: eng._get_combat_hp(u) for u in ('LAN', 'ALLY', 'E1')}
        eng._apply_zone_effects()
        after = {u: eng._get_combat_hp(u) for u in ('LAN', 'ALLY', 'E1')}
        return tuple(before[u] - after[u] for u in ('LAN', 'ALLY', 'E1'))

    def test_damage_filter_matrix(self):
        for obj, expected in self.EXPECT.items():
            self.assertEqual(self._run(obj), expected, f'客体={obj}')

    def test_filtered_unit_does_not_consume_dedup(self):
        # Same-name zones: first hits enemies only, second hits everyone.
        random.seed(7)
        eng = _setup([('LAN', 'Y11'), ('ALLY', 'Y3')], [('E1', 'Y1')])
        _clear_buffs(eng)
        _place(eng, {'F6': 'LAN', 'G6': 'ALLY', 'H6': 'E1'})
        for i, obj in enumerate((45, 35)):
            eng._execute_spell('LAN', 'LAN', {'name': '同名领域', 'index': 90 + i,
                'effects': [{'type': 8, '客体': obj, '作用半径': 5, '持续回合': 3,
                             '每回合伤害骰': '4', '领域中心跟随': 0}]})
        before = {u: eng._get_combat_hp(u) for u in ('LAN', 'ALLY', 'E1')}
        eng._apply_zone_effects()
        after = {u: eng._get_combat_hp(u) for u in ('LAN', 'ALLY', 'E1')}
        self.assertEqual(before['E1'] - after['E1'], 4)
        self.assertEqual(before['LAN'] - after['LAN'], 4)
        self.assertEqual(before['ALLY'] - after['ALLY'], 4)


class ZoneActionBudgetTests(unittest.TestCase):
    def test_zone_mov_is_live_but_does_not_reissue_actions(self):
        random.seed(5)
        eng = _setup([('LAN', 'Y11')], [('E1', 'Y1')])
        _clear_buffs(eng)
        _place(eng, {'F6': 'LAN', 'H6': 'E1'})
        before = eng._get_actions()['LAN']['主动']
        base_mov = eng._get_effective_mov('LAN')
        eng._execute_spell('LAN', 'LAN', {'name': 'MOV领域', 'index': 90, 'effects': [
            {'type': 8, '客体': 1, '作用半径': 5, '持续回合': 3, '技能加减值': 'MOV+5'}]})
        self.assertGreater(eng._get_effective_mov('LAN'), base_mov)
        self.assertEqual(eng._get_actions()['LAN']['主动'], before)
        # An unrelated cast later must not retroactively award zone actions.
        eng._execute_spell('LAN', 'LAN', {'name': '普通buff', 'index': 91, 'effects': [
            {'type': 4, '客体': 1, '持续回合': 2, '技能加减值': '斗殴+5'}]})
        self.assertEqual(eng._get_actions()['LAN']['主动'], before)


class PvpPlanTests(unittest.TestCase):
    def test_reaction_penalty_merges_with_fog(self):
        import battle_http_server as srv
        random.seed(5)
        eng = srv.PvPFullBattleEngine()
        for ser, u in (('Y11', 'LAN'), ('Y1', 'E1'), ('Y3', 'ALLY')):
            cd.load_character_to_engine(eng, _char(ser), u)
            eng.process_command(u, '.hs')
        eng.setup_battle(['LAN', 'ALLY'], ['E1'], '12x12', season_status=50)
        eng.get_char('LAN').set_attr('魔力', 99)
        eng._player_controllers = {'E1': ['p1']}
        _clear_buffs(eng)
        _place(eng, {'B6': 'LAN', 'C6': 'ALLY', 'E6': 'E1'})
        fog = next(s for s in eng.load_spells('LAN') if s['name'] == '黑雾笼罩')
        eng._execute_spell('LAN', 'E1', fog)
        sc = next(s for s in eng.load_spells('LAN') if s['name'] == '挥动镰刀')
        with self.assertRaises(srv.ReactionNeeded) as ctx:
            eng._execute_spell('LAN', 'E1', sc)
        state = copy.deepcopy(ctx.exception.data)
        # Base fog has no enemy penalty; the scythe contributes exactly one.
        self.assertEqual(eng._calc_spell_reaction_bp('E1', sc['effects'][0], '闪避'), 'p')
        eng._manual_reaction_bp = 'p'
        self.assertEqual(eng._calc_spell_reaction_bp('E1', sc['effects'][0], '闪避'), 'p2')
        eng._manual_reaction_bp = ''
        mp_before = eng.get_char('LAN').get_attr('魔力')
        eng.resolve_spell_reaction(state, 'dodge')
        self.assertEqual(eng.get_char('LAN').get_attr('魔力'), mp_before)

    def test_multi_effect_plan_resumes(self):
        import battle_http_server as srv
        random.seed(5)
        eng = srv.PvPFullBattleEngine()
        for ser, u in (('Y11', 'LAN'), ('Y1', 'E1'), ('Y3', 'ALLY')):
            cd.load_character_to_engine(eng, _char(ser), u)
            eng.process_command(u, '.hs')
        eng.setup_battle(['LAN', 'ALLY'], ['E1'], '12x12', season_status=50)
        eng.get_char('LAN').set_attr('魔力', 99)
        eng._player_controllers = {'E1': ['p1']}
        spell = {'name': '连段', 'index': 97, '消耗mp': 1, 'effects': [
            {'type': 1, '客体': 4, '伤害骰': '1d6', '成功率': 100, '可闪避性': 1,
             '可反击性': 1, '射程': 99},
            {'type': 4, '客体': 1, '持续回合': 2, '辅助效果': '伤害成功率奖励惩罚', '辅助效果值': 'b'},
            {'type': 4, '客体': 5, '持续回合': 2, '辅助效果': '伤害成功率奖励惩罚', '辅助效果值': 'p'},
        ]}
        try:
            eng._execute_spell('LAN', 'E1', spell)
            self.fail('expected ReactionNeeded')
        except srv.ReactionNeeded as e:
            state = copy.deepcopy(e.data)
        self.assertEqual(state['spell_plan'], [[1, 'LAN'], [2, 'E1']])
        mp = eng.get_char('LAN').get_attr('魔力')
        eng.resolve_spell_reaction(state, 'dodge')
        # remaining effects applied exactly once, no extra MP
        self.assertEqual(eng.get_char('LAN').get_attr('魔力'), mp)
        self.assertTrue(any(b.get('auxType') == '伤害成功率奖励惩罚' and b.get('auxVal') == 'b'
                            for b in eng._get_active_buffs('LAN')))
        self.assertTrue(any(b.get('auxType') == '伤害成功率奖励惩罚' and b.get('auxVal') == 'p'
                            for b in eng._get_active_buffs('E1')))


if __name__ == '__main__':
    unittest.main()
