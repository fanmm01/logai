"""Character constraints and battle-spirit protagonist lifecycle regressions."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

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
        path = Path(__file__).resolve().parents[1] / 'artifacts/balance_five/before_source/characters_data.py'
        spec = importlib.util.spec_from_file_location('balance_before', path)
        before = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(before)
        originals = {c['serial']: c for c in before.ALL_CHARACTERS}
        for c in cd.ALL_CHARACTERS:
            old = originals[c['serial']]
            attrs = {k: v for k, v in c['attrs'].items()
                     if c['serial'] != 'Y6' or k not in {'冰冻命中层数', '冰冻雪球命中层数', '冰冻跨循环叠加', '冰冻即时回复层数', '体力', '体力上限'}}
            old_attrs = {k: v for k, v in old['attrs'].items()
                         if c['serial'] != 'Y6' or k not in {'体力', '体力上限'}}
            self.assertEqual(attrs, old_attrs, c['serial'])
            self.assertEqual(c['str_attrs'], old['str_attrs'], c['serial'])
            if c['serial'] not in {'Y6', 'Y7', 'Y9', 'Y10', 'Y11'}:
                self.assertEqual(c, old, c['serial'])
        self.assertEqual({k: v for k, v in cd.SUMMON_TEMPLATES.items() if k != '小雪人'}, before.SUMMON_TEMPLATES)

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
