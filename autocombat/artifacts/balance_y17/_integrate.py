from pathlib import Path
import ast
import pprint

ROOT = Path(__file__).resolve().parents[2]
p = ROOT / 'battle_engine.py'
s = p.read_text(encoding='utf-8')
start = s.index('    def _is_y17(self, uid):')
end = s.index('    def _get_blood(self, uid):', start)
s = s[:start] + (Path(__file__).with_name('_methods.txt')).read_text(encoding='utf-8') + '\n' + s[end:]
def rep(old, new, count=1):
    global s
    assert s.count(old) >= count, old[:100]
    s = s.replace(old, new, count)

# A dedicated transaction for Y17 avoids changing the other characters' costs.
start = s.index('        if self._is_y17(caster_id):', s.index('    def _execute_spell(self,'))
end = s.index('        if self._is_y14(caster_id):', start)
s = s[:start] + '        if self._is_y17(caster_id):\n            return self._y17_execute(caster_id, target_id, spell)\n' + s[end:]
rep("        return getattr(char, '_blood_ml', 0) or 0", "        return char.get_attr('储血ml', getattr(char, '_blood_ml', 0)) or 0")
rep('        char._blood_ml = cur + amount', "        char._blood_ml = cur + amount\n        char.set_attr('储血ml', cur + amount)")
rep('        if cur < amount: return False', '        if amount < 0 or cur < amount: return False')
rep('        char._blood_ml = cur - amount', "        char._blood_ml = cur - amount\n        char.set_attr('储血ml', cur - amount)")

# Geometric targets must survive PvP pause/resume as per-cast effect fields.
rep('        codes = self._parse_target_codes(eff)', "        if eff.get('_blood_direction') or eff.get('_blood_burst'):\n            return self._y17_geometry(caster_id, eff)\n        codes = self._parse_target_codes(eff)")
# Only use live targets from a prebuilt plan. A dead caster may finish a paid wave.
rep("                eff = spell['effects'][eff_idx]\n                ct = eff['type']", "                eff = spell['effects'][eff_idx]\n                ct = eff['type']\n                if ct == 1 and self._hp_safe(tid, 0) <= 0:\n                    idx += 1\n                    continue")
rep("            if self._get_blood(caster_id) >= 3000 and spell.get('_chains', 0) < 4:", "            if self._get_blood(caster_id) >= self._y17_rules(caster_id)['chain_ml']:")
rep("                self._consume_blood(caster_id, 3000)", "                self._consume_blood(caster_id, self._y17_rules(caster_id)['chain_ml'])")
# Avoid recursion: append further waves to this same iterative plan.
rep("                return self._execute_spell_plan(caster_id, target_id, spell, wave, False,\n                    self._spell_plan_output + '  消耗3000ml血液，污血冲击再次发动！\\n')", "                if wave:\n                    return self._execute_spell_plan(caster_id, target_id, spell, wave, False,\n                        self._spell_plan_output + '  消耗3000ml血液，污血冲击再次发动！\\n')")
rep("        if not plan or not spell or not caster_id:\n            return ''", "        if not spell or not caster_id:\n            return ''")

# Shared equipment profile: all range checks and training use the same source.
rep('    def _y14_attack_profile(self, uid):', "    def _y14_attack_profile(self, uid):\n        return self._basic_attack_profile(uid)\n\n    def _basic_attack_profile(self, uid):\n        profile = self._y17_attack_profile(uid)\n        if profile:\n            return profile")
old="        if self._is_y17(uid) and any(e.get('spellIndex') == 5\n                and e.get('sourceUserId') == self._resolve_uid(uid)\n                and e.get('remainingRounds', 0) > 0 for e in self._get_effects()):\n            return '1d8+2'\n"
rep(old, '')
rep("        weapon = char.get_str('_weapon_name') or ''", "        if self._y17_attack_profile(uid):\n            return '斗殴', char.get_attr('斗殴', 0)\n        weapon = char.get_str('_weapon_name') or ''")
# Basic weapon durability is spent after range validation, before rolling, even on a miss.
needle='        # ── 飞行近战限制 ──'
rep(needle, '        self._y17_weapon_used(atk_uid)\n' + needle, 2)

# Interrupt only on real HP loss, in all attack paths, and clear curse on rescue.
rep("        # Track per-team last-death overflow for battle-end tiebreaker", "        if old_hp is not None and new_hp < old_hp and source_dmg > 0 and self._is_y17(base):\n            self._check_chant_interrupt(base)\n        if old_hp is not None and new_hp > old_hp and new_hp > 0 and self._is_y17(base):\n            self.get_char(base).set_str('_blood_death_target', '')\n\n        # Track per-team last-death overflow for battle-end tiebreaker")
rep("        # Chant interrupt: if target has an interruptible chant, break it\n        ci_msgs = self._check_chant_interrupt(target_id)\n        for m in ci_msgs: out += f'  {m}\\n'", "        # Y17 is interrupted only by actual HP loss in _set_combat_hp.\n        if not self._is_y17(target_id):\n            for m in self._check_chant_interrupt(target_id):\n                out += f'  {m}\\n'")
rep("        ci_msgs_ba = self._check_chant_interrupt(loser_uid)", "        ci_msgs_ba = [] if self._is_y17(loser_uid) else self._check_chant_interrupt(loser_uid)")
rep("                hp_pct = e.get('interrupt_hp_pct', 0) or 0", "                hp_pct = 90 if e.get('bloodChant') else (e.get('interrupt_hp_pct', 0) or 0)")
rep("                mp_pct = e.get('interrupt_mp_pct', 0) or 0", "                mp_pct = 50 if e.get('bloodChant') else (e.get('interrupt_mp_pct', 0) or 0)\n                e['remainingRounds'] = 0\n                if e.get('bloodChant'):\n                    char.set_attr('_blood_severe', 1)")
rep('hp_loss = max(1, cur_hp * hp_pct // 100)', 'hp_loss = max(1, math.ceil(cur_hp * hp_pct / 100))')
rep('mp_loss = max(1, cur_mp * mp_pct // 100)', 'mp_loss = max(1, math.ceil(cur_mp * mp_pct / 100))')
rep("        grave[base] = {", "        self._y17_death(base)\n        grave[base] = {")

# All active actions, reactions and implicit movement respect blood unconsciousness.
for signature in ('def _can_move(self, uid):', 'def _can_basic_attack(self, uid):',
                  'def _can_cast_active_skill(self, uid):', 'def _can_take_active_action(self, uid):',
                  'def _can_react(self, uid):'):
    begin=s.index('    '+signature)
    point=s.index('        resolved =',begin) if signature!='def _can_take_active_action(self, uid):' else s.index("        return not self._has_status",begin)
    s=s[:point]+"        if self._y17_disabled(uid):\n            return False\n"+s[point:]
# Fast's override also consults the base condition.
rep('    def _can_basic_attack(self, uid, char=None):', '    def _can_basic_attack(self, uid, char=None):\n        if self._y17_disabled(uid):\n            return False')

# New battle, not revive, resets source quotas and transient weapon/curse state.
rep("        for uid in team_a + team_b:\n            self.get_char(uid)._once_consumed = set()", "        for uid in team_a + team_b:\n            ch = self.get_char(uid)\n            ch._once_consumed = set()\n            ch.set_attr('_blood_extracted', 0)\n            ch.set_attr('_blood_war_rewarded', 0)\n            ch.set_attr('_blood_unconscious', 0)\n            ch.set_attr('_blood_severe', 0)\n            ch.set_str('_blood_death_target', '')\n            ch.set_str('_blood_spears', '[]')\n            if self._is_y17(uid):\n                ch.set_attr('储血ml', ch.get_attr('_blood_initial_ml', 2000))")
# Revival must not mint blood from the dead snapshot.
rep("            'once_consumed': list(getattr(char, '_once_consumed', set()) or []),", "            'once_consumed': list(getattr(char, '_once_consumed', set()) or []),")

# War kill rewards and moon drains use the paid spell also on the HTTP reaction path.
rep("        # On-kill effects: trigger if target died", "        if old_target_hp > 0 and eff_dmg > 0:\n            out += self._y17_war_kill(caster_id, target_id, spell)\n\n        # On-kill effects: trigger if target died")
# A moon drain is a separate damage component, with its own shield/armour settlement.
rep("        # 月相吸取（moon_phase）：目标实际受伤后等量治疗施法者（上限 体力上限）", "        if moon_drain > 0 and spell and spell.get('blood_kind') == 'slash':\n            if eff_dmg > 0 and self._hp_safe(target_id, 0) > 0:\n                drain_out = self._apply_spell_damage(caster_id, target_id, str(moon_drain), 0, 0,\n                    spell_name=spell_name + '·吸取', spell_index=spell_index)\n                actual_drain = max(0, old_target_hp - min(old_target_hp, eff_dmg) - self._hp_safe(target_id, 0))\n                out += drain_out\n            else:\n                actual_drain = 0\n            if self._hp_safe(caster_id, 0) > 0:\n                self._set_combat_hp(caster_id, min(self.get_char(caster_id).get_attr('体力上限'),\n                    self._hp_safe(caster_id) + actual_drain))\n            moon_drain = 0\n\n        # 月相吸取（moon_phase）：目标实际受伤后等量治疗施法者（上限 体力上限）")
# Full/waning drain-only phases: use actual dealt damage for healing, not a second roll.
rep("            eff['_moon_drain'] = roll_dice(phase_dice) if phase_drain else 0", "            eff['_moon_drain'] = (roll_dice('1d8') if phase_name == '亏凸月' else\n                5 if phase_name == '上弦月' else 0)\n            if phase_name in ('满月', '盈凸月'):\n                ls = 1.0\n                eff['吸血比例'] = '1'", 2)
# Generic lifesteal's real-damage clamp is essential for Y17 drain-only phases.
rep('            heal = int(dmg_val * lifesteal_ratio)', '            heal = int((min(eff_dmg, old_target_hp) if spell and spell.get("blood_kind") == "slash" else dmg_val) * lifesteal_ratio)')
rep('        if lifesteal_ratio > 0 and eff_dmg > 0:', '        if lifesteal_ratio > 0 and eff_dmg > 0 and self._hp_safe(caster_id, 0) > 0:')

# Command options: preserve old syntax and keep per-cast copies local.
rep("        match = re.fullmatch(r'\\.(a )?s(\\d+)', cmd)", "        match = re.fullmatch(r'\\.(a )?s(\\d+)(?:\\s+(.*))?', cmd)")
rep("        sn = int(match.group(2))", "        sn = int(match.group(2))\n        cast_args = match.group(3) or ''")
rep("        result = (self._additional_action(uid, f's{sn}') if match.group(1)\n                  else self._use_skill(uid, sn, ''))", "        result = (self._additional_action(uid, f's{sn} {cast_args}'.strip()) if match.group(1)\n                  else self._use_skill(uid, sn, cast_args))")
# Direct .sN / .a sN API paths (base and Full).
rep("        target_id = self._smart_target(uid, spell)\n        return self._execute_spell(uid, target_id, spell)", "        if self._is_y17(uid):\n            try:\n                spell = dict(spell, _blood_options=self._y17_options(args))\n            except ValueError as exc:\n                return ActionExecutionResult(str(exc), False, 'invalid_options')\n        target_id = self._smart_target(uid, spell)\n        return self._execute_spell(uid, target_id, spell)")
rep("        target = self._smart_target(uid, spell)\n        return self._execute_spell(uid, target, spell)", "        if self._is_y17(uid):\n            try:\n                spell = dict(spell, _blood_options=self._y17_options(args))\n            except ValueError as exc:\n                return ActionExecutionResult(str(exc), False, 'invalid_options')\n        target = self._smart_target(uid, spell)\n        return self._execute_spell(uid, target, spell)")
rep("            target = self._smart_target(uid, spell)\n            return self._execute_spell(uid, target, spell)", "            if self._is_y17(uid):\n                try:\n                    options = self._y17_options(' '.join(parts[1:]))\n                except ValueError as exc:\n                    return ActionExecutionResult(str(exc), False, 'invalid_options')\n                if options.get('mode') == 'throw':\n                    return ActionExecutionResult('投掷血枪需要主动作', False, 'invalid_timing')\n                spell = dict(spell, _blood_options=options)\n            target = self._smart_target(uid, spell)\n            return self._execute_spell(uid, target, spell)", 2)
p.write_text(s, encoding='utf-8', newline='')

# Character data stays declarative and goes through normal load/serialization.
p = ROOT / 'characters_data.py'
s = p.read_text(encoding='utf-8')
node = next(n for n in ast.parse(s).body if isinstance(n, ast.Assign) and
    any(isinstance(t, ast.Name) and t.id == 'YEZHAOMING' for t in n.targets))
data = ast.literal_eval(node.value)
data['blood_rules'] = dict(source_cap=1000, hp_to_ml=200, ball_ml=500, heal_max_units=4,
    duration=10, armor_ml=300, armor_cap=12, blade_ml=100, blade_damage='1d8+2',
    spear_ml=1000, spear_cap=2, spear_uses=3, spear_melee='1d8+6', spear_throw='1d10+6',
    spear_range=8, cape_ml=1000, cape_mp=4, slash_base_ml=500, slash_extra_ml=1000,
    arcana_base_blood=100, arcana_substitute_ml=500, war_radius=10, war_extra_radius=5,
    chain_ml=3000, corpse_ml=300)
kinds = {2:'arcana',3:'guard',4:'sacrifice',5:'blade',6:'war',7:'store',10:'cape',11:'slash',12:'spear'}
for i, sp in enumerate(data['spells'], 1):
    if i in kinds:
        sp['blood_kind'] = kinds[i]
    if i == 4:
        sp['effects'][0].pop('damage_pct_target_max_hp', None)
        sp['effects'][0]['damage_pct_caster_max_hp'] = 90
    if i == 12:
        sp['effects'] = [dict(type=4, 客体=1)]
    if i == 7:
        sp['effects'][0]['作用半径'] = 5
lines=s.splitlines(keepends=True)
lines[node.lineno-1:node.end_lineno]=['YEZHAOMING = '+pprint.pformat(data, width=110, sort_dicts=False)+'\n']
s=''.join(lines)
s=s.replace("        # Blood and HP cost fields", "        if spell.get('blood_kind'):\n            char.set_str(f\"{prefix}blood_kind\", spell['blood_kind'])\n        # Blood and HP cost fields")
s=s.replace("    char._blood_ml = char_data.get('初始血液', 0) or 0", "    char._blood_ml = char_data.get('初始血液', 0) or 0\n    char.set_attr('储血ml', char._blood_ml)\n    char.set_attr('_blood_initial_ml', char._blood_ml)\n    if char_data.get('blood_rules'):\n        char.set_str('_blood_rules', json.dumps(char_data['blood_rules'], ensure_ascii=False))")
p.write_text(s, encoding='utf-8', newline='')
