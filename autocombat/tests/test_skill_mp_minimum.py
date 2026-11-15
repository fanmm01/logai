"""Skill fees stay separate from ordinary attacks and defensive reactions."""
import copy
import unittest
from unittest.mock import patch

import ai_trainer as tr
import battle_engine as be
import characters_data as cd


def setup(cls=be.FullBattleEngine, serial='Y4'):
    engine = cls()
    for uid, code in [('actor', serial), ('enemy', 'Y4')]:
        card = copy.deepcopy(next(c for c in cd.ALL_CHARACTERS if c['serial'] == code))
        cd.load_character_to_engine(engine, card, uid)
        engine.process_command(uid, '.hs')
    engine.setup_battle(['actor'], ['enemy'], '12x12', season_status=50)
    engine._set_effects([])
    rows = engine._get_initiative()
    for row in rows:
        row['coord'] = 'B2' if row['userId'] == 'actor' else 'C2'
    engine._set_initiative(rows)
    return engine


class SkillMpTests(unittest.TestCase):
    def test_every_declared_skill_has_a_positive_base_fee(self):
        for card in cd.ALL_CHARACTERS:
            for spell in card['spells']:
                with self.subTest(serial=card['serial'], skill=spell['name']):
                    self.assertTrue(spell.get('消耗mp'))
        for template in cd.SUMMON_TEMPLATES.values():
            for spell in template.get('spells', []):
                self.assertGreaterEqual(spell['消耗mp'], 1)

    def test_multihit_pays_once_and_insufficient_mp_rejects(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            engine = setup(cls)
            spell = next(s for s in engine.load_spells('actor') if s['name'] == '星闪攻击')
            engine.get_char('actor').set_attr('魔力', 1)
            with patch.object(engine, '_roll_spell_success_bp', return_value=(99, '')) as checks:
                self.assertTrue(be.action_committed(engine._execute_spell('actor', 'enemy', spell)))
            self.assertEqual(checks.call_count, 2)
            self.assertEqual(engine.get_char('actor').get_attr('魔力'), 0)
            self.assertFalse(be.action_committed(engine._execute_spell('actor', 'enemy', spell)))

    def test_preparation_waives_only_non_mp_resources(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            for cost in (0, 4):
                engine = setup(cls)
                state = engine._get_state()
                state['phase'] = 'preparation'
                engine._set_state(state)
                char = engine.get_char('actor')
                char.set_attr('魔力', 10)
                before = (engine._hp_safe('actor'), char.get_attr('理智'))
                spell = dict(index=99, name='准备技能', 时机='2', 消耗mp=cost,
                             消耗san=3, cost_hp_pct_current=20, prep_free=True,
                             effects=[dict(type=4, 客体=1, 持续回合=2, 技能加减值='斗殴+1')])
                self.assertTrue(be.action_committed(engine._execute_spell('actor', 'actor', spell)))
                self.assertEqual(char.get_attr('魔力'), 10 - max(1, cost))
                self.assertEqual((engine._hp_safe('actor'), char.get_attr('理智')), before)

    def test_passive_initialization_pays_one_mp_per_skill(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            engine = cls()
            cd.load_character_to_engine(engine, cd.LINGNIU, 'actor')
            cd.load_character_to_engine(engine, cd.XINGSHAN, 'enemy')
            engine.setup_battle(['actor'], ['enemy'], '12x12', season_status=50)
            self.assertEqual(engine.get_char('actor').get_attr('魔力'), cd.LINGNIU['attrs']['魔力'] - 2)

    def test_blood_skill_plan_includes_new_fee_and_is_read_only(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            engine = setup(cls, 'Y17')
            spell = next(s for s in engine.load_spells('actor') if s['name'] == '凝血式-守')
            char = engine.get_char('actor')
            char.set_attr('魔力', 1)
            before = copy.deepcopy(char.attrs)
            self.assertEqual(engine._y17_plan('actor', 'actor', spell)['mp'], 1)
            self.assertEqual(char.attrs, before)
            char.set_attr('魔力', 0)
            self.assertTrue(engine._y17_preflight('actor', spell))

    def test_summon_skill_payment_and_ai_filtering(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            engine = setup(cls)
            sid = engine._create_summon('actor', '朱雀')
            engine._set_summon_entry_fields(sid, summon_mp=1, zone_skills=[])
            with patch.object(engine, '_coc7_attack', return_value=(0, 0, ['未命中'])):
                engine._summon_attack(sid)
            self.assertEqual(engine._get_summon_entry(sid)['summon_mp'], 0)
            self.assertEqual(tr.get_summon_actions(engine, sid), [])
            self.assertIn('魔力不足', ''.join(tr.execute_summon_action(engine, sid, 'SUMMON_SKILL_0__T0')))

    def test_ordinary_attack_and_dodge_counter_remain_free(self):
        from tests.test_battle_execution import http_module
        srv = http_module()
        engine = setup(srv.PvPFullBattleEngine)
        for uid in ('actor', 'enemy'):
            engine.get_char(uid).set_attr('魔力', 0)
        # Exercise real attack / defensive resolution, without any spell entry.
        for choice in ('dodge', 'counter'):
            engine._player_controllers = {'enemy': ['human']}
            with patch.object(srv, 'roll_d100', return_value=(50, '')):
                try:
                    engine._coc7_attack('actor', 'enemy', '斗殴', 75, '1d4', 0, 0)
                except srv.ReactionNeeded as pending:
                    engine.resolve_reaction(pending.data, choice)
                else:
                    self.fail('Expected an ordinary defensive reaction')
            self.assertEqual(engine.get_char('actor').get_attr('魔力'), 0)
            self.assertEqual(engine.get_char('enemy').get_attr('魔力'), 0)

    def test_special_reaction_pays_only_when_it_can_fire(self):
        engine = setup(serial='Y15')
        char = engine.get_char('actor')
        char.set_attr('魔力', 0)
        with patch.object(be, 'roll_d100', return_value=(99, '')):
            engine._coc7_attack('enemy', 'actor', '斗殴', 50, '1d4', 0, 0)
        self.assertNotIn('爆破圈', getattr(char, '_once_consumed', set()))
        char.set_attr('魔力', 1)
        with patch.object(be, 'roll_d100', return_value=(99, '')):
            engine._coc7_attack('enemy', 'actor', '斗殴', 50, '1d4', 0, 0)
        self.assertEqual(char.get_attr('魔力'), 1)
        self.assertNotIn('爆破圈', getattr(char, '_once_consumed', set()))
        # Y15-03a=A: the named reaction pays only after actual HP loss.
        engine._set_combat_hp('actor', engine._get_combat_hp('actor') - 1, damage_source='enemy')
        self.assertEqual(char.get_attr('魔力'), 0)
        self.assertIn('爆破圈', char._once_consumed)


if __name__ == '__main__':
    unittest.main()
