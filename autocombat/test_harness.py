#!/usr/bin/env python3
"""
独立测试系统 (Independent Test Harness for autocombat)
=====================================================
模拟 Sealdice JS 扩展 API，处理所有战斗指令。
严格遵循 testplan.txt 第4条：不改动 autocombat.js。

支持指令: .st .btastart .btaend .hs .unh .bta map .bta int .btastartfull
          .bta <skill> @target .e d/c .s1-.s9 .a m <coord> .i end .g
"""

import json
import random
import re
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict

# ============================================================
#  Constants (mirrored from autocombat.js)
# ============================================================

CN_NUMS = ['零','一','二','三','四','五','六','七','八','九','十',
           '十一','十二','十三','十四','十五','十六','十七','十八','十九','二十']

CAT_LETTERS = ['a','b','c','d','e','f']

CAT_NAMES = {1:'伤害', 2:'护盾', 3:'回复', 4:'辅助', 5:'召唤', 6:'制造', 7:'引发', 8:'领域', 0:'其他'}

TIMING_NAMES = {1:'被动', 2:'主动作', 3:'附加动作', 4:'反应'}

TARGET_NAMES = {1:'自身', 2:'友方单体', 3:'友方群体', 4:'敌方单体', 5:'敌方群体',
                12:'任意己方单体', 13:'自身+友方群体', 14:'自身+敌方单体', 15:'自身+敌方群体',
                124:'任意单体', 35:'领域内全体',
                25:'友方单体+敌方群体', 45:'敌方单体+敌方群体', 125:'任意己方单体+敌方群体'}

AUX_EFFECT_TYPES = {
    1:'受到伤害+', 2:'受到伤害-', 3:'造成伤害+', 4:'造成伤害-',
    5:'mp回复+', 6:'mp回复-', 7:'hp回复+', 8:'hp回复-',
    9:'魔能消耗+', 10:'魔能消耗-', 11:'致死骰优势', 12:'致死骰劣势',
    13:'伤害骰优势', 14:'伤害骰劣势', 15:'伤害成功率加减', 16:'伤害成功率奖励惩罚',
    17:'以上全部的成倍变化'
}

MELEE_COMBAT_SKILLS = ['格斗','斗殴','斧','链锯','连枷','绞索','矛','剑','鞭']

ALL_COMBAT_SKILLS = MELEE_COMBAT_SKILLS + [
    '射击','射击:弓','射击:手枪','射击:重武器','射击:火焰喷射器',
    '射击:机枪','射击:步霰','射击:冲锋枪','投掷','爆破','炮术'
]

RANGED_SKILLS = ALL_COMBAT_SKILLS[len(MELEE_COMBAT_SKILLS):]

# ============================================================
#  Dice roller
# ============================================================

def roll_dice(expr: str) -> int:
    """Roll a dice expression like '1d6', '2d4+1d6', '3d8+5'"""
    if not expr or expr == '0':
        return 0
    # Normalize: "d6" → "1d6"
    expr = re.sub(r'(^|[^0-9])d', r'\g<1>1d', expr, flags=re.IGNORECASE)
    total = 0
    # Split into terms
    parts = re.findall(r'[+-]?\s*\d*d\d+|[+-]?\s*\d+', expr, re.IGNORECASE)
    if not parts:
        try:
            return int(expr)
        except ValueError:
            return 0
    for part in parts:
        sign = -1 if part.strip().startswith('-') else 1
        clean = part.strip().lstrip('+-').strip()
        m = re.match(r'^(\d*)d(\d+)$', clean, re.IGNORECASE)
        if m:
            cnt = int(m.group(1)) if m.group(1) else 1
            sides = int(m.group(2))
            for _ in range(cnt):
                total += sign * random.randint(1, sides)
        else:
            try:
                total += sign * int(clean)
            except ValueError:
                pass
    return total


def roll_d100(bp_str=''):
    """Roll d100 with optional bonus/penalty dice. Returns (result, detail)."""
    tens = random.randint(0, 9)
    if tens == 0: tens = 0  # 0 = 0 for tens
    units = random.randint(0, 9)
    if units == 0: units = 0

    # Parse bonus/penalty
    bp_match = re.match(r'^([bp])(\d*)$', bp_str.lower()) if bp_str else None
    if bp_match:
        btype = bp_match.group(1)
        count = int(bp_match.group(2)) if bp_match.group(2) else 1
        best_tens = tens
        extras = []
        for _ in range(count):
            et = random.randint(0, 9)
            extras.append(str(et))
        for et_str in extras:
            et = int(et_str)
            if btype == 'b':
                if et < best_tens: best_tens = et
            else:
                if et > best_tens: best_tens = et
        label = '奖励' if btype == 'b' else '惩罚'
        detail = f"{label}{','.join(extras)}"
        tens = best_tens
    else:
        detail = ''

    result = 100 if (tens == 0 and units == 0) else tens * 10 + units
    return result, detail


def success_rank(d100_val: int, skill_val: int) -> int:
    """COC7 success rank: -2大失败 -1失败 1成功 2困难成功 3极难成功 4大成功"""
    if skill_val <= 0:
        return 4 if d100_val <= 5 else (-2 if d100_val >= 96 else -1)
    if d100_val == 1: return 4
    if d100_val == 100: return -2
    if d100_val > skill_val:
        if skill_val < 50 and d100_val >= 96: return -2
        return -1
    rank = 1
    if d100_val <= skill_val / 5: rank = 3
    elif d100_val <= skill_val / 2: rank = 2
    if d100_val <= 5: rank = 4
    return rank


def rank_text(rank: int) -> str:
    return {-2:'大失败', -1:'失败', 1:'成功', 2:'困难成功', 3:'极难成功', 4:'大成功'}.get(rank, '')


def avg_damage(expr: str) -> float:
    if not expr: return 0
    expr_norm = re.sub(r'(^|[^0-9])d', r'\g<1>1d', expr, flags=re.IGNORECASE)
    total = 0.0
    parts = re.findall(r'[+-]?\s*\d*d\d+|[+-]?\s*\d+', expr_norm, re.IGNORECASE)
    for part in parts:
        sign = -1 if part.strip().startswith('-') else 1
        clean = part.strip().lstrip('+-').strip()
        m = re.match(r'^(\d*)d(\d+)$', clean, re.IGNORECASE)
        if m:
            cnt = int(m.group(1)) if m.group(1) else 1
            sides = int(m.group(2))
            total += sign * cnt * (sides + 1) / 2
        else:
            try: total += sign * int(clean)
            except ValueError: pass
    return total


def has_timing(timing_str, code):
    """Check if timing string contains a specific code (substring match)."""
    if not timing_str: return False
    return str(code) in str(timing_str)


def has_object(obj_str, code):
    """Check if concatenated object string contains a specific target code."""
    if not obj_str: return False
    return str(code) in str(obj_str)


def parse_coord(s: str):
    """Parse A1-style coordinate → (col, row) 0-indexed."""
    m = re.match(r'^([A-Z])(\d+)$', s.upper())
    if not m: return None
    return (ord(m.group(1)) - 65, int(m.group(2)) - 1)


def format_coord(col: int, row: int) -> str:
    return f"{chr(65 + col)}{row + 1}"


def is_in_melee_range(a_coord: str, t_coord: str) -> bool:
    a = parse_coord(a_coord)
    t = parse_coord(t_coord)
    if not a or not t: return False
    return abs(a[0] - t[0]) <= 2 and abs(a[1] - t[1]) <= 2


def db_from_build(build: int) -> str:
    table = {-2:'-2', -1:'-1d4', 0:'0', 1:'1d4', 2:'1d6', 3:'2d6', 4:'3d6', 5:'4d6'}
    return table.get(build, '0')


# ============================================================
#  Character (simulates Sealdice char card)
# ============================================================

@dataclass
class Character:
    user_id: str
    name: str = ''
    attrs: Dict[str, int] = field(default_factory=dict)
    str_attrs: Dict[str, str] = field(default_factory=dict)
    skills: Dict[str, int] = field(default_factory=dict)
    spells: List[Dict] = field(default_factory=list)
    summon_templates: List[Dict] = field(default_factory=list)
    craft_templates: List[Dict] = field(default_factory=list)

    # Transform state
    hs_transformed: bool = False
    hs_orig: Dict = field(default_factory=dict)
    hs_spell_count: int = 0
    hs_combat_flag: int = 0
    hs_valid_transform_count: int = 0

    auto_battle_mode: int = 1
    restim_mode: int = 0
    combat_active: bool = False

    serial: str = ''

    def get_attr(self, name: str, fallback=0) -> int:
        return self.attrs.get(name, fallback)

    def get_str(self, name: str) -> str:
        return self.str_attrs.get(name, '')

    def set_attr(self, name: str, value: int):
        self.attrs[name] = value

    def set_str(self, name: str, value: str):
        self.str_attrs[name] = value

    def get_best_melee(self) -> Tuple[str, int]:
        best_name = '斗殴'
        best_val = 0
        for sk in MELEE_COMBAT_SKILLS:
            v = self.attrs.get(sk, 0)
            if v > best_val:
                best_val = v
                best_name = sk
        return best_name, max(best_val, 25)


# ============================================================
#  Combat Engine (simulates Sealdice API + combat logic)
# ============================================================

class CombatEngine:
    def __init__(self):
        self.characters: Dict[str, Character] = {}
        self.storage: Dict[str, str] = {}  # ext.storageSet/Get simulation
        self.cmd_map: Dict[str, callable] = {}
        self.group_id = 'test_group'
        self.output_log: List[str] = []

    # ---------- Storage helpers ----------
    def storage_get(self, key: str) -> Optional[str]:
        return self.storage.get(key)

    def storage_set(self, key: str, value: str):
        if value:
            self.storage[key] = value
        elif key in self.storage:
            del self.storage[key]

    def get_json(self, key: str, default=None):
        raw = self.storage_get(key)
        if not raw: return default
        try: return json.loads(raw)
        except: return default

    def set_json(self, key: str, data):
        self.storage_set(key, json.dumps(data, ensure_ascii=False))

    # ---------- Character management ----------
    def get_char(self, user_id: str) -> Character:
        if user_id not in self.characters:
            self.characters[user_id] = Character(user_id=user_id, name=user_id)
        return self.characters[user_id]

    # ---------- .st command ----------
    def cmd_st(self, user_id: str, args_str: str) -> str:
        """Parse .st keyvalue pairs and set character attributes."""
        char = self.get_char(user_id)
        out_parts = []

        # COC7 .st format: concatenated key-value pairs
        # e.g. "力量50体质55" or '名称="魔法箭"'
        remaining = args_str.strip()

        while remaining:
            # Try string value: key="value"
            str_match = re.match(r'^([^\d"]+?)="((?:[^"\\]|\\.)*)"', remaining)
            if str_match:
                key = str_match.group(1)
                val = str_match.group(2)
                char.set_str(key, val)
                remaining = remaining[str_match.end():]
                continue

            # Try int value: key followed by digits (possibly negative)
            int_match = re.match(r'^([^\d"]+?)([+-]?\d+)', remaining)
            if int_match:
                key = int_match.group(1)
                val = int(int_match.group(2))
                char.set_attr(key, val)
                remaining = remaining[int_match.end():]
                continue

            break  # No more parseable pairs

        return f"[.st] {char.name} attributes updated."

    # ---------- .hs (transform) ----------
    def cmd_hs(self, user_id: str) -> str:
        char = self.get_char(user_id)
        if char.hs_transformed:
            return f"{char.name} 已经处于变身状态！"

        level = char.get_attr('等级', 1)
        orig_hp_max = char.get_attr('体力上限', 10)
        orig_mp_max = char.get_attr('魔力上限') or char.get_attr('魔力', 10)
        orig_app = char.get_attr('外貌', 50)
        orig_dodge = char.get_attr('闪避', 25)
        orig_build = char.get_attr('体格', 0)
        orig_mov = char.get_attr('行动力', 8)
        orig_san = char.get_attr('理智', 50)

        # Save originals
        orig_skills = {}
        boosted = []
        for sk in ALL_COMBAT_SKILLS:
            v = char.get_attr(sk)
            if v > 0:
                orig_skills[sk] = v
                new_v = min(v + 20, 80)
                if new_v != v:
                    boosted.append((sk, v, new_v))

        char.hs_orig = {
            'hp_max': orig_hp_max, 'mp_max': orig_mp_max, 'app': orig_app,
            'dodge': orig_dodge, 'build': orig_build, 'mov': orig_mov, 'san': orig_san,
            'cur_hp': char.get_attr('体力', orig_hp_max),
            'cur_mp': char.get_attr('魔力', orig_mp_max),
            'level': level, 'skills': orig_skills
        }

        new_hp = int(orig_hp_max * (5 + level))
        new_mp = int(orig_mp_max * (2 + 0.5 * level))
        new_app = min(orig_app + 60, 80)
        new_dodge = min(orig_dodge + 20, 80)
        new_build = orig_build + 1
        new_mov = orig_mov + 2

        char.set_attr('体力上限', new_hp)
        char.set_attr('体力', new_hp)
        char.set_attr('魔力上限', new_mp)
        char.set_attr('魔力', new_mp)
        char.set_attr('外貌', new_app)
        char.set_attr('闪避', new_dodge)
        char.set_attr('体格', new_build)
        char.set_attr('行动力', new_mov)
        char.hs_transformed = True
        char.hs_spell_count = 0
        char.hs_combat_flag = 0
        char.combat_active = True

        for sk, old_v, new_v in boosted:
            char.set_attr(sk, new_v)

        # Init combat HP
        self._init_combat_hp(user_id, char.get_attr('体力', new_hp))

        out = f"{char.name} 进行了魔法少女变身！\n"
        out += f"HP: {orig_hp_max} → {new_hp} (×{5+level})\n"
        out += f"MP: {orig_mp_max} → {new_mp}\n"
        out += f"APP: {orig_app} → {new_app}\n"
        out += f"闪避: {orig_dodge} → {new_dodge}\n"
        out += f"体格: {orig_build} → {new_build}\n"
        out += f"MOV: {orig_mov} → {new_mov}\n"
        return out

    # ---------- .unh (un-transform) ----------
    def cmd_unh(self, user_id: str) -> str:
        char = self.get_char(user_id)
        if not char.hs_transformed:
            return f"{char.name} 当前未处于变身状态！"
        if not char.hs_orig:
            return "未找到变身前数据。"
        orig = char.hs_orig

        cur_hp = self._get_combat_hp(user_id) or char.get_attr('体力', orig['hp_max'])
        cur_mp = char.get_attr('魔力', orig['mp_max'])
        restored_hp = min(math.ceil(cur_hp / (orig['level'] + 5)) + 2, orig['hp_max'])
        restored_mp = min(cur_mp, orig['mp_max'])

        # SC/CON checks
        sc_out = ''
        if char.hs_spell_count > 2 or char.hs_combat_flag > 0:
            cur_san = char.get_attr('理智', orig.get('san', 50))
            sc_out += f"\n[SC检定模拟] SAN: {cur_san}"

        # Restore
        char.set_attr('体力上限', orig['hp_max'])
        char.set_attr('体力', restored_hp)
        char.set_attr('魔力上限', orig['mp_max'])
        char.set_attr('魔力', restored_mp)
        char.set_attr('外貌', orig['app'])
        char.set_attr('闪避', orig['dodge'])
        char.set_attr('体格', orig['build'])
        char.set_attr('行动力', orig['mov'])
        char.hs_transformed = False
        char.combat_active = False

        for sk, v in orig.get('skills', {}).items():
            char.set_attr(sk, v)

        return f"{char.name} 解除了变身！\nHP: {cur_hp} → {restored_hp}\nMP: {cur_mp} → {restored_mp}{sc_out}"

    # ---------- Combat HP store ----------
    def _combat_hp_key(self) -> str:
        return f"combat_hp_{self.group_id}"

    def _init_combat_hp(self, user_id: str, hp: int):
        hp_store = self.get_json(self._combat_hp_key(), {})
        if user_id not in hp_store:
            hp_store[user_id] = hp
        self.set_json(self._combat_hp_key(), hp_store)

    def _get_combat_hp(self, user_id: str) -> Optional[int]:
        hp_store = self.get_json(self._combat_hp_key(), {})
        return hp_store.get(user_id)

    def _set_combat_hp(self, user_id: str, hp: int):
        hp_store = self.get_json(self._combat_hp_key(), {})
        hp_store[user_id] = max(0, hp)
        self.set_json(self._combat_hp_key(), hp_store)

    # ---------- Combat state ----------
    def _get_state(self) -> Optional[Dict]:
        return self.get_json(f"combat_state_{self.group_id}")

    def _set_state(self, state: Dict):
        self.set_json(f"combat_state_{self.group_id}", state)

    def _get_initiative(self) -> List[Dict]:
        return self.get_json(f"combat_initiative_{self.group_id}", [])

    def _set_initiative(self, init_list: List[Dict]):
        self.set_json(f"combat_initiative_{self.group_id}", init_list)

    def _get_actions(self) -> Dict:
        return self.get_json(f"combat_actions_{self.group_id}", {})

    def _set_actions(self, actions: Dict):
        self.set_json(f"combat_actions_{self.group_id}", actions)

    def _get_effects(self) -> List[Dict]:
        return self.get_json(f"combat_effects_{self.group_id}", [])

    def _set_effects(self, effects: List[Dict]):
        if effects:
            self.set_json(f"combat_effects_{self.group_id}", effects)
        else:
            self.storage_set(f"combat_effects_{self.group_id}", '')

    # ---------- Map ----------
    def _get_map(self) -> Optional[Dict]:
        return self.get_json(f"combat_map_{self.group_id}")

    def _set_map(self, map_data: Dict):
        self.set_json(f"combat_map_{self.group_id}", map_data)

    # ---------- Buff/Debuff helpers ----------
    def _get_active_buffs(self, user_id: str) -> List[Dict]:
        effects = self._get_effects()
        return [e for e in effects
                if e.get('type') in ('buff', 'debuff')
                and e.get('targetUserId') == user_id
                and e.get('remainingRounds', 0) != 0]

    def _apply_buff_skill_mod(self, user_id: str, base_val: int) -> int:
        buffs = self._get_active_buffs(user_id)
        adjusted = base_val
        for b in buffs:
            sm = b.get('skillMod', '')
            if sm:
                m = re.search(r'([+-]?\d+)$', str(sm))
                if m:
                    adjusted += int(m.group(1))
        return max(1, adjusted)

    def _get_active_shields(self, user_id: str) -> Tuple[int, List[Dict]]:
        effects = self._get_effects()
        shields = [e for e in effects
                   if e.get('type') == 'shield'
                   and e.get('targetUserId') == user_id
                   and e.get('remainingRounds', 0) != 0]
        total = sum(s.get('value', 0) for s in shields)
        return total, shields

    def _absorb_damage_with_shield(self, user_id: str, dmg: int) -> Tuple[int, int, List[str]]:
        """Returns (remaining_dmg, absorbed, msgs)."""
        if dmg <= 0: return 0, 0, []
        effects = self._get_effects()
        remaining = dmg
        absorbed = 0
        msgs = []
        for e in effects:
            if e.get('type') != 'shield' or e.get('targetUserId') != user_id: continue
            if e.get('remainingRounds', 0) == 0: continue
            sv = e.get('value', 0)
            if sv <= 0: continue
            absorb = min(remaining, sv)
            remaining -= absorb
            absorbed += absorb
            e['value'] = sv - absorb
            if e['value'] <= 0:
                e['remainingRounds'] = 0
                msgs.append(f"护盾【{e.get('spellName', '')}】被击破！")
            else:
                msgs.append(f"护盾【{e.get('spellName', '')}】吸收 {absorb} 点伤害（剩余 {e['value']}）")
            if remaining <= 0: break
        self._set_effects(effects)
        return remaining, absorbed, msgs

    # ---------- Damage calculation ----------
    def _calc_damage(self, loser_id: str, winner_id: str,
                     winner_rank: int, winner_roll: int,
                     dmg_dice: str, pen: int, leth: int) -> int:
        """Calculate damage and apply to combat HP store."""
        dmg_val = 0
        if winner_rank == 2:
            r1 = roll_dice(dmg_dice)
            r2 = roll_dice(dmg_dice)
            dmg_val = max(r1, r2)
        elif winner_rank == 3:
            if pen:
                dmg_val = self._max_damage(dmg_dice) + roll_dice(dmg_dice)
            else:
                dmg_val = self._max_damage(dmg_dice)
        elif winner_rank == 4:
            if winner_roll == 1:
                dmg_val = self._max_damage(dmg_dice) * 2
            elif pen:
                dmg_val = self._max_damage(dmg_dice) * 2
            else:
                dmg_val = self._max_damage(dmg_dice) + roll_dice(dmg_dice)
        else:
            dmg_val = roll_dice(dmg_dice)

        # Shield absorption
        effective_dmg, absorbed, shield_msgs = self._absorb_damage_with_shield(loser_id, dmg_val)

        cur_hp = self._get_combat_hp(loser_id)
        if cur_hp is None: cur_hp = 10

        # Lethality
        exp_dmg = avg_damage(dmg_dice)
        if leth and exp_dmg > 6:
            leth_die = max(2, cur_hp * 2)
            leth_roll = random.randint(1, leth_die)
            if leth_roll <= int(exp_dmg):
                cur_hp = 0
            else:
                cur_hp = max(0, cur_hp - effective_dmg)
        else:
            cur_hp = max(0, cur_hp - effective_dmg)

        self._set_combat_hp(loser_id, cur_hp)
        return dmg_val

    def _max_damage(self, expr: str) -> int:
        if not expr: return 0
        expr = re.sub(r'(^|[^0-9])d', r'\g<1>1d', expr, flags=re.IGNORECASE)
        total = 0
        parts = re.findall(r'[+-]?\s*\d*d\d+|[+-]?\s*\d+', expr, re.IGNORECASE)
        for part in parts:
            sign = -1 if part.strip().startswith('-') else 1
            clean = part.strip().lstrip('+-').strip()
            m = re.match(r'^(\d*)d(\d+)$', clean, re.IGNORECASE)
            if m:
                cnt = int(m.group(1)) if m.group(1) else 1
                sides = int(m.group(2))
                total += sign * cnt * sides
            else:
                try: total += sign * int(clean)
                except: pass
        return max(0, total)

    # ---------- Load spells ----------
    def load_spells(self, user_id: str) -> List[Dict]:
        char = self.get_char(user_id)
        spells = []
        for i in range(1, 21):
            prefix = f"技能{CN_NUMS[i]}"
            name = char.get_str(f"{prefix}名称")
            if not name: continue

            spell = {'index': i, 'name': name, 'effects': []}

            # Read int keys
            for k in ['级别','类别','消耗mp','消耗san','熟练度','里程碑','吟唱回合','默认延续性']:
                v = char.get_attr(f"{prefix}{k}")
                if v is not None: spell[k] = v

            # Read timing as string
            timing = char.get_str(f"{prefix}时机")
            if timing: spell['时机'] = timing

            # Read str keys
            intro = char.get_str(f"{prefix}简介")
            if intro: spell['简介'] = intro

            # Defaults
            spell.setdefault('级别', 1)
            spell.setdefault('类别', 0)
            spell.setdefault('时机', '2')
            spell.setdefault('消耗mp', 0)
            spell.setdefault('消耗san', 0)
            spell.setdefault('吟唱回合', 0)
            spell.setdefault('默认延续性', 0)

            # Parse categories a-f
            for ci, letter in enumerate(CAT_LETTERS):
                prefix_l = f"{prefix}类别{letter}"
                # Check hasData
                has_data = bool(
                    char.get_attr(f"{prefix_l}客体") or
                    char.get_str(f"{prefix_l}伤害骰") or
                    char.get_str(f"{prefix_l}护盾值") or
                    char.get_str(f"{prefix_l}回复hp") or
                    char.get_str(f"{prefix_l}技能加减值") or
                    char.get_attr(f"{prefix_l}召唤个数") or
                    char.get_attr(f"{prefix_l}引发目标法术") or
                    char.get_attr(f"{prefix_l}领域中心跟随") or
                    char.get_str(f"{prefix_l}每回合伤害骰")
                )
                if not has_data and ci > 0: continue

                # Infer category type
                if ci == 0:
                    cat_type = spell['类别']
                elif char.get_str(f"{prefix_l}伤害骰"): cat_type = 1
                elif char.get_str(f"{prefix_l}护盾值"): cat_type = 2
                elif char.get_str(f"{prefix_l}回复hp") or char.get_str(f"{prefix_l}回复san") or char.get_str(f"{prefix_l}回复mp"): cat_type = 3
                elif char.get_str(f"{prefix_l}技能加减值") or char.get_str(f"{prefix_l}其他辅助效果a"): cat_type = 4
                elif char.get_attr(f"{prefix_l}召唤个数"): cat_type = 5
                elif char.get_attr(f"{prefix_l}引发目标法术"): cat_type = 7
                elif char.get_attr(f"{prefix_l}领域中心跟随") or (char.get_str(f"{prefix_l}每回合伤害骰") and char.get_attr(f"{prefix_l}作用半径", 0) > 0): cat_type = 8
                else: cat_type = 0

                eff = {'type': cat_type, 'letter': letter}

                # Int keys
                for k in ['客体','作用半径','可调节性','成功率','成功率奖惩骰',
                          '可反应性','可贯穿性','致死值','致死值优劣','附加效果时长','持续回合',
                          '引发目标法术','引发延迟回合','制造个数','制造花费回合数','领域中心跟随','触发HP比例']:
                    v = char.get_attr(f"{prefix_l}{k}")
                    if v is not None: eff[k] = v

                # Str keys
                for k in ['伤害骰','附加效果','护盾值','回复hp','回复san','回复mp',
                          '技能加减值','其他辅助效果a','辅助效果值a','召唤个数','召唤物模板','制造物模板',
                          '每回合伤害骰','吸血比例','属性削减']:
                    v = char.get_str(f"{prefix_l}{k}")
                    if v: eff[k] = v

                eff.setdefault('客体', 4)
                eff.setdefault('作用半径', 0)
                eff.setdefault('可反应性', 1 if cat_type == 1 else 0)
                eff.setdefault('可贯穿性', 1 if cat_type == 1 else 0)
                eff.setdefault('成功率', 0)
                spell['effects'].append(eff)

            spells.append(spell)

        char.spells = spells
        return spells

    # ---------- Execute spell ----------
    def _execute_spell(self, caster_id: str, target_id: str, spell: Dict) -> str:
        char = self.get_char(caster_id)
        target_name = self.get_char(target_id).name if target_id else '自身'
        out = f"{char.name} 释放【{spell['name']}】→ {target_name}\n"

        # MP/SAN cost
        mp_cost = spell.get('消耗mp', 0)
        san_cost = spell.get('消耗san', 0)
        if mp_cost > 0:
            cur_mp = char.get_attr('魔力', 0) or 0
            if cur_mp < mp_cost:
                return f"{char.name} 魔力不足！需要 {mp_cost} MP。"
            char.set_attr('魔力', cur_mp - mp_cost)
            out += f"消耗 {mp_cost} MP\n"
        if san_cost > 0:
            cur_san = char.get_attr('理智', 50)
            char.set_attr('理智', max(0, cur_san - san_cost))
            out += f"消耗 {san_cost} SAN\n"

        # Execute each effect
        for eff in spell['effects']:
            cat_type = eff['type']
            if cat_type == 1:  # Damage
                dmg_dice = eff.get('伤害骰', '1d4')
                pen = eff.get('可贯穿性', 0)
                leth = eff.get('致死值', 0)
                success_rate = eff.get('成功率', 0)
                dur = eff.get('持续回合', 0)
                dot_dice = eff.get('每回合伤害骰', '')
                lifesteal_ratio = float(eff.get('吸血比例', '0') or '0')

                if success_rate > 0:
                    check = random.randint(1, 100)
                    if check > success_rate:
                        out += f"成功率检定: {check}/{success_rate} 失败！\n"
                        continue
                    out += f"成功率检定: {check}/{success_rate} 成功。\n"

                dmg_val = self._calc_damage(target_id, caster_id, 1, 1, dmg_dice, pen, leth)
                out += f"造成 {dmg_val} 点伤害。\n"

                # Lifesteal
                if lifesteal_ratio > 0 and dmg_val > 0:
                    heal = int(dmg_val * lifesteal_ratio)
                    caster_hp = self._get_combat_hp(caster_id) or 10
                    self._set_combat_hp(caster_id, caster_hp + heal)
                    out += f"吸血回复 {heal} HP。\n"

                # DOT
                if dur > 0 or dot_dice:
                    effects = self._get_effects()
                    effects.append({
                        'type': 'dot', 'remainingRounds': max(dur, 1),
                        'dotDice': dot_dice or dmg_dice, 'pen': pen, 'leth': leth,
                        'sourceUserId': caster_id, 'targetUserId': target_id,
                        'spellName': spell['name'], 'spellIndex': spell['index']
                    })
                    self._set_effects(effects)
                    if dot_dice:
                        out += f"持续伤害: 每回合 {dot_dice}（持续 {max(dur, 1)} 回合）\n"

            elif cat_type == 2:  # Shield
                shield_val = roll_dice(eff.get('护盾值', '1d4'))
                dur = eff.get('持续回合', 1)
                effects = self._get_effects()
                effects.append({
                    'type': 'shield', 'value': shield_val, 'remainingRounds': dur,
                    'sourceUserId': caster_id, 'targetUserId': target_id or caster_id,
                    'spellName': spell['name'], 'spellIndex': spell['index'],
                    'persistent': spell.get('默认延续性', 0)
                })
                self._set_effects(effects)
                out += f"获得 {shield_val} 点护盾（持续 {dur} 回合）。\n"

            elif cat_type == 3:  # Heal
                hp_heal = roll_dice(eff.get('回复hp', '0'))
                san_heal = roll_dice(eff.get('回复san', '0'))
                mp_heal = roll_dice(eff.get('回复mp', '0'))
                tid = target_id or caster_id
                if hp_heal > 0:
                    cur_hp = self._get_combat_hp(tid) or 10
                    tchar = self.get_char(tid)
                    max_hp = tchar.get_attr('体力上限', cur_hp) if tchar else cur_hp
                    self._set_combat_hp(tid, min(cur_hp + hp_heal, max_hp))
                    out += f"回复 HP +{hp_heal}\n"
                if mp_heal > 0:
                    tchar = self.get_char(tid)
                    cur_mp = tchar.get_attr('魔力', 0)
                    tchar.set_attr('魔力', cur_mp + mp_heal)
                    out += f"回复 MP +{mp_heal}\n"

            elif cat_type == 4:  # Buff
                dur = eff.get('持续回合', 1)
                effects = self._get_effects()
                aux_type = eff.get('其他辅助效果a', '')
                effects.append({
                    'type': 'buff', 'remainingRounds': dur,
                    'skillMod': eff.get('技能加减值', ''),
                    'auxType': aux_type,
                    'auxVal': eff.get('辅助效果值a', ''),
                    'sourceUserId': caster_id, 'targetUserId': target_id or caster_id,
                    'spellName': spell['name'], 'spellIndex': spell['index'],
                    'persistent': spell.get('默认延续性', 0)
                })
                self._set_effects(effects)
                out += f"施加辅助效果: {eff.get('技能加减值', '')} {aux_type}（持续 {dur} 回合）\n"

            elif cat_type == 5:  # Summon
                count = eff.get('召唤个数', 1)
                dur = eff.get('持续回合', 1)
                tmpl_name = eff.get('召唤物模板', '')
                out += f"召唤 {count} 个【{tmpl_name or '使魔'}】（持续 {dur} 回合）。\n"

            elif cat_type == 6:  # Create
                count = eff.get('制造个数', 1)
                craft_rounds = eff.get('制造花费回合数', 0)
                effects = self._get_effects()
                for cc in range(count):
                    effects.append({
                        'type': 'create', 'craftId': f"craft_{spell['index']}_{cc}",
                        'template': eff.get('制造物模板', ''),
                        'remainingRounds': eff.get('持续回合', 0),
                        'craftRoundsRemaining': craft_rounds,
                        'sourceUserId': caster_id, 'targetUserId': target_id or caster_id,
                        'spellName': spell['name'], 'spellIndex': spell['index'],
                        'persistent': spell.get('默认延续性', 0)
                    })
                self._set_effects(effects)
                out += f"开始制造 {count} 个【{eff.get('制造物模板', '物品')}】（需要 {craft_rounds} 回合）\n"

            elif cat_type == 7:  # Trigger
                target_spell = eff.get('引发目标法术', 0)
                delay = eff.get('引发延迟回合', 0)
                effects = self._get_effects()
                effects.append({
                    'type': 'trigger', 'remainingRounds': delay,
                    'triggerSpellIndex': target_spell,
                    'sourceSpellIndex': spell['index'],
                    'sourceSpellName': spell['name'],
                    'sourceUserId': caster_id,
                    'spellName': spell['name'], 'spellIndex': spell['index'],
                    'persistent': spell.get('默认延续性', 0)
                })
                self._set_effects(effects)
                out += f"引发法术已设置：{delay} 回合后自动释放技能{target_spell}。\n"

            elif cat_type == 8:  # Zone
                radius = eff.get('作用半径', 0)
                dur = eff.get('持续回合', 1)
                tick_dmg = eff.get('每回合伤害骰', '')
                tick_heal_hp = eff.get('回复hp', '')
                tick_heal_mp = eff.get('回复mp', '')
                center_follows = eff.get('领域中心跟随', 0)
                obj_filter = eff.get('客体', 35)
                attr_debuff = eff.get('属性削减', '')
                effects = self._get_effects()
                # Remove existing zone with same spellName (同种领域不叠加)
                effects = [e for e in effects if not (e.get('type') == 'zone' and e.get('spellName') == spell['name'])]
                effects.append({
                    'type': 'zone', 'center': 'A1', 'radius': radius,
                    'remainingRounds': dur, 'tickDmg': tick_dmg,
                    'tickHealHp': tick_heal_hp, 'tickHealMp': tick_heal_mp,
                    'centerFollows': center_follows, 'filter': obj_filter,
                    'attributeDebuff': attr_debuff,
                    'sourceUserId': caster_id, 'spellName': spell['name'],
                    'spellIndex': spell['index'], 'persistent': spell.get('默认延续性', 0)
                })
                self._set_effects(effects)
                out += f"创建领域【{spell['name']}】（半径{radius}格，持续{dur}回合）\n"

        return out

    # ---------- Tick-down (回合后结算) ----------
    def _tick_down(self) -> str:
        """Process end-of-round effects. Returns tick messages."""
        msgs = []
        effects = self._get_effects()
        if not effects: return ''

        new_effects = []
        for eff in effects:
            # Craft countdown
            if eff.get('type') == 'create' and eff.get('craftRoundsRemaining', 0) > 0:
                eff['craftRoundsRemaining'] = eff['craftRoundsRemaining'] - 1
                if eff['craftRoundsRemaining'] <= 0:
                    msgs.append(f"制造完成：【{eff.get('template', eff.get('spellName', ''))}】已就绪！")

            # Trigger countdown
            if eff.get('type') == 'trigger':
                eff['remainingRounds'] = eff.get('remainingRounds', 0) - 1
                if eff['remainingRounds'] <= 0:
                    if eff.get('persistent'):
                        new_effects = [e for e in new_effects
                                       if not (e.get('spellIndex') == eff.get('sourceSpellIndex') and e is not eff)]
                        msgs.append(f"【{eff.get('sourceSpellName', '')}】法术形态转换中...")
                    msgs.append(f"引发：自动释放技能{eff.get('triggerSpellIndex')}！")
                    continue  # Remove trigger
                new_effects.append(eff)
                continue

            # DOT damage
            if eff.get('type') == 'dot' and eff.get('dotDice') and eff.get('targetUserId'):
                dot_dmg = roll_dice(eff['dotDice'])
                if dot_dmg > 0:
                    remaining, absorbed, sh_msgs = self._absorb_damage_with_shield(eff['targetUserId'], dot_dmg)
                    cur_hp = self._get_combat_hp(eff['targetUserId']) or 10
                    cur_hp = max(0, cur_hp - remaining)
                    self._set_combat_hp(eff['targetUserId'], cur_hp)
                    msgs.extend(sh_msgs)
                    msgs.append(f"持续伤害【{eff.get('spellName', '')}】→ {eff['dotDice']}={dot_dmg}，造成 {remaining} 点伤害（目标HP: {cur_hp}）")

            # Zone processing
            if eff.get('type') == 'zone' and eff.get('radius', 0) > 0:
                if eff.get('tickDmg'):
                    msgs.append(f"领域【{eff.get('spellName', '')}】每回合伤害: {eff['tickDmg']}")
                if eff.get('tickHealHp'):
                    msgs.append(f"领域【{eff.get('spellName', '')}】每回合回复: HP {eff['tickHealHp']}")

            # Regular countdown
            eff['remainingRounds'] = eff.get('remainingRounds', 0) - 1
            if eff['remainingRounds'] <= 0:
                if eff.get('persistent'):
                    eff['remainingRounds'] = -1
                    eff['persistentActive'] = True
                    msgs.append(f"持续效果【{eff.get('spellName', '')}】进入延续状态（使用 .s{eff.get('spellIndex', '?')} end 手动结束）")
                    new_effects.append(eff)
                    continue
                msgs.append(f"效果【{eff.get('spellName', '')}】已过期")
                continue  # Remove
            new_effects.append(eff)

        self._set_effects(new_effects)
        # MP regen: all alive chars recover 2d6 MP per round end
        init_list = self._get_initiative()
        for entry in init_list:
            uid = entry['userId']
            hp = self._get_combat_hp(uid)
            if hp is not None and hp > 0:
                char = self.get_char(uid)
                cur_mp = char.get_attr('魔力', 0) or 0
                max_mp = char.get_attr('魔力上限', cur_mp) or cur_mp
                regen = roll_dice('2d6')
                if regen > 0:
                    char.set_attr('魔力', min(cur_mp + regen, max_mp))
        return '\n'.join(msgs) if msgs else ''

    # ---------- Process battle command ----------
    def process_command(self, user_id: str, cmd_str: str) -> str:
        """Route a command string to the appropriate handler."""
        cmd_str = cmd_str.strip()
        if not cmd_str.startswith('.'):
            return f"Unknown command: {cmd_str}"

        parts = cmd_str[1:].split(None, 1)
        cmd_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ''

        char = self.get_char(user_id)
        self.output_log.append(f"[{char.name}] {cmd_str}")

        if cmd_name == 'st':
            return self.cmd_st(user_id, args)
        elif cmd_name == 'hs':
            return self.cmd_hs(user_id)
        elif cmd_name == 'unh':
            return self.cmd_unh(user_id)
        elif cmd_name == 'btastart':
            char.combat_active = True
            self._init_combat_hp(user_id, char.get_attr('体力', 10))
            return f"{char.name} 的战斗属性已同步！"
        elif cmd_name == 'btaend':
            char.combat_active = False
            return f"{char.name} 的战斗已结束。"
        elif cmd_name == 'stb':
            val = int(args) if args.lstrip('+-').isdigit() else 0
            cur = self._get_combat_hp(user_id) or 10
            if args.startswith('+'):
                self._set_combat_hp(user_id, cur + val)
            elif args.startswith('-'):
                self._set_combat_hp(user_id, cur - abs(val))
            else:
                self._set_combat_hp(user_id, val)
            return f"{char.name} 战斗HP: {cur} → {self._get_combat_hp(user_id)}"
        elif cmd_name.startswith('s') and cmd_name[1:].isdigit():
            skill_num = int(cmd_name[1:])
            if args.lower() == 'end':
                return self._end_persistent(user_id, skill_num)
            return self._use_skill(user_id, skill_num, args)
        elif cmd_name == 'a':
            return self._additional_action(user_id, args)
        elif cmd_name == 'i':
            if args.lower() == 'end':
                return self._end_turn(user_id)
            return "用法: .i end"
        elif cmd_name == 'g':
            return self._transfer_craft(user_id, args)
        elif cmd_name == 'btastartfull':
            init_list = self._get_initiative()
            actions = {}
            for e in init_list:
                actions[e['userId']] = {'附加': 3, '主动': 2}
            self._set_actions(actions)
            self._set_state({'phase': 'active', 'round': 1, 'activeIndex': 0})
            lines = [f"=== 战斗开始！第 1 回合 ==="]
            lines.append(f"先攻表 ({len(init_list)}人):")
            for i, e in enumerate(init_list):
                lines.append(f"  {i+1}. {e['name']} [{e['team']}]  主动×2 附加×3")
            lines.append(f"\n当前行动: {init_list[0]['name'] if init_list else '(无)'}")
            return '\n'.join(lines)
        elif cmd_name == 'bta':
            # Check sub-commands
            arg_parts = args.split()
            if not arg_parts:
                return "用法: .bta map|int|start|add|setreaction|new 或 .bta <技能> @目标"
            sub = arg_parts[0].lower()
            if sub == 'map':
                dims = arg_parts[1] if len(arg_parts) > 1 else '10*10'
                m = re.match(r'^(\d+)\*(\d+)$', dims)
                if not m: return "用法: .bta map [宽*高]"
                w, h = int(m.group(1)), int(m.group(2))
                self._set_map({'width': w, 'height': h, 'entryRow': math.ceil(h/2),
                               'obstacles': {}, 'occupants': {}})
                return f"地图 {w}×{h} 已创建。"
            elif sub == 'int':
                team = 'Y'
                pos = 'M'
                for a in arg_parts[1:]:
                    if a.startswith('team='): team = a.split('=')[1]
                    if a.startswith('pos='): pos = a.split('=')[1]
                init_list = self._get_initiative()
                init_list.append({
                    'userId': user_id, 'name': char.name,
                    'team': team, 'pos': pos,
                    'dex': char.get_attr('敏捷', 50),
                    'initRoll': random.randint(1, 100),
                    'coord': 'A1', 'isSummon': False
                })
                init_list.sort(key=lambda e: (-e['initRoll'], -e['dex']))
                self._set_initiative(init_list)
                self._init_combat_hp(user_id, char.get_attr('体力', 10))
                return f"{char.name} 已加入战斗（阵营{team}，位置{pos}）！先攻: {init_list[-1]['initRoll']}"
            elif sub == 'start' or sub == 'btastartfull':
                init_list = self._get_initiative()
                actions = {}
                for e in init_list:
                    actions[e['userId']] = {'附加': 3, '主动': 2}
                self._set_actions(actions)
                self._set_state({'phase': 'active', 'round': 1, 'activeIndex': 0})
                lines = [f"=== 战斗开始！第 1 回合 ==="]
                lines.append(f"先攻表 ({len(init_list)}人):")
                for i, e in enumerate(init_list):
                    lines.append(f"  {i+1}. {e['name']} [{e['team']}]")
                lines.append(f"\n当前行动: {init_list[0]['name'] if init_list else '(无)'}")
                return '\n'.join(lines)
            elif sub == 'setreaction':
                return f"NPC反应已设置。"
            else:
                return f".bta: 未知子命令 '{sub}'"
        else:
            return f"未知命令: .{cmd_name}（测试系统暂不支持）"

    def _use_skill(self, user_id: str, skill_num: int, args: str) -> str:
        """Execute .sN command."""
        char = self.get_char(user_id)
        state = self._get_state()
        if not state or state.get('phase') != 'active':
            return "当前不在战斗中！"

        init_list = self._get_initiative()
        actions = self._get_actions()
        active_entry = init_list[state['activeIndex']]
        if active_entry['userId'] != user_id:
            return f"现在不是你的回合！当前行动: {active_entry['name']}"

        my_actions = actions.get(user_id, {'附加': 0, '主动': 0})
        if my_actions['主动'] <= 0:
            return "主动作次数已用尽！"

        is_passive_flag = False
        if skill_num == 0:
            # Basic melee attack
            best_name, best_val = char.get_best_melee()
            best_val = self._apply_buff_skill_mod(user_id, best_val)
            atk_roll, detail = roll_d100('')
            rank = success_rank(atk_roll, best_val)
            out = f"{char.name} 基础近战攻击【{best_name}={best_val}】\n"
            out += f"D100={atk_roll}/{best_val} {rank_text(rank)}\n"
        else:
            spells = self.load_spells(user_id)
            spell = next((s for s in spells if s['index'] == skill_num), None)
            if not spell:
                return f"未找到技能{skill_num}！"
            timing = spell.get('时机', '2')
            is_passive = has_timing(timing, '1')
            is_main = has_timing(timing, '2')
            if not is_main and not is_passive:
                return f"【{spell['name']}】不能在主动作阶段使用。"
            out = self._execute_spell(user_id, '', spell)
            if is_passive:
                out += '\n（被动法术，已经正常打开，不消耗主动作）'
                is_passive_flag = True
            else:
                is_passive_flag = False

        if not is_passive_flag:
            my_actions['主动'] -= 1
            self._set_actions(actions)
            if my_actions['主动'] <= 0:
                return self._advance_turn(state, init_list, actions, out)

        out += f"\n剩余: 主动×{my_actions['主动']} 附加×{my_actions['附加']}"
        return out

    def _additional_action(self, user_id: str, args: str) -> str:
        """Execute .a m or .a sN command."""
        char = self.get_char(user_id)
        state = self._get_state()
        if not state or state.get('phase') != 'active':
            return "当前不在战斗中！"

        actions = self._get_actions()
        my_actions = actions.get(user_id, {'附加': 0, '主动': 0})
        if my_actions['附加'] <= 0:
            return "附加动作次数已用尽！"

        parts = args.split()
        if not parts:
            return "用法: .a m <坐标> 或 .a s<序号>"

        out = ''
        if parts[0].lower() == 'm' and len(parts) >= 2:
            coord = parts[1].upper()
            out = f"{char.name} 移动至 {coord}（消耗 1 附加动作）\n"
        elif re.match(r'^s\d+$', parts[0], re.IGNORECASE):
            skill_num = int(parts[0][1:])
            spells = self.load_spells(user_id)
            spell = next((s for s in spells if s['index'] == skill_num), None)
            if not spell:
                return f"未找到技能{skill_num}！"
            timing = spell.get('时机', '2')
            if not has_timing(timing, '3'):
                return f"【{spell['name']}】不能在附加动作阶段使用。"
            out = self._execute_spell(user_id, '', spell)
        else:
            return "用法: .a m <坐标> 或 .a s<序号>"

        my_actions['附加'] -= 1
        self._set_actions(actions)
        out += f"\n剩余: 主动×{my_actions['主动']} 附加×{my_actions['附加']}"
        return out

    def _advance_turn(self, state: Dict, init_list: List, actions: Dict, out: str) -> str:
        """Advance active index and handle round transitions."""
        state['activeIndex'] = (state['activeIndex'] + 1) % len(init_list)
        if state['activeIndex'] == 0:
            state['round'] += 1
            for k in actions:
                actions[k] = {'附加': 3, '主动': 2}
            self._set_actions(actions)
            out += f"\n=== 第 {state['round']} 回合 ===\n"

            # Tick-down
            tick_msgs = self._tick_down()
            if tick_msgs:
                out += tick_msgs + '\n'

        self._set_state(state)
        next_entry = init_list[state['activeIndex']]
        out += f"\n下一位: {next_entry['name']}（第{state.get('round', 1)}回合）"
        return out

    def _end_turn(self, user_id: str) -> str:
        """Execute .i end command."""
        char = self.get_char(user_id)
        state = self._get_state()
        if not state or state.get('phase') != 'active':
            return "当前不在战斗中！"

        init_list = self._get_initiative()
        actions = self._get_actions()
        active_entry = init_list[state['activeIndex']]
        if active_entry['userId'] != user_id:
            return f"现在不是你的回合！当前行动: {active_entry['name']}"

        my_actions = actions.get(user_id, {'附加': 0, '主动': 0})
        remaining_main = my_actions['主动']
        remaining_extra = my_actions['附加']
        my_actions['主动'] = 0
        my_actions['附加'] = 0
        self._set_actions(actions)

        out = f"{char.name} 主动结束回合。\n（剩余主动作×{remaining_main}、附加动作×{remaining_extra} 已清零）\n"
        return self._advance_turn(state, init_list, actions, out)

    def _end_persistent(self, user_id: str, skill_num: int) -> str:
        """End persistent effects for a spell."""
        effects = self._get_effects()
        removed = []
        new_effects = []
        for eff in effects:
            if eff.get('spellIndex') == skill_num and (eff.get('persistent') or eff.get('persistentActive')):
                removed.append(eff.get('spellName', f"技能{skill_num}"))
                continue
            new_effects.append(eff)
        self._set_effects(new_effects)
        char = self.get_char(user_id)
        if removed:
            return f"{char.name} 结束了持续效果: {', '.join(removed)}"
        return f"未找到技能{skill_num}的活跃持续效果。"

    def _transfer_craft(self, user_id: str, args: str) -> str:
        """Execute .g command — transfer completed craft item."""
        parts = args.split()
        if not parts:
            return "用法: .g <物品名> @目标"
        item_name = parts[0]
        effects = self._get_effects()
        craft_idx = None
        for i, eff in enumerate(effects):
            if (eff.get('type') == 'create' and eff.get('sourceUserId') == user_id and
                (eff.get('template') == item_name or eff.get('craftId') == item_name) and
                eff.get('craftRoundsRemaining', 0) <= 0):
                craft_idx = i
                break
        if craft_idx is None:
            return f"未找到可转移的制造物【{item_name}】。"
        craft = effects.pop(craft_idx)
        self._set_effects(effects)
        return f"{self.get_char(user_id).name} 将【{craft.get('template', item_name)}】转移完成。"


# ============================================================
#  Test runner
# ============================================================

def test_harness_demo():
    """Demo test of the harness with a simple character."""
    engine = CombatEngine()

    # Create character 炎焱
    uid = 'test_user_1'
    char = engine.get_char(uid)
    char.name = '炎焱'
    char.set_attr('等级', 5)
    char.set_attr('敏捷', 65)
    char.set_attr('体力', 14)
    char.set_attr('体力上限', 14)
    char.set_attr('魔力', 16)
    char.set_attr('魔力上限', 16)
    char.set_attr('闪避', 70)
    char.set_attr('理智', 50)
    char.set_attr('斗殴', 75)
    char.set_attr('行动力', 8)
    char.set_attr('体格', 1)

    # Spell 1: 火焰被动（被动，奖励骰）
    char.set_str('技能一名称', '火焰附魔')
    char.set_str('技能一时机', '1')  # 被动
    char.set_attr('技能一类别', 4)  # 辅助
    char.set_attr('技能一默认延续性', 1)
    char.set_attr('技能一类别a客体', 1)  # 自身
    char.set_str('技能一类别a技能加减值', '斗殴+10')
    char.set_attr('技能一类别a持续回合', 3)

    # Spell 2: 点燃攻击
    char.set_str('技能二名称', '火焰爆发')
    char.set_str('技能二时机', '2')  # 主动作
    char.set_attr('技能二类别', 1)  # 伤害
    char.set_attr('技能二消耗mp', 2)
    char.set_str('技能二类别a伤害骰', '1d8')
    char.set_attr('技能二类别a可反应性', 1)
    char.set_attr('技能二类别a成功���', 80)
    char.set_attr('技能二类别a持续回合', 3)
    char.set_str('技能二类别a每回合伤害骰', '1d4')

    # Load spells
    spells = engine.load_spells(uid)
    print(f"Loaded {len(spells)} spells for {char.name}:")
    for s in spells:
        print(f"  技能{s['index']}: {s['name']} (type={s.get('类别')}, timing={s.get('时机')}, effects={len(s['effects'])})")
        for e in s['effects']:
            print(f"    - type={e['type']} 客体={e.get('客体')} dmg={e.get('伤害骰','')} dot={e.get('每回合伤害骰','')}")

    # Test commands
    print("\n=== Testing commands ===")
    for cmd in ['.hs', '.s1', '.s2']:
        result = engine.process_command(uid, cmd)
        print(f"\n> {cmd}")
        print(result)


if __name__ == '__main__':
    test_harness_demo()
