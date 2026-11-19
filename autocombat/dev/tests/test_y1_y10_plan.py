"""Additional LOCK evidence and Y7-04 full personal-turn recovery."""
import copy
import json
import unittest
from unittest.mock import patch

import battle_engine as be
import characters_data as cd
from battle_http_server import PvPFullBattleEngine


def setup(cls, serial='Y7'):
    e = cls()
    for uid, code in [('source', serial), ('ally', 'Y1'), ('enemy', 'Y1')]:
        cd.load_character_to_engine(e, next(c for c in cd.ALL_CHARACTERS if c['serial'] == code), uid)
        e.process_command(uid, '.hs')
    e.setup_battle(['source', 'ally'], ['enemy'], '12x12', season_status=50)
    return e


class SpiritRecoveryTests(unittest.TestCase):
    def test_zero_mid_turn_waits_for_next_complete_turn_and_restores_three(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine, PvPFullBattleEngine):
            with self.subTest(engine=cls.__name__):
                e = setup(cls)
                il = e._get_initiative()
                # Place the enemy first: finishing this partial turn cannot restore.
                il.sort(key=lambda ent: ent['userId'] != 'enemy')
                e._set_initiative(il)
                state = e._get_state()
                state['activeIndex'] = 0
                e._set_state(state)
                effects = e._get_effects()
                aura = next(f for f in effects if f.get('type') == 'battleSpirit')
                aura['characterStacks'] = {'enemy': 1}
                e._set_effects(effects)
                with patch.object(be.random, 'randint', return_value=1):
                    e._process_battle_spirit()
                self.assertTrue(e._has_status('enemy', '沉默'))
                self.assertAlmostEqual(e._get_buff_dmg_mult('enemy', 'source'), .7)
                e._set_effects(json.loads(json.dumps(e._get_effects())))
                e._end_turn('enemy')
                aura = next(f for f in e._get_effects() if f.get('type') == 'battleSpirit')
                self.assertEqual(aura['characterStacks']['enemy'], 0)
                # Drive actual initiative transitions to the next personal turn.
                for _ in range(5):
                    state = e._get_state()
                    active = e._get_initiative()[state['activeIndex']]['userId']
                    if active == 'enemy':
                        break
                    e._end_turn(active)
                self.assertEqual(active, 'enemy')
                self.assertTrue(e._has_status('enemy', '沉默'))
                self.assertEqual(aura['characterRecovery']['enemy'], 'active')
                hp = e._get_combat_hp('enemy')
                e._end_turn('enemy')
                aura = next(f for f in e._get_effects() if f.get('type') == 'battleSpirit')
                self.assertEqual(aura['characterStacks']['enemy'], 3)
                self.assertNotIn('enemy', aura['characterRecovery'])
                self.assertFalse(e._has_status('enemy', '沉默'))
                self.assertEqual(e._get_buff_dmg_mult('enemy', 'source'), 1)
                self.assertEqual(e._battle_spirit_character_views('enemy')[0]['auxVal'], 'p1')
                self.assertEqual(e._get_combat_hp('enemy'), hp)
                self.assertIsNotNone(e._unit_il_entry('enemy', e._get_initiative()))


class LockedEvidenceTests(unittest.TestCase):
    def test_y5_02_base_two_global_override_99(self):
        eff = next(s for s in cd.XUETIANSHI['spells'] if s['name'] == '吸血')['effects'][0]
        with patch.object(cd, 'GLOBAL_RANGE_OVERRIDE', 0):
            self.assertEqual(be._get_attack_range(spell_effect=eff), 2)
        with patch.object(cd, 'GLOBAL_RANGE_OVERRIDE', 1):
            self.assertEqual(be._get_attack_range(spell_effect=eff), 99)

    def test_y10_03_barrier_rolls_capacity_separately_per_target(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine, PvPFullBattleEngine):
            e = setup(cls, 'Y10')
            e._set_effects([])
            e.get_char('source').set_attr('魔力', 100)
            spell = next(s for s in e.load_spells('source') if s['name'] == '屏障术')
            with patch.object(be, 'roll_dice', side_effect=[11, 17]) as dice:
                result = e._execute_spell('source', 'source', spell)
            self.assertTrue(be.action_committed(result), result)
            shields = [f for f in e._get_effects() if f.get('type') == 'shield']
            self.assertEqual(sorted(f['value'] for f in shields), [11, 17])
            self.assertEqual({f['targetUserId'] for f in shields}, {'source', 'ally'})
            self.assertEqual(dice.call_count, 2)


if __name__ == '__main__':
    unittest.main()
