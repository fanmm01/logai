"""Character constraints and battle-spirit protagonist lifecycle regressions."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import copy

import battle_engine as be
import characters_data as cd


def setup(cls, healers=1, initial=5):
    eng = cls()
    allies = [f'healer{i}' for i in range(healers)]
    for uid, data in [(u, cd.HUANHUANUAN) for u in allies] + [('enemy', cd.LAN)]:
        cd.load_character_to_engine(eng, data, uid)
        eng.process_command(uid, '.hs')
    eng.setup_battle(allies, ['enemy'], '10x10', season_status=50)
    if initial is not None:
        effects = eng._get_effects()
        for eff in effects:
            if eff.get('type') == 'battleSpirit':
                eff['战意主体初始层数'] = initial
        eng._set_effects(effects)
    return eng


class FiveBalanceTests(unittest.TestCase):
    def test_character_spirit_configuration_survives_card_loading(self):
        eng = setup(be.FullBattleEngine, initial=None)
        effect = next(e for e in eng._get_effects() if e.get('type') == 'battleSpirit')
        declared = cd.HUANHUANUAN['spells'][0]['effects'][0]
        for key, value in declared.items():
            if key.startswith('战意'):
                self.assertEqual(effect[key], value, key)

    def test_attributes_and_unselected_characters_unchanged(self):
        path = Path(__file__).resolve().parents[2] / 'archive/artifacts/balance_five/before_source/characters_data.py'
        spec = importlib.util.spec_from_file_location('balance_before', path)
        before = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(before)
        originals = {c['serial']: copy.deepcopy(c) for c in before.ALL_CHARACTERS}
        # Y60 preserves the old Y6 card, with only its identity fields changed.
        originals['Y60'] = copy.deepcopy(originals['Y6'])
        originals['Y60']['serial'] = 'Y60'
        originals['Y60']['attrs']['魔法少女序号'] = 60
        # This global fee revision also applies to the historical balance roster.
        for card in originals.values():
            for spell in card['spells']:
                if not spell.get('消耗mp'):
                    spell['消耗mp'] = 1

        # Approved character changes made after the frozen balance snapshot.
        originals['Y6']['attrs'].update({
            '体力': 10, '体力上限': 10, '冰冻命中层数': 2,
            '冰冻雪球命中层数': 1, '冰冻跨循环叠加': 1, '冰冻即时回复层数': 3,
        })
        originals['Y6'].update({
            'name': '雪人v2',
            'passive_rules': {
                'frost': {'vulnerability_pct': 18, 'skill_penalty': 10, 'dodge_penalty': 20,
                          'move_penalty': 5, 'decay': 1, 'dot': '1d4+2',
                          'burst': '1d10+1d6+2', 'instant_heal': '2d10',
                          'regen_hp': '1d6+2', 'regen_mp': '1d10'},
            },
        })
        originals['Y6']['spells'][0]['effects'] = [{
            'type': 8, '作用半径': 5, '冰冻每轮层数': 2, '客体': 45, '持续回合': 6,
            '领域中心跟随': 0, '领域同时上限': 1,
        }]
        originals['Y6']['spells'][2]['effects'] = [
            {'type': 1, '伤害骰': '1d8+2', '可反击性': 0, '可闪避性': 1,
             '客体': 4, '射程': 99, '成功率': 80},
            {'type': 1, '伤害骰': '1d8+2', '可反击性': 0, '可闪避性': 1,
             '客体': 4, '射程': 99, '成功率': 80},
        ]
        originals['Y6']['spells'].extend([
            {'category': 4, 'default_persist': 1,
             'effects': [{'type': 4, '客体': 13, '持续回合': 5, '默认延续性': 0,
                          '辅助效果': '伤害骰加值', '辅助效果值': '+1d6'},
                         {'type': 2, '客体': 13, '护盾值': '1d8+4', '持续回合': 5}],
             'name': '雪之法杖', 'timing': '1', '消耗mp': 1},
            {'category': 5, 'cooldown_rounds': 4,
             'effects': [{'type': 5, '召唤个数': 1, '召唤物模板': '小雪人',
                          '客体': 1, '持续回合': 999}],
             'name': '堆雪人', 'timing': '2', '消耗mp': 5},
        ])
        originals['Y6']['str_attrs']['伤害值'] = '1d6+1d4'

        originals['Y7']['spells'][0]['effects'][0].update({
            '战意主体减伤百分比': 30, '战意主体减伤阈值': 1,
            '战意主体初始层数': 4, '战意主体削弱概率': 65,
            '战意主体沉默阈值': 0, '战意影响主体': 1,
        })
        originals['Y9']['attrs'].update({'体力': 10, '体力上限': 10})
        originals['Y9']['spells'][1]['effects'][0].update(
            {'伤害骰': '4d6+7', '吸血比例': '0.25', '成功率': 85})
        originals['Y9']['spells'][5]['effects'][0].update(
            {'伤害骰': '2d8+4', '吸血比例': '0.25', '成功率': 85})
        originals['Y10']['attrs'].update({'体力': 11, '体力上限': 11})
        originals['Y10']['spells'][0]['effects'][0]['护盾值'] = '3d6+4'
        originals['Y10']['spells'][0]['消耗mp'] = 8
        originals['Y11']['spells'][0]['effects'][0].update(
            {'伤害骰': '1d10+1d8+6', '吸血比例': '0.65'})
        originals['Y11']['spells'].append({
            'category': 4, 'default_persist': 1,
            'effects': [{'type': 4, '客体': 1, '持续回合': 999,
                         '辅助效果': '伤害骰加值', '辅助效果值': '+2d3'}],
            'name': '镰刃余势', 'timing': '1', '消耗mp': 1,
        })
        originals['Y12']['spells'][1]['effects'][0]['伤害骰'] = '2d6'
        originals['Y13']['attrs']['飞行'] = 1
        originals['Y13']['spells'][1]['effects'].pop()
        originals['Y14']['passive_rules'] = {
            'cartridge': {'cooldown': 3, 'ring_cap': 3, 'limit_cap': 5,
                          'event_gain': 1, 'dash_distance': 8}}
        originals['Y14']['spells'][19]['effects'][1]['客体'] = 135
        originals['Y15']['attrs'].update({'体力': 11, '体力上限': 11})
        originals['Y15']['spells'][0]['effects'][0]['伤害骰'] = '1d10+1d6+2'
        originals['Y15']['spells'][2]['effects'].pop()
        originals['Y15']['passive_rules'] = {
            'cloak': {'mp_per_hit': 1, 'immune_max': 3, 'middle_below': 7,
                      'middle_damage': 3, 'high_multiplier': 0.6}}
        originals['Y16'].update({
            'initial_weapon': 'staff',
            'passive_rules': {
                'phantom': {'4': '2d6', '3': '1d6+6', '2': '1d6', '1': '1d3', '-2': '-1d2'},
                'moon': {'ultimate_threshold': 20, 'bow_extra_mp': 1}},
        })
        originals['Y17']['initial_weapon'] = 'bow'
        originals['Y17']['blood_rules']['moon_phases'] = [
            ['满月', '1d12+3', True], ['亏凸月', '1d8', True], ['弦月', '1d12', False],
            ['残月', '1d6+4', False], ['新月', '1d16+12', False], ['蛾眉月', '8', False],
            ['上弦月', '5', True], ['盈凸月', '10', True],
        ]
        originals['Y17']['spells'][5].pop('radius_per_extra_mp')

        for c in cd.ALL_CHARACTERS:
            old = originals[c['serial']]
            self.assertEqual(c, old, c['serial'])
        self.assertEqual({c['serial'] for c in cd.ALL_CHARACTERS}, set(originals))

        from ai_trainer import _parse_summon_skills
        summon_deltas = {
            '三合一': {'MOV': 13}, '使魔': {'MOV': 9},
            '朱雀': {'MOV': 14, 'HP': 75}, '玄武': {'MOV': 6, 'HP': 120},
            '生灵': {'MOV': 13}, '藤蔓': {'MOV': 11}, '蜂鸟': {'MOV': 14},
            '龙虎': {'MOV': 11, 'HP': 125}, '虚假之月': {'MOV': 3},
            '影之克隆': {'MOV': 11}, '血液长枪': {'MOV': 3},
        }
        expected_summons = copy.deepcopy(before.SUMMON_TEMPLATES)
        for name, fields in summon_deltas.items():
            expected_summons[name].update(fields)
        y6_path = path.parents[2] / 'balance_y6/final_source/characters_data.py'
        y6_spec = importlib.util.spec_from_file_location('balance_y6_reference', y6_path)
        y6_reference = importlib.util.module_from_spec(y6_spec)
        y6_spec.loader.exec_module(y6_reference)
        expected_summons['小雪人'] = copy.deepcopy(y6_reference.SUMMON_TEMPLATES['小雪人'])
        expected_summons['小雪人']['MOV'] = 8
        self.assertEqual(set(cd.SUMMON_TEMPLATES), set(expected_summons))
        for name, old in expected_summons.items():
            actual = copy.deepcopy(cd.SUMMON_TEMPLATES[name])
            expected = copy.deepcopy(old)
            for template in (actual, expected):
                for spell in template.get('spells', []):
                    if not spell.get('消耗mp'):
                        spell['消耗mp'] = 1
                if 'skills' in template:
                    template['skills'] = _parse_summon_skills(template)
                    for skill in template['skills']:
                        skill.pop('mp_cost', None)
            self.assertEqual(actual, expected, name)

    def test_character_spirit_tiers_do_not_remove_or_damage_character(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = setup(cls)
                hp = eng._get_combat_hp('enemy')
                with patch.object(be.random, 'randint', return_value=1):
                    eng._process_battle_spirit()  # initialization
                    eng._process_battle_spirit()  # 4 -> p1
                    self.assertEqual(eng._battle_spirit_character_views('enemy')[0]['auxVal'], 'p1')
                    eng._process_battle_spirit()
                    eng._process_battle_spirit()  # 2 -> p2
                    self.assertEqual(eng._battle_spirit_character_views('enemy')[0]['auxVal'], 'p2')
                    eng._process_battle_spirit()  # 1 -> damage -30%
                    self.assertAlmostEqual(eng._get_buff_dmg_mult('enemy', 'healer0'), .7)
                    eng._process_battle_spirit()  # 0 -> silence
                self.assertTrue(eng._has_status('enemy', '沉默'))
                self.assertFalse(eng._can_cast_active_skill('enemy'))
                self.assertIsNotNone(eng._unit_il_entry('enemy', eng._get_initiative()))
                self.assertEqual(eng._get_combat_hp('enemy'), hp)
                eng.get_char('healer0').inventory = []
                eng._set_combat_hp('healer0', 0)
                self.assertEqual(eng._battle_spirit_character_views('enemy'), [])

    def test_multiple_sources_process_character_once_and_no_friendly_fire(self):
        eng = setup(be.FullBattleEngine, healers=2)
        with patch.object(be.random, 'randint', return_value=1):
            eng._process_battle_spirit()
            eng._process_battle_spirit()
        states = [e.get('characterStacks', {}) for e in eng._get_effects() if e.get('type') == 'battleSpirit']
        self.assertEqual([s['enemy'] for s in states if 'enemy' in s], [4])
        self.assertTrue(all('healer0' not in s and 'healer1' not in s for s in states))

    def test_isolation_pauses_aura_and_serialized_stacks_survive_restore(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            eng = setup(cls)
            with patch.object(be.random, 'randint', return_value=1):
                eng._process_battle_spirit()
                eng._process_battle_spirit()
            saved = json.loads(json.dumps(eng._get_effects(), ensure_ascii=False))
            eng._set_effects(saved)
            self.assertEqual(eng._battle_spirit_character_views('enemy')[0]['auxVal'], 'p1')
            eng._set_effects(saved + [dict(type='buff', targetUserId='healer0',
                auxType='隔离', auxVal='1', remainingRounds=2)])
            self.assertEqual(eng._battle_spirit_character_views('enemy'), [])
            with patch.object(be.random, 'randint', return_value=1):
                eng._process_battle_spirit()
            spirit = next(e for e in eng._get_effects() if e.get('type') == 'battleSpirit')
            self.assertEqual(spirit['characterStacks']['enemy'], 4)

    def test_legacy_aura_without_opt_in_never_suppresses_characters(self):
        eng = setup(be.FullBattleEngine)
        effects = eng._get_effects()
        for eff in effects:
            if eff.get('type') == 'battleSpirit':
                eff.pop('战意影响主体', None)
        eng._set_effects(effects)
        with patch.object(be.random, 'randint', return_value=1):
            for _ in range(8):
                eng._process_battle_spirit()
        self.assertEqual(eng._battle_spirit_character_views('enemy'), [])


if __name__ == '__main__':
    unittest.main()
