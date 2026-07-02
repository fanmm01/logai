#!/usr/bin/env python3
"""
统一战斗引擎 (battle_engine.py)
=================================
JS autocombat.js ↔ Python 三引擎统一 -> 单一实现

- CombatEngine:    基础状态管理、存储、HP、效果、地图、先攻、行动
- FullBattleEngine: 详细COC7攻击流程、反应、法术执行、完整tick-down
- FastBattleEngine: 快速训练模式（简化反应、无延迟、Q-learning优化）

所有36处差异已合并到此单一文件中。
"""

import sys, os, random, math, json, re, time
from collections import defaultdict
from abc import ABC, abstractmethod

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ============================================================
#  Constants
# ============================================================
CN_NUMS = ['零','一','二','三','四','五','六','七','八','九','十',
           '十一','十二','十三','十四','十五','十六','十七','十八','十九','二十']
CAT_LETTERS = ['a','b','c','d','e','f']
CAT_NAMES = {1:'伤害',2:'护盾',3:'回复',4:'辅助',5:'召唤',6:'制造',7:'引发',8:'领域',0:'其他'}
TIMING_NAMES = {1:'被动',2:'主动作',3:'附加动作',4:'反应'}

AUX_EFFECT_TYPES = {
    1:'受到伤害+',2:'受到伤害-',3:'造成伤害+',4:'造成伤害-',
    5:'mp回复+',6:'mp回复-',7:'hp回复+',8:'hp回复-',
    9:'魔能消耗+',10:'魔能消耗-',11:'致死骰优势',12:'致死骰劣势',
    13:'伤害骰优势',14:'伤害骰劣势',15:'伤害成功率加减',16:'伤害成功率奖励惩罚',
    17:'以上全部的成倍变化'
}
AUX_NAME_TO_CODE = {v: k for k, v in AUX_EFFECT_TYPES.items()}

MELEE_SKILLS = ['格斗','斗殴','斧','链锯','连枷','绞索','矛','剑','鞭']
ALL_COMBAT_SKILLS = MELEE_SKILLS + ['射击','射击:弓','射击:手枪','射击:重武器','射击:火焰喷射器',
    '射击:机枪','射击:步霰','射击:冲锋枪','投掷','爆破','炮术']

# ============================================================
#  Dice & Math helpers
# ============================================================
def roll_dice(expr):
    if not expr or expr == '0': return 0
    expr_n = re.sub(r'(^|[^0-9])d', r'\g<1>1d', expr, flags=re.IGNORECASE)
    total = 0
    for part in re.findall(r'[+-]?\s*\d*d\d+|[+-]?\s*\d+', expr_n, re.IGNORECASE):
        sign = -1 if part.strip().startswith('-') else 1
        clean = part.strip().lstrip('+-').strip()
        m = re.match(r'^(\d*)d(\d+)$', clean, re.IGNORECASE)
        if m:
            cnt = int(m.group(1)) if m.group(1) else 1
            for _ in range(cnt): total += sign * random.randint(1, int(m.group(2)))
        else:
            try: total += sign * int(clean)
            except: pass
    return total

def avg_damage(expr):
    if not expr: return 0
    expr = re.sub(r'(^|[^0-9])d', r'\g<1>1d', expr, flags=re.IGNORECASE)
    total = 0.0
    for part in re.findall(r'[+-]?\s*\d*d\d+|[+-]?\s*\d+', expr, re.IGNORECASE):
        sign = -1 if part.strip().startswith('-') else 1
        clean = part.strip().lstrip('+-').strip()
        m = re.match(r'^(\d*)d(\d+)$', clean, re.IGNORECASE)
        if m:
            cnt = int(m.group(1)) if m.group(1) else 1
            total += sign * cnt * (int(m.group(2)) + 1) / 2
        else:
            try: total += sign * int(clean)
            except: pass
    return total

def success_rank(d100_val, skill_val):
    if skill_val <= 0: return 4 if d100_val <= 5 else (-2 if d100_val >= 96 else -1)
    if d100_val == 1: return 4
    if d100_val >= 96: return -2  # 96-100 = 大失败
    if d100_val > skill_val:
        return -1
    rank = 1
    if d100_val <= skill_val / 5: rank = 3
    elif d100_val <= skill_val / 2: rank = 2
    if d100_val <= 5: rank = 4
    return rank

def rank_text(rank):
    return {-2:'大失败',-1:'失败',1:'成功',2:'困难成功',3:'极难成功',4:'大成功'}.get(rank,'')

def roll_d100(bp_str=''):
    tens = random.randint(0, 9); units = random.randint(0, 9)
    bp_match = re.match(r'^([bp])(\d*)$', bp_str.lower()) if bp_str else None
    detail = ''
    if bp_match:
        btype = bp_match.group(1); count = int(bp_match.group(2) or '1')
        best_tens = tens; extras = [str(random.randint(0,9)) for _ in range(count)]
        for et_str in extras:
            et = int(et_str)
            if btype == 'b':
                if et < best_tens: best_tens = et
            else:
                if et > best_tens: best_tens = et
        detail = f'{"奖励" if btype=="b" else "惩罚"}{",".join(extras)}'
        tens = best_tens
    result = 100 if (tens == 0 and units == 0) else tens * 10 + units
    return (result, detail)

def max_damage(expr):
    if not expr: return 0
    expr = re.sub(r'(^|[^0-9])d', r'\g<1>1d', expr, flags=re.IGNORECASE)
    total = 0
    for part in re.findall(r'[+-]?\s*\d*d\d+|[+-]?\s*\d+', expr, re.IGNORECASE):
        sign = -1 if part.strip().startswith('-') else 1
        clean = part.strip().lstrip('+-').strip()
        m = re.match(r'^(\d*)d(\d+)$', clean, re.IGNORECASE)
        if m:
            cnt = int(m.group(1)) if m.group(1) else 1
            total += sign * cnt * int(m.group(2))
        else:
            try: total += sign * int(clean)
            except: pass
    return max(0, total)

def parse_coord(s):
    m = re.match(r'^([A-Z])(\d+)$', s.upper()); return (ord(m.group(1))-65, int(m.group(2))-1) if m else None

def format_coord(col, row):
    return f"{chr(65+col)}{row+1}"

def is_in_melee_range(a, b):
    ap, bp = parse_coord(a), parse_coord(b)
    return ap and bp and abs(ap[0]-bp[0]) <= 2 and abs(ap[1]-bp[1]) <= 2

def has_timing(ts, code):
    return str(code) in str(ts) if ts else False

def has_object(os, code):
    return str(code) in str(os) if os else False

# ============================================================
#  Character data class
# ============================================================
class Character:
    def __init__(self, user_id, name):
        self.user_id = user_id; self.name = name; self.serial = ''
        self.attrs = {}; self.str_attrs = {}; self.spells = []
        self.hs_transformed = False; self.hs_orig = {}
        self.hs_spell_count = 0; self.hs_combat_flag = 0
        self.phase = 1  # Current combat phase (for dual-phase characters like 木落)

    def get_attr(self, name, fallback=0):
        return self.attrs.get(name, fallback)

    def set_attr(self, name, value):
        self.attrs[name] = value

    def get_str(self, name):
        return self.str_attrs.get(name, '')

    def set_str(self, name, value):
        self.str_attrs[name] = value

    def get_best_melee(self):
        best_name = '斗殴'; best_val = 0
        for sk in MELEE_SKILLS:
            v = self.attrs.get(sk, 0)
            if v > best_val: best_val = v; best_name = sk
        return best_name, max(best_val, 25)

# ============================================================
#  CombatEngine — base state management
# ============================================================
class CombatEngine:
    def __init__(self):
        self.characters = {}
        self.storage = {}
        self.group_id = 'default'

    def get_char(self, uid):
        if uid not in self.characters:
            self.characters[uid] = Character(uid, uid)
        return self.characters[uid]

    def storage_get(self, key): return self.storage.get(key)
    def storage_set(self, key, value):
        if value: self.storage[key] = value
        elif key in self.storage: del self.storage[key]

    def get_json(self, key, default=None):
        raw = self.storage_get(key)
        return json.loads(raw) if raw else default

    def set_json(self, key, data):
        self.storage_set(key, json.dumps(data, ensure_ascii=False))

    def _combat_hp_key(self): return f"combat_hp_{self.group_id}"
    def _init_combat_hp(self, uid, hp):
        s = self.get_json(self._combat_hp_key(), {}); s[uid] = hp; self.set_json(self._combat_hp_key(), s)
    def _get_combat_hp(self, uid):
        return self.get_json(self._combat_hp_key(), {}).get(uid)
    def _set_combat_hp(self, uid, hp):
        s = self.get_json(self._combat_hp_key(), {}); s[uid] = max(0, hp); self.set_json(self._combat_hp_key(), s)

    def _get_state(self): return self.get_json(f"combat_state_{self.group_id}")
    def _set_state(self, s): self.set_json(f"combat_state_{self.group_id}", s)
    def _get_initiative(self): return self.get_json(f"combat_initiative_{self.group_id}", [])
    def _set_initiative(self, l): self.set_json(f"combat_initiative_{self.group_id}", l)
    def _get_actions(self): return self.get_json(f"combat_actions_{self.group_id}", {})
    def _set_actions(self, a): self.set_json(f"combat_actions_{self.group_id}", a)
    def _get_effects(self): return self.get_json(f"combat_effects_{self.group_id}", [])
    def _set_effects(self, e):
        self.storage_set(f"combat_effects_{self.group_id}", json.dumps(e, ensure_ascii=False) if e else '')
    def _get_map(self): return self.get_json(f"combat_map_{self.group_id}")
    def _set_map(self, m): self.set_json(f"combat_map_{self.group_id}", m)

    def _get_active_buffs(self, uid):
        return [e for e in self._get_effects()
                if e.get('type') in ('buff','debuff') and e.get('targetUserId')==uid and e.get('remainingRounds',0)!=0]

    def _get_active_shields(self, uid):
        shields = [e for e in self._get_effects()
                   if e.get('type')=='shield' and e.get('targetUserId')==uid and e.get('remainingRounds',0)!=0]
        return sum(s.get('value',0) for s in shields), shields

    def _absorb_damage_with_shield(self, uid, dmg):
        if dmg <= 0: return (0, 0, [])
        effects = self._get_effects(); remaining = dmg; absorbed = 0; msgs = []
        for e in effects:
            if e.get('type')!='shield' or e.get('targetUserId')!=uid or e.get('remainingRounds',0)==0: continue
            sv = e.get('value',0)
            if sv <= 0: continue
            a = min(remaining, sv); remaining -= a; absorbed += a; e['value'] = sv - a
            if e['value'] <= 0: e['remainingRounds'] = 0; msgs.append(f"护盾【{e.get('spellName','')}】被击破！")
            else: msgs.append(f"护盾【{e.get('spellName','')}】吸收 {a} 点伤害（剩余 {e['value']}）")
            if remaining <= 0: break
        self._set_effects(effects)
        return (remaining, absorbed, msgs)

    def _apply_buff_skill_mod(self, uid, base_val):
        buffs = self._get_active_buffs(uid); adjusted = base_val
        for b in buffs:
            # AUX 15: numeric modifier
            if b.get('auxCode') == 15 and b.get('auxVal') not in (None,''):
                try: adjusted += int(b['auxVal'])
                except: pass
            # Legacy skillMod text
            if b.get('skillMod'):
                m = re.search(r'([+-]?\d+)$', str(b['skillMod']))
                if m: adjusted += int(m.group(1))
        return max(1, adjusted)

    def hp_diff(self, team):
        total = 0
        for e in self._get_initiative():
            if e.get('team') != team: continue
            hp = self._get_combat_hp(e['userId']) or 0
            _, shields = self._get_active_shields(e['userId'])
            total += hp + sum(s.get('value',0) for s in shields)
        return total

    def load_spells(self, uid):
        char = self.get_char(uid); spells = []
        current_phase = getattr(char, 'phase', 1)
        for i in range(1, 21):
            prefix = f"技能{CN_NUMS[i]}"
            name = char.get_str(f"{prefix}名称")
            if not name: continue
            # Phase filtering: load spells matching current phase (0=universal, any=phase-specific)
            spell_phase = char.get_attr(f"{prefix}阶段") or 0
            if spell_phase != 0 and spell_phase != current_phase:
                continue
            spell = {'index': i, 'name': name, 'effects': []}
            for k in ['级别','类别','消耗mp','消耗san','熟练度','里程碑','吟唱回合','默认延续性']:
                v = char.get_attr(f"{prefix}{k}")
                if v is None:
                    # Try string attr (for dice expressions like '2d6')
                    vs = char.get_str(f"{prefix}{k}")
                    if vs: v = vs
                spell[k] = v if v is not None else 0
            timing = char.get_str(f"{prefix}时机"); spell['时机'] = timing or '2'
            for k in ['级别','类别','消耗mp','消耗san','吟唱回合','默认延续性']:
                if k not in spell: spell[k] = 0
            spell.setdefault('级别',1); spell.setdefault('时机','2')
            # Read _mp_formula
            mpf = char.get_str(f"{prefix}_mp_formula")
            if mpf: spell['_mp_formula'] = mpf
            for ci, letter in enumerate(CAT_LETTERS):
                pl = f"{prefix}类别{letter}"
                has_data = bool(char.get_attr(f"{pl}客体") or char.get_str(f"{pl}伤害骰") or
                    char.get_str(f"{pl}护盾值") or char.get_str(f"{pl}回复hp") or
                    char.get_str(f"{pl}技能加减值") or char.get_attr(f"{pl}召唤个数") or
                    char.get_attr(f"{pl}引发目标法术") or char.get_attr(f"{pl}领域中心跟随") or
                    char.get_str(f"{pl}每回合伤害骰"))
                if not has_data and ci > 0: continue
                stored_type = char.get_attr(f"{pl}type") or 0
                ct = stored_type if stored_type > 0 else (spell['类别'] if ci == 0 else (1 if char.get_str(f"{pl}伤害骰") else
                    2 if char.get_str(f"{pl}护盾值") else
                    3 if char.get_str(f"{pl}回复hp") or char.get_str(f"{pl}回复san") or char.get_str(f"{pl}回复mp") else
                    4 if char.get_str(f"{pl}技能加减值") or char.get_str(f"{pl}其他辅助效果a") else
                    5 if char.get_attr(f"{pl}召唤个数") else
                    7 if char.get_attr(f"{pl}引发目标法术") else
                    8 if char.get_attr(f"{pl}领域中心跟随") or (char.get_str(f"{pl}每回合伤害骰") and char.get_attr(f"{pl}作用半径",0)>0) else
                    9 if char.get_attr(f"{pl}引发延迟回合") == 0 and char.get_attr(f"{pl}触发HP比例") else
                    char.get_attr(f"{pl}type") or 0))
                eff = {'type': ct, 'letter': letter}
                for k in ['客体','作用半径','可调节性','成功率','成功率奖惩骰','可反应性','可贯穿性',
                          '致死值','致死值优劣','附加效果时长','持续回合','引发目标法术','引发延迟回合',
                          '制造个数','制造花费回合数','领域中心跟随','触发HP比例','target_phase',
                          '友方延迟回复回合','敌方延迟回复回合','ignite','ignite_tick_dmg',
                          'cooldown_rounds','on_enter_mp_drain_pct','on_enter_trigger_rate']:
                    v = char.get_attr(f"{pl}{k}");
                    if v is not None: eff[k] = v
                for k in ['伤害骰','附加效果','护盾值','回复hp','回复san','回复mp','技能加减值',
                          '其他辅助效果a','辅助效果值a','召唤个数','召唤物模板','制造物模板',
                          '每回合伤害骰','吸血比例','属性削减',
                          '友方行为','友方伤害骰','友方延迟回复骰','友方延迟回复公式',
                          '敌方回复','敌方回复骰','ignite_dmg_dice','on_enter_attr_debuff']:
                    v = char.get_str(f"{pl}{k}");
                    if v: eff[k] = v
                # Parse JSON-serialized complex fields (hp_thresholds etc.)
                for k in ['hp_thresholds']:
                    v = char.get_str(f"{pl}{k}")
                    if v:
                        try: eff[k] = json.loads(v)
                        except: pass
                eff.setdefault('客体',4); eff.setdefault('作用半径',0)
                eff.setdefault('可反应性', 1 if ct==1 else 0); eff.setdefault('可贯穿性', 1 if ct==1 else 0)
                eff.setdefault('成功率',0)
                spell['effects'].append(eff)
            spells.append(spell)
        char.spells = spells
        return spells

    def _smart_target(self, caster_id, spell):
        if not spell: return caster_id
        has_dmg = any(e['type']==1 for e in spell.get('effects',[]))
        has_heal = any(e['type'] in (2,3,4) or (e['type']==8 and e.get('回复hp')) for e in spell.get('effects',[]))
        has_zone_dmg = any(e['type']==8 and e.get('每回合伤害骰') and not e.get('回复hp') for e in spell.get('effects',[]))
        if has_dmg or has_zone_dmg:
            init_list = self._get_initiative()
            my_entry = next((e for e in init_list if e['userId']==caster_id), None)
            mt = my_entry.get('team','Y') if my_entry else 'Y'
            enemies = [e for e in init_list if e['team']!=mt and (self._get_combat_hp(e['userId'])or 0)>0]
            return enemies[0]['userId'] if enemies else caster_id
        return caster_id

    def process_command(self, uid, cmd):
        """Simple command interface for compatibility."""
        if cmd == '.hs':
            char = self.get_char(uid)
            char.hs_transformed = True
            level = char.get_attr('等级', 1) or 1
            ohp = char.get_attr('体力上限', char.get_attr('体力', 10))
            omp = char.get_attr('魔力上限', char.get_attr('魔力', 10))
            nhp = int(ohp * (5 + level)); nmp = int(omp * (2 + 0.5 * level))
            char.set_attr('体力上限', nhp); char.set_attr('体力', nhp)
            char.set_attr('魔力上限', nmp); char.set_attr('魔力', nmp)
            char.set_attr('闪避', min(char.get_attr('闪避',25)+20, 80))
            char.set_attr('体格', char.get_attr('体格',0)+1)
            char.set_attr('行动力', char.get_attr('行动力',8)+2)
            for sk in ALL_COMBAT_SKILLS:
                v = char.get_attr(sk)
                if v > 0: char.set_attr(sk, min(v+20, 80))
            self._init_combat_hp(uid, nhp)

    def _use_skill(self, uid, skill_num, args):
        """Execute .sN command — used by tournament AI loop."""
        if skill_num == 0:
            return self._basic_attack(uid)
        spells = self.load_spells(uid) or []
        spell = next((s for s in spells if s['index'] == skill_num), None)
        if not spell: return f'未找到技能{skill_num}'
        if not has_timing(spell.get('时机','2'), '2') and not has_timing(spell.get('时机','2'), '1'):
            return f'不能在主动作阶段使用'
        target_id = self._smart_target(uid, spell)
        return self._execute_spell(uid, target_id, spell)

    def _additional_action(self, uid, args):
        """Execute .a m / .a sN / .a eat / .a give command (附加动作)."""
        parts = args.split() if args else []
        if not parts: return '用法: .a m <坐标> 或 .a s<序号> 或 .a eat [目标] 或 .a give <目标>'
        if parts[0].lower() == 'm' and len(parts) >= 2:
            return f'移动至 {parts[1].upper()}'
        elif parts[0].lower().startswith('s'):
            sn = int(parts[0][1:])
            spells = self.load_spells(uid) or []
            spell = next((s for s in spells if s['index'] == sn), None)
            if not spell: return f'未找到技能{sn}'
            if not has_timing(spell.get('时机','2'), '3'): return f'不能在附加动作阶段使用'
            return self._execute_spell(uid, uid, spell)
        elif parts[0].lower() == 'eat':
            # .a eat [目标名] — 使用物品（给自己）或给出物品（给目标）
            target_id = None
            if len(parts) >= 2:
                target_name = ' '.join(parts[1:])
                il = self._get_initiative()
                target_entry = next((e for e in il if e.get('name', '') == target_name), None)
                if target_entry:
                    target_id = target_entry['userId']
                else:
                    return f'未找到目标: {target_name}'
            result_text, success = self._eat_cake(uid, target_id)
            return result_text
        elif parts[0].lower() == 'give':
            # .a give <目标名> — 给出物品给队友
            if len(parts) < 2:
                return '用法: .a give <目标名>'
            target_name = ' '.join(parts[1:])
            il = self._get_initiative()
            target_entry = next((e for e in il if e.get('name', '') == target_name), None)
            if not target_entry:
                return f'未找到目标: {target_name}'
            result_text, success = self._eat_cake(uid, target_entry['userId'])
            return result_text
        return '用法: .a m <坐标> 或 .a s<序号> 或 .a eat [目标] 或 .a give <目标>'

    def _end_turn(self, uid):
        """End current character's turn and advance initiative."""
        state = self._get_state(); il = self._get_initiative()
        if not state or not il: return
        state['activeIndex'] = (state['activeIndex'] + 1) % len(il)
        if state['activeIndex'] == 0:
            state['round'] = state.get('round', 1) + 1
            acts = self._get_actions()
            for k in acts: acts[k] = {'主动': 2, '附加': 3}
            self._set_actions(acts)
            self._tick_down(); self._apply_zone_effects()
        self._set_state(state)

    def _find_enemy(self, uid):
        il = self._get_initiative(); me = next((e for e in il if e['userId']==uid), None)
        if not me: return None
        for e in il:
            if e['team']!=me.get('team','Y') and (self._get_combat_hp(e['userId'])or 0)>0: return e['userId']
        return None

    def _get_damage_dice(self, uid, skill_name):
        """Look up damage dice with suffix support.
        Maps skill names to damage suffixes: e.g. 剑→'a', 斗殴→'b'.
        Falls back to 伤害値."""
        char = self.get_char(uid)
        suffix_map = {'剑': 'a', '斗殴': 'b'}
        suffix = suffix_map.get(skill_name, '')
        if suffix:
            dd = char.get_str(f"伤害值{suffix}")
            if dd: return dd
        return char.get_str("伤害值") or "1d4"

    def _get_phase(self, uid):
        char = self.get_char(uid)
        return getattr(char, 'phase', 1) if char else 1

    def _set_phase(self, uid, p):
        char = self.get_char(uid)
        if char: char.phase = p

    # ---- Unified _execute_spell (all 8 categories, all fixes applied) ----
    def _execute_spell(self, caster_id, target_id, spell):
        char = self.get_char(caster_id)
        tname = self.get_char(target_id).name if target_id and target_id != caster_id else '自身'
        out = f'{char.name} 释放【{spell["name"]}】→ {tname}\n'

        # MP cost: support dice expressions and formulas
        mp_cost_raw = spell.get('消耗mp', 0)
        mp_formula = spell.get('_mp_formula', '')
        if isinstance(mp_cost_raw, str) and ('d' in str(mp_cost_raw).lower()):
            mp_cost = max(1, (roll_dice(str(mp_cost_raw)) + 1) // 2) if 'ceil' in str(mp_cost_raw) else roll_dice(str(mp_cost_raw))
        elif mp_formula == 'ceil_2d6_div_2':
            mp_cost = max(1, (roll_dice('2d6') + 1) // 2)
        elif mp_formula == 'summon_count_ceil_half':
            # Roll count once, store for type 5 handler to reuse
            summon_eff = next((e for e in spell.get('effects',[]) if e.get('type')==5), None)
            if summon_eff:
                count_raw = summon_eff.get('召唤个数', 1)
                if isinstance(count_raw, str) and 'd' in str(count_raw):
                    pre_count = roll_dice(str(count_raw))
                else:
                    pre_count = int(count_raw) if count_raw else 1
                mp_cost = max(1, (pre_count + 1) // 2)
                # Store pre-rolled count so type 5 handler reuses it
                spell['_pre_rolled_count'] = pre_count
            else:
                mp_cost = int(mp_cost_raw) if mp_cost_raw else 0
        else:
            mp_cost = int(mp_cost_raw) if mp_cost_raw else 0

        san_cost_raw = spell.get('消耗san', 0)
        if isinstance(san_cost_raw, str) and 'd' in str(san_cost_raw).lower():
            san_cost = roll_dice(str(san_cost_raw))
        else:
            san_cost = int(san_cost_raw) if san_cost_raw else 0

        if mp_cost > 0:
            cur_mp = char.get_attr('魔力', 0) or 0
            if cur_mp < mp_cost: return f'{char.name} 魔力不足！需要 {mp_cost} MP。'
            char.set_attr('魔力', cur_mp - mp_cost); out += f'  消耗 {mp_cost} MP\n'
        if san_cost > 0:
            cur_san = char.get_attr('理智', 50)
            char.set_attr('理智', max(0, cur_san - san_cost)); out += f'  消耗 {san_cost} SAN\n'

        for eff in spell.get('effects', []):
            ct = eff['type']
            if ct == 1:  # Damage
                dmg_dice = eff.get('伤害骰', '1d4'); pen = eff.get('可贯穿性', 0)
                leth = eff.get('致死值', 0); sr = eff.get('成功率', 0)
                dur = eff.get('持续回合', 0); dot_dice = eff.get('每回合伤害骰', '')
                ls = float(eff.get('吸血比例','0') or '0')

                # Friend/foe behavior (生之矛 pattern)
                friend_behavior = eff.get('友方行为', '')
                is_friendly = False
                if friend_behavior:
                    il = self._get_initiative()
                    ce = next((e for e in il if e['userId'] == caster_id), None)
                    te = next((e for e in il if e['userId'] == target_id), None)
                    is_friendly = ce and te and ce.get('team') == te.get('team')

                if sr > 0 and random.randint(1, 100) > sr:
                    out += f'  成功率检定: 失败！\n'; continue
                if sr > 0: out += f'  成功率检定: 成功\n'

                if is_friendly:
                    # Friend-target damage: deal friendly damage, schedule delayed heal
                    fdmg_dice = eff.get('友方伤害骰', dmg_dice)
                    dmg_val = roll_dice(fdmg_dice)
                    eff_dmg, absorbed, _ = self._absorb_damage_with_shield(target_id, dmg_val)
                    cur_hp = self._get_combat_hp(target_id) or 10
                    actual_lost = min(cur_hp, eff_dmg)
                    cur_hp = max(0, cur_hp - actual_lost)
                    self._set_combat_hp(target_id, cur_hp)
                    tname_f = self.get_char(target_id).name if target_id else '目标'
                    out += f'  友方伤害: {fdmg_dice}={dmg_val} → {actual_lost}点 (HP:{cur_hp})\n'

                    # Schedule delayed heal: max(lost*2, heal_dice) over N rounds
                    heal_formula = eff.get('友方延迟回复公式', '')
                    heal_dice = eff.get('友方延迟回复骰', '4d6')
                    heal_rounds = eff.get('友方延迟回复回合', 3)
                    if heal_formula == 'max_double':
                        heal_total = max(actual_lost * 2, roll_dice(heal_dice))
                    else:
                        heal_total = roll_dice(heal_dice)
                    if heal_total > 0:
                        per_round = max(1, heal_total // heal_rounds)
                        effs = self._get_effects()
                        effs.append({
                            'type': 'delayedHeal', 'remainingRounds': heal_rounds,
                            'healPerRound': per_round, 'totalHeal': heal_total, 'healed': 0,
                            'sourceUserId': caster_id, 'targetUserId': target_id,
                            'spellName': spell['name'], 'spellIndex': spell['index'],
                            'persistent': 0,
                        })
                        self._set_effects(effs)
                        out += f'  延迟回复: 共{heal_total}HP, 每回合{per_round}HP ({heal_rounds}回合)\n'
                else:
                    dmg_val = roll_dice(dmg_dice)
                    eff_dmg, absorbed, _ = self._absorb_damage_with_shield(target_id, dmg_val)
                    cur_hp = self._get_combat_hp(target_id) or 10
                    # Lethality
                    exp_dmg = avg_damage(dmg_dice)
                    if leth and exp_dmg > 6:
                        if random.randint(1, max(2, cur_hp*2)) <= int(exp_dmg): cur_hp = 0
                        else: cur_hp = max(0, cur_hp - eff_dmg)
                    else: cur_hp = max(0, cur_hp - eff_dmg)
                    self._set_combat_hp(target_id, cur_hp)
                    out += f'  伤害: {dmg_dice}={dmg_val} → {eff_dmg}点 (HP:{cur_hp})\n'

                    # Enemy lifesteal: min(lost/2, heal_dice) for caster (生之矛 enemy pattern)
                    if ls > 0 and eff_dmg > 0:
                        heal = int(dmg_val * ls); chp = self._get_combat_hp(caster_id) or 10
                        mhp = char.get_attr('体力上限', chp)
                        self._set_combat_hp(caster_id, min(chp+heal, mhp))
                        out += f'  吸血回复 {heal} HP\n'

                    # Enemy delayed heal for 生之矛 pattern — target gets min(lost/2, dice)
                    enemy_recovery = eff.get('敌方回复', '')
                    if enemy_recovery and eff_dmg > 0:
                        recovery_dice = eff.get('敌方回复骰', '2d6')
                        heal_rounds = eff.get('敌方延迟回复回合', 3)
                        if enemy_recovery == 'min_half':
                            heal_total = min(eff_dmg // 2, roll_dice(recovery_dice))
                        else:
                            heal_total = roll_dice(recovery_dice)
                        if heal_total > 0:
                            per_round = max(1, heal_total // heal_rounds)
                            effs = self._get_effects()
                            effs.append({
                                'type': 'delayedHeal', 'remainingRounds': heal_rounds,
                                'healPerRound': per_round, 'totalHeal': heal_total, 'healed': 0,
                                'sourceUserId': caster_id, 'targetUserId': target_id,
                                'spellName': spell['name'], 'spellIndex': spell['index'],
                                'persistent': 0,
                            })
                            self._set_effects(effs)
                            tname_h = self.get_char(target_id).name if target_id else '目标'
                            out += f'  敌方延迟回复: {tname_h} 共{heal_total}HP, 每回合{per_round}HP ({heal_rounds}回合)\n'

                    if dur > 0 or dot_dice:
                        effects = self._get_effects()
                        effects.append({'type':'dot','remainingRounds':max(dur,1),'dotDice':dot_dice or dmg_dice,
                            'pen':pen,'leth':leth,'sourceUserId':caster_id,'targetUserId':target_id,
                            'spellName':spell['name'],'spellIndex':spell['index']})
                        self._set_effects(effects)
                        if dot_dice: out += f'  持续伤害: 每回合 {dot_dice} ({max(dur,1)}回合)\n'

            elif ct == 2:  # Shield
                sv = roll_dice(eff.get('护盾值','1d4')); dur = eff.get('持续回合',1)
                effects = self._get_effects()
                effects.append({'type':'shield','value':sv,'remainingRounds':dur,'sourceUserId':caster_id,
                    'targetUserId':target_id or caster_id,'spellName':spell['name'],
                    'spellIndex':spell['index'],'persistent':spell.get('默认延续性',0)})
                self._set_effects(effects); out += f'  获得 {sv} 点护盾\n'

            elif ct == 3:  # Heal (HP/SAN/MP with caps)
                hp_heal = roll_dice(eff.get('回复hp','0'))
                san_heal = roll_dice(eff.get('回复san','0'))
                mp_heal = roll_dice(eff.get('回复mp','0'))
                tid = target_id or caster_id; tchar = self.get_char(tid)
                if hp_heal > 0:
                    chp = self._get_combat_hp(tid) or 10
                    mhp = tchar.get_attr('体力上限', chp) if tchar else chp
                    self._set_combat_hp(tid, min(chp+hp_heal, mhp)); out += f'  回复 HP +{hp_heal}\n'
                if san_heal > 0:
                    cs = tchar.get_attr('理智', 50) if tchar else 50
                    tchar.set_attr('理智', min(cs+san_heal, 99)); out += f'  回复 SAN +{san_heal}\n'
                if mp_heal > 0:
                    cm = tchar.get_attr('魔力', 0) if tchar else 0
                    mx = tchar.get_attr('魔力上限', cm) if tchar else cm
                    tchar.set_attr('魔力', min(cm+mp_heal, mx)); out += f'  回复 MP +{mp_heal}\n'

            elif ct == 4:  # Buff (with auxCode)
                dur = eff.get('持续回合',1); aux_type = eff.get('其他辅助效果a','')
                aux_code = AUX_NAME_TO_CODE.get(aux_type, 0)
                effects = self._get_effects()
                effects.append({'type':'buff','remainingRounds':dur,'skillMod':eff.get('技能加减值',''),
                    'auxType':aux_type,'auxVal':eff.get('辅助效果值a',''),'auxCode':aux_code,
                    'sourceUserId':caster_id,'targetUserId':target_id or caster_id,
                    'spellName':spell['name'],'spellIndex':spell['index'],
                    'persistent':spell.get('默认延续性',0)})
                self._set_effects(effects); out += f'  施加辅助效果: {aux_type} {eff.get("辅助效果值a","")}\n'

            elif ct == 5:  # Summon — handled by subclass
                # Use pre-rolled count from MP calculation if available
                pre_rolled = spell.get('_pre_rolled_count')
                if pre_rolled is not None:
                    count = pre_rolled
                    del spell['_pre_rolled_count']  # Clean up
                else:
                    count_raw = eff.get('召唤个数',1)
                    if isinstance(count_raw, str) and 'd' in str(count_raw):
                        count = roll_dice(str(count_raw))
                    else:
                        count = int(count_raw) if count_raw else 1
                tmpl = eff.get('召唤物模板','')
                # Check ignite flag on effect for Phase 2 summons; read params from chData
                ignite = eff.get('ignite', False)
                ignite_dmg_dice = eff.get('ignite_dmg_dice', '2d4')
                ignite_tick_dmg = eff.get('ignite_tick_dmg', 3)
                for _ in range(count):
                    sid = self._create_summon(caster_id, tmpl)
                    if ignite and sid:
                        # Mark newly created summon as ignited
                        il = self._get_initiative()
                        for e in il:
                            if e['userId'] == sid:
                                e['ignited'] = True
                                e['ignite_dmg_dice'] = ignite_dmg_dice
                                e['ignite_tick_dmg'] = ignite_tick_dmg
                                if e.get('skills'):
                                    for s in e['skills']:
                                        s['dice'] = ignite_dmg_dice
                                e['dmg_dice'] = ignite_dmg_dice
                                break
                        self._set_initiative(il)
                out += f'  召唤 {count} 个【{tmpl or "使魔"}】\n'
                # Generic cooldown support (C1)
                cooldown = eff.get('cooldown_rounds', 0)
                if cooldown > 0:
                    effects = self._get_effects()
                    effects.append({'type': 'cooldown', 'remainingRounds': cooldown,
                        'spellName': spell['name'], 'spellIndex': spell['index'],
                        'sourceUserId': caster_id, 'targetUserId': caster_id,
                        'persistent': 0})
                    self._set_effects(effects)

            elif ct == 6:  # Create
                count = eff.get('制造个数',1); cr = eff.get('制造花费回合数',0)
                effects = self._get_effects()
                for cc in range(count):
                    effects.append({'type':'create','craftId':f"craft_{spell['index']}_{cc}",
                        'template':eff.get('制造物模板',''),'remainingRounds':eff.get('持续回合',0),
                        'craftRoundsRemaining':cr,'sourceUserId':caster_id,
                        'targetUserId':target_id or caster_id,'spellName':spell['name'],
                        'spellIndex':spell['index'],'persistent':spell.get('默认延续性',0)})
                self._set_effects(effects); out += f'  开始制造 {count} 个（{cr}回合）\n'

            elif ct == 7:  # Trigger
                ts = eff.get('引发目标法术',0); delay = eff.get('引发延迟回合',0)
                effects = self._get_effects()
                effects.append({'type':'trigger','remainingRounds':delay,'triggerSpellIndex':ts,
                    'sourceSpellIndex':spell['index'],'sourceSpellName':spell['name'],
                    'sourceUserId':caster_id,'spellName':spell['name'],'spellIndex':spell['index'],
                    'persistent':spell.get('默认延续性',0)})
                self._set_effects(effects); out += f'  引发法术已设置: {delay}回合后→技能{ts}\n'

            elif ct == 8:  # Zone
                radius = eff.get('作用半径',0); dur = eff.get('持续回合',1)
                tick_dmg = eff.get('每回合伤害骰',''); tick_heal_hp = eff.get('回复hp','')
                tick_heal_mp = eff.get('回复mp',''); cf = eff.get('领域中心跟随',0)
                of = eff.get('客体',35); ad = eff.get('属性削减','')
                # Find caster position for center
                center = 'A1'
                map_data = self._get_map()
                if map_data:
                    for c, occ in map_data.get('occupants',{}).items():
                        if occ == caster_id: center = c; break
                effects = self._get_effects()
                # Dedup: remove existing zone with same spellName
                effects = [e for e in effects if not (e.get('type')=='zone' and e.get('spellName')==spell['name'])]
                effects.append({'type':'zone','center':center,'radius':radius,'remainingRounds':dur,
                    'tickDmg':tick_dmg,'tickHealHp':tick_heal_hp,'tickHealMp':tick_heal_mp,
                    'centerFollows':cf,'filter':of,'attributeDebuff':ad,
                    'sourceUserId':caster_id,'spellName':spell['name'],'spellIndex':spell['index'],
                    'persistent':spell.get('默认延续性',0)})
                self._set_effects(effects)
                dur_text = '' if dur >= 99 else f' 持续{dur}回合'
                out += f'  创建领域【{spell["name"]}】（半径{radius}格{dur_text}，中心{center}）\n'

            elif ct == 9:  # hpTrigger — HP threshold phase transition
                thresholds = eff.get('hp_thresholds', [])
                target_phase = eff.get('target_phase', 2)
                trigger_san_cost = eff.get('消耗san', '2d6')
                effects = self._get_effects()
                effects.append({
                    'type': 'hpTrigger',
                    'sourceUserId': caster_id,
                    'targetUserId': caster_id,
                    'thresholds': thresholds,
                    'targetPhase': target_phase,
                    'triggerSanCost': trigger_san_cost,
                    'triggered': False,
                    'spellName': spell['name'],
                    'spellIndex': spell['index'],
                    'persistent': 1,
                    'remainingRounds': 999,
                })
                self._set_effects(effects)
                out += f'  阶段转换已准备: HP阈值触发 ({len(thresholds)}级)\n'

            elif ct == 10:  # delayedHeal — scheduled over N rounds
                heal_amount = eff.get('回复总量', 0)
                heal_rounds = eff.get('持续回合', 3)
                per_round = max(1, heal_amount // heal_rounds) if heal_rounds > 0 else heal_amount
                effects = self._get_effects()
                effects.append({
                    'type': 'delayedHeal',
                    'remainingRounds': heal_rounds,
                    'healPerRound': per_round,
                    'totalHeal': heal_amount,
                    'healed': 0,
                    'sourceUserId': caster_id,
                    'targetUserId': target_id or caster_id,
                    'spellName': spell['name'],
                    'spellIndex': spell['index'],
                    'persistent': 0,
                })
                self._set_effects(effects)
                out += f'  延迟回复: 共{heal_amount}HP, 每回合{per_round}HP ({heal_rounds}回合)\n'

            elif ct == 11:  # battleSpirit — 战意值光环 (环花暖)
                effects = self._get_effects()
                effects.append({
                    'type': 'battleSpirit',
                    'sourceUserId': caster_id,
                    'persistent': 1,
                    'remainingRounds': 999,
                    'spellName': spell['name'],
                    'spellIndex': spell['index'],
                })
                self._set_effects(effects)
                out += f'  战意值光环已激活\n'

        return out

    # ---- Battle Spirit processing (环花暖 passive) ----
    def _process_battle_spirit(self):
        """Process 战意值 for enemy summons. Called each round from _tick_down.
        - Enemy summons start at 5 stacks
        - 50% chance to reduce 1 stack per round (bonus die at ≤3)
        - 4 stacks→1 penalty die, 2 stacks→2 penalty dice, 0→flee"""
        effects = self._get_effects()
        # Check if any battleSpirit effect is active
        bs_active = any(e.get('type') == 'battleSpirit' for e in effects)
        if not bs_active:
            return []

        msgs = []
        il = self._get_initiative()
        # Find 环花暖's team
        bs_source = next((e.get('sourceUserId') for e in effects if e.get('type') == 'battleSpirit'), None)
        if not bs_source:
            return msgs
        bs_entry = next((e for e in il if e['userId'] == bs_source), None)
        bs_team = bs_entry.get('team', 'Y') if bs_entry else 'Y'

        for entry in il:
            if not entry.get('isSummon'):
                continue
            if entry.get('team') == bs_team:
                continue  # Only affect enemy summons
            uid = entry['userId']
            if (self._get_combat_hp(uid) or 0) <= 0:
                continue

            # Initialize battle spirit if not present
            if 'battle_spirit' not in entry:
                entry['battle_spirit'] = 5
                msgs.append(f"战意值: {entry.get('name',uid)} 获得 5 层战意值")
                continue

            bs = entry['battle_spirit']
            if bs <= 0:
                continue

            # Check reduction: 50% base, bonus die at ≤3
            success = False
            if bs <= 3:
                # Bonus die: roll twice, take lower
                r1 = random.randint(1, 100)
                r2 = random.randint(1, 100)
                success = min(r1, r2) <= 50
            else:
                success = random.randint(1, 100) <= 50

            if success:
                entry['battle_spirit'] -= 1
                bs = entry['battle_spirit']
                msgs.append(f"战意值: {entry.get('name',uid)} 削弱至 {bs} 层")

                if bs <= 0:
                    # 脱战: remove from battle
                    msgs.append(f"战意值: {entry.get('name',uid)} 战意归零，脱战！")
                    self._set_combat_hp(uid, 0)
                    continue

            # Apply penalty dice at thresholds
            old_pens = entry.get('battle_spirit_penalty_dice', 0)
            new_pens = 0
            if bs <= 2:
                new_pens = 2
            elif bs <= 4:
                new_pens = 1

            if new_pens != old_pens:
                entry['battle_spirit_penalty_dice'] = new_pens
                if new_pens > 0:
                    pen_label = 'p' if new_pens == 1 else 'p2'
                    msgs.append(f"战意值: {entry.get('name',uid)} 获得 {new_pens} 惩罚骰 ({pen_label})")

        self._set_initiative(il)
        return msgs

    # ---- HP Threshold Trigger & Phase System ----
    def _check_hp_triggers(self):
        """Check hpTrigger effects against current HP ratios. Return list of messages."""
        effects = self._get_effects()
        msgs = []
        for eff in effects:
            if eff.get('type') != 'hpTrigger' or eff.get('triggered'):
                continue
            uid = eff.get('targetUserId')
            if not uid: continue
            cur_hp = self._get_combat_hp(uid)
            if cur_hp is None: continue
            char = self.get_char(uid)
            if not char: continue
            max_hp = char.get_attr('体力上限', cur_hp) or cur_hp
            if max_hp <= 0: continue
            ratio = cur_hp / max_hp
            thresholds = eff.get('thresholds', [])
            # Check thresholds in descending order (highest ratio first)
            for t in sorted(thresholds, key=lambda x: x['ratio'], reverse=True):
                if ratio <= t['ratio']:
                    # Roll success check
                    if random.randint(1, 100) <= t['rate']:
                        eff['triggered'] = True
                        msgs.append(f"HP降至{ratio*100:.0f}%（阈值{t['ratio']*100:.0f}%），触发阶段转换！")
                        # Consume SAN cost
                        san_cost_raw = eff.get('triggerSanCost', '2d6')
                        if isinstance(san_cost_raw, str):
                            san_cost = roll_dice(san_cost_raw)
                        else:
                            san_cost = int(san_cost_raw) if san_cost_raw else 0
                        cur_san = char.get_attr('理智', 50)
                        char.set_attr('理智', max(0, cur_san - san_cost))
                        msgs.append(f"  消耗 {san_cost} SAN（剩余理智: {char.get_attr('理智', 0)}）")
                        # Transition phase
                        target_phase = eff.get('targetPhase', 2)
                        tmsgs = self._transition_phase(uid, target_phase)
                        msgs.extend(tmsgs)
                        msgs.append(f"  {char.name} 进入阶段{target_phase}！")
                        # Re-fetch effects: _transition_phase may have removed zones
                        effects = self._get_effects()
                        # Re-mark this trigger as triggered in refreshed effects
                        for re in effects:
                            if (re.get('type') == 'hpTrigger'
                                and re.get('sourceUserId') == uid
                                and re.get('spellIndex') == eff.get('spellIndex')
                                and not re.get('triggered')):
                                re['triggered'] = True
                                break
                    break  # Only trigger the first matching threshold
        self._set_effects(effects)
        return msgs

    def _transition_phase(self, uid, new_phase):
        """Transition character to new phase. Reloads spells and ignites summons."""
        msgs = []
        char = self.get_char(uid)
        old_phase = getattr(char, 'phase', 1)
        if old_phase == new_phase:
            return msgs
        char.phase = new_phase

        # Reload spells for the new phase
        self.load_spells(uid)

        # Remove old phase's zone effects (领域·生 → remove when entering phase 2)
        effects = self._get_effects()
        new_effects = []
        for e in effects:
            # Remove zones created by this character (they belong to old phase)
            if e.get('type') == 'zone' and e.get('sourceUserId') == uid:
                msgs.append(f"领域【{e.get('spellName','')}】消散...")
                continue
            new_effects.append(e)
        self._set_effects(new_effects)

        # Ignite existing summons
        if new_phase == 2:
            ignite_msgs = self._ignite_summons(uid)
            msgs.extend(ignite_msgs)

        return msgs

    def _ignite_summons(self, owner_uid):
        """Ignite all summons owned by owner_uid (Phase 2 effect). Reads ignite params from chData."""
        msgs = []
        il = self._get_initiative()
        # Read ignite parameters from character's loaded spells (Phase 2)
        ignite_dmg_dice = '2d4'
        ignite_tick_dmg = 3
        char = self.get_char(owner_uid)
        spells = char.spells or self.load_spells(owner_uid)
        for s in spells:
            for eff in s.get('effects', []):
                if eff.get('type') == 5 and eff.get('ignite'):
                    ignite_dmg_dice = str(eff.get('ignite_dmg_dice', '2d4'))
                    ignite_tick_dmg = int(eff.get('ignite_tick_dmg', 3))
                    break
        for entry in il:
            if entry.get('isSummon') and entry.get('ownerId') == owner_uid:
                entry['ignited'] = True
                entry['ignite_dmg_dice'] = ignite_dmg_dice
                entry['ignite_tick_dmg'] = ignite_tick_dmg
                if entry.get('skills'):
                    for s in entry['skills']:
                        s['dice'] = ignite_dmg_dice
                entry['dmg_dice'] = ignite_dmg_dice
                msgs.append(f"  {entry.get('name', entry['userId'])} 被点燃！（伤害变为{ignite_dmg_dice}）")
        self._set_initiative(il)
        return msgs

    # ---- Tick-down (unified) ----
    def _tick_down(self):
        effects = self._get_effects(); init_list = self._get_initiative()
        msgs = []; new_effects = []

        # Check HP threshold triggers at start of round
        hp_msgs = self._check_hp_triggers()
        msgs.extend(hp_msgs)

        # Battle spirit processing (环花暖)
        bs_msgs = self._process_battle_spirit()
        msgs.extend(bs_msgs)

        # Zone enter triggers (Fix #2) and trinity merge (Fix #4)
        self._process_zone_specials()
        self._check_trinity_merge()

        for eff in effects:
            # Craft countdown
            if eff.get('type')=='create' and eff.get('craftRoundsRemaining',0) > 0:
                eff['craftRoundsRemaining'] -= 1
                if eff['craftRoundsRemaining'] <= 0:
                    msgs.append(f"制造完成：【{eff.get('template',eff.get('spellName',''))}】已就绪！")

            # Trigger countdown
            if eff.get('type')=='trigger':
                eff['remainingRounds'] = eff.get('remainingRounds',0) - 1
                if eff['remainingRounds'] <= 0:
                    if eff.get('persistent'):
                        new_effects = [e for e in new_effects if not (e.get('spellIndex')==eff.get('sourceSpellIndex') and e is not eff)]
                        msgs.append(f"【{eff.get('sourceSpellName','')}】法术形态转换中...")
                    msgs.append(f"引发：自动释放技能{eff.get('triggerSpellIndex')}！")
                    continue
                new_effects.append(eff); continue

            # DOT damage
            if eff.get('type')=='dot' and eff.get('dotDice') and eff.get('targetUserId'):
                dot_dmg = roll_dice(eff['dotDice'])
                if dot_dmg > 0:
                    remaining, absorbed, sh_msgs = self._absorb_damage_with_shield(eff['targetUserId'], dot_dmg)
                    cur_hp = self._get_combat_hp(eff['targetUserId']) or 10
                    cur_hp = max(0, cur_hp - remaining); self._set_combat_hp(eff['targetUserId'], cur_hp)
                    msgs.extend(sh_msgs)
                    msgs.append(f"持续伤害【{eff.get('spellName','')}】→ {eff['dotDice']}={dot_dmg}，造成{remaining}点伤害(HP:{cur_hp})")
                    if cur_hp <= 0:
                        # Remove dead from initiative
                        self._set_initiative([e for e in init_list if e['userId'] != eff['targetUserId']])
                        msgs.append(f"目标因持续伤害死亡，退出战斗！")

            # Delayed heal processing
            if eff.get('type') == 'delayedHeal' and eff.get('targetUserId'):
                heal_amt = eff.get('healPerRound', 0)
                if heal_amt > 0:
                    tid = eff['targetUserId']
                    chp = self._get_combat_hp(tid) or 10
                    tchar = self.get_char(tid)
                    mhp = tchar.get_attr('体力上限', chp) if tchar else chp
                    actual_heal = min(heal_amt, max(0, mhp - chp))
                    if actual_heal > 0:
                        self._set_combat_hp(tid, chp + actual_heal)
                        eff['healed'] = eff.get('healed', 0) + actual_heal
                        msgs.append(f"延迟回复【{eff.get('spellName','')}】→ +{actual_heal} HP "
                                    f"({eff['healed']}/{eff.get('totalHeal',0)})")

            # Zone per-round effects (applied later by _apply_zone_effects)

            # Chant countdown (吟唱回合)
            # Simplified: stored on character, decremented elsewhere

            # hpTrigger: persistent, don't countdown
            if eff.get('type') == 'hpTrigger':
                new_effects.append(eff); continue

            # Create: craft own countdown, don't expire via regular countdown
            if eff.get('type') == 'create':
                new_effects.append(eff); continue

            # BattleSpirit: persistent aura, don't countdown
            if eff.get('type') == 'battleSpirit':
                new_effects.append(eff); continue

            # Cooldown — silent expiry
            if eff.get('type') == 'cooldown':
                eff['remainingRounds'] = eff.get('remainingRounds', 0) - 1
                if eff['remainingRounds'] > 0: new_effects.append(eff)
                continue

            # Regular countdown
            eff['remainingRounds'] = eff.get('remainingRounds',0) - 1
            if eff['remainingRounds'] <= 0:
                if eff.get('persistent'):
                    eff['remainingRounds'] = -1; eff['persistentActive'] = True
                    if eff.get('type') != 'zone':  # Zones use separate expiry
                        new_effects.append(eff)
                    else:
                        new_effects.append(eff)
                else:
                    msgs.append(f"效果【{eff.get('spellName','')}】已过期")
                    # Cleanup summon from map
                    if eff.get('type')=='summon' and eff.get('summonId'):
                        map_data = self._get_map()
                        if map_data:
                            for c, o in list(map_data.get('occupants',{}).items()):
                                if o == eff['summonId']: del map_data['occupants'][c]; break
                            self._set_map(map_data)
                    continue
            else:
                new_effects.append(eff)

        self._set_effects(new_effects)

        # Ignite damage: Phase 2 summons take 3 damage/turn
        for entry in init_list:
            if entry.get('ignited') and entry.get('isSummon'):
                sid = entry['userId']
                hp = self._get_combat_hp(sid)
                if hp is not None and hp > 0:
                    ignite_dmg = entry.get('ignite_tick_dmg', 3)
                    self._set_combat_hp(sid, max(0, hp - ignite_dmg))
                    msgs.append(f"点燃: {entry.get('name', sid)} 受到 {ignite_dmg} 点火焰伤害 (HP:{max(0, hp - ignite_dmg)})")
                    if hp - ignite_dmg <= 0:
                        msgs.append(f"  {entry.get('name', sid)} 被火焰烧尽！")

        # MP regen per round (Python training convenience)
        for entry in init_list:
            uid = entry['userId']; hp = self._get_combat_hp(uid)
            if hp is not None and hp > 0:
                char = self.get_char(uid); cm = char.get_attr('魔力',0) or 0
                mx = char.get_attr('魔力上限',cm) or cm
                regen = roll_dice('2d6')
                if regen > 0: char.set_attr('魔力', min(cm+regen, mx))
        return '\n'.join(msgs) if msgs else ''

    # ---- Summon system (stub — overridden by subclasses) ----
    def _create_summon(self, caster_id, template_name):
        return None  # Override in FullBattleEngine / FastBattleEngine

    def _summon_attack(self, summon_id):
        pass  # Override

    def _apply_zone_effects(self):
        pass  # Override

    def _process_zone_specials(self):
        pass  # Override

    def _check_trinity_merge(self):
        pass  # Override

    # ---- Item/cake system (shared by both engines) ----
    def _eat_cake(self, eater_id, target_id=None):
        """Consume a ready cake and apply its effect from ITEM_TEMPLATES. Returns (result_text, success)."""
        from characters_data import ITEM_TEMPLATES
        effects = self._get_effects()
        tid = target_id or eater_id
        tchar = self.get_char(tid)
        tname = tchar.name if tchar else tid
        for i, e in enumerate(effects):
            if e.get('type') != 'create': continue
            if e.get('craftRoundsRemaining', 1) > 0: continue
            tmpl = e.get('template', '')
            if not tmpl or tmpl not in ITEM_TEMPLATES: continue
            item = ITEM_TEMPLATES[tmpl]
            hp_heal = roll_dice(item.get('回复hp', '0'))
            mp_heal = roll_dice(item.get('回复mp', '0'))
            san_heal = roll_dice(item.get('回复san', '0'))
            parts = []
            if hp_heal > 0:
                chp = self._get_combat_hp(tid) or 10
                mhp = tchar.get_attr('体力上限', chp) if tchar else chp
                self._set_combat_hp(tid, min(chp + hp_heal, mhp))
                parts.append(f'{hp_heal} HP')
            if mp_heal > 0:
                cmp = tchar.get_attr('魔力', 0) if tchar else 0
                mx = tchar.get_attr('魔力上限', cmp) if tchar else cmp
                tchar.set_attr('魔力', min(cmp + mp_heal, mx))
                parts.append(f'{mp_heal} MP')
            if san_heal > 0:
                cs = tchar.get_attr('理智', 50) if tchar else 50
                tchar.set_attr('理智', min(cs + san_heal, 99))
                parts.append(f'{san_heal} SAN')
            effects.pop(i); self._set_effects(effects)
            return (f"食用【{tmpl}】→ {tname} 回复 {'+'.join(parts)}", True)
        return ("无可用的蛋糕", False)

    def _has_ready_cake(self):
        """Check if any ready cake exists (uses ITEM_TEMPLATES)."""
        from characters_data import ITEM_TEMPLATES
        for e in self._get_effects():
            if e.get('type') == 'create' and e.get('craftRoundsRemaining', 1) <= 0:
                if e.get('template', '') in ITEM_TEMPLATES: return True
        return False

class FullBattleEngine(CombatEngine):
    """Detailed COC7 battle mode with full attack flow, reactions, and logging."""
    def __init__(self):
        super().__init__()
        self.max_rounds = 30
        self._ai_react_dodge_w = {}
        self._ai_react_counter_w = {}

    def setup_battle(self, team_a, team_b, map_size="10x10"):
        w, h = map(int, map_size.split("x"))
        self.group_id = f"battle_{random.randint(1000,9999)}"
        self._set_map({"width":w, "height":h, "entryRow":math.ceil(h/2), "obstacles":{}, "occupants":{}})
        all_chars = team_a + team_b; init_list = []; map_data = self._get_map()
        for i, uid in enumerate(team_a):
            char = self.get_char(uid)
            row = min(h-1, math.ceil(h/2) + i - len(team_a)//2)
            coord = format_coord(1, row)
            init_list.append({"userId":uid, "name":char.name, "team":"Y", "dex":char.get_attr("敏捷",50), "initRoll":random.randint(1,100), "coord":coord})
            map_data["occupants"][coord] = uid
        for i, uid in enumerate(team_b):
            char = self.get_char(uid)
            row = min(h-1, math.ceil(h/2) + i - len(team_b)//2)
            coord = format_coord(w-2, row)
            init_list.append({"userId":uid, "name":char.name, "team":"X", "dex":char.get_attr("敏捷",50), "initRoll":random.randint(1,100), "coord":coord})
            map_data["occupants"][coord] = uid
        self._set_map(map_data); init_list.sort(key=lambda e: (-e["initRoll"], -e["dex"]))
        self._set_initiative(init_list)
        for uid in all_chars:
            char = self.get_char(uid); self._init_combat_hp(uid, char.get_attr("体力",10))
        self._set_actions({uid: {"主动":2, "附加":3} for uid in all_chars})
        # Roll random 状态 (0-100) for each character before battle
        for uid in all_chars:
            char = self.get_char(uid)
            char.set_attr('状态', random.randint(0, 100))

        # Auto-cast passive spells at battle start (with smart targeting)
        for uid in all_chars:
            spells = self.get_char(uid).spells or self.load_spells(uid)
            for s in spells:
                if has_timing(s.get("时机","2"), "1"):
                    target = self._smart_target(uid, s)
                    self._execute_spell(uid, target, s)
        self._set_state({"phase":"active", "round":1, "activeIndex":0})

    def _coc7_attack(self, atk_uid, def_uid, skill_name, skill_val, dmg_dice, pen, leth, bp_suffix="", dmg_adv=False, dmg_dis=False):
        """Full COC7 attack: roll → reaction → rank compare → damage by success level → shield → lethality."""
        achar = self.get_char(atk_uid); dchar = self.get_char(def_uid)
        aname = achar.name; dname = dchar.name; lines = []
        eff_skill = self._apply_buff_skill_mod(atk_uid, skill_val)
        atk_buffs = self._get_active_buffs(atk_uid); eff_bp = bp_suffix or ""
        for b in atk_buffs:
            if b.get("auxCode") == 16 and b.get("auxVal") and not eff_bp: eff_bp = str(b["auxVal"])
        atk_result, bp_detail = roll_d100(eff_bp); atk_rank = success_rank(atk_result, eff_skill)
        bp_str = f", {bp_detail}" if bp_detail else ""
        lines.append(f"{aname} 的【{skill_name}】检定:"); lines.append(f"  D100={atk_result}/{eff_skill}{bp_str} {rank_text(atk_rank)}")
        if atk_rank <= 0:
            fn = " [大失败! 反击等级+1]" if atk_rank == -2 else ""
            lines.append(f"  {aname} 攻击失败！{fn}"); return (def_uid, atk_uid, lines)

        dodge_val = dchar.get_attr("闪避",25); bmn, bmv = dchar.get_best_melee()
        dodge_val = self._apply_buff_skill_mod(def_uid, dodge_val); bmv = self._apply_buff_skill_mod(def_uid, bmv)
        def_buffs = self._get_active_buffs(def_uid); def_bp = ""
        for b in def_buffs:
            if b.get("auxCode") == 16 and b.get("auxVal") and not def_bp: def_bp = str(b["auxVal"])

        ai_dw = self._ai_react_dodge_w.get(def_uid, 50); ai_cw = self._ai_react_counter_w.get(def_uid, 50)
        is_dodge = random.random() < (ai_dw / max(1, ai_dw + ai_cw)) if (ai_dw+ai_cw) > 0 else (dodge_val >= bmv)

        if is_dodge:
            rr, rd = roll_d100(def_bp); react_rank = success_rank(rr, dodge_val)
            rbs = f", {rd}" if rd else ""
            lines.append(f"{dname} 的反应【闪避={dodge_val}】:"); lines.append(f"  D100={rr}/{dodge_val}{rbs} {rank_text(react_rank)}")
            eff_atk = atk_rank
            if react_rank > 1: eff_atk -= (react_rank - 1)
            if react_rank == -2: eff_atk += (1 if eff_atk == -1 else 2)
            eff_atk = max(-2, min(4, eff_atk))
            if eff_atk <= 0: lines.append(f"  {dname} 闪避成功！"); return (def_uid, atk_uid, lines)
            winner_rank, winner_uid, loser_uid = eff_atk, atk_uid, def_uid
            loser_name, winner_name, winner_roll, is_counter = dname, aname, atk_result, False
        else:
            rr, rd = roll_d100(def_bp); react_rank = success_rank(rr, bmv)
            rbs = f", {rd}" if rd else ""
            lines.append(f"{dname} 的反应【反击:{bmn}={bmv}】:"); lines.append(f"  D100={rr}/{bmv}{rbs} {rank_text(react_rank)}")
            eff_atk, eff_react = atk_rank, react_rank
            if react_rank == -2: eff_atk += (1 if eff_atk == -1 else 2)
            if eff_atk == -2: eff_react = min(4, eff_react + 1)
            eff_atk, eff_react = max(-2, min(4, eff_atk)), max(-2, min(4, eff_react))
            if eff_react > eff_atk and eff_react > 0:
                winner_rank, winner_uid, loser_uid = eff_react, def_uid, atk_uid
                loser_name, winner_name = aname, dname; winner_roll = rr
                cdmg = dchar.get_str("伤害值") or "1d4"; cpen = dchar.get_attr("伤害贯穿",1)
                cleth = dchar.get_attr("致死骰",1) or 0
                dmg_dice, pen, leth, is_counter = cdmg, cpen, cleth, True
            elif eff_atk > eff_react and eff_atk > 0:
                winner_rank, winner_uid, loser_uid, is_counter = eff_atk, atk_uid, def_uid, False
                loser_name, winner_name, winner_roll = dname, aname, atk_result
            else:
                # Equal positive ranks → both succeed (互命中)
                if eff_react == eff_atk and eff_react > 0:
                    # ---- Attacker hits defender ----
                    amx = max_damage(dmg_dice); adv = 0; adetail = ""
                    if eff_atk == 2:
                        r1, r2 = roll_dice(dmg_dice), roll_dice(dmg_dice); adv = max(r1, r2); adetail = f"优势({dmg_dice}={r1},{r2})→{adv}"
                    elif eff_atk == 3:
                        if pen: extra = roll_dice(dmg_dice); adv = amx + extra; adetail = f"贯穿!满值{amx}+{dmg_dice}={extra}={adv}"
                        else: adv = amx; adetail = f"{dmg_dice}满值={amx}"
                    elif eff_atk == 4:
                        if atk_result == 1: adv = amx * 2; adetail = f"大成功!满值{amx}×2={adv}"
                        elif pen: adv = amx * 2; adetail = f"贯穿!满值{amx}×2={adv}"
                        else: extra = roll_dice(dmg_dice); adv = amx + extra; adetail = f"{dmg_dice}满值{amx}+{extra}={adv}"
                    else:
                        if dmg_adv: r1, r2 = roll_dice(dmg_dice), roll_dice(dmg_dice); adv = max(r1, r2); adetail = f"优势({dmg_dice}={r1},{r2})→{adv}"
                        elif dmg_dis: r1, r2 = roll_dice(dmg_dice), roll_dice(dmg_dice); adv = min(r1, r2); adetail = f"劣势({dmg_dice}={r1},{r2})→{adv}"
                        else: adv = roll_dice(dmg_dice); adetail = f"{dmg_dice}={adv}"
                    asr = self._absorb_damage_with_shield(def_uid, adv); aeff = asr[0]
                    if asr[1] > 0: lines.append(f"  护盾吸收(atk): {asr[1]}点")
                    achp = self._get_combat_hp(def_uid) or 10
                    achp = max(0, achp - aeff); self._set_combat_hp(def_uid, achp)
                    lines.append(f"  {aname} 造成伤害: {adetail} → {aeff}点")
                    lines.append(f"  {dname} HP: {achp}")

                    # ---- Defender counter-hits attacker ----
                    cdmg = dchar.get_str("伤害值") or "1d4"
                    cpen = dchar.get_attr("伤害贯穿",1)
                    cleth = dchar.get_attr("致死骰",1) or 0
                    dmx = max_damage(cdmg); ddmg = 0; ddetail = ""
                    if eff_react == 2:
                        r1, r2 = roll_dice(cdmg), roll_dice(cdmg); ddmg = max(r1, r2); ddetail = f"优势({cdmg}={r1},{r2})→{ddmg}"
                    elif eff_react == 3:
                        if cpen: extra = roll_dice(cdmg); ddmg = dmx + extra; ddetail = f"贯穿!满值{dmx}+{cdmg}={extra}={ddmg}"
                        else: ddmg = dmx; ddetail = f"{cdmg}满值={dmx}"
                    elif eff_react == 4:
                        if rr == 1: ddmg = dmx * 2; ddetail = f"大成功!满值{dmx}×2={ddmg}"
                        elif cpen: ddmg = dmx * 2; ddetail = f"贯穿!满值{dmx}×2={ddmg}"
                        else: extra = roll_dice(cdmg); ddmg = dmx + extra; ddetail = f"{cdmg}满值{dmx}+{extra}={ddmg}"
                    else:
                        ddmg = roll_dice(cdmg); ddetail = f"{cdmg}={ddmg}"
                    dsr = self._absorb_damage_with_shield(atk_uid, ddmg); deff = dsr[0]
                    if dsr[1] > 0: lines.append(f"  护盾吸收(def): {dsr[1]}点")
                    dchp = self._get_combat_hp(atk_uid) or 10
                    dchp = max(0, dchp - deff); self._set_combat_hp(atk_uid, dchp)
                    lines.append(f"  {dname} 反击伤害: {ddetail} → {deff}点")
                    lines.append(f"  {aname} HP: {dchp}")

                    lines.append(f"  双方同时命中! ({rank_text(eff_atk)} vs {rank_text(eff_react)})")
                    return (atk_uid, def_uid, lines)
                else:
                    lines.append("  无人得手！"); return (None, None, lines)

        mx = max_damage(dmg_dice); dmg_val = 0; dmg_detail = ""
        if winner_rank == 2:
            r1, r2 = roll_dice(dmg_dice), roll_dice(dmg_dice); dmg_val = max(r1, r2)
            dmg_detail = f"优势({dmg_dice}={r1},{r2})→{dmg_val}"
        elif winner_rank == 3:
            if pen: extra = roll_dice(dmg_dice); dmg_val = mx + extra; dmg_detail = f"贯穿!满值{mx}+{dmg_dice}={extra}={dmg_val}"
            else: dmg_val = mx; dmg_detail = f"{dmg_dice}满值={mx}"
        elif winner_rank == 4:
            if winner_roll == 1: dmg_val = mx * 2; dmg_detail = f"大成功!满值{mx}×2={dmg_val}"
            elif pen: dmg_val = mx * 2; dmg_detail = f"贯穿!满值{mx}×2={dmg_val}"
            else: extra = roll_dice(dmg_dice); dmg_val = mx + extra; dmg_detail = f"{dmg_dice}满值{mx}+{extra}={dmg_val}"
        else:
            if dmg_adv: r1, r2 = roll_dice(dmg_dice), roll_dice(dmg_dice); dmg_val = max(r1, r2); dmg_detail = f"优势({dmg_dice}={r1},{r2})→{dmg_val}"
            elif dmg_dis: r1, r2 = roll_dice(dmg_dice), roll_dice(dmg_dice); dmg_val = min(r1, r2); dmg_detail = f"劣势({dmg_dice}={r1},{r2})→{dmg_val}"
            else: dmg_val = roll_dice(dmg_dice); dmg_detail = f"{dmg_dice}={dmg_val}"

        lines.append(f"  {winner_name} 胜利! {rank_text(winner_rank)}")
        sr = self._absorb_damage_with_shield(loser_uid, dmg_val); eff_dmg = sr[0]
        if sr[1] > 0: lines.append(f"  护盾吸收: {sr[1]}点")
        cur_hp = self._get_combat_hp(loser_uid) or 10
        exp_dmg = avg_damage(dmg_dice)
        if leth and exp_dmg > 6:
            lr = random.randint(1, max(2, cur_hp*2))
            if lr <= int(exp_dmg): cur_hp = 0; lines.append(f"  致死骰: 成功! {loser_name}死亡")
            else: cur_hp = max(0, cur_hp - eff_dmg); lines.append(f"  致死骰: 失败")
        else: cur_hp = max(0, cur_hp - eff_dmg)
        lines.append(f"  伤害: {dmg_detail} → {eff_dmg}点"); self._set_combat_hp(loser_uid, cur_hp)
        lines.append(f"  {loser_name} HP: {cur_hp}")
        return (winner_uid, loser_uid, lines)

    def _use_skill(self, uid, skill_num, args):
        """Handle .sN command — spell execution with timing checks."""
        char = self.get_char(uid); spells = char.spells or self.load_spells(uid)
        spell = next((s for s in spells if s['index'] == skill_num), None)
        if not spell: return f"未找到技能{skill_num}"
        timing = spell.get('时机', '2')
        is_passive = has_timing(timing, '1')
        if not has_timing(timing, '2') and not is_passive:
            return f"【{spell['name']}】不能在主动作阶段使用"
        target = self._smart_target(uid, spell)
        return self._execute_spell(uid, target, spell)

    def _additional_action(self, uid, args):
        """Handle .a m / .a sN / .a eat / .a give commands (附加动作)."""
        parts = args.split()
        if not parts: return "用法: .a m <坐标> 或 .a s<序号> 或 .a eat [目标] 或 .a give <目标>"
        char = self.get_char(uid)
        if parts[0].lower() == 'm' and len(parts) >= 2:
            coord = parts[1].upper()
            return f"{char.name} 移动至 {coord}"
        elif re.match(r'^s\d+$', parts[0], re.IGNORECASE):
            sn = int(parts[0][1:]); spells = char.spells or self.load_spells(uid)
            spell = next((s for s in spells if s['index'] == sn), None)
            if not spell: return f"未找到技能{sn}"
            if not has_timing(spell.get('时机','2'), '3'):
                return f"【{spell['name']}】不能在附加动作阶段使用"
            return self._execute_spell(uid, uid, spell)
        elif parts[0].lower() == 'eat':
            # .a eat [目标名] — 使用物品（给自己）或给出物品（给目标）
            target_id = None
            if len(parts) >= 2:
                target_name = ' '.join(parts[1:])
                il = self._get_initiative()
                target_entry = next((e for e in il if e.get('name', '') == target_name), None)
                if target_entry:
                    target_id = target_entry['userId']
                else:
                    return f"未找到目标: {target_name}"
            result_text, success = self._eat_cake(uid, target_id)
            return result_text
        elif parts[0].lower() == 'give':
            # .a give <目标名> — 给出物品给队友
            if len(parts) < 2:
                return "用法: .a give <目标名>"
            target_name = ' '.join(parts[1:])
            il = self._get_initiative()
            target_entry = next((e for e in il if e.get('name', '') == target_name), None)
            if not target_entry:
                return f"未找到目标: {target_name}"
            result_text, success = self._eat_cake(uid, target_entry['userId'])
            return result_text
        return "用法: .a m <坐标> 或 .a s<序号> 或 .a eat [目标] 或 .a give <目标>"

    def _basic_attack(self, uid):
        char = self.get_char(uid); il = self._get_initiative()
        me = next((e for e in il if e["userId"]==uid), None)
        if not me: return "No position"
        enemies = [e for e in il if e["team"]!=me.get("team","Y") and (self._get_combat_hp(e["userId"])or 0)>0]
        if not enemies: return "No enemies"
        t = enemies[0]; tid = t["userId"]
        bn, bv = char.get_best_melee()
        dd = self._get_damage_dice(uid, bn); p = char.get_attr("伤害贯穿",1); l = char.get_attr("致死骰",1) or 0
        _, _, lines = self._coc7_attack(uid, tid, bn, bv, dd, p, l)
        return "\n".join(lines)

    def _create_summon(self, caster_id, template_name):
        from characters_data import SUMMON_TEMPLATES
        # Fix #9 / C7: _meta template resolution handled below after SUMMON_TEMPLATES lookup
        tmpl = SUMMON_TEMPLATES.get(template_name)
        if not tmpl: return None
        # Fix #9 / C7: Handle _meta templates (e.g. 随机召唤物 with options pool)
        if tmpl.get('_meta'):
            options = list(tmpl.get('options', []))
            if tmpl.get('unique_per_caster'):
                il = self._get_initiative()
                existing = {e.get("name") for e in il
                            if e.get("isSummon") and e.get("ownerId") == caster_id}
                options = [n for n in options if n not in existing]
            if not options:
                return None
            template_name = random.choice(options)
            tmpl = SUMMON_TEMPLATES.get(template_name)
            if not tmpl: return None
        sid = f"sum_{caster_id}_{random.randint(1000,9999)}"
        hp = tmpl.get("HP",10); dex = tmpl.get("DEX",50)
        skills_raw = tmpl.get("skills",["斗殴:50 1d4"])
        parsed = []
        for sk_raw in skills_raw:
            if isinstance(sk_raw, dict):
                parsed.append({
                    "name": sk_raw.get("name", ""),
                    "val": sk_raw.get("val", 50),
                    "dice": sk_raw.get("dice", "1d4"),
                    "hits": sk_raw.get("hits", 1),
                    "on_whiff_aoe_dmg": sk_raw.get("on_whiff_aoe_dmg", ""),
                    "on_whiff_mp_cost": sk_raw.get("on_whiff_mp_cost", 0),
                })
            else:
                parts = str(sk_raw).split(); nv = parts[0].split(":")
                parsed.append({"name":nv[0],"val":int(nv[1]) if len(nv)>1 else 50,"dice":parts[1] if len(parts)>1 else "1d4",
                               "hits":1,"on_whiff_aoe_dmg":"","on_whiff_mp_cost":0})
        init_list = self._get_initiative()
        ce = next((e for e in init_list if e["userId"]==caster_id), None)
        coord = "A1"
        if ce and ce.get("coord"):
            mp = parse_coord(ce["coord"])
            if mp:
                for r in range(1,6):
                    for dc in range(-r,r+1):
                        for dr in range(-r,r+1):
                            if abs(dc)+abs(dr)!=r: continue
                            nc, nr = mp[0]+dc, mp[1]+dr
                            if 0<=nc<26 and 0<=nr<99:
                                c = format_coord(nc,nr); md = self._get_map()
                                if md and c not in md.get("occupants",{}):
                                    coord=c; md["occupants"][c]=sid; self._set_map(md); break
                        else: continue; break
                    else: continue; break
        team = ce.get("team","Y") if ce else "Y"
        caster = self.get_char(caster_id)
        caster_name = caster.name if caster else caster_id
        summon_display_name = template_name
        init_list.append({"userId":sid,"name":summon_display_name,"team":team,"dex":dex,"initRoll":dex+random.randint(1,20),
            "coord":coord,"isSummon":True,"ownerId":caster_id,"skills":parsed,
            "skill_name":parsed[0]["name"],"skill_val":parsed[0]["val"],"dmg_dice":parsed[0]["dice"],
            "react_dodge_w":tmpl.get("react_dodge",50),"react_counter_w":tmpl.get("react_counter",50),
            "shield_block":tmpl.get("shield_block",0),"shield_block_hp":tmpl.get("shield_block",0),
            "flying":tmpl.get("flying",False)})
        state_s = self._get_state()
        old_idx = state_s.get('activeIndex', -1) if state_s else -1
        tracked_uid = init_list[old_idx]['userId'] if 0 <= old_idx < len(init_list) else None
        init_list.sort(key=lambda e: (-e["initRoll"],-e["dex"])); self._set_initiative(init_list)
        if tracked_uid is not None:
            for i, e in enumerate(init_list):
                if e['userId'] == tracked_uid:
                    if i != old_idx and state_s:
                        state_s['activeIndex'] = i; self._set_state(state_s)
                    break
        self._init_combat_hp(sid, hp)
        acts = self._get_actions(); acts[sid] = {"主动":tmpl.get("行动次数",1),"附加":1}; self._set_actions(acts)
        # Fix #6: 2x2 tile occupation for large summons
        if tmpl.get("size_2x2"):
            mp = parse_coord(coord)
            if mp:
                for dr in range(2):
                    for dc in range(2):
                        if dr == 0 and dc == 0: continue
                        nc, nr = mp[0] + dc, mp[1] + dr
                        if 0 <= nc < 26 and 0 <= nr < 99:
                            self._get_map()["occupants"][format_coord(nc, nr)] = sid
                self._set_map(self._get_map())
        # Track summoned template for merge eligibility
        mg = tmpl.get('merge_group', '')
        if mg:
            if not hasattr(self, '_summoned_templates'):
                self._summoned_templates = {}
            st = self._summoned_templates
            if caster_id not in st:
                st[caster_id] = {}
            if mg not in st[caster_id]:
                st[caster_id][mg] = set()
            st[caster_id][mg].add(template_name)
        return sid

    def _summon_attack(self, summon_id):
        il = self._get_initiative(); entry = next((e for e in il if e["userId"]==summon_id), None)
        if not entry: return "未找到召唤物"
        enemies = [e for e in il if e["team"]!=entry.get("team","Y") and (self._get_combat_hp(e["userId"])or 0)>0]
        if not enemies: return "无可用目标"
        tid = enemies[0]["userId"]; tname = enemies[0].get("name", tid)
        skills = entry.get("skills",[]); sk_name = ""
        if skills:
            best_score = -1; best = skills[0]
            for sk in skills:
                score = (sk["val"]/100.0)*avg_damage(sk["dice"])
                if score > best_score: best_score = score; best = sk
            sv = best["val"]; dmg_dice = best["dice"]; sk_name = best["name"]
            hits = best.get("hits", 1)
        else:
            sv = entry.get("skill_val",50); dmg_dice = entry.get("dmg_dice","1d4")
            hits = 1
        # Ignited summons use their ignite damage dice
        if entry.get("ignited"):
            dmg_dice = entry.get('ignite_dmg_dice', '2d4')
        # Battle spirit penalty dice
        pens = entry.get('battle_spirit_penalty_dice', 0)
        if pens > 0:
            atk_roll = max(random.randint(1, 100) for _ in range(pens + 1))
        else:
            atk_roll = random.randint(1, 100)
        if atk_roll > sv:
            return f"{sk_name or '攻击'} {tname} → 未命中"
        total_dmg = 0
        for _ in range(hits):
            if hits > 1 and random.randint(1,100) > sv: continue
            dmg = roll_dice(dmg_dice); eff_dmg, _, _ = self._absorb_damage_with_shield(tid, dmg)
            eff_dmg = self._apply_shield_block(tid, eff_dmg); total_dmg += eff_dmg
        cur_hp = self._get_combat_hp(tid) or 10; self._set_combat_hp(tid, max(0, cur_hp - total_dmg))
        thp = self._get_combat_hp(tid) or 0
        return f"{sk_name or '攻击'} {tname} → 造成 {total_dmg} 点伤害 (HP:{thp})"

    def _apply_shield_block(self, target_id, dmg):
        il = self._get_initiative(); entry = next((e for e in il if e["userId"]==target_id), None)
        if not entry: return dmg
        block_hp = entry.get("shield_block_hp",0)
        if block_hp <= 0: return dmg
        if random.random() < 0.70:
            absorbed = min(dmg, block_hp); entry["shield_block_hp"] = block_hp - absorbed
            return dmg - absorbed
        return dmg

    def _is_flying(self, uid):
        il = self._get_initiative(); entry = next((e for e in il if e["userId"]==uid), None)
        if entry and entry.get("flying"): return True
        char = self.get_char(uid)
        if char and char.get_attr('飞行', 0): return True
        return any(b.get("auxType")=="飞行" for b in self._get_active_buffs(uid))

    def _can_melee(self, atk_uid, def_uid):
        if not self._is_flying(atk_uid) and self._is_flying(def_uid): return False
        return True

    def _apply_zone_effects(self):
        effects = self._get_effects(); il = self._get_initiative()
        need_save = False
        for eff in effects:
            if eff.get("type")!="zone" or eff.get("radius",0)<=0: continue
            cp = parse_coord(eff.get("center","A1"))
            if not cp: continue
            # Determine zone caster's team for healing filter (Fix #8)
            zone_team = "Y"
            src_uid = eff.get("sourceUserId")
            if src_uid:
                se = next((e for e in il if e["userId"] == src_uid), None)
                if se: zone_team = se.get("team", "Y")
            if eff.get("tickDmg"):
                for entry in il:
                    ec = entry.get("coord",""); ep = parse_coord(ec) if ec else None
                    if not ep: continue
                    if max(abs(ep[0]-cp[0]),abs(ep[1]-cp[1])) > eff["radius"]: continue
                    dmg = roll_dice(eff["tickDmg"])
                    if dmg > 0:
                        ed, _, _ = self._absorb_damage_with_shield(entry["userId"], dmg)
                        hp = self._get_combat_hp(entry["userId"]) or 10
                        self._set_combat_hp(entry["userId"], max(0, hp - ed))
            if eff.get("tickHealHp"):
                for entry in il:
                    if entry.get("team") != zone_team: continue
                    if entry.get("isSummon") and entry.get("ignited"): continue  # Ignited summons cannot be healed
                    ec = entry.get("coord",""); ep = parse_coord(ec) if ec else None
                    if not ep: continue
                    if max(abs(ep[0]-cp[0]),abs(ep[1]-cp[1])) > eff["radius"]: continue
                    heal = roll_dice(eff["tickHealHp"])
                    if heal > 0:
                        hp = self._get_combat_hp(entry["userId"]) or 10
                        ch = self.get_char(entry["userId"])
                        mhp = ch.get_attr("体力上限", hp) if ch else hp
                        self._set_combat_hp(entry["userId"], min(hp+heal, mhp))
            if eff.get("tickHealMp"):
                for entry in il:
                    if entry.get("team") != zone_team: continue
                    ec = entry.get("coord",""); ep = parse_coord(ec) if ec else None
                    if not ep: continue
                    if max(abs(ep[0]-cp[0]),abs(ep[1]-cp[1])) > eff["radius"]: continue
                    heal = roll_dice(eff["tickHealMp"])
                    if heal > 0:
                        ch = self.get_char(entry["userId"])
                        cm = ch.get_attr("魔力",0) or 0; mx = ch.get_attr("魔力上限",cm) or cm
                        ch.set_attr("魔力", min(cm+heal, mx))
            # Fix #1: Update zone center if follows caster
            if eff.get("centerFollows"):
                src = eff.get("sourceUserId")
                if src:
                    se = next((e for e in il if e["userId"] == src), None)
                    if se and se.get("coord"):
                        eff["center"] = se["coord"]
                        need_save = True
        if need_save:
            self._set_effects(effects)

    def _process_zone_specials(self):
        """Generic zone-enter triggers. Reads on_enter_* fields from zone effects."""
        effects = self._get_effects(); il = self._get_initiative()
        for eff in effects:
            if eff.get("type") != "zone": continue
            mp_drain_pct = eff.get("on_enter_mp_drain_pct", 0)
            attr_debuff = eff.get("on_enter_attr_debuff", "")
            trigger_rate = eff.get("on_enter_trigger_rate", 1.0)
            if not (mp_drain_pct > 0 or attr_debuff): continue
            entered = set(eff.get("_entered_ids", []))
            for entry in il:
                uid = entry["userId"]
                if uid in entered: continue
                if random.randint(1, 100) / 100.0 > trigger_rate: continue
                entered.add(uid)
                char = self.get_char(uid)
                if mp_drain_pct > 0:
                    cmp = char.get_attr("魔力", 0) or 0
                    absorbed = int(cmp * mp_drain_pct)
                    char.set_attr("魔力", cmp - absorbed)
                    # Transfer drained MP to zone caster
                    caster_uid = eff.get("sourceUserId")
                    if caster_uid:
                        bc = self.get_char(caster_uid)
                        bm = bc.get_attr("魔力", 0) or 0
                        bmx = bc.get_attr("魔力上限", bm) or bm
                        bc.set_attr("魔力", min(bm + absorbed, bmx))
                if attr_debuff:
                    match = re.match(r'^(.+?)([+-]\d+)$', attr_debuff)
                    if match:
                        attr_name = match.group(1)
                        delta = int(match.group(2))
                        old_val = char.get_attr(attr_name, 0)
                        char.set_attr(attr_name, max(1, old_val + delta))
            eff["_entered_ids"] = list(entered)
        self._set_effects(effects)

    def _check_trinity_merge(self):
        """Generic summon merge: summons with same merge_group merge when only one survives."""
        from characters_data import SUMMON_TEMPLATES
        il = self._get_initiative()
        summons = [e for e in il if e.get("isSummon")]
        # Group summons by merge_group
        groups = {}
        for s in summons:
            tmpl = SUMMON_TEMPLATES.get(s.get("name", ""), {})
            mg = tmpl.get("merge_group", "")
            if not mg: continue
            if mg not in groups:
                groups[mg] = []
            groups[mg].append(s)
        for mg, members in groups.items():
            alive = [m for m in members if (self._get_combat_hp(m["userId"]) or 0) > 0]
            if len(alive) != 1: continue
            # Require all templates in this merge_group to have been summoned
            all_templates = {name for name, t in SUMMON_TEMPLATES.items()
                             if not t.get('_meta') and t.get('merge_group') == mg}
            owner_id = members[0].get('ownerId', '')
            summoned = getattr(self, '_summoned_templates', {}).get(owner_id, {}).get(mg, set())
            if not all_templates or not all_templates.issubset(summoned):
                continue
            # Ensure merge happens at most once per owner per merge_group
            merge_key = mg + '_merged'
            st_owner = getattr(self, '_summoned_templates', {}).get(owner_id, {})
            if st_owner.get(merge_key):
                continue
            st_owner[merge_key] = True
            merged = alive[0]
            # Look up merge_result template from characters_data
            member_tmpl = SUMMON_TEMPLATES.get(members[0].get("name", ""), {})
            result_name = member_tmpl.get("merge_result", "")
            result_tmpl = SUMMON_TEMPLATES.get(result_name, {})
            if not result_tmpl:
                continue
            # Apply full stats from merge_result template
            merged["name"] = result_name
            merged["_merged"] = True
            self._set_combat_hp(merged["userId"], result_tmpl.get("HP", 200))
            merged["dex"] = result_tmpl.get("DEX", 50)
            merged["flying"] = result_tmpl.get("flying", False)
            merged["react_dodge_w"] = result_tmpl.get("react_dodge", 50)
            merged["react_counter_w"] = result_tmpl.get("react_counter", 50)
            merged["shield_block"] = result_tmpl.get("shield_block", 0)
            merged["shield_block_hp"] = result_tmpl.get("shield_block", 0)
            # Parse skills from merge_result template
            skills_raw = result_tmpl.get("skills", [])
            parsed = []
            for sk_raw in skills_raw:
                if isinstance(sk_raw, dict):
                    parsed.append({
                        "name": sk_raw.get("name", ""),
                        "val": sk_raw.get("val", 50),
                        "dice": sk_raw.get("dice", "1d4"),
                        "hits": sk_raw.get("hits", 1),
                        "on_whiff_aoe_dmg": sk_raw.get("on_whiff_aoe_dmg", ""),
                        "on_whiff_mp_cost": sk_raw.get("on_whiff_mp_cost", 0),
                    })
                else:
                    parts = str(sk_raw).split(); nv = parts[0].split(":")
                    parsed.append({"name":nv[0],"val":int(nv[1]) if len(nv)>1 else 50,"dice":parts[1] if len(parts)>1 else "1d4",
                                   "hits":1,"on_whiff_aoe_dmg":"","on_whiff_mp_cost":0})
            merged["skills"] = parsed
            if parsed:
                merged["skill_name"] = parsed[0]["name"]
                merged["skill_val"] = parsed[0]["val"]
                merged["dmg_dice"] = parsed[0]["dice"]
            # Update action count
            acts = self._get_actions(); acts[merged["userId"]] = {"主动": result_tmpl.get("行动次数", 1), "附加": 1}
            self._set_actions(acts)
            self._set_initiative(il)

class FastBattleEngine(CombatEngine):
    """Fast training battle mode — simplified reactions, Q-learning optimized, no delays."""
    def __init__(self):
        super().__init__()
        self.max_rounds = 20

    def setup_battle(self, team_a, team_b, map_size="10x10"):
        w, h = map(int, map_size.split("x"))
        self.group_id = f"fast_{random.randint(10000,99999)}"
        self._set_map({"width":w, "height":h, "entryRow":math.ceil(h/2), "obstacles":{}, "occupants":{}})
        all_chars = team_a + team_b; init_list = []; map_data = self._get_map()
        for i, uid in enumerate(team_a):
            char = self.get_char(uid)
            row = min(h-1, math.ceil(h/2) + i - len(team_a)//2)
            coord = format_coord(1, row)
            init_list.append({"userId":uid, "name":char.name, "team":"Y", "dex":char.get_attr("敏捷",50), "initRoll":random.randint(1,100), "coord":coord})
            map_data["occupants"][coord] = uid
        for i, uid in enumerate(team_b):
            char = self.get_char(uid)
            row = min(h-1, math.ceil(h/2) + i - len(team_b)//2)
            coord = format_coord(w-2, row)
            init_list.append({"userId":uid, "name":char.name, "team":"X", "dex":char.get_attr("敏捷",50), "initRoll":random.randint(1,100), "coord":coord})
            map_data["occupants"][coord] = uid
        self._set_map(map_data); init_list.sort(key=lambda e: (-e["initRoll"], -e["dex"]))
        self._set_initiative(init_list)
        for uid in all_chars:
            char = self.get_char(uid); self._init_combat_hp(uid, char.get_attr("体力",10))
        self._set_actions({uid: {"主动":2, "附加":3} for uid in all_chars})
        # Roll random 状态 for each character
        for uid in all_chars:
            self.get_char(uid).set_attr('状态', random.randint(0, 100))
        # Auto-cast passives (with smart targeting)
        for uid in all_chars:
            spells = self.get_char(uid).spells or self.load_spells(uid)
            for s in spells:
                if has_timing(s.get("时机","2"), "1"):
                    target = self._smart_target(uid, s)
                    self._execute_spell(uid, target, s)
        self._set_state({"phase":"active", "round":1, "activeIndex":0})

    # ---- Summon system (same as FullBattleEngine) ----
    def _create_summon(self, caster_id, template_name):
        from characters_data import SUMMON_TEMPLATES
        # Fix #9 / C7: _meta template resolution handled below after SUMMON_TEMPLATES lookup
        tmpl = SUMMON_TEMPLATES.get(template_name)
        if not tmpl: return None
        # Fix #9 / C7: Handle _meta templates (e.g. 随机召唤物 with options pool)
        if tmpl.get('_meta'):
            options = list(tmpl.get('options', []))
            if tmpl.get('unique_per_caster'):
                il = self._get_initiative()
                existing = {e.get("name") for e in il
                            if e.get("isSummon") and e.get("ownerId") == caster_id}
                options = [n for n in options if n not in existing]
            if not options:
                return None
            template_name = random.choice(options)
            tmpl = SUMMON_TEMPLATES.get(template_name)
            if not tmpl: return None
        sid = f"sum_{caster_id}_{random.randint(1000,9999)}"
        hp = tmpl.get("HP",10); dex = tmpl.get("DEX",50)
        skills_raw = tmpl.get("skills",["斗殴:50 1d4"])
        parsed = []
        for sk_raw in skills_raw:
            if isinstance(sk_raw, dict):
                parsed.append({
                    "name": sk_raw.get("name", ""),
                    "val": sk_raw.get("val", 50),
                    "dice": sk_raw.get("dice", "1d4"),
                    "hits": sk_raw.get("hits", 1),
                    "on_whiff_aoe_dmg": sk_raw.get("on_whiff_aoe_dmg", ""),
                    "on_whiff_mp_cost": sk_raw.get("on_whiff_mp_cost", 0),
                })
            else:
                parts = str(sk_raw).split(); nv = parts[0].split(":")
                parsed.append({"name":nv[0],"val":int(nv[1]) if len(nv)>1 else 50,"dice":parts[1] if len(parts)>1 else "1d4",
                               "hits":1,"on_whiff_aoe_dmg":"","on_whiff_mp_cost":0})
        init_list = self._get_initiative()
        ce = next((e for e in init_list if e["userId"]==caster_id), None)
        coord = "A1"
        if ce and ce.get("coord"):
            mp = parse_coord(ce["coord"])
            if mp:
                for r in range(1,6):
                    for dc in range(-r,r+1):
                        for dr in range(-r,r+1):
                            if abs(dc)+abs(dr)!=r: continue
                            nc, nr = mp[0]+dc, mp[1]+dr
                            if 0<=nc<26 and 0<=nr<99:
                                c = format_coord(nc,nr); md = self._get_map()
                                if md and c not in md.get("occupants",{}):
                                    coord=c; md["occupants"][c]=sid; self._set_map(md); break
                        else: continue; break
                    else: continue; break
        team = ce.get("team","Y") if ce else "Y"
        caster = self.get_char(caster_id)
        caster_name = caster.name if caster else caster_id
        summon_display_name = template_name
        init_list.append({"userId":sid,"name":summon_display_name,"team":team,"dex":dex,"initRoll":dex+random.randint(1,20),
            "coord":coord,"isSummon":True,"ownerId":caster_id,"skills":parsed,
            "skill_name":parsed[0]["name"],"skill_val":parsed[0]["val"],"dmg_dice":parsed[0]["dice"],
            "react_dodge_w":tmpl.get("react_dodge",50),"react_counter_w":tmpl.get("react_counter",50),
            "shield_block":tmpl.get("shield_block",0),"shield_block_hp":tmpl.get("shield_block",0),
            "flying":tmpl.get("flying",False)})
        state_s = self._get_state()
        old_idx = state_s.get('activeIndex', -1) if state_s else -1
        tracked_uid = init_list[old_idx]['userId'] if 0 <= old_idx < len(init_list) else None
        init_list.sort(key=lambda e: (-e["initRoll"],-e["dex"])); self._set_initiative(init_list)
        if tracked_uid is not None:
            for i, e in enumerate(init_list):
                if e['userId'] == tracked_uid:
                    if i != old_idx and state_s:
                        state_s['activeIndex'] = i; self._set_state(state_s)
                    break
        self._init_combat_hp(sid, hp)
        acts = self._get_actions(); acts[sid] = {"主动":tmpl.get("行动次数",1),"附加":1}; self._set_actions(acts)
        # 2x2占据
        if tmpl.get("size_2x2"):
            mp = parse_coord(coord)
            if mp:
                for dr in range(2):
                    for dc in range(2):
                        if dr==0 and dc==0: continue
                        nc, nr = mp[0]+dc, mp[1]+dr
                        if 0<=nc<26 and 0<=nr<99:
                            self._get_map()["occupants"][format_coord(nc,nr)] = sid
                self._set_map(self._get_map())
        # Track summoned template for merge eligibility
        mg = tmpl.get('merge_group', '')
        if mg:
            if not hasattr(self, '_summoned_templates'):
                self._summoned_templates = {}
            st = self._summoned_templates
            if caster_id not in st:
                st[caster_id] = {}
            if mg not in st[caster_id]:
                st[caster_id][mg] = set()
            st[caster_id][mg].add(template_name)
        return sid

    def _summon_attack(self, summon_id):
        il = self._get_initiative(); entry = next((e for e in il if e["userId"]==summon_id), None)
        if not entry: return
        enemies = [e for e in il if e["team"]!=entry.get("team","Y") and (self._get_combat_hp(e["userId"])or 0)>0]
        if not enemies: return
        tid = enemies[0]["userId"]; skills = entry.get("skills",[]); sk_name = ""
        on_whiff_aoe = ""; on_whiff_mp = 0
        if skills:
            best_score = -1; best = skills[0]
            for sk in skills:
                score = (sk["val"]/100.0)*avg_damage(sk["dice"])
                if score > best_score: best_score = score; best = sk
            sv = best["val"]; dmg_dice = best["dice"]; sk_name = best["name"]
            hits = best.get("hits", 1)
            on_whiff_aoe = best.get("on_whiff_aoe_dmg", "")
            on_whiff_mp = best.get("on_whiff_mp_cost", 0)
        else:
            sv = entry.get("skill_val",50); dmg_dice = entry.get("dmg_dice","1d4")
            hits = 1
        # Ignited summons use their ignite damage dice
        if entry.get("ignited"):
            dmg_dice = entry.get('ignite_dmg_dice', '2d4')
        # Battle spirit penalty dice
        pens = entry.get('battle_spirit_penalty_dice', 0)
        if pens > 0:
            atk_roll = max(random.randint(1, 100) for _ in range(pens + 1))
        else:
            atk_roll = random.randint(1, 100)
        if atk_roll > sv: return
        total_dmg = 0
        for _ in range(hits):
            if hits > 1 and random.randint(1,100) > sv: continue
            dmg = roll_dice(dmg_dice); eff_dmg, _, _ = self._absorb_damage_with_shield(tid, dmg)
            eff_dmg = self._apply_shield_block(tid, eff_dmg); total_dmg += eff_dmg
        cur_hp = self._get_combat_hp(tid) or 10; self._set_combat_hp(tid, max(0, cur_hp - total_dmg))
        # Generic on-all-miss AoE (e.g. 积雨云引导)
        if on_whiff_aoe and total_dmg == 0:
            owner_id = entry.get("ownerId")
            if owner_id:
                oc = self.get_char(owner_id); omp = oc.get_attr("魔力",0) or 0
                if omp >= on_whiff_mp:
                    oc.set_attr("魔力", omp - on_whiff_mp)
                    for enemy in enemies:
                        aoe_dmg = roll_dice(on_whiff_aoe) // 2
                        ehp = self._get_combat_hp(enemy["userId"]) or 10
                        self._set_combat_hp(enemy["userId"], max(0, ehp - aoe_dmg))

    def _apply_shield_block(self, target_id, dmg):
        il = self._get_initiative(); entry = next((e for e in il if e["userId"]==target_id), None)
        if not entry: return dmg
        block_hp = entry.get("shield_block_hp",0)
        if block_hp <= 0: return dmg
        if random.random() < 0.70:
            absorbed = min(dmg, block_hp); entry["shield_block_hp"] = block_hp - absorbed
            return dmg - absorbed
        return dmg

    # ---- Zone per-round effects ----
    def _apply_zone_effects(self):
        effects = self._get_effects(); il = self._get_initiative()
        need_save = False
        for eff in effects:
            if eff.get("type")!="zone" or eff.get("radius",0)<=0: continue
            cp = parse_coord(eff.get("center","A1"))
            if not cp: continue
            # Determine zone caster's team for healing filter (Fix #8)
            zone_team = "Y"
            src_uid = eff.get("sourceUserId")
            if src_uid:
                se = next((e for e in il if e["userId"] == src_uid), None)
                if se: zone_team = se.get("team", "Y")
            if eff.get("tickDmg"):
                for entry in il:
                    ec = entry.get("coord",""); ep = parse_coord(ec) if ec else None
                    if not ep: continue
                    if max(abs(ep[0]-cp[0]),abs(ep[1]-cp[1])) > eff["radius"]: continue
                    dmg = roll_dice(eff["tickDmg"])
                    if dmg > 0:
                        ed, _, _ = self._absorb_damage_with_shield(entry["userId"], dmg)
                        hp = self._get_combat_hp(entry["userId"]) or 10
                        self._set_combat_hp(entry["userId"], max(0, hp - ed))
            if eff.get("tickHealHp"):
                for entry in il:
                    if entry.get("team") != zone_team: continue
                    if entry.get("isSummon") and entry.get("ignited"): continue  # Ignited summons cannot be healed
                    ec = entry.get("coord",""); ep = parse_coord(ec) if ec else None
                    if not ep: continue
                    if max(abs(ep[0]-cp[0]),abs(ep[1]-cp[1])) > eff["radius"]: continue
                    heal = roll_dice(eff["tickHealHp"])
                    if heal > 0:
                        hp = self._get_combat_hp(entry["userId"]) or 10
                        ch = self.get_char(entry["userId"])
                        mhp = ch.get_attr("体力上限", hp) if ch else hp
                        self._set_combat_hp(entry["userId"], min(hp+heal, mhp))
            if eff.get("tickHealMp"):
                for entry in il:
                    if entry.get("team") != zone_team: continue
                    ec = entry.get("coord",""); ep = parse_coord(ec) if ec else None
                    if not ep: continue
                    if max(abs(ep[0]-cp[0]),abs(ep[1]-cp[1])) > eff["radius"]: continue
                    heal = roll_dice(eff["tickHealMp"])
                    if heal > 0:
                        ch = self.get_char(entry["userId"])
                        cm = ch.get_attr("魔力",0) or 0; mx = ch.get_attr("魔力上限",cm) or cm
                        ch.set_attr("魔力", min(cm+heal, mx))
            # Fix #1: Update zone center if follows caster
            if eff.get("centerFollows"):
                src = eff.get("sourceUserId")
                if src:
                    se = next((e for e in il if e["userId"] == src), None)
                    if se and se.get("coord"):
                        eff["center"] = se["coord"]
                        need_save = True
        if need_save:
            self._set_effects(effects)

    # ---- Zone enter triggers (generalized) + 三合一 (generalized) ----
    def _process_zone_specials(self):
        """Generic zone-enter triggers. Reads on_enter_* fields from zone effects."""
        effects = self._get_effects(); il = self._get_initiative()
        for eff in effects:
            if eff.get("type") != "zone": continue
            mp_drain_pct = eff.get("on_enter_mp_drain_pct", 0)
            attr_debuff = eff.get("on_enter_attr_debuff", "")
            trigger_rate = eff.get("on_enter_trigger_rate", 1.0)
            if not (mp_drain_pct > 0 or attr_debuff): continue
            entered = set(eff.get("_entered_ids", []))
            for entry in il:
                uid = entry["userId"]
                if uid in entered: continue
                if random.randint(1, 100) / 100.0 > trigger_rate: continue
                entered.add(uid)
                char = self.get_char(uid)
                if mp_drain_pct > 0:
                    cmp = char.get_attr("魔力", 0) or 0
                    absorbed = int(cmp * mp_drain_pct)
                    char.set_attr("魔力", cmp - absorbed)
                    # Transfer drained MP to zone caster
                    caster_uid = eff.get("sourceUserId")
                    if caster_uid:
                        bc = self.get_char(caster_uid)
                        bm = bc.get_attr("魔力", 0) or 0
                        bmx = bc.get_attr("魔力上限", bm) or bm
                        bc.set_attr("魔力", min(bm + absorbed, bmx))
                if attr_debuff:
                    match = re.match(r'^(.+?)([+-]\d+)$', attr_debuff)
                    if match:
                        attr_name = match.group(1)
                        delta = int(match.group(2))
                        old_val = char.get_attr(attr_name, 0)
                        char.set_attr(attr_name, max(1, old_val + delta))
            eff["_entered_ids"] = list(entered)
        self._set_effects(effects)

    def _check_trinity_merge(self):
        """Generic summon merge: summons with same merge_group merge when only one survives."""
        from characters_data import SUMMON_TEMPLATES
        il = self._get_initiative()
        summons = [e for e in il if e.get("isSummon")]
        groups = {}
        for s in summons:
            tmpl = SUMMON_TEMPLATES.get(s.get("name", ""), {})
            mg = tmpl.get("merge_group", "")
            if not mg: continue
            if mg not in groups:
                groups[mg] = []
            groups[mg].append(s)
        for mg, members in groups.items():
            alive = [m for m in members if (self._get_combat_hp(m["userId"]) or 0) > 0]
            if len(alive) != 1: continue
            # Require all templates in this merge_group to have been summoned
            all_templates = {name for name, t in SUMMON_TEMPLATES.items()
                             if not t.get('_meta') and t.get('merge_group') == mg}
            owner_id = members[0].get('ownerId', '')
            summoned = getattr(self, '_summoned_templates', {}).get(owner_id, {}).get(mg, set())
            if not all_templates or not all_templates.issubset(summoned):
                continue
            # Ensure merge happens at most once per owner per merge_group
            merge_key = mg + '_merged'
            st_owner = getattr(self, '_summoned_templates', {}).get(owner_id, {})
            if st_owner.get(merge_key):
                continue
            st_owner[merge_key] = True
            merged = alive[0]
            # Look up merge_result template from characters_data
            member_tmpl = SUMMON_TEMPLATES.get(members[0].get("name", ""), {})
            result_name = member_tmpl.get("merge_result", "")
            result_tmpl = SUMMON_TEMPLATES.get(result_name, {})
            if not result_tmpl:
                continue
            # Apply full stats from merge_result template
            merged["name"] = result_name
            merged["_merged"] = True
            self._set_combat_hp(merged["userId"], result_tmpl.get("HP", 200))
            merged["dex"] = result_tmpl.get("DEX", 50)
            merged["flying"] = result_tmpl.get("flying", False)
            merged["react_dodge_w"] = result_tmpl.get("react_dodge", 50)
            merged["react_counter_w"] = result_tmpl.get("react_counter", 50)
            merged["shield_block"] = result_tmpl.get("shield_block", 0)
            merged["shield_block_hp"] = result_tmpl.get("shield_block", 0)
            # Parse skills from merge_result template
            skills_raw = result_tmpl.get("skills", [])
            parsed = []
            for sk_raw in skills_raw:
                if isinstance(sk_raw, dict):
                    parsed.append({
                        "name": sk_raw.get("name", ""),
                        "val": sk_raw.get("val", 50),
                        "dice": sk_raw.get("dice", "1d4"),
                        "hits": sk_raw.get("hits", 1),
                        "on_whiff_aoe_dmg": sk_raw.get("on_whiff_aoe_dmg", ""),
                        "on_whiff_mp_cost": sk_raw.get("on_whiff_mp_cost", 0),
                    })
                else:
                    parts = str(sk_raw).split(); nv = parts[0].split(":")
                    parsed.append({"name":nv[0],"val":int(nv[1]) if len(nv)>1 else 50,"dice":parts[1] if len(parts)>1 else "1d4",
                                   "hits":1,"on_whiff_aoe_dmg":"","on_whiff_mp_cost":0})
            merged["skills"] = parsed
            if parsed:
                merged["skill_name"] = parsed[0]["name"]
                merged["skill_val"] = parsed[0]["val"]
                merged["dmg_dice"] = parsed[0]["dice"]
            # Update action count
            acts = self._get_actions(); acts[merged["userId"]] = {"主动": result_tmpl.get("行动次数", 1), "附加": 1}
            self._set_actions(acts)
            self._set_initiative(il)

    # ---- Conditional effects ----
    def _can_basic_attack(self, uid, char):
        return True

    def _is_flying(self, uid):
        il = self._get_initiative(); entry = next((e for e in il if e["userId"]==uid), None)
        if entry and entry.get("flying"): return True
        char = self.get_char(uid)
        if char and char.get_attr('飞行', 0): return True
        return any(b.get("auxType")=="飞行" for b in self._get_active_buffs(uid))

    def _can_melee(self, atk_uid, def_uid):
        if not self._is_flying(atk_uid) and self._is_flying(def_uid): return False
        return True

    # ---- Fast attack (simplified but with lethality + shield) ----
    # ---- Fast COC7 attack with success-rank damage + reaction (training mode) ----
    def _fast_coc7_attack(self, atk_uid, def_uid, skill_name, skill_val, dmg_dice, pen, leth):
        """Full COC7 attack for training: rank-based damage + trainable reaction."""
        achar = self.get_char(atk_uid); dchar = self.get_char(def_uid)
        eff_skill = self._apply_buff_skill_mod(atk_uid, skill_val)
        atk_buffs = self._get_active_buffs(atk_uid); eff_bp = ""
        for b in atk_buffs:
            if b.get("auxCode") == 16 and b.get("auxVal") and not eff_bp: eff_bp = str(b["auxVal"])
        atk_result, _ = roll_d100(eff_bp); atk_rank = success_rank(atk_result, eff_skill)
        if atk_rank <= 0:
            return (def_uid, atk_uid, 0, "")  # Miss/fumble: defender wins, no damage

        dodge_val = dchar.get_attr("闪避",25); bmn, bmv = dchar.get_best_melee()
        dodge_val = self._apply_buff_skill_mod(def_uid, dodge_val)
        bmv = self._apply_buff_skill_mod(def_uid, bmv)
        def_buffs = self._get_active_buffs(def_uid); def_bp = ""
        for b in def_buffs:
            if b.get("auxCode") == 16 and b.get("auxVal") and not def_bp: def_bp = str(b["auxVal"])

        # Reaction: use stored character weights (trainable)
        dw = getattr(self, '_react_dw', {}).get(def_uid, 50)
        cw = getattr(self, '_react_cw', {}).get(def_uid, 50)
        is_dodge = random.random() < (dw / max(1, dw + cw)) if (dw + cw) > 0 else (dodge_val >= bmv)

        if is_dodge:
            rr, _ = roll_d100(def_bp); react_rank = success_rank(rr, dodge_val)
            eff_atk = atk_rank
            if react_rank > 1: eff_atk -= (react_rank - 1)
            if react_rank == -2: eff_atk += (1 if eff_atk == -1 else 2)
            eff_atk = max(-2, min(4, eff_atk))
            if eff_atk <= 0: return (def_uid, atk_uid, 0, f"dodge:{react_rank}")
            winner_rank, winner_uid, loser_uid = eff_atk, atk_uid, def_uid
            is_counter = False
        else:
            rr, _ = roll_d100(def_bp); react_rank = success_rank(rr, bmv)
            eff_atk, eff_react = atk_rank, react_rank
            if react_rank == -2: eff_atk += (1 if eff_atk == -1 else 2)
            if eff_atk == -2: eff_react = min(4, eff_react + 1)
            eff_atk, eff_react = max(-2, min(4, eff_atk)), max(-2, min(4, eff_react))
            if eff_react > eff_atk and eff_react > 0:
                winner_rank, winner_uid, loser_uid = eff_react, def_uid, atk_uid
                cdmg = dchar.get_str("伤害值") or "1d4"
                cpen = dchar.get_attr("伤害贯穿",1); cleth = dchar.get_attr("致死骰",1) or 0
                dmg_dice, pen, leth, is_counter = cdmg, cpen, cleth, True
            elif eff_atk > eff_react and eff_atk > 0:
                winner_rank, winner_uid, loser_uid, is_counter = eff_atk, atk_uid, def_uid, False
            else: return (None, None, 0, "draw")

        mx = max_damage(dmg_dice); dmg_val = 0
        if winner_rank == 2:
            dmg_val = max(roll_dice(dmg_dice), roll_dice(dmg_dice))
        elif winner_rank == 3:
            dmg_val = mx + roll_dice(dmg_dice) if pen else mx
        elif winner_rank == 4:
            dmg_val = mx * 2 if (atk_result == 1 or pen) else mx + roll_dice(dmg_dice)
        else:
            dmg_val = roll_dice(dmg_dice)

        eff_dmg, _, _ = self._absorb_damage_with_shield(loser_uid, dmg_val)
        cur_hp = self._get_combat_hp(loser_uid) or 10
        exp_dmg = avg_damage(dmg_dice)
        if leth and exp_dmg > 6:
            if random.randint(1, max(2, cur_hp*2)) <= int(exp_dmg): cur_hp = 0
            else: cur_hp = max(0, cur_hp - eff_dmg)
        else: cur_hp = max(0, cur_hp - eff_dmg)
        self._set_combat_hp(loser_uid, cur_hp)

        react_tag = "dodge" if is_dodge else ("counter" if not is_dodge else "?")
        return (winner_uid, loser_uid, eff_dmg, f"rank:{winner_rank} react:{react_tag} dmg:{eff_dmg}")

    def _fast_basic_attack(self, uid, tid=None):
        char = self.get_char(uid); il = self._get_initiative()
        me = next((e for e in il if e["userId"]==uid), None)
        if not me: return ""
        if not self._can_basic_attack(uid, char): return ""
        enemies = [e for e in il if e["team"]!=me.get("team","Y") and (self._get_combat_hp(e["userId"])or 0)>0]
        if not enemies: return ""
        grounded = [e for e in enemies if self._can_melee(uid, e["userId"])]
        if not grounded: return ""
        target = grounded[0]
        if tid and any(e["userId"]==tid for e in grounded): target = next(e for e in grounded if e["userId"]==tid)
        tid = target["userId"]
        bn, bv = char.get_best_melee(); bv = self._apply_buff_skill_mod(uid, bv)
        if random.randint(1,100) > bv: return ""
        dd = self._get_damage_dice(uid, bn); p = char.get_attr("伤害贯穿",1); l = char.get_attr("致死骰",1) or 0
        dmg_val = roll_dice(dd); eff_dmg, _, _ = self._absorb_damage_with_shield(tid, dmg_val)
        eff_dmg = self._apply_shield_block(tid, eff_dmg)
        cur_hp = self._get_combat_hp(tid) or 10
        exp_dmg = avg_damage(dd)
        if l and exp_dmg > 6:
            if random.randint(1, max(2, cur_hp*2)) <= int(exp_dmg): cur_hp = 0
            else: cur_hp = max(0, cur_hp - eff_dmg)
        else: cur_hp = max(0, cur_hp - eff_dmg)
        self._set_combat_hp(tid, cur_hp)
        # Lifesteal
        spells = char.spells or self.load_spells(uid)
        for s in spells:
            for e in s.get("effects",[]):
                ls = float(str(e.get("吸血比例","0") or "0"))
                if ls > 0 and eff_dmg > 0:
                    heal = int(dmg_val * ls); mhp = char.get_attr("体力上限", self._get_combat_hp(uid) or 10)
                    self._set_combat_hp(uid, min((self._get_combat_hp(uid) or 10) + heal, mhp))
        return ""

    def _fast_move(self, uid):
        il = self._get_initiative(); me = next((e for e in il if e["userId"]==uid), None)
        if not me: return ""
        mt = me.get("team","Y"); mc = me.get("coord","")
        enemies = [e for e in il if e["team"]!=mt and (self._get_combat_hp(e["userId"])or 0)>0]
        if not enemies or not mc: return ""
        ec = enemies[0].get("coord","")
        if not ec: return ""
        mp, tp = parse_coord(mc), parse_coord(ec)
        if not mp or not tp: return ""
        dc = 0 if tp[0]==mp[0] else (1 if tp[0]>mp[0] else -1)
        dr = 0 if tp[1]==mp[1] else (1 if tp[1]>mp[1] else -1)
        for dcol, drow in [(dc,0),(0,dr),(dc,dr)]:
            nc, nr = mp[0]+dcol, mp[1]+drow
            if 0<=nc<26 and 0<=nr<99:
                newc = format_coord(nc,nr); md = self._get_map()
                if md and newc not in md.get("occupants",{}):
                    if mc in md.get("occupants",{}): del md["occupants"][mc]
                    md["occupants"][newc] = uid; self._set_map(md); me["coord"] = newc
                    return ""
        return ""
