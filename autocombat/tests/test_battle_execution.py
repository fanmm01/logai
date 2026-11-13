"""Execution regressions from the combat replay: state, budgets and visible results."""
import copy
import logging
import logging.handlers
import random
import sys
import unittest
from unittest.mock import patch

import battle_engine as be
import characters_data as cd
import ai_trainer as trainer
from tests.test_summon_groups import battle_classes


def setup(cls=be.FullBattleEngine):
    eng = cls()
    for uid, serial in [('actor', 'Y8'), ('ally', 'Y7'), ('other', 'Y5'), ('enemy', 'Y12')]:
        cd.load_character_to_engine(eng, next(c for c in cd.ALL_CHARACTERS if c['serial'] == serial), uid)
        eng.process_command(uid, '.hs')
    eng.setup_battle(['actor', 'ally', 'other'], ['enemy'], '12x12', season_status=50)
    positions = {'actor': 'B2', 'ally': 'B4', 'other': 'G2', 'enemy': 'J2'}
    il = eng._get_initiative()
    il.sort(key=lambda e: e['userId'] != 'actor')
    for entry in il:
        entry['coord'] = positions[eng._resolve_uid(entry['userId'])]
        entry['_anchor_coord'] = entry['coord']
        entry['_move_used'] = 0
    eng._set_initiative(il)
    md = eng._get_map()
    md['occupants'] = {coord: uid for uid, coord in positions.items()}
    eng._set_map(md)
    state = eng._get_state()
    state['activeIndex'] = next(i for i, e in enumerate(il) if e['userId'] == 'actor')
    eng._set_state(state)
    eng._set_actions({u: {'主动': 2, '附加': 3} for u in positions})
    eng._drain_battle_events()
    return eng


def http_module():
    original = sys.modules.get('characters_data')
    with patch.object(logging.handlers, 'RotatingFileHandler', return_value=logging.NullHandler()):
        import battle_http_server as srv
    sys.modules['characters_data'] = original
    return srv


class ExecutionTests(unittest.TestCase):
    def test_move_updates_map_slots_and_cost_once(self):
        eng = setup()
        il = eng._get_initiative()
        clone = copy.deepcopy(next(e for e in il if e['userId'] == 'actor'))
        clone.update(userId='actor__act1', baseUserId='actor')
        il.append(clone)
        eng._set_initiative(il)
        result = eng._execute_ai_command('actor', '.a m D2')
        self.assertTrue(result.committed)
        self.assertEqual(result.slot, '附加')
        for e in eng._get_initiative():
            if eng._resolve_uid(e['userId']) == 'actor':
                self.assertEqual((e['coord'], e['_move_used']), ('D2', 2))
        self.assertNotIn('B2', eng._get_map()['occupants'])
        self.assertEqual(eng._get_map()['occupants']['D2'], 'actor')
        # Engine execution does not double-charge the caller's action slot.
        self.assertEqual(eng._get_my_actions('actor')['附加'], 3)

    def test_invalid_moves_are_atomic(self):
        for coord in ['B2', 'B4', 'M2', 'A0', 'invalid', 'L12']:
            with self.subTest(coord=coord):
                eng = setup()
                with patch.object(eng, '_get_move_power', return_value=2):
                    before = copy.deepcopy((eng._get_map(), eng._get_initiative(), eng._get_actions()))
                    result = eng._execute_ai_command('actor', '.a m ' + coord)
                self.assertFalse(result.committed)
                self.assertEqual(before, (eng._get_map(), eng._get_initiative(), eng._get_actions()))

    def test_immobilized_move_and_exhausted_bonus_rejected(self):
        eng = setup()
        with patch.object(eng, '_can_move', return_value=False):
            self.assertFalse(eng._execute_ai_command('actor', '.a m C2').committed)
        eng._get_actions()['actor']['附加'] = 0
        self.assertFalse(eng._execute_ai_command('actor', '.a m C2').committed)
        self.assertEqual(eng._unit_il_entry('actor', eng._get_initiative())['coord'], 'B2')

    def test_anchor_return_uses_remaining_move_power(self):
        eng = setup()
        with patch.object(eng, '_get_move_power', return_value=3):
            eng._move_explicit('actor', 'D2')
            eng._return_to_anchor('actor')
        self.assertEqual(eng._unit_il_entry('actor', eng._get_initiative())['coord'], 'C2')
        self.assertEqual(eng._get_map()['occupants']['C2'], 'actor')

    def test_training_move_charges_additional_only(self):
        eng = setup()
        result = trainer.execute_training_action(eng, 'actor', 'MOVE_TOWARD')
        self.assertTrue(result.committed)
        self.assertEqual(eng._get_my_actions('actor'), {'主动': 2, '附加': 2})
        self.assertNotEqual(eng._unit_il_entry('actor', eng._get_initiative())['coord'], 'B2')
        eng._get_actions()['actor']['附加'] = 0
        before = copy.deepcopy((eng._get_map(), eng._get_initiative()))
        self.assertFalse(trainer.execute_training_action(eng, 'actor', 'MOVE_TOWARD').committed)
        self.assertEqual(before, (eng._get_map(), eng._get_initiative()))

    def test_cake_transfer_consumes_one_item_and_one_bonus(self):
        eng = setup()
        eng.get_char('actor').inventory = [{'item': '蛋糕HP', 'count': 2}]
        eng._set_combat_hp('ally', 10)
        with patch.object(be, 'roll_dice', return_value=5):
            result = trainer.execute_training_action(eng, 'actor', 'GIVE_CAKE__T0')
        self.assertTrue(result.committed)
        self.assertGreater(eng._get_combat_hp('ally'), 10)
        self.assertEqual(eng.get_char('actor').inventory[0]['count'], 1)
        self.assertEqual(eng._get_my_actions('actor'), {'主动': 2, '附加': 2})

    def test_failed_cake_or_invalid_recipient_does_not_charge(self):
        eng = setup()
        eng.get_char('actor').inventory = []
        eng._set_effects([e for e in eng._get_effects() if e.get('type') != 'create'])
        for cmd in ['.a give ally', '.a give actor', '.a give enemy', '.a give missing']:
            self.assertFalse(eng._execute_ai_command('actor', cmd).committed)
        self.assertEqual(eng._get_my_actions('actor'), {'主动': 2, '附加': 3})

    def test_teammate_strategies_use_distance_hp_and_mp(self):
        eng = setup()
        teammates = [eng._unit_il_entry(u, eng._get_initiative()) for u in ['other', 'ally']]
        eng._set_combat_hp('ally', 1)
        eng.get_char('other').set_attr('魔力', 0)
        eng.get_char('ally').set_attr('魔力', 10)
        self.assertEqual(trainer.select_teammate_by_strategy(eng, 'actor', 'T0', teammates), 'ally')
        self.assertEqual(trainer.select_teammate_by_strategy(eng, 'actor', 'T1', teammates), 'ally')
        self.assertEqual(trainer.select_teammate_by_strategy(eng, 'actor', 'T2', teammates), 'other')

    def test_revive_rejected_in_candidates_and_at_execution(self):
        eng = setup()
        spell = next(s for s in eng.load_spells('ally') if any(e.get('type') == 19 for e in s['effects']))
        before = copy.deepcopy((eng._get_actions(), eng.get_char('ally').attrs, eng._revive_use_ledger))
        rng = random.getstate()
        keys = [key for key, _ in trainer.get_available_actions(eng, 'ally')]
        self.assertFalse(any(key.startswith(f"SKILL_{spell['index']}__") for key in keys))
        self.assertEqual(random.getstate(), rng)
        result = trainer.execute_training_action(eng, 'ally', f"SKILL_{spell['index']}__T0")
        self.assertFalse(result.committed)
        self.assertEqual(before, (eng._get_actions(), eng.get_char('ally').attrs, eng._revive_use_ledger))
        result = eng._execute_spell('ally', 'ally', spell)
        self.assertFalse(result.committed)
        self.assertEqual(eng._get_cooldown('ally', spell['index']), 0)

    def test_simulator_give_then_end_advances_once(self):
        classes, _ = battle_classes()
        eng = setup(classes.BattleEngine)
        commands = iter(['.a give ally', '.i end'])
        ai = type('AI', (), {'decide_action': lambda self, e, u: next(commands)})()
        eng.get_char('actor').inventory = [{'item': '蛋糕HP', 'count': 1}]
        logs = []
        with patch.object(eng, '_end_turn', wraps=eng._end_turn) as end:
            eng._run_character_ai_turn('actor', ai, logs.append)
        self.assertEqual(end.call_count, 1)
        self.assertTrue(any('食用' in line for line in logs))
        self.assertEqual(eng._get_my_actions('actor')['附加'], 2)

    def test_unknown_ai_command_ends_after_five_failures_without_charge(self):
        classes, _ = battle_classes()
        eng = setup(classes.BattleEngine)
        ai = type('AI', (), {'decide_action': lambda self, e, u: '.unknown'})()
        logs = []
        eng._run_character_ai_turn('actor', ai, logs.append)
        self.assertEqual(sum('未知动作' in line for line in logs), 5)
        self.assertEqual(eng._get_my_actions('actor'), {'主动': 2, '附加': 3})

    def test_zero_main_ends_without_asking_ai(self):
        classes, _ = battle_classes()
        eng = setup(classes.BattleEngine)
        eng._get_actions()['actor']['主动'] = 0
        self.assertEqual(trainer.affordable_actions(eng, 'actor', [('MOVE_TOWARD', '')]), [])
        ai = type('AI', (), {'decide_action': lambda self, e, u: self.fail()})()
        eng._run_character_ai_turn('actor', ai, lambda text: None)
        self.assertEqual(eng._get_my_actions('actor')['附加'], 3)

    def test_failed_training_action_is_suppressed(self):
        eng = setup()
        with patch.object(eng, '_next_move_coord', return_value=None):
            result = trainer.execute_training_action(eng, 'actor', 'MOVE_TOWARD')
            self.assertFalse(result.committed)
            self.assertNotIn('MOVE_TOWARD', [k for k, _ in trainer.get_available_actions(eng, 'actor')])
        self.assertEqual(eng._get_my_actions('actor'), {'主动': 2, '附加': 3})


class ReplayTests(unittest.TestCase):
    def test_lifesteal_reports_actual_heal(self):
        for hp, expected in [(82, 6), (88, 0)]:
            eng = setup()
            eng._set_combat_hp('other', hp)
            text = eng._apply_spell_damage('other', 'enemy', '26', 0, 0, 1.0)
            self.assertIn(f'吸血回复 {expected} HP', text)
            self.assertEqual(eng._get_combat_hp('other'), 88)

    def test_immediate_and_round_heals_are_emitted_once(self):
        eng = setup()
        eng._set_combat_hp('actor', 10)
        spell = next(s for s in eng.load_spells('ally') if any(e.get('type') == 8 for e in s['effects']))
        eng._execute_spell('ally', 'ally', spell)
        events = eng._drain_battle_events()
        self.assertTrue(any('回复HP' in line for line in events))
        self.assertEqual(eng._drain_battle_events(), [])
        eng._apply_zone_effects()
        self.assertTrue(eng._drain_battle_events())
        eng._apply_zone_effects()  # Same round/pulse must not repeat a heal.
        self.assertEqual(eng._drain_battle_events(), [])

    def test_round_tick_text_is_not_lost(self):
        eng = setup()
        il = eng._get_initiative()
        state = eng._get_state()
        state['activeIndex'] = len(il) - 1
        eng._set_state(state)
        with patch.object(eng, '_tick_down', return_value='战意值: 测试组 削弱至 4 层'), patch.object(eng, '_apply_zone_effects'):
            eng._end_turn(il[-1]['userId'])
        self.assertIn('战意值: 测试组 削弱至 4 层', eng._drain_battle_events())
        self.assertEqual(eng._drain_battle_events(), [])

    def test_failed_spell_and_failed_dodge_not_called_success(self):
        eng = setup()
        with patch.object(be, 'roll_d100', return_value=(90, '')):
            _, _, lines = eng._resolve_spell_dodge('actor', 'enemy', {}, {}, '1d6', 10, atk_rank=-1)
        self.assertIn('法术未命中', '\n'.join(lines))
        self.assertNotIn('闪避了法术', '\n'.join(lines))

    def test_failed_attack_and_failed_dodge_not_called_success(self):
        eng = setup()
        eng._ai_react_dodge_w['enemy'] = 100
        eng._ai_react_counter_w['enemy'] = 0
        with patch.object(be, 'roll_d100', return_value=(90, '')):
            _, _, lines = eng._coc7_attack('actor', 'enemy', '斗殴', 40, '1d4', 0, 0)
        self.assertIn('攻击未命中', '\n'.join(lines))
        self.assertNotIn('闪避成功', '\n'.join(lines))

    def test_untargetable_spell_mutual_hit_reports_one_hit(self):
        eng = setup()
        with patch.object(be, 'roll_d100', return_value=(50, '')), \
                patch.object(eng, '_is_untargetable', return_value=True):
            _, _, lines = eng._resolve_spell_counter('actor', 'enemy', {}, {}, '1d4', '斗殴', 80, atk_rank=1)
        self.assertNotIn('同时命中', '\n'.join(lines))
        self.assertIn('仅', '\n'.join(lines))


class HttpExecutionTests(unittest.TestCase):
    def setUp(self):
        self.srv = http_module()
        self.eng = setup(self.srv.PvPFullBattleEngine)
        self.eng._player_controllers = {u: [u] for u in ['actor', 'ally', 'other', 'enemy']}
        self.registry = patch.dict(self.srv._battles, {self.eng.group_id: self.eng})
        self.registry.start()
        self.addCleanup(self.registry.stop)
        self.client = self.srv.app.test_client()

    def post(self, action, player='actor'):
        return self.client.post(f'/api/pvp/{self.eng.group_id}/action',
                                json={'player_id': player, 'action': action})

    def test_manual_move_updates_coordinate_and_charges_once(self):
        response = self.post('.a m C2')
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.eng._unit_il_entry('actor', self.eng._get_initiative())['coord'], 'C2')
        self.assertEqual(self.eng._get_my_actions('actor'), {'主动': 2, '附加': 2})

    def test_invalid_manual_move_keeps_budget_and_actor(self):
        before = copy.deepcopy((self.eng._get_actions(), self.eng._get_state(), self.eng._get_map()))
        self.post('.a m M2')
        self.assertEqual(before, (self.eng._get_actions(), self.eng._get_state(), self.eng._get_map()))

    def test_manual_revive_with_no_target_does_not_charge(self):
        il = self.eng._get_initiative()
        self.eng._get_state()['activeIndex'] = next(i for i, e in enumerate(il) if e['userId'] == 'ally')
        spell = next(s for s in self.eng.load_spells('ally') if any(e.get('type') == 19 for e in s['effects']))
        before = copy.deepcopy((self.eng._get_actions(), self.eng.get_char('ally').attrs, self.eng._get_state()))
        response = self.post(f".s{spell['index']}", 'ally')
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertIn('复活目标', response.get_json()['output'])
        self.assertEqual(before, (self.eng._get_actions(), self.eng.get_char('ally').attrs, self.eng._get_state()))

    def test_http_ai_give_uses_bonus_and_then_ends(self):
        self.eng._player_controllers.pop('actor')
        self.eng.get_char('actor').inventory = [{'item': '蛋糕HP', 'count': 1}]
        name = self.eng.get_char('ally').name
        with patch.object(self.srv, '_ai_decide_action', side_effect=[('.a give ' + name, None), ('.i end', None)]):
            outputs = self.srv._run_ai_turns(self.eng)
        self.assertTrue(any('食用' in line for line in outputs))
        self.assertEqual(self.eng._get_my_actions('actor')['附加'], 2)

    def test_http_ai_bounds_rejected_spell_retries(self):
        self.eng._player_controllers.pop('actor')
        failed = be.ActionExecutionResult('技能未执行', False, 'no_target')
        with patch.object(self.srv, '_ai_decide_action', return_value=('.s2', None)), \
                patch.object(self.eng, '_execute_spell', return_value=failed) as cast:
            self.srv._run_ai_turns(self.eng)
        self.assertEqual(cast.call_count, 5)
        self.assertEqual(self.eng._get_my_actions('actor'), {'主动': 2, '附加': 3})

    def test_pending_reaction_charges_attack_only_once(self):
        with patch.object(be, 'roll_d100', return_value=(50, '')):
            response = self.post('.s0')
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()['needs_reaction'], response.get_json())
        self.assertEqual(self.eng._get_my_actions('actor')['主动'], 1)
        with patch.object(be, 'roll_d100', return_value=(90, '')), \
                patch.object(self.srv, 'roll_d100', return_value=(90, '')):
            resumed = self.client.post(f'/api/pvp/{self.eng.group_id}/react',
                                       json={'player_id': 'enemy', 'choice': 'dodge'})
        self.assertEqual(resumed.status_code, 200, resumed.get_json())
        self.assertEqual(self.eng._get_my_actions('actor')['主动'], 1)


if __name__ == '__main__':
    unittest.main()
