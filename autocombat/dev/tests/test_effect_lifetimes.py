"""Expiry regressions through both combat engines and real character loading."""
import copy
import unittest
from unittest.mock import patch

import battle_engine as be
import characters_data as cd


def setup(cls):
    eng = cls()
    for uid, serial in [('mage', 'Y2'), ('snow', 'Y6'), ('enemy', 'Y1')]:
        card = next(c for c in cd.ALL_CHARACTERS if c['serial'] == serial)
        cd.load_character_to_engine(eng, card, uid)
        eng.process_command(uid, '.hs')
    eng.setup_battle(['mage', 'snow'], ['enemy'], '12x12', season_status=50)
    eng._set_effects([])
    return eng


def summon(eng, duration, chant=0):
    spell = dict(name='限时召唤', index=90, 消耗mp=0, 吟唱回合=chant,
                 effects=[{'type': 5, '客体': 1, '召唤个数': 1,
                           '召唤物模板': '使魔', '持续回合': duration}])
    result = eng._execute_spell('mage', 'mage', spell)
    assert be.action_committed(result), str(result)
    return next((e['summonId'] for e in reversed(eng._get_effects())
                 if e.get('type') == 'summon'), None)


class EffectLifetimeTests(unittest.TestCase):
    def test_staggered_group_members_expire_and_free_quota_without_death(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = setup(cls)
                first = summon(eng, 2)
                group_key = eng._summon_group_key(first)
                eng._tick_down_effects()
                self.assertGreater(eng._get_combat_hp(first), 0)
                second = summon(eng, 3)
                self.assertEqual(eng._get_summon_live_count('mage', '使魔'), 2)
                before_quota = eng._get_summon_quota('mage', '使魔')[0]
                with patch.object(eng, '_record_graveyard_entry', side_effect=AssertionError('expiry recorded death')):
                    eng._tick_down_effects()
                    self.assertFalse(eng._summon_member_can_act(first))
                    self.assertNotIn(first, eng._summon_members)
                    self.assertEqual(eng._summon_groups[group_key]['members'], [second])
                    self.assertEqual(eng._summon_groups[group_key]['representative_uid'], second)
                    self.assertEqual(eng._get_summon_quota('mage', '使魔')[0], before_quota + 1)
                    self.assertNotIn(first, eng._get_map()['occupants'].values())
                    self.assertTrue(any(e['userId'] == second for e in eng._get_initiative()))
                    eng._tick_down_effects()
                    self.assertGreater(eng._get_combat_hp(second), 0)
                    eng._tick_down_effects()
                self.assertNotIn(group_key, eng._summon_groups)
                self.assertNotIn(group_key, eng._get_actions())
                self.assertNotIn(second, eng._summon_members)
                self.assertFalse(any(e.get('isSummon') for e in eng._get_initiative()))
                self.assertEqual(eng._get_summon_live_count('mage', '使魔'), 0)
                self.assertIsNotNone(summon(eng, 2))

    def test_chanted_summon_gets_full_lifetime_after_completion(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = setup(cls)
                self.assertIsNone(summon(eng, 2, chant=1))
                eng._tick_down_effects()
                timers = [e for e in eng._get_effects() if e.get('type') == 'summon']
                self.assertEqual(len(timers), 1)
                self.assertEqual(timers[0]['remainingRounds'], 2)
                sid = timers[0]['summonId']
                eng._tick_down_effects()
                self.assertGreater(eng._get_combat_hp(sid), 0)
                eng._tick_down_effects()
                self.assertEqual(eng._get_combat_hp(sid), 0)
                self.assertNotIn(sid, eng._summon_members)

    def test_long_lifetimes_are_numeric_and_early_death_drops_timer(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            for duration in (99, 999):
                with self.subTest(engine=cls.__name__, duration=duration):
                    eng = setup(cls)
                    sid = summon(eng, duration)
                    eng._tick_down_effects()
                    timer = next(e for e in eng._get_effects() if e.get('type') == 'summon')
                    self.assertEqual(timer['remainingRounds'], duration - 1)
                    eng._set_combat_hp(sid, 0)
                    eng._reconcile_summon_groups()
                    eng._tick_down_effects()
                    self.assertFalse(any(e.get('summonId') == sid for e in eng._get_effects()))

    def test_snow_staff_damage_bonus_expires_but_shield_keeps_persistence(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = setup(cls)
                spell = next(s for s in eng.load_spells('snow') if s['name'] == '雪之法杖')
                self.assertEqual(spell['effects'][0]['默认延续性'], 0)
                eng._execute_spell('snow', 'snow', spell)
                buffs = [e for e in eng._get_effects() if e['type'] == 'buff']
                self.assertEqual({e['targetUserId'] for e in buffs}, {'mage', 'snow'})
                self.assertTrue(all(not e['persistent'] for e in buffs))
                for _ in range(4):
                    eng._tick_down_effects()
                self.assertEqual(eng._get_buff_dmg_dice_bonus('mage'), '+1d6')
                eng._tick_down_effects()
                self.assertFalse(eng._get_buff_dmg_dice_bonus('mage'))
                self.assertFalse(eng._get_buff_dmg_dice_bonus('snow'))
                shields = [e for e in eng._get_effects() if e['type'] == 'shield']
                self.assertTrue(shields)
                self.assertTrue(all(e['persistent'] and e['remainingRounds'] == -1 for e in shields))
                eng._tick_down_effects()
                self.assertFalse(eng._get_buff_dmg_dice_bonus('mage'))

    def test_ordinary_buffs_expire_while_persistent_buffs_remain(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = setup(cls)
                base_mov = eng._get_effective_mov('mage')
                spell = dict(name='普通增益', index=91, 消耗mp=0, effects=[
                    {'type': 4, '客体': 1, '持续回合': 2, '技能加减值': 'MOV+5'},
                    {'type': 4, '客体': 1, '持续回合': 2,
                     '辅助效果': '伤害骰加值', '辅助效果值': '+1d6'}])
                eng._execute_spell('mage', 'mage', spell)
                self.assertEqual(eng._get_effective_mov('mage'), base_mov + 5)
                eng._tick_down_effects()
                self.assertEqual(eng._get_buff_dmg_dice_bonus('mage'), '+1d6')
                eng._tick_down_effects()
                self.assertEqual(eng._get_effective_mov('mage'), base_mov)
                self.assertFalse(eng._get_active_buffs('mage'))
                persistent = copy.deepcopy(spell)
                persistent['默认延续性'] = 1
                eng._execute_spell('mage', 'mage', persistent)
                eng._tick_down_effects()
                eng._tick_down_effects()
                self.assertEqual(eng._get_effective_mov('mage'), base_mov + 5)
                self.assertEqual(eng._get_buff_dmg_dice_bonus('mage'), '+1d6')

    def test_negative_ordinary_buffs_and_isolation_are_inactive(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = setup(cls)
                eng._set_effects([
                    dict(type='buff', targetUserId='mage', sourceUserId='mage',
                         remainingRounds=-1, auxType='隔离', persistent=0),
                    dict(type='buff', targetUserId='mage', sourceUserId='mage',
                         remainingRounds=-2, skillMod='MOV+5', persistent=0),
                    dict(type='buff', targetUserId='mage', sourceUserId='mage',
                         remainingRounds=-1, skillMod='MOV+3', persistent=1, sourcePassive=True)])
                self.assertFalse(eng._has_status('mage', '隔离'))
                active = eng._get_active_buffs('mage', include_zone=False)
                self.assertEqual(len(active), 1)
                self.assertEqual(active[0]['skillMod'], 'MOV+3')
                zone = dict(type='zone', sourceUserId='mage', sourceBound=True,
                            sourcePassive=True, center='D4')
                self.assertEqual(eng._zone_caster_coord(zone), 'D4')


if __name__ == '__main__':
    unittest.main()
