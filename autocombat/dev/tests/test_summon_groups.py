"""Regressions for consolidated summon lifecycle and physical target visibility."""
import ast
import copy
import math
import logging
import logging.handlers
import sys
from pathlib import Path
import random
import re
import time
import types
import unittest
from unittest.mock import patch

import battle_engine as be
import characters_data as cd


ROOT = (Path(__file__).resolve().parents[2] / 'backend')


def setup_engine(cls=be.FullBattleEngine, reverse=False):
    random.seed(73)
    eng = cls()
    for uid, data in (('spring', cd.CHUNSHANG), ('blood', cd.XUETIANSHI)):
        cd.load_character_to_engine(eng, data, uid)
        eng.process_command(uid, '.hs')
    a, b = (['blood'], ['spring']) if reverse else (['spring'], ['blood'])
    eng.setup_battle(a, b, '10x10', season_status=50)
    return eng


def battle_classes():
    """Load the actual tournament classes without executing module logger setup."""
    import ai_trainer as trainer
    from combat_settings import value, snapshot, pinned
    tree = ast.parse((ROOT / 'ai_battle.py').read_text(encoding='utf-8'))
    classes = [n for n in tree.body if
               (isinstance(n, ast.ClassDef) and n.name in ('DisplayLevel', 'AIController', 'BattleEngine'))
               or (isinstance(n, ast.FunctionDef) and n.name == 'round_hp_line')]
    logs = []
    ns = dict(FullBattleEngine=be.FullBattleEngine, random=random, math=math, re=re,
              battle_setting=value, settings_snapshot=snapshot, pinned=pinned,
              time=time, MAX_ROUNDS_PRELIM=35, battle_log=lambda level, msg: logs.append((level, msg)),
              set_terminal_quiet=lambda value: None,
              load_q_table=trainer.load_q_table,
              encode_state=trainer.encode_state, get_available_actions=trainer.get_available_actions,
              execute_action=trainer.execute_action,
              action_slot_for=trainer.action_slot_for,
              affordable_actions=trainer.affordable_actions,
              select_teammate_by_strategy=trainer.select_teammate_by_strategy,
              encode_summon_state=trainer.encode_summon_state,
              get_summon_actions=trainer.get_summon_actions,
              execute_summon_action=trainer.execute_summon_action,
              defaultdict=__import__('collections').defaultdict)
    ns.update({name: getattr(be, name) for name in (
        'is_in_melee_range', 'has_timing', 'parse_coord', 'format_coord',
        'avg_damage', 'success_rank', 'roll_d100', 'max_damage', 'rank_text')})
    exec(compile(ast.Module(body=classes, type_ignores=[]), str(ROOT / 'ai_battle.py'), 'exec'), ns)
    return types.SimpleNamespace(**ns), logs


class SummonGroupTests(unittest.TestCase):
    def setUp(self):
        self.templates = patch.object(be, '_SUMMON_TEMPLATES', cd.SUMMON_TEMPLATES)
        self.templates.start()
        self.addCleanup(self.templates.stop)

    def test_previously_free_summon_attacks_keep_explicit_zero_mp_cost(self):
        templates = ('三合一', '朱雀', '玄武', '藤蔓', '蜂鸟', '小雪人')
        for template_name in templates:
            for skill in cd.SUMMON_TEMPLATES[template_name].get('skills', []):
                if isinstance(skill, dict):
                    with self.subTest(template=template_name, skill=skill.get('name')):
                        self.assertEqual(skill.get('mp_cost'), 0)
                        self.assertEqual(be._summon_skill_mp_cost(skill), 0)

    def test_promotion_preserves_slot_pool_and_member_order(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = setup_engine(cls)
                a = eng._create_summon('spring', '蜂鸟')
                b = eng._create_summon('spring', '蜂鸟')
                c = eng._create_summon('spring', '蜂鸟')
                il = eng._get_initiative()
                idx = next(i for i, e in enumerate(il) if e['userId'] == a)
                eng._get_state()['activeIndex'] = idx
                slot = copy.deepcopy(il[idx])
                eng._get_actions()[a] = {'主动': 1, '附加': 0}
                eng._set_combat_hp(a, 0)
                eng._reconcile_summon_groups()
                eng._reconcile_summon_groups()
                group = eng._summon_groups[a]
                new_slot = eng._get_initiative()[eng._get_state()['activeIndex']]
                self.assertEqual(new_slot['userId'], b)
                self.assertEqual(group['representative_uid'], b)
                for field in ('coord', 'initRank', 'initRoll', 'dex'):
                    self.assertEqual(new_slot[field], slot[field])
                self.assertEqual(eng._summon_group_key(b), a)
                self.assertEqual(eng._get_my_actions(c), {'主动': 1, '附加': 0})
                self.assertEqual(sum(e.get('isSummon', False) for e in eng._get_initiative()), 1)
                self.assertEqual(set(eng._get_map()['occupants'].values()), {'spring', 'blood', b})
                called = []
                result = eng._execute_summon_group_action(a, lambda m: (called.append(m) or [], False, None))
                self.assertEqual(called, [b, c])
                self.assertTrue(result['action_spent'])
                self.assertEqual(eng._get_my_actions(c)['主动'], 0)

    def test_missing_slot_reconstructed_without_granting_actions(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            for kill_rep in (False, True):
                with self.subTest(engine=cls.__name__, kill_rep=kill_rep):
                    eng = setup_engine(cls)
                    a = eng._create_summon('spring', '藤蔓')
                    b = eng._create_summon('spring', '藤蔓')
                    saved = copy.deepcopy(eng._get_summon_entry(a))
                    eng._get_actions().pop(a)
                    eng._replace_initiative_preserving_actor([e for e in eng._get_initiative() if e['userId'] != a])
                    if kill_rep:
                        eng._set_combat_hp(a, 0)
                    active = eng._get_initiative()[eng._get_state()['activeIndex']]['userId']
                    eng._reconcile_summon_groups()
                    eng._reconcile_summon_groups()
                    rep = b if kill_rep else a
                    slot = eng._get_summon_entry(rep)
                    self.assertFalse(slot.get('_synthetic', False))
                    self.assertEqual(slot['initRoll'], saved['initRoll'])
                    self.assertEqual(slot['coord'], saved['coord'])
                    self.assertEqual(eng._get_my_actions(rep)['主动'], 0)
                    self.assertEqual(eng._get_initiative()[eng._get_state()['activeIndex']]['userId'], active)
                    self.assertEqual(sum(e.get('isSummon', False) for e in eng._get_initiative()), 1)

    def test_extinction_resummon_keeps_total_cap(self):
        # This engine regression requires three complete two-member groups;
        # use its own fixture instead of depending on editable balance data.
        templates = copy.deepcopy(cd.SUMMON_TEMPLATES)
        templates['藤蔓']['max_total_spawned'] = 6
        fixture = patch.object(be, '_SUMMON_TEMPLATES', templates)
        fixture.start()
        self.addCleanup(fixture.stop)
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = setup_engine(cls)
                for _ in range(3):
                    a = eng._create_summon('spring', '藤蔓')
                    b = eng._create_summon('spring', '藤蔓')
                    self.assertIsNotNone(a)
                    self.assertIsNotNone(b)
                    self.assertIsNone(eng._create_summon('spring', '藤蔓'))
                    self.assertIn(a, [e['userId'] for e in eng._get_initiative()])
                    eng._set_combat_hp(a, 0)
                    eng._set_combat_hp(b, 0)
                    self.assertFalse(eng._is_untargetable('spring'))
                    eng._reconcile_summon_groups()
                    self.assertFalse(eng._summon_groups)
                    self.assertFalse(eng._summon_members)
                self.assertIsNone(eng._create_summon('spring', '藤蔓'))
                self.assertEqual(eng._summon_total_spawned[('spring', '藤蔓')], 6)
                self.assertEqual(set(eng._get_map()['occupants'].values()), {'spring', 'blood'})

    def test_new_round_allocates_under_stable_key_using_live_rep(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = setup_engine(cls)
                a = eng._create_summon('spring', '藤蔓')
                b = eng._create_summon('spring', '藤蔓')
                eng.get_char(b).set_attr('额外行动数', 3)
                eng._set_combat_hp(a, 0)
                eng._reconcile_summon_groups()
                il = eng._get_initiative()
                eng._get_state()['activeIndex'] = len(il) - 1
                eng._end_turn(il[-1]['userId'])
                self.assertEqual(eng._get_state()['round'], 2)
                self.assertEqual(eng._get_actions()[a]['主动'], eng._calc_main_actions(b))
                self.assertNotIn(b, eng._get_actions())
                eng._consume_action(b)
                before = copy.deepcopy(eng._get_actions())
                eng._reconcile_summon_groups()
                self.assertEqual(eng._get_actions(), before)

    def test_hidden_target_selection_and_real_damage(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = setup_engine(cls)
                a = eng._create_summon('spring', '藤蔓')
                b = eng._create_summon('spring', '藤蔓')
                eng._set_combat_hp(a, 0)
                # Queries must work even before the lifecycle maintenance pass.
                snapshot = copy.deepcopy((eng._summon_groups, eng._summon_members,
                                          eng._get_initiative(), eng._get_actions(), eng._get_map()))
                spell = next(s for s in eng.load_spells('blood') if s['name'] == '吸血')
                with patch.object(be, '_weight_loaded', True), patch.object(be, '_weight_cache', None):
                    self.assertEqual(eng._smart_target('blood', spell), b)
                self.assertEqual(eng._pick_random_target('blood'), b)
                self.assertIsNone(eng._pick_random_target('blood', include_summons=False))
                for strat in ('T0', 'T1', 'T2', 'T3'):
                    with patch.object(be, '_weight_loaded', True), patch.object(be, '_weight_cache', {'x': 1}), \
                            patch.object(be, '_weight_best_strategy', {'Q_solo:Y5': strat}):
                        self.assertEqual(eng._get_weighted_target('blood'), b)
                self.assertEqual(snapshot, (eng._summon_groups, eng._summon_members,
                                            eng._get_initiative(), eng._get_actions(), eng._get_map()))
                with patch.object(eng, '_coc7_attack', return_value=(b, 'blood', ['attack'])) as attack:
                    eng._basic_attack('blood')
                    self.assertEqual(attack.call_args.args[1], b)
                eng._get_summon_entry(a)['coord'] = eng._unit_il_entry('blood', eng._get_initiative())['coord']
                eff = {'name': '测试伤害', 'index': 99, 'effects': [
                    {'type': 1, '客体': 4, '伤害骰': '2', '成功率': 100,
                     '可闪避性': 0, '可反击性': 0, '射程': 99}]}
                hp = eng._get_combat_hp(b)
                with patch.object(be, 'roll_d100', return_value=(20, '20')):
                    out = eng._execute_spell('blood', b, eff)
                self.assertNotIn('无法接近', out)
                self.assertLess(eng._get_combat_hp(b), hp)

    def test_hidden_traits_teams_and_vine_only_protection(self):
        eng = setup_engine(reverse=True)
        a = eng._create_summon('spring', '蜂鸟')
        b = eng._create_summon('spring', '蜂鸟')
        self.assertEqual(eng._battle_team_of(b), 'X')
        self.assertTrue(eng._is_flying(b))
        self.assertFalse(eng._is_untargetable('spring'))
        self.assertIn('blood', eng._enemy_targets(b))
        eng.get_char(b).set_attr('不可指定', 1)
        self.assertNotIn(b, eng._enemy_targets('blood'))
        v = eng._create_summon('spring', '藤蔓')
        self.assertTrue(eng._is_untargetable('spring'))
        eng._set_combat_hp(v, 0)
        self.assertFalse(eng._is_untargetable('spring'))

    def test_death_cleanup_before_at_after_actor(self):
        for dead_index in range(3):
            eng = setup_engine()
            a = eng._create_summon('spring', '藤蔓')
            by_id = {e['userId']: e for e in eng._get_initiative()}
            entries = [by_id['spring'], by_id['blood']]
            entries.insert(dead_index, by_id[a])
            eng._set_initiative(entries)
            active_index = 1
            active = entries[active_index]['userId']
            expected = entries[(active_index + 1) % 3]['userId'] if active == a else active
            eng._get_state()['activeIndex'] = active_index
            eng._set_combat_hp(a, 0)
            with patch.object(eng, '_tick_down', wraps=eng._tick_down) as tick:
                eng._reconcile_summon_groups()
                self.assertEqual(eng._get_initiative()[eng._get_state()['activeIndex']]['userId'], expected)
                self.assertEqual(tick.call_count, 0)
            self.assertNotIn(a, eng._summon_groups)
        # Removing the active final slot must cross the round boundary exactly once.
        eng = setup_engine()
        a = eng._create_summon('spring', '藤蔓')
        il = eng._get_initiative()
        slot = eng._get_summon_entry(a)
        eng._set_initiative([e for e in il if e['userId'] != a] + [slot])
        eng._get_state()['activeIndex'] = 2
        eng._set_combat_hp(a, 0)
        with patch.object(eng, '_tick_down', wraps=eng._tick_down) as tick:
            eng._reconcile_summon_groups()
            self.assertEqual(tick.call_count, 1)
        self.assertEqual(eng._get_state()['round'], 2)
        self.assertEqual(eng._get_state()['activeIndex'], 0)

    def test_dot_promotes_without_dropping_survivor(self):
        eng = setup_engine()
        a = eng._create_summon('spring', '藤蔓')
        b = eng._create_summon('spring', '藤蔓')
        eng._get_effects().append({'type': 'dot', 'dotDice': '99', 'targetUserId': a,
                                  'remainingRounds': 1, 'spellName': '持续伤害'})
        eng._tick_down()
        self.assertEqual(eng._summon_groups[a]['representative_uid'], b)
        self.assertIn(b, [e['userId'] for e in eng._get_initiative()])
        self.assertTrue(eng._enemy_targets('blood'))

    def test_per_member_resources_persist_and_do_not_transfer(self):
        eng = setup_engine()
        a = eng._create_summon('spring', '蜂鸟')
        b = eng._create_summon('spring', '蜂鸟')
        eng._set_summon_entry_fields(a, shield_block_hp=13)
        eng._set_summon_entry_fields(b, shield_block_hp=7, shield_block_rate=1)
        self.assertEqual(eng._apply_shield_block(b, 5), 0)
        self.assertEqual(eng._get_summon_entry(b)['shield_block_hp'], 2)
        self.assertEqual(eng._get_summon_entry(a)['shield_block_hp'], 13)
        eng._set_combat_hp(a, 0)
        eng._reconcile_summon_groups()
        self.assertEqual(eng._get_summon_entry(b)['shield_block_hp'], 2)
        eng._reconcile_summon_groups()
        self.assertEqual(eng._get_summon_entry(b)['shield_block_hp'], 2)

    def test_two_by_two_footprint_owner_cleanup_and_owner_caps(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                eng = setup_engine(cls)
                templates = copy.deepcopy(cd.SUMMON_TEMPLATES)
                templates['玄武'].update(max_simultaneous=2, max_total_spawned=2)
                with patch.object(be, '_SUMMON_TEMPLATES', templates):
                    a = eng._create_summon('spring', '玄武')
                    before = dict(eng._get_map()['occupants'])
                    b = eng._create_summon('spring', '玄武')
                self.assertIsNotNone(b)
                self.assertEqual(eng._get_map()['occupants'], before)
                cells = {c for c, uid in before.items() if uid == a}
                self.assertEqual(len(cells), 4)
                eng._set_combat_hp(a, 0)
                eng._reconcile_summon_groups()
                self.assertEqual({c for c, uid in eng._get_map()['occupants'].items() if uid == b}, cells)
                v1 = eng._create_summon('spring', '藤蔓')
                v2 = eng._create_summon('blood', '藤蔓')
                self.assertEqual(eng._get_summon_live_count('spring', '藤蔓'), 1)
                self.assertEqual(eng._get_summon_live_count('blood', '藤蔓'), 1)
                eng._remove_summons_of_owner('spring')
                self.assertNotIn(b, eng._summon_members)
                self.assertNotIn(v1, eng._summon_members)
                self.assertIn(v2, eng._summon_members)
                self.assertEqual(eng._summon_total_spawned[('spring', '藤蔓')], 1)

    def test_actual_training_driver_promotes_before_dispatch(self):
        import ai_trainer as trainer
        seen = []

        class SeededEngine(be.FastBattleEngine):
            def setup_battle(self, *args, **kwargs):
                super().setup_battle(*args, **kwargs)
                self.a = self._create_summon('spring', '藤蔓')
                self.b = self._create_summon('spring', '藤蔓')
                self._get_state()['activeIndex'] = next(
                    i for i, e in enumerate(self._get_initiative()) if e['userId'] == self.a)
                self._set_combat_hp(self.a, 0)

            def _execute_summon_group_action(self, key, execute_member):
                self_test.assertEqual(key, self.a)
                self_test.assertEqual(self._get_initiative()[self._get_state()['activeIndex']]['userId'], self.b)
                # The driver intentionally replaced actions after setup; repair must not refill it.
                self_test.assertEqual(self._get_my_actions(self.b)['主动'], 0)
                seen.append(self.b)
                self._get_state()['phase'] = 'ended'
                return {'action_spent': False, 'remaining': 0, 'lines': []}

        self_test = self
        with patch.object(trainer, 'FastBattleEngine', SeededEngine):
            trainer._mp_run_battle((['Y10'], ['Y5'], '10x10',
                                   [cd.CHUNSHANG, cd.XUETIANSHI],
                                   {'Y10': 'spring', 'Y5': 'blood'}, cd.SUMMON_TEMPLATES))
        self.assertTrue(seen)
        random.seed(7)
        templates = copy.deepcopy(cd.SUMMON_TEMPLATES)
        for name in ('藤蔓', '蜂鸟'):
            templates[name]['MP'] = 20
        updates = trainer._mp_run_summon_battle((['藤蔓'], ['蜂鸟'], '10x10', templates))
        self.assertIsInstance(updates, list)
        self.assertTrue(updates)

    def test_group_action_snapshot_does_not_include_new_members(self):
        eng = setup_engine()
        a = eng._create_summon('spring', '蜂鸟')
        b = eng._create_summon('spring', '蜂鸟')
        born = []
        called = []

        def member(uid):
            called.append(uid)
            if uid == a:
                born.append(eng._create_summon('spring', '蜂鸟'))
            return [], False, None

        before = eng._get_my_actions(a)['主动']
        eng._execute_summon_group_action(a, member)
        self.assertEqual(called, [a, b])
        self.assertNotIn(born[0], called)
        self.assertEqual(eng._get_my_actions(a)['主动'], before - 1)

    def test_recovery_preserves_depleted_member_resources(self):
        eng = setup_engine()
        uid = eng._create_summon('spring', '蜂鸟')
        eng._set_summon_entry_fields(uid, shield_block_hp=0, summon_mp=1, zone_cooldown=3)
        eng._replace_initiative_preserving_actor([e for e in eng._get_initiative() if e['userId'] != uid])
        eng._reconcile_summon_groups()
        view = eng._get_summon_entry(uid)
        self.assertEqual((view['shield_block_hp'], view['summon_mp'], view['zone_cooldown']), (0, 1, 3))

    def test_removed_actor_does_not_advance_successor_twice(self):
        eng = setup_engine()
        eng._get_state()['activeIndex'] = next(
            i for i, e in enumerate(eng._get_initiative()) if e['userId'] == 'spring')
        eng._remove_summons_of_owner('spring')
        before = copy.deepcopy(eng._get_state())
        eng._end_turn('spring')
        self.assertEqual(eng._get_state()['activeIndex'], before['activeIndex'])
        self.assertEqual(eng._get_state()['round'], before['round'])

    def test_http_hidden_target_indices_and_reaction_resources(self):
        # HTTP import aliases character modules and opens a rotating log. Isolate both.
        original_cd = sys.modules.get('characters_data')
        with patch.object(logging.handlers, 'RotatingFileHandler', return_value=logging.NullHandler()):
            import battle_http_server as srv
        if original_cd is not None:
            sys.modules['characters_data'] = original_cd
        eng = setup_engine(srv.PvPFullBattleEngine)
        a = eng._create_summon('spring', '蜂鸟')
        b = eng._create_summon('spring', '蜂鸟')
        entries = srv._build_global_list(eng)
        hidden = next(e for e in entries if e['userId'] == b)
        self.assertEqual(srv._resolve_global_index(eng, hidden['index']), b)
        self.assertEqual(srv._resolve_enemy_index(eng, 'blood', hidden['index']), b)
        eng._player_controllers = {b: ['p1']}
        eng.get_char(b).set_attr('可格挡', 1)
        eng.get_char(b).set_attr('格挡', 100)
        eng._set_summon_entry_fields(a, shield_block_hp=100)
        eng._set_summon_entry_fields(b, shield_block_hp=6, ignore_unreactable_block=True)
        effect = {'type': 1, '客体': 4, '伤害骰': '2', '可闪避性': 0, '可反击性': 0}
        spell = {'name': '测试法术', 'index': 99, 'effects': [effect]}
        with self.assertRaises(srv.ReactionNeeded) as caught:
            eng._trigger_spell_reaction('blood', b, effect, spell, '2', False, False,
                                        atk_rank=1, atk_roll=50)
        state = caught.exception.data
        self.assertTrue(state['can_block'])
        with patch.object(be, 'roll_d100', return_value=(20, '20')):
            eng.resolve_spell_reaction(state, 'block')
        self.assertEqual(eng._get_summon_entry(b)['shield_block_hp'], 4)
        self.assertEqual(eng._get_summon_entry(a)['shield_block_hp'], 100)
        # A subsequent pause/resume starts from the depleted physical shield.
        with self.assertRaises(srv.ReactionNeeded) as caught:
            eng._trigger_spell_reaction('blood', b, effect, spell, '2', False, False,
                                        atk_rank=1, atk_roll=50)
        with patch.object(be, 'roll_d100', return_value=(20, '20')):
            eng.resolve_spell_reaction(caught.exception.data, 'block')
        self.assertEqual(eng._get_summon_entry(b)['shield_block_hp'], 2)

    def test_actual_battle_loop_promotion_and_resummon(self):
        classes, logs = battle_classes()
        eng = setup_engine(classes.BattleEngine)
        eng.delay = 0
        eng.display_level = classes.DisplayLevel.DEBUG
        eng.max_rounds = 4
        a = eng._create_summon('spring', '藤蔓')
        b = eng._create_summon('spring', '藤蔓')
        eng._set_combat_hp(a, 0)
        targets = []
        born = []

        class Spring:
            def decide_action(self, e, uid):
                if e._get_state()['round'] >= 2 and not born:
                    for m in list(e._summon_members):
                        e._set_combat_hp(m, 0)
                    born.append(e._create_summon(uid, '藤蔓'))
                return '.i end'

        class Blood:
            def decide_action(self, e, uid):
                enemies = e._enemy_targets(uid)
                self_test.assertTrue(enemies)
                targets.append(enemies[0])
                # The real attack/skill implementation executes through run_battle.
                return '.s2'

        self_test = self
        with patch.object(be, '_weight_loaded', True), patch.object(be, '_weight_cache', None):
            eng.run_battle({'spring': Spring(), 'blood': Blood()})
        self.assertIn(b, targets)
        self.assertTrue(born)
        self.assertIn(born[0], targets)
        self.assertTrue(any('召24' in line for _, line in logs))
        self.assertFalse(any('没有可攻击的目标' in line for _, line in logs))



class RepresentativeSlotDefaultsTests(unittest.TestCase):
    def test_promoted_slot_always_has_team_and_name(self):
        import characters_data as cd
        import battle_engine as be
        be._SUMMON_TEMPLATES = cd.SUMMON_TEMPLATES
        def char(serial):
            return next(c for c in cd.ALL_CHARACTERS if c['serial'] == serial)
        eng = be.FullBattleEngine()
        for uid, serial in [('owner', 'Y2'), ('enemy', 'Y11')]:
            cd.load_character_to_engine(eng, char(serial), uid)
            eng.process_command(uid, '.hs')
        eng.setup_battle(['owner'], ['enemy'], '12x12', season_status=50)
        first = eng._create_summon('owner', '使魔')
        second = eng._create_summon('owner', '使魔')
        key = eng._summon_group_key(first)
        with patch.object(eng, '_check_con_save', return_value=False):
            eng._set_combat_hp(first, 0, source_dmg=eng._get_combat_hp(first) + 1)
        rep = eng._ensure_group_representative(key)
        entry = next(e for e in eng._get_initiative() if e['userId'] == rep)
        self.assertIn('team', entry)
        self.assertTrue(entry.get('name'))
        self.assertTrue(entry.get('isSummon'))


if __name__ == '__main__':
    unittest.main()
