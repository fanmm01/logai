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
from datetime import datetime
import threading as _threading

# ═══════════════════════════════════════════════════════════════
#  Debug logging to file (shared with battle_http_server.py)
# ═══════════════════════════════════════════════════════════════
_ENGINE_DEBUG_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'battle_debug.log')
_ENGINE_DEBUG_LOCK = _threading.Lock()

_ENGINE_DEBUG_ENABLED = os.environ.get('BATTLE_DEBUG_LOG', '') == '1'

def _engine_debug_log(msg: str):
    """Append a timestamped message to the shared debug log file.

    Disabled by default — set env var BATTLE_DEBUG_LOG=1 to enable.
    When disabled, this is a true no-op: no file I/O, no lock, no stderr."""
    if not _ENGINE_DEBUG_ENABLED:
        return
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    line = f"[{ts}] [ENGINE] {msg}"
    with _ENGINE_DEBUG_LOCK:
        try:
            with open(_ENGINE_DEBUG_LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            pass
    print(line, file=sys.stderr, flush=True)

# Overridable by trainers to inject correct SUMMON_TEMPLATES for PvP vs PvE
_SUMMON_TEMPLATES = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ============================================================
#  Constants
# ============================================================
# FLYING_MELEE_RULE: 近战对飞行单位的命中规则
#   2 = 非飞行单位无法近战飞行目标（默认）
#   1 = 非飞行单位近战飞行目标时获得1个惩罚骰
#   0 = 无限制（近战可正常命中飞行单位）
FLYING_MELEE_RULE = 2

CN_NUMS = ['零','一','二','三','四','五','六','七','八','九','十',
           '十一','十二','十三','十四','十五','十六','十七','十八','十九','二十',
           '二十一','二十二','二十三','二十四','二十五','二十六','二十七','二十八','二十九','三十']
CAT_LETTERS = ['a','b','c','d','e','f']
CAT_NAMES = {1:'伤害',2:'护盾',3:'回复',4:'辅助',5:'召唤',6:'制造',7:'引发',8:'领域',0:'其他'}
TIMING_NAMES = {1:'被动',2:'主动作',3:'附加动作',4:'反应'}

AUX_EFFECT_TYPES = {
    1:'受到伤害百分比',2:'受到伤害加值',3:'造成伤害百分比',4:'伤害骰加值',
    5:'mp回复百分比',6:'mp回复加值',7:'hp回复百分比',8:'hp回复加值',
    9:'魔能消耗百分比',10:'魔能消耗加值',
    11:'致死骰优势',12:'致死骰劣势',
    13:'伤害骰优势',14:'伤害骰劣势',15:'伤害成功率加减',16:'伤害成功率奖励惩罚',
    17:'技能奖励骰',18:'技能惩罚骰',
    19:'伤害骰倍增/倍减',20:'技能值倍增/倍减',
    21:'暴击率',22:'暴击值',
    23:'行动力+',24:'行动力-',
    25:'移动力+',26:'移动力-',
    27:'MOV+',28:'MOV-',
    29:'消耗hp转为消耗mp',
    30:'免疫一次伤害',31:'受到伤害上限',32:'受到伤害减免阈值',
}
AUX_NAME_TO_CODE = {v: k for k, v in AUX_EFFECT_TYPES.items()}

# ── 控制状态类型（auxType 字符串，不入 AUX_EFFECT_TYPES）──
CONTROL_STATUS_TYPES = ('沉默','缴械','击晕','禁锢','隔离','减速','飞行')

def _eff_get(eff, key):
    """兼容新旧字段名：辅助效果/其他辅助效果a, 辅助效果值/辅助效果值a"""
    if key == 'auxType':
        return eff.get('辅助效果') or eff.get('其他辅助效果a', '')
    if key == 'auxVal':
        return eff.get('辅助效果值') or eff.get('辅助效果值a', '')
    return eff.get(key)

MELEE_SKILLS = ['格斗','斗殴','斧','链锯','连枷','绞索','矛','剑','鞭']
ALL_COMBAT_SKILLS = MELEE_SKILLS + ['射击','射击:弓','射击:手枪','射击:重武器','射击:火焰喷射器',
    '射击:机枪','射击:步霰','射击:冲锋枪','投掷','爆破','炮术']

# ============================================================
#  Dice & Math helpers
# ============================================================
def _get_beta_params(season_avg):
    """Map season average status (0-100) to Beta(α, β) parameters.

    - season_avg = 100 → α=2.5, β=1.2  (~70% chance status > 50)
    - season_avg = 50  → α=1.0, β=1.0  (uniform random)
    - season_avg = 0   → α=1.2, β=2.5  (~70% chance status < 50)

    Values between are linearly interpolated.
    """
    s = max(0, min(100, season_avg)) / 100.0  # normalize to 0-1
    if s <= 0.5:
        t = s / 0.5  # 0 → 1 as s goes 0 → 0.5
        alpha = 1.2 + (1.0 - 1.2) * t
        beta = 2.5 + (1.0 - 2.5) * t
    else:
        t = (s - 0.5) / 0.5  # 0 → 1 as s goes 0.5 → 1.0
        alpha = 1.0 + (2.5 - 1.0) * t
        beta = 1.0 + (1.2 - 1.0) * t
    return alpha, beta


def season_status_roll(season_avg, uid=None):
    """Generate a 0-100 character status using Beta distribution biased by season average.

    Args:
        season_avg: The season bias. Can be:
            - None: pure uniform random (backward compat)
            - int: 0-100, same bias for all characters
            - dict: {uid → int} per-character season status
        uid: Character uid, used when season_avg is a dict to look up per-char value.

    Returns:
        int between 0 and 100.
    """
    if season_avg is None:
        return random.randint(0, 100)
    if isinstance(season_avg, dict):
        val = season_avg.get(uid)
        if val is None:
            return random.randint(0, 100)
        season_avg = val
    alpha, beta = _get_beta_params(season_avg)
    raw = random.betavariate(alpha, beta)  # 0.0 - 1.0
    return max(0, min(100, int(round(raw * 100))))


# ============================================================
#  DB (Damage Bonus) — COC7 体格→伤害加成映射
# ============================================================
_DB_TABLE = {-2: '-2', -1: '-1', 0: '0', 1: '1d4', 2: '1d6', 3: '2d6', 4: '3d6', 5: '4d6'}


def _get_db_str(char):
    """Get the resolved db dice string for a character.

    Priority:
      1. str_attrs['db'] — direct db specification (e.g. '1d4', '-1', '2d6+1')
      2. 体格 (Build) → _DB_TABLE lookup
      3. '0' (no bonus)

    Returns a string like '1d4', '-1', '0', '2d6+1', etc.
    """
    if char is None:
        return '0'
    # Check for direct db string attribute (e.g., 'db': '1d4' in str_attrs)
    db_direct = char.get_str('db')
    if db_direct:
        return db_direct
    # Fall back to 体格 → _DB_TABLE
    build = char.get_attr('体格', 0)
    return _DB_TABLE.get(int(build), '0')


def _resolve_db(dice_expr, build_or_db):
    """Replace 'db' token in a dice expression with the damage bonus.

    Args:
        dice_expr: dice expression string (e.g. "1d8+db")
        build_or_db: either an int (体格 value, looked up in _DB_TABLE)
                     or a string (direct db dice expression, used as-is)

    Returns the dice expression with 'db' replaced by the resolved value.
    """
    if not dice_expr or build_or_db is None:
        return dice_expr
    if 'db' not in str(dice_expr).lower():
        return dice_expr

    # Resolve db value: str → use directly, int → look up table
    if isinstance(build_or_db, str):
        db_val = build_or_db
    else:
        db_val = _DB_TABLE.get(int(build_or_db), '0')

    expr = str(dice_expr)

    if db_val == '0':
        # Remove the db term entirely: "+db", "-db", or bare "db"
        expr = re.sub(r'[+-]?\s*\bdb\b', '', expr, flags=re.IGNORECASE).strip()
        expr = re.sub(r'^\+\s*', '', expr)  # strip leading '+'
        if not expr:
            expr = '0'
    elif db_val.startswith('-'):
        # Negative flat value like "-1" or "-2"
        # "+db" → db_val (the '+' is consumed, db_val already starts with '-')
        # Use negative lookbehind to avoid matching "-db" (handled by second pattern)
        expr = re.sub(r'(?<!-)\+?\s*\bdb\b', db_val, expr, flags=re.IGNORECASE)
        # "-db" with negative db → double negative becomes positive
        expr = re.sub(r'-\s*\bdb\b', f'+{abs(int(db_val))}', expr, flags=re.IGNORECASE)
    else:
        # Positive dice like "1d4", "2d6", etc.
        # Use negative lookbehind to avoid matching "-db" (handled by second pattern)
        expr = re.sub(r'(?<!-)\+?\s*\bdb\b', f'+{db_val}', expr, flags=re.IGNORECASE)
        expr = re.sub(r'-\s*\bdb\b', f'-{db_val}', expr, flags=re.IGNORECASE)
        # Strip leading '+' for clean standalone expressions like "db"
        expr = re.sub(r'^\+', '', expr)

    return expr


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
            except (ValueError, TypeError): pass
    return total


def roll_dice_detailed(expr):
    """Roll dice and return (total, breakdown_string).

    Breakdown format:
      2d3+1d4 → "2d3(2+1=3)+1d4(4)=7"
      1d6     → "1d6=6"
      Static: "2d6+4" → "2d6(3+5=8)+4=12"
      If expr is empty or '0', returns (0, '0').
    """
    if not expr or expr == '0':
        return (0, '0')
    expr_n = re.sub(r'(^|[^0-9])d', r'\g<1>1d', expr, flags=re.IGNORECASE)
    total = 0
    parts_detail = []
    for part in re.findall(r'[+-]?\s*\d*d\d+|[+-]?\s*\d+', expr_n, re.IGNORECASE):
        sign = -1 if part.strip().startswith('-') else 1
        sign_str = '-' if sign < 0 else '+'
        clean = part.strip().lstrip('+-').strip()
        m = re.match(r'^(\d*)d(\d+)$', clean, re.IGNORECASE)
        if m:
            cnt = int(m.group(1)) if m.group(1) else 1
            sides = int(m.group(2))
            rolls = [random.randint(1, sides) for _ in range(cnt)]
            subtotal = sum(rolls)
            total += sign * subtotal
            if cnt == 1:
                dice_str = f'{sign_str}1d{sides}({rolls[0]})' if (parts_detail or sign_str == '-') else f'1d{sides}({rolls[0]})'
                parts_detail.append(dice_str)
            else:
                inner = '+'.join(str(r) for r in rolls)
                dice_str = f'{sign_str}{cnt}d{sides}({inner}={subtotal})' if (parts_detail or sign_str == '-') else f'{cnt}d{sides}({inner}={subtotal})'
                parts_detail.append(dice_str)
        else:
            val = int(clean)
            total += sign * val
            parts_detail.append(f'{sign_str}{val}' if (parts_detail or sign_str == '-') else str(val))
    # Clean up leading '+' on first part
    detail = ''.join(parts_detail)
    if detail.startswith('+'):
        detail = detail[1:]
    return (total, f'{detail}={total}')

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

def success_rank(d100_val, skill_val, crit_mod_pct=0.0, crit_mod_flat=0):
    if skill_val <= 0: return 4 if d100_val <= 5 else (-2 if d100_val >= 96 else -1)
    if d100_val == 1: return 4
    if d100_val >= 96: return -2  # 96-100 = 大失败
    if d100_val > skill_val:
        return -1
    rank = 1
    crit_threshold = skill_val * max(0.01, min(0.50, 0.20 + crit_mod_pct)) + crit_mod_flat
    if d100_val <= crit_threshold: rank = 3
    elif d100_val <= skill_val / 2: rank = 2
    if d100_val <= 5: rank = 4
    return rank

def rank_text(rank):
    return {-2:'大失败',-1:'失败',1:'成功',2:'困难成功',3:'极难成功',4:'大成功'}.get(rank,'')

def roll_d100(bp_str=''):
    tens = random.randint(0, 9); units = random.randint(0, 9)
    base_detail = f'10位{tens}, 个位{units}'
    bp_match = re.match(r'^([bp])(\d*)$', bp_str.lower()) if bp_str else None
    detail = base_detail
    if bp_match:
        btype = bp_match.group(1); count = int(bp_match.group(2) or '1')
        best_tens = tens; extras = [str(random.randint(0,9)) for _ in range(count)]
        for et_str in extras:
            et = int(et_str)
            # When units==0, a tens of 0 gives result 100 (worst), not 0 (best).
            # Treat it as 10 for comparison so it's correctly ranked as largest.
            eff_et = 10 if (units == 0 and et == 0) else et
            eff_best = 10 if (units == 0 and best_tens == 0) else best_tens
            if btype == 'b':
                if eff_et < eff_best: best_tens = et
            else:
                if eff_et > eff_best: best_tens = et
        bp_label = f'{"奖励" if btype=="b" else "惩罚"}{",".join(extras)}'
        detail = f'{base_detail}, {bp_label}'
        tens = best_tens
    result = 100 if (tens == 0 and units == 0) else tens * 10 + units
    return (result, detail)

def _calc_net_bp(buffs, bp_suffix='', skill_name=None):
    """Calculate net bonus/penalty dice from all buffs with stacking (max ±3).

    Counts all bonus dice and penalty dice from:
    - AUX 16 (伤害成功率奖励惩罚) buffs with auxVal like 'b','b2','b3','p','p2','p3'
    - Skill-specific bonus/penalty dice (auxCode 17/18 or auxType 技能奖励骰/技能惩罚骰)
    - The passed-in bp_suffix (from command like .btab/.btap)

    Nets them: bonus − penalty, capped at ±3.
    Returns formatted string: 'b3','b2','b','','p','p2','p3'
    """
    bonus_count = 0
    penalty_count = 0

    # 1. Parse bp_suffix (from command)
    if bp_suffix:
        m = re.match(r'^([bp])(\d*)$', bp_suffix.lower())
        if m:
            cnt = int(m.group(2) or '1')
            if m.group(1) == 'b':
                bonus_count += cnt
            else:
                penalty_count += cnt

    # 2. Scan all buffs
    for b in buffs:
        # AUX 16: 伤害成功率奖励惩罚
        if b.get("auxCode") == 16:
            val = str(b.get("auxVal", "")).strip().lower()
            m = re.match(r'^([bp])(\d*)$', val)
            if m:
                cnt = int(m.group(2) or '1')
                if m.group(1) == 'b':
                    bonus_count += cnt
                else:
                    penalty_count += cnt
        # Skill-specific bonus/penalty dice (auxCode 17/18)
        if skill_name and b.get('auxCode') in (17, 18):
            try:
                aux_val = str(b.get('auxVal', '')).strip()
                if aux_val.lower() == skill_name.lower():
                    if b.get('auxCode') == 17:
                        bonus_count += 1
                    else:
                        penalty_count += 1
            except Exception:
                pass
        # Skill-specific bonus/penalty dice (legacy auxType string matching)
        if skill_name:
            try:
                aux_type = b.get('auxType', '')
                aux_val = str(b.get('auxVal', '')).strip()
                if aux_type == '技能奖励骰' and aux_val.lower() == skill_name.lower():
                    bonus_count += 1
                if aux_type == '技能惩罚骰' and aux_val.lower() == skill_name.lower():
                    penalty_count += 1
            except Exception:
                pass

    # 3. Net and cap at ±3
    net = bonus_count - penalty_count
    net = max(-3, min(3, net))

    if net > 0:
        return f'b{net}' if net > 1 else 'b'
    elif net < 0:
        n = -net
        return f'p{n}' if n > 1 else 'p'
    else:
        return ''


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

def chebyshev_dist(a, b):
    """Chebyshev distance between two coords."""
    ap, bp = parse_coord(a), parse_coord(b)
    if not ap or not bp: return 999
    return max(abs(ap[0]-bp[0]), abs(ap[1]-bp[1]))

def _get_attack_range(spell_effect=None, skill_name=None):
    """获取攻击的射程。法术效果优先读取其'射程'字段；未声明默认2(近战)。
    普攻: MELEE_SKILLS中的技能=2, 其他=99。
    调试: 若 characters_data_pvp.GLOBAL_RANGE_OVERRIDE == 1，所有射程返回99。"""
    try:
        from characters_data import GLOBAL_RANGE_OVERRIDE
        if GLOBAL_RANGE_OVERRIDE:
            return 99
    except ImportError:
        pass
    if spell_effect:
        return spell_effect.get('射程', 2)
    if skill_name:
        if any(ms in str(skill_name) for ms in MELEE_SKILLS):
            return 2
        return 99
    return 2

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
        self.inventory = []  # 物品栏: [{'item': '蛋糕HP', 'count': 3}, ...]

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

# ── Module-level cache for AI weight target strategies ──
# Shared across all engine instances to avoid repeated JSON parsing and Q-table iteration.
_weight_cache = None          # Parsed weights JSON
_weight_best_strategy = {}    # {serial: best_strategy} pre-computed
_weight_loaded = False

def _ensure_weight_cache():
    """One-time load and pre-compute target strategy preferences from AI weights."""
    global _weight_cache, _weight_best_strategy, _weight_loaded
    if _weight_loaded:
        return
    _weight_loaded = True
    import os, json
    weight_paths = [
        os.path.join(os.path.dirname(__file__), 'ai_weights_pvp.json'),
        os.path.join(os.path.dirname(__file__), 'ai_weights.json'),
    ]
    for wp in weight_paths:
        try:
            if os.path.exists(wp):
                with open(wp, 'r', encoding='utf-8') as f:
                    _weight_cache = json.load(f)
                break
        except Exception:
            continue
    if not _weight_cache:
        return
    # Pre-compute best target strategy per character
    for q_mode in ['Q_solo', 'Q_team']:
        q_table = _weight_cache.get(q_mode, {})
        for serial, state_actions in q_table.items():
            sums = {'T0': 0.0, 'T1': 0.0, 'T2': 0.0, 'T3': 0.0}
            cnts = {'T0': 0, 'T1': 0, 'T2': 0, 'T3': 0}
            for key, q_val in state_actions.items():
                if not isinstance(q_val, (int, float)):
                    continue
                for s in ['T0', 'T1', 'T2', 'T3']:
                    if key.endswith(f'__{s}'):
                        sums[s] += q_val
                        cnts[s] += 1
                        break
            best_s = None
            best_avg = -999999
            for s in ['T0', 'T1', 'T2', 'T3']:
                if cnts[s] > 0:
                    avg = sums[s] / cnts[s]
                    if avg > best_avg:
                        best_avg = avg
                        best_s = s
            if best_s:
                _weight_best_strategy[f"{q_mode}:{serial}"] = best_s

# ============================================================
#  CombatEngine — base state management
# ============================================================
class CombatEngine:
    def __init__(self):
        self.characters = {}
        self.storage = {}
        self._cache = {}          # Deserialized object cache — avoids repeated json.loads
        self._fast_store = False  # When True, skip json.dumps (for training/sim)
        self._buff_cache = {}     # Per-uid active buff cache — cleared on effect changes
        self.group_id = 'default'
        # Dying system: when HP reaches 0, CON save → dying state instead of instant death.
        # Set to False to revert to old behaviour (HP≤0 → instant death, no death saves).
        # Toggle at runtime: engine.use_dying_system = True / False
        self.use_dying_system = True
        self._last_death_overflow = {}  # team → overflow damage of last non-summon death (mutual-annihilation tiebreaker)
        self._last_death_max_hp = {}    # team → max HP of last non-summon death (for overflow % tiebreaker)
        self._battle_result = None  # Set by _check_battle_end when battle concludes
        # Summon group consolidation: same-type summons share one initiative slot
        self._summon_groups = {}   # {rep_base_uid: {members, template_name, ownerId}}
        self._summon_members = {}  # {sid: {userId, name, display_name, team, coord, skills, ...}}

    def _get_block_skill(self, uid):
        """获取格挡技能名和值。优先：格挡 → 盾击。返回 (skill_name, skill_value)。"""
        char = self.get_char(uid)
        for sk in ['格挡', '盾击']:
            v = char.get_attr(sk, 0)
            if v > 0:
                return (sk, v)
        return (None, 0)

    def _get_block_redirect_target(self, uid):
        """如果角色配置了block_redirect，找到目标召唤物并返回其userId。
        目标召唤物必须存活且有shield_block_hp>0，否则返回None。"""
        char = self.get_char(uid)
        redirect_name = char.get_str('block_redirect')
        if not redirect_name:
            return None
        il = self._get_initiative()
        resolved = self._resolve_uid(uid)
        for e in il:
            if (e.get('isSummon') and e.get('ownerId') == resolved
                and e.get('name') == redirect_name
                and e.get('shield_block_hp', 0) > 0):
                return e['userId']
        return None

    def get_char(self, uid):
        base = self._resolve_uid(uid)
        if base not in self.characters:
            self.characters[base] = Character(base, base)
        return self.characters[base]

    def storage_get(self, key): return self.storage.get(key)
    def storage_set(self, key, value):
        if value: self.storage[key] = value
        elif key in self.storage: del self.storage[key]

    def get_json(self, key, default=None):
        if key in self._cache:
            return self._cache[key]
        raw = self.storage_get(key)
        val = json.loads(raw) if raw else default
        self._cache[key] = val
        return val

    def set_json(self, key, data):
        self._cache[key] = data
        if not self._fast_store:
            self.storage_set(key, json.dumps(data, ensure_ascii=False))

    def _combat_hp_key(self): return f"combat_hp_{self.group_id}"
    def _resolve_uid(self, uid):
        """Resolve multi-action UIDs (Y1__act0) to base UID (Y1) for shared HP/attrs."""
        if '__act' in str(uid):
            return uid.rsplit('__act', 1)[0]
        return uid

    # ── Summon group consolidation ──
    def _get_summon_group(self, uid):
        """Return the summon group dict for uid, or None if uid is not in a group."""
        base = self._resolve_uid(uid)
        # Check if base is a representative key
        if base in self._summon_groups:
            return self._summon_groups[base]
        # Check if uid/base is a member of any group
        for rep_uid, grp in self._summon_groups.items():
            if base in grp.get('members', []):
                return grp
        return None

    def _find_summon_group_by(self, owner_id, template_name):
        """Find an existing summon group by owner and template name. Returns rep_uid or None."""
        for rep_uid, grp in self._summon_groups.items():
            if (grp.get('ownerId') == owner_id
                    and grp.get('template_name') == template_name):
                return rep_uid
        return None

    def _find_moon_for_team(self, team):
        """Find the 虚假之月 summon on the given team. Returns (moon_uid, moon_char) or (None, None)."""
        for entry in self._get_initiative():
            if entry.get('isSummon') and entry.get('name') == '虚假之月' \
               and entry.get('team') == team \
               and (self._get_combat_hp(entry['userId']) or 0) > 0:
                uid = entry['userId']
                return uid, self.get_char(uid)
        return None, None

    def _charge_moon_energy(self, team, amount):
        """Add amount to the moon's MP on the given team. Returns actual amount charged."""
        if amount <= 0: return 0
        moon_uid, moon_char = self._find_moon_for_team(team)
        if not moon_char: return 0
        cur_mp = moon_char.get_attr('魔力', 0) or 0
        moon_char.set_attr('魔力', cur_mp + amount)
        return amount

    def _has_moon_buff(self, uid):
        """Check if a character has the 事象的馈赠 buff (linked to 虚假之月)."""
        for e in self._get_effects():
            if e.get('type') == 'buff' and e.get('targetUserId') == uid \
               and e.get('linked_summon') == '虚假之月' \
               and e.get('remainingRounds', 0) > 0:
                return True
        return False

    def _consume_immunity(self, uid):
        """Check and consume AUX 30 (免疫一次伤害) buff on uid. Returns True if immune."""
        effects = self._get_effects()
        for e in effects:
            if e.get('auxCode') == 30 and e.get('targetUserId') == uid \
               and e.get('remainingRounds', 0) > 0:
                e['remainingRounds'] = 0  # Consume
                self._set_effects(effects)
                return True
        return False

    def _apply_immunity_buff(self, uid, source_uid=None):
        """Apply AUX 30 (免疫一次伤害) buff to uid."""
        effects = self._get_effects()
        effects.append({
            'type': 'buff', 'auxCode': 30, 'auxType': '免疫一次伤害', 'auxVal': '1',
            'sourceUserId': source_uid or uid, 'targetUserId': uid,
            'spellName': '月相的庇佑', 'spellIndex': 0,
            'remainingRounds': 99, 'persistent': 0, 'stackable': 1,
        })
        self._set_effects(effects)

    def _get_summon_group_members(self, rep_or_uid):
        """Return list of member userIds for the group containing uid, or [uid] if not in a group.
        Prunes dead members from the group to prevent stale accumulation."""
        grp = self._get_summon_group(rep_or_uid)
        if grp:
            members = list(grp.get('members', []))
            # Prune dead members (HP=0 or None) from the group list
            alive = [m for m in members if (self._get_combat_hp(m) or 0) > 0]
            if len(alive) != len(members):
                if alive:
                    grp['members'] = alive
                else:
                    # All dead: remove the group entirely
                    self._summon_groups.pop(self._resolve_uid(rep_or_uid), None)
                    for m in members:
                        self._summon_members.pop(m, None)
            return alive if alive else members  # Return alive members, or keep dead ones if all dead
        base = self._resolve_uid(rep_or_uid)
        return [base]

    def _get_alive_summon_group_members(self, rep_or_uid):
        """Return list of alive member userIds in the group."""
        members = self._get_summon_group_members(rep_or_uid)
        return [m for m in members if (self._get_combat_hp(m) or 0) > 0]

    def _init_combat_hp(self, uid, hp):
        s = self.get_json(self._combat_hp_key(), {}); s[self._resolve_uid(uid)] = hp; self.set_json(self._combat_hp_key(), s)
    def _get_combat_hp(self, uid):
        return self.get_json(self._combat_hp_key(), {}).get(self._resolve_uid(uid))
    def _set_combat_hp(self, uid, hp, source_dmg=0):
        """Set combat HP. Accepts raw HP (negative = overflow damage).
        Tracks per-team overflow for mutual-annihilation tiebreaker."""
        base = self._resolve_uid(uid)
        s = self.get_json(self._combat_hp_key(), {})
        old_hp = s.get(base)
        # Compute overflow: from negative hp or from source_dmg exceeding old_hp
        overflow = max(0, -hp) if hp < 0 else 0
        if source_dmg > 0 and old_hp is not None:
            overflow = max(overflow, max(0, source_dmg - old_hp))
        new_hp = max(0, hp)
        # AUX 29: HP→MP conversion (消耗hp转为消耗mp) — intercept HP loss, consume MP instead
        if old_hp is not None and new_hp < old_hp:
            char = self.get_char(uid)
            if char:
                hp_loss = old_hp - new_hp
                has_conversion = any(b.get('auxCode') == 29 for b in self._get_active_buffs(base))
                if has_conversion:
                    cur_mp = char.get_attr('魔力', 0) or 0
                    mp_consume = min(cur_mp, hp_loss)
                    if mp_consume > 0:
                        char.set_attr('魔力', cur_mp - mp_consume)
                        new_hp = old_hp - (hp_loss - mp_consume)
                        _engine_debug_log(f"AUX29 HP→MP: {char.name}({base}) hp_loss={hp_loss} mp_consume={mp_consume} old_hp={old_hp} new_hp={new_hp}")
        s[base] = new_hp
        self.set_json(self._combat_hp_key(), s)

        # Track per-team last-death overflow for battle-end tiebreaker
        if old_hp is not None and old_hp > 0 and new_hp <= 0:
            entry = next((e for e in self._get_initiative() if self._resolve_uid(e['userId']) == base), None)
            if entry and not entry.get('isSummon'):
                team = entry.get('team', '?')
                if not hasattr(self, '_last_death_overflow'):
                    self._last_death_overflow = {}
                if not hasattr(self, '_last_death_max_hp'):
                    self._last_death_max_hp = {}
                self._last_death_overflow[team] = overflow
                char = self.get_char(uid)
                self._last_death_max_hp[team] = char.get_attr('体力上限', old_hp) if char else old_hp
                if getattr(self, 'use_dying_system', False):
                    # New system: CON save → dying or death
                    excess = max(0, source_dmg - old_hp) if source_dmg > 0 else max(0, -hp)
                    self._enter_dying_or_die(base, excess)
                else:
                    # Old system: immediate death, remove character + summons
                    self._remove_summons_of_owner(base)

    # ---- Dying / Death helpers ----
    def _check_con_save(self, uid, difficulty='normal'):
        """COC CON saving throw. difficulty: 'normal'=need success, 'hard'=need hard success, etc.
        Returns True if the save succeeds (stays alive/dying)."""
        char = self.get_char(uid)
        con = char.get_attr('体质', 50)
        roll, detail = roll_d100('')
        rank = success_rank(roll, con)
        if difficulty == 'normal': return rank >= 1
        elif difficulty == 'hard': return rank >= 2
        elif difficulty == 'extreme': return rank >= 3
        elif difficulty == 'critical': return rank >= 4
        return rank >= 1

    def _hp_safe(self, uid, default=10):
        """Get combat HP safely: None→default, 0→0 (not default!). Prevents dead healing."""
        v = self._get_combat_hp(uid)
        return default if v is None else v

    def _is_dying(self, uid):
        """Check if uid is currently in dying state."""
        base = self._resolve_uid(uid)
        for e in self._get_effects():
            if e.get('type') == 'dying' and self._resolve_uid(e.get('targetUserId', '')) == base:
                return e
        return None

    def _enter_dying_or_die(self, uid, excess_damage=0):
        """On HP≤0: CON save → dying state or death. Returns True if still alive (dying)."""
        if self._is_dying(uid): return True  # Already dying

        # Check for deathPrevention effect
        effects = self._get_effects()
        for e in effects:
            if e.get('type') == 'deathPrevention' and e.get('targetUserId') == uid and e.get('remainingRounds', 0) > 0:
                # Respawn instead of dying
                respawn_hp = e.get('respawn_hp', 1)
                respawn_radius = e.get('respawn_radius', 3)
                eject_phases = e.get('eject_phases', [])
                # Set HP to respawn_hp
                old_hp = self._hp_safe(uid, 10)
                self._set_combat_hp(uid, respawn_hp)
                # Clear any dying state
                effects = [e for e in effects if not (e.get('type') == 'dying' and e.get('targetUserId') == uid)]
                # Move to a random valid tile within radius (simplified: stay in place)
                # Lock ejected phases
                if eject_phases:
                    char = self.get_char(uid)
                    existing_locked = char.get_attr('永久移除阶段') or 0
                    for ph in eject_phases:
                        existing_locked |= (1 << ph)
                    char.set_attr('永久移除阶段', existing_locked)
                    # Switch to first available phase
                    current_phase = getattr(char, 'phase', 1)
                    if current_phase in eject_phases:
                        for new_ph in [1, 2, 3, 4]:
                            if new_ph not in eject_phases and not (existing_locked & (1 << new_ph)):
                                self._transition_phase(uid, new_ph)
                                break
                # Consume the effect
                e['remainingRounds'] = 0
                self._set_effects(effects)
                cname = self.get_char(uid).name if self.get_char(uid) else '?'
                return f'{cname} 触发残机替身！以 {respawn_hp} HP 重生。\n'
        char = self.get_char(uid)
        # CON saving throw
        if self._check_con_save(uid, 'normal'):
            # Success → enter dying state
            effects = self._get_effects()
            effects.append({
                'type': 'dying', 'targetUserId': uid,
                'excessDamage': excess_damage,
                'dyingRounds': 0,
                'sourceUserId': uid, 'spellName': '濒死',
                'remainingRounds': 999, 'persistent': 1,
            })
            self._set_effects(effects)
            # P2: Teammate first enters dying → allies' limit_gauge +2
            self._on_ally_dying(uid)
            return True
        else:
            # Failed save → check for revive, else die
            effects = self._get_effects()
            revive_item = None
            for e in effects:
                if e.get('type') in ('item', 'zone') and e.get('复活回复比例'):
                    revive_item = e
                    break
            if revive_item:
                ratio = revive_item.get('复活回复比例', 0.5)
                max_hp = char.get_attr('体力上限', char.get_attr('体力', 10))
                self._set_combat_hp(uid, int(max_hp * ratio))
                # Remove dying effects
                effs = [e for e in effects if not (e.get('type') == 'dying' and e.get('targetUserId') == uid)]
                self._set_effects(effs)
                return True
            # Death
            self._remove_summons_of_owner(uid)
            return False

    def _remove_character_from_initiative(self, uid):
        """Remove a dead character from initiative. Kept separate for clarity."""
        pass  # handled by _remove_summons_of_owner

    def _get_state(self): return self.get_json(f"combat_state_{self.group_id}")
    def _set_state(self, s): self.set_json(f"combat_state_{self.group_id}", s)
    def _get_initiative(self):
        il = self.get_json(f"combat_initiative_{self.group_id}", [])
        if _ENGINE_DEBUG_ENABLED and il:
            teams = [(e.get('name', e.get('userId', '?')), e.get('team', '?')) for e in il]
            _engine_debug_log(f"_get_initiative gid={self.group_id} len={len(il)} teams={teams}")
        return il
    def _set_initiative(self, l):
        if _ENGINE_DEBUG_ENABLED:
            teams = [(e.get('name', e.get('userId', '?')), e.get('team', '?')) for e in l]
            _engine_debug_log(f"_set_initiative gid={self.group_id} len={len(l)} teams={teams}")
        self.set_json(f"combat_initiative_{self.group_id}", l)
    def _get_actions(self): return self.get_json(f"combat_actions_{self.group_id}", {})
    def _set_actions(self, a): self.set_json(f"combat_actions_{self.group_id}", a)
    def _get_my_actions(self, uid):
        """Get actions for uid, resolving multi-action UIDs and summon groups."""
        base = self._resolve_uid(uid)
        grp = self._get_summon_group(base)
        if grp:
            rep = grp.get('members', [base])[0] if grp.get('members') else base
            return self._get_actions().get(rep, {'主动': 0, '附加': 0})
        return self._get_actions().get(base, {'主动': 0, '附加': 0})
    def _consume_action(self, uid, action_type='主动', amount=1):
        """Consume one action point for uid, resolving multi-action UIDs and summon groups."""
        base = self._resolve_uid(uid)
        grp = self._get_summon_group(base)
        if grp:
            rep = grp.get('members', [base])[0] if grp.get('members') else base
            if rep in self._summon_groups:
                base = rep
        actions = self._get_actions()
        my_acts = actions.get(base, {'主动': 0, '附加': 0})
        my_acts[action_type] = max(0, my_acts.get(action_type, 0) - amount)
        actions[base] = my_acts
        self._set_actions(actions)
        return my_acts
    def _get_effects(self): return self.get_json(f"combat_effects_{self.group_id}", [])
    def _set_effects(self, e):
        self._buff_cache.clear()  # Invalidate buff cache (preserves references)
        self.set_json(f"combat_effects_{self.group_id}", e if e else [])
    def _get_map(self): return self.get_json(f"combat_map_{self.group_id}")
    def _set_map(self, m): self.set_json(f"combat_map_{self.group_id}", m)

    # ---- 行动力/移动力 (Movement/Action Power) methods ----
    def _get_move_power(self, uid):
        """获取角色的移动力(每回合可移动距离)。MOV默认=行动力。"""
        char = self.get_char(uid)
        base = char.get_attr('行动力', 8)
        if char.get_attr('分离行动力移动力', 0):
            base = char.get_attr('移动力', base)
        # Zone debuff (temporary, per-round)
        il = self._get_initiative()
        entry = next((e for e in il if e['userId'] == uid), None)
        if entry:
            base -= entry.get('_zone_penalty_移动力', 0)
            base -= entry.get('_zone_penalty_行动力', 0)
        return max(0, base + self._get_buff_move_mod(uid))

    def _get_action_power(self, uid):
        """获取角色的行动力(影响反应/先攻)。默认=行动力=MOV。"""
        char = self.get_char(uid)
        base = char.get_attr('行动力', 8)
        # Zone debuff (temporary, per-round)
        il = self._get_initiative()
        entry = next((e for e in il if e['userId'] == uid), None)
        if entry:
            base -= entry.get('_zone_penalty_行动力', 0)
        return max(1, base + self._get_buff_move_mod(uid))

    def _get_effective_mov(self, uid):
        """统一计算有效MOV。
        结算顺序: 基础MOV + buff/领域增减 → 飞行+8 → 减速倍率 → floor, min0。
        召唤物优先读取 MOV 属性，角色用 行动力(默认8)。"""
        import math
        resolved = self._resolve_uid(uid)
        char = self.get_char(resolved)
        # 基础 MOV：召唤物用 MOV 属性，角色用 行动力
        base = char.get_attr('MOV', None)
        if base is None:
            base = char.get_attr('行动力', 8)
            if char.get_attr('分离行动力移动力', 0):
                base = char.get_attr('移动力', base)
        # Zone 惩罚
        il = self._get_initiative()
        entry = next((e for e in il if e['userId'] == uid), None)
        if entry:
            base -= entry.get('_zone_penalty_移动力', 0)
            base -= entry.get('_zone_penalty_行动力', 0)
        # Buff 修正 (AUX 23-28)
        base += self._get_buff_move_mod(uid)
        # 飞行: +8
        if self._is_flying(uid):
            base += 8
        # 减速倍率
        slow = self._get_slow_multiplier(uid)
        result = int(math.floor(max(0, base) * slow))
        return result

    def _calc_main_actions(self, uid):
        """计算每回合主动作数。公式: max(1, (effective_MOV + 4) // 5 + 额外行动数)，无上限。
        区间映射: MOV 0-5→1动, 6-10→2动, 11-15→3动, 16-20→4动, 21-25→5动, ..."""
        effective_mov = self._get_effective_mov(uid)
        char = self.get_char(self._resolve_uid(uid))
        extra = char.get_attr('额外行动数', None)
        if extra is None:
            extra = char.get_attr('回合行动数', 0)
        return max(1, (effective_mov + 4) // 5 + extra)

    def _get_buff_move_mod(self, uid):
        """从活跃buff中提取MOV/行动力/移动力修改量（每回合缓存）。"""
        il = self._get_initiative()
        entry = next((e for e in il if e['userId'] == uid), None)
        if entry and '_cached_buff_mov_mod' in entry:
            return entry['_cached_buff_mov_mod']
        mod = 0
        for b in self._get_active_buffs(uid):
            sm = str(b.get('skillMod', ''))
            m = re.match(r'(?:MOV|行动力|移动力)([+-]\d+)', sm)
            if m:
                mod += int(m.group(1))
        if entry:
            entry['_cached_buff_mov_mod'] = mod
        return mod

    def _consume_move_power(self, uid, amount):
        """消耗移动力。返回实际可消耗量。
        多动角色的所有行动槽共享 _move_used。"""
        if not self._can_move(uid):
            return 0  # 禁锢/击晕禁止移动
        il = self._get_initiative()
        entry = next((e for e in il if e['userId'] == uid), None)
        if not entry: return 0
        base_uid = entry.get('baseUserId', uid)
        # 读取同角色所有行动槽中最大的 _move_used（同步读取）
        used = 0
        for e in il:
            if e.get('baseUserId', e['userId']) == base_uid:
                used = max(used, e.get('_move_used', 0))
        max_move = self._get_move_power(uid)
        available = max(0, max_move - used)
        consumed = min(amount, available)
        new_used = used + consumed
        # 同步写入所有行动槽
        for e in il:
            if e.get('baseUserId', e['userId']) == base_uid:
                e['_move_used'] = new_used
        return consumed

    def _reset_move_power(self):
        """每回合开始时重置所有角色移动力消耗和zone临时debuff，保存锚点坐标。"""
        il = self._get_initiative()
        for entry in il:
            entry['_move_used'] = 0
            entry['_anchor_coord'] = entry.get('coord', '')  # 回合开始时的位置作为锚点
            entry.pop('_cached_buff_mov_mod', None)
            for k in list(entry.keys()):
                if k.startswith('_zone_penalty_'):
                    del entry[k]

    def _move_to_attack_range(self, uid, target_id, atk_range):
        """移动到可攻击目标的位置。返回 (can_attack, original_coord)。"""
        # ── 状态检查：禁锢/击晕禁止自动接近 ──
        if not self._can_move(uid):
            il = self._get_initiative()
            my_entry = next((e for e in il if e['userId'] == uid), None)
            return (False, my_entry.get('coord', '') if my_entry else '')
        il = self._get_initiative()
        my_entry = next((e for e in il if e['userId'] == uid), None)
        t_entry = next((e for e in il if e['userId'] == target_id), None)
        if not my_entry or not t_entry: return (False, '')
        my_coord = my_entry.get('coord', '')
        t_coord = t_entry.get('coord', '')
        if not my_coord or not t_coord: return (False, '')
        mp, tp = parse_coord(my_coord), parse_coord(t_coord)
        if not mp or not tp: return (False, '')
        dist = max(abs(mp[0]-tp[0]), abs(mp[1]-tp[1]))
        if dist <= atk_range:
            return (True, my_coord)  # 已在射程内
        move_needed = dist - atk_range
        actual_move = self._consume_move_power(uid, move_needed)
        if actual_move <= 0:
            return (False, my_coord)
        dc = 0 if tp[0] == mp[0] else (1 if tp[0] > mp[0] else -1)
        dr = 0 if tp[1] == mp[1] else (1 if tp[1] > mp[1] else -1)
        new_col = mp[0] + dc * actual_move
        new_row = mp[1] + dr * actual_move
        new_coord = format_coord(new_col, new_row)
        map_data = self._get_map()
        if map_data and new_coord not in map_data.get('occupants', {}):
            if my_coord in map_data.get('occupants', {}):
                del map_data['occupants'][my_coord]
            map_data['occupants'][new_coord] = uid
            self._set_map(map_data)
            my_entry['coord'] = new_coord
            # 同步同角色其他行动槽的坐标（多动角色共享位置）
            base_uid = my_entry.get('baseUserId', uid)
            for e in il:
                if e['userId'] != uid and e.get('baseUserId', e['userId']) == base_uid:
                    e['coord'] = new_coord
        can_attack = (actual_move >= move_needed)
        return (can_attack, my_coord)

    def _return_to_anchor(self, uid):
        """回合结束时，消耗剩余移动力尝试退回锚点坐标。
        移动力在前压和退回之间共享——前压用得越多，退回越少。"""
        il = self._get_initiative()
        my_entry = next((e for e in il if e['userId'] == uid), None)
        if not my_entry: return
        my_coord = my_entry.get('coord', '')
        anchor = my_entry.get('_anchor_coord', '')
        if not my_coord or not anchor or my_coord == anchor: return
        mp, ap = parse_coord(my_coord), parse_coord(anchor)
        if not mp or not ap: return
        dist = max(abs(mp[0]-ap[0]), abs(mp[1]-ap[1]))
        if dist <= 0: return
        # 消耗剩余移动力退回
        actual_move = self._consume_move_power(uid, dist)
        if actual_move <= 0: return
        dc = 0 if ap[0] == mp[0] else (1 if ap[0] > mp[0] else -1)
        dr = 0 if ap[1] == mp[1] else (1 if ap[1] > mp[1] else -1)
        new_col = mp[0] + dc * actual_move
        new_row = mp[1] + dr * actual_move
        new_coord = format_coord(new_col, new_row)
        map_data = self._get_map()
        if map_data and new_coord not in map_data.get('occupants', {}):
            if my_coord in map_data.get('occupants', {}):
                del map_data['occupants'][my_coord]
            map_data['occupants'][new_coord] = uid
            self._set_map(map_data)
            my_entry['coord'] = new_coord
            # 同步同角色其他行动槽的坐标
            base_uid = my_entry.get('baseUserId', uid)
            for e in il:
                if e['userId'] != uid and e.get('baseUserId', e['userId']) == base_uid:
                    e['coord'] = new_coord

    def _calc_action_power_bp(self, atk_uid, def_uid):
        """行动力差→奖惩骰。差≥5且攻方>10→攻方奖励骰；否则防方>10→防方惩罚骰。"""
        atk_ap = self._get_action_power(atk_uid)
        def_ap = self._get_action_power(def_uid)
        atk_bonus = ''
        def_penalty = ''
        if atk_ap >= def_ap + 5 and atk_ap > 10:
            atk_bonus = 'b'
        elif def_ap > 10:
            def_penalty = 'p'
        return (atk_bonus, def_penalty)

    # ---- 动态行动数 (now delegates to _calc_main_actions) ----
    def _get_dynamic_action_count(self, uid):
        """计算主动作数（委托给新公式）。保留此方法以兼容旧调用方。"""
        return self._calc_main_actions(uid)

    def _sync_initiative_slots(self, uid):
        """已废弃 — 单槽系统无需抑制。保留以兼容旧调用方。"""
        pass

    def _sync_all_initiative_slots(self):
        """清除移动力修正缓存。每回合边界调用。"""
        il = self._get_initiative()
        for e in il:
            e.pop('_cached_buff_mov_mod', None)

    def _ensure_dynamic_slots(self, uid):
        """确保 uid 有先攻条目且设置了 baseUserId/actionIdx。
        单槽系统：仅补全字段，不扩展。"""
        base_uid = self._resolve_uid(uid)
        il = self._get_initiative()
        for e in il:
            if e.get('baseUserId', e['userId']) == base_uid:
                if 'baseUserId' not in e:
                    e['baseUserId'] = base_uid
                if 'actionIdx' not in e:
                    e['actionIdx'] = 0
                self._set_initiative(il)
                return

    # ---- 物品栏 (Inventory) methods ----
    def get_inventory(self, uid):
        """Get a character's inventory list. Returns list of {'item': name, 'count': N}."""
        return self.get_char(uid).inventory

    def add_item_to_inventory(self, uid, item_name, count=1):
        """Add items to a character's inventory."""
        char = self.get_char(uid)
        for entry in char.inventory:
            if entry['item'] == item_name:
                entry['count'] += count
                return
        char.inventory.append({'item': item_name, 'count': count})

    def remove_item_from_inventory(self, uid, item_name, count=1):
        """Remove items from a character's inventory. Returns True if successful."""
        char = self.get_char(uid)
        for i, entry in enumerate(char.inventory):
            if entry['item'] == item_name:
                if entry['count'] >= count:
                    entry['count'] -= count
                    if entry['count'] <= 0:
                        char.inventory.pop(i)
                    return True
        return False

    def _has_healing_item(self, uid):
        """Check if a character has any item in inventory that restores HP."""
        from characters_data import ITEM_TEMPLATES
        char = self.get_char(uid)
        for entry in char.inventory:
            item_name = entry['item']
            if entry['count'] > 0 and item_name in ITEM_TEMPLATES:
                item_data = ITEM_TEMPLATES[item_name]
                hp_dice = item_data.get('回复hp', '0')
                if hp_dice and hp_dice != '0':
                    return True
        return False

    def _team_has_healing_item(self, team):
        """Check if any dying character on the given team has a healing item.
        Only checks characters at 0 HP (dying state), since that's when it matters."""
        il = self._get_initiative()
        for e in il:
            if e.get('isSummon') or e.get('team') != team:
                continue
            uid = e['userId']
            hp = self._get_combat_hp(uid)
            if hp == 0 and self._is_dying(uid):
                if self._has_healing_item(uid):
                    return True
        return False

    def _remove_summons_of_owner(self, owner_uid):
        """Remove all summons owned by owner_uid from initiative and map. Also removes dead owner."""
        il = self._get_initiative()
        # Use _resolve_uid to match summons even when ownerId was set from a multi-action entry
        dead_summons = {e['userId'] for e in il
                        if e.get('isSummon') and self._resolve_uid(e.get('ownerId', '')) == owner_uid}
        # Also collect group members not in initiative
        for grp in list(self._summon_groups.values()):
            if grp.get('ownerId') == owner_uid:
                for m in grp.get('members', []):
                    dead_summons.add(m)
        # Clean map occupants for dead summons
        md = self._get_map()
        if md:
            for sid in dead_summons:
                for c, o in list(md.get('occupants', {}).items()):
                    if o == sid: del md['occupants'][c]
            self._set_map(md)
        # Remove dead summons + dead owner + multi-action entries from initiative
        new_il = [e for e in il
                  if e['userId'] not in dead_summons and self._resolve_uid(e['userId']) != owner_uid]
        if len(new_il) < len(il):
            state = self._get_state()
            if state and state.get('activeIndex', 0) >= len(new_il):
                state['activeIndex'] = max(0, len(new_il) - 1)
                self._set_state(state)
        self._set_initiative(new_il)
        # Clean up summon groups and members for this owner
        keys_to_remove = [k for k, g in self._summon_groups.items() if g.get('ownerId') == owner_uid]
        for k in keys_to_remove:
            grp = self._summon_groups.pop(k)
            for m in grp.get('members', []):
                self._summon_members.pop(m, None)
                self.characters.pop(m, None)

    def _is_untargetable(self, uid):
        """Check if uid should be excluded from targeting.
        Rule: Character with 不可指定=1 cannot be targeted if they have non-三合一 summons alive.
        If 不可指定召唤物 is set, only summons matching that name trigger untargetability."""
        entry = next((e for e in self._get_initiative() if e['userId'] == uid), None)
        if not entry or entry.get('isSummon'):
            return False
        char = self.get_char(uid)
        if char.get_attr('不可指定', 0) != 1:
            return False
        required_name = char.get_str('不可指定召唤物') or ''
        il = self._get_initiative()
        other = [e for e in il
                 if e.get('isSummon') and e.get('ownerId') == uid
                 and (self._get_combat_hp(e['userId']) or 0) > 0
                 and e.get('name', '') != '三合一'
                 and (not required_name or e.get('name', '') == required_name)]
        # Also check group members not in initiative
        for grp in self._summon_groups.values():
            if grp.get('ownerId') == uid:
                for m in grp.get('members', []):
                    if m not in {e['userId'] for e in other}:
                        mem_data = self._summon_members.get(m, {})
                        mem_name = mem_data.get('name', '')
                        if mem_name != '三合一' and (self._get_combat_hp(m) or 0) > 0 \
                                and (not required_name or mem_name == required_name):
                            other.append({'userId': m, 'name': mem_name, 'isSummon': True, 'ownerId': uid})
        return len(other) > 0

    # ── 控制状态查询层 ──

    def _get_active_statuses(self, uid):
        """返回 uid 当前所有控制状态效果的列表。入参经过 _resolve_uid。"""
        resolved = self._resolve_uid(uid)
        return [b for b in self._get_active_buffs(resolved)
                if b.get('auxType') in CONTROL_STATUS_TYPES]

    def _has_status(self, uid, status_name):
        """检查 uid 是否具有指定控制状态。入参经过 _resolve_uid。"""
        return any(b.get('auxType') == status_name
                   for b in self._get_active_buffs(self._resolve_uid(uid)))

    def _can_move(self, uid):
        """禁锢/击晕禁止移动（含自动接近和显式移动）。"""
        resolved = self._resolve_uid(uid)
        if self._has_status(resolved, '禁锢') or self._has_status(resolved, '击晕'):
            return False
        return True

    def _can_basic_attack(self, uid):
        """缴械/击晕禁止基本攻击(.s0)。"""
        resolved = self._resolve_uid(uid)
        if self._has_status(resolved, '缴械') or self._has_status(resolved, '击晕'):
            return False
        return True

    def _can_cast_active_skill(self, uid):
        """沉默/击晕禁止主动技能(.s1+ 包括 .a sN)。.s0 和被动不受限。"""
        resolved = self._resolve_uid(uid)
        if self._has_status(resolved, '沉默') or self._has_status(resolved, '击晕'):
            return False
        return True

    def _can_take_active_action(self, uid):
        """击晕禁止主动作（但 .a eat/.a give/.i end 需单独放过）。"""
        resolved = self._resolve_uid(uid)
        return not self._has_status(resolved, '击晕')

    def _can_react(self, uid):
        """击晕禁止所有反应（闪避/反击/格挡）。"""
        resolved = self._resolve_uid(uid)
        return not self._has_status(resolved, '击晕')

    def _is_stun_protected(self, uid):
        """击晕且 auxVal=='0' → 不可被伤害性攻击/技能选中。auxVal=='1' → 可被选中。"""
        resolved = self._resolve_uid(uid)
        for b in self._get_active_buffs(resolved):
            if b.get('auxType') == '击晕' and str(b.get('auxVal', '1')) == '0':
                return True
        return False

    def _is_targetable_for_damage(self, uid):
        """伤害性攻击目标检查：击晕保护(auxVal=0)返回False。不影响治疗/buff。"""
        return not self._is_stun_protected(uid)

    # ── 计数器系统（resourceCounter type=14）──

    def _get_counter(self, uid, counter_type):
        """Get current value of a resourceCounter effect for uid."""
        for e in self._get_effects():
            if e.get('type') == 'resourceCounter' and e.get('targetUserId') == uid \
               and e.get('counter_type') == counter_type:
                return e.get('current_value', 0)
        return 0

    def _mod_counter(self, uid, counter_type, delta, max_val=None):
        """Modify a resourceCounter by delta. Returns new value."""
        effects = self._get_effects()
        for e in effects:
            if e.get('type') == 'resourceCounter' and e.get('targetUserId') == uid \
               and e.get('counter_type') == counter_type:
                mx = max_val if max_val is not None else e.get('max_value', 999)
                new_val = max(0, min(mx, e.get('current_value', 0) + delta))
                e['current_value'] = new_val
                self._set_effects(effects)
                return new_val
        return 0

    def _consume_counter(self, uid, counter_type, amount):
        """Consume counter value. Returns True if enough value was available."""
        cur = self._get_counter(uid, counter_type)
        if cur >= amount:
            self._mod_counter(uid, counter_type, -amount)
            return True
        return False

    def _on_ally_dying(self, dying_uid):
        """P2: When an ally first enters dying, all teammates' limit_gauge +2."""
        init_list = self._get_initiative()
        dying_entry = next((e for e in init_list if e['userId'] == dying_uid), None)
        if not dying_entry: return
        team = dying_entry.get('team', '')
        effects = self._get_effects()
        for e in init_list:
            if e['team'] == team and e['userId'] != dying_uid and not e.get('isSummon'):
                # Check if this ally has an active limit_gauge counter effect
                has_lg = any(ef.get('type') == 'resourceCounter'
                           and ef.get('targetUserId') == e['userId']
                           and ef.get('counter_type') == 'limit_gauge'
                           for ef in effects)
                if has_lg:
                    self._mod_counter(e['userId'], 'limit_gauge', 2, 5)

    def _get_slow_multiplier(self, uid):
        """获取所有减速效果的乘积。不同来源可叠加时相乘，同名刷新取 max。"""
        resolved = self._resolve_uid(uid)
        product = 1.0
        for b in self._get_active_buffs(resolved):
            if b.get('auxType') == '减速':
                try:
                    product *= float(b.get('auxVal', '1.0'))
                except (ValueError, TypeError):
                    pass
        return max(0.0, product)

    def _get_active_buffs(self, uid):
        resolved = self._resolve_uid(uid)
        cache = getattr(self, '_buff_cache', None)
        if cache is not None and resolved in cache:
            return cache[resolved]
        # 直接检查隔离状态，避免通过 _has_status 调用本方法造成无限递归
        has_isolation = False
        for e in self._get_effects():
            if (e.get('type') in ('buff', 'debuff')
                    and e.get('auxType') == '隔离'
                    and self._resolve_uid(e.get('targetUserId', '')) == resolved
                    and e.get('remainingRounds', 0) != 0):
                has_isolation = True
                break
        result = []
        for e in self._get_effects():
            if e.get('type') not in ('buff', 'debuff'):
                continue
            if self._resolve_uid(e.get('targetUserId', '')) != resolved:
                continue
            if e.get('remainingRounds', 0) == 0:
                continue
            # 隔离: pause passive-originated buffs on the isolated target
            if has_isolation and e.get('sourcePassive'):
                continue
            result.append(e)
        if cache is not None:
            cache[resolved] = result
        return result

    # ======== AUX helper methods ========

    def _get_buff_dmg_mult(self, attacker_uid, defender_uid):
        """auxCode 1,3: net damage multiplier (sign on auxVal)."""
        net_pct = 0
        for b in self._get_active_buffs(attacker_uid):
            if b.get('auxCode') == 3:  # 造成伤害百分比
                try: net_pct += int(b.get('auxVal', '0'))
                except: pass
        for b in self._get_active_buffs(defender_uid):
            if b.get('auxCode') == 1:  # 受到伤害百分比
                try: net_pct += int(b.get('auxVal', '0'))
                except: pass
        return max(0.01, 1.0 + net_pct / 100.0)

    def _get_buff_heal_pct(self, uid, heal_type='hp'):
        """auxCode 5,7: heal amount multiplier (sign on auxVal)."""
        net_pct = 0
        for b in self._get_active_buffs(uid):
            code = b.get('auxCode', 0)
            if heal_type == 'hp' and code == 7:  # hp回复百分比
                try: net_pct += int(b.get('auxVal', '0'))
                except: pass
            elif heal_type == 'mp' and code == 5:  # mp回复百分比
                try: net_pct += int(b.get('auxVal', '0'))
                except: pass
        return max(0.01, 1.0 + net_pct / 100.0)

    def _get_buff_mp_cost_pct(self, uid):
        """auxCode 9: MP cost multiplier (sign on auxVal)."""
        net_pct = 0
        for b in self._get_active_buffs(uid):
            if b.get('auxCode') == 9:  # 魔能消耗百分比
                try: net_pct += int(b.get('auxVal', '0'))
                except: pass
        return max(0.01, 1.0 + net_pct / 100.0)

    def _get_buff_leth_adv_dis(self, uid):
        """auxCode 11-12: lethality die advantage / disadvantage."""
        buffs = self._get_active_buffs(uid)
        has_adv = any(b.get('auxCode') == 11 for b in buffs)
        has_dis = any(b.get('auxCode') == 12 for b in buffs)
        return (has_adv, has_dis)

    def _get_buff_dmg_dice_adv_dis(self, uid):
        """auxCode 13-14: damage dice advantage / disadvantage from buffs."""
        buffs = self._get_active_buffs(uid)
        has_adv = any(b.get('auxCode') == 13 for b in buffs)
        has_dis = any(b.get('auxCode') == 14 for b in buffs)
        return (has_adv, has_dis)

    def _get_buff_crit_adjustment(self, uid):
        """auxCode 21-22: returns (pct_ratio, flat) for crit threshold adjustment.
        pct_ratio = ΣauxCode21 / 100 (e.g. +10 → 0.1)
        flat      = ΣauxCode22       (e.g. +5  → 5)"""
        pct = 0; flat = 0
        for b in self._get_active_buffs(uid):
            code = b.get('auxCode', 0)
            if code == 21:
                try: pct += int(b.get('auxVal', '0'))
                except: pass
            elif code == 22:
                try: flat += int(b.get('auxVal', '0'))
                except: pass
        return (pct / 100.0, flat)

    def _get_buff_skill_mult(self, uid):
        """auxCode 20: skill value multiplier (multiple buffs multiply)."""
        mult = 1.0
        for b in self._get_active_buffs(uid):
            if b.get('auxCode') == 20:
                try: mult *= float(b.get('auxVal', '1.0'))
                except: pass
        return mult

    def _get_buff_dmg_dice_mult(self, uid):
        """auxCode 19: damage dice result multiplier (multiple buffs multiply)."""
        mult = 1.0
        for b in self._get_active_buffs(uid):
            if b.get('auxCode') == 19:
                try: mult *= float(b.get('auxVal', '1.0'))
                except: pass
        return mult

    def _eval_buff_flat_val(self, aux_val):
        """Parse auxVal as either a dice expression (e.g. '1d3') or a flat integer."""
        if not aux_val:
            return 0
        s = str(aux_val).strip()
        if 'd' in s.lower():
            return roll_dice(s)
        try:
            return int(s)
        except (ValueError, TypeError):
            return 0

    def _get_buff_dmg_flat(self, uid):
        """auxCode 2: net flat damage modifier (受到伤害加值). Supports dice expressions."""
        net = 0
        for b in self._get_active_buffs(uid):
            if b.get('auxCode') == 2:
                net += self._eval_buff_flat_val(b.get('auxVal'))
        return net

    def _get_buff_dmg_dice_bonus(self, uid):
        """Return bonus damage dice string from code 4 (伤害骰加值) + legacy bonusDmgDice.
        Code 4 takes priority; legacy bonusDmgDice only used when auxCode != 4 (avoids double-count)."""
        parts = []
        for b in self._get_active_buffs(uid):
            if b.get('auxCode') == 4 and b.get('auxVal'):
                parts.append(str(b['auxVal']))
            elif b.get('bonusDmgDice') and b.get('auxCode') != 4:
                # Legacy fallback — only when new AUX is not present on this buff
                parts.append(str(b['bonusDmgDice']))
        return '+'.join(parts) if parts else ''

    def _get_buff_heal_flat(self, uid, heal_type='hp'):
        """auxCode 6,8: flat heal bonus. Supports dice expressions."""
        net = 0
        target_code = 6 if heal_type == 'mp' else 8
        for b in self._get_active_buffs(uid):
            if b.get('auxCode') == target_code:
                net += self._eval_buff_flat_val(b.get('auxVal'))
        return net

    def _get_buff_mp_cost_flat(self, uid):
        """auxCode 10: flat MP cost modifier (魔能消耗加值). Supports dice expressions."""
        net = 0
        for b in self._get_active_buffs(uid):
            if b.get('auxCode') == 10:
                net += self._eval_buff_flat_val(b.get('auxVal'))
        return net

    def _get_active_shields(self, uid):
        resolved = self._resolve_uid(uid)
        shields = [e for e in self._get_effects()
                   if e.get('type')=='shield' and self._resolve_uid(e.get('targetUserId','')) == resolved and e.get('remainingRounds',0)!=0]
        return sum(s.get('value',0) for s in shields), shields

    def _absorb_damage_with_shield(self, uid, dmg):
        if dmg <= 0: return (0, 0, [])
        effects = self._get_effects(); remaining = dmg; absorbed = 0; msgs = []
        resolved = self._resolve_uid(uid)
        for e in effects:
            if e.get('type')!='shield' or self._resolve_uid(e.get('targetUserId','')) != resolved or e.get('remainingRounds',0)==0: continue
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
        # AUX 20: skill value multiplier (applied after flat modifiers)
        mult = self._get_buff_skill_mult(uid)
        adjusted = int(adjusted * mult)
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
        for i in range(1, 31):
            prefix = f"技能{CN_NUMS[i]}"
            name = char.get_str(f"{prefix}名称")
            if not name: continue
            # Phase filtering: load spells matching current phase (0=universal, any=phase-specific)
            spell_phase = char.get_attr(f"{prefix}阶段") or 0
            if spell_phase != 0 and spell_phase != current_phase:
                continue
            # Weapon required check
            wpn_req = char.get_str(f"{prefix}weapon_required")
            if wpn_req:
                current_wpn = char.get_str('_weapon_name') or 'staff'
                if current_wpn != wpn_req:
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
            mpft = char.get_str(f"{prefix}_mp_formula_transformed")
            if mpft: spell['_mp_formula_transformed'] = mpft
            # Read weapon_required
            wpn_req = char.get_str(f"{prefix}weapon_required")
            if wpn_req: spell['weapon_required'] = wpn_req
            # Read counter fields
            cr_str = char.get_str(f"{prefix}counter_required")
            if cr_str:
                try: spell['counter_required'] = json.loads(cr_str)
                except: pass
            cct = char.get_str(f"{prefix}cost_counter_type")
            if cct: spell['cost_counter_type'] = cct
            cca = char.get_attr(f"{prefix}cost_counter_amount")
            if cca: spell['cost_counter_amount'] = cca
            ob = char.get_attr(f"{prefix}_once_per_battle")
            if ob: spell['_once_per_battle'] = True
            for ci, letter in enumerate(CAT_LETTERS):
                pl = f"{prefix}类别{letter}"
                has_data = bool(char.get_attr(f"{pl}客体") or char.get_str(f"{pl}伤害骰") or
                    char.get_str(f"{pl}护盾值") or char.get_str(f"{pl}回复hp") or
                    char.get_str(f"{pl}技能加减值") or char.get_str(f"{pl}辅助效果") or
                    char.get_attr(f"{pl}召唤个数") or
                    char.get_attr(f"{pl}引发目标法术") or char.get_attr(f"{pl}领域中心跟随") or
                    char.get_str(f"{pl}每回合伤害骰") or
                    char.get_attr(f"{pl}制造个数") or char.get_str(f"{pl}制造个数") or
                    char.get_str(f"{pl}制造物模板"))
                if not has_data and ci > 0: continue
                stored_type = char.get_attr(f"{pl}type") or 0
                ct = stored_type if stored_type > 0 else (spell['类别'] if ci == 0 else (1 if char.get_str(f"{pl}伤害骰") else
                    2 if char.get_str(f"{pl}护盾值") else
                    3 if char.get_str(f"{pl}回复hp") or char.get_str(f"{pl}回复san") or char.get_str(f"{pl}回复mp") else
                    4 if char.get_str(f"{pl}技能加减值") or char.get_str(f"{pl}辅助效果") or char.get_str(f"{pl}其他辅助效果a") else
                    5 if char.get_attr(f"{pl}召唤个数") else
                    6 if char.get_attr(f"{pl}制造个数") or char.get_str(f"{pl}制造个数") or char.get_str(f"{pl}制造物模板") else
                    7 if char.get_attr(f"{pl}引发目标法术") else
                    8 if char.get_attr(f"{pl}领域中心跟随") or (char.get_str(f"{pl}每回合伤害骰") and char.get_attr(f"{pl}作用半径",0)>0) else
                    9 if char.get_attr(f"{pl}引发延迟回合") == 0 and char.get_attr(f"{pl}触发HP比例") else
                    char.get_attr(f"{pl}type") or 0))
                eff = {'type': ct, 'letter': letter}
                for k in ['客体','作用半径','可调节性','成功率','成功率奖惩骰','可闪避性','可反击性','可格挡性','可贯穿性',
                          '致死值','致死值优劣','附加效果时长','持续回合','引发目标法术','引发延迟回合',
                          '制造个数','制造花费回合数','召唤个数','领域中心跟随','触发HP比例','target_phase',
                          '友方延迟回复回合','敌方延迟回复回合','ignite','ignite_tick_dmg',
                          'cooldown_rounds','on_enter_mp_drain_pct','on_enter_trigger_rate',
                          '可叠加',
                          'respawn_hp','respawn_radius','max_value',
                          'cost_counter_amount','吟唱回合',
                          '幻造','once_per_transform','persist_through_battle']:
                    v = char.get_attr(f"{pl}{k}");
                    if v is not None: eff[k] = v
                for k in ['伤害骰','附加效果','护盾值','回复hp','回复san','回复mp','技能加减值',
                          '辅助效果','辅助效果值','其他辅助效果a','辅助效果值a',
                          '召唤个数','制造个数','制造花费回合数','召唤物模板','制造物模板',
                          '每回合伤害骰','吸血比例','属性削减',
                          '友方行为','友方伤害骰','友方延迟回复骰','友方延迟回复公式',
                          '敌方回复','敌方回复骰','ignite_dmg_dice','on_enter_attr_debuff',
                          'variant','weapon_state','counter_type','linked_summon']:
                    v = char.get_str(f"{pl}{k}");
                    if v: eff[k] = v
                # Parse JSON-serialized complex fields (hp_thresholds etc.)
                for k in ['hp_thresholds', 'exclude', 'eject_phases', 'on_enter_zone_damage']:
                    v = char.get_str(f"{pl}{k}")
                    if v:
                        try: eff[k] = json.loads(v)
                        except: pass
                eff.setdefault('客体',4); eff.setdefault('作用半径',0)
                # 可闪避性/可反击性: 向后兼容旧的 '可反应性' 字段
                if '可闪避性' not in eff and '可反击性' not in eff:
                    reactable = eff.get('可反应性', 1 if ct == 1 else 0)
                    eff.setdefault('可闪避性', reactable)
                    eff.setdefault('可反击性', reactable)
                else:
                    eff.setdefault('可闪避性', 1 if ct == 1 else 0)
                    eff.setdefault('可反击性', 1 if ct == 1 else 0)
                eff.setdefault('可贯穿性', 1 if ct==1 else 0)
                eff.setdefault('成功率',0)
                spell['effects'].append(eff)
            spells.append(spell)
        char.spells = spells
        return spells

    def _pick_random_target(self, caster_id, include_summons=True):
        """Pick a random valid enemy target for the caster.
        If include_summons=True (客体='R'), includes summons in the pool.
        If include_summons=False (客体='RP'), only picks from player characters.
        Returns userId or None if no valid targets.
        """
        import random as _random_rt
        init_list = self._get_initiative()
        my_entry = next((e for e in init_list if e['userId'] == caster_id), None)
        mt = my_entry.get('team', 'Y') if my_entry else 'Y'
        candidates = []
        for e in init_list:
            if e['team'] == mt:
                continue
            if (self._get_combat_hp(e['userId']) or 0) <= 0:
                continue
            if self._is_untargetable(e['userId']):
                continue
            if not include_summons and e.get('isSummon'):
                continue  # RP: exclude summons
            candidates.append(e['userId'])
        return _random_rt.choice(candidates) if candidates else None

    def _get_weighted_target(self, caster_id):
        """Select target using pre-computed AI weight strategy preferences (T0-T3).
        Falls back to None if weights unavailable, allowing _smart_target to use heuristic.
        """
        _ensure_weight_cache()
        if not _weight_cache:
            return None

        # Quick lookup: get caster serial + determine solo/team
        caster_char = self.get_char(caster_id)
        caster_serial = getattr(caster_char, 'serial', '')
        if not caster_serial:
            return None

        init_list = self._get_initiative()
        my_entry = next((e for e in init_list if e['userId'] == caster_id), None)
        mt = my_entry.get('team', 'Y') if my_entry else 'Y'
        has_allies = any(e['team'] == mt and e['userId'] != caster_id
                         and (self._get_combat_hp(e['userId']) or 0) > 0
                         for e in init_list)
        q_mode = 'Q_team' if has_allies else 'Q_solo'
        best_strategy = _weight_best_strategy.get(f"{q_mode}:{caster_serial}")

        if not best_strategy:
            return None

        # Get enemies
        enemies = [e for e in init_list
                   if e['team'] != mt
                   and (self._get_combat_hp(e['userId']) or 0) > 0
                   and not self._is_untargetable(e['userId'])]
        if not enemies:
            return None

        # Apply strategy to select target
        # (re-implementing select_target_by_strategy inline to avoid import cycle)
        if best_strategy == 'T0':  # Lowest HP non-summon
            non_summons = [e for e in enemies if not e.get('isSummon')]
            pool = non_summons if non_summons else enemies
            return min(pool, key=lambda e: self._get_combat_hp(e['userId']) or 9999)['userId']
        elif best_strategy == 'T1':  # Nearest (first in initiative)
            return enemies[0]['userId']
        elif best_strategy == 'T2':  # Highest threat
            # Compute threat: estimate damage per round based on best melee skill
            def _threat(e):
                uid = e['userId']
                char = self.get_char(uid)
                bn, bv = char.get_best_melee() if char else ('', 0)
                return bv
            return max(enemies, key=_threat)['userId']
        elif best_strategy == 'T3':  # Lowest HP summon
            summons = [e for e in enemies if e.get('isSummon')]
            if summons:
                return min(summons, key=lambda e: self._get_combat_hp(e['userId']) or 9999)['userId']
            return enemies[0]['userId'] if enemies else None

        return None

    def _smart_target(self, caster_id, spell):
        if not spell: return caster_id
        has_dmg = any(e['type']==1 for e in spell.get('effects',[]))
        has_heal = any(e['type'] in (2,3,4) or (e['type']==8 and e.get('回复hp')) for e in spell.get('effects',[]))
        has_zone_dmg = any(e['type']==8 and e.get('每回合伤害骰') and not e.get('回复hp') for e in spell.get('effects',[]))
        # Type-4 buff/debuff with 客体=5 (enemy debuff) or 124 (legacy) targets an enemy
        has_enemy_debuff = any(
            e['type'] == 4 and e.get('客体', 4) in (5, 124)
            for e in spell.get('effects', [])
        )
        # Check for "R" (random including summons) or "RP" (random players only) 客体
        has_random_target = False
        random_include_summons = True
        for e in spell.get('effects', []):
            obj = str(e.get('客体', '')).upper().strip()
            if obj == 'R':
                has_random_target = True
                random_include_summons = True
                break
            elif obj == 'RP':
                has_random_target = True
                random_include_summons = False
                break

        if has_random_target:
            tgt = self._pick_random_target(caster_id, include_summons=random_include_summons)
            return tgt if tgt else caster_id

        if has_dmg or has_zone_dmg or has_enemy_debuff:
            # Try weight-based target selection first
            weighted_tgt = self._get_weighted_target(caster_id)
            if weighted_tgt:
                return weighted_tgt
            # Fallback: first enemy by initiative
            init_list = self._get_initiative()
            my_entry = next((e for e in init_list if e['userId']==caster_id), None)
            mt = my_entry.get('team','Y') if my_entry else 'Y'
            enemies = [e for e in init_list if e['team']!=mt and (self._get_combat_hp(e['userId'])or 0)>0 and not self._is_untargetable(e['userId'])]
            if not enemies:
                all_teams = [(e.get('name', e.get('userId', '?')), e.get('team', '?')) for e in init_list]
                _engine_debug_log(f"_smart_target: caster={caster_id} team={mt} spell={spell.get('name','?')} "
                      f"NO ENEMIES! all teams: {all_teams}")
            return enemies[0]['userId'] if enemies else caster_id
        return caster_id

    def process_command(self, uid, cmd):
        """Simple command interface for compatibility."""
        if cmd == '.hs':
            char = self.get_char(uid)
            # Save original stats for .unh reversal
            char.hs_orig = {
                '体力上限': char.get_attr('体力上限', 10),
                '体力': char.get_attr('体力', 10),
                '魔力上限': char.get_attr('魔力上限', 10),
                '魔力': char.get_attr('魔力', 10),
                '闪避': char.get_attr('闪避', 25),
                '体格': char.get_attr('体格', 0),
                '行动力': char.get_attr('行动力', 8),
                'skills': {sk: char.get_attr(sk) for sk in ALL_COMBAT_SKILLS if char.get_attr(sk) > 0},
            }
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
            # Recalculate db from updated 体格
            new_build = char.get_attr('体格', 0)
            char.set_str('db', _DB_TABLE.get(int(new_build), '0'))
            self._init_combat_hp(uid, nhp)
        elif cmd == '.unh':
            char = self.get_char(uid)
            if not getattr(char, 'hs_transformed', False):
                return
            orig = getattr(char, 'hs_orig', None)
            if orig:
                char.set_attr('体力上限', orig['体力上限'])
                char.set_attr('体力', orig['体力'])
                char.set_attr('魔力上限', orig['魔力上限'])
                char.set_attr('魔力', orig['魔力'])
                char.set_attr('闪避', orig['闪避'])
                char.set_attr('体格', orig['体格'])
                char.set_attr('行动力', orig['行动力'])
                for sk, v in orig.get('skills', {}).items():
                    char.set_attr(sk, v)
                self._init_combat_hp(uid, orig['体力上限'])
            # Restore db from original 体格
            orig_build = char.get_attr('体格', 0)
            char.set_str('db', _DB_TABLE.get(int(orig_build), '0'))
            char.hs_transformed = False
            char.hs_orig = None

    def _get_cooldown(self, uid, spell_index):
        """Return remaining cooldown rounds for a spell, or 0 if ready."""
        for e in self._get_effects():
            if e.get('type') == 'cooldown' and e.get('sourceUserId') == uid \
               and e.get('spellIndex') == spell_index and e.get('remainingRounds', 0) > 0:
                return e['remainingRounds']
        return 0

    def _use_skill(self, uid, skill_num, args):
        """Execute .sN command — used by tournament AI loop."""
        if skill_num == 0:
            return self._basic_attack(uid)
        spells = self.load_spells(uid) or []
        spell = next((s for s in spells if s['index'] == skill_num), None)
        if not spell: return f'未找到技能{skill_num}'
        # Check cooldown
        cd = self._get_cooldown(uid, spell['index'])
        if cd > 0:
            return f"技能【{spell['name']}】冷却中（剩余 {cd} 回合）"
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
            cd = self._get_cooldown(uid, spell['index'])
            if cd > 0:
                return f"技能【{spell['name']}】冷却中（剩余 {cd} 回合）"
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
        """End current character's turn and advance initiative.
        Single-slot system: each character has one initiative entry.
        Turn advances when 主动 exhausted or .i end called."""
        state = self._get_state(); il = self._get_initiative()
        if not state: return

        # ── 回合结束：退回锚点 ──
        self._return_to_anchor(self._resolve_uid(uid))

        if not il:
            self._check_and_mark_end(state)
            return False

        old_idx = state['activeIndex']
        state['activeIndex'] = (old_idx + 1) % len(il)

        if state['activeIndex'] == 0:
            # ── New round ──
            state['round'] = state.get('round', 1) + 1
            self._reset_move_power()
            # Reset all actions using formula
            acts = {}
            for e in il:
                base = e.get('baseUserId', e['userId'])
                if base not in acts:
                    acts[base] = {
                        '主动': self._calc_main_actions(base),
                        '附加': 3,
                    }
            self._set_actions(acts)
            self._tick_down(); self._apply_zone_effects()
            self._sync_all_initiative_slots()  # clears buff_mov cache
        self._set_state(state)
        self._check_and_mark_end(state)

        # ── Auto-skip dead entries ──
        il2 = self._get_initiative()
        safety = 0
        while il2 and safety < len(il2) * 2:
            safety += 1
            cur = il2[state['activeIndex']]
            cuid = cur['userId']
            should_skip = False
            chp = self._get_combat_hp(cuid)
            if chp is not None and chp <= 0:
                if not cur.get('isSummon'):
                    should_skip = True
                else:
                    alive_members = self._get_alive_summon_group_members(cuid)
                    if not alive_members:
                        should_skip = True
            if not should_skip:
                break
            state['activeIndex'] = (state['activeIndex'] + 1) % len(il2)
            if state['activeIndex'] == 0:
                state['round'] = state.get('round', 1) + 1
                self._reset_move_power()
                acts = {}
                for e in il2:
                    base = e.get('baseUserId', e['userId'])
                    if base not in acts:
                        acts[base] = {
                            '主动': self._calc_main_actions(base),
                            '附加': 3,
                        }
                self._set_actions(acts)
                self._tick_down(); self._apply_zone_effects()
                self._sync_all_initiative_slots()
                il2 = self._get_initiative()
            self._set_state(state)
            self._check_and_mark_end(state)

        return state['activeIndex'] == 0 if state else False

    def _check_and_mark_end(self, state):
        """Call _check_battle_end and, if over, store result and set phase='ended'."""
        result = self._check_battle_end()
        if result:
            self._battle_result = result
            if state:
                state['phase'] = 'ended'
                self._set_state(state)

    def _check_battle_end(self):
        """Check if the battle should end after a turn completes.

        Rules:
        1. If only one side has surviving non-summon characters → that side wins.
        2. If a team has ONLY dying characters AND they have no healing items → that team loses.
        3. If neither side has survivors → compare last-death overflow damage per team;
           the team whose last death took LESS overflow wins (smaller overflow = winner).
        4. If overflow is equal or untracked, Y wins (arbitrary tie-break).

        "Surviving" means: HP > 0, OR HP == 0 but in dying state (濒死) WITH healing items.
        Only non-summon characters are counted.

        Returns dict {winner, ended, mutual_death?} if battle ended, None otherwise.
        """
        il = self._get_initiative()

        # Count living non-summon characters per team (resolve multi-action UIDs)
        # "Alive" = HP > 0, OR in dying state WITH healing items available
        y_alive = set()
        x_alive = set()
        y_has_true_alive = False
        x_has_true_alive = False
        for e in il:
            if e.get('isSummon'):
                continue
            uid = e['userId']
            hp = self._get_combat_hp(uid)
            base = self._resolve_uid(uid)
            if hp is not None and hp > 0:
                if e['team'] == 'Y':
                    y_alive.add(base)
                    y_has_true_alive = True
                else:
                    x_alive.add(base)
                    x_has_true_alive = True
            elif hp == 0 and self._is_dying(uid):
                # Character at 0 HP but in dying state → still fighting (if team has healing)
                if e['team'] == 'Y':
                    y_alive.add(base)
                else:
                    x_alive.add(base)

        # Dying-team defeat check: if a team has ONLY dying characters
        # and none of them have healing items → they auto-lose
        if not y_has_true_alive and y_alive:
            if not self._team_has_healing_item('Y'):
                y_alive.clear()  # Y cannot recover → effectively dead
        if not x_has_true_alive and x_alive:
            if not self._team_has_healing_item('X'):
                x_alive.clear()  # X cannot recover → effectively dead

        if y_alive and not x_alive:
            return {'winner': 'Y', 'ended': True}
        if x_alive and not y_alive:
            return {'winner': 'X', 'ended': True}
        if not y_alive and not x_alive:
            # Mutual annihilation — 3-tier tiebreaker:
            # 1. Less absolute overflow = winner
            # 2. If equal → less overflow% of max HP = winner
            # 3. If still equal → random winner
            y_overflow = self._last_death_overflow.get('Y', float('inf'))
            x_overflow = self._last_death_overflow.get('X', float('inf'))
            result = {'ended': True, 'mutual_death': True,
                      'y_overflow': y_overflow, 'x_overflow': x_overflow}
            if y_overflow < x_overflow:
                result['winner'] = 'Y'; return result
            if x_overflow < y_overflow:
                result['winner'] = 'X'; return result
            # Equal absolute overflow — compare as % of max HP
            y_max = self._last_death_max_hp.get('Y', 1) if hasattr(self, '_last_death_max_hp') else 1
            x_max = self._last_death_max_hp.get('X', 1) if hasattr(self, '_last_death_max_hp') else 1
            y_pct = y_overflow / max(y_max, 1)
            x_pct = x_overflow / max(x_max, 1)
            if y_pct < x_pct:
                result['winner'] = 'Y'; result['tie_pct'] = True; return result
            if x_pct < y_pct:
                result['winner'] = 'X'; result['tie_pct'] = True; return result
            # Absolutely equal — random winner
            import random
            result['winner'] = 'Y' if random.random() < 0.5 else 'X'
            result['tie'] = True
            return result

        return None  # Battle continues

    def _get_initiative_display(self):
        """Format initiative table with DEX rolls and HPs. Suppressed entries hidden.
        Same-name summons are merged into one line showing (行动1)/(行动2)/..."""
        il = self._get_initiative()
        state = self._get_state()
        rnd = state.get('round', 1) if state else 1
        lines = [f"===== 第{rnd}回合 =====", "先攻表:"]
        # Merge consecutive entries with same name + summonGroup (or same name + isSummon)
        merged = []
        for e in il:
            group_key = e.get('summonGroup') or (e.get('name') if e.get('isSummon') else None)
            last = merged[-1] if merged else None
            if group_key and last and last[0] == group_key:
                last[1].append(e)
            else:
                merged.append((group_key, [e]))
        idx = 0
        for _grp, entries in merged:
            idx += 1
            base = entries[0]
            dex_v = base.get('dex', '?')
            roll = base.get('initRoll', '?')
            rank = base.get('initRank', 0)
            hp = self._get_combat_hp(base['userId']) or 0
            if len(entries) == 1:
                name = base.get('displayName', base.get('name', base['userId']))
                # Show group size for consolidated summon groups
                grp_tag = ''
                if base.get('isSummon'):
                    grp_members = self._get_alive_summon_group_members(base.get('baseUserId', base['userId']))
                    if len(grp_members) > 1:
                        total_hp = sum(self._get_combat_hp(m) or 0 for m in grp_members)
                        grp_tag = f' ×{len(grp_members)} (HP合计:{total_hp})'
                lines.append(f"  {idx}. {name}{grp_tag} D100={roll}/DEX={dex_v} {rank_text(rank)} HP:{hp}")
            else:
                names = " / ".join(
                    f"{e.get('displayName', e.get('name', e['userId']))} (行动{j+1})"
                    for j, e in enumerate(entries))
                lines.append(f"  {idx}. {names} D100={roll}/DEX={dex_v} {rank_text(rank)} HP:{hp}")
        return '\n'.join(lines)

    def _get_member_status_display(self, uid):
        """Format member HP status for turn announcement."""
        il = self._get_initiative()
        entry = next((e for e in il if e['userId'] == uid), None)
        name = entry.get('displayName', entry.get('name', uid)) if entry else uid
        lines = [f"【{name} 的回合！】", "成员状态:"]
        seen = set()
        for e in il:
            base = e.get('baseUserId', e['userId'])
            if base in seen:
                continue
            seen.add(base)
            n = e.get('displayName', e.get('name', e['userId']))
            hp = self._get_combat_hp(e['userId']) or 0
            lines.append(f"  {n} HP:{hp}")
        return '\n'.join(lines)

    def _get_available_skills_display(self, uid):
        """Return formatted list of available skills with MP/CD/action status."""
        char = self.get_char(uid)
        spells = char.spells or self.load_spells(uid)
        acts = self._get_actions().get(uid, {'主动': 0, '附加': 0})
        mp = char.get_attr('魔力', 0) or 0
        effects = self._get_effects()

        lines = ["【可用技能】"]
        for s in spells:
            timing = s.get('时机', '2')
            name = s.get('name', '?')
            mp_cost = sum(int(e.get('消耗mp', 0) or 0) for e in s.get('effects', []))
            cd_eff = [e for e in effects if e.get('type') == 'cooldown'
                      and e.get('sourceUserId') == uid
                      and e.get('spellIndex') == s.get('index')
                      and e.get('remainingRounds', 0) > 0]
            cd_remaining = cd_eff[0].get('remainingRounds', 0) if cd_eff else 0

            timing_label = []
            if has_timing(timing, '2'): timing_label.append('主')
            if has_timing(timing, '3'): timing_label.append('附')
            timing_str = '/'.join(timing_label) if timing_label else '?'

            status = ""
            if cd_remaining > 0:
                status = f" [CD:{cd_remaining}回合]"
            elif mp_cost > mp:
                status = f" [MP不足({mp}/{mp_cost})]"
            elif has_timing(timing, '2') and acts.get('主动', 0) <= 0:
                status = " [主动作不足]"
            elif has_timing(timing, '3') and acts.get('附加', 0) <= 0:
                status = " [附加动作不足]"

            lines.append(f"  .s{s['index']} [{timing_str}] {name} MP:{mp_cost}{status}")

        bn, bv = char.get_best_melee()
        if bv > 0:
            atk_status = "" if acts.get('主动', 0) > 0 else " [主动作不足]"
            lines.append(f"  .s0 基本攻击 [{bn}={bv}]{atk_status}")

        return '\n'.join(lines)

    def _get_targets_display(self, uid):
        """Return enemy target list with distance, HP, and untargetable markers."""
        il = self._get_initiative()
        me = next((e for e in il if e['userId'] == uid), None)
        if not me: return "无目标信息"
        mt = me.get('team', 'Y')
        my_coord = me.get('coord', '?')

        enemies = [e for e in il if e['team'] != mt and (self._get_combat_hp(e['userId']) or 0) > 0]
        if not enemies:
            return "【敌对目标】无存活敌人"

        lines = ["【敌对目标】"]
        global_idx = 0
        for e in il:
            global_idx += 1
            if e['team'] == mt: continue
            if (self._get_combat_hp(e['userId']) or 0) <= 0: continue

            name = e.get('displayName', e.get('name', e['userId']))
            hp = self._get_combat_hp(e['userId']) or 0
            coord = e.get('coord', '?')
            if my_coord != '?' and coord != '?':
                mp_a = parse_coord(my_coord)
                mp_b = parse_coord(coord)
                dist = max(abs(mp_a[0]-mp_b[0]), abs(mp_a[1]-mp_b[1])) if mp_a and mp_b else '?'
            else:
                dist = '?'
            unt = " [不可指定]" if self._is_untargetable(e['userId']) else ""

            lines.append(f"  #{global_idx} {name} HP:{hp} 坐标:{coord} 距离:{dist}{unt}")

        return '\n'.join(lines)

    def _get_damage_dice(self, uid, skill_name):
        """Look up damage dice with suffix support.
        Maps skill names to damage suffixes: e.g. 剑→'a', 斗殴→'b'.
        Falls back to 伤害値. Resolves 'db' token from character's 体格."""
        char = self.get_char(uid)
        db_str = _get_db_str(char)
        suffix_map = {'剑': 'a', '斗殴': 'b'}
        suffix = suffix_map.get(skill_name, '')
        if suffix:
            dd = char.get_str(f"伤害值{suffix}")
            if dd: return _resolve_db(dd, db_str)
        return _resolve_db(char.get_str("伤害值") or "1d4", db_str)

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

        # _once_per_battle: check if spell already used this battle
        if spell.get('_once_per_battle'):
            consumed = getattr(char, '_once_consumed', set())
            if spell['name'] in consumed:
                return f'{char.name} 本场战斗已使用过【{spell["name"]}】！\n'
            consumed.add(spell['name'])
            char._once_consumed = consumed

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
        elif spell.get('_mp_formula_transformed') and getattr(char, 'hs_transformed', False):
            mp_cost = roll_dice(str(spell['_mp_formula_transformed']))
        else:
            mp_cost = int(mp_cost_raw) if mp_cost_raw else 0

        san_cost_raw = spell.get('消耗san', 0)
        if isinstance(san_cost_raw, str) and 'd' in str(san_cost_raw).lower():
            san_cost = roll_dice(str(san_cost_raw))
        else:
            san_cost = int(san_cost_raw) if san_cost_raw else 0

        # ── 射程检查: type=1 敌方效果 (MP扣除前) ──
        dmg_effs = [e for e in spell.get('effects', []) if e.get('type') == 1
                    and str(e.get('客体', 0)).upper() in ('4', '5', '45', 'R', 'RP')]
        _orig_coord = ''
        if dmg_effs:
            atk_range = _get_attack_range(spell_effect=dmg_effs[0])
            can_atk, _orig_coord = self._move_to_attack_range(caster_id, target_id, atk_range)
            if not can_atk:
                return f'{char.name} 无法接近目标！（射程={atk_range}）'

        # Check counter cost (rings, etc.)
        cost_ct = spell.get('cost_counter_type', '')
        cost_ca = spell.get('cost_counter_amount', 0)
        if cost_ct and cost_ca:
            if not self._consume_counter(caster_id, cost_ct, cost_ca):
                return f'{char.name} 资源不足！需要 {cost_ca} {cost_ct}。\n'
            out += f'  消耗 {cost_ca} {cost_ct}\n'

        # Check counter requirement (e.g. 极限技·凶斩)
        cr = spell.get('counter_required')
        if cr:
            cr_type, cr_amount = cr
            if self._get_counter(caster_id, cr_type) < cr_amount:
                return f'{char.name} {cr_type}不足！需要 {cr_amount}。\n'
            self._consume_counter(caster_id, cr_type, cr_amount)
            out += f'  消耗 {cr_amount} {cr_type}\n'

        # AUX 9/10: MP cost modifiers
        if mp_cost > 0:
            mp_cost = max(1, int(mp_cost * self._get_buff_mp_cost_pct(caster_id)))
            mp_cost += self._get_buff_mp_cost_flat(caster_id)  # AUX code 10
            mp_cost = max(1, mp_cost)

        if mp_cost > 0:
            cur_mp = char.get_attr('魔力', 0) or 0
            if cur_mp < mp_cost: return f'{char.name} 魔力不足！需要 {mp_cost} MP。'
            char.set_attr('魔力', cur_mp - mp_cost); out += f'  消耗 {mp_cost} MP\n'
        if san_cost > 0:
            cur_san = char.get_attr('理智', 50)
            char.set_attr('理智', max(0, cur_san - san_cost)); out += f'  消耗 {san_cost} SAN\n'

        # ── 被动标记：若法术时机含'1'（被动），所有产生效果标记 sourcePassive ──
        is_passive_spell = has_timing(spell.get('时机', '2'), '1')

        # ── same_target_multiplier: if all damage effects target the same unit, amplify ──
        stm = spell.get('same_target_multiplier', 0)
        if stm > 0:
            dmg_effs_all = [e for e in spell.get('effects', []) if e.get('type') == 1]
            if len(dmg_effs_all) > 1:
                targets = set()
                for de in dmg_effs_all:
                    tgt = target_id  # all effects use the same target_id
                    targets.add(tgt)
                if len(targets) == 1:
                    # Store multiplier so damage handlers can apply it
                    spell['_same_target_mult_active'] = stm
                    out += f'  同一目标加成: ×{stm}\n'

        for eff in spell.get('effects', []):
            ct = eff['type']
            if ct == 1:  # Damage
                out += self._handle_spell_damage_effect(caster_id, target_id, eff, spell)
                continue
            elif ct == 2:  # Shield
                sv = roll_dice(eff.get('护盾值','1d4')); dur = eff.get('持续回合',1)
                effects = self._get_effects()
                shield_entry = {'type':'shield','value':sv,'remainingRounds':dur,'sourceUserId':caster_id,
                    'targetUserId':target_id or caster_id,'spellName':spell['name'],
                    'spellIndex':spell['index'],'persistent':spell.get('默认延续性',0)}
                if is_passive_spell:
                    shield_entry['sourcePassive'] = True
                effects.append(shield_entry)
                self._set_effects(effects); out += f'  获得 {sv} 点护盾 ({eff.get("护盾值","1d4")})\n'

            elif ct == 3:  # Heal (HP/SAN/MP with caps)
                tid = target_id or caster_id
                hp_heal = int(roll_dice(eff.get('回复hp','0')) * self._get_buff_heal_pct(tid, 'hp'))
                hp_heal += self._get_buff_heal_flat(tid, 'hp')  # AUX code 8
                san_heal = roll_dice(eff.get('回复san','0'))
                mp_heal = int(roll_dice(eff.get('回复mp','0')) * self._get_buff_heal_pct(tid, 'mp'))
                mp_heal += self._get_buff_heal_flat(tid, 'mp')  # AUX code 6
                tchar = self.get_char(tid)
                if hp_heal > 0:
                    chp = self._hp_safe(tid, 10)
                    if chp <= 0 and not self._is_dying(tid):
                        out += f'  目标已死亡，无法治疗\n'
                    else:
                        mhp = tchar.get_attr('体力上限', chp) if tchar else chp
                        # Dying: heal excess damage first
                        dying_eff = self._is_dying(tid)
                        if dying_eff:
                            excess = dying_eff.get('excessDamage', 0)
                            healed_excess = min(hp_heal, excess)
                            dying_eff['excessDamage'] = excess - healed_excess
                            hp_heal -= healed_excess
                            if dying_eff['excessDamage'] <= 0 and hp_heal > 0:
                                chp = hp_heal
                                self._set_combat_hp(tid, chp)
                                # Remove dying effect — re-fetch effects to minimize write-after-read window
                                effs = [e for e in list(self._get_effects()) if e is not dying_eff]
                                self._set_effects(effs)
                                out += f'  脱离濒死！回复 HP: {chp}\n'
                            else:
                                out += f'  治愈濒死伤害 {healed_excess}点 (剩余{dying_eff["excessDamage"]})\n'
                        else:
                            actual_hp = min(chp+hp_heal, mhp); overflow_hp = max(0, (chp+hp_heal) - mhp)
                            self._set_combat_hp(tid, actual_hp); out += f'  回复 HP +{hp_heal} ({eff.get("回复hp","0")})\n'
                            # 事象的馈赠：溢出HP充能月能 (1:1)
                            if overflow_hp > 0 and self._has_moon_buff(tid):
                                entry = next((e for e in self._get_initiative() if e['userId'] == tid), None)
                                if entry:
                                    charged = self._charge_moon_energy(entry.get('team', 'Y'), overflow_hp)
                                    if charged > 0: out += f'  月能充能 (HP溢出): +{charged}\n'
                if san_heal > 0:
                    cs = tchar.get_attr('理智', 50) if tchar else 50
                    tchar.set_attr('理智', min(cs+san_heal, 99)); out += f'  回复 SAN +{san_heal}\n'
                if mp_heal > 0:
                    cm = tchar.get_attr('魔力', 0) if tchar else 0
                    mx = tchar.get_attr('魔力上限', cm) if tchar else cm
                    actual_mp = min(cm+mp_heal, mx); overflow_mp = max(0, (cm+mp_heal) - mx)
                    tchar.set_attr('魔力', actual_mp); out += f'  回复 MP +{mp_heal}\n'
                    # 事象的馈赠：溢出MP充能月能 (1:1)
                    if overflow_mp > 0 and self._has_moon_buff(tid):
                        entry = next((e for e in self._get_initiative() if e['userId'] == tid), None)
                        if entry:
                            charged = self._charge_moon_energy(entry.get('team', 'Y'), overflow_mp)
                            if charged > 0: out += f'  月能充能 (MP溢出): +{charged}\n'

            elif ct == 4:  # Buff (with auxCode)
                dur = eff.get('持续回合',1); aux_type = _eff_get(eff, 'auxType')
                aux_code = AUX_NAME_TO_CODE.get(aux_type, 0)
                aux_val = _eff_get(eff, 'auxVal')
                stackable = eff.get('可叠加', 0)
                tid = target_id or caster_id
                is_control_status = aux_type in CONTROL_STATUS_TYPES
                effects = self._get_effects()
                # Dedup: control statuses match on targetUserId+auxType only (ignore spellName)
                # Other AUX match on targetUserId+spellName+auxType
                existing = None
                if not stackable:
                    for e in effects:
                        if e.get('type') != 'buff' or e.get('targetUserId') != tid:
                            continue
                        if is_control_status:
                            if e.get('auxType') == aux_type:
                                existing = e; break
                        else:
                            if e.get('spellName') == spell['name'] and e.get('auxType') == aux_type:
                                existing = e; break
                if existing:
                    existing['remainingRounds'] = max(existing.get('remainingRounds', 0), dur)
                    # For 减速: also refresh auxVal (multiplier) when duration is refreshed
                    if is_control_status and aux_type == '减速':
                        existing['auxVal'] = aux_val
                    out += f'  刷新辅助效果: {aux_type} {aux_val} (延长至{existing["remainingRounds"]}回合)\n'
                else:
                    bonus_dmg = eff.get('伤害骰', '')
                    buff_entry = {'type':'buff','remainingRounds':dur,'skillMod':eff.get('技能加减值',''),
                        'auxType':aux_type,'auxVal':aux_val,'auxCode':aux_code,
                        'sourceUserId':caster_id,'targetUserId':tid,
                        'spellName':spell['name'],'spellIndex':spell['index'],
                        'persistent':spell.get('默认延续性',0),
                        'stackable':stackable,
                        'exclude': eff.get('exclude', [])}
                    if bonus_dmg:
                        buff_entry['bonusDmgDice'] = bonus_dmg
                    if is_passive_spell:
                        buff_entry['sourcePassive'] = True
                    # 击晕: auxVal is attack protection switch (0/1), duration from 持续回合
                    # 减速: auxVal is MOV multiplier, duration from 持续回合
                    # Other control statuses: auxVal is duration in rounds
                    effects.append(buff_entry)
                    bonus_note = f' (伤害骰+{bonus_dmg})' if bonus_dmg else ''
                    out += f'  施加辅助效果: {aux_type} {aux_val}{bonus_note}\n'
                self._set_effects(effects)
                # s1 灵感大爆发：月能充能 (2d3 灵感值)
                if spell['name'] == '灵感大爆发':
                    caster_entry = next((e for e in self._get_initiative() if e['userId'] == caster_id), None)
                    if caster_entry:
                        charged = self._charge_moon_energy(caster_entry.get('team', 'Y'), roll_dice('2d3'))
                        if charged > 0: out += f'  月能充能 (灵感大爆发): +{charged}\n'

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
                # Shared initiative roll for same-template batch summons
                shared_s_roll = None
                if count > 1:
                    shared_s_roll, _ = roll_d100("")
                for _ in range(count):
                    sid = self._create_summon(caster_id, tmpl,
                        summon_group=tmpl if count > 1 else None,
                        shared_roll=shared_s_roll, shared_rank=None)
                    if ignite and sid:
                        # Mark newly created summon as ignited (check initiative + summon_members)
                        il = self._get_initiative()
                        found = False
                        for e in il:
                            if e['userId'] == sid:
                                e['ignited'] = True
                                e['ignite_dmg_dice'] = ignite_dmg_dice
                                e['ignite_tick_dmg'] = ignite_tick_dmg
                                if e.get('skills'):
                                    for s in e['skills']:
                                        s['dice'] = ignite_dmg_dice
                                e['dmg_dice'] = ignite_dmg_dice
                                found = True; break
                        if not found and sid in self._summon_members:
                            self._summon_members[sid]['ignited'] = True
                            self._summon_members[sid]['ignite_dmg_dice'] = ignite_dmg_dice
                            self._summon_members[sid]['ignite_tick_dmg'] = ignite_tick_dmg
                            sm_skills = self._summon_members[sid].get('skills', [])
                            for s in sm_skills:
                                s['dice'] = ignite_dmg_dice
                        if found:
                            self._set_initiative(il)
                # ── Re-sync all initiative slots after batch summon creation ──
                # Each _create_summon calls _ensure_dynamic_slots → _sync_initiative_slots,
                # but re-sorting in each call can interact with cache; a final sync ensures
                # all summons (and existing entities) have correct _suppressed flags.
                self._sync_all_initiative_slots()
                cnt_detail = f'{count_raw}={count}' if isinstance(count_raw, str) and 'd' in str(count_raw) else str(count)
                out += f'  召唤 {cnt_detail} 个【{tmpl or "使魔"}】\n'
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
                count_raw = eff.get('制造个数',1); cr_raw = eff.get('制造花费回合数',0)
                # Support dice formulas like '1d2+1' for count/rounds
                if isinstance(count_raw, str) and 'd' in str(count_raw):
                    count = roll_dice(str(count_raw))
                else:
                    count = int(count_raw) if count_raw else 1
                if isinstance(cr_raw, str) and 'd' in str(cr_raw):
                    cr = roll_dice(str(cr_raw))
                else:
                    cr = int(cr_raw) if cr_raw else 0
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
                zone_entry = {'type':'zone','center':center,'radius':radius,'remainingRounds':dur,
                    'tickDmg':tick_dmg,'tickHealHp':tick_heal_hp,'tickHealMp':tick_heal_mp,
                    'centerFollows':cf,'filter':of,'attributeDebuff':ad,
                    'sourceUserId':caster_id,'spellName':spell['name'],'spellIndex':spell['index'],
                    'persistent':spell.get('默认延续性',0),
                    'stackable':eff.get('可叠加',0)}
                if is_passive_spell:
                    zone_entry['sourcePassive'] = True
                effects.append(zone_entry)
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

            elif ct == 13:  # deathPrevention — 残机替身
                effects = self._get_effects()
                effects.append({
                    'type': 'deathPrevention', 'sourceUserId': caster_id, 'targetUserId': caster_id,
                    'respawn_hp': eff.get('respawn_hp', 1), 'respawn_radius': eff.get('respawn_radius', 3),
                    'eject_phases': eff.get('eject_phases', []), 'once_per_transform': eff.get('once_per_transform', True),
                    'remainingRounds': eff.get('持续回合', 999), 'persistent': spell.get('默认延续性', 0),
                    'spellName': spell['name'], 'spellIndex': spell['index'],
                })
                if is_passive_spell:
                    effects[-1]['sourcePassive'] = True
                self._set_effects(effects)
                out += f'  获得残机替身（死亡时以1HP重生）\n'

            elif ct == 14:  # resourceCounter — 计数器
                effects = self._get_effects()
                # Check if counter already exists (update it)
                existing = None
                for e in effects:
                    if e.get('type') == 'resourceCounter' and e.get('targetUserId') == (target_id or caster_id) \
                       and e.get('counter_type') == eff.get('counter_type', ''):
                        existing = e; break
                if existing:
                    existing['max_value'] = max(existing.get('max_value', 0), eff.get('max_value', 999))
                    mod_delta = eff.get('mod_delta', 0)
                    if mod_delta:
                        existing['current_value'] = min(existing['max_value'], existing.get('current_value', 0) + mod_delta)
                else:
                    effects.append({
                        'type': 'resourceCounter', 'counter_type': eff.get('counter_type', ''),
                        'sourceUserId': caster_id, 'targetUserId': target_id or caster_id,
                        'max_value': eff.get('max_value', 999), 'current_value': eff.get('current_value', 0),
                        'remainingRounds': eff.get('持续回合', 999), 'persistent': spell.get('默认延续性', 0),
                        'spellName': spell['name'], 'spellIndex': spell['index'],
                    })
                    if is_passive_spell:
                        effects[-1]['sourcePassive'] = True
                self._set_effects(effects)
                out += f'  初始化 {eff.get("counter_type","")} 计数器 (max={eff.get("max_value",999)})\n'

            elif ct == 17:  # stateToggle — 形态/武器切换
                target_phase = eff.get('target_phase', 0)
                weapon_state = eff.get('weapon_state', '')
                if target_phase:
                    self._transition_phase(caster_id, target_phase)
                    out += f'  切换到阶段 {target_phase}\n'
                if weapon_state:
                    char.set_attr('_weapon_state', {'staff': 0, 'bow': 1, 'sword': 2}.get(weapon_state, 0))
                    char.set_str('_weapon_name', weapon_state)
                    out += f'  切换武器为 {weapon_state}\n'

            elif ct == 18:  # allyTeleport — 传送至友方身边
                ally = self.get_char(target_id)
                if ally:
                    caster_char = self.get_char(caster_id)
                    # Find ally position on map
                    ally_coord = None
                    map_data = self._get_map()
                    if map_data:
                        for c, occ in map_data.get('occupants', {}).items():
                            if occ == target_id: ally_coord = c; break
                    if ally_coord:
                        # Move caster to ally's position (if unoccupied or replace)
                        caster_coord = None
                        for c, occ in list(map_data.get('occupants', {}).items()):
                            if occ == caster_id: caster_coord = c; break
                        if caster_coord and caster_coord in map_data.get('occupants', {}):
                            del map_data['occupants'][caster_coord]
                        map_data['occupants'][ally_coord] = caster_id
                        self._set_map(map_data)
                        out += f'  {caster_char.name} 传送至 {ally.name} 身边\n'

        return out

    def _apply_spell_damage(self, caster_id, target_id, dmg_dice, pen, leth,
                             lifesteal_ratio=0, dot_dice='', dur=0, spell_name='',
                             spell_index=0, atk_rank=1, atk_roll=0, spell=None):
        """Apply direct spell damage to target. Shared by base and reaction paths.
        When atk_rank > 1, applies COC7 rank-based damage (rank 2=advantage, rank 3=max/pen, rank 4=×2).
        Returns output string for the damage application step."""
        out = ''
        # AUX 30: 月相的庇佑 — 免疫一次伤害
        if self._consume_immunity(target_id):
            out += f'  月相的庇佑：免疫本次伤害！\n'
            return out
        # AUX code 4: merge bonus damage dice into dmg_dice expression
        bonus_dice = self._get_buff_dmg_dice_bonus(caster_id)
        if bonus_dice:
            dmg_dice = f"{dmg_dice}+{bonus_dice}" if dmg_dice else bonus_dice
        mx = max_damage(dmg_dice)
        dmg_detail = ''
        if atk_rank == 2:
            r1, d1 = roll_dice_detailed(dmg_dice); r2, d2 = roll_dice_detailed(dmg_dice)
            dmg_val = max(r1, r2); dmg_detail = f'优势({d1}, {d2})→{dmg_val}'
        elif atk_rank == 3:
            if pen:
                r1, d1 = roll_dice_detailed(dmg_dice)
                dmg_val = mx + r1; dmg_detail = f'贯穿!满值{mx}+{d1}={dmg_val}'
            else:
                dmg_val = mx; dmg_detail = f'满值={mx}'
        elif atk_rank == 4:
            if atk_roll == 1 or pen:
                dmg_val = mx * 2; dmg_detail = f'大成功!满值{mx}×2={dmg_val}'
            else:
                r1, d1 = roll_dice_detailed(dmg_dice)
                dmg_val = mx + r1; dmg_detail = f'满值{mx}+{d1}={dmg_val}'
        else:
            dmg_val, dmg_detail = roll_dice_detailed(dmg_dice)
        # AUX 1,3,19: apply damage multipliers before shield
        dmg_val = int(dmg_val * self._get_buff_dmg_mult(caster_id, target_id) * self._get_buff_dmg_dice_mult(caster_id))
        # same_target_multiplier
        if spell and spell.get('_same_target_mult_active', 0) > 0:
            dmg_val = int(dmg_val * spell['_same_target_mult_active'])
        dmg_val += self._get_buff_dmg_flat(target_id); dmg_val = max(0, dmg_val)  # AUX code 2: before shield
        # Check for damageModifier effects (e.g. 火鼠裘)
        for e in self._get_effects():
            if e.get('type') == 'damageModifier' and e.get('targetUserId') == target_id \
               and e.get('variant') == 'fire_rat_cloak' and e.get('remainingRounds', 0) > 0:
                def_char = self.get_char(target_id)
                cur_mp = def_char.get_attr('魔力', 0) if def_char else 0
                if cur_mp >= 1:
                    def_char.set_attr('魔力', cur_mp - 1)
                    if dmg_val <= 3:
                        dmg_val = 0
                    elif dmg_val < 7:
                        dmg_val = 3
                    else:
                        dmg_val = int(dmg_val * 0.6)
                    out += f'  火鼠裘减免：最终伤害 {dmg_val}\n'
                break
        eff_dmg, absorbed, _ = self._absorb_damage_with_shield(target_id, dmg_val)
        cur_hp = self._get_combat_hp(target_id) or 10
        # Lethality: d(2×cur_hp) ≤ 致死值 → instant death
        exp_dmg = avg_damage(dmg_dice)
        if leth and exp_dmg > 6:
            leth_die_size = max(2, cur_hp * 2)
            leth_adv, leth_dis = self._get_buff_leth_adv_dis(caster_id)
            if leth_adv:
                lr = max(random.randint(1, leth_die_size), random.randint(1, leth_die_size))
            elif leth_dis:
                lr = min(random.randint(1, leth_die_size), random.randint(1, leth_die_size))
            else:
                lr = random.randint(1, leth_die_size)
            if lr <= leth:
                cur_hp = 0
                out += f'  致死骰: 1d{leth_die_size}={lr} ≤ {leth} 即死!\n'
            else:
                cur_hp = cur_hp - eff_dmg
                out += f'  致死骰: 1d{leth_die_size}={lr} > {leth} 失败\n'
        else: cur_hp = cur_hp - eff_dmg
        self._set_combat_hp(target_id, cur_hp, source_dmg=eff_dmg)
        out += f'  伤害: {dmg_detail} → {eff_dmg}点 (HP:{cur_hp})\n'

        # Lifesteal: heal caster based on damage dealt
        if lifesteal_ratio > 0 and eff_dmg > 0:
            char = self.get_char(caster_id)
            heal = int(dmg_val * lifesteal_ratio)
            chp = self._get_combat_hp(caster_id) or 10
            mhp = char.get_attr('体力上限', chp)
            self._set_combat_hp(caster_id, min(chp + heal, mhp))
            out += f'  吸血回复 {heal} HP\n'

        # DOT: store recurring damage effect
        if dur > 0 or dot_dice:
            effects = self._get_effects()
            effects.append({'type': 'dot', 'remainingRounds': max(dur, 1),
                'dotDice': dot_dice or dmg_dice,
                'pen': pen, 'leth': leth,
                'sourceUserId': caster_id, 'targetUserId': target_id,
                'spellName': spell_name, 'spellIndex': spell_index})
            self._set_effects(effects)
            if dot_dice: out += f'  持续伤害: 每回合 {dot_dice} ({max(dur, 1)}回合)\n'
        return out

    def _handle_spell_damage_effect(self, caster_id, target_id, eff, spell):
        """Execute a single type==1 damage effect. Returns output string.
        Base implementation: success rate -> direct damage (no reaction).
        FullBattleEngine overrides to insert reaction between success and damage."""
        out = ''
        dmg_dice = eff.get('伤害骰', '1d4')
        char = self.get_char(caster_id) if caster_id else None
        dmg_dice = _resolve_db(dmg_dice, _get_db_str(char))
        pen = eff.get('可贯穿性', 0)
        leth = eff.get('致死值', 0)
        sr = eff.get('成功率', 0)
        dur = eff.get('持续回合', 0)
        dot_dice = eff.get('每回合伤害骰', '')
        ls = float(eff.get('吸血比例', '0') or '0')

        # 幻造 (phantom creation): check for 幻造兵武 passive and roll 写作 skill
        if eff.get('幻造'):
            # Look for 幻造兵武 passive effect on caster
            caster_effects = self._get_effects()
            phantom_bonus = 0
            for ce in caster_effects:
                if ce.get('type') == 'buff' and ce.get('spellName') == '幻造兵武' \
                   and ce.get('targetUserId') == caster_id:
                    if char:
                        writing_val = int(char.get_attr('写作', 0) or 0)
                        if writing_val > 0:
                            roll, _ = roll_d100('')
                            if roll <= writing_val:
                                phantom_bonus = ce.get('auxVal', 0) or 0
                                out += f'  幻造兵武·写作检定: D100={roll}/{writing_val} 成功！伤害+{phantom_bonus}\n'
                            else:
                                out += f'  幻造兵武·写作检定: D100={roll}/{writing_val} 失败\n'
                    break
            if phantom_bonus:
                try:
                    if int(phantom_bonus) > 0:
                        dmg_dice = f"{dmg_dice}+{phantom_bonus}"
                except (ValueError, TypeError):
                    dmg_dice = f"{dmg_dice}+{phantom_bonus}"

            # s6 即兴短句：月能充能 (2 + 写作加值)
            caster_entry = next((e for e in self._get_initiative() if e['userId'] == caster_id), None)
            spell_name_ph = spell.get('name', '') if spell else ''
            if caster_entry and '即兴短句' in str(spell_name_ph):
                charge_amt = 2
                if phantom_bonus:
                    try: charge_amt += int(phantom_bonus)
                    except: pass
                charged = self._charge_moon_energy(caster_entry.get('team', 'Y'), charge_amt)
                if charged > 0: out += f'  月能充能 (即兴短句): +{charged}\n'

        # Friend/foe behavior
        friend_behavior = eff.get('友方行为', '')
        is_friendly = False
        if friend_behavior:
            il = self._get_initiative()
            ce = next((e for e in il if e['userId'] == caster_id), None)
            te = next((e for e in il if e['userId'] == target_id), None)
            is_friendly = ce and te and ce.get('team') == te.get('team')

        # Success rate check with COC7 rank
        # If no explicit 成功率, fall back to caster's best melee skill for enemy-targeting spells.
        # Self-targeting effects (客体=1) without 成功率 are auto-success (e.g. self-buffs).
        if sr <= 0:
            obj = eff.get('客体', 4)
            obj_str = str(obj).upper().strip()
            if obj == 1:
                # Self-targeting: auto-success (e.g. 灵牛 附魔术 self-buff)
                atk_rank = 4; check_roll = 1
            elif obj_str in ('R', 'RP'):
                # Random-targeting: treat same as enemy-targeting (needs success roll)
                char = self.get_char(caster_id)
                _, sr = char.get_best_melee()
                sr = max(1, sr)  # Ensure at least 1 to force a roll
            else:
                # Enemy-targeting: use caster's best melee skill as default success rate
                char = self.get_char(caster_id)
                _, sr = char.get_best_melee()
                sr = max(1, sr)  # Ensure at least 1 to force a roll
        if sr > 0:
            atk_rank = 4; check_roll = 1  # will be overwritten by roll below
            sr_bp_raw = eff.get('成功率奖惩骰', 0) or 0
            if isinstance(sr_bp_raw, int):
                if sr_bp_raw > 0:
                    sr_bp = 'b' + (str(sr_bp_raw) if sr_bp_raw > 1 else '')
                elif sr_bp_raw < 0:
                    sr_bp = 'p' + (str(abs(sr_bp_raw)) if abs(sr_bp_raw) > 1 else '')
                else:
                    sr_bp = ''
            else:
                sr_bp = str(sr_bp_raw) if sr_bp_raw else ''
            check_roll, bp_detail = roll_d100(sr_bp)
            bp_str = f', {bp_detail}' if bp_detail else ''
            crit_pct, crit_flat = self._get_buff_crit_adjustment(caster_id)
            atk_rank = success_rank(check_roll, sr, crit_pct, crit_flat)
            if atk_rank <= 0:
                out += f'  成功率检定: D100={check_roll}/{sr}{bp_str} {rank_text(atk_rank)}！\n'
                return out
            out += f'  成功率检定: D100={check_roll}/{sr}{bp_str} {rank_text(atk_rank)}！\n'

        if is_friendly:
            # Friend-target damage: deal friendly damage, schedule delayed heal
            fdmg_dice = eff.get('友方伤害骰', dmg_dice)
            dmg_val, dmg_detail = roll_dice_detailed(fdmg_dice)
            eff_dmg, absorbed, _ = self._absorb_damage_with_shield(target_id, dmg_val)
            cur_hp = self._get_combat_hp(target_id) or 10
            actual_lost = min(cur_hp, eff_dmg)
            cur_hp = cur_hp - actual_lost
            self._set_combat_hp(target_id, cur_hp, source_dmg=actual_lost)
            tname_f = self.get_char(target_id).name if target_id else '目标'
            out += f'  友方伤害: {dmg_detail} → {actual_lost}点 (HP:{cur_hp})\n'
            # Schedule delayed heal
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

            # Enemy delayed heal
            enemy_recovery = eff.get('敌方回复', '')
            if enemy_recovery and eff_dmg > 0:
                recovery_dice = eff.get('敌方回复骰', '2d6')
                heal_rounds_r = eff.get('敌方延迟回复回合', 3)
                if enemy_recovery == 'min_half':
                    heal_total_r = min(eff_dmg // 2, roll_dice(recovery_dice))
                else:
                    heal_total_r = roll_dice(recovery_dice)
                if heal_total_r > 0:
                    per_round_r = max(1, heal_total_r // heal_rounds_r)
                    effs_r = self._get_effects()
                    effs_r.append({
                        'type': 'delayedHeal', 'remainingRounds': heal_rounds_r,
                        'healPerRound': per_round_r, 'totalHeal': heal_total_r, 'healed': 0,
                        'sourceUserId': caster_id, 'targetUserId': target_id,
                        'spellName': spell['name'], 'spellIndex': spell['index'],
                        'persistent': 0,
                    })
                    self._set_effects(effs_r)
                    tname_h = self.get_char(target_id).name if target_id else '目标'
                    out += f'  敌方延迟回复: {tname_h} 共{heal_total_r}HP, 每回合{per_round_r}HP ({heal_rounds_r}回合)\n'
        else:
            # Enemy-target damage with rank-based damage
            out += self._apply_spell_damage(caster_id, target_id, dmg_dice, pen, leth,
                                            lifesteal_ratio=ls, dot_dice=dot_dice, dur=dur,
                                            spell_name=spell['name'], spell_index=spell['index'],
                                            atk_rank=atk_rank, atk_roll=check_roll, spell=spell)
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

        # Ignite existing summons (generic: check if new phase has any ignite spells)
        new_spells = char.spells  # spells reloaded after phase change
        has_ignite = any(
            any(e.get('type') == 5 and e.get('ignite') for e in s.get('effects', []))
            for s in new_spells
        )
        if has_ignite:
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

        # Re-fetch effects: _check_hp_triggers and _process_zone_specials may have
        # modified effects (e.g., removed zones on phase transition)
        effects = self._get_effects()

        # Auto-increment limit_gauge counters
        for e in effects:
            if e.get('type') == 'resourceCounter' and e.get('counter_type') == 'limit_gauge':
                e['current_value'] = min(e.get('max_value', 5), e.get('current_value', 0) + 1)
        self._set_effects(effects)

        # ---- Dying state round-start CON saves ----
        for eff in effects:
            if eff.get('type') == 'dying' and eff.get('remainingRounds', 0) > 0:
                uid = eff.get('targetUserId', '')
                eff['dyingRounds'] = eff.get('dyingRounds', 0) + 1
                rnd = eff['dyingRounds']
                # Required rank escalates: round1=成功(1), r2=困难(2), r3=极难(3), r4+=大成功(4)
                required = min(rnd, 4)
                diff_map = {1: 'normal', 2: 'hard', 3: 'extreme', 4: 'critical'}
                if not self._check_con_save(uid, diff_map.get(required, 'normal')):
                    # Died in dying state
                    char = self.get_char(uid)
                    msgs.append(f"{char.name} 在濒死第{rnd}回合安息了...")
                    self._remove_summons_of_owner(uid)
                    # Mark for removal
                    eff['remainingRounds'] = -1

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
                    cur_hp = cur_hp - remaining  # raw (may be negative → overflow)
                    self._set_combat_hp(eff['targetUserId'], cur_hp, source_dmg=remaining)
                    msgs.extend(sh_msgs)
                    msgs.append(f"持续伤害【{eff.get('spellName','')}】→ {eff['dotDice']}={dot_dmg}，造成{remaining}点伤害(HP:{max(0,cur_hp)})")
                    if cur_hp <= 0:
                        # Remove dead target + their summons from initiative
                        target_entry = next((e for e in init_list if e['userId'] == eff['targetUserId']), None)
                        if target_entry and not target_entry.get('isSummon'):
                            self._remove_summons_of_owner(eff['targetUserId'])
                            msgs.append(f"目标因持续伤害死亡，召唤物一同清除！")
                        else:
                            self._set_initiative([e for e in init_list if e['userId'] != eff['targetUserId']])
                            msgs.append(f"目标因持续伤害死亡，退出战斗！")

            # Delayed heal processing
            if eff.get('type') == 'delayedHeal' and eff.get('targetUserId'):
                heal_amt = eff.get('healPerRound', 0)
                if heal_amt > 0:
                    tid = eff['targetUserId']
                    heal_amt = int(heal_amt * self._get_buff_heal_pct(tid, 'hp'))
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

            # ── 隔离暂停：被动效果不倒计时 ──
            if eff.get('sourcePassive'):
                source_uid = eff.get('sourceUserId', '')
                if source_uid and self._has_status(source_uid, '隔离'):
                    new_effects.append(eff)
                    continue

            # linked_summon: buff expires when linked summon no longer exists on caster's team
            linked_summon = eff.get('linked_summon', '')
            if linked_summon:
                source_uid = eff.get('sourceUserId', '')
                source_entry = next((e for e in init_list if e['userId'] == source_uid), None)
                team = source_entry.get('team', 'Y') if source_entry else 'Y'
                summon_alive = any(
                    e.get('isSummon') and e.get('name') == linked_summon
                    and e.get('team') == team
                    and (self._get_combat_hp(e['userId']) or 0) > 0
                    for e in init_list
                )
                if not summon_alive:
                    eff['remainingRounds'] = 0  # Force expire
                elif eff.get('remainingRounds', 0) <= 1:
                    eff['remainingRounds'] = 999  # Keep alive while summon exists

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

        # ── 月能补满：虚假之月每回合为事象的馈赠持有者补满HP/MP ──
        for entry in init_list:
            if entry.get('isSummon') and entry.get('name') == '虚假之月' \
               and (self._get_combat_hp(entry['userId']) or 0) > 0:
                moon_uid = entry['userId']
                moon_char = self.get_char(moon_uid)
                moon_mp = moon_char.get_attr('魔力', 0) or 0
                if moon_mp <= 0: continue
                team = entry.get('team', 'Y')
                for ally in init_list:
                    if ally.get('team') != team: continue
                    aid = ally['userId']
                    if not self._has_moon_buff(aid): continue
                    ach = self.get_char(aid)
                    cur_hp = self._get_combat_hp(aid)
                    if cur_hp is None or cur_hp <= 0: continue
                    max_hp = ach.get_attr('体力上限', cur_hp) or cur_hp
                    missing_hp = max(0, max_hp - cur_hp)
                    cur_mp = ach.get_attr('魔力', 0) or 0
                    max_mp = ach.get_attr('魔力上限', cur_mp) or cur_mp
                    missing_mp = max(0, max_mp - cur_mp)
                    total_missing = missing_hp + missing_mp
                    if total_missing <= 0 or moon_mp <= 0: continue
                    spend = min(total_missing, moon_mp)
                    hp_part = min(missing_hp, spend)
                    mp_part = spend - hp_part
                    if hp_part > 0:
                        self._set_combat_hp(aid, cur_hp + hp_part)
                    if mp_part > 0:
                        ach.set_attr('魔力', cur_mp + mp_part)
                    moon_mp -= spend
                    moon_char.set_attr('魔力', moon_mp)
                    msgs.append(f'虚假之月·月能补满 → {ach.name}: HP+{hp_part} MP+{mp_part} (剩余月能:{moon_mp})')
                    if moon_mp <= 0: break

        # ── 月相的庇佑：重伤/濒死时消耗5月能获得免疫一次伤害 ──
        for entry in init_list:
            aid = entry['userId']
            if not self._has_moon_buff(aid): continue
            hp = self._get_combat_hp(aid)
            if hp is None or hp <= 0: continue
            ach = self.get_char(aid)
            max_hp = ach.get_attr('体力上限', hp) or hp
            hp_pct = float(hp) / float(max_hp) if max_hp > 0 else 1.0
            # Check if already has immunity (don't re-apply)
            has_immunity = any(e.get('auxCode') == 30 and e.get('targetUserId') == aid
                               and e.get('remainingRounds', 0) > 0 for e in effects)
            if hp_pct < 0.5 and not has_immunity:
                team = entry.get('team', 'Y')
                moon_uid, moon_char = self._find_moon_for_team(team)
                if moon_char:
                    moon_mp = moon_char.get_attr('魔力', 0) or 0
                    if moon_mp >= 5:
                        moon_char.set_attr('魔力', moon_mp - 5)
                        self._apply_immunity_buff(aid, moon_uid)
                        msgs.append(f'月相的庇佑：消耗5月能，{ach.name} 获得免疫一次伤害！(剩余月能:{moon_mp - 5})')

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

        # Clear _once_per_round consumed sets for all characters
        for entry in init_list:
            uid = entry.get('baseUserId', entry['userId'])
            char = self.get_char(uid)
            if char and hasattr(char, '_round_consumed'):
                char._round_consumed = set()

        # MP regen per round — deduplicate by base uid for multi-action characters
        seen_uids = set()
        for entry in init_list:
            uid = entry.get('baseUserId', entry['userId'])
            if uid in seen_uids: continue
            seen_uids.add(uid)
            hp = self._get_combat_hp(uid)
            if hp is not None and hp > 0:
                char = self.get_char(uid); cm = char.get_attr('魔力',0) or 0
                mx = char.get_attr('魔力上限',cm) or cm
                regen = roll_dice('2d6')
                if regen > 0: char.set_attr('魔力', min(cm+regen, mx))
        return '\n'.join(msgs) if msgs else ''

    # ---- Summon system (stub — overridden by subclasses) ----
    def _create_summon(self, caster_id, template_name, summon_group=None, shared_roll=None, shared_rank=None):
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
    def _apply_item_heal(self, item_name, target_id):
        """Apply an item's healing effect to target. Returns formatted result string."""
        from characters_data import ITEM_TEMPLATES
        item = ITEM_TEMPLATES[item_name]
        tchar = self.get_char(target_id)
        tname = tchar.name if tchar else target_id
        hp_heal = int(roll_dice(item.get('回复hp', '0')) * self._get_buff_heal_pct(target_id, 'hp'))
        mp_heal = int(roll_dice(item.get('回复mp', '0')) * self._get_buff_heal_pct(target_id, 'mp'))
        san_heal = roll_dice(item.get('回复san', '0'))
        parts = []
        if hp_heal > 0:
            chp = self._hp_safe(target_id, 10)
            mhp = tchar.get_attr('体力上限', chp) if tchar else chp
            self._set_combat_hp(target_id, min(chp + hp_heal, mhp))
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
        # Apply buff if item template specifies one (e.g. 闪避+10 from cake)
        buff_msg = ''
        skill_mod = item.get('技能加减值', '')
        buff_dur = item.get('buff持续回合', 0)
        if skill_mod and buff_dur > 0:
            effects = self._get_effects()
            effects.append({
                'type': 'buff',
                'remainingRounds': buff_dur,
                'skillMod': skill_mod,
                'auxType': '',
                'auxVal': '',
                'auxCode': 0,
                'sourceUserId': target_id,
                'targetUserId': target_id,
                'spellName': f'食用【{item_name}】',
                'spellIndex': 0,
                'persistent': 0,
                'stackable': 0,
            })
            self._set_effects(effects)
            buff_msg = f'，{skill_mod}({buff_dur}回合)'
        return f"食用【{item_name}】→ {tname} 回复 {'+'.join(parts)}{buff_msg}"

    def _eat_cake(self, eater_id, target_id=None):
        """Consume a ready cake from inventory or shared effects pool.
        First checks eater's personal inventory (物品栏), then falls back to
        the shared battle effects pool (for mid-battle crafted items).
        Returns (result_text, success)."""
        from characters_data import ITEM_TEMPLATES
        tid = target_id or eater_id

        # 1) Check eater's personal inventory first
        eater_char = self.get_char(eater_id)
        for i, entry in enumerate(eater_char.inventory):
            item_name = entry['item']
            if item_name in ITEM_TEMPLATES and entry['count'] > 0:
                # Consume one from inventory
                result = self._apply_item_heal(item_name, tid)
                entry['count'] -= 1
                if entry['count'] <= 0:
                    eater_char.inventory.pop(i)
                return (result, True)

        # 2) Fall back to shared effects pool (mid-battle crafted items)
        effects = self._get_effects()
        for i, e in enumerate(effects):
            if e.get('type') != 'create': continue
            if e.get('craftRoundsRemaining', 1) > 0: continue
            tmpl = e.get('template', '')
            if not tmpl or tmpl not in ITEM_TEMPLATES: continue
            result = self._apply_item_heal(tmpl, tid)
            effects.pop(i); self._set_effects(effects)
            return (result, True)
        return ("无可用的蛋糕", False)

    def _has_ready_cake(self):
        """Check if any ready cake exists (checks inventories + shared effects)."""
        from characters_data import ITEM_TEMPLATES
        # Check all characters' inventories
        for char in self.characters.values():
            for entry in char.inventory:
                if entry.get('item', '') in ITEM_TEMPLATES and entry.get('count', 0) > 0:
                    return True
        # Check shared effects pool
        for e in self._get_effects():
            if e.get('type') == 'create' and e.get('craftRoundsRemaining', 1) <= 0:
                if e.get('template', '') in ITEM_TEMPLATES: return True
        return False

class FullBattleEngine(CombatEngine):
    """Detailed COC7 battle mode with full attack flow, reactions, and logging.

    When fast_mode=True, skips detailed text formatting in attacks/spells,
    returning compact data structures suitable for AI training loops."""
    def __init__(self, fast_mode=False):
        super().__init__()
        self.fast_mode = fast_mode
        self.max_rounds = 20 if fast_mode else 30
        self._ai_react_dodge_w = {}
        self._ai_react_counter_w = {}
        self._ai_react_block_w = {}
        self._summoned_once = {}  # caster_id -> set of template names ever summoned
        self._summon_counters = {}
        self._allow_failed_reaction = True  # 是否允许失败/大失败的攻击触发反应

    # ── Aliases for FastBattleEngine / ai_trainer compatibility ──
    @property
    def _react_dw(self):
        return self._ai_react_dodge_w
    @_react_dw.setter
    def _react_dw(self, val):
        self._ai_react_dodge_w = val

    @property
    def _react_cw(self):
        return self._ai_react_counter_w
    @_react_cw.setter
    def _react_cw(self, val):
        self._ai_react_counter_w = val

    @property
    def _react_bw(self):
        return self._ai_react_block_w
    @_react_bw.setter
    def _react_bw(self, val):
        self._ai_react_block_w = val

    def setup_battle(self, team_a, team_b, map_size="10x10", season_status=None):
        w, h = map(int, map_size.split("x"))
        self.group_id = f"battle_{random.randint(1000,9999)}"
        self._set_map({"width":w, "height":h, "entryRow":math.ceil(h/2), "obstacles":{}, "occupants":{}})
        all_chars = team_a + team_b; init_list = []; map_data = self._get_map()
        # Y team (left side) — single initiative entry per character
        for i, uid in enumerate(team_a):
            char = self.get_char(uid)
            row = min(h-1, math.ceil(h/2) + i - len(team_a)//2)
            coord = format_coord(1, row)
            dex_val = char.get_attr("敏捷",50)
            init_roll, _ = roll_d100("")
            init_rank = success_rank(init_roll, dex_val)
            init_list.append({"userId":uid, "baseUserId":uid, "name":char.name, "actionIdx":0,
                              "team":"Y", "dex":dex_val, "initRoll":init_roll, "initRank":init_rank, "coord":coord})
            map_data["occupants"][coord] = uid
        # X team (right side) — single initiative entry per character
        for i, uid in enumerate(team_b):
            char = self.get_char(uid)
            row = min(h-1, math.ceil(h/2) + i - len(team_b)//2)
            coord = format_coord(w-2, row)
            dex_val = char.get_attr("敏捷",50)
            init_roll, _ = roll_d100("")
            init_rank = success_rank(init_roll, dex_val)
            init_list.append({"userId":uid, "baseUserId":uid, "name":char.name, "actionIdx":0,
                              "team":"X", "dex":dex_val, "initRoll":init_roll, "initRank":init_rank, "coord":coord})
            map_data["occupants"][coord] = uid
        self._set_map(map_data); init_list.sort(key=lambda e: (-e["initRank"], -e["dex"], e["initRoll"]))
        self._set_initiative(init_list)
        # Save original team roster for timeout HP ratio
        self._team_roster = [(uid, 'Y') for uid in team_a] + [(uid, 'X') for uid in team_b]
        for uid in all_chars:
            char = self.get_char(uid); self._init_combat_hp(uid, char.get_attr("体力",10))
        # 主动作数基于有效MOV公式计算（被动尚未施放，先按基础值初始化）
        self._set_actions({uid: {"主动": self._calc_main_actions(uid), "附加": 3} for uid in all_chars})
        # Roll 状态 for each character before battle
        for uid in all_chars:
            char = self.get_char(uid)
            char.set_attr('状态', season_status_roll(season_status, uid))

        # Auto-cast passive spells at battle start (with isolation guard)
        for uid in all_chars:
            if self._has_status(uid, '隔离'):
                continue  # 隔离中不自动施放被动
            spells = self.get_char(uid).spells or self.load_spells(uid)
            for s in spells:
                if has_timing(s.get("时机","2"), "1"):
                    target = self._smart_target(uid, s)
                    self._execute_spell(uid, target, s)

        # Recalculate actions after passives (may have changed MOV via buffs)
        acts = self._get_actions()
        for uid in all_chars:
            acts[uid] = {"主动": self._calc_main_actions(uid), "附加": 3}
        self._set_actions(acts)

        self._set_state({"phase":"active", "round":1, "activeIndex":0})
        # Skip dead entries to find first valid active
        self._skip_to_valid_active()

    def _skip_to_valid_active(self):
        """Advance activeIndex past dead entries without triggering round logic."""
        il = self._get_initiative()
        state = self._get_state()
        if not il or not state:
            return
        safety = 0
        while safety < len(il):
            safety += 1
            cur = il[state['activeIndex']]
            cuid = cur['userId']
            chp = self._get_combat_hp(cuid)
            skip = (chp is not None and chp <= 0 and not cur.get('isSummon'))
            if not skip:
                # Check summon group all dead
                if cur.get('isSummon') and chp is not None and chp <= 0:
                    grp_members = self._get_alive_summon_group_members(cuid)
                    if not grp_members:
                        skip = True
            if not skip:
                break
            state['activeIndex'] = (state['activeIndex'] + 1) % len(il)
            self._set_state(state)

    def _skip_to_valid_active(self):
        """Advance activeIndex past suppressed and dead entries without triggering round logic."""
        il = self._get_initiative()
        state = self._get_state()
        if not il or not state:
            return
        safety = 0
        while safety < len(il):
            safety += 1
            cur = il[state['activeIndex']]
            cuid = cur['userId']
            skip = cur.get('_suppressed', False)
            if not skip:
                chp = self._get_combat_hp(cuid)
                if chp is not None and chp <= 0 and not cur.get('isSummon'):
                    skip = True
            if not skip:
                break
            state['activeIndex'] = (state['activeIndex'] + 1) % len(il)
            self._set_state(state)

    def _coc7_attack(self, atk_uid, def_uid, skill_name, skill_val, dmg_dice, pen, leth, bp_suffix="", dmg_adv=False, dmg_dis=False):
        """Full COC7 attack: roll → reaction → rank compare → damage by success level → shield → lethality."""
        achar = self.get_char(atk_uid); dchar = self.get_char(def_uid)
        aname = achar.name; dname = dchar.name; lines = []
        # ── 影之克隆被攻击 → 其他克隆和主体获得月相庇佑 ──
        def_entry = next((e for e in self._get_initiative() if e['userId'] == def_uid), None)
        if def_entry and def_entry.get('isSummon') and def_entry.get('name') == '影之克隆':
            owner_id = def_entry.get('ownerId')
            il_clone = self._get_initiative()
            for e_clone in il_clone:
                if e_clone['userId'] != def_uid:
                    is_other_clone = e_clone.get('isSummon') and e_clone.get('name') == '影之克隆' and e_clone.get('ownerId') == owner_id
                    is_owner = e_clone.get('baseUserId', e_clone['userId']) == owner_id or e_clone['userId'] == owner_id
                    if is_other_clone or is_owner:
                        self._apply_immunity_buff(e_clone['userId'], def_uid)
                        lines.append(f'  月相的庇佑：{e_clone.get("displayName", e_clone.get("name", "?"))} 获得免疫一次伤害！')
        # ── 射程移动 ──
        atk_range = _get_attack_range(skill_name=skill_name)
        can_atk, _orig_coord = self._move_to_attack_range(atk_uid, def_uid, atk_range)
        if not can_atk:
            lines.append(f'{aname} 无法接近目标！（射程={atk_range}）')
            return (def_uid, atk_uid, lines)
        # ── 飞行近战限制 ──
        can_melee, fly_penalty = self._can_melee(atk_uid, def_uid)
        if not can_melee:
            lines.append(f'{aname} 无法近战飞行目标 {dname}！')
            return (def_uid, atk_uid, lines)
        if fly_penalty:
            bp_suffix = (bp_suffix or '') + 'p'  # 飞行近战惩罚骰

        # ── 幻造兵武 (phantom creation): basic attacks also benefit from 幻造 buff ──
        atk_effects = self._get_effects()
        for ce in atk_effects:
            if ce.get('type') == 'buff' and ce.get('spellName') == '幻造兵武' \
               and ce.get('targetUserId') == atk_uid:
                writing_val = int(achar.get_attr('写作', 0) or 0)
                if writing_val > 0:
                    roll, _ = roll_d100('')
                    if roll <= writing_val:
                        phantom_bonus = ce.get('auxVal', 0) or 0
                        if phantom_bonus:
                            if isinstance(phantom_bonus, str) or int(phantom_bonus) > 0:
                                dmg_dice = f"{dmg_dice}+{phantom_bonus}"
                                lines.append(f'  幻造兵武·写作检定: D100={roll}/{writing_val} 成功！伤害+{phantom_bonus}')
                    else:
                        lines.append(f'  幻造兵武·写作检定: D100={roll}/{writing_val} 失败')
                break

        # ── Reaction trigger hook (timing='4'): defender auto-triggers reaction spells ──
        def_spells = dchar.spells  # 直接复用缓存，避免每次攻击调用 load_spells
        if def_spells is None:
            def_spells = self.load_spells(def_uid)
        for ds in (def_spells or []):
            if has_timing(ds.get('时机', '2'), '4'):
                # Check _once_per_battle
                if ds.get('_once_per_battle'):
                    consumed = getattr(dchar, '_once_consumed', set())
                    if ds['name'] in consumed:
                        continue
                    consumed.add(ds['name'])
                    dchar._once_consumed = consumed
                # Check _once_per_round
                if ds.get('_once_per_round'):
                    consumed_round = getattr(dchar, '_round_consumed', set())
                    if ds['name'] in consumed_round:
                        continue
                    consumed_round.add(ds['name'])
                    dchar._round_consumed = consumed_round
                # Auto-trigger the reaction spell (self-target or at attacker)
                react_target = atk_uid if any(
                    str(e.get('客体', 0)).upper() in ('4', '5', '45', 'R', 'RP')
                    for e in ds.get('effects', []) if e.get('type') == 1
                ) else def_uid
                react_out = self._execute_spell(def_uid, react_target, ds)
                lines.append(f'  [{dname} 反应] {react_out.strip()}')
        eff_skill = self._apply_buff_skill_mod(atk_uid, skill_val)
        atk_buffs = self._get_active_buffs(atk_uid)
        # 行动力优势合并到攻击BP
        ap_atk_b, ap_def_p = self._calc_action_power_bp(atk_uid, def_uid)
        merged_bp = bp_suffix
        if ap_atk_b:
            merged_bp = (merged_bp or '') + ap_atk_b
        eff_bp = _calc_net_bp(atk_buffs, merged_bp, skill_name)
        # AUX 21/22: crit rate modifier
        crit_pct, crit_flat = self._get_buff_crit_adjustment(atk_uid)
        atk_result, bp_detail = roll_d100(eff_bp); atk_rank = success_rank(atk_result, eff_skill, crit_pct, crit_flat)
        # AUX 13/14: damage dice adv/dis from buffs (merge with command-line flags)
        buf_dmg_adv, buf_dmg_dis = self._get_buff_dmg_dice_adv_dis(atk_uid)
        dmg_adv = dmg_adv or buf_dmg_adv
        dmg_dis = dmg_dis or buf_dmg_dis
        # AUX code 4: merge bonus damage dice into dmg_dice
        bonus_dice = self._get_buff_dmg_dice_bonus(atk_uid)
        if bonus_dice:
            dmg_dice = f"{dmg_dice}+{bonus_dice}" if dmg_dice else bonus_dice
        bp_str = f", {bp_detail}" if bp_detail else ""
        lines.append(f"{aname} 的【{skill_name}】检定:"); lines.append(f"  D100={atk_result}/{eff_skill}{bp_str} {rank_text(atk_rank)}")
        if atk_rank <= 0:
            fn = " [大失败! 反击等级+1]" if atk_rank == -2 else ""
            lines.append(f"  {aname} 攻击失败！{fn}")
            # 即使攻击失败也允许防御方反应——大失败闪避/格挡可能使攻方意外命中，
            # 成功反击也可能在攻方失败时命中造成伤害。
            if not self._allow_failed_reaction:
                return (def_uid, atk_uid, lines)

        dodge_val = dchar.get_attr("闪避",25); bmn, bmv = dchar.get_best_melee()
        dodge_val = self._apply_buff_skill_mod(def_uid, dodge_val); bmv = self._apply_buff_skill_mod(def_uid, bmv)
        def_buffs = self._get_active_buffs(def_uid)
        _, ap_def_p2 = self._calc_action_power_bp(atk_uid, def_uid)
        def_bp = _calc_net_bp(def_buffs, ap_def_p2, skill_name)

        # ── 格挡可用性检查 ──
        block_name, block_val = self._get_block_skill(def_uid)
        block_val = self._apply_buff_skill_mod(def_uid, block_val)
        can_block = (dchar.get_attr('可格挡', 0) == 1
                     and block_name is not None and block_val > 0)
        block_hp = next((e for e in self._get_initiative() if e['userId'] == def_uid), {}).get('shield_block_hp', 0)
        # Check for block_redirect (summon takes damage instead of defender)
        _block_redirect = self._get_block_redirect_target(def_uid)
        can_block = can_block and (block_hp > 0 or _block_redirect is not None)

        # ── 击晕：禁止所有反应 → 攻击者直接命中 ──
        if not self._can_react(def_uid):
            lines.append(f'  {dname} 击晕中，无法反应！')
            react_choice = None
        else:
            # ── 三维反应选择 (dodge / counter / block) ──
            ai_dw = self._ai_react_dodge_w.get(def_uid, 50)
            ai_cw = self._ai_react_counter_w.get(def_uid, 50)
            ai_bw = self._ai_react_block_w.get(def_uid, 0) if can_block else 0
            total_w = ai_dw + ai_cw + ai_bw
            if total_w > 0:
                r = random.random() * total_w
                if r < ai_dw: react_choice = 'dodge'
                elif r < ai_dw + ai_cw: react_choice = 'counter'
                else: react_choice = 'block'
            else:
                react_choice = 'dodge' if dodge_val >= bmv else 'counter'

        if react_choice == 'dodge':
            rr, rd = roll_d100(def_bp); react_rank = success_rank(rr, dodge_val)
            rbs = f", {rd}" if rd else ""
            lines.append(f"{dname} 的反应【闪避={dodge_val}】:"); lines.append(f"  D100={rr}/{dodge_val}{rbs} {rank_text(react_rank)}")
            eff_atk = atk_rank
            if react_rank > 0: eff_atk -= react_rank
            if react_rank == -2: eff_atk += (2 if eff_atk == -1 else 1)
            eff_atk = max(-2, min(4, eff_atk))
            if eff_atk <= 0:
                lines.append(f"  {dname} 闪避成功！")
                return (def_uid, atk_uid, lines)
            winner_rank, winner_uid, loser_uid = eff_atk, atk_uid, def_uid
            loser_name, winner_name, winner_roll, is_counter = dname, aname, atk_result, False
        elif react_choice == 'counter':
            rr, rd = roll_d100(def_bp); react_rank = success_rank(rr, bmv)
            rbs = f", {rd}" if rd else ""
            lines.append(f"{dname} 的反应【反击:{bmn}={bmv}】:"); lines.append(f"  D100={rr}/{bmv}{rbs} {rank_text(react_rank)}")
            eff_atk, eff_react = atk_rank, react_rank
            if react_rank == -2: eff_atk += (2 if eff_atk == -1 else 1)
            if eff_atk == -2: eff_react = min(4, eff_react + 1)
            eff_atk, eff_react = max(-2, min(4, eff_atk)), max(-2, min(4, eff_react))
            if eff_react > eff_atk and eff_react > 0:
                if self._is_untargetable(atk_uid):
                    lines.append(f"  {dname} 反击成功！但 {aname} 不可选中，无法命中。")
                    winner_rank, winner_uid, loser_uid = eff_react, def_uid, atk_uid
                    loser_name, winner_name, winner_roll = aname, dname, rr
                    # Counter damage skipped; attacker's attack is negated
                    lines.append(f"  {aname} 的攻击被化解。")
                    return (def_uid, atk_uid, lines)
                winner_rank, winner_uid, loser_uid = eff_react, def_uid, atk_uid
                loser_name, winner_name = aname, dname; winner_roll = rr
                cdmg = dchar.get_str("伤害值") or "1d4"; cpen = dchar.get_attr("伤害贯穿",1)
                cdmg = _resolve_db(cdmg, _get_db_str(dchar))
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
                        else: adv, adetail = roll_dice_detailed(dmg_dice)
                    # AUX 1,3,19: apply damage multipliers before shield
                    adv = int(adv * self._get_buff_dmg_mult(atk_uid, def_uid) * self._get_buff_dmg_dice_mult(atk_uid))
                    adv += self._get_buff_dmg_flat(def_uid); adv = max(0, adv)  # AUX code 2: before shield
                    asr = self._absorb_damage_with_shield(def_uid, adv); aeff = asr[0]
                    if asr[1] > 0: lines.append(f"  护盾吸收(atk): {asr[1]}点")
                    achp = self._get_combat_hp(def_uid) or 10
                    achp = max(0, achp - aeff); self._set_combat_hp(def_uid, achp)
                    lines.append(f"  {aname} 造成伤害: {adetail} → {aeff}点")
                    lines.append(f"  {dname} HP: {achp}")

                    # ---- Defender counter-hits attacker ----
                    if not self._is_untargetable(atk_uid):
                        cdmg = dchar.get_str("伤害值") or "1d4"
                        cdmg = _resolve_db(cdmg, _get_db_str(dchar))
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
                            ddmg, ddetail = roll_dice_detailed(cdmg)
                        # AUX 1,3,19: apply damage multipliers before shield
                        ddmg = int(ddmg * self._get_buff_dmg_mult(def_uid, atk_uid) * self._get_buff_dmg_dice_mult(def_uid))
                        ddmg += self._get_buff_dmg_flat(atk_uid); ddmg = max(0, ddmg)  # AUX code 2: before shield
                        dsr = self._absorb_damage_with_shield(atk_uid, ddmg); deff = dsr[0]
                        if dsr[1] > 0: lines.append(f"  护盾吸收(def): {dsr[1]}点")
                        dchp = self._get_combat_hp(atk_uid) or 10
                        dchp = max(0, dchp - deff); self._set_combat_hp(atk_uid, dchp)
                        lines.append(f"  {dname} 反击伤害: {ddetail} → {deff}点")
                        lines.append(f"  {aname} HP: {dchp}")
                    else:
                        lines.append(f"  {aname} 不可选中，{dname} 的反击无法命中。")

                    lines.append(f"  双方同时命中! ({rank_text(eff_atk)} vs {rank_text(eff_react)})")
                    return (atk_uid, def_uid, lines)
                else:
                    lines.append("  无人得手！")
                    return (None, None, lines)
        else:  # react_choice == 'block'
            # ── 格挡反应 ──
            rr, rd = roll_d100(def_bp); react_rank = success_rank(rr, block_val)
            rbs = f", {rd}" if rd else ""
            lines.append(f"{dname} 的反应【格挡:{block_name}={block_val}】:")
            lines.append(f"  D100={rr}/{block_val}{rbs} {rank_text(react_rank)}")
            eff_atk = atk_rank
            if react_rank > 0: eff_atk -= react_rank
            if react_rank == -2: eff_atk += (2 if eff_atk == -1 else 1)
            eff_atk = max(-2, min(4, eff_atk))

            if eff_atk <= 0:
                # ═══ 格挡成功：盾牌承受伤害 ═══
                lines.append(f"  {dname} 格挡成功！盾牌承受攻击。")
                # 按攻击方原始等级计算伤害
                blk_mx = max_damage(dmg_dice); blk_dmg = 0; blk_detail = ""
                if atk_rank == 2:
                    r1, r2 = roll_dice(dmg_dice), roll_dice(dmg_dice); blk_dmg = max(r1, r2)
                    blk_detail = f"优势({dmg_dice}={r1},{r2})→{blk_dmg}"
                elif atk_rank == 3:
                    if pen: extra = roll_dice(dmg_dice); blk_dmg = blk_mx + extra; blk_detail = f"贯穿!满值{blk_mx}+{dmg_dice}={extra}={blk_dmg}"
                    else: blk_dmg = blk_mx; blk_detail = f"{dmg_dice}满值={blk_mx}"
                elif atk_rank == 4:
                    if atk_result == 1: blk_dmg = blk_mx * 2; blk_detail = f"大成功!满值{blk_mx}×2={blk_dmg}"
                    elif pen: blk_dmg = blk_mx * 2; blk_detail = f"贯穿!满值{blk_mx}×2={blk_dmg}"
                    else: extra = roll_dice(dmg_dice); blk_dmg = blk_mx + extra; blk_detail = f"{dmg_dice}满值{blk_mx}+{extra}={blk_dmg}"
                else:
                    if dmg_adv: r1, r2 = roll_dice(dmg_dice), roll_dice(dmg_dice); blk_dmg = max(r1, r2); blk_detail = f"优势({dmg_dice}={r1},{r2})→{blk_dmg}"
                    elif dmg_dis: r1, r2 = roll_dice(dmg_dice), roll_dice(dmg_dice); blk_dmg = min(r1, r2); blk_detail = f"劣势({dmg_dice}={r1},{r2})→{blk_dmg}"
                    else: blk_dmg, blk_detail = roll_dice_detailed(dmg_dice)
                blk_dmg = int(blk_dmg * self._get_buff_dmg_mult(atk_uid, def_uid) * self._get_buff_dmg_dice_mult(atk_uid))
                blk_dmg += self._get_buff_dmg_flat(def_uid); blk_dmg = max(0, blk_dmg)  # AUX code 2: before shield
                # 法术护盾吸收
                blk_sr = self._absorb_damage_with_shield(def_uid, blk_dmg); blk_eff = blk_sr[0]
                if blk_sr[1] > 0: lines.append(f"  护盾吸收: {blk_sr[1]}点")
                # ── block_redirect: 伤害由召唤物承担 ──
                _block_redirect = self._get_block_redirect_target(def_uid)
                if _block_redirect:
                    rt_entry = next((e for e in self._get_initiative() if e['userId'] == _block_redirect), None)
                    rt_name = rt_entry.get('displayName', '召唤物') if rt_entry else '召唤物'
                    rt_block_hp = rt_entry.get('shield_block_hp', 0) if rt_entry else 0
                    if blk_eff > 0 and rt_block_hp > 0:
                        absorbed = min(blk_eff, rt_block_hp)
                        blk_eff -= absorbed
                        rt_entry['shield_block_hp'] = rt_block_hp - absorbed
                        lines.append(f"  {rt_name} 盾牌吸收: {absorbed}点 (剩余护盾HP: {rt_block_hp - absorbed})")
                    cur_hp = self._get_combat_hp(_block_redirect) or 10
                    cur_hp = max(0, cur_hp - blk_eff); self._set_combat_hp(_block_redirect, cur_hp, source_dmg=blk_eff)
                    if blk_eff > 0:
                        lines.append(f"  {rt_name} 承担穿透伤害: {blk_eff}点, HP: {cur_hp}")
                    else:
                        lines.append(f"  {rt_name} 完全承担了伤害")
                    lines.append(f"  {dname} HP: {self._get_combat_hp(def_uid)}")
                    return (def_uid, atk_uid, lines)
                # ── 标准逻辑：shield_block HP 吸收（100%吸收率）──
                if blk_eff > 0 and block_hp > 0:
                    absorbed = min(blk_eff, block_hp)
                    blk_eff -= absorbed
                    entry = next((e for e in self._get_initiative() if e['userId'] == def_uid), None)
                    if entry: entry['shield_block_hp'] = block_hp - absorbed
                    lines.append(f"  盾牌吸收: {absorbed}点 (剩余护盾HP: {block_hp - absorbed})")
                # 溢出伤害扣HP（不触发致死骰）
                cur_hp = self._get_combat_hp(def_uid) or 10
                cur_hp = max(0, cur_hp - blk_eff); self._set_combat_hp(def_uid, cur_hp, source_dmg=blk_eff)
                if blk_eff > 0:
                    lines.append(f"  穿透伤害: {blk_detail} → {blk_eff}点")
                else:
                    lines.append(f"  伤害全部被吸收: {blk_detail}")
                lines.append(f"  {dname} HP: {cur_hp}")
                if cur_hp <= 0:
                    loser_entry = next((e for e in self._get_initiative() if e['userId'] == def_uid), None)
                    if loser_entry and not loser_entry.get('isSummon'):
                        self._remove_summons_of_owner(def_uid)
                return (def_uid, atk_uid, lines)
            else:
                # ═══ 格挡突破：盾牌被绕过，攻击直接作用角色 ═══
                lines.append(f"  {dname} 格挡失败！攻击突破盾牌。")
                winner_rank, winner_uid, loser_uid = eff_atk, atk_uid, def_uid
                loser_name, winner_name, winner_roll, is_counter = dname, aname, atk_result, False

        if react_choice is None:
            # ── 击晕/无反应：攻击者直接命中 ──
            winner_rank, winner_uid, loser_uid = atk_rank, atk_uid, def_uid
            loser_name, winner_name = dname, aname
            winner_roll = atk_result
            is_counter = False

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
            else: dmg_val, dmg_detail = roll_dice_detailed(dmg_dice)

        lines.append(f"  {winner_name} 胜利! {rank_text(winner_rank)}")
        # AUX 1,3,19: apply damage multipliers before shield
        dmg_val = int(dmg_val * self._get_buff_dmg_mult(winner_uid, loser_uid) * self._get_buff_dmg_dice_mult(winner_uid))
        dmg_val += self._get_buff_dmg_flat(loser_uid); dmg_val = max(0, dmg_val)  # AUX code 2: before shield
        # Check for damageModifier effects (e.g. 火鼠裘)
        for e in self._get_effects():
            if e.get('type') == 'damageModifier' and e.get('targetUserId') == loser_uid \
               and e.get('variant') == 'fire_rat_cloak' and e.get('remainingRounds', 0) > 0:
                def_char = self.get_char(loser_uid)
                cur_mp = def_char.get_attr('魔力', 0) if def_char else 0
                if cur_mp >= 1:
                    def_char.set_attr('魔力', cur_mp - 1)
                    if dmg_val <= 3:
                        dmg_val = 0
                    elif dmg_val < 7:
                        dmg_val = 3
                    else:
                        dmg_val = int(dmg_val * 0.6)
                    lines.append(f'  火鼠裘减免：最终伤害 {dmg_val}')
                break
        sr = self._absorb_damage_with_shield(loser_uid, dmg_val); eff_dmg = sr[0]
        if sr[1] > 0: lines.append(f"  护盾吸收: {sr[1]}点")
        cur_hp = self._get_combat_hp(loser_uid) or 10
        exp_dmg = avg_damage(dmg_dice)
        if leth and exp_dmg > 6:
            # Lethality: d(2×cur_hp) ≤ expected_damage → instant 120% max HP death
            leth_die = max(2, cur_hp * 2)
            # AUX 11/12: lethality die adv/dis
            leth_adv, leth_dis = self._get_buff_leth_adv_dis(winner_uid)
            if leth_adv:
                lr = max(random.randint(1, leth_die), random.randint(1, leth_die))
            elif leth_dis:
                lr = min(random.randint(1, leth_die), random.randint(1, leth_die))
            else:
                lr = random.randint(1, leth_die)
            if lr <= int(exp_dmg):
                loser_char = self.get_char(loser_uid)
                max_hp = loser_char.get_attr('体力上限', loser_char.get_attr('体力', 10))
                lethal_dmg = int(max_hp * 1.2)
                cur_hp = -lethal_dmg
                lines.append(f"  致死骰: 1d{leth_die}={lr} ≤ {int(exp_dmg)} 即死! 受到{max_hp}×1.2={lethal_dmg}伤害")
            else:
                cur_hp = cur_hp - eff_dmg  # raw (may be negative → overflow tracked in _set_combat_hp)
                lines.append(f"  致死骰: 1d{leth_die}={lr} > {int(exp_dmg)} 失败")
        else: cur_hp = cur_hp - eff_dmg  # raw (may be negative → overflow)
        lines.append(f"  伤害: {dmg_detail} → {eff_dmg}点"); self._set_combat_hp(loser_uid, cur_hp, source_dmg=eff_dmg)
        lines.append(f"  {loser_name} HP: {max(0, cur_hp)}")
        # Cleanup: if a non-summon character died, remove their summons immediately
        if cur_hp <= 0:
            loser_entry = next((e for e in self._get_initiative() if e['userId'] == loser_uid), None)
            if loser_entry and not loser_entry.get('isSummon'):
                self._remove_summons_of_owner(loser_uid)
        return (winner_uid, loser_uid, lines)

    # ═══════════════════════════════════════════════════════════════
    #  Spell/Skill Damage Reaction Support
    # ═══════════════════════════════════════════════════════════════

    def _handle_spell_damage_effect(self, caster_id, target_id, eff, spell):
        """Override: success rate -> reaction -> damage.
        Inserts dodge/counter reaction between success check and damage application."""
        out = ''
        dmg_dice = eff.get('伤害骰', '1d4')
        char = self.get_char(caster_id) if caster_id else None
        dmg_dice = _resolve_db(dmg_dice, _get_db_str(char))
        pen = eff.get('可贯穿性', 0)
        leth = eff.get('致死值', 0)
        sr = eff.get('成功率', 0)
        dur = eff.get('持续回合', 0)
        dot_dice = eff.get('每回合伤害骰', '')
        ls = float(eff.get('吸血比例', '0') or '0')

        # 幻造 (phantom creation): check for 幻造兵武 passive and roll 写作 skill
        if eff.get('幻造'):
            # Look for 幻造兵武 passive effect on caster
            caster_effects = self._get_effects()
            phantom_bonus = 0
            for ce in caster_effects:
                if ce.get('type') == 'buff' and ce.get('spellName') == '幻造兵武' \
                   and ce.get('targetUserId') == caster_id:
                    if char:
                        writing_val = char.get_attr('写作', 0) or 0
                        if writing_val > 0:
                            roll, _ = roll_d100('')
                            if roll <= writing_val:
                                phantom_bonus = ce.get('auxVal', 0) or 0
                                out += f'  幻造兵武·写作检定: D100={roll}/{writing_val} 成功！伤害+{phantom_bonus}\n'
                            else:
                                out += f'  幻造兵武·写作检定: D100={roll}/{writing_val} 失败\n'
                    break
            if phantom_bonus:
                try:
                    if int(phantom_bonus) > 0:
                        dmg_dice = f"{dmg_dice}+{phantom_bonus}"
                except (ValueError, TypeError):
                    dmg_dice = f"{dmg_dice}+{phantom_bonus}"

            # s6 即兴短句：月能充能 (2 + 写作加值)
            caster_entry = next((e for e in self._get_initiative() if e['userId'] == caster_id), None)
            spell_name_ph = spell.get('name', '') if spell else ''
            if caster_entry and '即兴短句' in str(spell_name_ph):
                charge_amt = 2
                if phantom_bonus:
                    try: charge_amt += int(phantom_bonus)
                    except: pass
                charged = self._charge_moon_energy(caster_entry.get('team', 'Y'), charge_amt)
                if charged > 0: out += f'  月能充能 (即兴短句): +{charged}\n'

        # Friend/foe behavior (same as base)
        friend_behavior = eff.get('友方行为', '')
        is_friendly = False
        if friend_behavior:
            il = self._get_initiative()
            ce = next((e for e in il if e['userId'] == caster_id), None)
            te = next((e for e in il if e['userId'] == target_id), None)
            is_friendly = ce and te and ce.get('team') == te.get('team')

        # Success rate check with COC7 rank
        # If no explicit 成功率, fall back to caster's best melee skill for enemy-targeting spells.
        # Self-targeting effects (客体=1) without 成功率 are auto-success (e.g. self-buffs).
        if sr <= 0:
            obj = eff.get('客体', 4)
            obj_str = str(obj).upper().strip()
            if obj == 1:
                # Self-targeting: auto-success (e.g. 灵牛 附魔术 self-buff)
                atk_rank = 4; check_roll = 1
            elif obj_str in ('R', 'RP'):
                # Random-targeting: treat same as enemy-targeting (needs success roll)
                char = self.get_char(caster_id)
                _, sr = char.get_best_melee()
                sr = max(1, sr)
            else:
                # Enemy-targeting: use caster's best melee skill as default success rate
                char = self.get_char(caster_id)
                _, sr = char.get_best_melee()
                sr = max(1, sr)  # Ensure at least 1 to force a roll
        if sr > 0:
            atk_rank = 4; check_roll = 1  # will be overwritten by roll below
            sr_bp_raw = eff.get('成功率奖惩骰', 0) or 0
            if isinstance(sr_bp_raw, int):
                if sr_bp_raw > 0:
                    sr_bp = 'b' + (str(sr_bp_raw) if sr_bp_raw > 1 else '')
                elif sr_bp_raw < 0:
                    sr_bp = 'p' + (str(abs(sr_bp_raw)) if abs(sr_bp_raw) > 1 else '')
                else:
                    sr_bp = ''
            else:
                sr_bp = str(sr_bp_raw) if sr_bp_raw else ''
            check_roll, bp_detail = roll_d100(sr_bp)
            bp_str = f', {bp_detail}' if bp_detail else ''
            # AUX 21/22: crit rate modifier for spells
            crit_pct, crit_flat = self._get_buff_crit_adjustment(caster_id)
            atk_rank = success_rank(check_roll, sr, crit_pct, crit_flat)
            if atk_rank <= 0:
                out += f'  成功率检定: D100={check_roll}/{sr}{bp_str} {rank_text(atk_rank)}！\n'
                # 法术失败也允许反应——大失败闪避/格挡可能使法术意外命中，
                # 成功反击也可能在法术失败时命中造成伤害。
                if not self._allow_failed_reaction:
                    return out
            else:
                out += f'  成功率检定: D100={check_roll}/{sr}{bp_str} {rank_text(atk_rank)}！\n'

        if is_friendly:
            # Friend-target damage — no reactions (same as base)
            fdmg_dice = eff.get('友方伤害骰', dmg_dice)
            dmg_val, dmg_detail = roll_dice_detailed(fdmg_dice)
            eff_dmg, absorbed, _ = self._absorb_damage_with_shield(target_id, dmg_val)
            cur_hp = self._get_combat_hp(target_id) or 10
            actual_lost = min(cur_hp, eff_dmg)
            cur_hp = cur_hp - actual_lost
            self._set_combat_hp(target_id, cur_hp, source_dmg=actual_lost)
            tname_f = self.get_char(target_id).name if target_id else '目标'
            out += f'  友方伤害: {dmg_detail} → {actual_lost}点 (HP:{cur_hp})\n'
            # Schedule delayed heal (same as base)
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
            # Enemy delayed heal (same as base)
            enemy_recovery = eff.get('敌方回复', '')
            if enemy_recovery and eff_dmg > 0:
                recovery_dice = eff.get('敌方回复骰', '2d6')
                heal_rounds_r = eff.get('敌方延迟回复回合', 3)
                if enemy_recovery == 'min_half':
                    heal_total_r = min(eff_dmg // 2, roll_dice(recovery_dice))
                else:
                    heal_total_r = roll_dice(recovery_dice)
                if heal_total_r > 0:
                    per_round_r = max(1, heal_total_r // heal_rounds_r)
                    effs_r = self._get_effects()
                    effs_r.append({
                        'type': 'delayedHeal', 'remainingRounds': heal_rounds_r,
                        'healPerRound': per_round_r, 'totalHeal': heal_total_r, 'healed': 0,
                        'sourceUserId': caster_id, 'targetUserId': target_id,
                        'spellName': spell['name'], 'spellIndex': spell['index'],
                        'persistent': 0,
                    })
                    self._set_effects(effs_r)
                    tname_h = self.get_char(target_id).name if target_id else '目标'
                    out += f'  敌方延迟回复: {tname_h} 共{heal_total_r}HP, 每回合{per_round_r}HP ({heal_rounds_r}回合)\n'
        else:
            # ── Enemy-target damage: insert reaction if applicable ──
            can_dodge = eff.get('可闪避性', 1)
            can_counter = eff.get('可反击性', 1)
            can_block_spell = eff.get('可格挡性', 1)
            if (can_dodge or can_counter or can_block_spell) and target_id:
                # Collect prefix lines (success check) for PvP reaction display
                spell_prefix = [out.rstrip('\n')] if out and out.strip() else []
                dodged, countered, react_lines = self._trigger_spell_reaction(
                    caster_id, target_id, eff, spell, dmg_dice,
                    can_dodge, can_counter, can_block_spell, atk_rank=atk_rank, atk_roll=check_roll, prefix_lines=spell_prefix)
                if react_lines:
                    out += '\n'.join(react_lines) + '\n'

                if dodged:
                    return out  # Spell completely dodged/blocked, no damage
                if countered:
                    return out  # Defender countered cleanly, spell damage negated

            # If the spell success check failed, it NEVER deals damage.
            # A fumbled reaction does not make a failed spell hit.
            if atk_rank <= 0:
                out += '  法术未命中！\n'
                return out

            # Apply spell damage with rank-based damage
            out += self._apply_spell_damage(caster_id, target_id, dmg_dice, pen, leth,
                                            lifesteal_ratio=ls, dot_dice=dot_dice, dur=dur,
                                            spell_name=spell['name'], spell_index=spell['index'],
                                            atk_rank=atk_rank, atk_roll=check_roll, spell=spell)
        return out

    def _trigger_spell_reaction(self, caster_id, target_id, eff, spell, dmg_dice,
                                 can_dodge, can_counter, can_block=False, atk_rank=4, atk_roll=1, prefix_lines=None):
        """Resolve defender reaction to a spell damage effect.
        Base (FullBattleEngine): auto-resolve for AI-vs-AI using trained weights.
        PvP (PvPFullBattleEngine): override to raise ReactionNeeded.
        Returns (dodged: bool, countered: bool, lines: list)."""
        # ── 击晕禁止所有反应 ──
        if not self._can_react(target_id):
            lines = (prefix_lines or []) + [f'  {self.get_char(target_id).name} 击晕中，无法反应！']
            return (False, False, lines)
        dchar = self.get_char(target_id)
        dodge_val = dchar.get_attr("闪避", 25)
        bmn, bmv = dchar.get_best_melee()
        dodge_val = self._apply_buff_skill_mod(target_id, dodge_val)
        bmv = self._apply_buff_skill_mod(target_id, bmv)

        # ── 格挡可用性 ──
        block_name, block_val = self._get_block_skill(target_id)
        block_val = self._apply_buff_skill_mod(target_id, block_val)
        can_block = can_block and (dchar.get_attr('可格挡', 0) == 1
                     and block_name is not None and block_val > 0)
        block_hp = next((e for e in self._get_initiative() if e['userId'] == target_id), {}).get('shield_block_hp', 0)
        # Check for block_redirect (summon takes damage instead of defender)
        _block_redirect = self._get_block_redirect_target(target_id)
        can_block = can_block and (block_hp > 0 or _block_redirect is not None)

        # ── 玄武/三合一等: 护盾格挡无视"不可反应"标记 ──
        def_entry = next((e for e in self._get_initiative() if e['userId'] == target_id), None)
        ignore_unreact = def_entry.get('ignore_unreactable_block', 0) if def_entry else 0
        if ignore_unreact and not can_block and (dchar.get_attr('可格挡', 0) == 1
            and block_name is not None and block_val > 0
            and (block_hp > 0 or _block_redirect is not None)):
            can_block = True  # 即使法术不可反应，仍可格挡

        if not can_dodge and not can_counter and not can_block:
            # If spell already failed and no reaction is possible, spell simply misses
            if atk_rank <= 0:
                return (True, False, [])
            return (False, False, [])

        # AI three-way weighted choice
        ai_dw = self._ai_react_dodge_w.get(target_id, 50)
        ai_cw = self._ai_react_counter_w.get(target_id, 50)
        ai_bw = self._ai_react_block_w.get(target_id, 0) if can_block else 0
        if not can_dodge:
            ai_dw = 0
        if not can_counter:
            ai_cw = 0
        total_w = ai_dw + ai_cw + ai_bw
        if total_w <= 0:
            # AI chooses not to react. If spell failed, it simply misses.
            if atk_rank <= 0:
                return (True, False, [])
            return (False, False, [])

        r = random.random() * total_w
        if r < ai_dw:
            return self._resolve_spell_dodge(caster_id, target_id, eff, spell,
                                              dmg_dice, dodge_val, atk_rank)
        elif r < ai_dw + ai_cw:
            return self._resolve_spell_counter(caster_id, target_id, eff, spell,
                                                dmg_dice, bmn, bmv, atk_rank)
        else:
            return self._resolve_spell_block(caster_id, target_id, eff, spell,
                                              dmg_dice, block_name, block_val, block_hp, atk_rank)

    def _resolve_spell_dodge(self, caster_id, target_id, eff, spell, dmg_dice,
                              dodge_val, atk_rank=4):
        """Roll defender dodge vs spell attack rank. Returns (dodged, countered, lines)."""
        achar = self.get_char(caster_id)
        dchar = self.get_char(target_id)
        lines = []

        def_buffs = self._get_active_buffs(target_id)
        def_bp = _calc_net_bp(def_buffs, "", "")
        # Spell effect imposes penalty on reactions (e.g. 挥动镰刀)
        if _eff_get(eff, 'auxType') == '技能惩罚骰' and _eff_get(eff, 'auxVal') == 'p':
            if def_bp == '': def_bp = 'p'
            elif def_bp == 'b': def_bp = ''
            elif def_bp == 'b2': def_bp = 'b'
            elif def_bp == 'b3': def_bp = 'b2'
        rr, rd = roll_d100(def_bp)
        react_rank = success_rank(rr, dodge_val)
        rbs = f", {rd}" if rd else ""

        lines.append(f"{dchar.name} 的反应【闪避={dodge_val}】(法术):")
        lines.append(f"  D100={rr}/{dodge_val}{rbs} {rank_text(react_rank)}")

        eff_atk = atk_rank
        if react_rank > 0:
            eff_atk -= react_rank
        if react_rank == -2:
            eff_atk += (2 if eff_atk == -1 else 1)
        eff_atk = max(-2, min(4, eff_atk))

        if eff_atk <= 0:
            lines.append(f"  {dchar.name} 闪避了法术！")
            return (True, False, lines)
        lines.append(f"  {achar.name} 的法术突破闪避！(有效等级:{rank_text(eff_atk)})")
        return (False, False, lines)

    def _resolve_spell_counter(self, caster_id, target_id, eff, spell, dmg_dice,
                                counter_skill, counter_val, atk_rank=4):
        """Roll defender counter vs spell attack rank. Returns (dodged, countered, lines).
        countered=True means defender won cleanly (spell damage negated)."""
        achar = self.get_char(caster_id)
        dchar = self.get_char(target_id)
        lines = []

        def_buffs = self._get_active_buffs(target_id)
        def_bp = _calc_net_bp(def_buffs, "", "")
        # Spell effect imposes penalty on reactions (e.g. 挥动镰刀)
        if _eff_get(eff, 'auxType') == '技能惩罚骰' and _eff_get(eff, 'auxVal') == 'p':
            if def_bp == '': def_bp = 'p'
            elif def_bp == 'b': def_bp = ''
            elif def_bp == 'b2': def_bp = 'b'
            elif def_bp == 'b3': def_bp = 'b2'
        rr, rd = roll_d100(def_bp)
        react_rank = success_rank(rr, counter_val)
        rbs = f", {rd}" if rd else ""

        lines.append(f"{dchar.name} 的反应【反击:{counter_skill}={counter_val}】(法术):")
        lines.append(f"  D100={rr}/{counter_val}{rbs} {rank_text(react_rank)}")

        eff_atk, eff_react = atk_rank, react_rank
        if react_rank == -2:
            eff_atk += (2 if eff_atk == -1 else 1)
        if eff_atk == -2:
            eff_react = min(4, eff_react + 1)
        eff_atk, eff_react = max(-2, min(4, eff_atk)), max(-2, min(4, eff_react))

        # Helper to apply counter damage based on success rank
        def _apply_counter_dmg(rank, roll, cdmg, cpen, target_uid, label):
            mx = max_damage(cdmg)
            dmg_val = 0; dmg_detail = ""
            if rank == 2:
                r1, r2 = roll_dice(cdmg), roll_dice(cdmg)
                dmg_val = max(r1, r2)
                dmg_detail = f"优势({cdmg}={r1},{r2})→{dmg_val}"
            elif rank == 3:
                if cpen:
                    extra = roll_dice(cdmg); dmg_val = mx + extra
                    dmg_detail = f"贯穿!满值{mx}+{cdmg}={extra}={dmg_val}"
                else:
                    dmg_val = mx; dmg_detail = f"{cdmg}满值={mx}"
            elif rank == 4:
                if roll == 1:
                    dmg_val = mx * 2; dmg_detail = f"大成功!满值{mx}×2={dmg_val}"
                elif cpen:
                    dmg_val = mx * 2; dmg_detail = f"贯穿!满值{mx}×2={dmg_val}"
                else:
                    extra = roll_dice(cdmg); dmg_val = mx + extra
                    dmg_detail = f"{cdmg}满值{mx}+{extra}={dmg_val}"
            else:
                dmg_val, dmg_detail = roll_dice_detailed(cdmg)

            sr_abs = self._absorb_damage_with_shield(target_uid, dmg_val)
            eff_dmg = sr_abs[0]
            if sr_abs[1] > 0: lines.append(f"  护盾吸收({label}): {sr_abs[1]}点")
            cur_hp = self._get_combat_hp(target_uid) or 10
            cur_hp = max(0, cur_hp - eff_dmg)
            self._set_combat_hp(target_uid, cur_hp, source_dmg=eff_dmg)
            lines.append(f"  {label}伤害: {dmg_detail} → {eff_dmg}点")
            lines.append(f"  {self.get_char(target_uid).name} HP: {cur_hp}")

        if eff_react > eff_atk and eff_react > 0:
            # Defender wins cleanly — counter damage to caster, spell negated
            if self._is_untargetable(caster_id):
                lines.append(f"  {dchar.name} 反击成功！但 {achar.name} 不可选中，无法命中。法术被击破。")
                return (False, True, lines)
            cdmg = dchar.get_str("伤害值") or "1d4"
            cdmg = _resolve_db(cdmg, dchar.get_attr('体格', 0))
            cpen = dchar.get_attr("伤害贯穿", 1)
            cleth = dchar.get_attr("致死骰", 1) or 0
            _apply_counter_dmg(react_rank, rr, cdmg, cpen, caster_id, "反击")
            lines.append(f"  {dchar.name} 反击成功！法术被击破。")
            return (False, True, lines)

        elif eff_atk > eff_react and eff_atk > 0:
            # Attacker wins — spell connects
            lines.append(f"  {achar.name} 的法术压制了反击！(有效等级:{rank_text(eff_atk)})")
            return (False, False, lines)

        elif eff_react == eff_atk and eff_react > 0:
            # Mutual hit — both take damage
            if not self._is_untargetable(caster_id):
                cdmg = dchar.get_str("伤害值") or "1d4"
                cdmg = _resolve_db(cdmg, _get_db_str(dchar))
                cpen = dchar.get_attr("伤害贯穿", 1)
                _apply_counter_dmg(react_rank, rr, cdmg, cpen, caster_id, "反击")
            else:
                lines.append(f"  {achar.name} 不可选中，{dchar.name} 的反击无法命中。")
            lines.append(f"  互命中！{dchar.name} 的反击与 {achar.name} 的法术同时命中。")
            # countered=False so spell damage still applies to defender
            return (False, False, lines)
        else:
            lines.append("  无人得手！")
            return (True, False, lines)

    def _resolve_spell_block(self, caster_id, target_id, eff, spell, dmg_dice,
                              block_skill, block_val, block_hp, atk_rank=4):
        """Roll defender block vs spell attack rank. Returns (dodged, countered, lines).
        dodged=True means spell fully blocked by shield."""
        achar = self.get_char(caster_id)
        dchar = self.get_char(target_id)
        lines = []

        def_buffs = self._get_active_buffs(target_id)
        def_bp = _calc_net_bp(def_buffs, "", "")
        rr, rd = roll_d100(def_bp)
        react_rank = success_rank(rr, block_val)
        rbs = f", {rd}" if rd else ""

        lines.append(f"{dchar.name} 的反应【格挡:{block_skill}={block_val}】(法术):")
        lines.append(f"  D100={rr}/{block_val}{rbs} {rank_text(react_rank)}")

        eff_atk = atk_rank
        if react_rank > 0:
            eff_atk -= react_rank
        if react_rank == -2:
            eff_atk += (2 if eff_atk == -1 else 1)
        eff_atk = max(-2, min(4, eff_atk))

        if eff_atk <= 0:
            # 格挡成功：法术伤害由盾牌承受
            lines.append(f"  {dchar.name} 用盾牌完全格挡了法术！")
            # 计算法术伤害、护盾吸收、盾牌HP消耗
            dmg_val = roll_dice(dmg_dice)
            blk_sr = self._absorb_damage_with_shield(target_id, dmg_val)
            blk_eff = blk_sr[0]
            if blk_sr[1] > 0: lines.append(f"  护盾吸收: {blk_sr[1]}点")
            # ── block_redirect: 伤害由召唤物承担 ──
            _block_redirect = self._get_block_redirect_target(target_id)
            if _block_redirect:
                rt_entry = next((e for e in self._get_initiative() if e['userId'] == _block_redirect), None)
                rt_name = rt_entry.get('displayName', '召唤物') if rt_entry else '召唤物'
                rt_block_hp = rt_entry.get('shield_block_hp', 0) if rt_entry else 0
                if blk_eff > 0 and rt_block_hp > 0:
                    absorbed = min(blk_eff, rt_block_hp)
                    blk_eff -= absorbed
                    rt_entry['shield_block_hp'] = rt_block_hp - absorbed
                    lines.append(f"  {rt_name} 盾牌吸收: {absorbed}点 (剩余护盾HP: {rt_block_hp - absorbed})")
                if blk_eff > 0:
                    cur_hp = self._get_combat_hp(_block_redirect) or 10
                    cur_hp = max(0, cur_hp - blk_eff)
                    self._set_combat_hp(_block_redirect, cur_hp, source_dmg=blk_eff)
                    lines.append(f"  {rt_name} 承担穿透伤害: {blk_eff}点, HP: {cur_hp}")
                else:
                    lines.append(f"  {rt_name} 完全承担了伤害")
                return (True, False, lines)
            # ── 标准逻辑：shield_block HP 吸收 ──
            if blk_eff > 0 and block_hp > 0:
                absorbed = min(blk_eff, block_hp)
                blk_eff -= absorbed
                entry = next((e for e in self._get_initiative() if e['userId'] == target_id), None)
                if entry: entry['shield_block_hp'] = block_hp - absorbed
                lines.append(f"  盾牌吸收: {absorbed}点 (剩余护盾HP: {block_hp - absorbed})")
            # 溢出伤害扣HP（不触发致死）
            if blk_eff > 0:
                cur_hp = self._get_combat_hp(target_id) or 10
                cur_hp = max(0, cur_hp - blk_eff)
                self._set_combat_hp(target_id, cur_hp, source_dmg=blk_eff)
                lines.append(f"  穿透伤害: {blk_eff}点, HP: {cur_hp}")
            return (True, False, lines)
        else:
            # 格挡失败：法术突破盾牌，正常结算
            lines.append(f"  {achar.name} 的法术突破格挡！(有效等级:{rank_text(eff_atk)})")
            return (False, False, lines)

    def _use_skill(self, uid, skill_num, args):
        """Handle .sN command — spell execution with timing checks."""
        # ── 状态检查：沉默/击晕禁止主动技能 ──
        if not self._can_cast_active_skill(uid):
            statuses = [b.get('auxType','') for b in self._get_active_statuses(uid)]
            return f"无法释放技能（{'/'.join(statuses)}中）"
        char = self.get_char(uid); spells = char.spells or self.load_spells(uid)
        spell = next((s for s in spells if s['index'] == skill_num), None)
        if not spell: return f"未找到技能{skill_num}"
        timing = spell.get('时机', '2')
        is_passive = has_timing(timing, '1')
        can_main = has_timing(timing, '2')
        can_extra = has_timing(timing, '3')
        if not can_main and not can_extra and not is_passive:
            return f"【{spell['name']}】不能在主动作阶段使用"
        target = self._smart_target(uid, spell)
        return self._execute_spell(uid, target, spell)

    def _additional_action(self, uid, args):
        """Handle .a m / .a sN / .a eat / .a give commands (附加动作)."""
        parts = args.split()
        if not parts: return "用法: .a m <坐标> 或 .a s<序号> 或 .a eat [目标] 或 .a give <目标>"
        char = self.get_char(uid)
        if parts[0].lower() == 'm' and len(parts) >= 2:
            # ── 状态检查：禁锢/击晕禁止移动 ──
            if not self._can_move(uid):
                statuses = [b.get('auxType','') for b in self._get_active_statuses(uid)]
                return f"无法移动（{'/'.join(statuses)}中）"
            coord = parts[1].upper()
            return f"{char.name} 移动至 {coord}"
        elif re.match(r'^s\d+$', parts[0], re.IGNORECASE):
            # ── 状态检查：沉默/击晕禁止附加技能 ──
            if not self._can_cast_active_skill(uid):
                statuses = [b.get('auxType','') for b in self._get_active_statuses(uid)]
                return f"无法释放技能（{'/'.join(statuses)}中）"
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
        # ── 状态检查：缴械/击晕禁止基本攻击 ──
        if not self._can_basic_attack(uid):
            statuses = [b.get('auxType','') for b in self._get_active_statuses(uid)]
            return f"无法普通攻击（{'/'.join(statuses)}中）"
        char = self.get_char(uid); il = self._get_initiative()
        me = next((e for e in il if e["userId"]==uid), None)
        if not me: return "No position"
        enemies = [e for e in il if e["team"]!=me.get("team","Y") and (self._get_combat_hp(e["userId"])or 0)>0 and not self._is_untargetable(e['userId'])]
        if not enemies:
            all_teams = [(e.get('name', e.get('userId', '?')), e.get('team', '?')) for e in il]
            _engine_debug_log(f"_basic_attack: {char.name}({uid}) team={me.get('team','?')} "
                  f"NO ENEMIES! all teams: {all_teams}")
            return "No enemies"
        t = enemies[0]; tid = t["userId"]
        bn, bv = char.get_best_melee()
        dd = self._get_damage_dice(uid, bn); p = char.get_attr("伤害贯穿",1); l = char.get_attr("致死骰",1) or 0
        _, _, lines = self._coc7_attack(uid, tid, bn, bv, dd, p, l)
        return "\n".join(lines)

    def _create_summon(self, caster_id, template_name, summon_group=None, shared_roll=None, shared_rank=None):
        tmpls = _SUMMON_TEMPLATES
        if tmpls is None:
            from characters_data import SUMMON_TEMPLATES as tmpls
        if tmpls is None:
            import sys
            print(f'[ERROR] _SUMMON_TEMPLATES is None, cannot create summon "{template_name}" for {caster_id}', file=sys.stderr, flush=True)
            return None
        tmpl = tmpls.get(template_name)
        if not tmpl:
            import sys
            print(f'[ERROR] Summon template "{template_name}" not found (available: {list(tmpls.keys())}) for caster {caster_id}', file=sys.stderr, flush=True)
            return None
        # Fix #9 / C7: Handle _meta templates (e.g. 随机召唤物 with options pool)
        if tmpl.get('_meta'):
            meta_unique = tmpl.get('unique_per_caster', False)  # Save before tmpl reassignment
            options = list(tmpl.get('options', []))
            if meta_unique:
                existing = self._summoned_once.get(caster_id, set())
                options = [n for n in options if n not in existing]
            if not options:
                return None
            template_name = random.choice(options)
            tmpl = tmpls.get(template_name)  # tmpl is now the resolved template (e.g. '朱雀') — no unique_per_caster!
            if not tmpl: return None
            # Track as summoned (unique_per_caster: once per battle — survives death)
            if meta_unique:  # Use the saved flag from meta template
                self._summoned_once.setdefault(caster_id, set()).add(template_name)
        # Generate summon ID: {caster_serial}_sum_{n} (e.g. Y12_sum_1)
        caster_info = self.get_char(caster_id)
        caster_serial = (caster_info.serial if caster_info and caster_info.serial else caster_id)
        n = self._summon_counters.get(caster_id, 0) + 1
        self._summon_counters[caster_id] = n
        sid = f"{caster_serial}_sum_{n}"
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
                    "skill_type": sk_raw.get("skill_type", "attack"),
                    "zone_heal_hp": sk_raw.get("zone_heal_hp", ""),
                    "zone_radius": sk_raw.get("zone_radius", 0),
                    "zone_duration": sk_raw.get("zone_duration", 0),
                    "mp_cost": sk_raw.get("mp_cost", 0),
                    "cooldown_rounds": sk_raw.get("cooldown_rounds", 0),
                })
            else:
                parts = str(sk_raw).split(); nv = parts[0].split(":")
                parsed.append({"name":nv[0],"val":int(nv[1]) if len(nv)>1 else 50,"dice":parts[1] if len(parts)>1 else "1d4",
                               "hits":1,"on_whiff_aoe_dmg":"","on_whiff_mp_cost":0,
                               "skill_type":"attack","zone_heal_hp":"","zone_radius":0,"zone_duration":0,
                               "mp_cost":0,"cooldown_rounds":0})
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
        summon_display_name = f"{caster_name} 的 {template_name}"
        # Summon COC DEX initiative roll (use shared roll for same-template batch)
        if shared_roll is not None:
            sum_init_roll = shared_roll
            sum_init_rank = shared_rank if shared_rank is not None else success_rank(sum_init_roll, dex)
        else:
            sum_init_roll, _ = roll_d100("")
            sum_init_rank = success_rank(sum_init_roll, dex)
        # ── Summon group consolidation: check for existing same-type group ──
        owner_base = self._resolve_uid(caster_id)
        existing_rep = self._find_summon_group_by(owner_base, template_name)
        if existing_rep is not None:
            # ── Add to existing group: no new initiative entry, shared action pool ──
            grp = self._summon_groups[existing_rep]
            grp['members'].append(sid)
            self._summon_members[sid] = {
                'userId': sid, 'name': template_name,
                'display_name': summon_display_name,
                'team': team, 'coord': coord,
                'skills': parsed, 'dex': dex,
                'flying': tmpl.get('flying', False),
                'ignited': False, 'ignite_dmg_dice': '', 'ignite_tick_dmg': 0,
                'summonGroup': summon_group,
            }
            self._init_combat_hp(sid, hp)
            summon_char2 = self.get_char(sid)
            summon_char2.name = summon_display_name
            summon_char2.set_attr('MOV', tmpl.get('MOV', 6))
            if parsed and parsed[0].get('dice'):
                summon_char2.set_str('伤害值', parsed[0]['dice'])
            summon_char2.set_attr('额外行动数', tmpl.get('额外行动数', tmpl.get('回合行动数', 0)))
            # Copy template-based skill attrs to character (needed for get_best_melee/reactions)
            _tmpl_skill_keys = ['斗殴', '闪避', '格挡', '盾击', '鞭', '爪击']
            for _k in _tmpl_skill_keys:
                if _k in tmpl:
                    summon_char2.set_attr(_k, tmpl[_k])
            # Also set skill attrs from parsed skills array (handles dict-format skills like 龙虎)
            for _ps in parsed:
                _sn = _ps.get('name', '')
                if _sn and _ps.get('skill_type', 'attack') == 'attack':
                    summon_char2.set_attr(_sn, _ps['val'])
            # Copy spells from template to character (for .s1/.s2 commands)
            summon_char2.spells = list(tmpl.get('spells', []))
            # Clone of owner: inherit caster's spells and skills
            if tmpl.get('clone_of_owner'):
                caster = self.get_char(caster_id)
                if caster and caster.spells:
                    summon_char2.spells = list(caster.spells)
                for _k in ['斗殴', '剑', '闪避', '力量', '敏捷', '体质', '体型', '意志', '幸运']:
                    _v = caster.get_attr(_k) if caster else None
                    if _v is not None:
                        summon_char2.set_attr(_k, _v)
                if caster:
                    summon_char2.set_str('伤害值', caster.get_str('伤害值') or '1d4')
                    summon_char2.set_str('db', caster.get_str('db') or '0')
            # Group members share the representative's map position.
            # Do NOT place them on separate cells (would fill the map).
            rep_entry = next((e for e in self._get_initiative() if e.get('baseUserId', e['userId']) == existing_rep), None)
            if rep_entry and rep_entry.get('coord'):
                self._summon_members[sid]['coord'] = rep_entry['coord']
            if tmpl.get('size_2x2'):
                mp2 = parse_coord(self._summon_members[sid]['coord'])
                if mp2:
                    for dr2x in range(2):
                        for dc2x in range(2):
                            if dr2x == 0 and dc2x == 0: continue
                            nc2x, nr2x = mp2[0]+dc2x, mp2[1]+dr2x
                            if 0 <= nc2x < 26 and 0 <= nr2x < 99:
                                self._get_map()['occupants'][format_coord(nc2x, nr2x)] = sid
                    self._set_map(self._get_map())
            mg2 = tmpl.get('merge_group', '')
            if mg2:
                if not hasattr(self, '_summoned_templates'):
                    self._summoned_templates = {}
                st2 = self._summoned_templates
                if caster_id not in st2: st2[caster_id] = {}
                if mg2 not in st2[caster_id]: st2[caster_id][mg2] = set()
                st2[caster_id][mg2].add(template_name)
            return sid
        # ── No existing group: create normal initiative entry ──
        init_list.append({"userId":sid,"name":template_name,"displayName":summon_display_name,"team":team,"dex":dex,"initRoll":sum_init_roll,"initRank":sum_init_rank,
            "coord":coord,"isSummon":True,"ownerId":self._resolve_uid(caster_id),"skills":parsed,
            "skill_name":parsed[0]["name"],"skill_val":parsed[0]["val"],"dmg_dice":parsed[0]["dice"],
            "zone_skills": [sk for sk in parsed if sk.get("skill_type") == "zone_heal"],
            "zone_cooldown": 0, "summon_mp": tmpl.get("MP", 0),
            "react_dodge_w":tmpl.get("react_dodge",50),"react_counter_w":tmpl.get("react_counter",50),
            "react_block_w":tmpl.get("react_block",0),
            "shield_block":tmpl.get("shield_block",0),"shield_block_hp":tmpl.get("shield_block",0),
            "shield_block_rate":tmpl.get("shield_block_rate",0.70),
            "ignore_unreactable_block":tmpl.get("ignore_unreactable_block",0),
            "flying":tmpl.get("flying",False),
            "max_simultaneous":tmpl.get("max_simultaneous"), "max_total_spawned":tmpl.get("max_total_spawned"),
            "summonGroup": summon_group})
        state_s = self._get_state()
        old_idx = state_s.get('activeIndex', -1) if state_s else -1
        tracked_uid = init_list[old_idx]['userId'] if 0 <= old_idx < len(init_list) else None
        init_list.sort(key=lambda e: (-e["initRank"], -e["dex"], e["initRoll"])); self._set_initiative(init_list)
        if tracked_uid is not None:
            for i, e in enumerate(init_list):
                if e['userId'] == tracked_uid:
                    if i != old_idx and state_s:
                        state_s['activeIndex'] = i; self._set_state(state_s)
                    break
        self._init_combat_hp(sid, hp)
        # Set summon character's display name to avoid internal ID leaks
        summon_char = self.get_char(sid)
        summon_char.name = summon_display_name
        # 设置动态行动数所需的属性（召唤物也使用统一公式）
        summon_char.set_attr('MOV', tmpl.get('MOV', 6))
        # 虚假之月：月能无上限
        if template_name == '虚假之月':
            summon_char.set_attr('魔力上限', 99999)
        if parsed and parsed[0].get('dice'):
            summon_char.set_str('伤害值', parsed[0]['dice'])
        summon_char.set_attr('额外行动数', tmpl.get('额外行动数', tmpl.get('回合行动数', 0)))
        # Copy template-based skill attrs to character (needed for get_best_melee/reactions)
        _tmpl_skill_keys_s = ['斗殴', '闪避', '格挡', '盾击', '鞭', '爪击']
        for _k in _tmpl_skill_keys_s:
            if _k in tmpl:
                summon_char.set_attr(_k, tmpl[_k])
        # Also set skill attrs from parsed skills array (handles dict-format skills like 龙虎)
        for _ps in parsed:
            _sn = _ps.get('name', '')
            if _sn and _ps.get('skill_type', 'attack') == 'attack':
                summon_char.set_attr(_sn, _ps['val'])
        # Copy spells from template to character (for .s1/.s2 commands)
        summon_char.spells = list(tmpl.get('spells', []))
        # Clone of owner: inherit caster's spells and skills
        if tmpl.get('clone_of_owner'):
            caster = self.get_char(caster_id)
            if caster and caster.spells:
                summon_char.spells = list(caster.spells)
            for _k in ['斗殴', '剑', '闪避', '力量', '敏捷', '体质', '体型', '意志', '幸运']:
                _v = caster.get_attr(_k) if caster else None
                if _v is not None:
                    summon_char.set_attr(_k, _v)
            if caster:
                summon_char.set_str('伤害值', caster.get_str('伤害值') or '1d4')
                summon_char.set_str('db', caster.get_str('db') or '0')
        # 将单条目扩展为 MAX_DYNAMIC_ACTIONS 个预掷条目
        self._ensure_dynamic_slots(sid)
        # ── _ensure_dynamic_slots 内部重新排序了先攻列表，需重新定位召唤人 ──
        if tracked_uid is not None:
            init_list_re = self._get_initiative()
            for i, e in enumerate(init_list_re):
                if e['userId'] == tracked_uid:
                    if state_s and i != state_s.get('activeIndex', -1):
                        state_s['activeIndex'] = i
                        self._set_state(state_s)
                    break
        # ── 永久状态（如藤蔓的禁锢） ──
        perm_statuses = tmpl.get('permanent_statuses', [])
        if perm_statuses:
            effects = self._get_effects()
            for ps_name in perm_statuses:
                effects.append({
                    'type': 'buff', 'auxType': ps_name, 'auxVal': '0', 'auxCode': 0,
                    'remainingRounds': 999, 'sourceUserId': caster_id, 'targetUserId': sid,
                    'spellName': f'永久{ps_name}', 'spellIndex': 0,
                    'persistent': 1, 'stackable': 0,
                })
            self._set_effects(effects)
        acts = self._get_actions()
        acts[sid] = {"主动": self._calc_main_actions(sid), "附加": 3}
        self._set_actions(acts)
        # ── Create summon group for first summon of this type ──
        self._summon_groups[sid] = {
            'members': [sid], 'template_name': template_name,
            'ownerId': owner_base,
        }
        self._summon_members[sid] = {
            'userId': sid, 'name': template_name,
            'display_name': summon_display_name,
            'team': team, 'coord': coord,
            'skills': parsed, 'dex': dex,
            'flying': tmpl.get('flying', False),
            'ignited': False, 'ignite_dmg_dice': '', 'ignite_tick_dmg': 0,
            'summonGroup': summon_group,
        }
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
        # ── 标记召唤人当前槽位，防止新召唤物行动后误触发召唤人行动数重置 ──
        if tracked_uid is not None:
            tracked_base = self._resolve_uid(tracked_uid)
            if not hasattr(self, '_slot_actions_given'):
                self._slot_actions_given = {}
            current_entry = next((e for e in self._get_initiative() if e['userId'] == tracked_uid), None)
            if current_entry:
                self._slot_actions_given[tracked_base] = current_entry.get('actionIdx', 0)
        return sid

    def _summon_attack(self, summon_id):
        il = self._get_initiative(); entry = next((e for e in il if e["userId"]==summon_id), None)
        if not entry: return ["未找到召唤物"]
        sname = entry.get("displayName", entry.get("name", summon_id))
        lines = []

        # Zone skill check — summon can cast a zone instead of attacking
        zone_cooldown = entry.get("zone_cooldown", 0)
        if zone_cooldown > 0:
            entry["zone_cooldown"] = zone_cooldown - 1
        if entry.get("zone_cooldown", 0) <= 0:
            zone_skills = entry.get("zone_skills", [])
            if zone_skills:
                zsk = zone_skills[0]
                mp_cost = zsk.get("mp_cost", 0)
                summon_mp = entry.get("summon_mp", 0)
                if summon_mp >= mp_cost:
                    entry["summon_mp"] = summon_mp - mp_cost
                    entry["zone_cooldown"] = zsk.get("cooldown_rounds", 0) + zsk.get("zone_duration", 3)
                    center = entry.get("coord", "A1")
                    effects = self._get_effects()
                    effects.append({
                        'type': 'zone', 'center': center,
                        'radius': zsk.get("zone_radius", 8),
                        'remainingRounds': zsk.get("zone_duration", 3),
                        'tickDmg': '', 'tickHealHp': zsk.get("zone_heal_hp", "2d6"),
                        'tickHealMp': '', 'centerFollows': 1, 'filter': 3,
                        'attributeDebuff': '', 'sourceUserId': summon_id,
                        'spellName': zsk.get("name", "治愈领域"), 'spellIndex': -1,
                        'persistent': 0, 'stackable': 0,
                    })
                    self._set_effects(effects)
                    zname = zsk.get("name", "治愈领域")
                    lines.append(f"{zname} → 创建治愈领域（半径{zsk.get('zone_radius',8)}格，持续{zsk.get('zone_duration',3)}回合，剩余MP:{entry['summon_mp']}）")
                    return lines

        enemies = [e for e in il if e["team"]!=entry.get("team","Y") and (self._get_combat_hp(e["userId"])or 0)>0 and not self._is_untargetable(e['userId'])]
        if not enemies: return ["无可用目标"]
        tid = enemies[0]["userId"]
        skills = entry.get("skills",[])
        if skills:
            best_score = -1; best = skills[0]
            for sk in skills:
                if sk.get("skill_type") != "attack": continue
                score = (sk["val"]/100.0)*avg_damage(sk["dice"])
                if score > best_score: best_score = score; best = sk
            sv = best["val"]; dmg_dice = best["dice"]; sk_name = best["name"]
            hits = best.get("hits", 1)
            on_whiff_aoe = best.get("on_whiff_aoe_dmg", "")
            on_whiff_mp = best.get("on_whiff_mp_cost", 0)
        else:
            sv = entry.get("skill_val",50); dmg_dice = entry.get("dmg_dice","1d4")
            hits = 1
            on_whiff_aoe = ""; on_whiff_mp = 0
        # Ignited summons use their ignite damage dice
        if entry.get("ignited"):
            dmg_dice = entry.get('ignite_dmg_dice', '2d4')
        # Resolve db using summon owner's 体格
        owner_id = entry.get('ownerId')
        if owner_id:
            owner_char = self.get_char(owner_id)
            if owner_char:
                dmg_dice = _resolve_db(dmg_dice, _get_db_str(owner_char))
        sk_label = sk_name or "攻击"
        # Battle spirit penalty dice
        pens = entry.get('battle_spirit_penalty_dice', 0)
        bp_suffix = f"p{pens}" if pens > 1 else ("p" if pens == 1 else "")

        # ── Delegate to _coc7_attack for dice / damage / shield / lethality / reaction ──
        # This ensures summons respect the same combat rules as characters (including ReactionNeeded in PvP).
        pen = 1; leth = entry.get("lethality", 0)
        _, _, atk_lines = self._coc7_attack(summon_id, tid, sk_label, sv, dmg_dice, pen, leth, bp_suffix=bp_suffix)
        lines.extend(atk_lines)

        # ── Multi-hit: additional hits beyond the first ──
        if hits > 1:
            total_dmg = 0
            for l in atk_lines:
                m = re.search(r'造成\s+(\d+)\s+点伤害', l)
                if m: total_dmg = int(m.group(1)); break
            for hi in range(1, hits):
                if random.randint(1, 100) > sv: continue
                dmg = roll_dice(dmg_dice)
                eff_dmg, shield_abs, _ = self._absorb_damage_with_shield(tid, dmg)
                eff_dmg = self._apply_shield_block(tid, eff_dmg)
                total_dmg += eff_dmg
                lines.append(f"    第{hi+1}击: {dmg_dice}={dmg} → {eff_dmg}点")
                if shield_abs > 0:
                    lines.append(f"  护盾吸收: {shield_abs}点")
            cur_hp = self._get_combat_hp(tid) or 10
            new_hp = max(0, cur_hp - total_dmg)
            self._set_combat_hp(tid, new_hp, source_dmg=total_dmg)
            lines.append(f"  造成 {total_dmg} 点伤害")
            tname = enemies[0].get("name", tid) if enemies else '目标'
            lines.append(f"  {tname} HP: {new_hp}")

        # On-whiff AoE (post-attack, only fires if target survived with 0 damage dealt)
        if on_whiff_aoe:
            tgt_hp = self._get_combat_hp(tid) or 10
            # Check if damage was dealt by scanning lines for "造成"
            dmg_dealt = any("造成" in l for l in atk_lines)
            if not dmg_dealt:
                owner_id = entry.get("ownerId")
                if owner_id:
                    oc = self.get_char(owner_id); omp = oc.get_attr("魔力",0) or 0
                    if omp >= on_whiff_mp:
                        oc.set_attr("魔力", omp - on_whiff_mp)
                        for enemy in enemies:
                            aoe_dmg = roll_dice(on_whiff_aoe) // 2
                            ehp = self._get_combat_hp(enemy["userId"]) or 10
                            self._set_combat_hp(enemy["userId"], max(0, ehp - aoe_dmg))
                        lines.append(f"  全部未命中！对区域内所有敌人造成 {on_whiff_aoe}//2 点范围伤害")
        return lines

    def _apply_shield_block(self, target_id, dmg):
        il = self._get_initiative(); entry = next((e for e in il if e["userId"]==target_id), None)
        if not entry: return dmg
        block_hp = entry.get("shield_block_hp",0)
        if block_hp <= 0: return dmg
        rate = entry.get("shield_block_rate", 0.70)
        if random.random() < rate:
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
        """Check melee vs flying. Returns (can_attack:bool, penalty_die:bool)."""
        atk_flying = self._is_flying(atk_uid)
        def_flying = self._is_flying(def_uid)
        if not atk_flying and def_flying:
            if FLYING_MELEE_RULE >= 2:
                return False, False
            elif FLYING_MELEE_RULE == 1:
                return True, True  # can attack with penalty die
        return True, False

    def _apply_zone_effects(self):
        effects = self._get_effects(); il = self._get_initiative()
        need_save = False
        # Collect per-entity results for display
        tick_results = []  # [{spell_name, target_name, type, dice, roll, amount, hp, hp_max, alive}]
        # Track (coord, spellName) pairs processed this round for non-stackable zones
        processed_zone_positions = set()
        for eff in effects:
            if eff.get("type")!="zone" or eff.get("radius",0)<=0: continue
            cp = parse_coord(eff.get("center","A1"))
            if not cp: continue
            stackable = eff.get("stackable", 0)
            spell_name = eff.get("spellName", "")
            # Determine zone caster's team for healing filter (Fix #8)
            zone_team = "Y"
            src_uid = eff.get("sourceUserId")
            if src_uid:
                se = next((e for e in il if e["userId"] == src_uid), None)
                if se: zone_team = se.get("team", "Y")
            if eff.get("tickDmg"):
                src_uid = eff.get('sourceUserId')
                base_tick_dice = eff["tickDmg"]
                for entry in il:
                    ec = entry.get("coord",""); ep = parse_coord(ec) if ec else None
                    if not ep: continue
                    if max(abs(ep[0]-cp[0]),abs(ep[1]-cp[1])) > eff["radius"]: continue
                    # 隔离：不受领域效果影响
                    if self._has_status(entry['userId'], '隔离'):
                        continue
                    # ally_safe: skip allies if zone is marked ally-safe
                    if eff.get('ally_safe') and entry.get('team') == zone_team:
                        continue
                    # Dedup: skip if this (coord, spellName) already processed by another non-stackable zone
                    pos_key = (ec, spell_name)
                    if not stackable and pos_key in processed_zone_positions:
                        continue
                    processed_zone_positions.add(pos_key)
                    # Per-entity damage roll (AUX code 4: merge bonus damage dice)
                    tick_dice = base_tick_dice
                    if src_uid:
                        bonus_dice = self._get_buff_dmg_dice_bonus(src_uid)
                        if bonus_dice:
                            tick_dice = f"{tick_dice}+{bonus_dice}"
                    dmg = roll_dice(tick_dice)
                    if dmg > 0:
                        # AUX 1,3,19: apply damage multipliers from zone source
                        if src_uid:
                            dmg = int(dmg * self._get_buff_dmg_mult(src_uid, entry["userId"]) * self._get_buff_dmg_dice_mult(src_uid))
                        dmg += self._get_buff_dmg_flat(entry["userId"]); dmg = max(0, dmg)  # AUX code 2: before shield
                        ed, _, _ = self._absorb_damage_with_shield(entry["userId"], dmg)
                        hp = self._get_combat_hp(entry["userId"]) or 10
                        self._set_combat_hp(entry["userId"], hp - ed, source_dmg=ed)
                        tname = entry.get("displayName", entry.get("name", entry["userId"]))
                        hp_max = (self.get_char(entry["userId"]).get_attr('体力上限', hp) if self.get_char(entry["userId"]) else hp)
                        tick_results.append({
                            'spell_name': spell_name, 'target_name': tname,
                            'type': 'damage', 'dice': tick_dice, 'roll': dmg,
                            'amount': ed, 'hp': hp - ed, 'hp_max': hp_max,
                            'alive': (hp - ed) > 0,
                        })
            if eff.get("tickHealHp"):
                for entry in il:
                    if entry.get("team") != zone_team: continue
                    if entry.get("isSummon") and entry.get("ignited"): continue  # Ignited summons cannot be healed
                    ec = entry.get("coord",""); ep = parse_coord(ec) if ec else None
                    if not ep: continue
                    if max(abs(ep[0]-cp[0]),abs(ep[1]-cp[1])) > eff["radius"]: continue
                    # 隔离：不受领域效果影响
                    if self._has_status(entry['userId'], '隔离'):
                        continue
                    # Dedup: skip if this (coord, spellName) already processed
                    pos_key = (ec, spell_name)
                    if not stackable and pos_key in processed_zone_positions:
                        continue
                    processed_zone_positions.add(pos_key)
                    heal = roll_dice(eff["tickHealHp"])
                    if heal > 0:
                        heal = int(heal * self._get_buff_heal_pct(entry["userId"], 'hp'))
                        heal += self._get_buff_heal_flat(entry["userId"], 'hp')  # AUX code 8
                        hp = self._get_combat_hp(entry["userId"]) or 10
                        ch = self.get_char(entry["userId"])
                        mhp = ch.get_attr("体力上限", hp) if ch else hp
                        self._set_combat_hp(entry["userId"], min(hp+heal, mhp))
                        tname = entry.get("displayName", entry.get("name", entry["userId"]))
                        tick_results.append({
                            'spell_name': spell_name, 'target_name': tname,
                            'type': 'heal_hp', 'dice': eff["tickHealHp"], 'roll': heal,
                            'amount': heal, 'hp': min(hp+heal, mhp), 'hp_max': mhp,
                            'alive': True,
                        })
            if eff.get("tickHealMp"):
                for entry in il:
                    if entry.get("team") != zone_team: continue
                    ec = entry.get("coord",""); ep = parse_coord(ec) if ec else None
                    if not ep: continue
                    if max(abs(ep[0]-cp[0]),abs(ep[1]-cp[1])) > eff["radius"]: continue
                    # 隔离：不受领域效果影响
                    if self._has_status(entry['userId'], '隔离'):
                        continue
                    # Dedup: skip if this (coord, spellName) already processed
                    pos_key = (ec, spell_name)
                    if not stackable and pos_key in processed_zone_positions:
                        continue
                    processed_zone_positions.add(pos_key)
                    heal = roll_dice(eff["tickHealMp"])
                    if heal > 0:
                        heal = int(heal * self._get_buff_heal_pct(entry["userId"], 'mp'))
                        heal += self._get_buff_heal_flat(entry["userId"], 'mp')  # AUX code 6
                        tname = entry.get("displayName", entry.get("name", entry["userId"]))
                        tick_results.append({
                            'spell_name': spell_name, 'target_name': tname,
                            'type': 'heal_mp', 'dice': eff["tickHealMp"], 'roll': heal,
                            'amount': heal, 'hp': 0, 'hp_max': 0,
                            'alive': True,
                        })
                        ch = self.get_char(entry["userId"])
                        cm = ch.get_attr("魔力",0) or 0; mx = ch.get_attr("魔力上限",cm) or cm
                        ch.set_attr("魔力", min(cm+heal, mx))
            # Attribute debuff per round (属性削减, e.g. 行动力-5) — temporary on entry
            ad = eff.get("attributeDebuff", "")
            if ad:
                match = re.match(r'^(.+?)([+-]\d+)$', ad)
                if match:
                    attr_name = match.group(1)
                    delta = int(match.group(2))
                    for entry in il:
                        ec = entry.get("coord", ""); ep = parse_coord(ec) if ec else None
                        if not ep: continue
                        if max(abs(ep[0]-cp[0]), abs(ep[1]-cp[1])) > eff["radius"]: continue
                        if attr_name in ('行动力', '移动力'):
                            key = '_zone_penalty_' + attr_name
                            entry[key] = max(entry.get(key, 0), abs(delta))
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
        self._zone_tick_results = tick_results

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
        tmpls = _SUMMON_TEMPLATES
        if tmpls is None:
            from characters_data import SUMMON_TEMPLATES as tmpls
        if tmpls is None:
            return
        il = self._get_initiative()
        summons = [e for e in il if e.get("isSummon")]
        # Group summons by merge_group
        groups = {}
        for s in summons:
            tmpl = tmpls.get(s.get("name", ""), {})
            mg = tmpl.get("merge_group", "")
            if not mg: continue
            if mg not in groups:
                groups[mg] = []
            groups[mg].append(s)
        for mg, members in groups.items():
            alive = [m for m in members if (self._get_combat_hp(m["userId"]) or 0) > 0]
            if len(alive) != 1: continue
            # Require all templates in this merge_group to have been summoned
            all_templates = {name for name, t in tmpls.items()
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
            member_tmpl = tmpls.get(members[0].get("name", ""), {})
            result_name = member_tmpl.get("merge_result", "")
            result_tmpl = tmpls.get(result_name, {})
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
            merged["react_block_w"] = result_tmpl.get("react_block", 0)
            merged["shield_block"] = result_tmpl.get("shield_block", 0)
            merged["shield_block_hp"] = result_tmpl.get("shield_block", 0)
            merged["shield_block_rate"] = result_tmpl.get("shield_block_rate", 0.70)
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
            merged["zone_skills"] = [sk for sk in parsed if sk.get("skill_type") == "zone_heal"]
            merged["zone_cooldown"] = 0
            merged["summon_mp"] = result_tmpl.get("MP", 0)
            # Update action count
            # Set character attrs needed for main-action formula
            merged_char = self.get_char(merged["userId"])
            merged_char.set_attr('MOV', result_tmpl.get('MOV', 6))
            merged_char.set_attr('额外行动数', result_tmpl.get('额外行动数', result_tmpl.get('回合行动数', result_tmpl.get('行动次数', 0))))
            # Update action count using formula
            acts = self._get_actions()
            merged["baseUserId"] = merged["userId"]
            merged["actionIdx"] = 0
            self._ensure_dynamic_slots(merged["userId"])
            acts[merged["userId"]] = {"主动": self._calc_main_actions(merged["userId"]), "附加": 1}
            self._set_actions(acts)
            self._set_initiative(il)

class FastBattleEngine(FullBattleEngine):
    """Fast training battle mode — thin wrapper around FullBattleEngine.

    Uses FullBattleEngine(fast_mode=True) for consistent COC7 mechanics.
    Only overrides methods where training-specific optimizations differ."""
    def __init__(self):
        super().__init__(fast_mode=True)
        self._fast_store = True  # Skip json.dumps for training
        # Note: max_rounds=20, _ai_react_*_w dicts, _summoned_once, _summon_counters
        # are all initialized by FullBattleEngine.__init__(fast_mode=True)

    def setup_battle(self, team_a, team_b, map_size="10x10", season_status=None):
        w, h = map(int, map_size.split("x"))
        self.group_id = f"fast_{random.randint(10000,99999)}"
        self._set_map({"width":w, "height":h, "entryRow":math.ceil(h/2), "obstacles":{}, "occupants":{}})
        all_chars = team_a + team_b; init_list = []; map_data = self._get_map()
        for i, uid in enumerate(team_a):
            char = self.get_char(uid)
            row = min(h-1, math.ceil(h/2) + i - len(team_a)//2)
            coord = format_coord(1, row)
            dex_val = char.get_attr("敏捷",50)
            init_roll, _ = roll_d100("")
            init_rank = success_rank(init_roll, dex_val)
            init_list.append({"userId":uid, "baseUserId":uid, "name":char.name, "actionIdx":0,
                              "team":"Y", "dex":dex_val, "initRoll":init_roll, "initRank":init_rank, "coord":coord})
            map_data["occupants"][coord] = uid
        for i, uid in enumerate(team_b):
            char = self.get_char(uid)
            row = min(h-1, math.ceil(h/2) + i - len(team_b)//2)
            coord = format_coord(w-2, row)
            dex_val = char.get_attr("敏捷",50)
            init_roll, _ = roll_d100("")
            init_rank = success_rank(init_roll, dex_val)
            init_list.append({"userId":uid, "baseUserId":uid, "name":char.name, "actionIdx":0,
                              "team":"X", "dex":dex_val, "initRoll":init_roll, "initRank":init_rank, "coord":coord})
            map_data["occupants"][coord] = uid
        self._set_map(map_data); init_list.sort(key=lambda e: (-e["initRank"], -e["dex"], e["initRoll"]))
        self._set_initiative(init_list)
        self._team_roster = [(uid, 'Y') for uid in team_a] + [(uid, 'X') for uid in team_b]
        for uid in all_chars:
            char = self.get_char(uid); self._init_combat_hp(uid, char.get_attr("体力",10))
        self._set_actions({uid: {"主动": self._calc_main_actions(uid), "附加": 3} for uid in all_chars})
        for uid in all_chars:
            self.get_char(uid).set_attr('状态', season_status_roll(season_status, uid))
        # Auto-cast passives (with isolation guard)
        for uid in all_chars:
            if self._has_status(uid, '隔离'):
                continue
            spells = self.get_char(uid).spells or self.load_spells(uid)
            for s in spells:
                if has_timing(s.get("时机","2"), "1"):
                    target = self._smart_target(uid, s)
                    self._execute_spell(uid, target, s)
        # Recalculate actions after passives
        acts = self._get_actions()
        for uid in all_chars:
            acts[uid] = {"主动": self._calc_main_actions(uid), "附加": 3}
        self._set_actions(acts)
        self._set_state({"phase":"active", "round":1, "activeIndex":0})
        self._skip_to_valid_active()

    # ---- Summon system (same as FullBattleEngine) ----
    def _create_summon(self, caster_id, template_name, summon_group=None, shared_roll=None, shared_rank=None):
        tmpls = _SUMMON_TEMPLATES
        if tmpls is None:
            from characters_data import SUMMON_TEMPLATES as tmpls
        if tmpls is None:
            import sys
            print(f'[ERROR] _SUMMON_TEMPLATES is None, cannot create summon "{template_name}" for {caster_id}', file=sys.stderr, flush=True)
            return None
        tmpl = tmpls.get(template_name)
        if not tmpl:
            import sys
            print(f'[ERROR] Summon template "{template_name}" not found (available: {list(tmpls.keys())}) for caster {caster_id}', file=sys.stderr, flush=True)
            return None
        # Fix #9 / C7: Handle _meta templates (e.g. 随机召唤物 with options pool)
        if tmpl.get('_meta'):
            meta_unique = tmpl.get('unique_per_caster', False)  # Save before tmpl reassignment
            options = list(tmpl.get('options', []))
            if meta_unique:
                existing = self._summoned_once.get(caster_id, set())
                options = [n for n in options if n not in existing]
            if not options:
                return None
            template_name = random.choice(options)
            tmpl = tmpls.get(template_name)  # tmpl is now the resolved template (e.g. '朱雀') — no unique_per_caster!
            if not tmpl: return None
            # Track as summoned (unique_per_caster: once per battle — survives death)
            if meta_unique:  # Use the saved flag from meta template
                self._summoned_once.setdefault(caster_id, set()).add(template_name)
        # Generate summon ID: {caster_serial}_sum_{n} (e.g. Y12_sum_1)
        caster_info = self.get_char(caster_id)
        caster_serial = (caster_info.serial if caster_info and caster_info.serial else caster_id)
        n = self._summon_counters.get(caster_id, 0) + 1
        self._summon_counters[caster_id] = n
        sid = f"{caster_serial}_sum_{n}"
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
                    "skill_type": sk_raw.get("skill_type", "attack"),
                    "zone_heal_hp": sk_raw.get("zone_heal_hp", ""),
                    "zone_radius": sk_raw.get("zone_radius", 0),
                    "zone_duration": sk_raw.get("zone_duration", 0),
                    "mp_cost": sk_raw.get("mp_cost", 0),
                    "cooldown_rounds": sk_raw.get("cooldown_rounds", 0),
                })
            else:
                parts = str(sk_raw).split(); nv = parts[0].split(":")
                parsed.append({"name":nv[0],"val":int(nv[1]) if len(nv)>1 else 50,"dice":parts[1] if len(parts)>1 else "1d4",
                               "hits":1,"on_whiff_aoe_dmg":"","on_whiff_mp_cost":0,
                               "skill_type":"attack","zone_heal_hp":"","zone_radius":0,"zone_duration":0,
                               "mp_cost":0,"cooldown_rounds":0})
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
        summon_display_name = f"{caster_name} 的 {template_name}"
        # Summon COC DEX initiative roll (use shared roll for same-template batch)
        if shared_roll is not None:
            sum_init_roll = shared_roll
            sum_init_rank = shared_rank if shared_rank is not None else success_rank(sum_init_roll, dex)
        else:
            sum_init_roll, _ = roll_d100("")
            sum_init_rank = success_rank(sum_init_roll, dex)
        # ── Summon group consolidation: check for existing same-type group ──
        owner_base = self._resolve_uid(caster_id)
        existing_rep = self._find_summon_group_by(owner_base, template_name)
        if existing_rep is not None:
            # ── Add to existing group: no new initiative entry, shared action pool ──
            grp = self._summon_groups[existing_rep]
            grp['members'].append(sid)
            self._summon_members[sid] = {
                'userId': sid, 'name': template_name,
                'display_name': summon_display_name,
                'team': team, 'coord': coord,
                'skills': parsed, 'dex': dex,
                'flying': tmpl.get('flying', False),
                'ignited': False, 'ignite_dmg_dice': '', 'ignite_tick_dmg': 0,
                'summonGroup': summon_group,
            }
            self._init_combat_hp(sid, hp)
            summon_char2 = self.get_char(sid)
            summon_char2.name = summon_display_name
            summon_char2.set_attr('MOV', tmpl.get('MOV', 6))
            if parsed and parsed[0].get('dice'):
                summon_char2.set_str('伤害值', parsed[0]['dice'])
            summon_char2.set_attr('额外行动数', tmpl.get('额外行动数', tmpl.get('回合行动数', 0)))
            # Copy template-based skill attrs to character (needed for get_best_melee/reactions)
            _tmpl_skill_keys = ['斗殴', '闪避', '格挡', '盾击', '鞭', '爪击']
            for _k in _tmpl_skill_keys:
                if _k in tmpl:
                    summon_char2.set_attr(_k, tmpl[_k])
            # Also set skill attrs from parsed skills array (handles dict-format skills like 龙虎)
            for _ps in parsed:
                _sn = _ps.get('name', '')
                if _sn and _ps.get('skill_type', 'attack') == 'attack':
                    summon_char2.set_attr(_sn, _ps['val'])
            # Copy spells from template to character (for .s1/.s2 commands)
            summon_char2.spells = list(tmpl.get('spells', []))
            # Clone of owner: inherit caster's spells and skills
            if tmpl.get('clone_of_owner'):
                caster = self.get_char(caster_id)
                if caster and caster.spells:
                    summon_char2.spells = list(caster.spells)
                for _k in ['斗殴', '剑', '闪避', '力量', '敏捷', '体质', '体型', '意志', '幸运']:
                    _v = caster.get_attr(_k) if caster else None
                    if _v is not None:
                        summon_char2.set_attr(_k, _v)
                if caster:
                    summon_char2.set_str('伤害值', caster.get_str('伤害值') or '1d4')
                    summon_char2.set_str('db', caster.get_str('db') or '0')
            # Group members share the representative's map position.
            # Do NOT place them on separate cells (would fill the map).
            rep_entry = next((e for e in self._get_initiative() if e.get('baseUserId', e['userId']) == existing_rep), None)
            if rep_entry and rep_entry.get('coord'):
                self._summon_members[sid]['coord'] = rep_entry['coord']
            if tmpl.get('size_2x2'):
                mp2 = parse_coord(self._summon_members[sid]['coord'])
                if mp2:
                    for dr2x in range(2):
                        for dc2x in range(2):
                            if dr2x == 0 and dc2x == 0: continue
                            nc2x, nr2x = mp2[0]+dc2x, mp2[1]+dr2x
                            if 0 <= nc2x < 26 and 0 <= nr2x < 99:
                                self._get_map()['occupants'][format_coord(nc2x, nr2x)] = sid
                    self._set_map(self._get_map())
            mg2 = tmpl.get('merge_group', '')
            if mg2:
                if not hasattr(self, '_summoned_templates'):
                    self._summoned_templates = {}
                st2 = self._summoned_templates
                if caster_id not in st2: st2[caster_id] = {}
                if mg2 not in st2[caster_id]: st2[caster_id][mg2] = set()
                st2[caster_id][mg2].add(template_name)
            return sid
        # ── No existing group: create normal initiative entry ──
        init_list.append({"userId":sid,"name":template_name,"displayName":summon_display_name,"team":team,"dex":dex,"initRoll":sum_init_roll,"initRank":sum_init_rank,
            "coord":coord,"isSummon":True,"ownerId":self._resolve_uid(caster_id),"skills":parsed,
            "skill_name":parsed[0]["name"],"skill_val":parsed[0]["val"],"dmg_dice":parsed[0]["dice"],
            "zone_skills": [sk for sk in parsed if sk.get("skill_type") == "zone_heal"],
            "zone_cooldown": 0, "summon_mp": tmpl.get("MP", 0),
            "react_dodge_w":tmpl.get("react_dodge",50),"react_counter_w":tmpl.get("react_counter",50),
            "react_block_w":tmpl.get("react_block",0),
            "shield_block":tmpl.get("shield_block",0),"shield_block_hp":tmpl.get("shield_block",0),
            "shield_block_rate":tmpl.get("shield_block_rate",0.70),
            "ignore_unreactable_block":tmpl.get("ignore_unreactable_block",0),
            "flying":tmpl.get("flying",False),
            "max_simultaneous":tmpl.get("max_simultaneous"), "max_total_spawned":tmpl.get("max_total_spawned"),
            "summonGroup": summon_group})
        state_s = self._get_state()
        old_idx = state_s.get('activeIndex', -1) if state_s else -1
        tracked_uid = init_list[old_idx]['userId'] if 0 <= old_idx < len(init_list) else None
        init_list.sort(key=lambda e: (-e["initRank"], -e["dex"], e["initRoll"])); self._set_initiative(init_list)
        if tracked_uid is not None:
            for i, e in enumerate(init_list):
                if e['userId'] == tracked_uid:
                    if i != old_idx and state_s:
                        state_s['activeIndex'] = i; self._set_state(state_s)
                    break
        self._init_combat_hp(sid, hp)
        # Set summon character's display name to avoid internal ID leaks
        summon_char_fast = self.get_char(sid)
        summon_char_fast.name = summon_display_name
        # 设置动态行动数所需的属性（召唤物也使用统一公式）
        summon_char_fast.set_attr('MOV', tmpl.get('MOV', 6))
        if parsed and parsed[0].get('dice'):
            summon_char_fast.set_str('伤害值', parsed[0]['dice'])
        summon_char_fast.set_attr('额外行动数', tmpl.get('额外行动数', tmpl.get('回合行动数', 0)))
        # Copy spells from template to character (for .s1/.s2 commands)
        summon_char_fast.spells = list(tmpl.get('spells', []))
        # Ensure initiative entry has correct fields
        self._ensure_dynamic_slots(sid)
        # ── _ensure_dynamic_slots 内部重新排序了先攻列表，需重新定位召唤人 ──
        if tracked_uid is not None:
            init_list_re = self._get_initiative()
            for i, e in enumerate(init_list_re):
                if e['userId'] == tracked_uid:
                    if state_s and i != state_s.get('activeIndex', -1):
                        state_s['activeIndex'] = i
                        self._set_state(state_s)
                    break
        # ── 永久状态（如藤蔓的禁锢） ──
        perm_statuses_fast = tmpl.get('permanent_statuses', [])
        if perm_statuses_fast:
            effects_fast = self._get_effects()
            for ps_name in perm_statuses_fast:
                effects_fast.append({
                    'type': 'buff', 'auxType': ps_name, 'auxVal': '0', 'auxCode': 0,
                    'remainingRounds': 999, 'sourceUserId': caster_id, 'targetUserId': sid,
                    'spellName': f'永久{ps_name}', 'spellIndex': 0,
                    'persistent': 1, 'stackable': 0,
                })
            self._set_effects(effects_fast)
        acts = self._get_actions()
        acts[sid] = {"主动": self._calc_main_actions(sid), "附加": 3}
        self._set_actions(acts)
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
        # ── 标记召唤人当前槽位，防止新召唤物行动后误触发召唤人行动数重置 ──
        if tracked_uid is not None:
            tracked_base = self._resolve_uid(tracked_uid)
            if not hasattr(self, '_slot_actions_given'):
                self._slot_actions_given = {}
            current_entry = next((e for e in self._get_initiative() if e['userId'] == tracked_uid), None)
            if current_entry:
                self._slot_actions_given[tracked_base] = current_entry.get('actionIdx', 0)
        return sid

    # _summon_attack and _apply_shield_block are inherited from FullBattleEngine
    # (which uses proper success_rank and rank-based damage)

    # ---- Zone per-round effects ----
    def _apply_zone_effects(self):
        effects = self._get_effects(); il = self._get_initiative()
        need_save = False
        # Collect per-entity results for display
        tick_results = []  # [{spell_name, target_name, type, dice, roll, amount, hp, hp_max, alive}]
        # Track (coord, spellName) pairs processed this round for non-stackable zones
        processed_zone_positions = set()
        for eff in effects:
            if eff.get("type")!="zone" or eff.get("radius",0)<=0: continue
            cp = parse_coord(eff.get("center","A1"))
            if not cp: continue
            stackable = eff.get("stackable", 0)
            spell_name = eff.get("spellName", "")
            # Determine zone caster's team for healing filter (Fix #8)
            zone_team = "Y"
            src_uid = eff.get("sourceUserId")
            if src_uid:
                se = next((e for e in il if e["userId"] == src_uid), None)
                if se: zone_team = se.get("team", "Y")
            if eff.get("tickDmg"):
                src_uid = eff.get('sourceUserId')
                base_tick_dice = eff["tickDmg"]
                for entry in il:
                    ec = entry.get("coord",""); ep = parse_coord(ec) if ec else None
                    if not ep: continue
                    if max(abs(ep[0]-cp[0]),abs(ep[1]-cp[1])) > eff["radius"]: continue
                    # 隔离：不受领域效果影响
                    if self._has_status(entry['userId'], '隔离'):
                        continue
                    # ally_safe: skip allies if zone is marked ally-safe
                    if eff.get('ally_safe') and entry.get('team') == zone_team:
                        continue
                    # Dedup: skip if this (coord, spellName) already processed by another non-stackable zone
                    pos_key = (ec, spell_name)
                    if not stackable and pos_key in processed_zone_positions:
                        continue
                    processed_zone_positions.add(pos_key)
                    # Per-entity damage roll (AUX code 4: merge bonus damage dice)
                    tick_dice = base_tick_dice
                    if src_uid:
                        bonus_dice = self._get_buff_dmg_dice_bonus(src_uid)
                        if bonus_dice:
                            tick_dice = f"{tick_dice}+{bonus_dice}"
                    dmg = roll_dice(tick_dice)
                    if dmg > 0:
                        # AUX 1,3,19: apply damage multipliers from zone source
                        if src_uid:
                            dmg = int(dmg * self._get_buff_dmg_mult(src_uid, entry["userId"]) * self._get_buff_dmg_dice_mult(src_uid))
                        dmg += self._get_buff_dmg_flat(entry["userId"]); dmg = max(0, dmg)  # AUX code 2: before shield
                        ed, _, _ = self._absorb_damage_with_shield(entry["userId"], dmg)
                        hp = self._get_combat_hp(entry["userId"]) or 10
                        self._set_combat_hp(entry["userId"], hp - ed, source_dmg=ed)
                        tname = entry.get("displayName", entry.get("name", entry["userId"]))
                        hp_max = (self.get_char(entry["userId"]).get_attr('体力上限', hp) if self.get_char(entry["userId"]) else hp)
                        tick_results.append({
                            'spell_name': spell_name, 'target_name': tname,
                            'type': 'damage', 'dice': tick_dice, 'roll': dmg,
                            'amount': ed, 'hp': hp - ed, 'hp_max': hp_max,
                            'alive': (hp - ed) > 0,
                        })
            if eff.get("tickHealHp"):
                for entry in il:
                    if entry.get("team") != zone_team: continue
                    if entry.get("isSummon") and entry.get("ignited"): continue  # Ignited summons cannot be healed
                    ec = entry.get("coord",""); ep = parse_coord(ec) if ec else None
                    if not ep: continue
                    if max(abs(ep[0]-cp[0]),abs(ep[1]-cp[1])) > eff["radius"]: continue
                    # 隔离：不受领域效果影响
                    if self._has_status(entry['userId'], '隔离'):
                        continue
                    # Dedup: skip if this (coord, spellName) already processed
                    pos_key = (ec, spell_name)
                    if not stackable and pos_key in processed_zone_positions:
                        continue
                    processed_zone_positions.add(pos_key)
                    heal = roll_dice(eff["tickHealHp"])
                    if heal > 0:
                        heal = int(heal * self._get_buff_heal_pct(entry["userId"], 'hp'))
                        heal += self._get_buff_heal_flat(entry["userId"], 'hp')  # AUX code 8
                        hp = self._get_combat_hp(entry["userId"]) or 10
                        ch = self.get_char(entry["userId"])
                        mhp = ch.get_attr("体力上限", hp) if ch else hp
                        self._set_combat_hp(entry["userId"], min(hp+heal, mhp))
                        tname = entry.get("displayName", entry.get("name", entry["userId"]))
                        tick_results.append({
                            'spell_name': spell_name, 'target_name': tname,
                            'type': 'heal_hp', 'dice': eff["tickHealHp"], 'roll': heal,
                            'amount': heal, 'hp': min(hp+heal, mhp), 'hp_max': mhp,
                            'alive': True,
                        })
            if eff.get("tickHealMp"):
                for entry in il:
                    if entry.get("team") != zone_team: continue
                    ec = entry.get("coord",""); ep = parse_coord(ec) if ec else None
                    if not ep: continue
                    if max(abs(ep[0]-cp[0]),abs(ep[1]-cp[1])) > eff["radius"]: continue
                    # 隔离：不受领域效果影响
                    if self._has_status(entry['userId'], '隔离'):
                        continue
                    # Dedup: skip if this (coord, spellName) already processed
                    pos_key = (ec, spell_name)
                    if not stackable and pos_key in processed_zone_positions:
                        continue
                    processed_zone_positions.add(pos_key)
                    heal = roll_dice(eff["tickHealMp"])
                    if heal > 0:
                        heal = int(heal * self._get_buff_heal_pct(entry["userId"], 'mp'))
                        heal += self._get_buff_heal_flat(entry["userId"], 'mp')  # AUX code 6
                        tname = entry.get("displayName", entry.get("name", entry["userId"]))
                        tick_results.append({
                            'spell_name': spell_name, 'target_name': tname,
                            'type': 'heal_mp', 'dice': eff["tickHealMp"], 'roll': heal,
                            'amount': heal, 'hp': 0, 'hp_max': 0,
                            'alive': True,
                        })
                        ch = self.get_char(entry["userId"])
                        cm = ch.get_attr("魔力",0) or 0; mx = ch.get_attr("魔力上限",cm) or cm
                        ch.set_attr("魔力", min(cm+heal, mx))
            # Attribute debuff per round (属性削减, e.g. 行动力-5) — temporary on entry
            ad = eff.get("attributeDebuff", "")
            if ad:
                match = re.match(r'^(.+?)([+-]\d+)$', ad)
                if match:
                    attr_name = match.group(1)
                    delta = int(match.group(2))
                    for entry in il:
                        ec = entry.get("coord", ""); ep = parse_coord(ec) if ec else None
                        if not ep: continue
                        if max(abs(ep[0]-cp[0]), abs(ep[1]-cp[1])) > eff["radius"]: continue
                        if attr_name in ('行动力', '移动力'):
                            key = '_zone_penalty_' + attr_name
                            entry[key] = max(entry.get(key, 0), abs(delta))
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
        tmpls = _SUMMON_TEMPLATES
        if tmpls is None:
            from characters_data import SUMMON_TEMPLATES as tmpls
        if tmpls is None:
            return
        il = self._get_initiative()
        summons = [e for e in il if e.get("isSummon")]
        groups = {}
        for s in summons:
            tmpl = tmpls.get(s.get("name", ""), {})
            mg = tmpl.get("merge_group", "")
            if not mg: continue
            if mg not in groups:
                groups[mg] = []
            groups[mg].append(s)
        for mg, members in groups.items():
            alive = [m for m in members if (self._get_combat_hp(m["userId"]) or 0) > 0]
            if len(alive) != 1: continue
            # Require all templates in this merge_group to have been summoned
            all_templates = {name for name, t in tmpls.items()
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
            member_tmpl = tmpls.get(members[0].get("name", ""), {})
            result_name = member_tmpl.get("merge_result", "")
            result_tmpl = tmpls.get(result_name, {})
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
            merged["react_block_w"] = result_tmpl.get("react_block", 0)
            merged["shield_block"] = result_tmpl.get("shield_block", 0)
            merged["shield_block_hp"] = result_tmpl.get("shield_block", 0)
            merged["shield_block_rate"] = result_tmpl.get("shield_block_rate", 0.70)
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
            merged["zone_skills"] = [sk for sk in parsed if sk.get("skill_type") == "zone_heal"]
            merged["zone_cooldown"] = 0
            merged["summon_mp"] = result_tmpl.get("MP", 0)
            # Update action count
            # Set character attrs needed for main-action formula
            merged_char = self.get_char(merged["userId"])
            merged_char.set_attr('MOV', result_tmpl.get('MOV', 6))
            merged_char.set_attr('额外行动数', result_tmpl.get('额外行动数', result_tmpl.get('回合行动数', result_tmpl.get('行动次数', 0))))
            # Update action count using formula
            acts = self._get_actions()
            merged["baseUserId"] = merged["userId"]
            merged["actionIdx"] = 0
            self._ensure_dynamic_slots(merged["userId"])
            acts[merged["userId"]] = {"主动": self._calc_main_actions(merged["userId"]), "附加": 1}
            self._set_actions(acts)
            self._set_initiative(il)

    # ---- Conditional effects ----
    def _can_basic_attack(self, uid, char=None):
        # Check control status (缴械/击晕) from parent, then fast-specific checks
        if not CombatEngine._can_basic_attack(self, uid):
            return False
        return True

    def _is_flying(self, uid):
        il = self._get_initiative(); entry = next((e for e in il if e["userId"]==uid), None)
        if entry and entry.get("flying"): return True
        char = self.get_char(uid)
        if char and char.get_attr('飞行', 0): return True
        return any(b.get("auxType")=="飞行" for b in self._get_active_buffs(uid))

    def _can_melee(self, atk_uid, def_uid):
        """Check melee vs flying. Returns (can_attack:bool, penalty_die:bool)."""
        atk_flying = self._is_flying(atk_uid)
        def_flying = self._is_flying(def_uid)
        if not atk_flying and def_flying:
            if FLYING_MELEE_RULE >= 2:
                return False, False
            elif FLYING_MELEE_RULE == 1:
                return True, True  # can attack with penalty die
        return True, False

    # ---- Fast attack (simplified but with lethality + shield) ----
    # ---- Fast COC7 attack with success-rank damage + reaction (training mode) ----
    def _fast_coc7_attack(self, atk_uid, def_uid, skill_name, skill_val, dmg_dice, pen, leth):
        """Full COC7 attack for training: rank-based damage + trainable reaction."""
        achar = self.get_char(atk_uid); dchar = self.get_char(def_uid)
        # ── 射程移动 ──
        atk_range = _get_attack_range(skill_name=skill_name)
        can_atk, _orig_coord = self._move_to_attack_range(atk_uid, def_uid, atk_range)
        if not can_atk:
            return (def_uid, atk_uid, 0, f"射程不足 (range={atk_range})")
        # ── 飞行近战限制 ──
        can_melee, fly_penalty = self._can_melee(atk_uid, def_uid)
        if not can_melee:
            return (def_uid, atk_uid, 0, f"无法近战飞行目标")
        if fly_penalty:
            pen += 1  # 飞行近战惩罚骰
        # ── 幻造兵武 (phantom creation): basic attacks also benefit from 幻造 buff ──
        atk_effects2 = self._get_effects()
        for ce in atk_effects2:
            if ce.get('type') == 'buff' and ce.get('spellName') == '幻造兵武' \
               and ce.get('targetUserId') == atk_uid:
                writing_val2 = int(achar.get_attr('写作', 0) or 0)
                if writing_val2 > 0:
                    roll2, _ = roll_d100('')
                    if roll2 <= writing_val2:
                        phantom_bonus2 = ce.get('auxVal', 0) or 0
                        if phantom_bonus2:
                            if isinstance(phantom_bonus2, str) or int(phantom_bonus2) > 0:
                                dmg_dice = f"{dmg_dice}+{phantom_bonus2}"
                break
        # AUX code 4: merge bonus damage dice
        bonus_dice = self._get_buff_dmg_dice_bonus(atk_uid)
        if bonus_dice:
            dmg_dice = f"{dmg_dice}+{bonus_dice}" if dmg_dice else bonus_dice
        eff_skill = self._apply_buff_skill_mod(atk_uid, skill_val)
        atk_buffs = self._get_active_buffs(atk_uid)
        # 行动力优势合并到攻击BP
        ap_atk_b, ap_def_p = self._calc_action_power_bp(atk_uid, def_uid)
        eff_bp = _calc_net_bp(atk_buffs, ap_atk_b, skill_name)
        crit_pct, crit_flat = self._get_buff_crit_adjustment(atk_uid)
        atk_result, atk_bp_detail = roll_d100(eff_bp); atk_rank = success_rank(atk_result, eff_skill, crit_pct, crit_flat)
        bp_str = f", {atk_bp_detail}" if atk_bp_detail else ""
        # 即使攻击失败也允许防御方反应——大失败闪避/格挡可能使攻方意外命中，
        # 成功反击也可能在攻方失败时命中造成伤害。
        if atk_rank <= 0 and not getattr(self, '_allow_failed_reaction', True):
            return (def_uid, atk_uid, 0, f"D100={atk_result}/{eff_skill}{bp_str} {rank_text(atk_rank)}")

        dodge_val = dchar.get_attr("闪避",25); bmn, bmv = dchar.get_best_melee()
        dodge_val = self._apply_buff_skill_mod(def_uid, dodge_val)
        bmv = self._apply_buff_skill_mod(def_uid, bmv)
        def_buffs = self._get_active_buffs(def_uid)
        _, ap_def_p2 = self._calc_action_power_bp(atk_uid, def_uid)
        def_bp = _calc_net_bp(def_buffs, ap_def_p2, skill_name)

        # ── 格挡可用性 ──
        block_name, block_val = self._get_block_skill(def_uid)
        block_val = self._apply_buff_skill_mod(def_uid, block_val)
        can_block = (dchar.get_attr('可格挡', 0) == 1
                     and block_name is not None and block_val > 0)
        block_hp = next((e for e in self._get_initiative() if e['userId'] == def_uid), {}).get('shield_block_hp', 0)
        # Check for block_redirect (summon takes damage instead of defender)
        _block_redirect = self._get_block_redirect_target(def_uid)
        can_block = can_block and (block_hp > 0 or _block_redirect is not None)

        # ── 击晕禁止所有反应 ──
        if not self._can_react(def_uid):
            return (def_uid, atk_uid, 0, f"击晕中，无法反应")
        # Reaction: use stored character weights (trainable, three-way)
        dw = getattr(self, '_react_dw', {}).get(def_uid, 50)
        cw = getattr(self, '_react_cw', {}).get(def_uid, 50)
        bw = getattr(self, '_react_bw', {}).get(def_uid, 0) if can_block else 0
        total_w = dw + cw + bw
        if total_w > 0:
            r = random.random() * total_w
            if r < dw: react_choice = 'dodge'
            elif r < dw + cw: react_choice = 'counter'
            else: react_choice = 'block'
        else:
            react_choice = 'dodge' if dodge_val >= bmv else 'counter'

        if react_choice == 'dodge':
            rr, rd_detail = roll_d100(def_bp); react_rank = success_rank(rr, dodge_val)
            eff_atk = atk_rank
            if react_rank > 0: eff_atk -= react_rank
            if react_rank == -2: eff_atk += (2 if eff_atk == -1 else 1)
            eff_atk = max(-2, min(4, eff_atk))
            if eff_atk <= 0: return (def_uid, atk_uid, 0, f"D100={rr}/{dodge_val} dodge rank={react_rank}")
            winner_rank, winner_uid, loser_uid = eff_atk, atk_uid, def_uid
            is_counter = False
        elif react_choice == 'counter':
            rr, rd_detail = roll_d100(def_bp); react_rank = success_rank(rr, bmv)
            eff_atk, eff_react = atk_rank, react_rank
            if react_rank == -2: eff_atk += (2 if eff_atk == -1 else 1)
            if eff_atk == -2: eff_react = min(4, eff_react + 1)
            eff_atk, eff_react = max(-2, min(4, eff_atk)), max(-2, min(4, eff_react))
            if eff_react > eff_atk and eff_react > 0:
                winner_rank, winner_uid, loser_uid = eff_react, def_uid, atk_uid
                cdmg = dchar.get_str("伤害值") or "1d4"
                cdmg = _resolve_db(cdmg, _get_db_str(dchar))
                cpen = dchar.get_attr("伤害贯穿",1); cleth = dchar.get_attr("致死骰",1) or 0
                dmg_dice, pen, leth, is_counter = cdmg, cpen, cleth, True
            elif eff_atk > eff_react and eff_atk > 0:
                winner_rank, winner_uid, loser_uid, is_counter = eff_atk, atk_uid, def_uid, False
            else: return (None, None, 0, "draw")

        else:  # react_choice == 'block'
            # ── 格挡反应 (训练引擎) ──
            rr, rd_detail = roll_d100(def_bp); react_rank = success_rank(rr, block_val)
            eff_atk = atk_rank
            if react_rank > 0: eff_atk -= react_rank
            if react_rank == -2: eff_atk += (2 if eff_atk == -1 else 1)
            eff_atk = max(-2, min(4, eff_atk))
            if eff_atk <= 0:
                # 格挡成功：盾牌承受伤害，不触发致死
                blk_mx = max_damage(dmg_dice)
                if atk_rank == 2: blk_dmg = max(roll_dice(dmg_dice), roll_dice(dmg_dice))
                elif atk_rank == 3: blk_dmg = blk_mx + roll_dice(dmg_dice) if pen else blk_mx
                elif atk_rank == 4: blk_dmg = blk_mx * 2 if (atk_result == 1 or pen) else blk_mx + roll_dice(dmg_dice)
                else: blk_dmg = roll_dice(dmg_dice)
                blk_dmg = int(blk_dmg * self._get_buff_dmg_mult(atk_uid, def_uid) * self._get_buff_dmg_dice_mult(atk_uid))
                blk_dmg += self._get_buff_dmg_flat(def_uid); blk_dmg = max(0, blk_dmg)  # AUX code 2: before shield
                blk_eff, _, _ = self._absorb_damage_with_shield(def_uid, blk_dmg)
                # ── block_redirect: 伤害由召唤物承担 ──
                _block_redirect = self._get_block_redirect_target(def_uid)
                if _block_redirect:
                    rt_entry = next((e for e in self._get_initiative() if e['userId'] == _block_redirect), None)
                    rt_block_hp = rt_entry.get('shield_block_hp', 0) if rt_entry else 0
                    if blk_eff > 0 and rt_block_hp > 0:
                        absorbed = min(blk_eff, rt_block_hp)
                        blk_eff -= absorbed
                        rt_entry['shield_block_hp'] = rt_block_hp - absorbed
                    cur_hp = self._get_combat_hp(_block_redirect) or 10
                    cur_hp = max(0, cur_hp - blk_eff)
                    self._set_combat_hp(_block_redirect, cur_hp, source_dmg=blk_eff)
                    return (def_uid, atk_uid, blk_eff, f"block redirect to {rt_entry.get('name','?')} dmg:{blk_eff}")
                # ── 标准逻辑 ──
                if blk_eff > 0 and block_hp > 0:
                    absorbed = min(blk_eff, block_hp)
                    blk_eff -= absorbed
                    entry = next((e for e in self._get_initiative() if e['userId'] == def_uid), None)
                    if entry: entry['shield_block_hp'] = block_hp - absorbed
                cur_hp = self._get_combat_hp(def_uid) or 10
                cur_hp = max(0, cur_hp - blk_eff)
                self._set_combat_hp(def_uid, cur_hp, source_dmg=blk_eff)
                return (def_uid, atk_uid, blk_eff, f"block success dmg:{blk_eff}")
            else:
                # 格挡失败：攻击突破盾牌
                winner_rank, winner_uid, loser_uid = eff_atk, atk_uid, def_uid
                is_counter = False

        # Track winning roll for 大成功 handling: use rr for counter, atk_result for attacker
        winner_roll = rr if is_counter else atk_result
        mx = max_damage(dmg_dice); dmg_val = 0
        # AUX 13/14: damage dice adv/dis from buffs
        buf_dmg_adv, buf_dmg_dis = self._get_buff_dmg_dice_adv_dis(winner_uid)
        if winner_rank == 2:
            dmg_val = max(roll_dice(dmg_dice), roll_dice(dmg_dice))
        elif winner_rank == 3:
            dmg_val = mx + roll_dice(dmg_dice) if pen else mx
        elif winner_rank == 4:
            dmg_val = mx * 2 if (winner_roll == 1 or pen) else mx + roll_dice(dmg_dice)
        else:
            if buf_dmg_adv:
                dmg_val = max(roll_dice(dmg_dice), roll_dice(dmg_dice))
            elif buf_dmg_dis:
                dmg_val = min(roll_dice(dmg_dice), roll_dice(dmg_dice))
            else:
                dmg_val = roll_dice(dmg_dice)

        # AUX 1,3,19: apply damage multipliers before shield
        dmg_val = int(dmg_val * self._get_buff_dmg_mult(winner_uid, loser_uid) * self._get_buff_dmg_dice_mult(winner_uid))
        dmg_val += self._get_buff_dmg_flat(loser_uid); dmg_val = max(0, dmg_val)  # AUX code 2: before shield
        # Check for damageModifier effects (e.g. 火鼠裘)
        for e in self._get_effects():
            if e.get('type') == 'damageModifier' and e.get('targetUserId') == loser_uid \
               and e.get('variant') == 'fire_rat_cloak' and e.get('remainingRounds', 0) > 0:
                def_char = self.get_char(loser_uid)
                cur_mp = def_char.get_attr('魔力', 0) if def_char else 0
                if cur_mp >= 1:
                    def_char.set_attr('魔力', cur_mp - 1)
                    if dmg_val <= 3:
                        dmg_val = 0
                    elif dmg_val < 7:
                        dmg_val = 3
                    else:
                        dmg_val = int(dmg_val * 0.6)
                break
        eff_dmg, _, _ = self._absorb_damage_with_shield(loser_uid, dmg_val)
        cur_hp = self._get_combat_hp(loser_uid) or 10
        exp_dmg = avg_damage(dmg_dice)
        if leth and exp_dmg > 6:
            # Lethality: d(2×cur_hp) ≤ expected_damage → instant 120% max HP death
            leth_die_size = max(2, cur_hp * 2)
            leth_adv, leth_dis = self._get_buff_leth_adv_dis(winner_uid)
            if leth_adv:
                lr = max(random.randint(1, leth_die_size), random.randint(1, leth_die_size))
            elif leth_dis:
                lr = min(random.randint(1, leth_die_size), random.randint(1, leth_die_size))
            else:
                lr = random.randint(1, leth_die_size)
            if lr <= int(exp_dmg):
                loser_char = self.get_char(loser_uid)
                max_hp = loser_char.get_attr('体力上限', loser_char.get_attr('体力', 10))
                cur_hp = -int(max_hp * 1.2)
            else:
                cur_hp = cur_hp - eff_dmg  # raw (may be negative → overflow)
        else:
            cur_hp = cur_hp - eff_dmg  # raw (may be negative → overflow)
        self._set_combat_hp(loser_uid, cur_hp, source_dmg=eff_dmg)

        react_tag = react_choice
        return (winner_uid, loser_uid, eff_dmg, f"rank:{winner_rank} react:{react_tag} dmg:{eff_dmg}")

    def _fast_basic_attack(self, uid, tid=None):
        char = self.get_char(uid); il = self._get_initiative()
        me = next((e for e in il if e["userId"]==uid), None)
        if not me: return ""
        if not self._can_basic_attack(uid, char): return ""
        enemies = [e for e in il if e["team"]!=me.get("team","Y") and (self._get_combat_hp(e["userId"])or 0)>0 and not self._is_untargetable(e['userId'])]
        if not enemies: return ""
        # ── 射程过滤 + 移动 ──
        bn_tmp, bv_tmp = char.get_best_melee()
        atk_range = _get_attack_range(skill_name=bn_tmp)
        reachable = []
        for e in enemies:
            if self._can_melee(uid, e["userId"]):
                can, orig = self._move_to_attack_range(uid, e["userId"], atk_range)
                if can:
                    reachable.append((e, orig))
        if not reachable:
            return ""
        target, _orig_coord = reachable[0]
        if tid and any(e["userId"]==tid for e, _ in reachable):
            for e, oc in reachable:
                if e["userId"]==tid: target, _orig_coord = e, oc; break
        tid = target["userId"]
        bn, bv = char.get_best_melee(); bv = self._apply_buff_skill_mod(uid, bv)
        if random.randint(1,100) > bv:
            return ""
        dd = self._get_damage_dice(uid, bn); p = char.get_attr("伤害贯穿",1); l = char.get_attr("致死骰",1) or 0
        # AUX code 4: merge bonus damage dice
        bonus_dice = self._get_buff_dmg_dice_bonus(uid)
        if bonus_dice:
            dd = f"{dd}+{bonus_dice}" if dd else bonus_dice
        dmg_val = roll_dice(dd)
        # AUX 1,3,19: apply damage multipliers before shield
        dmg_val = int(dmg_val * self._get_buff_dmg_mult(uid, tid) * self._get_buff_dmg_dice_mult(uid))
        dmg_val += self._get_buff_dmg_flat(tid); dmg_val = max(0, dmg_val)  # AUX code 2: before shields
        # Check for damageModifier effects (e.g. 火鼠裘)
        for e in self._get_effects():
            if e.get('type') == 'damageModifier' and e.get('targetUserId') == tid \
               and e.get('variant') == 'fire_rat_cloak' and e.get('remainingRounds', 0) > 0:
                def_char = self.get_char(tid)
                cur_mp = def_char.get_attr('魔力', 0) if def_char else 0
                if cur_mp >= 1:
                    def_char.set_attr('魔力', cur_mp - 1)
                    if dmg_val <= 3:
                        dmg_val = 0
                    elif dmg_val < 7:
                        dmg_val = 3
                    else:
                        dmg_val = int(dmg_val * 0.6)
                break
        eff_dmg, _, _ = self._absorb_damage_with_shield(tid, dmg_val)
        eff_dmg = self._apply_shield_block(tid, eff_dmg)
        cur_hp = self._get_combat_hp(tid) or 10
        exp_dmg = avg_damage(dd)
        if l and exp_dmg > 6:
            # Lethality: d(2×cur_hp) ≤ expected_damage → instant 120% max HP death
            leth_die_size = max(2, cur_hp * 2)
            leth_adv, leth_dis = self._get_buff_leth_adv_dis(uid)
            if leth_adv:
                lr = max(random.randint(1, leth_die_size), random.randint(1, leth_die_size))
            elif leth_dis:
                lr = min(random.randint(1, leth_die_size), random.randint(1, leth_die_size))
            else:
                lr = random.randint(1, leth_die_size)
            if lr <= int(exp_dmg): cur_hp = 0
            else: cur_hp = cur_hp - eff_dmg  # raw (may be negative → overflow)
        else: cur_hp = cur_hp - eff_dmg  # raw (may be negative → overflow)
        self._set_combat_hp(tid, cur_hp, source_dmg=eff_dmg)
        if cur_hp <= 0:
            target_entry = next((e for e in il if e['userId'] == tid), None)
            if target_entry and not target_entry.get('isSummon'):
                self._remove_summons_of_owner(tid)
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
        # ── 状态检查：禁锢/击晕禁止移动 ──
        if not self._can_move(uid):
            return ""
        il = self._get_initiative(); me = next((e for e in il if e["userId"]==uid), None)
        if not me: return ""
        mt = me.get("team","Y"); mc = me.get("coord","")
        enemies = [e for e in il if e["team"]!=mt and (self._get_combat_hp(e["userId"])or 0)>0 and not self._is_untargetable(e['userId'])]
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
