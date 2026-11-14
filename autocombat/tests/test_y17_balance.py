"""Y17 blood resource, chant, and weapon integration regressions."""
import logging.handlers
import random
import copy
import sys
import unittest
from unittest.mock import patch

import ai_trainer as trainer
import battle_engine as be
import characters_data as cd


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
        self.assertEqual(engine.get_char("blood").get_attr("魔力"), 40)

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
                self.assertEqual(engine._get_combat_hp("ally"), 101)
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
        self.assertEqual(engine._get_combat_hp("ally"), before + 1)
        self.assertEqual(engine._get_combat_hp("enemy"), engine.get_char("enemy").get_attr("体力上限"))
        self.assertEqual(engine._get_blood("blood"), 1500)

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
        actions = [("SKILL_6__T0", "污血战争"), ("SKILL_3__T0", "凝血护甲"), ("BASIC_ATTACK__T0", "普攻")]
        prioritized = engine._y17_ai_priority("blood", actions)
        self.assertEqual(prioritized, [("SKILL_6__T0", "污血战争")])

    def test_ai_prioritizes_treatment_and_mana_conversion_when_useful(self):
        engine = _setup(be.FastBattleEngine)
        engine._set_combat_hp("blood", 40)
        actions = trainer.get_available_actions(engine, "blood")
        prioritized = trainer.affordable_actions(engine, "blood", actions)
        self.assertTrue(prioritized)
        self.assertTrue(all(key.startswith("SKILL_7__") for key, _ in prioritized))

        engine._set_combat_hp("blood", engine.get_char("blood").get_attr("体力上限"))
        engine.get_char("blood").set_attr("魔力", engine.get_char("blood").get_attr("魔力上限") - 8)
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


if __name__ == "__main__":
    unittest.main()
