"""Y17 blood resource, chant, and weapon integration regressions."""
import logging.handlers
import random
import copy
import json
import pickle
import sys
import unittest
import ast
from pathlib import Path
from unittest.mock import patch

import ai_trainer as trainer
import battle_engine as be
import characters_data as cd
from ui_effects import EffectComments, runtime_character


def _enabled_y17():
    # Rules tests use the complete kit even when the browser has disabled skills.
    source = Path(cd.__file__).read_text(encoding='utf-8-sig')
    assignment = next(node for node in ast.parse(source).body
                      if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                      and node.targets[0].id == 'YEZHAOMING')
    data = EffectComments(source).restore(cd.YEZHAOMING, assignment)
    for spell in data['spells']:
        spell['_disabled'] = False
    return runtime_character(data)


def _setup(cls):
    random.seed(17)
    engine = cls()
    for uid, serial in (("blood", "Y17"), ("enemy", "Y1")):
        data = next(card for card in cd.ALL_CHARACTERS if card["serial"] == serial)
        cd.load_character_to_engine(engine, data, uid)
        engine.process_command(uid, ".hs")
    engine.setup_battle(["blood"], ["enemy"], "14x14", season_status=50)
    _place(engine, {"B7": "blood", "D7": "enemy"})
    return engine


def _place(engine, positions):
    by_uid = {uid: coord for coord, uid in positions.items()}
    initiative = engine._get_initiative()
    for entry in initiative:
        if entry["userId"] in by_uid:
            entry["coord"] = by_uid[entry["userId"]]
    engine._set_initiative(initiative)
    battle_map = engine._get_map()
    battle_map["occupants"] = dict(positions)
    engine._set_map(battle_map)


def _spell(engine, index):
    return next(spell for spell in engine.load_spells("blood") if spell["index"] == index)


class Y17BattleStateTests(unittest.TestCase):
    def setUp(self):
        trainer._ACTION_CACHE.clear()
        data = _enabled_y17()
        roster = [data if card['serial'] == 'Y17' else card for card in cd.ALL_CHARACTERS]
        for name, value in (('YEZHAOMING', data), ('ALL_CHARACTERS', roster)):
            patched = patch.object(cd, name, value)
            patched.start()
            self.addCleanup(patched.stop)

    def test_ai_priorities_follow_skill_kind_after_any_skill_is_disabled(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            for disabled in range(len(cd.YEZHAOMING['spells'])):
                for state, expected_kind in (('healthy', 'guard'), ('injured', 'store'), ('low_mp', 'cape')):
                    with self.subTest(engine=cls.__name__, disabled=disabled + 1, state=state):
                        data = copy.deepcopy(cd.YEZHAOMING)
                        data['spells'][disabled]['_disabled'] = True
                        data = runtime_character(data)
                        roster = [data if card['serial'] == 'Y17' else card for card in cd.ALL_CHARACTERS]
                        with patch.object(cd, 'ALL_CHARACTERS', roster):
                            engine = _setup(cls)
                        char = engine.get_char('blood')
                        if state == 'injured':
                            engine._set_combat_hp('blood', int(char.get_attr('体力上限') * .6))
                        elif state == 'low_mp':
                            char.set_attr('魔力', 4)
                        actions = trainer.affordable_actions(engine, 'blood', trainer.get_available_actions(engine, 'blood'))
                        self.assertTrue(actions)
                        if any(s.get('blood_kind') == expected_kind for s in data['spells']):
                            for key, _ in actions:
                                self.assertTrue(key.startswith('SKILL_'))
                                spell = _spell(engine, int(key.split('_')[1]))
                                self.assertEqual(engine._y17_spell_kind('blood', spell), expected_kind)

    def test_disabled_arcana_keeps_spear_identity_and_main_action(self):
        from ai_battle import AIController
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=cls.__name__):
                data = copy.deepcopy(cd.YEZHAOMING)
                data['spells'][1]['_disabled'] = True
                data = runtime_character(data)
                roster = [data if card['serial'] == 'Y17' else card for card in cd.ALL_CHARACTERS]
                with patch.object(cd, 'ALL_CHARACTERS', roster):
                    engine = _setup(cls)
                spear = next(s for s in engine.load_spells('blood') if engine._y17_spell_kind('blood', s) == 'spear')
                self.assertEqual(spear['index'], 12)
                self.assertTrue(be.action_committed(engine._execute_spell('blood', 'blood', spear)))
                engine.get_char('blood').set_str('_blood_spears', '[1]')
                _place(engine, {'B7': 'blood', 'H7': 'enemy'})
                key = 'SKILL_12_THROW__T0'
                actions = trainer.get_available_actions(engine, 'blood')
                self.assertIn(key, [key for key, _ in actions])
                self.assertEqual(trainer.action_slot_for(engine, 'blood', key), '主动')
                command = AIController._command_for_action(None, engine, 'blood', key)
                self.assertEqual(command, '.s12 mode=throw')
                self.assertEqual(engine._y17_ai_priority('blood', [(key, 'throw')]), [(key, 'throw')])
                self.assertTrue(be.action_committed(trainer.execute_action(engine, 'blood', key)))
                self.assertEqual(engine._y17_spears('blood'), [])

    def test_blood_ledgers_and_weapons_reset_on_each_engine_setup(self):
        for engine_type in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=engine_type.__name__):
                engine = _setup(engine_type)
                char = engine.get_char("blood")
                engine._consume_blood("blood", 700)
                char.set_attr("_blood_extracted", 900)
                char.set_str("_blood_spears", "[2]")
                engine.setup_battle(["blood"], ["enemy"], "14x14", season_status=50)
                self.assertEqual(engine._get_blood("blood"), 2000)
                self.assertEqual(char.get_attr("_blood_extracted"), 0)
                self.assertEqual(char.get_str("_blood_spears"), "[]")

    def test_blade_expiry_restores_attack_profile(self):
        engine = _setup(be.FullBattleEngine)
        engine._execute_spell("blood", "blood", _spell(engine, 5))
        self.assertEqual(engine._basic_attack_profile("blood"), ("1d8+2", 2))
        blade = engine._y17_blade("blood")
        blade["remainingRounds"] = 1
        engine._tick_down()
        self.assertIsNone(engine._y17_blade("blood"))
        self.assertIsNone(engine._basic_attack_profile("blood"))

    def test_blood_spear_uses_and_range_are_shared_by_combat_paths(self):
        engine = _setup(be.FullBattleEngine)
        engine._execute_spell("blood", "blood", _spell(engine, 12))
        self.assertEqual(engine._basic_attack_profile("blood"), ("1d8+6", 2))
        self.assertEqual(engine._get_attack_skill("blood"), ("斗殴", 48))
        self.assertEqual(engine._y17_spears("blood"), [3])
        engine._coc7_attack("blood", "enemy", "斗殴", 48, "1d8+6", 1, 0)
        self.assertEqual(engine._y17_spears("blood"), [2])

    def test_pvp_attempt_spends_spear_use_once_after_range_check(self):
        with patch.object(logging.handlers, "RotatingFileHandler", return_value=logging.NullHandler()):
            import battle_http_server as server
        engine = _setup(server.PvPFullBattleEngine)
        engine._execute_spell("blood", "blood", _spell(engine, 12))
        engine._player_controllers = {"enemy": ["human"]}
        with self.assertRaises(server.ReactionNeeded) as pending:
            engine._basic_attack("blood")
        self.assertEqual(engine._y17_spears("blood"), [2])
        self.assertEqual(pending.exception.data["dmg_dice"], "1d8+6")

    def test_unreachable_and_flying_rejections_do_not_spend_spear_use(self):
        engine = _setup(be.FullBattleEngine)
        engine._execute_spell("blood", "blood", _spell(engine, 12))
        with patch.object(engine, "_move_to_attack_range", return_value=(False, "B7")):
            engine._coc7_attack("blood", "enemy", "斗殴", 48, "1d8+6", 1, 0)
        self.assertEqual(engine._y17_spears("blood"), [3])
        with patch.object(engine, "_move_to_attack_range", return_value=(True, "B7")):
            with patch.object(engine, "_can_melee", return_value=(False, False)):
                engine._coc7_attack("blood", "enemy", "斗殴", 48, "1d8+6", 1, 0)
        self.assertEqual(engine._y17_spears("blood"), [3])

    def test_chant_cannot_be_advanced_with_an_additional_action(self):
        engine = _setup(be.FullBattleEngine)
        result = engine._execute_spell("blood", "enemy", _spell(engine, 2))
        self.assertTrue(be.action_committed(result))
        before = (engine.get_char("blood").attrs.copy(), engine._get_effects())
        rejected = engine._execute_ai_command("blood", ".a s2")
        self.assertFalse(be.action_committed(rejected))
        self.assertEqual(engine.get_char("blood").attrs, before[0])
        self.assertEqual(engine._get_effects(), before[1])
        self.assertEqual(engine._y17_chant("blood")["remainingRounds"], 2)

    def test_arcana_chant_spends_once_and_keeps_scaled_damage(self):
        engine = _setup(be.FullBattleEngine)
        caster = engine.get_char("blood")
        caster.set_attr("魔力上限", 120)
        caster.set_attr("魔力", 60)
        initial_blood, initial_mp = engine._get_blood("blood"), caster.get_attr("魔力")
        spell = _spell(engine, 2)
        spell["effects"][0]["可闪避性"] = 0
        spell["effects"][0]["可反击性"] = 0
        engine._set_combat_hp("enemy", 1000)
        cast = engine._execute_spell("blood", "enemy", spell)
        self.assertTrue(be.action_committed(cast))
        chant = engine._y17_chant("blood")
        self.assertIsNotNone(chant)
        self.assertEqual(chant["remainingRounds"], 2)
        self.assertEqual(engine._get_blood("blood"), initial_blood - 100)
        self.assertEqual(caster.get_attr("魔力"), initial_mp - 30)
        continued = engine._execute_spell("blood", "enemy", copy.deepcopy(spell))
        self.assertTrue(continued.endswith("\n"))
        self.assertIn("还需1次主动作", continued)
        self.assertEqual(engine._y17_chant("blood")["remainingRounds"], 1)
        engine._set_combat_hp("enemy", 1000)
        result = engine._execute_spell("blood", "enemy", _spell(engine, 2))
        self.assertIn("伤害", result)
        self.assertEqual(engine._get_combat_hp("enemy"), 760)
        self.assertEqual(caster.get_attr("魔力"), initial_mp - 30)
        self.assertEqual(engine._get_blood("blood"), 2900)
        self.assertIsNone(engine._y17_chant("blood"))

    def test_failed_arcana_preflight_is_atomic(self):
        engine = _setup(be.FullBattleEngine)
        engine.get_char("blood").set_attr("魔力", 4)
        initiative = [e for e in engine._get_initiative() if e["userId"] == "blood"]
        engine._set_initiative(initiative)
        before = (engine._get_blood("blood"), engine.get_char("blood").attrs.copy(), engine._get_effects())
        result = engine._execute_spell("blood", None, _spell(engine, 2))
        self.assertFalse(be.action_committed(result))
        self.assertEqual((engine._get_blood("blood"), engine.get_char("blood").attrs, engine._get_effects()), before)

    def test_chant_interrupt_requires_actual_hp_loss(self):
        engine = _setup(be.FullBattleEngine)
        spell = _spell(engine, 4)
        engine.get_char("blood").set_attr("魔力", 80)
        engine._set_combat_hp("blood", 28)
        engine._execute_spell("blood", "enemy", spell)
        hp = engine._get_combat_hp("blood")
        engine._set_combat_hp("blood", hp, source_dmg=3)
        self.assertIsNotNone(engine._y17_chant("blood"))
        self.assertEqual(engine.get_char("blood").get_attr("_blood_severe"), 0)
        engine._set_combat_hp("blood", hp - 10, source_dmg=10)
        self.assertIsNone(engine._y17_chant("blood"))
        self.assertEqual(engine.get_char("blood").get_attr("_blood_severe"), 1)
        self.assertEqual(engine._get_combat_hp("blood"), 1)
        self.assertEqual(engine.get_char("blood").get_attr("魔力"), 39)

    def test_sacrifice_death_followup_is_consumed_once(self):
        engine = _setup(be.FullBattleEngine)
        caster = engine.get_char("blood")
        caster.set_attr("魔力", 80)
        engine.get_char("enemy").set_attr("体力上限", 40)
        engine._set_combat_hp("enemy", 40)
        result = engine._execute_spell("blood", "enemy", _spell(engine, 4))
        self.assertTrue(be.action_committed(result))
        chant = engine._y17_chant("blood")
        self.assertIsNotNone(chant)
        engine._execute_spell("blood", "enemy", _spell(engine, 4))
        self.assertIsNotNone(engine._is_dying("blood"))
        self.assertEqual(engine._hp_safe("blood", 0), 0)
        self.assertEqual(engine._hp_safe("enemy", 0), 0)
        engine._set_combat_hp("enemy", 40)
        engine.get_char("blood").set_str("_blood_death_target", "enemy")
        engine._record_graveyard_entry("blood")
        self.assertEqual(engine._hp_safe("enemy", 0), 36)
        engine._record_graveyard_entry("blood")
        self.assertEqual(engine._hp_safe("enemy", 0), 36)

    def test_arcana_backlash_uses_saved_origin_after_caster_dies(self):
        engine = _setup(be.FullBattleEngine)
        effect = {"_blood_burst": True, "_blood_origin": "B7"}
        # The caster is removed from the initiative when backlash kills them;
        # the saved origin must still produce the burst geometry.
        engine._set_initiative([e for e in engine._get_initiative()
                                if e["userId"] != "blood"])
        self.assertEqual(engine._y17_geometry("blood", effect), ["enemy"])

    def test_blood_store_heals_only_eligible_allies_and_charges_all_costs(self):
        for engine_type in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=engine_type.__name__):
                engine = engine_type()
                for uid, serial in (("blood", "Y17"), ("ally", "Y3"), ("enemy", "Y1")):
                    data = next(card for card in cd.ALL_CHARACTERS if card["serial"] == serial)
                    cd.load_character_to_engine(engine, data, uid)
                    engine.process_command(uid, ".hs")
                engine.setup_battle(["blood", "ally"], ["enemy"], "14x14", season_status=50)
                engine._set_combat_hp("ally", 100)
                before_blood = engine._get_blood("blood")
                before_mp = engine.get_char("blood").get_attr("魔力")
                before_san = engine.get_char("blood").get_attr("理智")
                result = engine._execute_spell("blood", "ally", _spell(engine, 7))
                self.assertTrue(be.action_committed(result))
                self.assertEqual(engine._get_combat_hp("ally"), 104)
                self.assertEqual(engine._get_blood("blood"), before_blood - 500)
                self.assertEqual(engine.get_char("blood").get_attr("魔力"), before_mp - 2)
                self.assertEqual(engine.get_char("blood").get_attr("理智"), before_san - 1)

    def test_blood_store_rejects_severe_target_without_cost(self):
        engine = be.FullBattleEngine()
        for uid, serial in (("blood", "Y17"), ("ally", "Y3"), ("enemy", "Y1")):
            data = next(card for card in cd.ALL_CHARACTERS if card["serial"] == serial)
            cd.load_character_to_engine(engine, data, uid)
            engine.process_command(uid, ".hs")
        engine.setup_battle(["blood", "ally"], ["enemy"], "14x14", season_status=50)
        engine._set_combat_hp("ally", 40)
        engine.get_char("ally").set_attr("_blood_severe", 1)
        before = (engine._get_blood("blood"), engine.get_char("blood").attrs.copy(), engine._get_effects())
        result = engine._execute_spell("blood", "ally", _spell(engine, 7))
        self.assertFalse(be.action_committed(result))
        self.assertEqual((engine._get_blood("blood"), engine.get_char("blood").attrs, engine._get_effects()), before)

    def test_training_store_action_selects_wounded_ally(self):
        engine = be.FastBattleEngine()
        for uid, serial in (("blood", "Y17"), ("ally", "Y3"), ("enemy", "Y1")):
            data = next(card for card in cd.ALL_CHARACTERS if card["serial"] == serial)
            cd.load_character_to_engine(engine, data, uid)
            engine.process_command(uid, ".hs")
        engine.setup_battle(["blood", "ally"], ["enemy"], "14x14", season_status=50)
        engine._set_combat_hp("ally", 100)
        before = engine._get_combat_hp("ally")
        spell_action = next(key for key, _ in trainer.get_available_actions(engine, "blood")
                            if key.startswith("SKILL_7__"))
        result = trainer.execute_training_action(engine, "blood", spell_action)
        self.assertTrue(be.action_committed(result))
        self.assertEqual(engine._get_combat_hp("ally"), before + 16)
        self.assertEqual(engine._get_combat_hp("enemy"), engine.get_char("enemy").get_attr("体力上限"))
        self.assertEqual(engine._get_blood("blood"), 0)

    def test_ai_selects_blood_war_against_multiple_enemies(self):
        engine = be.FastBattleEngine()
        for uid, serial in (("blood", "Y17"), ("enemy", "Y1"), ("enemy2", "Y2")):
            data = next(card for card in cd.ALL_CHARACTERS if card["serial"] == serial)
            cd.load_character_to_engine(engine, data, uid)
            engine.process_command(uid, ".hs")
        engine.setup_battle(["blood"], ["enemy", "enemy2"], "14x14", season_status=50)
        _place(engine, {"B7": "blood", "D7": "enemy", "E7": "enemy2"})
        engine.get_char("blood").set_attr("魔力", 91)
        engine._set_combat_hp("blood", engine.get_char("blood").get_attr("体力上限"))
        engine._get_actions()["blood"]["附加"] = 0
        actions = [("SKILL_6__T0", "污血战争"), ("SKILL_3__T0", "凝血护甲"), ("BASIC_ATTACK__T0", "普攻")]
        prioritized = engine._y17_ai_priority("blood", actions)
        self.assertEqual(prioritized, [("SKILL_6__T0", "污血战争")])

    def test_ai_prioritizes_treatment_and_mana_conversion_when_useful(self):
        engine = _setup(be.FastBattleEngine)
        maximum = engine.get_char("blood").get_attr("体力上限")
        engine._set_combat_hp("blood", int(maximum * .6))
        actions = trainer.get_available_actions(engine, "blood")
        prioritized = trainer.affordable_actions(engine, "blood", actions)
        self.assertTrue(prioritized)
        self.assertTrue(all(key.startswith("SKILL_7__") for key, _ in prioritized))

        engine._set_combat_hp("blood", engine.get_char("blood").get_attr("体力上限"))
        engine.get_char("blood").set_attr("魔力", 4)
        actions = trainer.get_available_actions(engine, "blood")
        prioritized = trainer.affordable_actions(engine, "blood", actions)
        self.assertTrue(prioritized)
        self.assertTrue(all(key.startswith("SKILL_10__") for key, _ in prioritized))

    def test_ai_candidate_generation_does_not_roll_or_mutate_blood(self):
        for engine_type in (be.FullBattleEngine, be.FastBattleEngine):
            with self.subTest(engine=engine_type.__name__):
                engine = _setup(engine_type)
                char = engine.get_char("blood")
                before = (char.attrs.copy(), char.str_attrs.copy(), random.getstate())
                trainer.get_available_actions(engine, "blood")
                self.assertEqual(char.attrs, before[0])
                self.assertEqual(char.str_attrs, before[1])
                self.assertEqual(random.getstate(), before[2])

    def test_bow_uses_archery_range_and_avoids_flying_melee_penalty(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            engine = _setup(cls)
            cast = dict(_spell(engine, 5), _blood_options={'form': 'bow'})
            self.assertTrue(be.action_committed(engine._execute_spell('blood', 'blood', cast)))
            self.assertEqual(engine._get_attack_skill('blood'), ('弓术', engine.get_char('blood').get_attr('弓术')))
            self.assertEqual(engine._basic_attack_profile('blood'), ('1d8+2', 8))
            _place(engine, {'B7': 'blood', 'H7': 'enemy'})
            attack = engine._fast_coc7_attack if cls is be.FastBattleEngine else engine._coc7_attack
            with patch.object(engine, '_can_melee', side_effect=AssertionError('ranged weapon tested as melee')):
                attack('blood', 'enemy', '弓术', 80, '1d8+2', 1, 0)
            before = (engine._get_blood('blood'), copy.deepcopy(engine._get_effects()))
            self.assertFalse(be.action_committed(engine._execute_spell('blood', 'enemy', _spell(engine, 11))))
            self.assertEqual((engine._get_blood('blood'), engine._get_effects()), before)

    def test_custom_archery_receives_and_reverses_standard_transform(self):
        engine = be.FullBattleEngine()
        cd.load_character_to_engine(engine, cd.YEZHAOMING, 'blood')
        base = engine.get_char('blood').get_attr('弓术')
        engine.process_command('blood', '.hs')
        self.assertEqual(engine.get_char('blood').get_attr('弓术'), min(80, base + 20))
        engine.process_command('blood', '.unh')
        self.assertEqual(engine.get_char('blood').get_attr('弓术'), base)

    def test_weapon_switch_replaces_previous_form_and_charges_material(self):
        engine = _setup(be.FullBattleEngine)
        for form in ('bow', 'sword'):
            engine._execute_spell('blood', 'blood', dict(_spell(engine, 5), _blood_options={'form': form}))
        self.assertEqual(engine._get_blood('blood'), 1800)
        self.assertEqual(sum(bool(e.get('bloodBlade')) for e in engine._get_effects()), 1)
        self.assertFalse(engine._y17_ranged('blood'))

    def test_small_wounds_and_minor_mana_deficit_do_not_take_priority(self):
        engine = _setup(be.FastBattleEngine)
        char = engine.get_char('blood')
        engine._set_combat_hp('blood', char.get_attr('体力上限') - 1)
        char.set_attr('魔力', char.get_attr('魔力上限') - 8)
        actions = trainer.affordable_actions(engine, 'blood', trainer.get_available_actions(engine, 'blood'))
        self.assertTrue(actions)
        self.assertFalse(any(k.startswith(('SKILL_7__', 'SKILL_10__')) for k, _ in actions))

    def test_guard_persists_and_duplicate_cast_is_atomic(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            engine = _setup(cls)
            guard = dict(_spell(engine, 3), _blood_options={'blood': 900})
            engine._execute_spell('blood', 'blood', guard)
            for _ in range(2):
                hp = engine._hp_safe('blood')
                engine._apply_spell_damage('enemy', 'blood', '9', 0, 0)
                self.assertEqual(engine._hp_safe('blood'), hp - 3)
            before = copy.deepcopy((engine.get_char('blood').attrs, engine.get_char('blood').str_attrs, engine._get_effects()))
            self.assertFalse(be.action_committed(engine._execute_spell('blood', 'blood', guard)))
            self.assertEqual((engine.get_char('blood').attrs, engine.get_char('blood').str_attrs, engine._get_effects()), before)

    def test_chant_survives_round_tick_and_serialization(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            engine = _setup(cls)
            engine._execute_spell('blood', 'enemy', _spell(engine, 2))
            engine._tick_down()
            self.assertEqual(engine._y17_chant('blood')['remainingRounds'], 2)
            restored = pickle.loads(pickle.dumps(engine))
            paid = (restored._get_blood('blood'), restored.get_char('blood').get_attr('魔力'))
            restored._execute_spell('blood', 'enemy', _spell(restored, 2))
            self.assertEqual(restored._y17_chant('blood')['remainingRounds'], 1)
            self.assertEqual((restored._get_blood('blood'), restored.get_char('blood').get_attr('魔力')), paid)

    def test_invalid_explicit_enemy_target_does_not_redirect_or_spend(self):
        engine = _setup(be.FullBattleEngine)
        before = copy.deepcopy((engine.get_char('blood').attrs, engine.get_char('blood').str_attrs))
        result = engine._execute_spell('blood', 'missing', _spell(engine, 2))
        self.assertFalse(be.action_committed(result))
        self.assertEqual((engine.get_char('blood').attrs, engine.get_char('blood').str_attrs), before)

    def test_actual_lethal_arcana_backlash_uses_saved_origin(self):
        engine = _setup(be.FullBattleEngine)
        engine._consume_blood('blood', 2000)
        engine._set_combat_hp('blood', 1)
        engine.get_char('blood').set_attr('魔力', 2)
        result = engine._execute_spell('blood', 'enemy', _spell(engine, 2))
        self.assertTrue(be.action_committed(result))
        self.assertIn('反噬爆炸', result)

    def test_sacrifice_target_leaving_range_does_not_force_dying(self):
        engine = _setup(be.FullBattleEngine)
        engine._execute_spell('blood', 'enemy', _spell(engine, 4))
        _place(engine, {'A1': 'blood', 'N14': 'enemy'})
        with patch.object(engine, '_range_override_snapshot', 0, create=True):
            engine._execute_spell('blood', 'enemy', _spell(engine, 4))
        self.assertIsNone(engine._is_dying('blood'))
        self.assertIsNone(engine._y17_chant('blood'))

    def test_block_damage_cannot_transfer_to_next_spear(self):
        engine = _setup(be.FullBattleEngine)
        engine.get_char('blood').set_str('_blood_spears', '[1, 3]')
        engine._y17_weapon_used('blood')
        engine._y17_weapon_used('blood', blocked=True)
        self.assertEqual(engine._y17_spears('blood'), [3])
        engine._y17_weapon_used('blood')
        engine._y17_weapon_used('blood', blocked=True)
        engine._y17_weapon_used('blood', blocked=True)
        self.assertEqual(engine._y17_spears('blood'), [1])

    def test_successful_counter_uses_the_last_spear_before_it_breaks(self):
        engine = _setup(be.FullBattleEngine)
        engine.get_char('blood').set_str('_blood_spears', '[1]')
        self.assertEqual(engine._counter_damage_dice('blood', '斗殴'), '1d8+6')
        self.assertEqual(engine._y17_spears('blood'), [])

    def test_postmortem_harvest_survives_resurrection(self):
        engine = _setup(be.FullBattleEngine)
        cd.load_character_to_engine(engine, cd.HUANHUANUAN, 'rescuer')
        engine.process_command('rescuer', '.hs')
        engine.setup_battle(['blood'], ['enemy', 'rescuer'], '14x14', season_status=50)
        engine._record_graveyard_entry('enemy')
        grave = engine._graveyard['enemy']
        engine._set_combat_hp('enemy', 0)
        engine._y17_harvest('blood', 'enemy', 700)
        self.assertIsNotNone(engine._restore_graveyard_unit('rescuer', grave, {'复活回复比例': .4}))
        self.assertGreater(engine._hp_safe('enemy'), 0)
        engine._y17_harvest('blood', 'enemy', 700)
        self.assertEqual(engine.get_char('enemy').get_attr('_blood_extracted'), 1000)
        self.assertEqual(engine._get_blood('blood'), 3000)

    def test_pvp_loader_includes_blood_rules_and_chant_costs(self):
        with patch.object(logging.handlers, 'RotatingFileHandler', return_value=logging.NullHandler()):
            import battle_http_server as server
        engine = server.PvPFullBattleEngine()
        server.merge_character(engine, 'blood', {'name': '夜诏明', 'serial': 'Y17', 'attrs': {}})
        self.assertEqual(engine.get_char('blood').get_attr('储血ml'), 2000)
        self.assertEqual(engine._y17_rules('blood')['ball_heal'], 4)
        self.assertEqual(engine._y17_spell_kind('blood', _spell(engine, 2)), 'arcana')

    def test_pvp_multislash_resumes_with_one_cost_and_one_material_payment(self):
        with patch.object(logging.handlers, 'RotatingFileHandler', return_value=logging.NullHandler()):
            import battle_http_server as server
        engine = _setup(server.PvPFullBattleEngine)
        engine._execute_spell('blood', 'blood', _spell(engine, 5))
        engine._player_controllers = {'enemy': ['human']}
        engine._set_combat_hp('enemy', 1000)
        char = engine.get_char('blood')
        char.set_attr('魔力', 100)
        spell = dict(_spell(engine, 11), _blood_options={'blood': 1500})
        with patch.object(engine, '_roll_spell_success_bp', return_value=(20, '')):
            with self.assertRaises(server.ReactionNeeded) as event:
                engine._execute_spell('blood', 'enemy', spell)
            state = json.loads(json.dumps(event.exception.data))
            paid = (engine._get_blood('blood'), char.get_attr('魔力'))
            reactions = 0
            while True:
                reactions += 1
                try:
                    engine.resolve_spell_reaction(state, 'dodge')
                    break
                except server.ReactionNeeded as event:
                    state = json.loads(json.dumps(event.data))
            self.assertEqual(reactions, 2)
            self.assertEqual(char.get_attr('魔力'), paid[1])
            self.assertLessEqual(engine._get_blood('blood') - paid[0], 200)
            audit = json.loads(char.get_str('_blood_audit'))
            self.assertEqual(audit['slash_spent_blood'], 1500)

    def test_war_chain_is_iterative_and_terminates_from_finite_blood(self):
        engine = _setup(be.FullBattleEngine)
        engine._add_blood('blood', 3000 * 1100)
        spell = dict(_spell(engine, 6), _blood_chain=True)
        # A fully absorbed wave has no corpse rewards; sufficient blood must
        # still terminate beyond Python's recursion depth without a stack error.
        with patch.object(engine, '_apply_target_effect', return_value=''):
            engine._execute_spell_plan('blood', 'enemy', spell, [(0, 'enemy')], False)
        self.assertLess(engine._get_blood('blood'), 3000)
        self.assertEqual(spell['_chains'], 1100)

    def test_slash_harvest_cap_is_shared_by_all_segments_and_shields_give_none(self):
        engine = _setup(be.FullBattleEngine)
        spell = dict(_spell(engine, 11), blood_kind='slash')
        engine._set_combat_hp('enemy', 1000)
        initial = engine._get_blood('blood')
        for _ in range(3):
            engine._apply_spell_damage('blood', 'enemy', '20', 0, 0, spell=spell)
        self.assertEqual(engine._get_blood('blood'), initial + 200)
        self.assertEqual(engine.get_char('enemy').get_attr('_blood_extracted'), 200)
        engine._set_effects(engine._get_effects() + [dict(type='shield', targetUserId='enemy', value=1000, remainingRounds=5)])
        second = dict(_spell(engine, 11), blood_kind='slash')
        hp = engine._hp_safe('blood')
        engine._apply_spell_damage('blood', 'enemy', '20', 0, 0, spell=second, lifesteal_ratio=1)
        self.assertEqual(engine._get_blood('blood'), initial + 200)
        self.assertEqual(engine._hp_safe('blood'), hp)

    def test_two_casters_with_same_chant_sequence_continue_independently(self):
        engine = _setup(be.FullBattleEngine)
        cd.load_character_to_engine(engine, cd.YEZHAOMING, 'blood2')
        engine.process_command('blood2', '.hs')
        engine.setup_battle(['blood', 'blood2'], ['enemy'], '14x14', season_status=50)
        _place(engine, {'B7': 'blood', 'C7': 'blood2', 'D7': 'enemy'})
        for uid in ('blood', 'blood2'):
            spell = next(s for s in engine.load_spells(uid) if s['index'] == 2)
            engine._execute_spell(uid, 'enemy', spell)
        engine._execute_spell('blood2', 'enemy', _spell(engine, 2))
        self.assertEqual(engine._y17_chant('blood')['remainingRounds'], 2)
        self.assertEqual(engine._y17_chant('blood2')['remainingRounds'], 1)

    def test_ai_cancels_when_ally_enters_paid_chant_lane(self):
        engine = _setup(be.FullBattleEngine)
        cd.load_character_to_engine(engine, cd.LINGNIU, 'ally')
        engine.process_command('ally', '.hs')
        engine.setup_battle(['blood', 'ally'], ['enemy'], '14x14', season_status=50)
        _place(engine, {'B7': 'blood', 'A1': 'ally', 'H7': 'enemy'})
        engine._execute_spell('blood', 'enemy', _spell(engine, 2))
        _place(engine, {'B7': 'blood', 'E7': 'ally', 'H7': 'enemy'})
        paid = (engine._get_blood('blood'), engine.get_char('blood').get_attr('魔力'))
        options = engine._y17_ai_options('blood', _spell(engine, 2))
        self.assertEqual(options, {'mode': 'cancel'})
        engine._execute_spell('blood', 'enemy', dict(_spell(engine, 2), _blood_options=options))
        self.assertIsNone(engine._y17_chant('blood'))
        self.assertEqual((engine._get_blood('blood'), engine.get_char('blood').get_attr('魔力')), paid)

    def test_reaction_policy_prefers_current_stronger_skill_and_preserves_others(self):
        engine = _setup(be.FullBattleEngine)
        dodge, counter = engine._y17_reaction_weights('blood', 10, 10, 80, 48)
        self.assertGreater(dodge, counter)
        self.assertGreater(counter, 0)
        dodge, counter = engine._y17_reaction_weights('blood', 10, 10, 40, 80)
        self.assertGreater(counter, dodge)
        dodge, counter = engine._y17_reaction_weights('blood', 10, 10, 80, 48, attack_rank=0)
        self.assertGreater(counter, dodge)
        self.assertEqual(engine._y17_reaction_weights('enemy', 10, 20, 80, 48), (10, 20))

    def test_enemy_death_is_not_misreported_as_friendly_damage(self):
        engine = _setup(be.FullBattleEngine)
        engine._set_combat_hp('enemy', 10)
        spell = dict(_spell(engine, 11), blood_kind='slash')
        engine._apply_spell_damage('blood', 'enemy', '100', 0, 0, spell=spell)
        self.assertEqual(json.loads(engine.get_char('blood').get_str('_blood_audit')).get('friendly_damage', 0), 0)

    def test_explicit_moon_phase_selects_damage_or_actual_lifesteal(self):
        for cls in (be.FullBattleEngine, be.FastBattleEngine):
            engine = _setup(cls)
            engine._execute_spell('blood', 'blood', _spell(engine, 5))
            engine._set_combat_hp('blood', 40)
            engine._set_combat_hp('enemy', 100)
            spell = dict(_spell(engine, 11), _blood_options={'moon': 8})
            spell['effects'] = copy.deepcopy(spell['effects'])
            spell['effects'][0].update(可闪避性=0, 可反击性=0)
            with patch.object(engine, '_roll_spell_success_bp', return_value=(20, '')):
                result = engine._execute_spell('blood', 'enemy', spell)
            self.assertIn('盈凸月', result)
            self.assertEqual(engine._hp_safe('enemy'), 90)
            self.assertEqual(engine._hp_safe('blood'), 50)

    def test_charged_arcana_keeps_bonus_and_cannot_shorten_three_actions(self):
        engine = _setup(be.FullBattleEngine)
        max_hp = engine.get_char('blood').get_attr('体力上限')
        engine._execute_spell('blood', 'enemy', _spell(engine, 2))
        chant = engine._y17_chant('blood')
        self.assertEqual(chant['remainingRounds'], 2)
        self.assertEqual(chant['stored_spell']['effects'][0]['成功率奖惩骰'], 'b')
        self.assertEqual(engine.get_char('blood').get_attr('体力上限'), max_hp)

    def test_spell_preflight_respects_same_global_range_setting_as_execution(self):
        engine = _setup(be.FullBattleEngine)
        engine._execute_spell('blood', 'blood', _spell(engine, 5))
        _place(engine, {'B7': 'blood', 'H7': 'enemy'})
        with patch.object(engine, '_range_override_snapshot', 0, create=True):
            self.assertTrue(engine._y17_preflight('blood', _spell(engine, 11)))
        with patch.object(engine, '_range_override_snapshot', 1, create=True):
            self.assertFalse(engine._y17_preflight('blood', _spell(engine, 11)))


if __name__ == "__main__":
    unittest.main()
