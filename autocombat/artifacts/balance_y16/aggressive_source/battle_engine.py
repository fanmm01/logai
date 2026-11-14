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


def _get_summon_template(tmpl_name):
    """Resolve a summon template dict by template name.

    Prefers the module-level `_SUMMON_TEMPLATES` registry (injected by trainers /
    the merged backend); falls back to a lazy import of characters_data.
    Returns None when the template is unknown."""
    if not tmpl_name:
        return None
    tmpls = _SUMMON_TEMPLATES
    if tmpls is None:
        from characters_data import SUMMON_TEMPLATES as tmpls
    return (tmpls or {}).get(tmpl_name)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ============================================================
#  Constants
# ============================================================
# FLYING_MELEE_RULE: 近战对飞行单位的命中规则
#   2 = 非飞行单位无法近战飞行目标
#   1 = 非飞行单位近战飞行目标时获得1个惩罚骰（当前规则）
#   0 = 无限制（近战可正常命中飞行单位）
FLYING_MELEE_RULE = 1

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
    33:'反应惩罚骰',
    34:'全D100检定奖励骰',
}
AUX_NAME_TO_CODE = {v: k for k, v in AUX_EFFECT_TYPES.items()}
# Only implemented, read-only modifiers can be supplied by a live zone view.
ZONE_AUX_CODES = frozenset(range(1, 23)) | {29}

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
        # Remove the db term entirely: "+db", "-db", "db" — including a trailing
        # division qualifier ("+db/2") so no dangling "/N" survives (e.g. '1d6+db/2' → '1d6').
        expr = re.sub(r'[+-]?\s*\bdb\b(?:\s*/\s*\d+)?', '', expr, flags=re.IGNORECASE).strip()
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


_DICE_TOKEN = re.compile(r'(\d*)d(\d+)', re.IGNORECASE)
_ARITH_ONLY = re.compile(r'[0-9+\-*/().\s]+$')

# 月相表（Y17 血月斩击 damage_table='moon_phase'）— 规格见 reference/各魔法少女魔法列表.md
# 每项: (月相名, 伤害骰或固定值, 是否生命吸取)。引擎掷 1d8 选月相。
_MOON_PHASE_TABLE = [
    ('满月',   '1d12+3',  True),   # 1 强行吸取 1d12+3 生命
    ('亏凸月', '1d8',     True),   # 2 造成 1d8 伤害 + 1d8 生命吸取
    ('弦月',   '1d12',    False),  # 3 造成 1d12 伤害
    ('残月',   '1d6+4',   False),  # 4 造成 1d6+4 伤害
    ('新月',   '1d16+12', False),  # 5 造成 d16+12 伤害
    ('蛾眉月', '8',       False),  # 6 固定 8 点伤害
    ('上弦月', '5',       True),   # 7 固定 5 点伤害 + 5 点生命吸收
    ('盈凸月', '10',      True),   # 8 固定 10 点生命偷取
]


def _dice_group_value(cnt, sides, mode):
    """Value of one NdM dice group for the given mode.

    mode: 'roll' (random total), 'avg' (expected value), 'max' (maximum)."""
    cnt = int(cnt) if cnt else 1
    sides = int(sides)
    if cnt <= 0 or sides <= 0:
        return 0
    if mode == 'avg':
        return cnt * (sides + 1) / 2.0
    if mode == 'max':
        return cnt * sides
    return sum(random.randint(1, sides) for _ in range(cnt))


def _legacy_dice_sum(expr_n):
    """Legacy fallback: sum dice/integer tokens, silently ignoring anything else."""
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


def _eval_dice_expr(expr, mode='roll'):
    """Evaluate a dice expression.

    mode: 'roll' (random totals, int) | 'avg' (expected values, float) | 'max' (max, int).
    Supports NdM dice, integers and arithmetic + - * / ( ). Division truncates
    toward zero, matching the JS reference implementation (parseInt).
    Malformed tokens fall back to the legacy token-sum behavior."""
    if not expr or expr == '0':
        return 0
    expr_n = re.sub(r'(^|[^0-9])d', r'\g<1>1d', str(expr), flags=re.IGNORECASE)
    expr_v = _DICE_TOKEN.sub(lambda m: f'({_dice_group_value(m.group(1), m.group(2), mode)})', expr_n)
    if not _ARITH_ONLY.fullmatch(expr_v):
        return _legacy_dice_sum(expr_n)
    try:
        val = eval(expr_v, {'__builtins__': {}}, {})
    except Exception:
        # e.g. '2 3' (two bare numbers) — legacy token sum
        return _legacy_dice_sum(expr_n)
    if mode == 'avg':
        return val
    return int(val)


def roll_dice(expr):
    return _eval_dice_expr(expr, 'roll')


def _legacy_dice_detailed(expr_n):
    """Legacy detailed roll (pre-arithmetic format), kept for malformed expressions."""
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
    detail = ''.join(parts_detail)
    if detail.startswith('+'):
        detail = detail[1:]
    return (total, f'{detail}={total}')


def roll_dice_detailed(expr):
    """Roll dice and return (total, breakdown_string).

    Breakdown format:
      2d3+1d4 → "2d3(2+1=3)+1d4(4)=7"
      1d6     → "1d6=6"
      Static: "2d6+4" → "2d6(3+5=8)+4=12"
      Arithmetic: "1d6/2" → "1d6(4)/2=2"
      If expr is empty or '0', returns (0, '0').
    """
    if not expr or expr == '0':
        return (0, '0')
    expr_n = re.sub(r'(^|[^0-9])d', r'\g<1>1d', str(expr), flags=re.IGNORECASE)
    # Roll each dice group once; record its breakdown text, then reuse it for the
    # arithmetic evaluation so the dice are not rolled twice.
    parts = []
    def sub_dice(m):
        cnt = int(m.group(1)) if m.group(1) else 1
        sides = int(m.group(2))
        if cnt <= 0 or sides <= 0:
            parts.append('0'); return '(0)'
        rolls = [random.randint(1, sides) for _ in range(cnt)]
        subtotal = sum(rolls)
        if cnt == 1:
            parts.append(f'1d{sides}({rolls[0]})')
        else:
            parts.append(f'{cnt}d{sides}({"+".join(str(r) for r in rolls)}={subtotal})')
        return f'({subtotal})'
    expr_v = _DICE_TOKEN.sub(sub_dice, expr_n)
    if _ARITH_ONLY.fullmatch(expr_v):
        try:
            total = int(eval(expr_v, {'__builtins__': {}}, {}))
            detail = _DICE_TOKEN.sub(lambda m: parts.pop(0), expr_n)
            return (total, f'{detail}={total}')
        except Exception:
            pass
    # Malformed expression — legacy formatting path
    return _legacy_dice_detailed(expr_n)

def avg_damage(expr):
    return _eval_dice_expr(expr, 'avg')

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

def _parse_bp_token(token):
    """Parse one bonus/penalty-dice token into a signed count.

    Accepted forms: 'b', 'b2', '2b', 'p', 'p3', '3p', '' and ints
    (positive = bonus dice, negative = penalty dice). Unknown text → 0.
    """
    if token is None or isinstance(token, bool):
        return 0
    if isinstance(token, int):
        return token
    s = str(token).strip().lower()
    if not s:
        return 0
    m = re.match(r'^([bp])(\d*)$', s)
    if m:
        n = int(m.group(2) or 1)
        return n if m.group(1) == 'b' else -n
    m = re.match(r'^(\d*)([bp])$', s)
    if m:
        n = int(m.group(1) or 1)
        return n if m.group(2) == 'b' else -n
    return 0


def _format_bp(net):
    """Format a signed net dice count, capped at ±3, as 'b'/'b2'/'p'/'p2'/''."""
    net = max(-3, min(3, int(net)))
    if net > 0:
        return f'b{net}' if net > 1 else 'b'
    if net < 0:
        n = -net
        return f'p{n}' if n > 1 else 'p'
    return ''


def _calc_net_bp(buffs, bp_suffix='', skill_name=None, extra_tokens=None,
                 ignore_global=False, only_codes=None):
    """Calculate net bonus/penalty dice from every source, then cap at ±3.

    Sources, all summed with p+b cancellation before the cap:
    - explicit tokens (bp_suffix / extra_tokens) like 'b','b2','2b','p','p3','3p'
    - AUX 16 (伤害成功率奖励惩罚): a generic dice count
    - AUX 34 (全D100检定奖励骰): bonus dice on every D100 check of the unit
    - AUX 17/18 (技能奖励骰/技能惩罚骰): a dice count ('b2'/'p') applies to any
      skill; a non-dice value is matched against skill_name and counts as ±1.
    - AUX 33 (反应惩罚骰) is supplied by the caller via extra_tokens (attack-only).

    ``ignore_global=True`` skips only the caster's active buffs; explicit tokens
    (command line, effect-inherent dice) still apply.

    ``only_codes`` restricts which buff AUX codes contribute (e.g. non-skill
    checks pass {34} so attack-only modifiers never leak into saves).
    """
    net = _parse_bp_token(bp_suffix)
    for tok in (extra_tokens or []):
        net += _parse_bp_token(tok)

    if not ignore_global:
        for b in buffs or []:
            aux_code = b.get('auxCode')
            aux_type = b.get('auxType', '')
            aux_val = str(b.get('auxVal', '')).strip()
            # Tolerate spells loaded before auxCode existed.
            if not aux_code:
                aux_code = AUX_NAME_TO_CODE.get(aux_type, 0)
            if only_codes is not None and aux_code not in only_codes:
                continue

            if aux_code == 16:
                net += _parse_bp_token(aux_val)
            elif aux_code == 34:
                # 全D100检定奖励骰: every D100 check gets this many bonus dice,
                # consumed once per check (this helper runs once per check).
                net += _parse_bp_token(aux_val) or 1
            elif aux_code in (17, 18):
                signed = _parse_bp_token(aux_val)
                if signed:
                    # Generic dice count — applies to every skill.
                    net += signed
                elif skill_name and aux_val.lower() == str(skill_name).lower():
                    net += 1 if aux_code == 17 else -1
    return _format_bp(net)


def _parse_attr_mods(spec):
    """Parse 'MOV+5' / '闪避-30' / '斗殴+10;敏捷-5' into [(name, delta), ...]."""
    if spec is None:
        return []
    mods = []
    for part in re.split(r'[;；,，/]', str(spec)):
        m = re.match(r'^\s*([^\d+\-]+?)\s*([+-])\s*(\d+)\s*$', part)
        if m:
            delta = int(m.group(3)) * (-1 if m.group(2) == '-' else 1)
            mods.append((m.group(1).strip(), delta))
    return mods


_MOV_ATTR_GROUP = ('MOV', '行动力', '移动力')


def _attr_group(name):
    return 'MOV' if name in _MOV_ATTR_GROUP else name


def max_damage(expr):
    if not expr: return 0
    return max(0, _eval_dice_expr(expr, 'max'))

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

def _get_attack_range(spell_effect=None, skill_name=None, override=None):
    """获取攻击的射程。法术效果优先读取其'射程'字段；未声明默认2(近战)。
    普攻: MELEE_SKILLS中的技能=2, 其他=99。
    调试: 若 characters_data_pvp.GLOBAL_RANGE_OVERRIDE == 1，所有射程返回99。"""
    try:
        from characters_data import GLOBAL_RANGE_OVERRIDE
        if GLOBAL_RANGE_OVERRIDE if override is None else override:
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
class SpellInterrupted(Exception):
    """Raised when an effect needs external resolution (e.g. PvP reaction).

    ``data`` carries the pending-reaction payload. Subclasses (ReactionNeeded
    in battle_http_server) keep the same contract.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args)
        self.data = kwargs.get('data', {})


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
class ActionExecutionResult(str):
    """String-compatible result; rejected commands never spend an action."""
    def __new__(cls, text='', committed=True, reason='', slot='主动'):
        result = super().__new__(cls, text)
        result.committed = committed
        result.reason = reason
        result.slot = slot
        return result


def action_committed(result):
    return getattr(result, 'committed', True)


class CombatEngine:
    def _get_attack_range(self, spell_effect=None, skill_name=None):
        return _get_attack_range(spell_effect, skill_name, getattr(self, '_range_override_snapshot', None))

    def _get_summon_template(self, name):
        registry = getattr(self, '_summon_templates_snapshot', None)
        return registry.get(name) if registry is not None else _get_summon_template(name)

    def _is_y16(self, uid):
        return self.get_char(uid).serial == 'Y16'

    def _y16_resource_state(self, uid):
        weapon = self.get_char(uid).get_str('_weapon_name') or 'staff'
        moons = self._find_summons_by_field(self._battle_team_of(uid), 'replenish_allies')
        energy = moons[0][1].get_attr('魔力', 0) if moons else 0
        return ({'staff': 0, 'bow': 1, 'sword': 2}.get(weapon, 0),
                int(bool(moons)), 0 if energy <= 0 else (1 if energy <= 20 else 2))

    def _y16_preflight(self, uid, spell, *, check_cost=False):
        if not self._is_y16(uid):
            return ''
        char = self.get_char(uid)
        weapon = char.get_str('_weapon_name') or 'staff'
        if spell.get('weapon_required') and spell['weapon_required'] != weapon:
            return '当前武器无法使用该技能'
        for eff in spell.get('effects', []):
            if eff.get('weapon_state') == weapon:
                return '已经装备该武器'
            if eff.get('consume_moon') and self._y16_resource_state(uid)[2] != 2:
                return '需要存活的虚假之月且月能超过20'
            if eff.get('召唤物模板'):
                remain, reason = self._get_summon_quota(uid, eff['召唤物模板'])
                if remain == 0:
                    return reason
        if check_cost:
            cost = spell.get('_mp_formula_transformed') if char.hs_transformed else None
            cost = cost or spell.get('消耗mp', 0)
            cost = max_damage(str(cost)) if isinstance(cost, str) else cost
            if weapon == 'bow' and any(e.get('bow_volley') for e in spell.get('effects', [])):
                cost += 1
            if cost > 0:
                cost = max(1, int(cost * self._get_buff_mp_cost_pct(uid))
                           + self._get_buff_mp_cost_flat(uid))
            if char.get_attr('魔力', 0) < cost:
                return '魔力不足'
        return ''

    def _y16_prepare_spell(self, uid, spell):
        if not self._is_y16(uid):
            return spell
        spell = dict(spell, effects=[dict(e) for e in spell.get('effects', [])])
        for key in ('_shared_phantom', '_moon_charge_done', '_moon_dmg'):
            spell.pop(key, None)
        if (self.get_char(uid).get_str('_weapon_name') or 'staff') == 'bow':
            effects = []
            for eff in spell['effects']:
                count = eff.get('bow_volley', 0)
                if count:
                    spell['消耗mp'] = int(spell.get('消耗mp', 0)) + 1
                    spell['_share_writing'] = True
                    bow_dice = self._get_damage_dice(uid, '弓术')
                    for hit in range(count):
                        item = dict(eff, 伤害骰=f"{eff['伤害骰']}+{bow_dice}", letter=f'volley{hit}')
                        if hit:
                            item.pop('charge_moon', None)
                        effects.append(item)
                else:
                    effects.append(eff)
            spell['effects'] = effects
        return spell

    def _note_inspiration_event(self, uid):
        if not any(b.get('turn_mp_refund') for b in self._get_active_buffs(uid)):
            return
        pending = getattr(self, '_inspiration_pending', set())
        pending.add(self._resolve_uid(uid))
        self._inspiration_pending = pending

    def _settle_inspiration(self, uid):
        uid = self._resolve_uid(uid)
        pending = getattr(self, '_inspiration_pending', set())
        if uid not in pending:
            return
        pending.discard(uid)
        state = self._get_state() or {}
        marker = (state.get('round'), uid)
        settled = getattr(self, '_inspiration_settled', set())
        if marker in settled or (self._get_combat_hp(uid) or 0) <= 0:
            return
        settled.add(marker)
        self._inspiration_settled = settled
        buffs = [b for b in self._get_active_buffs(uid) if b.get('turn_mp_refund')][:2]
        amount = sum(roll_dice(b['turn_mp_refund']) for b in buffs)
        if amount:
            self._restore_resource(uid, amount)
            self._pending_battle_events.append(f'{self.get_char(uid).name} 灵感返蓝 +{amount} MP')

    def _phantom_spell_adjustment(self, uid, eff, spell, dice):
        """One shared writing result per volley, with one charge per committed cast."""
        if spell.get('_share_writing') and '_shared_phantom' in spell:
            bonus, output = spell['_shared_phantom']
            output = ''
        else:
            bonus, output = self._roll_phantom_bonus(uid)
            if spell.get('_share_writing'):
                spell['_shared_phantom'] = (bonus, output)
        if bonus:
            dice += f'{bonus:+d}'
        charge = eff.get('charge_moon', 0)
        if charge and not spell.get('_moon_charge_done'):
            amount = roll_dice(charge) if isinstance(charge, str) else int(charge)
            amount += bonus
            if (self.get_char(uid).get_str('_weapon_name') or 'staff') == 'staff':
                amount += eff.get('staff_charge_bonus', 0)
            charged = self._charge_moon_energy(self._battle_team_of(uid), max(0, amount))
            spell['_moon_charge_done'] = True
            if charged:
                output += f'  月能充能 ({spell.get("name", "")}): +{charged}\n'
        return dice, output

    def _roll_phantom_bonus(self, uid):
        owner = uid
        buffs = self._get_active_buffs(uid)
        if not any(b.get('spellName') == '幻造兵武' for b in buffs):
            entry = self._unit_il_entry(uid, self._get_initiative()) or {}
            owner = entry.get('ownerId', uid)
            buffs = self._get_active_buffs(owner)
        if not any(b.get('spellName') == '幻造兵武' for b in buffs):
            return 0, ''
        char = self.get_char(owner)
        writing = char.get_attr('写作', 0)
        if writing <= 0:
            return 0, ''
        self._note_inspiration_event(uid)
        roll, detail = roll_d100(_calc_net_bp(buffs, '', '写作'))
        rank = success_rank(roll, self._apply_buff_skill_mod(owner, writing, '写作'))
        if rank >= 4:
            bonus = roll_dice('2d6')
        elif rank == 3:
            bonus = 6 + roll_dice('1d6')
        elif rank == 2:
            bonus = roll_dice('1d6')
        elif rank == 1:
            bonus = roll_dice('1d3')
        elif rank == -2:
            bonus = -roll_dice('1d2')
        else:
            bonus = 0
        return bonus, f'  幻造兵武·写作检定: D100={roll}/{writing} {rank_text(rank)} 伤害{bonus:+d}\n'

    def _y16_action_q(self, table, state, action):
        # Old weights remain usable until the Y16-only training run replaces them.
        value = table.get((state, action), table.get((state[:22], action), 0.0))
        if not action.startswith('SKILL_'):
            return value
        index = int(action.split('_')[1])
        weapon, alive, energy = state[-3:]
        prior = 0.0
        if index == 2 and not alive:
            prior = 1.5
        elif index == 3:
            prior = 1.0
        elif index == 6:
            prior = 1.5 if weapon == 1 else 0.5
        elif index in (7, 8, 9):
            prior = 2.0 if index == 9 and energy == 2 else (1.0 if index == 8 and weapon != 1 and energy < 2 else -1.5)
        elif index == 12:
            prior = 3.0
        elif index == 11:
            prior = 0.5 if state[0] < 6 else -1.0
        return value + prior

    def _charge_heal_overflow(self, uid, amount):
        """Route actual healing excess once, only through a live matching link."""
        amount = max(0, int(amount or 0))
        if not amount or (self._get_combat_hp(uid) or 0) <= 0:
            return 0
        entry = self._unit_il_entry(uid, self._get_initiative())
        if not entry:
            return 0
        tmpl = (self._get_summon_template(entry.get('name', '')) or {}) if entry.get('isSummon') else {}
        if tmpl.get('replenish_allies'):
            return 0
        for eff in self._get_effects():
            name = eff.get('linked_summon')
            if (eff.get('type') != 'buff' or not name or eff.get('remainingRounds', 0) <= 0
                    or self._resolve_uid(eff.get('targetUserId', '')) != self._resolve_uid(uid)):
                continue
            if self._has_status(uid, '隔离'):
                return 0
            for _sid, moon in self._find_summons_by_field(entry.get('team', 'Y'), 'replenish_allies'):
                if (self._get_summon_template(name) or {}).get('replenish_allies'):
                    linked = self._get_summon_entry(_sid)
                    if linked and linked.get('name') == name:
                        moon.set_attr('魔力', moon.get_attr('魔力', 0) + amount)
                        return amount
        return 0

    def _restore_resource(self, uid, amount, kind='mp'):
        amount = max(0, int(amount or 0))
        char = self.get_char(uid)
        key, cap = ('魔力', '魔力上限') if kind == 'mp' else ('理智', None)
        current = char.get_attr(key, 0)
        maximum = char.get_attr(cap, current) if cap else 99
        applied = min(amount, max(0, maximum - current))
        char.set_attr(key, current + applied)
        self._charge_heal_overflow(uid, amount - applied)
        return applied

    def _round_mp_regeneration(self, entries):
        seen = set()
        for entry in entries:
            uid = self._resolve_uid(entry['userId'])
            if uid in seen or (self._get_combat_hp(uid) or 0) <= 0:
                continue
            seen.add(uid)
            template = (self._get_summon_template(entry.get('name', '')) or {}) if entry.get('isSummon') else {}
            if template.get('receives_round_mp_regen', True):
                self._restore_resource(uid, roll_dice('2d6'))

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
        self._pending_battle_events = []
        self._ai_failed_commands = set()
        # Summon group consolidation: same-type summons share one initiative slot
        self._summon_groups = {}   # {rep_base_uid: {members, template_name, ownerId}}
        self._summon_members = {}  # {sid: {userId, name, display_name, team, coord, skills, ...}}
        self._summon_total_spawned = {}  # {(owner_base, template_name): 本场累计成功创建数}

    def _drain_battle_events(self):
        events = self._pending_battle_events
        self._pending_battle_events = []
        return events

    def _queue_zone_events(self, rows):
        if getattr(self, 'fast_mode', False):
            return
        for row in rows:
            amount = row.get('amount', 0)
            if not amount and not row.get('absorbed_excess'):
                continue
            kind = row['type']
            label = '伤害' if kind == 'damage' else ('回复HP' if kind == 'heal_hp' else '回复MP')
            self._pending_battle_events.append(
                f"【{row['spell_name']}】{row['target_name']} {label} {amount}"
                + (f" (HP:{row['hp']})" if kind != 'heal_mp' else ''))

    def _revive_preflight(self, uid, spell):
        effects = [e for e in spell.get('effects', []) if e.get('type') == 19]
        if effects and not any(self._revive_candidates(uid, e) for e in effects):
            return '没有符合条件的复活目标！'
        return ''

    def _is_y14(self, uid):
        char = self.characters.get(self._resolve_uid(uid))
        return bool(char and char.serial == 'Y14')

    def _y14_preflight(self, uid, spell):
        """Read-only cartridge checks shared by commands, policy and training."""
        if not self._is_y14(uid):
            return ''
        uid = self._resolve_uid(uid)
        char = self.get_char(uid)
        index = spell.get('index', 0)
        phase = getattr(char, 'phase', 2)
        if index not in {s['index'] for s in (char.spells or [])}:
            return '当前形态无法使用该技能'
        if self._get_cooldown(uid, index) > 0:
            return '技能冷却中'
        target_phase = next((e.get('target_phase') for e in spell.get('effects', [])
                             if e.get('type') == 17), None)
        if target_phase:
            if target_phase == phase:
                return '已经处于该卡带形态'
            if (char.get_attr('永久移除阶段') or 0) & (1 << target_phase):
                return '该卡带已弹出，无法再次加载'
        mode = getattr(char, '_y14_mode', 'swift')
        if index in (9, 10) and mode != ('swift' if index == 9 else 'brave'):
            return '该攻击属于另一重剑模式'
        if index in (11, 12) and mode == ('swift' if index == 11 else 'brave'):
            return '已经处于该模式'
        if spell.get('_once_per_battle') and (
                spell.get('name') in getattr(char, '_once_consumed', set()) or
                (uid, index) in getattr(self, '_revive_use_ledger', set())):
            return '本场战斗已使用过'
        cost = spell.get('消耗mp', 0) or 0
        if isinstance(cost, (int, float)) and cost > 0 and index != 8:
            cost = max(1, int(cost * self._get_buff_mp_cost_pct(uid)))
            cost = max(1, cost + self._get_buff_mp_cost_flat(uid))
            if char.get_attr('魔力', 0) < cost:
                return '魔力不足'
        needed = {}
        if spell.get('cost_counter_type'):
            needed[spell['cost_counter_type']] = spell.get('cost_counter_amount', 0)
        if spell.get('counter_required'):
            kind, amount = spell['counter_required']
            needed[kind] = needed.get(kind, 0) + amount
        if any(self._get_counter(uid, kind) < amount for kind, amount in needed.items()):
            return '资源不足'
        if any(e.get('type') == 1 and str(e.get('客体', 4)).upper() in ('4', '5', '24', '45', 'R', 'RP')
               for e in spell.get('effects', [])) and not self._enemy_target_entries(uid):
            return '没有可攻击的目标'
        return ''

    def _y14_start_cooldown(self, uid):
        uid = self._resolve_uid(uid)
        effects = [e for e in self._get_effects() if not (
            e.get('type') == 'cooldown' and e.get('sourceUserId') == uid
            and e.get('spellIndex') in (1, 2, 3))]
        for index in (1, 2, 3):
            effects.append(dict(type='cooldown', sourceUserId=uid, targetUserId=uid,
                                spellIndex=index, spellName='卡带切换',
                                remainingRounds=3, persistent=0))
        self._set_effects(effects)

    @staticmethod
    def _y14_action_q(table, state, action):
        key = (state, action)
        if key in table:
            return table[key]
        if action in {f'SKILL_{i}__T0' for i in (1, 2, 3, 11, 12, 19, 21, 22)}:
            base = action.rsplit('__', 1)[0]
            values = [table[(state, f'{base}__T{i}')] for i in range(1, 4)
                      if (state, f'{base}__T{i}') in table]
            if values:
                return sum(values) / len(values)
        return 0.0

    def _y14_event(self, uid, event):
        """Each resource trigger is counted once per physical unit and round."""
        if not self._is_y14(uid):
            return
        uid = self._resolve_uid(uid)
        char = self.get_char(uid)
        phase = getattr(char, 'phase', 2)
        ring_event = event in ('dodge', 'dash')
        if ring_event and phase != 3:
            return
        if not ring_event and (phase != 2 or (event == 'counter' and
                                             getattr(char, '_y14_mode', 'swift') != 'brave')):
            return
        kind = 'rings' if ring_event else 'limit_gauge'
        if not any(e.get('type') == 'resourceCounter' and e.get('counter_type') == kind
                   and e.get('targetUserId') == uid for e in self._get_effects()):
            return
        key = 'ring_gain' if ring_event else event
        round_num = (self._get_state() or {}).get('round', 1)
        ledger = getattr(char, '_y14_events', {})
        if ledger.get(key) == round_num:
            return
        char._y14_events = dict(ledger, **{key: round_num})
        self._mod_counter(uid, kind, 1, 3 if ring_event else 5)

    def _y14_damage(self, source, target, old_hp, *, counter=False):
        """Called after damage resolution, never for HP costs or healing."""
        if not self._is_y14(source) or self._battle_team_of(source) == self._battle_team_of(target):
            return
        if counter:
            # Winning the counter check is a separate trigger, even if a shield
            # subsequently absorbs its damage.
            self._y14_event(source, 'counter')
        actual = max(0, (old_hp or 0) - (self._get_combat_hp(target) or 0))
        # A substitute can restore 1 HP after an otherwise lethal hit.
        if actual <= 0:
            return
        self._y14_event(source, 'damage_dealt')

    def _y14_set_mode(self, uid, mode):
        uid = self._resolve_uid(uid)
        char = self.get_char(uid)
        self._set_effects([e for e in self._get_effects() if not (
            e.get('sourceUserId') == uid and e.get('spellIndex') in (11, 12))])
        char._y14_mode = mode
        index = 11 if mode == 'swift' else 12
        spell = next(s for s in self.load_spells(uid) if s['index'] == index)
        for i, eff in enumerate(spell['effects']):
            self._apply_target_effect(uid, uid, eff, spell, False, i)

    def _y14_initialize_phase(self, uid):
        """Install only Y14's phase passives; do not recast unrelated passives."""
        uid = self._resolve_uid(uid)
        char = self.get_char(uid)
        phase = getattr(char, 'phase', 2)
        index = {1: 8, 2: 13, 3: 17}.get(phase)
        if index:
            spell = next((s for s in self.load_spells(uid) if s['index'] == index), None)
            exists = any(e.get('sourceUserId') == uid and e.get('spellIndex') == index
                         and e.get('remainingRounds', 0) > 0 for e in self._get_effects())
            if spell and not exists and not (index == 8 and getattr(char, '_y14_substitute_used', False)):
                self._execute_misc_effect(uid, uid, spell['effects'][0], spell, True)
        if phase == 2 and not any(e.get('sourceUserId') == uid and e.get('spellIndex') in (11, 12)
                                  for e in self._get_effects()):
            self._y14_set_mode(uid, 'swift')

    def _y14_attack_profile(self, uid):
        if not self._is_y14(uid):
            return None
        char = self.get_char(uid)
        phase = getattr(char, 'phase', 2)
        index = {1: 4, 2: (10 if getattr(char, '_y14_mode', 'swift') == 'brave' else 9), 3: 18}.get(phase)
        spell = next((s for s in (char.spells or self.load_spells(uid)) if s['index'] == index), None)
        if not spell:
            return None
        eff = spell['effects'][0]
        return (_resolve_db(eff['伤害骰'], _get_db_str(char)), eff.get('射程', 2))

    def _move_explicit(self, uid, coord):
        """Validate before committing position, occupancy and movement cost together."""
        reject = lambda message: ActionExecutionResult(message, False, 'invalid_move', '附加')
        entry = self._unit_il_entry(uid, self._get_initiative())
        md = self._get_map()
        coord = str(coord).strip().upper()
        point = parse_coord(coord)
        if not entry or not md or not point:
            return reject('无效的移动坐标或地图')
        if not self._can_move(uid):
            return reject('当前状态无法移动')
        if not (0 <= point[0] < md['width'] and 0 <= point[1] < md['height']):
            return reject('移动坐标超出地图边界')
        old = entry.get('coord', '')
        if not parse_coord(old) or coord == old:
            return reject('移动目标与当前位置相同或起点无效')
        occupant = md.get('occupants', {}).get(coord)
        base = self._resolve_uid(uid)
        if occupant and self._resolve_uid(occupant) != base:
            return reject('移动目标已被占用')
        if md.get('obstacles', {}).get(coord):
            return reject('移动目标是障碍物')
        distance = chebyshev_dist(old, coord)
        used = entry.get('_move_used', 0)
        if not entry.get('isSummon'):
            used = max((e.get('_move_used', 0) for e in self._get_initiative()
                        if self._resolve_uid(e['userId']) == base), default=used)
        if distance > max(0, self._get_move_power(uid) - used):
            return reject('剩余移动力不足')
        if self._consume_move_power(uid, distance) != distance:
            return reject('剩余移动力不足')
        group = self._get_summon_group(uid)
        map_uid = group.get('representative_uid', uid) if group else base
        occupants = md.setdefault('occupants', {})
        if occupants.get(old) in (uid, base, map_uid):
            del occupants[old]
        occupants[coord] = map_uid
        self._set_map(md)
        if entry.get('isSummon'):
            self._set_summon_entry_fields(uid, coord=coord)
        else:
            il = self._get_initiative()
            for slot in il:
                if self._resolve_uid(slot['userId']) == base:
                    slot['coord'] = coord
            self._set_initiative(il)
        self._settle_zone_enters()
        return ActionExecutionResult(f'{self.get_char(uid).name} 移动至 {coord}', slot='附加')

    def _next_move_coord(self, uid):
        """Pick a legal one-step approach without mutating movement state."""
        entry = self._unit_il_entry(uid, self._get_initiative())
        md = self._get_map()
        if not entry or not md or not self._can_move(uid):
            return None
        if self._get_move_power(uid) - entry.get('_move_used', 0) < 1:
            return None
        point = parse_coord(entry.get('coord', ''))
        enemies = [e for e in self._enemy_target_entries(uid) if parse_coord(e.get('coord', ''))]
        if not point or not enemies:
            return None
        target = min(enemies, key=lambda e: chebyshev_dist(entry['coord'], e['coord']))
        tp = parse_coord(target['coord'])
        dc = (tp[0] > point[0]) - (tp[0] < point[0])
        dr = (tp[1] > point[1]) - (tp[1] < point[1])
        for dx, dy in ((dc, 0), (0, dr), (dc, dr)):
            col, row = point[0] + dx, point[1] + dy
            coord = format_coord(col, row)
            if (dx or dy) and 0 <= col < md['width'] and 0 <= row < md['height']:
                if coord not in md.get('occupants', {}) and not md.get('obstacles', {}).get(coord):
                    return coord
        return None

    def _execute_ai_command(self, uid, cmd):
        """Execute without charging actions; callers charge only committed results."""
        slot = '附加' if cmd.startswith('.a ') else '主动'
        reject = lambda text, reason: ActionExecutionResult(text, False, reason, slot)
        if self._get_my_actions(uid).get('主动', 0) <= 0:
            return reject('主动作已用尽', 'no_main_actions')
        if self._get_my_actions(uid).get(slot, 0) <= 0:
            return reject(f'{slot}动作已用尽', 'no_actions')
        if cmd.startswith('.a eat') or cmd.startswith('.a give'):
            giving = cmd.startswith('.a give')
            target = cmd.split(maxsplit=2)[2].strip() if len(cmd.split(maxsplit=2)) == 3 else uid
            target = self._resolve_uid(target)
            if giving and (target == self._resolve_uid(uid)
                           or self._battle_team_of(target) != self._battle_team_of(uid)):
                return reject('没有可给予的同队目标', 'invalid_target')
            text, ok = self._eat_cake(uid, target)
            return ActionExecutionResult(text, ok, '' if ok else 'no_cake', slot)
        if cmd.startswith('.a m '):
            return self._move_explicit(uid, cmd[5:])
        match = re.fullmatch(r'\.(a )?s(\d+)', cmd)
        if not match:
            return reject(f'未知动作: {cmd}', 'unknown_command')
        sn = int(match.group(2))
        if sn == 0 and not match.group(1):
            if not self._can_basic_attack(uid) or not self._enemy_target_entries(uid):
                return reject('没有可执行的普通攻击', 'invalid_attack')
            return ActionExecutionResult(self._basic_attack(uid), slot=slot)
        spells = self.load_spells(uid) or []
        spell = next((s for s in spells if s['index'] == sn), None)
        if not spell or not has_timing(spell.get('时机', '2'), '3' if match.group(1) else '2'):
            return reject('技能不存在或施放时机不符', 'invalid_skill')
        if self._revive_preflight(uid, spell):
            return reject(self._revive_preflight(uid, spell), 'no_revive_target')
        if self._get_cooldown(uid, sn) > 0 or not self._can_cast_active_skill(uid):
            return reject('技能冷却中或当前状态无法施法', 'invalid_skill')
        result = (self._additional_action(uid, f's{sn}') if match.group(1)
                  else self._use_skill(uid, sn, ''))
        return ActionExecutionResult(result, action_committed(result), getattr(result, 'reason', ''), slot)

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
        for e in self._battle_unit_entries():
            if (e.get('isSummon') and e.get('ownerId') == resolved
                and e.get('name') == redirect_name
                and (self._get_combat_hp(e['userId']) or 0) > 0
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
                    and grp.get('template_name') == template_name
                    and any((self._get_combat_hp(m) or 0) > 0 for m in grp.get('members', []))):
                return rep_uid
        return None

    def _get_summon_live_count(self, owner_id, template_name):
        """统计某主人某模板当前存活的召唤物数量（含不在先攻表里的隐藏组员）。

        只读：不剪枝组、不修改任何状态，可安全用于 AI 动作预检。
        """
        owner_base = self._resolve_uid(owner_id)
        seen = set()

        def _alive(sid):
            return (self._get_combat_hp(sid) or 0) > 0

        # 1) 先攻条目（代表/单体召唤）
        for e in self._get_initiative():
            if not e.get('isSummon'): continue
            if self._resolve_uid(e.get('ownerId', '')) != owner_base: continue
            if e.get('name', '') != template_name: continue
            sid = self._resolve_uid(e['userId'])
            if _alive(sid): seen.add(sid)
        # 2) 分组登记（含隐藏组员）
        for rep_uid, grp in self._summon_groups.items():
            if self._resolve_uid(grp.get('ownerId', '')) != owner_base: continue
            if grp.get('template_name') != template_name: continue
            for m in grp.get('members', []):
                if _alive(m): seen.add(self._resolve_uid(m))
        # Read-only ownership fallback for legacy members without ownerId.
        for m, mdata in self._summon_members.items():
            if mdata.get('name') != template_name: continue
            owner = mdata.get('ownerId')
            if owner is None:
                owner = (self._get_summon_group(m) or {}).get('ownerId')
            if self._resolve_uid(owner) != owner_base:
                continue
            if _alive(m): seen.add(self._resolve_uid(m))
        return len(seen)

    def _get_summon_quota(self, owner_id, template_name, template=None):
        """返回 (remaining, reason)。remaining 为本次还能新建的数量；None 表示不限。

        None=无限（不设上限），0=名额已满，需与"未设置"区分。
        """
        tmpl = template
        if tmpl is None:
            tmpl = self._get_summon_template(template_name) or {}
        max_sim = tmpl.get('max_simultaneous')
        max_total = tmpl.get('max_total_spawned')
        live = self._get_summon_live_count(owner_id, template_name)
        spawned = self._summon_total_spawned.get(
            (self._resolve_uid(owner_id), template_name), 0)

        sim_left = None if max_sim is None else max(0, int(max_sim) - live)
        tot_left = None if max_total is None else max(0, int(max_total) - spawned)
        if sim_left is None and tot_left is None:
            return None, ''
        if sim_left is not None and sim_left <= 0:
            return 0, f'{template_name}已达同时存活上限({max_sim})'
        if tot_left is not None and tot_left <= 0:
            return 0, f'{template_name}已达本场累计召唤上限({max_total})'
        candidates = [v for v in (sim_left, tot_left) if v is not None]
        return min(candidates), ''

    def _register_summon_spawned(self, owner_id, template_name):
        """成功创建后累加本场累计召唤数。死亡不返还。"""
        key = (self._resolve_uid(owner_id), template_name)
        self._summon_total_spawned[key] = self._summon_total_spawned.get(key, 0) + 1

    def _find_summons_by_field(self, team, field_name):
        results = []
        for entry in self._get_initiative():
            if not entry.get('isSummon'): continue
            if entry.get('team') != team: continue
            if (self._get_combat_hp(entry['userId']) or 0) <= 0: continue
            # entry['name'] is always the template name (display name lives in
            # displayName / Character.name) — resolve template fields from the registry.
            tmpl = self._get_summon_template(entry.get('name', ''))
            if tmpl and tmpl.get(field_name):
                results.append((entry['userId'], self.get_char(entry['userId'])))
        return results

    def _charge_moon_energy(self, team, amount):
        """Add amount to the first replenish_allies summon's MP on the given team."""
        if amount <= 0: return 0
        moons = self._find_summons_by_field(team, 'replenish_allies')
        if not moons: return 0
        moon_char = moons[0][1]
        cur_mp = moon_char.get_attr('魔力', 0) or 0
        moon_char.set_attr('魔力', cur_mp + amount)
        return amount

    # ── 血液资源系统 ──
    def _is_y17(self, uid):
        return self.get_char(self._resolve_uid(uid)).serial == 'Y17'

    def _y17_chant(self, uid):
        base = self._resolve_uid(uid)
        return next((e for e in self._get_effects() if e.get('type') == 'chanting'
                     and e.get('targetUserId') == base and e.get('bloodChant')
                     and e.get('remainingRounds', 0) > 0), None)

    def _y17_preflight(self, uid, spell, ai=False):
        """Read-only blood checks, shared by manual commands and both AI paths."""
        if not self._is_y17(uid):
            return ''
        hp = self._hp_safe(uid, 0)
        if hp <= 0 or self._is_dying(uid):
            return '昏迷或濒死时无法操纵血液'
        chant = self._y17_chant(uid)
        if chant:
            return '' if chant['spellIndex'] == spell['index'] else '正在吟唱，请继续同一技能'
        char = self.get_char(uid)
        idx = spell['index']
        blood = self._get_blood(uid)
        if idx == 7:
            target = self._y17_heal_target(uid)
            if blood < 500 or not target:
                return '需要500ml血液及范围内可治疗的轻伤目标'
        if idx in (3, 5):
            if any(e.get('sourceUserId') == self._resolve_uid(uid)
                   and e.get('spellIndex') == idx and e.get('remainingRounds', 0) > 0
                   for e in self._get_effects()):
                return '该凝血效果仍在持续'
        raw = spell.get('消耗mp', 0) or 0
        # Use maximum dice cost in AI so a randomly unaffordable cast is never chosen.
        mp = max_damage(str(raw)) if isinstance(raw, str) else raw
        if idx == 2:
            mp = max(1, char.get_attr('魔力', 0) // 2)
        if idx == 6:
            mp = math.ceil(char.get_attr('魔力上限', 26) * .2)
        if mp:
            mp = max(1, int(mp * self._get_buff_mp_cost_pct(uid)))
            mp = max(1, mp + self._get_buff_mp_cost_flat(uid))
        if char.get_attr('魔力', 0) < (mp if ai or not isinstance(raw, str) else 1):
            return '魔力不足'
        cost = spell.get('cost_blood_ml', 0) or 0
        if idx == 3:
            cost = 300 if blood >= 300 else 0
        if idx == 2:
            deficit = max(0, math.ceil(char.get_attr('魔力上限', 26) / 4) -
                          max(1, char.get_attr('魔力', 0) // 2))
            cost += deficit * 500
        if cost > blood and idx not in (2, 3, 5):
            return '血液不足，无法施放'
        if idx == 5 and cost > blood and math.ceil((cost-blood) / 200) > hp // 2:
            return '献血不能超过当前生命的一半'
        if ai:
            if idx == 3 and blood < 300:
                return '保留生命，避免防御性自残'
            if idx == 6 and hp <= 8:
                return '生命不足以承受污血战争的最高代价'
            if idx == 10 and char.get_attr('魔力上限', 26) - char.get_attr('魔力', 0) < 4:
                return '当前无需补充魔力'
            if idx == 4:
                allies = self._ally_targets(uid, include_self=False)
                if not allies or hp > char.get_attr('体力上限', hp) * .3:
                    return '保留献祭技用于有队友救援的绝境'
        return ''

    def _y17_heal_target(self, uid, selected=None):
        candidates = self._resolve_effect_targets(uid, selected, {'客体': 13, '作用半径': 5})
        candidates = [u for u in candidates if self._hp_safe(u, 0) > 0
                      and not self._is_dying(u)
                      and self._hp_safe(u) < self.get_char(u).get_attr('体力上限', 0)]
        if selected in candidates:
            return selected
        return min(candidates, key=lambda u: self._hp_safe(u) /
                   max(1, self.get_char(u).get_attr('体力上限', 1)), default=None)

    def _y17_prepare_spell(self, uid, target, original):
        """Build a per-cast plan; never mutate loaded spells or character definitions."""
        import copy
        spell = copy.deepcopy(original)
        char = self.get_char(uid)
        idx = spell['index']
        blood = self._get_blood(uid)
        if idx == 2:
            spent = max(1, char.get_attr('魔力', 0) // 2)
            deficit = max(0, math.ceil(char.get_attr('魔力上限', 26) / 4) - spent)
            spell['消耗mp'] = spent
            spell.pop('mp_cost_formula', None)
            spell['cost_blood_ml'] = 100 + deficit * 500
            # Blood substitution contributes to the minimum effective power.
            spell['_actual_mp_cost'] = spent + deficit
            spell['_blood_backlash'] = max(0, math.ceil((spell['cost_blood_ml'] - blood) / 500))
        elif idx == 3:
            if blood >= 300:
                units = min(blood // 300, 12)
                spell['cost_blood_ml'] = units * 300
                armor = units
            else:
                spell['cost_blood_ml'] = 0
                spell['cost_hp_pct_current'] = 50
                armor = max(1, self._hp_safe(uid) // 2) * 2
            spell['effects'][0]['辅助效果值'] = str(-armor)
        elif idx == 7:
            target = self._y17_heal_target(uid, target)
            missing = self.get_char(target).get_attr('体力上限', 0) - self._hp_safe(target)
            units = min(4, blood // 500, missing)
            spell['cost_blood_ml'] = units * 500
            spell['effects'][0]['回复hp'] = str(units)
        elif idx == 11:
            extra = min(2, max(0, (blood - 500) // 1000))
            spell['cost_blood_ml'] = 500 + extra * 1000
            spell['effects'] = [copy.deepcopy(spell['effects'][0]) for _ in range(1 + extra)]
        elif idx == 6:
            spell['消耗mp'] = math.ceil(char.get_attr('魔力上限', 26) * .2)
            spell.pop('cost_mp_pct_max', None)
            spell['effects'][0]['客体'] = 135
            spell['effects'][0]['_blood_wave'] = True
            # Wave pays its cost once; chain waves are executed by the plan itself.
            spell['_blood_chain'] = True
        spell['_y17_prepared'] = True
        return target, spell

    def _y17_continue_chant(self, uid, chant):
        chant['remainingRounds'] -= 1
        if chant['remainingRounds'] > 0:
            return f'继续吟唱【{chant["spellName"]}】，还需{chant["remainingRounds"]}次主动作\n'
        self._set_effects([e for e in self._get_effects() if e is not chant])
        spell = chant['stored_spell']
        target = chant['stored_target']
        if self._hp_safe(target, 0) <= 0:
            return '吟唱完成，原目标已失去战斗能力，法术消散。\n'
        if spell['index'] == 4:
            effects = self._get_effects()
            effects.append(dict(type='dying', targetUserId=self._resolve_uid(uid),
                remainingRounds=999, dyingRounds=0, excessDamage=0))
            self._set_effects(effects)
            self.get_char(uid)._blood_death_target = target
            self._set_combat_hp(uid, 0)
        plan = [(i, t) for i, e in enumerate(spell['effects'])
                for t in self._resolve_effect_targets(uid, target, e)]
        self._execute_spell_plan(uid, target, spell, plan, False,
                                 f'吟唱完成【{spell["name"]}】\n')
        return self._spell_plan_output

    def _y17_collect_hit(self, caster, target, hp_loss, spell):
        if not spell or not self._is_y17(caster) or hp_loss <= 0:
            return ''
        entry = self._unit_il_entry(target, self._get_initiative())
        if not entry or entry.get('isSummon') or self._battle_team_of(caster) == entry.get('team'):
            return ''
        char = self.get_char(caster)
        ledger = getattr(char, '_blood_harvested', {})
        base = self._resolve_uid(target)
        remaining = max(0, 1000 - ledger.get(base, 0))
        rate = 50 * spell.get('_actual_mp_cost', 0) if spell['index'] == 2 else hp_loss * 50
        amount = min(remaining, 300 if spell['index'] == 6 and self._hp_safe(target) <= 0 else rate)
        ledger[base] = ledger.get(base, 0) + amount
        char._blood_harvested = ledger
        self._add_blood(caster, amount)
        return f'  汲取血液 +{amount}ml（本目标累计{ledger[base]}ml，上限1000ml）\n' if amount else ''

    def _get_blood(self, uid):
        """Get current blood (ml) stored on a character."""
        char = self.get_char(uid)
        return getattr(char, '_blood_ml', 0) or 0

    def _add_blood(self, uid, amount):
        """Add blood (ml) to a character. Returns amount added."""
        if amount <= 0: return 0
        char = self.get_char(uid)
        cur = self._get_blood(uid)
        char._blood_ml = cur + amount
        return amount

    def _consume_blood(self, uid, amount):
        """Consume blood (ml) from a character. Returns True if sufficient."""
        cur = self._get_blood(uid)
        if cur < amount: return False
        char = self.get_char(uid)
        char._blood_ml = cur - amount
        return True

    def _check_chant_interrupt(self, uid):
        """Check if uid has interruptible chanting effects. If hit, interrupt them.
        Returns list of interrupt messages."""
        msgs = []
        effects = self._get_effects()
        interrupted = False
        for e in effects:
            if e.get('type') == 'chanting' and e.get('targetUserId') == uid \
               and e.get('interruptible') and e.get('remainingRounds', 0) > 0:
                char = self.get_char(uid)
                spell_name = e.get('spellName', '')
                # Apply interrupt penalty
                hp_pct = e.get('interrupt_hp_pct', 0) or 0
                mp_pct = e.get('interrupt_mp_pct', 0) or 0
                if hp_pct > 0:
                    cur_hp = self._get_combat_hp(uid) or 0
                    hp_loss = max(1, cur_hp * hp_pct // 100)
                    self._set_combat_hp(uid, max(0, cur_hp - hp_loss))
                    msgs.append(f'{char.name} 吟唱【{spell_name}】被打断！损失 {hp_loss} HP！')
                if mp_pct > 0:
                    cur_mp = char.get_attr('魔力', 0) or 0
                    mp_loss = max(1, cur_mp * mp_pct // 100)
                    char.set_attr('魔力', max(0, cur_mp - mp_loss))
                    msgs.append(f'{char.name} 损失 {mp_loss} MP！')
                if not hp_pct and not mp_pct:
                    msgs.append(f'{char.name} 吟唱【{spell_name}】被打断！')
                e['remainingRounds'] = 0  # Force expire
                interrupted = True
        if interrupted:
            self._set_effects(effects)
        return msgs

    def _has_linked_buff(self, uid, template_name):
        """Check if a character has a buff linked to the given summon template."""
        for e in self._get_effects():
            if e.get('type') == 'buff' and e.get('targetUserId') == uid \
               and e.get('linked_summon') == template_name \
               and e.get('remainingRounds', 0) > 0:
                return True
        return False

    def _has_moon_buff(self, uid):
        """事象的馈赠: uid 是否持有与月能召唤物(replenish_allies)链接的 buff。

        数据驱动：任意 active buff 带有 linked_summon 即视为月能链接
        （目前只有虚假之月的 事象的馈赠 使用该字段）。"""
        for e in self._get_effects():
            if e.get('type') == 'buff' and e.get('targetUserId') == uid \
               and e.get('linked_summon') \
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
        """Read members without changing lifecycle state during queries/display."""
        grp = self._get_summon_group(rep_or_uid)
        return list(grp.get('members', [])) if grp else [self._resolve_uid(rep_or_uid)]

    def _get_alive_summon_group_members(self, rep_or_uid):
        """Return list of alive member userIds in the group."""
        members = self._get_summon_group_members(rep_or_uid)
        return [m for m in members if (self._get_combat_hp(m) or 0) > 0]

    def _init_combat_hp(self, uid, hp):
        s = self.get_json(self._combat_hp_key(), {}); s[self._resolve_uid(uid)] = hp; self.set_json(self._combat_hp_key(), s)
    def _get_combat_hp(self, uid):
        return self.get_json(self._combat_hp_key(), {}).get(self._resolve_uid(uid))
    def _set_combat_hp(self, uid, hp, source_dmg=0, damage_source=None, is_counter=False):
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

        if old_hp is not None and new_hp < old_hp and source_dmg > 0:
            if self._is_y14(base):
                self._y14_event(base, 'damage_taken')
                if getattr(self.get_char(base), 'phase', 2) == 3:
                    self._mod_counter(base, 'rings', -1, 3)
        if old_hp is not None and damage_source is not None and (source_dmg > 0 or is_counter):
            self._y14_damage(damage_source, base, old_hp, counter=is_counter)

        # Track per-team last-death overflow for battle-end tiebreaker
        if old_hp is not None and old_hp > 0 and new_hp <= 0:
            entry = next((e for e in self._battle_unit_entries()
                          if self._resolve_uid(e.get('userId', '')) == base), None)
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
                    excess = max(0, source_dmg - old_hp) if source_dmg > 0 else max(0, -hp)
                    self._enter_dying_or_die(base, excess)
                else:
                    # Legacy instant death: capture the unit and its summons first.
                    self._record_graveyard_entry(base)
                    self._remove_summons_of_owner(base, record_collateral=True)
            elif entry and entry.get('isSummon'):
                # Summons do not enter dying; record before group maintenance removes them.
                self._record_graveyard_entry(base)


    # ---- Dying / Death helpers ----
    def _roll_check_d100(self, uid, skill_name=None, extra_tokens=None,
                         ignore_global=False, only_codes=None):
        """Unified entry for unit-attributable D100 checks (saves, skills).

        Every bonus/penalty source of that unit is folded in exactly once:
        command tokens, active buffs (including AUX34 全D100检定奖励骰) and
        live zone views. Checks with no skill name (CON saves, threshold
        rolls) take only AUX34 so attack-only modifiers never leak in.
        Non-D100 dice (damage/heal/count/cost) must never be routed here.
        """
        buffs = [] if ignore_global else self._get_active_buffs(uid)
        if only_codes is None and skill_name is None:
            only_codes = {34}
        return roll_d100(_calc_net_bp(buffs, '', skill_name,
                                      extra_tokens=extra_tokens,
                                      only_codes=only_codes))

    def _check_con_save(self, uid, difficulty='normal'):
        """COC CON saving throw. difficulty: 'normal'=need success, 'hard'=need hard success, etc.
        Returns True if the save succeeds (stays alive/dying)."""
        char = self.get_char(uid)
        con = char.get_attr('体质', 50)
        # Saving throws are not skill checks: only 全D100检定 dice apply.
        roll, detail = self._roll_check_d100(uid, only_codes={34})
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

    def _is_final_dead(self, uid):
        """True only for HP<=0 units that are not dying (and were not self-rescued).

        Turn loops must use this instead of raw HP when deciding to tear down an
        owner's summons: a dying character may still eat a cake and stay in play.
        """
        hp = self._get_combat_hp(uid)
        return hp is not None and hp <= 0 and not self._is_dying(uid)

    def _enter_dying_or_die(self, uid, excess_damage=0):
        """On HP≤0: CON save → dying state or death. Returns True if still alive (dying)."""
        if self._is_dying(uid): return True  # Already dying

        # Check for deathPrevention effect
        effects = self._get_effects()
        for e in effects:
            if e.get('type') == 'deathPrevention' and e.get('targetUserId') == uid and e.get('remainingRounds', 0) > 0:
                if self._is_y14(uid):
                    char = self.get_char(uid)
                    if getattr(char, 'phase', 2) != 1 or getattr(char, '_y14_substitute_used', False):
                        continue
                    if char.get_attr('魔力', 0) < 8:
                        continue
                    char.set_attr('魔力', char.get_attr('魔力') - 8)
                    char._y14_substitute_used = True
                    # Consume before transition; transition may initialize new effects.
                    e['remainingRounds'] = 0
                    self._set_effects(effects)
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
                if not self._is_y14(uid):
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
            # A character carrying a personal HP cake eats it immediately.
            self._auto_consume_hp_cake(uid)
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
            # Death: preserve a graveyard entry before cleanup removes the unit.
            self._record_graveyard_entry(uid)
            self._remove_summons_of_owner(uid, record_collateral=True)
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
    def _summon_group_key(self, uid):
        """Resolve a physical unit to its stable summon-group action key."""
        base = self._resolve_uid(uid)
        # Restored/transferred summons keep their original physical UID but may
        # have a new owner and therefore a different stable group key.
        member = getattr(self, '_summon_members', {}).get(base, {})
        explicit = member.get('summonGroupKey')
        if explicit and explicit in self._summon_groups:
            return explicit
        for rep_uid, grp in self._summon_groups.items():
            if base == rep_uid or base in grp.get('members', []):
                return rep_uid
        return base

    def _get_my_actions(self, uid):
        """Get actions for uid, resolving multi-action UIDs and summon groups."""
        key = self._summon_group_key(uid)
        return self._get_actions().get(key, {'主动': 0, '附加': 0})

    def _consume_action(self, uid, action_type='主动', amount=1):
        """Consume one action point for uid, resolving multi-action UIDs and summon groups."""
        key = self._summon_group_key(uid)
        actions = self._get_actions()
        my_acts = actions.get(key, {'主动': 0, '附加': 0})
        my_acts[action_type] = max(0, my_acts.get(action_type, 0) - amount)
        actions[key] = my_acts
        self._set_actions(actions)
        return my_acts
    def _get_summon_live_members(self, group_key):
        """Return (all_alive_members, actionable_members) for a summon group.

        只读、不剪枝。actionable 额外排除击晕/缴械等无法行动的状态。
        """
        grp = self._summon_groups.get(group_key)
        if not grp:
            alive = [group_key] if (self._get_combat_hp(group_key) or 0) > 0 else []
            return alive, [m for m in alive if self._summon_member_can_act(m)]
        alive = [m for m in grp.get('members', []) if (self._get_combat_hp(m) or 0) > 0]
        return alive, [m for m in alive if self._summon_member_can_act(m)]

    def _summon_member_can_act(self, member_uid):
        """成员能否发起攻击：存活 + 未击晕 + 未缴械（召唤物无主动技能）。"""
        if (self._get_combat_hp(member_uid) or 0) <= 0:
            return False
        if not self._can_basic_attack(member_uid):
            return False
        return True

    def _replace_initiative_preserving_actor(self, entries, replacements=None):
        """Keep the current actor (or its promoted representative) across edits."""
        old = self._get_initiative()
        state = self._get_state()
        if state and old:
            idx = min(state.get('activeIndex', 0), len(old) - 1)
            active = old[idx]['userId']
            wanted = (replacements or {}).get(active, active)
            new_idx = next((i for i, e in enumerate(entries) if e['userId'] == wanted), None)
            if new_idx is None:
                # If the actor was removed, retain its next surviving successor.
                survivors = {e['userId']: i for i, e in enumerate(entries)}
                new_idx = next((survivors[e['userId']]
                                for e in old[idx + 1:] + old[:idx]
                                if e['userId'] in survivors), 0)
            state['activeIndex'] = new_idx
            self._set_state(state)
        self._set_initiative(entries)

    def _ensure_group_representative(self, group_key):
        """Repair the single group slot without rolling or granting any actions."""
        group_key = self._summon_group_key(group_key)
        grp = self._summon_groups.get(group_key)
        if not grp:
            return None
        members = grp.get('members', [])
        alive = [m for m in members if (self._get_combat_hp(m) or 0) > 0]
        if not alive:
            return None
        rep = grp.get('representative_uid') or group_key
        new_rep = rep if rep in alive else alive[0]
        il = self._get_initiative()
        member_ids = set(members) | {group_key, rep}
        slots = [e for e in il if e['userId'] in member_ids]
        slot = next((e for e in slots if e['userId'] == rep), None)
        if slot is None and slots:
            slot = slots[0]
        old_uid = slot['userId'] if slot else rep
        original_slot = slot
        if slot is not None:
            slot = dict(slot)
            # Save mutable resources on the physical old member before promotion.
            old_data = self._summon_members.get(old_uid)
            if old_data is not None:
                old_data.update({k: v for k, v in slot.items()
                                 if k in self._summon_resource_fields()})
            grp['initiative_entry'] = dict(slot)
        saved = grp.get('initiative_entry', {})
        mdata = self._summon_members.get(new_rep, {})
        coord = (slot or saved).get('coord') or mdata.get('coord', '')
        if slot is None:
            slot = dict(saved or self._get_summon_entry(new_rep) or {})
            slot.update({k: v for k, v in mdata.items() if k in self._summon_resource_fields()})
            slot.pop('_synthetic', None)
            slot.setdefault('dex', mdata.get('dex', 50))
            slot.setdefault('initRoll', 100)
            slot.setdefault('initRank', 0)
            slot.setdefault('actionIdx', 0)
        if old_uid != new_rep:
            view = self._get_summon_entry(new_rep) or {}
            # Member combat resources must not be inherited from the dead rep.
            slot.update({k: v for k, v in view.items()
                         if k in self._summon_resource_fields()})
            slot['skills'] = mdata.get('skills', slot.get('skills', []))
            slot['name'] = mdata.get('name', slot.get('name', grp['template_name']))
            slot['displayName'] = mdata.get('display_name', slot.get('displayName', slot['name']))
        slot.update(userId=new_rep, baseUserId=new_rep, isSummon=True,
                    ownerId=grp['ownerId'], coord=coord)
        # Reconstructed/promoted slots must always carry team/name; turn loops
        # and AI state encoding index these unguarded.
        if not slot.get('team'):
            owner_entry = self._unit_il_entry(grp.get('ownerId', ''), self._get_initiative())
            slot['team'] = (mdata.get('team')
                            or (owner_entry or {}).get('team')
                            or self._battle_team_of(grp.get('ownerId', '')))
        slot.setdefault('name', mdata.get('name', grp.get('template_name', new_rep)))
        slot.setdefault('displayName', slot['name'])
        slot.setdefault('isSummon', True)
        slot.pop('_synthetic', None)
        grp['representative_uid'] = new_rep
        # Preserve slot identity/position when possible, reconstruct in saved order otherwise.
        if slots:
            entries = [slot if e is original_slot else e for e in il
                       if e is original_slot or e['userId'] not in member_ids]
        else:
            entries = il + [slot]
            entries.sort(key=lambda e: (-e.get('initRank', 0), -e.get('dex', 0), e.get('initRoll', 100)))
        self._replace_initiative_preserving_actor(entries, {old_uid: new_rep})
        grp['initiative_entry'] = dict(slot)
        actions = self._get_actions()
        if group_key not in actions:
            legacy_key = next((m for m in (rep, new_rep) if m in actions), None)
            if legacy_key is not None:
                actions[group_key] = actions[legacy_key]
        for m in member_ids - {group_key}:
            actions.pop(m, None)
        self._set_actions(actions)
        for m in members:
            if m in self._summon_members:
                self._summon_members[m]['coord'] = coord
        md = self._get_map()
        if md:
            occupants = md.setdefault('occupants', {})
            footprint = {coord} if coord else set()
            tmpl = self._get_summon_template(slot.get('name', '')) or {}
            point = parse_coord(coord) if coord else None
            if point and tmpl.get('size_2x2'):
                footprint = {format_coord(point[0] + dc, point[1] + dr)
                             for dc in range(2) for dr in range(2)
                             if point[0] + dc < md.get('width', 26)
                             and point[1] + dr < md.get('height', 99)}
            for cell, uid in list(occupants.items()):
                if uid in member_ids:
                    del occupants[cell]
            for cell in footprint:
                if cell not in occupants:
                    occupants[cell] = new_rep
            self._set_map(md)
        return new_rep

    def _reconcile_summon_groups(self):
        """Maintain groups at mutation boundaries; never refill mid-round pools."""
        if getattr(self, '_reconciling_summons', False):
            return
        self._reconciling_summons = True
        try:
            for key in list(self._summon_groups):
                self._ensure_group_representative(key)
            dead_keys = [k for k, g in self._summon_groups.items()
                         if not any((self._get_combat_hp(m) or 0) > 0 for m in g.get('members', []))]
            dead_ids = {m for k in dead_keys for m in (
                self._summon_groups[k].get('members', []) +
                [self._summon_groups[k].get('representative_uid', k), k])}
            # Advance the active dead slot through the normal round-boundary path.
            state = self._get_state()
            il = self._get_initiative()
            if (state and il and state.get('phase') == 'active'
                    and not getattr(self, '_advancing_turn', False)
                    and not getattr(self, '_settling_round', False)):
                active = il[min(state.get('activeIndex', 0), len(il) - 1)]['userId']
                if active in dead_ids:
                    self._end_turn(active)
            for key in dead_keys:
                self._summon_groups.pop(key, None)
            for uid in dead_ids:
                self._summon_members.pop(uid, None)
            if dead_ids:
                self._replace_initiative_preserving_actor(
                    [e for e in self._get_initiative() if e['userId'] not in dead_ids])
                acts = self._get_actions()
                for uid in dead_ids:
                    acts.pop(uid, None)
                self._set_actions(acts)
            # Prune deceased members only after their representative has been repaired.
            for grp in self._summon_groups.values():
                deceased = [m for m in grp['members'] if (self._get_combat_hp(m) or 0) <= 0]
                grp['members'] = [m for m in grp['members'] if m not in deceased]
                for uid in deceased:
                    self._summon_members.pop(uid, None)
                dead_ids.update(deceased)
            md = self._get_map()
            if md and dead_ids:
                md['occupants'] = {c: uid for c, uid in md.get('occupants', {}).items()
                                   if uid not in dead_ids}
                self._set_map(md)
        finally:
            self._reconciling_summons = False

    @staticmethod
    def _summon_resource_fields():
        return ('zone_cooldown', 'summon_mp', 'shield_block', 'shield_block_hp',
                'shield_block_rate', 'react_dodge_w', 'react_counter_w', 'react_block_w',
                'ignore_unreactable_block', 'flying', 'ignited', 'ignite_dmg_dice',
                'ignite_tick_dmg', 'battle_spirit_penalty_dice')

    def _set_summon_entry_fields(self, uid, **fields):
        """Persist physical member resources, including for synthetic target views."""
        base = self._resolve_uid(uid)
        il = self._get_initiative()
        entry = next((e for e in il if self._resolve_uid(e['userId']) == base), None)
        if entry is not None:
            entry.update(fields)
            self._set_initiative(il)
        member = self._summon_members.get(base)
        if member is not None:
            member.update(fields)

    def _register_summon_member_state(self, sid, template, owner_id):
        """Initialize each physical member's resources, including hidden additions."""
        data = self._summon_members[sid]
        data['ownerId'] = self._resolve_uid(owner_id)
        defaults = {
            'zone_cooldown': 0, 'summon_mp': template.get('MP', 0),
            'shield_block': template.get('shield_block', 0),
            'shield_block_hp': template.get('shield_block', 0),
            'shield_block_rate': template.get('shield_block_rate', 0.70),
            'react_dodge_w': template.get('react_dodge', 50),
            'react_counter_w': template.get('react_counter', 50),
            'react_block_w': template.get('react_block', 0),
            'ignore_unreactable_block': template.get('ignore_unreactable_block', 0),
        }
        for field, value in defaults.items():
            data.setdefault(field, value)
        char = self.get_char(sid)
        char.attrs.setdefault('体力上限', template.get('HP', 10))
        char.attrs.setdefault('魔力', template.get('MP', 0))
        char.attrs.setdefault('魔力上限', template.get('MP', 0))
        char.attrs.setdefault('理智', template.get('SAN', 0))
        if template.get('无主动行动'):
            char.set_attr('无主动行动', 1)
        effects = self._get_effects()
        for name in template.get('permanent_statuses', []):
            if not any(e.get('targetUserId') == sid and e.get('auxType') == name for e in effects):
                effects.append({'type': 'buff', 'auxType': name, 'auxVal': '0', 'auxCode': 0,
                                'remainingRounds': 999, 'sourceUserId': owner_id,
                                'targetUserId': sid, 'spellName': f'永久{name}', 'spellIndex': 0,
                                'persistent': 1, 'stackable': 0})
        self._set_effects(effects)
        group_key = self._summon_group_key(sid)
        group = self._summon_groups[group_key]
        group.setdefault('representative_uid', group_key)
        entry = self._unit_il_entry(group['representative_uid'], self._get_initiative())
        if entry:
            group['initiative_entry'] = dict(entry)

    def _reset_round_actions(self, character_main=None):
        """Allocate one pool per group, using its living representative's budget."""
        acts = {}
        for entry in self._get_initiative():
            uid = entry.get('baseUserId', entry['userId'])
            key = self._summon_group_key(uid)
            if key not in acts:
                main = (self._calc_main_actions(uid)
                        if entry.get('isSummon') or character_main is None or self._is_y14(uid) else character_main)
                acts[key] = {'主动': main, '附加': 3}
        self._set_actions(acts)

    def _execute_summon_group_action(self, group_key, execute_member):
        """执行一次共享组动作：快照→扣一次组动作→逐成员结算。

        execute_member(member_uid) -> (lines, halted)
          lines: 该成员本次行动的日志行
          halted: True 表示因等待玩家反应而中断，需保留进度供恢复
        返回 dict：{'lines': [...], 'action_spent': bool, 'halted': bool,
                   'pending': list, 'remaining': int}
        """
        grp = self._summon_groups.get(group_key)
        if not grp:
            return {'lines': [], 'action_spent': False, 'halted': False,
                    'pending': [], 'remaining': 0}
        rep = self._ensure_group_representative(group_key)
        if rep is None:
            return {'lines': [], 'action_spent': False, 'halted': False,
                    'pending': [], 'remaining': 0}
        members = grp.get('members', [])
        # 快照：本批次开始时的存活成员；新成员不插入当前快照
        snapshot = [m for m in members if (self._get_combat_hp(m) or 0) > 0]
        actionable = [m for m in snapshot if self._summon_member_can_act(m)]
        if not actionable:
            return {'lines': [], 'action_spent': False, 'halted': False,
                    'pending': [], 'remaining': 0}
        acts = self._get_my_actions(group_key)
        if acts.get('主动', 0) <= 0:
            return {'lines': [], 'action_spent': False, 'halted': False,
                    'pending': [], 'remaining': 0}
        # 扣一次共享动作
        self._consume_action(group_key, '主动')
        lines = []
        pending = []
        halted = False
        for m in snapshot:
            # 逐成员重新验证：轮到之前死亡/被控的成员直接跳过，不占用出手
            if not self._summon_member_can_act(m):
                continue
            res = execute_member(m)
            if res is None:
                continue
            m_lines, m_halt, m_pending = res if isinstance(res, tuple) else (res, False, None)
            lines.extend(m_lines or [])
            if m_halt:
                halted = True
                pending.append({'member': m, 'data': m_pending})
                break
        remaining = self._get_my_actions(group_key).get('主动', 0)
        return {'lines': lines, 'action_spent': True, 'halted': halted,
                'pending': pending, 'remaining': remaining}

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
        return max(0, self._apply_attr_mods(uid, base, 'MOV'))

    def _get_action_power(self, uid):
        """获取角色的行动力(影响反应/先攻)。默认=行动力=MOV。"""
        char = self.get_char(uid)
        base = char.get_attr('行动力', 8)
        return max(1, self._apply_attr_mods(uid, base, 'MOV'))

    def _get_effective_mov(self, uid, include_zone=True):
        """统一计算有效MOV。
        结算顺序: 基础MOV + buff/领域增减 → 飞行+8 → 减速倍率 → floor, min0。
        召唤物优先读取 MOV 属性，角色用 行动力(默认8)。
        include_zone=False omits the live zone aura view (used to isolate the
        portion of a MOV change that ordinary buffs are responsible for)."""
        import math
        resolved = self._resolve_uid(uid)
        char = self.get_char(resolved)
        # 基础 MOV：召唤物用 MOV 属性，角色用 行动力
        base = char.get_attr('MOV', None)
        if base is None:
            base = char.get_attr('行动力', 8)
            if char.get_attr('分离行动力移动力', 0):
                base = char.get_attr('移动力', base)
        # Buff修正按属性名（MOV/行动力/移动力同属 MOV 组，其它名字不参与）
        base += self._apply_buff_attr_mod(uid, 'MOV', include_zone=include_zone)
        base += self._zone_attr_mod(uid, 'MOV')
        # 飞行: +8
        if self._is_flying(uid):
            base += 8
        # 减速倍率
        slow = self._get_slow_multiplier(uid)
        result = int(math.floor(max(0, base) * slow))
        return result

    def _calc_main_actions(self, uid, include_zone=True):
        """计算每回合主动作数。公式: max(1, (effective_MOV + 4) // 5 + 额外行动数)，无上限。
        区间映射: MOV 0-5→1动, 6-10→2动, 11-15→3动, 16-20→4动, 21-25→5动, ...
        若角色/召唤物设置了 无主动行动=1，返回 0。"""
        char = self.get_char(self._resolve_uid(uid))
        if char.get_attr('无主动行动', 0) == 1:
            return 0
        effective_mov = self._get_effective_mov(uid, include_zone=include_zone)
        extra = char.get_attr('额外行动数', None)
        if extra is None:
            extra = char.get_attr('回合行动数', 0)
        return max(1, (effective_mov + 4) // 5 + extra)

    def _sync_actions_from_mov(self, uid, old_max=None, new_max=None):
        """当普通 buff 的 MOV 变化时，同步调整当前剩余主行动数。

        Zone auras are deliberately excluded: entering/leaving a zone changes
        effective MOV immediately but must not reissue or claw back the action
        pool already allocated for this round. The previous implementation
        compared against a usually-unset ``_max_主动`` marker, which silently
        invented deltas; callers now pass explicit pre/post action ceilings.
        """
        il = self._get_initiative()
        base = self._resolve_uid(uid)
        for e in il:
            if e.get('baseUserId', e['userId']) == base:
                e.pop('_cached_buff_mov_mod', None)
        self._set_initiative(il)
        actions = self._get_actions()
        lookup = self._summon_group_key(base)
        if lookup not in actions:
            return
        if old_max is None:
            old_max = actions[lookup].get('_max_主动')
        if old_max is None:
            return
        if new_max is None:
            new_max = self._calc_main_actions(uid, include_zone=False)
        delta = new_max - old_max
        if delta != 0:
            actions[lookup]['主动'] = max(0, actions[lookup].get('主动', 0) + delta)
            actions[lookup]['_max_主动'] = new_max
            self._set_actions(actions)

    def _get_buff_move_mod(self, uid):
        """从活跃buff中提取MOV/行动力/移动力修改量（实时，含领域视图）。"""
        return self._apply_buff_attr_mod(uid, 'MOV')

    def _apply_buff_attr_mod(self, uid, attr_name, include_zone=True):
        """Sum named attribute modifiers from active buffs (MOV group unified).

        ``技能加减值`` like 'MOV+5' / '闪避-10' only ever changes the named
        attribute group; it is never a blanket skill-value bonus.
        """
        target = _attr_group(attr_name)
        total = 0
        for b in self._get_active_buffs(uid, include_zone=include_zone):
            for name, delta in _parse_attr_mods(b.get('skillMod', '')):
                if _attr_group(name) == target:
                    total += delta
        return total

    def _zone_attr_mod(self, uid, attr_name):
        """Temporary per-round zone penalty for a named attribute group.

        Non-stackable zones leave a 'strongest' penalty; stackable zones sum.
        Values live on the initiative entry and are refreshed each round.
        """
        target, strongest, stacked = _attr_group(attr_name), 0, 0
        if self._has_status(uid, '隔离'):
            return 0
        for effect in self._get_effects():
            if effect.get('type') != 'zone' or effect.get('remainingRounds', 0) == 0:
                continue
            if not self._zone_covers_uid(effect, uid):
                continue
            mods = _parse_attr_mods(effect.get('attributeDebuff', ''))
            if self._resolve_uid(uid) in effect.get('_triggered_ids', []):
                mods += _parse_attr_mods(effect.get('on_enter_attr_debuff', ''))
            for name, delta in mods:
                if _attr_group(name) != target or delta >= 0:
                    continue
                if effect.get('stackable'):
                    stacked += abs(delta)
                else:
                    strongest = max(strongest, abs(delta))
        return -(strongest + stacked)

    def _settle_zone_enters(self):
        effects = self._get_effects()
        for effect in effects:
            if effect.get('type') != 'zone' or not (effect.get('on_enter_mp_drain_pct') or effect.get('on_enter_attr_debuff')):
                continue
            inside = {self._resolve_uid(e['userId']) for e in self._battle_unit_entries()
                      if self._zone_covers_uid(effect, e['userId']) and not self._has_status(e['userId'], '隔离')}
            previous = set(effect.get('_entered_ids', []))
            triggered = set(effect.get('_triggered_ids', [])) & inside
            for uid in sorted(inside - previous):
                if random.random() > effect.get('on_enter_trigger_rate', 1):
                    continue
                triggered.add(uid)
                char = self.get_char(uid)
                amount = int(char.get_attr('魔力', 0) * effect.get('on_enter_mp_drain_pct', 0))
                char.set_attr('魔力', char.get_attr('魔力', 0) - amount)
                source = self.get_char(effect.get('sourceUserId', ''))
                if source and amount:
                    source.set_attr('魔力', min(source.get_attr('魔力上限', 0), source.get_attr('魔力', 0) + amount))
            effect['_entered_ids'], effect['_triggered_ids'] = sorted(inside), sorted(triggered)
        self._set_effects(effects)

    def _apply_attr_mods(self, uid, base, attr_name):
        """Apply buffs + live zone penalties to one named attribute group."""
        adjusted = base + self._apply_buff_attr_mod(uid, attr_name)
        adjusted += self._zone_attr_mod(uid, attr_name)
        return adjusted

    def _apply_buff_skill_mod(self, uid, base_val, skill_name=None):
        """Resolve a skill/attribute check value from buffs.

        AUX 15 (generic skill success %) and AUX 20 (skill multiplier) still
        apply broadly; named ``技能加减值`` only applies when ``skill_name``
        matches (``MOV`` group unified). Zone attribute debuffs apply to named
        combat skills / attributes as well.
        """
        buffs = self._get_active_buffs(uid); adjusted = base_val
        for b in buffs:
            # AUX 15: numeric modifier
            if b.get('auxCode') == 15 and b.get('auxVal') not in (None,''):
                try: adjusted += int(b['auxVal'])
                except: pass
        if skill_name:
            adjusted += self._apply_buff_attr_mod(uid, skill_name)
            adjusted += self._zone_attr_mod(uid, skill_name)
        # AUX 20: skill value multiplier (applied after flat modifiers)
        mult = self._get_buff_skill_mult(uid)
        adjusted = int(adjusted * mult)
        return max(1, adjusted)

    def _consume_move_power(self, uid, amount):
        """消耗移动力。返回实际可消耗量。
        多动角色的所有行动槽共享 _move_used。"""
        if not self._can_move(uid):
            return 0  # 禁锢/击晕禁止移动
        il = self._get_initiative()
        entry = self._unit_il_entry(uid, il)
        if not entry: return 0
        base_uid = entry.get('baseUserId', uid)
        if entry.get('_synthetic'):
            used = entry.get('_move_used', 0)
            consumed = min(amount, max(0, self._get_move_power(uid) - used))
            self._set_summon_entry_fields(uid, _move_used=used + consumed)
            return consumed
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
        if consumed > 0 and self._is_y14(uid):
            char = self.get_char(self._resolve_uid(uid))
            if getattr(char, '_y14_dash_round', None) == (self._get_state() or {}).get('round', 1):
                char._y14_dash_distance = getattr(char, '_y14_dash_distance', 0) + consumed
                if char._y14_dash_distance >= 8:
                    self._y14_event(uid, 'dash')
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
        for data in self._summon_members.values():
            data['_move_used'] = 0

    def _move_to_attack_range(self, uid, target_id, atk_range):
        """移动到可攻击目标的位置。返回 (can_attack, original_coord)。

        禁锢/击晕只能阻止「移动」，不能阻止原地攻击：先解析坐标并判断是否
        已在射程内；确实需要接近时才检查移动权限。"""
        il = self._get_initiative()
        my_entry = self._unit_il_entry(uid, il)
        t_entry = self._unit_il_entry(target_id, il)
        if not my_entry or not t_entry: return (False, '')
        my_coord = my_entry.get('coord', '')
        t_coord = t_entry.get('coord', '')
        if not my_coord or not t_coord: return (False, '')
        mp, tp = parse_coord(my_coord), parse_coord(t_coord)
        if not mp or not tp: return (False, '')
        dist = max(abs(mp[0]-tp[0]), abs(mp[1]-tp[1]))
        if dist <= atk_range:
            return (True, my_coord)  # 已在射程内：禁锢也能原地攻击
        # ── 只有需要接近时才检查禁锢/击晕 ──
        if not self._can_move(uid):
            return (False, my_coord)
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
            group = self._get_summon_group(uid)
            occupant_uid = group.get('representative_uid', uid) if group else uid
            if map_data['occupants'].get(my_coord) == occupant_uid:
                del map_data['occupants'][my_coord]
            map_data['occupants'][new_coord] = occupant_uid
            self._set_map(map_data)
            my_entry['coord'] = new_coord
            if group:
                self._set_summon_entry_fields(occupant_uid, coord=new_coord)
                for member in group['members']:
                    self._summon_members[member]['coord'] = new_coord
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

    def add_item_to_inventory(self, uid, item_name, count=1, metadata=None):
        """Add items, preserving source/consumption metadata on the stack."""
        char = self.get_char(uid)
        meta = dict(metadata or {})
        for entry in char.inventory:
            if entry['item'] == item_name and all(
                    entry.get(k) == v for k, v in meta.items()):
                entry['count'] += count
                return
        new_entry = {'item': item_name, 'count': count}
        new_entry.update(meta)
        char.inventory.append(new_entry)

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
        ITEM_TEMPLATES = getattr(self, '_item_templates_snapshot', ITEM_TEMPLATES)
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

    def _record_graveyard_entry(self, uid, reason='death'):
        """Store enough state to let a data-configured revive effect restore a unit."""
        import copy
        base = self._resolve_uid(uid)
        state = self._get_state() or {}
        entry = self._unit_il_entry(base, self._get_initiative())
        char = self.get_char(base)
        if char is None:
            return
        grave = getattr(self, '_graveyard', None)
        if grave is None:
            grave = {}
            self._graveyard = grave
        if any(g.get('uid') == base and g.get('revived') is False for g in grave.values()):
            return
        member = self._summon_members.get(base) if (entry or {}).get('isSummon') else None
        grave[base] = {
            'uid': base, 'team': ((entry or {}).get('team') or (member or {}).get('team') or self._battle_team_of(base)),
            'round': int(state.get('round', 1)),
            'entry': copy.deepcopy(entry) if entry else None,
            'member': copy.deepcopy(member) if member else None,
            'ownerId': (entry or {}).get('ownerId') or (member or {}).get('ownerId'),
            'originalOwnerId': (entry or {}).get('originalOwnerId'),
            'isSummon': bool((entry or {}).get('isSummon')),
            'attrs': copy.deepcopy(char.attrs), 'str_attrs': copy.deepcopy(char.str_attrs),
            'spells': copy.deepcopy(char.spells), 'inventory': copy.deepcopy(char.inventory),
            'name': char.name, 'serial': char.serial, 'phase': getattr(char, 'phase', 1),
            'hs_transformed': getattr(char, 'hs_transformed', False),
            'once_consumed': list(getattr(char, '_once_consumed', set()) or []),
            'revived': False, 'reason': reason,
        }

    def _revive_candidates(self, caster_id, eff):
        """Return graveyard entries selectable by a revive effect."""
        grave = getattr(self, '_graveyard', {}) or {}
        cur_round = int((self._get_state() or {}).get('round', 1))
        window = int(eff.get('死亡轮窗口', 0) or 0)
        caster_base = self._resolve_uid(caster_id)
        caster_team = self._battle_team_of(caster_id)
        allow_summon = int(eff.get('复活主体', 1) or 0) == 1
        selected = getattr(self, '_revive_selected_uid', None)
        out = []
        for g in grave.values():
            if g.get('revived'):
                continue
            if selected and g.get('uid') != selected:
                continue
            if g['uid'] == caster_base or g.get('team') != caster_team:
                continue
            if g.get('isSummon') and not allow_summon:
                continue
            if window and cur_round - int(g.get('round', 0)) > window:
                continue
            out.append(g)
        return out

    def _find_free_coord_near(self, caster_id):
        il = self._get_initiative()
        caster_entry = self._unit_il_entry(caster_id, il)
        base = parse_coord((caster_entry or {}).get('coord', '')) or (1, 1)
        md = self._get_map() or {}
        occupied = md.get('occupants', {})
        for radius in range(0, 10):
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy)) != radius:
                        continue
                    c = format_coord(base[0] + dx, base[1] + dy)
                    if c not in occupied:
                        return c
        return format_coord(base)

    def _restore_graveyard_unit(self, caster_id, grave_entry, eff):
        """Preflight, then restore one unit without resetting resources/actions."""
        import copy
        uid = self._resolve_uid(grave_entry['uid'])
        caster = self.get_char(caster_id)
        ratio = float(eff.get('复活回复比例', 0.4) or 0.4)
        if not 0 < ratio <= 1 or grave_entry.get('revived'):
            return None
        state = self._get_state() or {}
        now_round = int(state.get('round', 1))
        window = int(eff.get('死亡轮窗口', 0) or 0)
        if window and now_round - int(grave_entry.get('round', 0)) > window:
            return None
        caster_team = self._battle_team_of(caster_id)
        if grave_entry.get('team') != caster_team or uid == self._resolve_uid(caster_id):
            return None
        if self._get_combat_hp(uid) not in (None, 0):
            return None

        is_summon = bool(grave_entry.get('isSummon'))
        old_entry = copy.deepcopy(grave_entry.get('entry') or {})
        old_member = copy.deepcopy(grave_entry.get('member') or {})
        name = old_entry.get('name') or old_member.get('name') or ''
        source_owner = self._resolve_uid(grave_entry.get('ownerId') or '') if grave_entry.get('ownerId') else ''
        new_owner = self._resolve_uid(caster_id) if is_summon else old_entry.get('ownerId')
        template = self._get_summon_template(name) or {} if is_summon else {}
        attrs = grave_entry.get('attrs') or {}
        max_hp = int(attrs.get('体力上限', 1) or 1)
        revive_hp = max(1, min(max_hp, int(max_hp * ratio)))

        md = self._get_map() or {}
        coord = old_entry.get('coord') or old_member.get('coord') or ''
        size = 2 if template.get('size_2x2') else 1
        def footprint_at(cell):
            point = parse_coord(cell) if cell else None
            if not point:
                return None
            cells = {format_coord(point[0] + dx, point[1] + dy)
                     for dx in range(size) for dy in range(size)}
            if any(c not in md.get('occupants', {}) and c not in md.get('obstacles', {})
                   for c in cells):
                occupied_by = {self._resolve_uid(md['occupants'][c]) for c in cells
                               if c in md.get('occupants', {})}
                if not occupied_by or occupied_by == {uid}:
                    return cells
            return None
        footprint = footprint_at(coord)
        if footprint is None:
            coord = self._find_free_coord_near(caster_id)
            footprint = footprint_at(coord)
        if footprint is None:
            return None

        group_key = None
        if is_summon:
            owner_base = self._resolve_uid(new_owner)
            # Test Y7's live quota against a hypothetical state where this UID
            # has left the former owner; no mutations are committed on failure.
            saved_groups, saved_members = self._summon_groups, self._summon_members
            saved_counts = self._summon_total_spawned
            try:
                groups = {k: dict(g, members=list(g.get('members', []))) for k, g in saved_groups.items()}
                for key, group in list(groups.items()):
                    if key == uid or uid in group.get('members', []):
                        group['members'] = [m for m in group.get('members', []) if m != uid]
                        if not group['members']:
                            groups.pop(key, None)
                        elif group.get('representative_uid') == uid:
                            group['representative_uid'] = group['members'][0]
                members = {k: dict(v) for k, v in saved_members.items() if k != uid}
                counts = dict(saved_counts)
                prior = grave_entry.get('previousTransfers', [])
                if owner_base not in prior:
                    key = (owner_base, name)
                    counts[key] = counts.get(key, 0) + 1
                self._summon_groups, self._summon_members, self._summon_total_spawned = groups, members, counts
                quota, _reason = self._get_summon_quota(owner_base, name, template)
                group_key = next((k for k, g in groups.items()
                                  if g.get('template_name') == name
                                  and self._resolve_uid(g.get('ownerId', '')) == owner_base
                                  and any((self._get_combat_hp(m) or 0) > 0 for m in g.get('members', []))), None)
            finally:
                self._summon_groups, self._summon_members, self._summon_total_spawned = saved_groups, saved_members, saved_counts
            if quota == 0:
                return None
            if group_key is None:
                group_key = uid

        # All validation passed; now restore the saved unit and transfer ownership.
        char = self.get_char(uid)
        char.attrs = copy.deepcopy(attrs)
        char.str_attrs = copy.deepcopy(grave_entry.get('str_attrs') or {})
        char.spells = copy.deepcopy(grave_entry.get('spells') or [])
        char.inventory = copy.deepcopy(grave_entry.get('inventory') or [])
        char.name = grave_entry.get('name') or char.name
        char.serial = grave_entry.get('serial') or char.serial
        char.phase = grave_entry.get('phase', 1)
        char.hs_transformed = grave_entry.get('hs_transformed', False)
        char._once_consumed = set(grave_entry.get('once_consumed') or [])

        if is_summon:
            for key, group in list(self._summon_groups.items()):
                if key == uid or uid in group.get('members', []):
                    group['members'] = [m for m in group.get('members', []) if m != uid]
                    if not group['members']:
                        self._summon_groups.pop(key, None)
                    elif group.get('representative_uid') == uid:
                        group['representative_uid'] = group['members'][0]
            old_member.update({'userId': uid, 'name': name, 'ownerId': new_owner,
                               'team': caster_team, 'coord': coord,
                               'summonGroupKey': group_key})
            self._summon_members[uid] = old_member
            if group_key not in self._summon_groups:
                self._summon_groups[group_key] = {
                    'members': [uid], 'template_name': name, 'ownerId': new_owner,
                    'representative_uid': uid, 'initiative_entry': dict(old_entry)}
            elif uid not in self._summon_groups[group_key].setdefault('members', []):
                self._summon_groups[group_key]['members'].append(uid)
            group = self._summon_groups[group_key]
            if group.get('representative_uid') == uid:
                group['initiative_entry'] = dict(old_entry)
                group['initiative_entry'].update({'ownerId': new_owner, 'team': caster_team, 'coord': coord})
            prior = list(grave_entry.get('previousTransfers', []))
            if new_owner not in prior:
                prior.append(new_owner)
                spawn_key = (new_owner, name)
                self._summon_total_spawned[spawn_key] = self._summon_total_spawned.get(spawn_key, 0) + 1
            grave_entry['previousTransfers'] = prior
            old_member.setdefault('name', name)
            char.name = old_member.get('display_name') or char.name

        entry = dict(old_entry)
        entry.update({'userId': uid, 'baseUserId': uid, 'team': caster_team,
                      'coord': coord, 'ownerId': new_owner,
                      'initRoll': old_entry.get('initRoll', 100), 'initRank': old_entry.get('initRank', 0),
                      'dex': old_entry.get('dex', char.get_attr('敏捷', 50)),
                      'actionIdx': old_entry.get('actionIdx', 0), 'isSummon': is_summon})
        entry.setdefault('name', name or char.name)
        if is_summon:
            entry['summonGroup'] = group_key
        il = self._get_initiative()
        if not any(e.get('userId') == uid for e in il):
            il.append(entry)
        self._set_initiative(il)
        self._init_combat_hp(uid, revive_hp)
        actions = self._get_actions()
        action_key = self._summon_group_key(uid) if is_summon else uid
        actions.setdefault(action_key, {'主动': 0, '附加': 0})
        self._set_actions(actions)
        md.setdefault('occupants', {})
        for cell in footprint:
            md['occupants'][cell] = uid
        self._set_map(md)
        grave_entry['revived'] = True
        return revive_hp

    def _remove_summons_of_owner(self, owner_uid, record_collateral=False):
        """Remove an owner's physical summons, preserving the next/current actor."""
        owner_uid = self._resolve_uid(owner_uid)
        il = self._get_initiative()
        if record_collateral:
            for entry in self._battle_unit_entries():
                if entry.get('isSummon') and self._resolve_uid(entry.get('ownerId', '')) == owner_uid \
                        and (self._get_combat_hp(entry.get('userId', '')) or 0) > 0:
                    self._record_graveyard_entry(entry['userId'], reason='owner_death')
        keys = [k for k, g in self._summon_groups.items()
                if self._resolve_uid(g.get('ownerId', '')) == owner_uid]
        summon_ids = {self._resolve_uid(e['userId']) for e in self._battle_unit_entries()
                      if e.get('isSummon') and self._resolve_uid(e.get('ownerId', '')) == owner_uid}
        summon_ids.update(keys)
        for key in keys:
            summon_ids.update(self._summon_groups[key].get('members', []))
            self._summon_groups.pop(key)
        for uid in summon_ids:
            self._summon_members.pop(uid, None)
            self.characters.pop(uid, None)
        removed = summon_ids | {owner_uid}
        self._replace_initiative_preserving_actor(
            [e for e in il if self._resolve_uid(e['userId']) not in removed])
        md = self._get_map()
        if md:
            md['occupants'] = {c: uid for c, uid in md.get('occupants', {}).items()
                               if self._resolve_uid(uid) not in removed}
            self._set_map(md)
        acts = self._get_actions()
        for uid in removed:
            acts.pop(uid, None)
        self._set_actions(acts)

    def _is_untargetable(self, uid):
        """Apply summon traits and owner bodyguards to physical hidden members too."""
        uid = self._resolve_uid(uid)
        entry = self._unit_il_entry(uid, self._get_initiative())
        if entry is None:
            return False
        char = self.get_char(uid)
        if char.get_attr('不可指定', 0) != 1:
            return False
        if entry.get('isSummon'):
            return True
        required_name = char.get_str('不可指定召唤物') or ''
        return any(e.get('isSummon')
                   and self._resolve_uid(e.get('ownerId', '')) == uid
                   and e.get('name', '') != '三合一'
                   and (not required_name or e.get('name') == required_name)
                   and (self._get_combat_hp(e['userId']) or 0) > 0
                   for e in self._battle_unit_entries())

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
                    if self._is_y14(e['userId']):
                        char = self.get_char(e['userId'])
                        seen = getattr(char, '_y14_dying_allies', set())
                        base = self._resolve_uid(dying_uid)
                        if self._resolve_uid(e['userId']) == base or base in seen:
                            continue
                        char._y14_dying_allies = set(seen) | {base}
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

    def _get_physical_buffs(self, uid):
        """Real buff/debuff effects only (cached per uid)."""
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

    def _get_active_buffs(self, uid, include_zone=True):
        """Physical buffs plus a freshly-derived live view from covering zones.

        Zone views are never cached and never appended to the physical list:
        membership depends on the current position, which changes without
        going through `_set_effects`.
        """
        physical = self._get_physical_buffs(uid)
        if not include_zone:
            return physical
        # Cheap gate: most battles have no aura-carrying zone at all, and this
        # accessor is on every dice/attribute query (including training loops).
        # Scan the (already cached) effect list rather than a cached flag so a
        # raw state restore can never hide a live zone aura.
        if not any(x.get('type') == 'zone' and (x.get('auxType') or x.get('skillMod'))
                   and x.get('remainingRounds', 0) != 0
                   for x in self._get_effects()):
            return physical
        views = self._zone_buff_views(uid)
        return physical + views if views else physical

    def _zone_caster_coord(self, eff, il=None):
        """Current center for a zone. Follow-zones read the caster position live."""
        if eff.get('sourceBound'):
            source = eff.get('sourceUserId', '')
            source_entry = self._unit_il_entry(source, il or self._get_initiative()) if source else None
            if not source_entry or (self._get_combat_hp(source) or 0) <= 0:
                return ''
            if eff.get('sourcePassive') and any(
                    e.get('type') in ('buff', 'debuff')
                    and e.get('auxType') == '隔离'
                    and self._resolve_uid(e.get('targetUserId', '')) == self._resolve_uid(source)
                    and e.get('remainingRounds', 0) != 0
                    for e in self._get_effects()):
                return ''
        if eff.get('centerFollows'):
            src = eff.get('sourceUserId')
            if src:
                il = il if il is not None else self._get_initiative()
                base_src = self._resolve_uid(src)
                entry = next((e for e in il
                              if self._resolve_uid(e.get('userId', '')) == base_src), None)
                if entry is None and base_src in getattr(self, '_summon_members', {}):
                    synthetic = getattr(self, '_get_summon_entry', None)
                    entry = synthetic(base_src) if synthetic else None
                if entry and entry.get('coord'):
                    return entry['coord']
        return eff.get('center', 'A1')

    def _unit_il_entry(self, uid, il):
        """Initiative entry for uid; synthesizes hidden summon-group members."""
        base = self._resolve_uid(uid)
        entry = next((e for e in il
                      if self._resolve_uid(e.get('userId', '')) == base), None)
        if entry is not None:
            return entry
        mdata = getattr(self, '_summon_members', {}).get(base)
        if not mdata:
            return None
        synthetic = getattr(self, '_get_summon_entry', None)
        if synthetic:
            return synthetic(base)
        coord = mdata.get('coord', '')
        owner = mdata.get('ownerId', '')
        reps = [e for e in il
                if self._resolve_uid(e.get('userId', '')) == self._resolve_uid(owner)]
        rep_entry = reps[0] if reps else None
        if rep_entry and rep_entry.get('coord'):
            coord = rep_entry['coord']
        return {'userId': base, 'team': mdata.get('team', 'Y'), 'coord': coord,
                'isSummon': True, 'ownerId': owner}

    def _zone_covers_uid(self, eff, uid, il=None):
        """Is uid inside the zone's current area and allowed by its 客体 filter?"""
        il = il if il is not None else self._get_initiative()
        entry = self._unit_il_entry(uid, il)
        if not entry:
            return False
        radius = eff.get('radius', 0) or 0
        if radius <= 0:
            return False
        cp = parse_coord(self._zone_caster_coord(eff, il) or '')
        ep = parse_coord(entry.get('coord', '') or '')
        if not cp or not ep:
            return False
        if max(abs(ep[0] - cp[0]), abs(ep[1] - cp[1])) > radius:
            return False
        caster_uid = eff.get('sourceUserId', '')
        src_entry = self._unit_il_entry(caster_uid, il) if caster_uid else None
        return self._zone_includes(
            eff.get('filter', 35), uid, caster_uid, il,
            uid_team=entry.get('team'),
            caster_team=(src_entry or {}).get('team'))

    def _zone_buff_views(self, uid):
        """Live, read-only buff views granted by zones currently covering uid.

        Non-stackable zones dedupe by (spellName, effectKey, auxType) regardless
        of caster; stackable zones each contribute. Zone 属性削减 stays on its
        own settlement path and is not duplicated here.
        """
        il = self._get_initiative()
        entry = self._unit_il_entry(uid, il)
        if not entry or not entry.get('coord'):
            return []
        base = self._resolve_uid(uid)
        # Isolation is read from physical effects only — never call _has_status
        # here: _has_status -> _get_active_buffs -> this method.
        if any(e.get('type') in ('buff', 'debuff')
               and e.get('auxType') == '隔离'
               and self._resolve_uid(e.get('targetUserId', '')) == base
               and e.get('remainingRounds', 0) != 0
               for e in self._get_effects()):
            return []
        views = []
        seen = set()
        for eff in self._get_effects():
            if eff.get('type') != 'zone':
                continue
            if eff.get('remainingRounds', 0) == 0 and not eff.get('sourceBound'):
                continue
            if eff.get('sourceBound') and not self._zone_caster_coord(eff, il):
                continue
            aux_type = eff.get('auxType', '') or ''
            skill_mod = eff.get('skillMod', '') or ''
            if not aux_type and not skill_mod:
                continue
            # A passive zone whose source is isolated is paused (same as buffs).
            if eff.get('sourcePassive'):
                src = self._resolve_uid(eff.get('sourceUserId', ''))
                if any(e.get('type') in ('buff', 'debuff')
                       and e.get('auxType') == '隔离'
                       and self._resolve_uid(e.get('targetUserId', '')) == src
                       and e.get('remainingRounds', 0) != 0
                       for e in self._get_effects()):
                    continue
            if not self._zone_covers_uid(eff, uid, il):
                continue
            ident = (eff.get('spellName', ''), eff.get('effectKey', ''), aux_type)
            if not eff.get('stackable'):
                if ident in seen:
                    continue
                seen.add(ident)
            views.append({
                'type': 'buff',
                'remainingRounds': eff.get('remainingRounds', 0),
                'skillMod': skill_mod,
                'auxType': aux_type,
                'auxVal': eff.get('auxVal', ''),
                'auxCode': eff.get('auxCode', 0) or 0,
                'sourceUserId': eff.get('sourceUserId', ''),
                'targetUserId': uid,
                'spellName': eff.get('spellName', ''),
                'spellIndex': eff.get('spellIndex', 0),
                'effectKey': eff.get('effectKey', ''),
                'stackable': eff.get('stackable', 0),
                'sourceZone': True,
            })
        return views

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
            ratio = e.get('shield_absorb_moon_ratio', 0)
            if ratio and a:
                charged = self._charge_moon_energy(self._battle_team_of(e.get('sourceUserId', uid)), int(a * ratio))
                if charged:
                    msgs.append(f'理想城护盾转化月能 +{charged}')
            if e['value'] <= 0: e['remainingRounds'] = 0; msgs.append(f"护盾【{e.get('spellName','')}】被击破！")
            else: msgs.append(f"护盾【{e.get('spellName','')}】吸收 {a} 点伤害（剩余 {e['value']}）")
            if remaining <= 0: break
        self._set_effects(effects)
        return (remaining, absorbed, msgs)

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
                if not v:
                    # Try string attr (for dice expressions like '2d6'); get_attr
                    # returns 0 for missing keys, so a falsy int means "not set as int"
                    vs = char.get_str(f"{prefix}{k}")
                    if vs: v = vs
                spell[k] = v if v else 0
            timing = char.get_str(f"{prefix}时机"); spell['时机'] = timing or '2'
            for k in ['级别','类别','消耗mp','消耗san','吟唱回合','默认延续性']:
                if k not in spell: spell[k] = 0
            spell.setdefault('级别',1); spell.setdefault('时机','2')
            # Read _mp_formula
            mpf = char.get_str(f"{prefix}_mp_formula")
            if mpf: spell['_mp_formula'] = mpf
            mpft = char.get_str(f"{prefix}_mp_formula_transformed")
            if mpft: spell['_mp_formula_transformed'] = mpft
            # Read charge_moon (spell-level)
            cm = char.get_attr(f"{prefix}charge_moon")
            if cm is not None: spell['charge_moon'] = cm
            cms = char.get_str(f"{prefix}charge_moon")
            if cms: spell['charge_moon'] = cms
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
            sp_cd = char.get_attr(f"{prefix}cooldown_rounds")
            if sp_cd: spell['cooldown_rounds'] = int(sp_cd)
            # Read blood & HP cost fields
            cbl = char.get_attr(f"{prefix}cost_blood_ml")
            if cbl: spell['cost_blood_ml'] = cbl
            hpct = char.get_attr(f"{prefix}cost_hp_pct_current")
            if hpct: spell['cost_hp_pct_current'] = hpct
            hpd = char.get_str(f"{prefix}cost_hp_dice")
            if hpd: spell['cost_hp_dice'] = hpd
            mpctm = char.get_attr(f"{prefix}cost_mp_pct_max")
            if mpctm: spell['cost_mp_pct_max'] = mpctm
            mpctc = char.get_attr(f"{prefix}cost_mp_pct_current")
            if mpctc: spell['cost_mp_pct_current'] = mpctc
            mpcf = char.get_str(f"{prefix}mp_cost_formula")
            if mpcf: spell['mp_cost_formula'] = mpcf
            ci = char.get_attr(f"{prefix}chant_interruptible")
            if ci: spell['chant_interruptible'] = True
            cihp = char.get_attr(f"{prefix}chant_interrupt_hp_pct")
            if cihp is not None: spell['chant_interrupt_hp_pct'] = cihp
            cimp = char.get_attr(f"{prefix}chant_interrupt_mp_pct")
            if cimp is not None: spell['chant_interrupt_mp_pct'] = cimp
            pf = char.get_attr(f"{prefix}prep_free")
            if pf: spell['prep_free'] = True
            # Spell-level blood/on-kill fields
            for sk in ['on_kill_heal_hp','on_kill_heal_san','on_kill_blood_ml',
                       'blood_per_hit_ml','chain_on_blood_ml','radius_per_extra_mp',
                       'extra_attack_per_blood','blood_ml_per_hp','heal_limit_not_dying']:
                sv = char.get_attr(f"{prefix}{sk}")
                if sv is not None: spell[sk] = sv
            # An explicitly empty effect list must not synthesize the legacy
            # first-category fallback after all effects were commented out.
            effect_letters = [] if char.get_attr(f"{prefix}效果数量") == 0 else CAT_LETTERS
            for ci, letter in enumerate(effect_letters):
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
                # An explicit category type recorded on the card always wins.
                # A zone effect that also carries AUX/skillMod must stay type 8
                # instead of being inferred as type 4 below.
                ct = stored_type if stored_type > 0 else (spell['类别'] if ci == 0 else (1 if char.get_str(f"{pl}伤害骰") else
                    2 if char.get_str(f"{pl}护盾值") else
                    3 if char.get_str(f"{pl}回复hp") or char.get_str(f"{pl}回复san") or char.get_str(f"{pl}回复mp") else
                    4 if (char.get_str(f"{pl}技能加减值") or char.get_str(f"{pl}辅助效果") or char.get_str(f"{pl}其他辅助效果a"))
                        and not char.get_attr(f"{pl}领域中心跟随") else
                    5 if char.get_attr(f"{pl}召唤个数") else
                    6 if char.get_attr(f"{pl}制造个数") or char.get_str(f"{pl}制造个数") or char.get_str(f"{pl}制造物模板") else
                    7 if char.get_attr(f"{pl}引发目标法术") else
                    8 if char.get_attr(f"{pl}领域中心跟随") or (char.get_str(f"{pl}每回合伤害骰") and char.get_attr(f"{pl}作用半径",0)>0) else
                    9 if char.get_attr(f"{pl}引发延迟回合") == 0 and char.get_attr(f"{pl}触发HP比例") else
                    char.get_attr(f"{pl}type") or 0))
                eff = {'type': ct, 'letter': letter}
                for k in ['客体','作用半径','射程','可调节性','成功率','成功率奖惩骰','可闪避性','可反击性','可格挡性','可贯穿性',
                          '致死值','致死值优劣','附加效果时长','持续回合','引发目标法术','引发延迟回合',
                          '制造个数','制造花费回合数','召唤个数','领域中心跟随','触发HP比例','target_phase',
                          '友方延迟回复回合','敌方延迟回复回合','ignite','ignite_tick_dmg',
                          'cooldown_rounds','on_enter_mp_drain_pct','on_enter_trigger_rate',
                          '可叠加','忽略全局奖惩骰','ally_safe',
                          'respawn_hp','respawn_radius','max_value',
                          'cost_counter_amount','吟唱回合',
                          '战意初始层数','战意削弱概率','战意削弱概率奖励层',
                          '战意奖励骰阈值','战意惩罚1阈值','战意惩罚2阈值','战意惩罚骰上限',
                          '领域立即结算','领域轮首结算次数','领域急救','领域同时上限',
                          '死亡轮窗口','复活回复比例','复活主体',
                          '分发自留','分发队友每人','分发给总数上限',
                          '幻造','once_per_transform','persist_through_battle',
                          'charge_moon',
                          'staff_charge_bonus','bow_volley','shield_absorb_moon_ratio',
                          'damage_pct_caster_max_hp','damage_pct_target_max_hp',
                          'damage_pct_caster_cur_hp','damage_mp_multiplier','damage_fixed',
                          'damage_moon_mult','consume_moon',
                          'heal_limit_not_dying']:
                    v = char.get_attr(f"{pl}{k}");
                    if f"{pl}{k}" in char.attrs: eff[k] = v
                for k in ['客体','伤害骰','附加效果','护盾值','回复hp','回复san','回复mp','技能加减值',
                          '辅助效果','辅助效果值','其他辅助效果a','辅助效果值a',
                          '召唤个数','制造个数','制造花费回合数','召唤物模板','制造物模板',
                          '每回合伤害骰','吸血比例','属性削减',
                          '友方行为','友方伤害骰','友方延迟回复骰','友方延迟回复公式',
                          '敌方回复','敌方回复骰','ignite_dmg_dice','on_enter_attr_debuff',
                          'variant','weapon_state','counter_type','linked_summon','turn_mp_refund',
                          'damage_table','mp_cost_formula','制造分发','复活召唤物主人政策','消费追加回复hp','tasteBonusOwnerSerial']:
                    v = char.get_str(f"{pl}{k}");
                    if v: eff[k] = v
                # 持续回合/吟唱回合 can be dice strings ('2d3'); the int loop above
                # stores 0 for them — roll the string variant and overwrite.
                for k in ['持续回合','吟唱回合']:
                    vs = char.get_str(f"{pl}{k}")
                    if vs:
                        eff[k] = vs
                # Parse JSON-serialized complex fields (hp_thresholds etc.)
                for k in ['hp_thresholds', 'exclude', 'eject_phases', 'on_enter_zone_damage', 'hp_threshold_grant',
                          '分发给总数公式','分发人数覆盖','分发削减顺序']:
                    v = char.get_str(f"{pl}{k}")
                    if v:
                        try: eff[k] = json.loads(v)
                        except: pass
                for k in ['来源存活绑定','领域立即结算','领域轮首结算次数','领域急救','领域同时上限',
                          '死亡轮窗口','复活回复比例','复活主体','分发自留','分发队友每人','分发给总数上限']:
                    v = char.get_attr(f"{pl}{k}")
                    if v not in (None, 0, ''):
                        eff[k] = v
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
            # Buffs belonging to a domain must follow live domain membership,
            # rather than becoming detached buffs on whoever was present at cast.
            zones = [e for e in spell['effects'] if e.get('type') == 8]
            if spell.get('类别') == 8 and zones:
                for effect in spell['effects']:
                    if effect.get('type') == 4:
                        effect.update(type=8)
                        for key in ('作用半径', '领域中心跟随'):
                            effect[key] = effect.get(key) or zones[0].get(key, 0)
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
        candidates = [e['userId'] for e in self._enemy_target_entries(caster_id)
                      if include_summons or not e.get('isSummon')]
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
        my_entry = self._unit_il_entry(caster_id, init_list)
        mt = my_entry.get('team', 'Y') if my_entry else 'Y'
        has_allies = any(e['team'] == mt and e['userId'] != caster_id
                         and (self._get_combat_hp(e['userId']) or 0) > 0
                         for e in init_list)
        q_mode = 'Q_team' if has_allies else 'Q_solo'
        best_strategy = _weight_best_strategy.get(f"{q_mode}:{caster_serial}")

        if not best_strategy:
            return None

        # Get enemies
        enemies = self._enemy_target_entries(caster_id)
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
            # 无合法目标时返回 None（不再回退到施法者，否则伤害法术会打向自己）
            return tgt

        if has_dmg or has_zone_dmg or has_enemy_debuff:
            # Try weight-based target selection first
            weighted_tgt = self._get_weighted_target(caster_id)
            if weighted_tgt:
                return weighted_tgt
            # Fallback: first enemy by initiative
            init_list = self._get_initiative()
            mt = self._battle_team_of(caster_id)
            enemies = self._enemy_target_entries(caster_id)
            if not enemies:
                all_teams = [(e.get('name', e.get('userId', '?')), e.get('team', '?')) for e in init_list]
                _engine_debug_log(f"_smart_target: caster={caster_id} team={mt} spell={spell.get('name','?')} "
                      f"NO ENEMIES! all teams: {all_teams}")
            # 无合法敌人 → None（由 _execute_spell 拦截并提示，不扣 MP）
            return enemies[0]['userId'] if enemies else None
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

    def end_skill_effects(self, uid, spell_index):
        if spell_index <= 0:
            return '请选择技能编号'
        effects = self._get_effects()
        kept = [e for e in effects if not (self._resolve_uid(e.get('sourceUserId', '')) == self._resolve_uid(uid)
                and e.get('spellIndex') == spell_index and not e.get('sourcePassive') and e.get('type') != 'cooldown')]
        self._set_effects(kept)
        return f'已结束技能 {spell_index} 的 {len(effects)-len(kept)} 个持续效果'

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
            target = self._smart_target(uid, spell)
            return self._execute_spell(uid, target, spell)
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
        """Advance normally even when the active summon group has just died."""
        state = self._get_state() or {}
        il = self._get_initiative()
        active = il[state.get('activeIndex', 0) % len(il)]['userId'] if il else None
        if active and self._resolve_uid(active) == self._resolve_uid(uid):
            self._settle_inspiration(uid)
        previous = getattr(self, '_advancing_turn', False)
        self._advancing_turn = True
        try:
            return self._advance_turn(uid)
        finally:
            self._advancing_turn = previous

    def _advance_turn(self, uid):
        state = self._get_state()
        if not state:
            return False
        self._return_to_anchor(self._resolve_uid(uid))
        # Promotion is safe here; defer deleting dead slots until their turn is passed.
        for key in list(self._summon_groups):
            self._ensure_group_representative(key)
        il = self._get_initiative()
        if not il:
            self._check_and_mark_end(state)
            return False
        active_uid = il[min(state.get('activeIndex', 0), len(il) - 1)]['userId']
        if self._summon_group_key(active_uid) != self._summon_group_key(uid):
            # An attack already removed its actor/owner and selected the successor.
            self._reconcile_summon_groups()
            self._check_and_mark_end(self._get_state())
            return False
        crossed_round = False
        for _ in range(len(il) * 2 + 1):
            il = self._get_initiative()
            if not il:
                break
            state = self._get_state()
            idx = min(state.get('activeIndex', 0), len(il) - 1)
            state['activeIndex'] = (idx + 1) % len(il)
            wrapped = state['activeIndex'] == 0
            if wrapped:
                crossed_round = True
                state['round'] = state.get('round', 1) + 1
            # Publish the new actor before ticks can remove/reorder entries.
            self._set_state(state)
            if wrapped:
                self._reset_move_power()
                tick_text = self._tick_down()
                if tick_text and not getattr(self, 'fast_mode', False):
                    self._pending_battle_events.extend(tick_text.splitlines())
                self._apply_zone_effects()
                self._reconcile_summon_groups()
                self._sync_all_initiative_slots()
                self._reset_round_actions()
            state = self._get_state()
            self._check_and_mark_end(state)
            if state.get('phase') != 'active':
                break
            il = self._get_initiative()
            if not il:
                break
            cur = il[state['activeIndex']]
            hp = self._get_combat_hp(cur['userId'])
            if hp is None or hp > 0:
                break
            if cur.get('isSummon') and self._get_alive_summon_group_members(cur['userId']):
                self._ensure_group_representative(self._summon_group_key(cur['userId']))
                break
        self._reconcile_summon_groups()
        return crossed_round

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

        Default suffix map: 剑→'a', 斗殴→'b'. Characters may override it via the
        str_attrs key `damage_suffix_map` (a dict, or its JSON serialization) —
        e.g. LINBAI/YEZHAOMING declare {'剑':'a','弓术':'b'} for sword/bow variants.
        Falls back to 伤害値. Resolves 'db' token from character's 体格."""
        if self._is_y17(uid) and any(e.get('spellIndex') == 5
                and e.get('sourceUserId') == self._resolve_uid(uid)
                and e.get('remainingRounds', 0) > 0 for e in self._get_effects()):
            return '1d8+2'
        profile = self._y14_attack_profile(uid)
        if profile:
            return profile[0]
        char = self.get_char(uid)
        db_str = _get_db_str(char)
        suffix_map = {'剑': 'a', '斗殴': 'b'}
        override = char.get_str('damage_suffix_map')
        if isinstance(override, dict):
            if override: suffix_map = override
        elif isinstance(override, str) and override:
            try:
                pm = json.loads(override)
                if isinstance(pm, dict) and pm: suffix_map = pm
            except (ValueError, TypeError):
                pass
        suffix = suffix_map.get(skill_name, '')
        if suffix:
            dd = char.get_str(f"伤害值{suffix}")
            if dd: return _resolve_db(dd, db_str)
        return _resolve_db(char.get_str("伤害值") or "1d4", db_str)

    def _get_attack_skill(self, uid, char=None):
        """Weapon-aware basic-attack skill selection, shared by all battle paths.

        _weapon_name == 'sword'/'bow' with a matching skill attr → use that skill;
        otherwise fall back to get_best_melee(). Returns (skill_name, skill_value)."""
        char = char or self.get_char(uid)
        weapon = char.get_str('_weapon_name') or ''
        if weapon == 'sword' and char.get_attr('剑', 0):
            return '剑', char.get_attr('剑', 55)
        if weapon == 'bow' and char.get_attr('弓术', 0):
            return '弓术', char.get_attr('弓术', 61)
        return char.get_best_melee()

    def _get_phase(self, uid):
        char = self.get_char(uid)
        return getattr(char, 'phase', 1) if char else 1

    def _set_phase(self, uid, p):
        char = self.get_char(uid)
        if char: char.phase = p

    # ---- Per-effect target resolution (each effect targets independently) ----
    # Longest-first so concatenated codes ('24','45','135','124') match the right token.
    _TARGET_CODES = ('135', '125', '124', '45', '35', '25', '24', '15', '14', '13', '12', '5', '4', '3', '2', '1')

    def _parse_obj_codes(self, obj):
        """Split a concatenated 客体 code string ('45','135',...) into tokens."""
        obj = str(obj).upper().strip()
        if obj in ('R', 'RP'):
            return [obj]
        codes = []
        i = 0
        while i < len(obj):
            for code in self._TARGET_CODES:
                if obj.startswith(code, i):
                    if code not in codes:
                        codes.append(code)
                    i += len(code)
                    break
            else:
                i += 1
        return codes or ['4']

    def _parse_target_codes(self, eff):
        return self._parse_obj_codes(eff.get('客体', 4))

    def _zone_includes(self, filter_obj, uid, caster_id, il, uid_team=None, caster_team=None):
        """Does a zone entry's 客体 filter include uid? (客体=3 不含自身)

        Zone membership uses the same token vocabulary as ordinary targeting,
        but single-target codes act as a faction eligibility test (a radius
        buffer cannot pick one random enemy per query).
        `uid_team`/`caster_team` may be supplied for hidden summon members
        whose entries are not in the initiative list."""
        codes = self._parse_obj_codes(filter_obj)
        if 'R' in codes or 'RP' in codes:
            return False
        base = self._resolve_uid(uid)
        caster_base = self._resolve_uid(caster_id)
        is_self = base == caster_base
        team_of = lambda u: next((e.get('team', 'Y') for e in il
                                 if self._resolve_uid(e['userId']) == self._resolve_uid(u)), 'Y')
        my_team = uid_team or team_of(uid)
        src_team = caster_team or team_of(caster_id)
        is_ally = not is_self and my_team == src_team
        if '135' in codes or '35' in codes:
            return True
        if is_self:
            return any(c in codes for c in ('1', '12', '13', '14', '15', '124', '125'))
        if is_ally:
            return any(c in codes for c in ('2', '3', '12', '13', '24', '25', '124', '125'))
        return any(c in codes for c in ('4', '5', '14', '15', '24', '25', '45', '124', '125'))

    def _apply_zone_attr_penalties(self, il):
        """Recompute live zone attribute penalties on initiative entries.

        Non-stackable zones keep the strongest per-attribute penalty; stackable
        zones sum. Values are read live by _zone_attr_mod, never written into
        base attributes, so leaving/expiry restores the value automatically.
        """
        for entry in il:
            for k in list(entry.keys()):
                if k.startswith('_zone_penalty_'):
                    del entry[k]
        for eff in self._get_effects():
            if eff.get('type') != 'zone':
                continue
            mods = _parse_attr_mods(eff.get('attributeDebuff', ''))
            if not mods:
                continue
            cp = parse_coord(self._zone_caster_coord(eff, il))
            if not cp:
                continue
            stackable = eff.get('stackable', 0)
            for entry in il:
                uid = entry['userId']
                if self._has_status(uid, '隔离'):
                    continue
                ep = parse_coord(entry.get('coord', '')) if entry.get('coord') else None
                if not ep:
                    continue
                if max(abs(ep[0] - cp[0]), abs(ep[1] - cp[1])) > (eff.get('radius', 0) or 0):
                    continue
                if not self._zone_includes(eff.get('filter', 35), uid,
                                           eff.get('sourceUserId', ''), il):
                    continue
                for attr_name, delta in mods:
                    if delta >= 0:
                        continue  # 属性削减 is a penalty only
                    group = _attr_group(attr_name)
                    key = '_zone_penalty_' + group
                    if stackable:
                        skey = '_zone_penalty_stack_' + group
                        entry[skey] = entry.get(skey, 0) + abs(delta)
                    else:
                        entry[key] = max(entry.get(key, 0), abs(delta))
        return il

    def _battle_team_of(self, uid):
        entry = self._unit_il_entry(uid, self._get_initiative())
        return entry.get('team', 'Y') if entry else 'Y'

    def _battle_unit_entries(self):
        """Read physical units in initiative order, then hidden group members."""
        il = self._get_initiative()
        picked = []
        seen = set()
        for entry in il:
            uid = self._resolve_uid(entry['userId'])
            if uid not in seen and not entry.get('_suppressed', False):
                picked.append(entry)
                seen.add(uid)
        for uid in self._summon_members:
            if uid not in seen:
                entry = self._unit_il_entry(uid, il)
                if entry is not None:
                    picked.append(entry)
                    seen.add(uid)
        return picked

    def _enemy_target_entries(self, caster_id):
        il = self._get_initiative()
        return [entry for uid in self._enemy_targets(caster_id)
                if (entry := self._unit_il_entry(uid, il)) is not None]

    def _allies_in_battle(self, caster_id, include_self):
        """Alive same-team units, including hidden summon-group members."""
        team = self._battle_team_of(caster_id)
        base = self._resolve_uid(caster_id)
        picked = []
        for e in self._get_initiative():
            uid = e['userId']
            if e.get('team') != team:
                continue
            if not include_self and self._resolve_uid(uid) == base:
                continue
            if (self._get_combat_hp(uid) or 0) <= 0:
                continue
            picked.append(uid)
        # 召唤物分组中的隐藏成员不在先攻表里，但它们同样在场上
        for sid, mdata in self._summon_members.items():
            if sid in picked:
                continue
            if mdata.get('team') != team:
                continue
            if not include_self and self._resolve_uid(sid) == base:
                continue
            if (self._get_combat_hp(sid) or 0) <= 0:
                continue
            picked.append(sid)
        return picked

    def _ally_targets(self, caster_id, include_self):
        """Alive allies (same team). By rule 客体=3 excludes the caster."""
        return self._allies_in_battle(caster_id, include_self=include_self)

    def _enemy_targets(self, caster_id):
        team = self._battle_team_of(caster_id)
        return [self._resolve_uid(e['userId']) for e in self._battle_unit_entries()
                if e.get('team') != team and (self._get_combat_hp(e['userId']) or 0) > 0
                and not self._is_untargetable(e['userId'])]

    def _selected_or_first(self, candidates, selected):
        if selected and any(self._resolve_uid(c) == self._resolve_uid(selected) for c in candidates):
            return selected
        return candidates[0] if candidates else None

    def _resolve_effect_targets(self, caster_id, selected_target, eff):
        """Resolve one effect's actual target list from *its own* 客体 field.

        `selected_target` is only a candidate (e.g. the enemy chosen on the
        command line); it never overrides another effect's targeting.
        """
        codes = self._parse_target_codes(eff)
        base_uid = self._resolve_uid(caster_id)
        picked = []
        for code in codes:
            if code == 'R':
                t = self._pick_random_target(caster_id, include_summons=True)
                if t:
                    picked.append(t)
            elif code == 'RP':
                t = self._pick_random_target(caster_id, include_summons=False)
                if t:
                    picked.append(t)
            elif code == '1':
                picked.append(caster_id)
            elif code == '2':
                allies = self._ally_targets(caster_id, include_self=False)
                t = self._selected_or_first(allies, selected_target)
                if t:
                    picked.append(t)
            elif code == '3':
                picked.extend(self._ally_targets(caster_id, include_self=False))
            elif code == '4':
                enemies = self._enemy_targets(caster_id)
                t = self._selected_or_first(enemies, selected_target)
                if t:
                    picked.append(t)
                elif not enemies:
                    # 未指定目标且无敌人由调用方拦截；此处不串到友方
                    pass
            elif code == '24':
                # 友方单体或敌方单体：尊重显式选择，未选时优先敌方
                if selected_target and (self._get_combat_hp(selected_target) or 0) > 0:
                    picked.append(selected_target)
                else:
                    enemies = self._enemy_targets(caster_id)
                    if enemies:
                        picked.append(enemies[0])
                    else:
                        allies = self._ally_targets(caster_id, include_self=False)
                        if allies:
                            picked.append(allies[0])
            elif code == '5':
                picked.extend(self._enemy_targets(caster_id))
            elif code == '12':
                allies = [caster_id] + self._ally_targets(caster_id, include_self=False)
                t = self._selected_or_first(allies, selected_target)
                if t:
                    picked.append(t)
            elif code == '13':
                picked.append(caster_id)
                picked.extend(self._ally_targets(caster_id, include_self=False))
            elif code in ('14', '15'):
                picked.append(caster_id)
                if code == '14':
                    enemies = self._enemy_targets(caster_id)
                    t = self._selected_or_first(enemies, selected_target)
                    if t:
                        picked.append(t)
                else:
                    picked.extend(self._enemy_targets(caster_id))
            elif code == '124':
                any_uid = selected_target if selected_target else self._get_weighted_target(caster_id)
                picked.append(any_uid or caster_id)
            elif code in ('35', '135'):
                for e in self._allies_in_battle(caster_id, include_self=True) + self._enemy_targets(caster_id):
                    picked.append(e)
            elif code == '45':
                enemies = self._enemy_targets(caster_id)
                t = self._selected_or_first(enemies, selected_target)
                if t:
                    picked.append(t)
                picked.extend(x for x in enemies if not t or self._resolve_uid(x) != self._resolve_uid(t))
            elif code == '25':
                allies = self._ally_targets(caster_id, include_self=False)
                t = self._selected_or_first(allies, selected_target)
                if t:
                    picked.append(t)
                picked.extend(self._enemy_targets(caster_id))
            elif code == '125':
                allies = [caster_id] + self._ally_targets(caster_id, include_self=False)
                t = self._selected_or_first(allies, selected_target)
                if t:
                    picked.append(t)
                picked.extend(self._enemy_targets(caster_id))

        if eff.get('_blood_wave'):
            picked = [u for u in picked if self._resolve_uid(u) != base_uid]
        # Radius filter for group effects that declare 作用半径
        radius = int(eff.get('作用半径', 0) or 0)
        if radius > 0:
            cpos = None
            c_entry = next((e for e in self._get_initiative()
                            if self._resolve_uid(e['userId']) == base_uid), None)
            if c_entry:
                cpos = parse_coord(c_entry.get('coord', ''))
            if cpos:
                kept = []
                for uid in picked:
                    ent = self._unit_il_entry(uid, self._get_initiative())
                    pos = parse_coord(ent.get('coord', '')) if ent else None
                    if not pos or max(abs(pos[0]-cpos[0]), abs(pos[1]-cpos[1])) <= radius:
                        kept.append(uid)
                picked = kept

        # Dedup by base uid (multi-action slots share a character), keep order
        seen = set(); result = []
        for uid in picked:
            if not uid:
                continue
            b = self._resolve_uid(uid)
            if b in seen:
                continue
            seen.add(b)
            result.append(uid)
        return result

    def _apply_hp_heal_amount(self, target_id, amount, *, allow_dying=True):
        """Apply HP healing through one path for spells, zones, and items.

        A zero-HP dying unit is not treated as a default-HP unit. Healing first
        removes excess dying damage; only leftover healing revives the unit.
        """
        amount = max(0, int(amount or 0))
        if amount <= 0:
            return {'applied': 0, 'absorbed_excess': 0, 'rescued': False, 'dead': False}
        current = self._get_combat_hp(target_id)
        if current is None:
            return {'applied': 0, 'absorbed_excess': 0, 'rescued': False, 'dead': True}
        dying = self._is_dying(target_id) if allow_dying else None
        if current <= 0 and dying is None:
            return {'applied': 0, 'absorbed_excess': 0, 'rescued': False, 'dead': True}
        target = self.get_char(target_id)
        max_hp = target.get_attr('体力上限', max(current, 1)) if target else max(current, 1)
        if dying is not None:
            excess = max(0, int(dying.get('excessDamage', 0) or 0))
            absorbed = min(amount, excess)
            remaining = amount - absorbed
            dying['excessDamage'] = excess - absorbed
            rescued = dying['excessDamage'] <= 0 and remaining > 0
            if rescued:
                new_hp = min(max_hp, remaining)
                self._set_combat_hp(target_id, new_hp)
                effects = [e for e in self._get_effects() if e is not dying]
                self._set_effects(effects)
                self._charge_heal_overflow(target_id, remaining - new_hp)
                return {'applied': new_hp, 'absorbed_excess': absorbed,
                        'rescued': True, 'dead': False}
            self._set_effects(self._get_effects())
            return {'applied': 0, 'absorbed_excess': absorbed,
                    'rescued': False, 'dead': False}
        new_hp = min(max_hp, current + amount)
        self._set_combat_hp(target_id, new_hp)
        self._charge_heal_overflow(target_id, amount - (new_hp - current))
        return {'applied': new_hp - current, 'absorbed_excess': 0,
                'rescued': False, 'dead': False}

    def _auto_consume_hp_cake(self, uid):
        """Consume personal HP cakes immediately when a unit enters dying."""
        if getattr(self, '_auto_cake_rescue', False):
            return ''
        if self._get_combat_hp(uid) != 0 or not self._is_dying(uid):
            return ''
        self._auto_cake_rescue = True
        try:
            messages = []
            while self._get_combat_hp(uid) == 0 and self._is_dying(uid):
                cake = self._find_personal_item(uid, item_kind='hp')
                if not cake:
                    break
                item_name, index = cake
                result, ok = self._consume_inventory_item(uid, index, item_name, uid)
                if not ok:
                    break
                messages.append(result)
            return '\n'.join(messages)
        finally:
            self._auto_cake_rescue = False

    def _find_personal_item(self, uid, item_kind=None, item_name=None):
        from characters_data import ITEM_TEMPLATES
        ITEM_TEMPLATES = getattr(self, '_item_templates_snapshot', ITEM_TEMPLATES)
        char = self.get_char(uid)
        if not char:
            return None
        for index, entry in enumerate(char.inventory):
            name = entry.get('item', '')
            if entry.get('count', 0) <= 0 or name not in ITEM_TEMPLATES:
                continue
            if item_name and name != item_name:
                continue
            if item_kind == 'hp' and not ITEM_TEMPLATES[name].get('回复hp'):
                continue
            if item_kind == 'mp' and not ITEM_TEMPLATES[name].get('回复mp'):
                continue
            return name, index
        return None

    def _consume_inventory_item(self, eater_id, index, item_name, target_id):
        char = self.get_char(eater_id)
        if not char or index >= len(char.inventory):
            return '无可用的物品', False
        entry = char.inventory[index]
        if entry.get('item') != item_name or entry.get('count', 0) <= 0:
            return '无可用的物品', False
        result = self._apply_item_heal(item_name, target_id, metadata=entry)
        entry['count'] -= 1
        if entry['count'] <= 0:
            char.inventory.pop(index)
        return result, True

    def _apply_target_effect(self, caster_id, tid, eff, spell, is_passive_spell, eff_idx):
        """Execute one damage/shield/heal/buff effect on one resolved target."""
        out = ''
        ct = eff['type']
        if ct == 1:  # Damage
            return self._handle_spell_damage_effect(caster_id, tid, eff, spell)
        if ct == 2:  # Shield
            sv = roll_dice(eff.get('护盾值', '1d4')); dur = eff.get('持续回合', 1)
            effects = self._get_effects()
            shield_entry = {'type': 'shield', 'value': sv, 'remainingRounds': dur,
                            'sourceUserId': caster_id, 'targetUserId': tid or caster_id,
                            'spellName': spell['name'], 'spellIndex': spell['index'],
                            'persistent': spell.get('默认延续性', 0)}
            if eff.get('shield_absorb_moon_ratio'):
                shield_entry['shield_absorb_moon_ratio'] = eff['shield_absorb_moon_ratio']
            if is_passive_spell:
                shield_entry['sourcePassive'] = True
            effects.append(shield_entry)
            self._set_effects(effects)
            tname = self.get_char(tid).name if tid and tid != caster_id else '自身'
            out += f'  {tname} 获得 {sv} 点护盾 ({eff.get("护盾值","1d4")})\n'
            return out
        if ct == 3:  # Heal
            hp_heal = int(roll_dice(eff.get('回复hp', '0')) * self._get_buff_heal_pct(tid, 'hp'))
            hp_heal += self._get_buff_heal_flat(tid, 'hp')
            san_heal = roll_dice(eff.get('回复san', '0'))
            mp_heal = int(roll_dice(eff.get('回复mp', '0')) * self._get_buff_heal_pct(tid, 'mp'))
            mp_heal += self._get_buff_heal_flat(tid, 'mp')
            tchar = self.get_char(tid); tname = tchar.name if tchar else tid
            if hp_heal > 0:
                outcome = self._apply_hp_heal_amount(tid, hp_heal)
                if outcome['rescued']:
                    # Continue with the latest effect list after dying was removed.
                    pass
                if outcome['dead']:
                    out += f'  {tname} 已死亡，无法治疗\n'
                elif outcome['rescued']:
                    out += f'  {tname} 脱离濒死！回复 HP: {outcome["applied"]}\n'
                elif outcome['absorbed_excess'] > 0:
                    out += f'  {tname} 治愈濒死伤害 {outcome["absorbed_excess"]}点\n'
                else:
                    out += f'  {tname} 回复 HP +{outcome["applied"]} ({eff.get("回复hp","0")})\n'
            if san_heal > 0:
                self._restore_resource(tid, san_heal, 'san')
                out += f'  {tname} 回复 SAN +{san_heal}\n'
            if mp_heal > 0:
                self._restore_resource(tid, mp_heal)
                out += f'  {tname} 回复 MP +{mp_heal}\n'
            return out
        if ct == 4:  # Buff (with auxCode)
            # load_spells 会把缺失的 持续回合 写成 0；0 视为未声明，取默认 1
            dur = eff.get('持续回合') or 1; aux_type = _eff_get(eff, 'auxType')
            aux_code = AUX_NAME_TO_CODE.get(aux_type, 0)
            aux_val = _eff_get(eff, 'auxVal')
            stackable = eff.get('可叠加', 0)
            is_control_status = aux_type in CONTROL_STATUS_TYPES
            # Buff identity includes the effect: two effects of the same spell
            # with the same auxType must NOT collapse into one refresh.
            eff_key = str(eff.get('letter', eff_idx))
            effects = self._get_effects()
            if eff.get('turn_mp_refund'):
                stacks = [e for e in effects if e.get('type') == 'buff'
                          and self._resolve_uid(e.get('targetUserId', '')) == self._resolve_uid(tid)
                          and e.get('turn_mp_refund') and e.get('remainingRounds', 0) > 0]
                if len(stacks) >= int(stackable or 1):
                    oldest = min(stacks, key=lambda e: e['remainingRounds'])
                    oldest['remainingRounds'] = max(oldest['remainingRounds'], dur)
                    self._set_effects(effects)
                    return f'  灵感层数已满，刷新持续回合至{oldest["remainingRounds"]}\n'
            existing = None
            if not stackable:
                for e in effects:
                    if e.get('type') != 'buff' or self._resolve_uid(e.get('targetUserId', '')) != self._resolve_uid(tid):
                        continue
                    if is_control_status:
                        if e.get('auxType') == aux_type:
                            existing = e; break
                    else:
                        if (e.get('spellName') == spell['name'] and e.get('auxType') == aux_type
                                and e.get('effectKey', '') == eff_key):
                            existing = e; break
            tname = self.get_char(tid).name if tid and tid != caster_id else '自身'
            if existing:
                existing['remainingRounds'] = max(existing.get('remainingRounds', 0), dur)
                if is_control_status and aux_type == '减速':
                    existing['auxVal'] = aux_val
                out += f'  {tname} 刷新辅助效果: {aux_type} {aux_val} (延长至{existing["remainingRounds"]}回合)\n'
            else:
                bonus_dmg = eff.get('伤害骰', '')
                buff_entry = {'type': 'buff', 'remainingRounds': dur, 'skillMod': eff.get('技能加减值', ''),
                    'auxType': aux_type, 'auxVal': aux_val, 'auxCode': aux_code,
                    'sourceUserId': caster_id, 'targetUserId': tid,
                    'spellName': spell['name'], 'spellIndex': spell['index'],
                    'effectKey': eff_key,
                    'persistent': spell.get('默认延续性', 0),
                    'stackable': stackable,
                    'exclude': eff.get('exclude', [])}
                if bonus_dmg:
                    buff_entry['bonusDmgDice'] = bonus_dmg
                if is_passive_spell:
                    buff_entry['sourcePassive'] = True
                if eff.get('linked_summon'):
                    buff_entry['linked_summon'] = eff['linked_summon']
                if eff.get('hp_threshold_grant'):
                    buff_entry['hp_threshold_grant'] = eff['hp_threshold_grant']
                    # This is a trigger registration, not an already granted immunity.
                    buff_entry['auxCode'] = 0
                    buff_entry['auxType'] = ''
                if eff.get('turn_mp_refund'):
                    buff_entry['turn_mp_refund'] = eff['turn_mp_refund']
                effects.append(buff_entry)
                bonus_note = f' (伤害骰+{bonus_dmg})' if bonus_dmg else ''
                label = aux_type or buff_entry.get('skillMod') or 'buff'
                out += f'  {tname} 施加辅助效果: {label} {aux_val if aux_type else ""}{bonus_note}\n'.replace('  \n', '\n')
            self._set_effects(effects)
            return out
        return out

    # ---- Unified _execute_spell (all 8 categories, all fixes applied) ----
    def _execute_spell(self, caster_id, target_id, spell):
        reason = self._y16_preflight(caster_id, spell)
        if reason:
            return ActionExecutionResult(reason, False, 'spell_rejected')
        spell = self._y16_prepare_spell(caster_id, spell)
        if self._is_y17(caster_id):
            reason = self._y17_preflight(caster_id, spell)
            if reason:
                return ActionExecutionResult(reason, False, 'spell_rejected')
            chant = self._y17_chant(caster_id)
            if chant:
                return self._y17_continue_chant(caster_id, chant)
            target_id, spell = self._y17_prepare_spell(caster_id, target_id, spell)
        if self._is_y14(caster_id):
            caster_id = self._resolve_uid(caster_id)
            if target_id is not None:
                target_id = self._resolve_uid(target_id)
            reason = self._y14_preflight(caster_id, spell)
            if reason:
                return ActionExecutionResult(reason, False, 'spell_rejected')
            index = spell.get('index')
            if index in (8, 13, 17):
                if any(e.get('sourceUserId') == self._resolve_uid(caster_id)
                       and e.get('spellIndex') == index and e.get('remainingRounds', 0) > 0
                       for e in self._get_effects()):
                    return ActionExecutionResult('被动已经初始化', False, 'spell_rejected')
                if index == 8:
                    if getattr(self.get_char(caster_id), '_y14_substitute_used', False):
                        return ActionExecutionResult('残机替身已消耗', False, 'spell_rejected')
                    # Registration is free; the lethal trigger pays 8 MP.
                    spell = dict(spell, 消耗mp=0)
        # Duration dice are rolled on cast, never when merely listing skills.
        if any(isinstance(e.get('持续回合'), str) or isinstance(e.get('吟唱回合'), str) for e in spell.get('effects', [])):
            spell = dict(spell, effects=[dict(e) for e in spell['effects']])
            for effect in spell['effects']:
                for key in ('持续回合', '吟唱回合'):
                    if isinstance(effect.get(key), str):
                        effect[key] = roll_dice(effect[key])
        if getattr(self, '_manual_attack_bp', ''):
            spell = dict(spell, _command_bp=self._manual_attack_bp)
        char = self.get_char(caster_id)
        tname = self.get_char(target_id).name if target_id and target_id != caster_id else '自身'
        out = f'{char.name} 释放【{spell["name"]}】→ {tname}\n'

        # _once_per_battle is committed only after all target/resource prechecks pass.
        once_name = spell.get('name', '')
        once_required = bool(spell.get('_once_per_battle'))
        once_key = (self._resolve_uid(caster_id), spell.get('index', 0))
        if once_required:
            ledger = getattr(self, '_revive_use_ledger', set())
            consumed = getattr(char, '_once_consumed', set())
            if once_key in ledger or once_name in consumed:
                return ActionExecutionResult(f'{char.name} 的【{once_name}】本场战斗已使用过，无法再次施放。\n', False, 'spell_rejected')
            if self._get_cooldown(caster_id, spell.get('index', 0)) > 0:
                return ActionExecutionResult(f'{char.name} 的【{once_name}】冷却中（剩余 {self._get_cooldown(caster_id, spell.get('index', 0))} 回合）。\n', False, 'spell_rejected')

        # ── 未指定目标→随机敌对目标 ──
        # hostile 判定需覆盖所有"应对敌方施放"的效果类型，否则 type=4 敌方减益 /
        # type=8 敌方领域会留在 target_id == caster_id 上，变成给自己上 debuff。
        def _needs_enemy(e):
            et = e.get('type')
            obj = str(e.get('客体', 4)).upper().strip()
            if et == 1:
                return obj in ('4', '5', '24', '45', 'R', 'RP')
            if et == 4:
                return obj in ('5', '124')
            if et == 8:
                return bool(e.get('每回合伤害骰')) and not e.get('回复hp') \
                    and obj in ('4', '5', '45', '124', '134', '135', '35')
            return False
        is_hostile_spell = any(_needs_enemy(e) for e in spell.get('effects', []))

        if not target_id or target_id == caster_id:
            if is_hostile_spell:
                rt = self._pick_random_target(caster_id)
                if rt:
                    target_id = rt
                    tname = self.get_char(target_id).name if target_id else '自身'
                    out = f'{char.name} 释放【{spell["name"]}】→ {tname}（随机）\n'
                else:
                    # 没有任何合法敌对目标：置 None，交由下方守卫拦截（不扣 MP/SAN）
                    target_id = None

        # ── 无合法目标 → 禁止施放，不消耗任何资源 ──
        # 只拦截"需要对敌"的法术；自我增益/治疗/召唤传 None 时仍走原逻辑（_smart_target
        # 对它们始终返回 caster_id，None 只可能来自 .a sN 或外部调用）。
        if target_id is None and is_hostile_spell:
            return ActionExecutionResult(f'{char.name} 没有可攻击的目标！\n', False, 'spell_rejected')

        # ── 纯召唤技能：零名额时提前返回，不扣 MP/SAN、不消耗一次性标记 ──
        # 每个召唤效果的数量只掷一次，结果存入本次调用的局部计划，避免共享 spell
        # 对象上的 _pre_rolled_count 被下次施放误用。
        summon_plan = {}
        _summon_only = bool(spell.get('effects')) and all(
            e.get('type') == 5 for e in spell.get('effects', []))
        if _summon_only:
            _block_reason = ''
            for _ei, _e in enumerate(spell.get('effects', [])):
                _tmpl_name = _e.get('召唤物模板', '')
                if not _tmpl_name:
                    continue
                _count_raw = _e.get('召唤个数', 1)
                if isinstance(_count_raw, str) and 'd' in str(_count_raw):
                    _roll = roll_dice(str(_count_raw))
                else:
                    try:
                        _roll = int(_count_raw) if _count_raw else 1
                    except (TypeError, ValueError):
                        _roll = 1
                _remain, _reason = self._get_summon_quota(caster_id, _tmpl_name)
                if _remain == 0:
                    _block_reason = _reason
                    summon_plan[_ei] = 0
                    continue
                _granted = _roll if _remain is None else max(0, min(_roll, _remain))
                summon_plan[_ei] = _granted
                summon_plan.setdefault('_requested', {})[_ei] = _roll
                if _granted < _roll and not _block_reason:
                    _block_reason = _reason or f'{_tmpl_name}名额不足'
            if not any(v for k, v in summon_plan.items() if isinstance(k, int)):
                return ActionExecutionResult(f'{char.name} 无法召唤：{_block_reason or "召唤限制"}\n', False, 'spell_rejected')

        # Per-caster non-stackable zone limit, if configured by the effect.
        _zone_limit = next((e.get('领域同时上限') for e in spell.get('effects', [])
                            if e.get('type') == 8 and e.get('领域同时上限')), 0)
        if _zone_limit:
            active = [e for e in self._get_effects() if e.get('type') == 'zone'
                      and e.get('spellName') == spell.get('name')
                      and self._resolve_uid(e.get('sourceUserId', '')) == self._resolve_uid(caster_id)
                      and (e.get('remainingRounds', 0) > 0 or e.get('sourceBound'))]
            if len(active) >= int(_zone_limit):
                return ActionExecutionResult(f'{char.name} 的【{spell["name"]}】已存在，无法重复施放。\n', False, 'spell_rejected')

        revive_effects = [e for e in spell.get('effects', []) if e.get('type') == 19]
        if revive_effects and not any(self._revive_candidates(caster_id, e) for e in revive_effects):
            return ActionExecutionResult(f'{char.name} 没有符合条件的复活目标！\n', False, 'no_revive_target')

        # MP cost: support dice expressions and formulas
        mp_cost_raw = spell.get('消耗mp', 0)
        mp_formula = spell.get('_mp_formula', '')
        if spell.get('_mp_formula_transformed') and getattr(char, 'hs_transformed', False):
            mp_cost = roll_dice(str(spell['_mp_formula_transformed']))
        elif isinstance(mp_cost_raw, str) and ('d' in str(mp_cost_raw).lower()):
            mp_cost = max(1, (roll_dice(str(mp_cost_raw)) + 1) // 2) if 'ceil' in str(mp_cost_raw) else roll_dice(str(mp_cost_raw))
        elif mp_formula == 'ceil_2d6_div_2':
            mp_cost = max(1, (roll_dice('2d6') + 1) // 2)
        elif mp_formula == 'summon_count_ceil_half':
            # 数量已在纯召唤计划里掷过；按实际获准数量计费（非法术对象共享状态）
            summon_eff = next((e for e in spell.get('effects',[]) if e.get('type')==5), None)
            if summon_eff:
                _ei = spell.get('effects', []).index(summon_eff)
                if _ei in summon_plan:
                    pre_count = summon_plan[_ei]
                else:
                    count_raw = summon_eff.get('召唤个数', 1)
                    if isinstance(count_raw, str) and 'd' in str(count_raw):
                        pre_count = roll_dice(str(count_raw))
                    else:
                        pre_count = int(count_raw) if count_raw else 1
                    summon_plan[_ei] = pre_count
                mp_cost = max(1, (pre_count + 1) // 2) if pre_count else 0
            else:
                mp_cost = int(mp_cost_raw) if mp_cost_raw else 0
        elif spell.get('mp_cost_formula') == 'max_25pct_cur_50pct':
            # 夜诏明 s2: max(25% maxMP, 50% curMP)
            max_mp = char.get_attr('魔力上限', char.get_attr('魔力', 26)) or 26
            cur_mp_raw = char.get_attr('魔力', 0) or 0
            pct_max = max(1, max_mp * 25 // 100)
            pct_cur = max(1, cur_mp_raw * 50 // 100)
            mp_cost = max(pct_max, pct_cur)
            spell['_actual_mp_cost'] = mp_cost  # Store for damage calculation
        else:
            mp_cost = int(mp_cost_raw) if mp_cost_raw else 0

        san_cost_raw = spell.get('消耗san', 0)
        if isinstance(san_cost_raw, str) and 'd' in str(san_cost_raw).lower():
            san_cost = roll_dice(str(san_cost_raw))
        else:
            san_cost = int(san_cost_raw) if san_cost_raw else 0

        # ── 射程检查: type=1 敌方效果 (MP扣除前) ──
        dmg_effs = [e for e in spell.get('effects', []) if e.get('type') == 1
                    and str(e.get('客体', 0)).upper() in ('4', '5', '24', '45', 'R', 'RP')]
        _orig_coord = ''
        if dmg_effs:
            atk_range = self._get_attack_range(spell_effect=dmg_effs[0])
            can_atk, _orig_coord = self._move_to_attack_range(caster_id, target_id, atk_range)
            if not can_atk:
                return ActionExecutionResult(f'{char.name} 无法接近目标！（射程={atk_range}）', False, 'spell_rejected')

        # ── consume_moon（月光王座）：伤害 = 月能×damage_moon_mult，消耗全部月能并销毁月亮 ──
        # 必须在 MP 扣除之前结算：无月亮时禁止施放且不消耗任何资源；
        # 有月亮时一次性消耗（多个伤害效果/满值穿透都不会重复扣）。
        moon_payment = None
        for _cm_eff in spell.get('effects', []):
            if _cm_eff.get('consume_moon'):
                caster_entry_cm = next((e for e in self._get_initiative() if e['userId'] == caster_id), None)
                caster_team_cm = caster_entry_cm.get('team', 'Y') if caster_entry_cm else 'Y'
                moons_cm = self._find_summons_by_field(caster_team_cm, 'replenish_allies')
                if not moons_cm:
                    return ActionExecutionResult(f'{char.name} 无虚假之月，无法释放【{spell["name"]}】！\n', False, 'spell_rejected')
                moon_char_cm = moons_cm[0][1]
                moon_mp_cm = moon_char_cm.get_attr('魔力', 0) or 0
                if moon_mp_cm <= 0:
                    return ActionExecutionResult(f'{char.name} 月能为0，无法释放【{spell["name"]}】！\n', False, 'spell_rejected')
                mult_cm = _cm_eff.get('damage_moon_mult', 5)
                spell['_moon_dmg'] = max(1, int(moon_mp_cm * mult_cm))
                moon_payment = (moons_cm[0][0], moon_char_cm, moon_mp_cm)
                break

        # Check counter cost (rings, etc.)
        deferred_counters = {}
        defer_counters = self._is_y14(caster_id)
        cost_ct = spell.get('cost_counter_type', '')
        cost_ca = spell.get('cost_counter_amount', 0)
        if cost_ct and cost_ca:
            if (self._get_counter(caster_id, cost_ct) < cost_ca if defer_counters else
                    not self._consume_counter(caster_id, cost_ct, cost_ca)):
                return ActionExecutionResult(f'{char.name} 资源不足！需要 {cost_ca} {cost_ct}。\n', False, 'spell_rejected')
            if defer_counters:
                deferred_counters[cost_ct] = cost_ca
            out += f'  消耗 {cost_ca} {cost_ct}\n'

        # Check counter requirement (e.g. 极限技·凶斩)
        cr = spell.get('counter_required')
        if cr:
            cr_type, cr_amount = cr
            if self._get_counter(caster_id, cr_type) < cr_amount:
                return ActionExecutionResult(f'{char.name} {cr_type}不足！需要 {cr_amount}。\n', False, 'spell_rejected')
            if defer_counters:
                deferred_counters[cr_type] = deferred_counters.get(cr_type, 0) + cr_amount
            else:
                self._consume_counter(caster_id, cr_type, cr_amount)
            out += f'  消耗 {cr_amount} {cr_type}\n'

        # prep_free spells: skip all costs during preparation phase
        is_prep = False
        state = self._get_state()
        if state and state.get('phase') == 'preparation':
            is_prep = True
        prep_free = spell.get('prep_free', False)
        if is_prep and prep_free:
            out += f'  （准备阶段免消耗）\n'
            blood_cost = 0
            mp_cost = 0
            san_cost = 0
            # skip HP%, HP dice, MP% costs too
            spell = dict(spell)  # shallow copy so we don't mutate original
            spell['cost_blood_ml'] = 0
            spell['cost_hp_pct_current'] = 0
            spell['cost_hp_dice'] = ''
            spell['cost_mp_pct_max'] = 0
            spell['cost_mp_pct_current'] = 0

        # Blood cost (cost_blood_ml): consumed before MP; HP fallback if insufficient
        if self._is_y17(caster_id):
            checked_mp = mp_cost
            if checked_mp > 0:
                checked_mp = max(1, int(checked_mp * self._get_buff_mp_cost_pct(caster_id)))
                checked_mp = max(1, checked_mp + self._get_buff_mp_cost_flat(caster_id))
            if char.get_attr('魔力', 0) < checked_mp:
                return ActionExecutionResult('魔力不足，未消耗血液或生命。', False, 'spell_rejected')
        blood_cost = spell.get('cost_blood_ml', 0) or 0
        if blood_cost > 0:
            if self._is_y17(caster_id) and spell['index'] == 2 and self._get_blood(caster_id) < blood_cost:
                self._consume_blood(caster_id, self._get_blood(caster_id))
                backlash = spell.get('_blood_backlash', 0)
                self._set_combat_hp(caster_id, self._hp_safe(caster_id, 0) - backlash)
                out += f'  血液不足，反噬 {backlash} HP\n'
            elif self._get_blood(caster_id) < blood_cost:
                # 血液不足：HP补足差额
                cur_blood = self._get_blood(caster_id)
                shortfall = blood_cost - cur_blood
                hp_to_ml = getattr(char, '_hp_to_blood_ml', 200) or 200
                hp_needed = max(1, (shortfall + hp_to_ml - 1) // hp_to_ml)  # ceil
                cur_hp_b = self._get_combat_hp(caster_id) or 0
                if cur_hp_b <= hp_needed:
                    return ActionExecutionResult(f'{char.name} 血液不足且HP不足以献祭！需要 {blood_cost}ml 血液。\n', False, 'spell_rejected')
                self._consume_blood(caster_id, cur_blood)
                self._set_combat_hp(caster_id, cur_hp_b - hp_needed)
                out += f'  血液不足，献祭 {hp_needed} HP 补足 {shortfall}ml 血液\n'
            else:
                self._consume_blood(caster_id, blood_cost)
                out += f'  消耗 {blood_cost}ml 血液\n'

        # HP percentage cost (cost_hp_pct_current): consumed before MP
        hp_pct = spell.get('cost_hp_pct_current', 0) or 0
        if hp_pct > 0:
            cur_hp = self._get_combat_hp(caster_id) or 0
            hp_cost = max(1, cur_hp * hp_pct // 100)
            self._set_combat_hp(caster_id, cur_hp - hp_cost)
            out += f'  消耗 {hp_cost} HP (献祭)\n'

        # HP dice cost (cost_hp_dice): rolled HP consumption
        hp_dice = spell.get('cost_hp_dice', '')
        if hp_dice:
            hp_cost_d = roll_dice(str(hp_dice))
            cur_hp_d = self._get_combat_hp(caster_id) or 0
            self._set_combat_hp(caster_id, max(0, cur_hp_d - hp_cost_d))
            out += f'  消耗 {hp_cost_d} HP ({hp_dice})\n'

        # MP percentage cost (cost_mp_pct_max): min MP based on max MP
        mp_pct_max = spell.get('cost_mp_pct_max', 0) or 0
        if mp_pct_max > 0:
            max_mp = char.get_attr('魔力上限', char.get_attr('魔力', 26)) or 26
            mp_cost_pct = max(1, max_mp * mp_pct_max // 100)
            # Use the larger of dice cost and percentage cost
            mp_cost = max(mp_cost, mp_cost_pct)

        # MP percentage of current (cost_mp_pct_current)
        mp_pct_cur = spell.get('cost_mp_pct_current', 0) or 0
        if mp_pct_cur > 0:
            cur_mp_for_pct = char.get_attr('魔力', 0) or 0
            mp_cost_cur = max(1, cur_mp_for_pct * mp_pct_cur // 100)
            mp_cost = max(mp_cost, mp_cost_cur)

        # AUX 9/10: MP cost modifiers
        if mp_cost > 0:
            mp_cost = max(1, int(mp_cost * self._get_buff_mp_cost_pct(caster_id)))
            mp_cost += self._get_buff_mp_cost_flat(caster_id)  # AUX code 10
            mp_cost = max(1, mp_cost)

        if any(self._get_counter(caster_id, kind) < amount for kind, amount in deferred_counters.items()):
            return ActionExecutionResult(f'{char.name} 资源不足。\n', False, 'spell_rejected')
        if mp_cost > 0:
            cur_mp = char.get_attr('魔力', 0) or 0
            if cur_mp < mp_cost: return ActionExecutionResult(f'{char.name} 魔力不足！需要 {mp_cost} MP。', False, 'spell_rejected')
            char.set_attr('魔力', cur_mp - mp_cost); out += f'  消耗 {mp_cost} MP\n'
        if moon_payment:
            moon_uid_cm, moon_char_cm, moon_mp_cm = moon_payment
            moon_char_cm.set_attr('魔力', 0)
            self._set_combat_hp(moon_uid_cm, 0)
            out += f'  消耗全部月能 {moon_mp_cm} → 月光王座伤害 {spell["_moon_dmg"]}！虚假之月消逝...\n'
        if mp_cost > 0:
            self._note_inspiration_event(caster_id)
        if san_cost > 0:
            cur_san = char.get_attr('理智', 50)
            char.set_attr('理智', max(0, cur_san - san_cost)); out += f'  消耗 {san_cost} SAN\n'
        for kind, amount in deferred_counters.items():
            self._consume_counter(caster_id, kind, amount)

        # Commit one-per-battle only after target and resource validation.
        if once_required:
            consumed = getattr(char, '_once_consumed', set())
            consumed.add(once_name)
            char._once_consumed = consumed

        # ── 吟唱系统：若法术有吟唱回合 > 0，创建 chanting 效果，不立即执行效果 ──
        chant_rounds = spell.get('吟唱回合', 0) or 0
        if chant_rounds > 0:
            if self._is_y17(caster_id) and self._hp_safe(caster_id, 0) <= 0:
                # Backlash killed the caster: resolve a last uncontrolled area burst.
                burst = dict(spell, 吟唱回合=0, effects=[dict(spell['effects'][0], 客体=135, 作用半径=5)])
                plan = [(0, t) for t in self._resolve_effect_targets(caster_id, target_id, burst['effects'][0])
                        if self._resolve_uid(t) != self._resolve_uid(caster_id)]
                self._execute_spell_plan(caster_id, target_id, burst, plan, False, out + '反噬爆炸！\n')
                return self._spell_plan_output
            effects = self._get_effects()
            chant_entry = {
                'type': 'chanting',
                'sourceUserId': caster_id,
                'targetUserId': caster_id,  # Chant is ON the caster (they're the one chanting)
                'remainingRounds': chant_rounds,
                'spellName': spell['name'],
                'spellIndex': spell['index'],
                'interruptible': spell.get('chant_interruptible', False),
                'stored_effects': spell.get('effects', []),
                'stored_target': target_id,
                'stored_spell_name': spell['name'],
                'stored_spell_index': spell['index'],
                'persistent': 0,
            }
            if self._is_y17(caster_id):
                import copy
                chant_entry['bloodChant'] = True
                chant_entry['remainingRounds'] = max(1, chant_rounds - 1)
                chant_entry['stored_spell'] = copy.deepcopy(spell)
            # Store interrupt penalty if applicable
            if spell.get('chant_interruptible'):
                chant_entry['interrupt_hp_pct'] = spell.get('chant_interrupt_hp_pct', 0)
                chant_entry['interrupt_mp_pct'] = spell.get('chant_interrupt_mp_pct', 0)
            effects.append(chant_entry)
            self._set_effects(effects)
            out += f'  开始吟唱【{spell["name"]}】（{chant_rounds}回合後发动）\n'
            return out

        # ── 被动标记：若法术时机含'1'（被动），所有产生效果标记 sourcePassive ──
        is_passive_spell = has_timing(spell.get('时机', '2'), '1')

        # ── same_target_multiplier: if all damage effects target the same unit, amplify ──
        stm = spell.get('same_target_multiplier', 0)
        if stm > 0:
            dmg_effs_all = [e for e in spell.get('effects', []) if e.get('type') == 1]
            if len(dmg_effs_all) > 1:
                targets = set()
                for de in dmg_effs_all:
                    # 按各 effect 自己的客体解析，不能假定共享 target_id
                    resolved = self._resolve_effect_targets(caster_id, target_id, de)
                    targets.add(tuple(self._resolve_uid(t) for t in resolved))
                if len(targets) == 1 and len(next(iter(targets))) == 1:
                    # Store multiplier so damage handlers can apply it
                    spell['_same_target_mult_active'] = stm
                    out += f'  同一目标加成: ×{stm}\n'
                else:
                    spell.pop('_same_target_mult_active', None)

        # ── 构造逐 effect × 逐目标的执行计划 ──
        # 先全部解析再执行：任意一个 effect 需要外部反应（PvP）时，剩余计划可续跑，
        # 不同 effect 的目标互不污染。
        plan = []
        for eff_idx, eff in enumerate(spell.get('effects', [])):
            if eff.get('type') in (1, 2, 3, 4):
                eff_targets = self._resolve_effect_targets(caster_id, target_id, eff)
                if not eff_targets:
                    tname_eff = self._parse_target_codes(eff)
                    out += f'  [类别{eff.get("letter", eff_idx)}] 没有合法目标（客体={"/".join(tname_eff)}），跳过\n'
                    continue
                for _tid in eff_targets:
                    plan.append((eff_idx, _tid))
            else:
                plan.append((eff_idx, None))

        self._spell_plan_mp_cost = mp_cost
        self._spell_plan_summon_plan = summon_plan
        # Snapshot ordinary-buff action ceilings (zone auras excluded) so a
        # zone cast/membership change never reissues or claws back actions.
        pre_max = {}
        for _e in self._get_initiative():
            _base = self._resolve_uid(_e['userId'])
            if _base not in pre_max:
                pre_max[_base] = self._calc_main_actions(_base, include_zone=False)
        if self._is_y14(caster_id) and spell['index'] in (11, 12):
            self._set_effects([e for e in self._get_effects() if not (
                e.get('sourceUserId') == self._resolve_uid(caster_id) and e.get('spellIndex') in (11, 12))])
            self.get_char(caster_id)._y14_mode = 'swift' if spell['index'] == 11 else 'brave'
        self._execute_spell_plan(caster_id, target_id, spell, plan, is_passive_spell, out)
        if mp_cost > 0:
            self._note_inspiration_event(caster_id)
        # 计划执行完毕后重新读取输出（_execute_spell_plan 内部维护累积输出）
        out = self._spell_plan_output
        if self._is_y14(caster_id):
            if spell['index'] in (1, 2, 3):
                self._y14_start_cooldown(caster_id)
            elif spell['index'] == 19:
                char._y14_dash_round = (self._get_state() or {}).get('round', 1)
                char._y14_dash_distance = 0
        if once_required:
            ledger = getattr(self, '_revive_use_ledger', None)
            if ledger is None:
                ledger = set(); self._revive_use_ledger = ledger
            ledger.add(once_key)
            char._once_consumed = set(getattr(char, '_once_consumed', set()) or ()) | {once_name}
            cooldown = max([int(spell.get('cooldown_rounds', 0) or 0)] +
                           [int(e.get('cooldown_rounds', 0) or 0) for e in spell.get('effects', [])])
            if cooldown > 0:
                cooldowns = self._get_effects()
                cooldowns.append({'type': 'cooldown', 'remainingRounds': cooldown,
                    'spellName': once_name, 'spellIndex': spell.get('index', 0),
                    'sourceUserId': caster_id, 'targetUserId': caster_id, 'persistent': 0})
                self._set_effects(cooldowns)

        # ── MOV变化同步行动数（仅普通 buff 部分，按实际执行前的上限求差）──
        # 每个 effect 可能改变不同单位，统一对全场刷新一次，避免额外行动数不一致
        seen_lookup = set()
        for _e in self._get_initiative():
            _base = self._resolve_uid(_e['userId'])
            if _base in seen_lookup:
                continue
            seen_lookup.add(_base)
            self._sync_actions_from_mov(
                _base,
                old_max=pre_max.get(_base),
                new_max=self._calc_main_actions(_base, include_zone=False))

        return out


    def _execute_spell_plan(self, caster_id, target_id, spell, plan,
                            is_passive_spell, out=''):
        """Execute a pre-resolved [(effect_index, target_uid), ...] plan in order.

        If an item needs external resolution it raises SpellInterrupted with
        the *remaining* plan attached, so PvP can resume without replaying
        already-resolved effects or re-charging costs.
        """
        self._spell_plan_output = out
        idx = 0
        try:
            while idx < len(plan):
                eff_idx, tid = plan[idx]
                eff = spell['effects'][eff_idx]
                ct = eff['type']
                if ct in (1, 2, 3, 4):
                    self._spell_plan_output += self._apply_target_effect(
                        caster_id, tid, eff, spell, is_passive_spell, eff_idx)
                    if ct == 4:
                        self._finish_buff_charge(caster_id, spell)
                elif ct == 5:
                    self._spell_plan_output += self._execute_summon_effect(
                        caster_id, target_id, eff, spell, is_passive_spell)
                elif ct in (6, 7, 8, 9, 10, 11, 12, 13, 14, 17, 18, 19):
                    self._spell_plan_output += self._execute_misc_effect(
                        caster_id, target_id, eff, spell, is_passive_spell, eff_idx)
                idx += 1
        except SpellInterrupted as e:
            # 附带剩余计划；调用方（PvP）在反应结算后调用 resume
            e.data['spell_plan'] = [list(p) for p in plan[idx + 1:]]
            e.data['spell_plan_caster'] = caster_id
            e.data['spell_plan_passive'] = is_passive_spell
            # 已结算效果/头部输出 + 本 effect 的前置检定行，合并给调用方展示
            prior = (self._spell_plan_output or '').rstrip('\n')
            own = list(e.data.get('prefix_lines') or [])
            merged = (prior.split('\n') if prior else []) + own
            e.data['prefix_lines'] = merged
            raise
        if spell.get('_blood_chain') and self._hp_safe(caster_id, 0) > 0:
            # Each extra wave pays from the current pool; new kills can fund the next.
            if self._get_blood(caster_id) >= 3000 and spell.get('_chains', 0) < 4:
                self._consume_blood(caster_id, 3000)
                spell['_chains'] = spell.get('_chains', 0) + 1
                wave = [(i, t) for i, e in enumerate(spell['effects'])
                        for t in self._resolve_effect_targets(caster_id, target_id, e)]
                return self._execute_spell_plan(caster_id, target_id, spell, wave, False,
                    self._spell_plan_output + '  消耗3000ml血液，污血冲击再次发动！\n')
        return self._spell_plan_output

    def resume_spell_plan(self, state):
        """Continue a paused spell plan after a PvP reaction (or no reaction).

        Returns only the *new* output; the caller has already displayed
        state['prefix_lines'] (header + check lines) separately.
        """
        plan = state.get('spell_plan') or []
        spell = state.get('spell') or {}
        caster_id = state.get('spell_plan_caster') or state.get('atk_uid')
        if not plan or not spell or not caster_id:
            return ''
        target_id = state.get('def_uid')
        return self._execute_spell_plan(
            caster_id, target_id, spell, [tuple(p) for p in plan],
            bool(state.get('spell_plan_passive')), '')

    def _finish_buff_charge(self, caster_id, spell):
        charge_moon = spell.get('charge_moon', '')
        if not charge_moon:
            return
        caster_entry = next((e for e in self._get_initiative() if e['userId'] == caster_id), None)
        if caster_entry:
            amt = roll_dice(str(charge_moon)) if isinstance(charge_moon, str) else int(charge_moon)
            charged = self._charge_moon_energy(caster_entry.get('team', 'Y'), amt)
            if charged > 0:
                self._spell_plan_output += f'  月能充能 ({spell["name"]}): +{charged}\n'

    def _execute_summon_effect(self, caster_id, target_id, eff, spell, is_passive_spell):
        """Summon effect body (extracted from _execute_spell for resumable plans)."""
        out = ''
        summon_plan = getattr(self, '_spell_plan_summon_plan', {}) or {}
        mp_cost = getattr(self, '_spell_plan_mp_cost', 0) or 0
        _eff_idx = spell.get('effects', []).index(eff)
        if _eff_idx in summon_plan:
            count = summon_plan[_eff_idx]
        else:
            count_raw = eff.get('召唤个数',1)
            if isinstance(count_raw, str) and 'd' in str(count_raw):
                count = roll_dice(str(count_raw))
            else:
                count = int(count_raw) if count_raw else 1
            # 创建层仍按名额截断，避免直接调用/延迟路径超配
            _remain, _ = self._get_summon_quota(caster_id, eff.get('召唤物模板',''))
            if _remain is not None:
                count = max(0, min(count, _remain))
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
            if sid:
                # Set initial MP from spell cost for unlimited_mp summons
                tmpls_c = getattr(self, '_summon_templates_snapshot', _SUMMON_TEMPLATES)
                if tmpls_c is None:
                    from characters_data import SUMMON_TEMPLATES as tmpls_c
                tmpl_data_c = tmpls_c.get(tmpl, {}) if tmpls_c else {}
                if tmpl_data_c.get('unlimited_mp'):
                    s_char = self.get_char(sid)
                    s_char.set_attr('魔力', mp_cost)
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
        _requested = None
        if isinstance(summon_plan.get('_requested'), dict):
            _requested = summon_plan['_requested'].get(_eff_idx)
        if _requested is not None and _requested != count:
            out += f'  召唤 {tmpl or "使魔"} ×{count}（请求{_requested}，受数量上限截断）\n'
        else:
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
        return out

    def _select_revive_candidate(self, caster_id, candidates):
        owners = {g.get('originalOwnerId') or g.get('ownerId') for g in candidates if g.get('isSummon')}
        for g in candidates:
            if not g.get('isSummon') and g.get('uid') in owners:
                return g
        return sorted(candidates, key=lambda g: (g.get('isSummon', False), g.get('round', 0)))[0]

    def _execute_personal_feast(self, caster_id, eff, spell, craft_rounds):
        il = self._get_initiative(); team = self._battle_team_of(caster_id)
        targets = []
        seen = set()
        for entry in il:
            if entry.get('isSummon') or entry.get('team') != team:
                continue
            uid = self._resolve_uid(entry.get('userId', ''))
            if uid in seen or self._get_combat_hp(uid) is None or (self._get_combat_hp(uid) <= 0 and not self._is_dying(uid)):
                continue
            seen.add(uid); targets.append(uid)
        if not targets:
            return '  没有可分配的队友\n'
        # The caster is always the self slot, regardless of initiative order.
        caster_base = self._resolve_uid(caster_id)
        targets.sort(key=lambda uid: (uid != caster_base, uid))
        n = len(targets); total_cfg = eff.get('分发给总数公式') or {'每人': 2, '加': -1}
        per_ally = int(eff.get('分发队友每人', total_cfg.get('每人', 2)) or 0)
        self_keep = int(eff.get('分发自留', 1) or 0)
        cap = int(eff.get('分发给总数上限', 6) or 6)
        override = (eff.get('分发人数覆盖') or {}).get(str(n), (eff.get('分发人数覆盖') or {}).get(n, {}))
        if override:
            self_keep = int(override.get('分发自留', self_keep)); per_ally = int(override.get('分发队友每人', per_ally))
        if n == 1:
            portions = {targets[0]: int(override.get('分发自留', 3) if override else 3)}
        elif n == 2:
            portions = {targets[0]: self_keep, targets[1]: per_ally}
        else:
            portions = {uid: (self_keep if i == 0 else per_ally) for i, uid in enumerate(targets)}
            formula_total = max(0, int(total_cfg.get('每人', per_ally)) * n + int(total_cfg.get('加', 0)))
            desired_total = min(cap, formula_total)
            excess = max(0, sum(portions.values()) - desired_total)
            if excess and portions[targets[0]]:
                removed = min(excess, portions[targets[0]])
                portions[targets[0]] -= removed
                excess -= removed
            while excess > 0:
                eligible = [uid for uid in targets[1:] if portions[uid] > 0]
                if not eligible:
                    return '  款待配置无法满足总量公式，未消耗资源\n'
                uid = random.choice(eligible)
                portions[uid] -= 1
                excess -= 1
            shortfall = desired_total - sum(portions.values())
            while shortfall > 0:
                eligible = [uid for uid in targets[1:] if portions[uid] < per_ally]
                if not eligible:
                    break
                uid = random.choice(eligible)
                portions[uid] += 1
                shortfall -= 1
        item_name = eff.get('制造物模板', '')
        # One effect makes one cake type; multi-effect feasts call this once per effect.
        for uid, amount in portions.items():
            if amount <= 0: continue
            meta = {'sourceUserId': self._resolve_uid(caster_id), 'sourceTeam': team}
            if eff.get('消费追加回复hp'):
                meta['消费追加回复hp'] = eff.get('消费追加回复hp')
                if eff.get('tasteBonusOwnerSerial'):
                    meta['tasteBonusOwnerSerial'] = eff.get('tasteBonusOwnerSerial')
            self.add_item_to_inventory(uid, item_name, amount, metadata=meta)
        return f'  款待分发 {item_name} ×{sum(portions.values())}（按个人库存）\n'

    def _execute_misc_effect(self, caster_id, target_id, eff, spell, is_passive_spell, eff_idx=0):
        """Non-targeted effect types (craft/trigger/zone/...), extracted for plans."""
        out = ''
        char = self.get_char(caster_id)
        ct = eff['type']
        if ct == 6:  # Create
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
            if eff.get('制造分发') == 'personal_split':
                return self._execute_personal_feast(caster_id, eff, spell, cr)
            effects = self._get_effects()
            for cc in range(count):
                effects.append({'type':'create','craftId':f"craft_{spell['index']}_{cc}",
                    'template':eff.get('制造物模板',''),'remainingRounds':eff.get('持续回合',0),
                    'craftRoundsRemaining':cr,'sourceUserId':caster_id,
                    'sourceTeam':self._battle_team_of(caster_id),
                    'targetUserId':target_id or caster_id,'spellName':spell['name'],
                    'spellIndex':spell['index'],'persistent':spell.get('默认延续性',0)})
            self._set_effects(effects); out += f'  开始制造 {count} 个（{cr}回合）\n'
        elif ct == 19:  # Data-configured revive
            candidates = self._revive_candidates(caster_id, eff)
            # An explicit selection is single-use: clear it after this effect.
            self._revive_selected_uid = None
            if not candidates:
                return '  没有符合条件的复活目标\n'
            target = self._select_revive_candidate(caster_id, candidates)
            hp = self._restore_graveyard_unit(caster_id, target, eff)
            out += f'  复活 {target.get("name", target["uid"])}，HP恢复至 {hp}\n'

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
            # AUX / named modifiers living on the zone itself (live range view)
            raw_aux_type = _eff_get(eff, 'auxType') or ''
            aux_val = _eff_get(eff, 'auxVal') or ''
            aux_code = AUX_NAME_TO_CODE.get(raw_aux_type, 0)
            skill_mod = eff.get('技能加减值', '') or ''
            effect_key = str(eff.get('letter', eff_idx))
            unsupported = []
            aux_type = raw_aux_type
            if raw_aux_type:
                if raw_aux_type in CONTROL_STATUS_TYPES:
                    unsupported.append(f'{raw_aux_type}(控制状态)')
                    aux_type, aux_val, aux_code = '', '', 0
                elif aux_code not in ZONE_AUX_CODES:
                    unsupported.append(f'{raw_aux_type}(不支持持续领域)')
                    aux_type, aux_val, aux_code = '', '', 0
            if str(of).upper().strip() in ('R', 'RP'):
                unsupported.append('R/RP(随机目标)不作为领域过滤')
            # Find caster position for center
            center = 'A1'
            map_data = self._get_map()
            if map_data:
                for c, occ in map_data.get('occupants',{}).items():
                    if occ == caster_id: center = c; break
            effects = self._get_effects()
            caster_base = self._resolve_uid(caster_id)
            active_same = [e for e in effects if e.get('type') == 'zone'
                           and self._resolve_uid(e.get('sourceUserId', '')) == caster_base
                           and e.get('spellName') == spell['name']
                           and (e.get('remainingRounds', 0) > 0 or e.get('sourceBound'))]
            zone_limit = int(eff.get('领域同时上限', 0) or 0)
            if zone_limit and len(active_same) >= zone_limit:
                self._spell_plan_output += f'  【{spell["name"]}】已达到同时存在上限，本次未消耗资源。\n'
                return out
            zone_entry = {'type':'zone','center':center,'radius':radius,'remainingRounds':dur,
                'tickDmg':tick_dmg,'tickHealHp':tick_heal_hp,'tickHealMp':tick_heal_mp,
                'centerFollows':cf,'filter':of,'attributeDebuff':ad,
                'sourceUserId':caster_id,'sourceTeam':self._battle_team_of(caster_id),
                'spellName':spell['name'],'spellIndex':spell['index'],
                'persistent':spell.get('默认延续性',0),
                'stackable':eff.get('可叠加',0),
                'ally_safe':eff.get('ally_safe',0),
                'skillMod':skill_mod,'auxType':aux_type,'auxVal':aux_val,'auxCode':aux_code,
                'effectKey':effect_key,
                'on_enter_mp_drain_pct':eff.get('on_enter_mp_drain_pct',0),
                'on_enter_attr_debuff':eff.get('on_enter_attr_debuff',''),
                'on_enter_trigger_rate':eff.get('on_enter_trigger_rate',1.0),
                'sourceBound': int(bool(eff.get('来源存活绑定'))),
                'dyingFirstAid': int(bool(eff.get('领域急救'))),
                'roundPulsesRemaining': int(eff.get('领域轮首结算次数') or dur),
                'healedTargets': {'hp': {}, 'mp': {}},
                'creationRound': int((self._get_state() or {}).get('round', 1))}
            if eff.get('来源存活绑定') and is_passive_spell:
                zone_entry['sourcePassive'] = True
            effects.append(zone_entry)
            self._set_effects(effects)
            if eff.get('on_enter_mp_drain_pct') or eff.get('on_enter_attr_debuff'):
                self._settle_zone_enters()
            immediate = int(eff.get('领域立即结算', 0) or 0)
            if immediate and (tick_heal_hp or tick_heal_mp):
                result_rows = []
                self._settle_zone_heals(zone_entry, self._get_initiative(), result_rows,
                                        allow_dying=bool(eff.get('领域急救')),
                                        pulse_id=f"cast{(self._get_state() or {}).get('round', 1)}")
                self._set_effects(effects)
                self._zone_tick_results = getattr(self, '_zone_tick_results', []) + result_rows
                self._queue_zone_events(result_rows)
            dur_text = f' ({dur} rounds)' if dur < 99 else ''
            out += 'Zone created, radius=%s%s, center=%s' % (radius, dur_text, center) + chr(10)
            for item in unsupported:
                out += '领域辅助效果不生效: %s' % item + chr(10)
            if skill_mod:
                out += 'Zone modifier: %s' % skill_mod + chr(10)
            if aux_type:
                out += 'Zone auxiliary: %s %s' % (aux_type, aux_val) + chr(10)

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
            bs_entry = {
                'type': 'battleSpirit',
                'sourceUserId': caster_id,
                'sourceTeam': self._battle_team_of(caster_id),
                'persistent': 1,
                'remainingRounds': 999,
                'spellName': spell['name'],
                'spellIndex': spell['index'],
            }
            # Per-source tuning lives in the CharacterData effect; only carry keys
            # that were actually declared so the generic defaults stay intact.
            for key in ('战意初始层数', '战意削弱概率', '战意削弱概率奖励层',
                        '战意奖励骰阈值', '战意惩罚1阈值', '战意惩罚2阈值', '战意惩罚骰上限'):
                val = _eff_get(eff, key)
                if val not in (None, ''):
                    bs_entry[key] = val
            effects.append(bs_entry)
            self._set_effects(effects)
            out += f'  战意值光环已激活\n'

        elif ct == 12:  # Data-configured special damage modifier.
            effects = self._get_effects()
            effects.append(dict(type='damageModifier', sourceUserId=caster_id, targetUserId=caster_id,
                                variant=eff.get('variant', ''), remainingRounds=eff.get('持续回合', 1),
                                spellName=spell['name'], spellIndex=spell['index'],
                                sourcePassive=bool(is_passive_spell)))
            self._set_effects(effects)
            out += f'  特殊防护已生效：{eff.get("variant", "")}\n'

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
                self.load_spells(caster_id)
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
                             spell_index=0, atk_rank=1, atk_roll=0, spell=None,
                             moon_drain=0):
        """Apply direct spell damage to target. Shared by base and reaction paths.
        When atk_rank > 1, applies COC7 rank-based damage (rank 2=advantage, rank 3=max/pen, rank 4=×2).
        moon_drain: 月相吸取（moon_phase）— 目标实际受伤后等量治疗施法者（上限 体力上限）。
        Returns output string for the damage application step."""
        out = ''
        # AUX 30: 月相的庇佑 — 免疫一次伤害
        if self._consume_immunity(target_id):
            out += f'  月相的庇佑：免疫本次伤害！\n'
            return out
        # Chant interrupt: if target has an interruptible chant, break it
        ci_msgs = self._check_chant_interrupt(target_id)
        for m in ci_msgs: out += f'  {m}\n'
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
        old_target_hp = self._hp_safe(target_id, 0)
        cur_hp = old_target_hp
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
        self._set_combat_hp(target_id, cur_hp, source_dmg=eff_dmg, damage_source=caster_id)
        out += f'  伤害: {dmg_detail} → {eff_dmg}点 (HP:{cur_hp})\n'
        out += self._y17_collect_hit(caster_id, target_id,
                                    max(0, old_target_hp - self._hp_safe(target_id, 0)), spell)

        # Lifesteal: heal caster based on damage dealt
        if lifesteal_ratio > 0 and eff_dmg > 0:
            char = self.get_char(caster_id)
            heal = int(dmg_val * lifesteal_ratio)
            chp = self._get_combat_hp(caster_id)
            if chp is None:
                chp = 10
            mhp = char.get_attr('体力上限', chp)
            self._set_combat_hp(caster_id, min(chp + heal, mhp))
            actual_heal = max(0, (self._get_combat_hp(caster_id) or 0) - chp)
            self._charge_heal_overflow(caster_id, heal - actual_heal)
            out += f'  吸血回复 {actual_heal} HP\n'

        # 月相吸取（moon_phase）：目标实际受伤后等量治疗施法者（上限 体力上限）
        if moon_drain > 0 and eff_dmg > 0:
            c_char_md = self.get_char(caster_id)
            chp_md = self._get_combat_hp(caster_id)
            if chp_md and chp_md > 0:
                mhp_md = c_char_md.get_attr('体力上限', chp_md) or chp_md
                heal_md = min(moon_drain, eff_dmg, old_target_hp, max(0, mhp_md - chp_md))
                if heal_md > 0:
                    self._set_combat_hp(caster_id, chp_md + heal_md)
                    out += f'  月相吸取: {c_char_md.name} 回复 {heal_md} HP\n'
                self._charge_heal_overflow(caster_id, min(moon_drain, eff_dmg, old_target_hp) - heal_md)

        # On-kill effects: trigger if target died
        target_dead = old_target_hp > 0 and self._is_final_dead(target_id)
        caster_char = self.get_char(caster_id) if caster_id else None
        if target_dead and eff_dmg > 0:
            if spell:
                ok_hp = spell.get('on_kill_heal_hp', 0) or 0
                ok_san = spell.get('on_kill_heal_san', 0) or 0
                ok_blood = spell.get('on_kill_blood_ml', 0) or 0
                if ok_hp > 0 and caster_char and self._hp_safe(caster_id, 0) > 0:
                    chp_k = self._get_combat_hp(caster_id) or 10
                    mhp_k = caster_char.get_attr('体力上限', chp_k)
                    self._set_combat_hp(caster_id, min(chp_k + ok_hp, mhp_k))
                    self._charge_heal_overflow(caster_id, max(0, chp_k + ok_hp - mhp_k))
                    out += f'  击杀回复 {ok_hp} HP\n'
                if ok_san > 0 and caster_char:
                    self._restore_resource(caster_id, ok_san, 'san')
                    out += f'  击杀回复 {ok_san} SAN\n'
                if ok_blood > 0:
                    self._add_blood(caster_id, ok_blood)
                    out += f'  收集血液 +{ok_blood}ml\n'
            # blood_per_hit_ml: collect blood for each hit (always, not just on kill)
            if spell:
                bph = spell.get('blood_per_hit_ml', 0) or 0
                if bph > 0:
                    self._add_blood(caster_id, bph)
                    out += f'  汲取血液 +{bph}ml\n'

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

    def _roll_spell_success_bp(self, caster_id, eff, spell=None):
        """Roll an effect's success check with attacker-side bonus/penalty dice.

        The effect-inherent 成功率奖惩骰 is folded together with the caster's
        active buffs (generic AUX16/17/18) into one net count. An effect may
        opt out of the caster's buffs with 忽略全局奖惩骰=1, keeping only its
        own declared dice.
        """
        ignore_global = str(eff.get('忽略全局奖惩骰', 0)).strip() == '1'
        caster_buffs = [] if ignore_global else self._get_active_buffs(caster_id)
        skill_name = (spell or {}).get('name', '') or eff.get('技能名', '')
        inherent = eff.get('成功率奖惩骰', 0) or 0
        bp = _calc_net_bp(caster_buffs, '', skill_name, extra_tokens=[inherent, (spell or {}).get('_command_bp', '')])
        return roll_d100(bp)

    def _handle_spell_damage_effect(self, caster_id, target_id, eff, spell):
        """Execute a single type==1 damage effect. Returns output string.
        Base implementation: success rate -> direct damage (no reaction).
        FullBattleEngine overrides to insert reaction between success and damage."""
        out = ''
        dmg_dice = eff.get('伤害骰', '1d4')
        char = self.get_char(caster_id) if caster_id else None
        # ── New damage formulas (computed before _resolve_db) ──
        if eff.get('damage_fixed'):
            dmg_dice = str(eff['damage_fixed'])
        elif eff.get('damage_moon_mult'):
            # 月光王座：伤害 = 月能×倍率（月能已在 _execute_spell 一次性消耗并存于 spell['_moon_dmg']）
            dmg_dice = str(spell.get('_moon_dmg', 1) if spell else 1)
        elif eff.get('damage_mp_multiplier'):
            mp_spent = spell.get('_actual_mp_cost', 0) if spell else 0
            dmg_val = mp_spent * eff['damage_mp_multiplier']
            dmg_dice = str(dmg_val)
        elif eff.get('damage_pct_caster_max_hp'):
            if char:
                max_hp = char.get_attr('体力上限', char.get_attr('体力', 10)) or 10
                dmg_val = max(1, max_hp * eff['damage_pct_caster_max_hp'] // 100)
                dmg_dice = str(dmg_val)
        elif eff.get('damage_pct_target_max_hp'):
            tchar = self.get_char(target_id)
            if tchar:
                t_max_hp = tchar.get_attr('体力上限', tchar.get_attr('体力', 10)) or 10
                dmg_val = max(1, t_max_hp * eff['damage_pct_target_max_hp'] // 100)
                dmg_dice = str(dmg_val)
        elif eff.get('damage_pct_caster_cur_hp'):
            if char:
                cur_hp_raw = self._get_combat_hp(caster_id) or 1
                dmg_val = max(1, cur_hp_raw * eff['damage_pct_caster_cur_hp'] // 100)
                dmg_dice = str(dmg_val)
        elif eff.get('damage_table') == 'moon_phase':
            # 血月斩击·月相表：掷 1d8 选月相，吸取月相结算后等量治疗施法者
            # 吸取量暂存 eff['_moon_drain']，随 PvP ReactionNeeded 的 effect 往返存活
            phase_name, phase_dice, phase_drain = _MOON_PHASE_TABLE[random.randint(0, len(_MOON_PHASE_TABLE) - 1)]
            dmg_dice = phase_dice
            eff['_moon_drain'] = roll_dice(phase_dice) if phase_drain else 0
            out += f'  月相判定: {phase_name}（伤害骰 {phase_dice}）\n'
        dmg_dice = _resolve_db(dmg_dice, _get_db_str(char))
        pen = eff.get('可贯穿性', 0)
        leth = eff.get('致死值', 0)
        sr = eff.get('成功率', 0)
        dur = eff.get('持续回合', 0)
        dot_dice = eff.get('每回合伤害骰', '')
        ls = float(eff.get('吸血比例', '0') or '0')

        if eff.get('幻造'):
            dmg_dice, phantom_output = self._phantom_spell_adjustment(caster_id, eff, spell, dmg_dice)
            out += phantom_output

        friend_behavior = eff.get('友方行为', '')
        is_friendly = False
        if friend_behavior:
            il = self._get_initiative()
            ce = self._unit_il_entry(caster_id, il)
            te = self._unit_il_entry(target_id, il)
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
            check_roll, bp_detail = self._roll_spell_success_bp(caster_id, eff, spell)
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
                                            atk_rank=atk_rank, atk_roll=check_roll, spell=spell,
                                            moon_drain=eff.get('_moon_drain', 0))
        return out

    # ---- Battle Spirit processing (环花暖 passive) ----
    def _process_battle_spirit(self):
        """Process 战意值 for enemy summon groups. Called each round from _tick_down.

        State lives on the stable summon group, not on whichever member currently
        holds the initiative slot: representative changes keep their stacks, and
        a group is processed once per round even with several active sources.
        All numbers come from the source's CharacterData type-11 effect.
        """
        effects = self._get_effects()
        sources = []
        il = self._get_initiative()
        for eff in effects:
            if eff.get('type') != 'battleSpirit':
                continue
            src = self._resolve_uid(eff.get('sourceUserId', ''))
            if not src:
                continue
            src_entry = next((e for e in il if self._resolve_uid(e['userId']) == src), None)
            if src_entry is None or (self._get_combat_hp(src) or 0) <= 0:
                continue  # 来源阵亡/离场：光环停止
            if self._has_status(src, '隔离'):
                continue
            sources.append((src, _eff_get(eff, 'sourceTeam') or src_entry.get('team', 'Y'), eff))
        if not sources:
            return []

        msgs = []
        processed = set()
        for src, src_team, eff in sources:
            cfg = self._battle_spirit_config(eff)
            for key in list(self._summon_groups):
                grp = self._summon_groups.get(key)
                if not grp or key in processed:
                    continue
                if self._summon_group_team(key) == src_team:
                    continue  # 只影响敌方召唤组
                members = [m for m in grp.get('members', [])
                           if (self._get_combat_hp(m) or 0) > 0]
                if not members:
                    continue
                processed.add(key)
                label = grp.get('template_name', key)
                stacks = grp.get('battle_spirit')
                if stacks is None:
                    grp['battle_spirit'] = cfg['initial']
                    msgs.append(f"战意值: {label} 获得 {cfg['initial']} 层战意值")
                    continue
                if stacks <= 0:
                    continue
                rate = cfg['rate'] if stacks > cfg['bonus_threshold'] else cfg['rate_bonus']
                success = random.randint(1, 100) <= rate
                if success:
                    grp['battle_spirit'] = stacks - 1
                    stacks = grp['battle_spirit']
                    msgs.append(f"战意值: {label} 削弱至 {stacks} 层")
                    if stacks <= 0:
                        msgs.append(f"战意值: {label} 战意归零，整组撤离战场！")
                        grp['battle_spirit_penalty_dice'] = 0
                        self._withdraw_summon_group(key, reason='battle_spirit')
                        continue
                old_pens = grp.get('battle_spirit_penalty_dice', 0)
                new_pens = 0
                if stacks <= cfg['penalty2_threshold']:
                    new_pens = 2
                elif stacks <= cfg['penalty1_threshold']:
                    new_pens = 1
                new_pens = min(new_pens, cfg['penalty_max'])
                if new_pens != old_pens:
                    grp['battle_spirit_penalty_dice'] = new_pens
                    if new_pens > 0:
                        pen_label = 'p' if new_pens == 1 else f'p{new_pens}'
                        msgs.append(f"战意值: {label} 获得 {new_pens} 惩罚骰 ({pen_label})")
        return msgs

    def _battle_spirit_config(self, eff):
        """Per-source battle-spirit parameters, all declared in CharacterData."""
        def num(key, default):
            try:
                return int(float(_eff_get(eff, key)))
            except (TypeError, ValueError):
                return default
        return {
            'initial': num('战意初始层数', 5),
            'rate': num('战意削弱概率', 50),
            'rate_bonus': num('战意削弱概率奖励层', 50),
            'bonus_threshold': num('战意奖励骰阈值', 3),
            'penalty1_threshold': num('战意惩罚1阈值', 4),
            'penalty2_threshold': num('战意惩罚2阈值', 2),
            'penalty_max': num('战意惩罚骰上限', 2),
        }

    def _summon_group_team(self, group_key):
        grp = self._summon_groups.get(group_key) or {}
        for m in grp.get('members', []):
            mdata = self._summon_members.get(m) or {}
            if mdata.get('team'):
                return mdata['team']
        entry = self._unit_il_entry(group_key, self._get_initiative())
        return (entry or {}).get('team', 'Y')

    def _withdraw_summon_group(self, group_key, reason='battle_spirit'):
        """Remove a whole summon group from battle without recording deaths."""
        grp = self._summon_groups.pop(group_key, None)
        if not grp:
            return
        members = set(grp.get('members', [])) | {group_key, grp.get('representative_uid', group_key)}
        for uid in members:
            self._summon_members.pop(uid, None)
        self._replace_initiative_preserving_actor(
            [e for e in self._get_initiative()
             if self._resolve_uid(e.get('userId', '')) not in members])
        md = self._get_map()
        if md:
            md['occupants'] = {c: uid for c, uid in md.get('occupants', {}).items()
                               if self._resolve_uid(uid) not in members}
            self._set_map(md)
        acts = self._get_actions()
        for uid in members:
            acts.pop(uid, None)
        self._set_actions(acts)

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
                    # Trigger rate is a probability roll, not a skill check;
                    # D100-check buffs must not perturb it.
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
        if self._is_y14(uid):
            if (char.get_attr('永久移除阶段') or 0) & (1 << new_phase):
                return msgs
            # Only form-bound self effects end; independent effects keep running.
            self._set_effects([e for e in self._get_effects() if not (
                e.get('sourceUserId') == self._resolve_uid(uid) and
                e.get('spellIndex') in (8, 11, 12, 19))])
            self._mod_counter(self._resolve_uid(uid), 'rings', -3, 3)
            char._y14_dash_round = None
            char.phase = new_phase
            self.load_spells(uid)
            self._y14_initialize_phase(uid)
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
        previous = getattr(self, '_settling_round', False)
        self._settling_round = True
        try:
            return self._tick_down_effects()
        finally:
            self._settling_round = previous

    def _tick_down_effects(self):
        effects = self._get_effects(); init_list = self._get_initiative()
        msgs = []; new_effects = []

        # Configured zone first-aid resolves before the dying CON save of this round.
        urgent_rows = []
        for zone in effects:
            if zone.get('type') != 'zone' or not zone.get('dyingFirstAid'):
                continue
            if zone.get('remainingRounds', 0) == 0 and not zone.get('sourceBound'):
                continue
            if zone.get('sourceBound') and not self._zone_caster_coord(zone, init_list):
                continue
            self._settle_zone_heals(zone, init_list, urgent_rows, allow_dying=True,
                                    dying_only=True, pulse_id=f"r{(self._get_state() or {}).get('round', 1)}")
        if urgent_rows:
            self._set_effects(effects)
        msgs.extend(f"{r['target_name']} 被治疗领域急救，HP恢复 {r['amount']}" for r in urgent_rows
                    if r.get('type') == 'heal_hp' and (r.get('amount') or r.get('absorbed_excess')))

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
                    self._record_graveyard_entry(uid, reason='dying_failed')
                    self._remove_summons_of_owner(uid, record_collateral=True)
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
                        target_entry = self._unit_il_entry(eff['targetUserId'], self._get_initiative())
                        if target_entry and not target_entry.get('isSummon'):
                            self._remove_summons_of_owner(eff['targetUserId'])
                            msgs.append(f"目标因持续伤害死亡，召唤物一同清除！")
                        elif self._get_summon_group(eff['targetUserId']):
                            self._reconcile_summon_groups()
                            msgs.append(f"目标因持续伤害死亡，维护召唤组！")
                        else:
                            self._replace_initiative_preserving_actor(
                                [e for e in self._get_initiative() if e['userId'] != eff['targetUserId']])
                            msgs.append(f"目标因持续伤害死亡，退出战斗！")
                        init_list = self._get_initiative()

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

            # Chanting: countdown and execute on completion
            if eff.get('type') == 'chanting':
                if eff.get('bloodChant'):
                    if self._hp_safe(eff.get('sourceUserId'), 0) > 0 and eff.get('remainingRounds', 0) > 0:
                        new_effects.append(eff)
                    continue
                eff['remainingRounds'] = eff.get('remainingRounds', 0) - 1
                if eff['remainingRounds'] <= 0:
                    # Execute stored spell effects
                    caster_id = eff.get('sourceUserId', '')
                    target_id = eff.get('stored_target', caster_id)
                    stored_effects = eff.get('stored_effects', [])
                    spell_name = eff.get('spellName', '')
                    spell_index = eff.get('spellIndex', 0)
                    caster_char = self.get_char(caster_id) if caster_id else None
                    chant_out = f'{caster_char.name if caster_char else "?"} 吟唱完成【{spell_name}】→ 发动！\n'
                    fake_spell = {'name': spell_name, 'index': spell_index, 'effects': stored_effects}
                    for seff in stored_effects:
                        ct = seff.get('type', 1)
                        if ct == 1:
                            chant_out += self._handle_spell_damage_effect(caster_id, target_id, seff, fake_spell)
                        elif ct == 2:
                            sv = roll_dice(seff.get('护盾值', '1d4'))
                            dur_s = seff.get('持续回合', 1)
                            effs_s = self._get_effects()
                            effs_s.append({'type': 'shield', 'value': sv, 'remainingRounds': dur_s,
                                'sourceUserId': caster_id, 'targetUserId': target_id or caster_id,
                                'spellName': spell_name, 'spellIndex': spell_index, 'persistent': 0})
                            self._set_effects(effs_s)
                            chant_out += f'  获得 {sv} 点护盾\n'
                        elif ct == 3:
                            tid_h = target_id or caster_id
                            hp_heal = roll_dice(seff.get('回复hp', '0'))
                            mp_heal = roll_dice(seff.get('回复mp', '0'))
                            tchar = self.get_char(tid_h)
                            if hp_heal > 0:
                                self._apply_hp_heal_amount(tid_h, hp_heal)
                                chant_out += f'  回复 HP +{hp_heal}\n'
                            if mp_heal > 0:
                                self._restore_resource(tid_h, mp_heal)
                                chant_out += f'  回复 MP +{mp_heal}\n'
                        elif ct == 5:
                            # Summon on chant completion
                            count_raw = seff.get('召唤个数', 1)
                            if isinstance(count_raw, str) and 'd' in str(count_raw):
                                count = roll_dice(str(count_raw))
                            else:
                                count = int(count_raw) if count_raw else 1
                            tmpl = seff.get('召唤物模板', '')
                            for _ in range(count):
                                self._create_summon(caster_id, tmpl)
                            chant_out += f'  召唤 {count} 个【{tmpl or "使魔"}】\n'
                    msgs.append(chant_out)
                    continue  # Don't keep expired chanting effect
                else:
                    new_effects.append(eff)
                continue

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

                # Source-bound passive zones live for the source's active lifetime.
                # Isolation pauses membership without consuming any duration.
                if eff.get('type') == 'zone' and eff.get('sourceBound'):
                    source_uid = eff.get('sourceUserId', '')
                    source_entry = self._unit_il_entry(source_uid, self._get_initiative()) if source_uid else None
                    source_alive = bool(source_entry and (self._get_combat_hp(source_uid) or 0) > 0)
                    if not source_alive:
                        msgs.append(f"光环领域【{eff.get('spellName','')}】因来源离场而消散")
                        continue
                    if eff.get('sourcePassive') and self._has_status(source_uid, '隔离'):
                        new_effects.append(eff)
                        continue
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

            # Source-bound passive auras are lifetime-bound, not long timers.
            if eff.get('type') == 'zone' and eff.get('sourceBound'):
                src = eff.get('sourceUserId', '')
                source_entry = self._unit_il_entry(src, self._get_initiative()) if src else None
                if source_entry and (self._get_combat_hp(src) or 0) > 0:
                    new_effects.append(eff)
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

        # Natural restoration precedes paid top-ups; the moon explicitly opts out.
        has_moon = any(self._find_summons_by_field(team, 'replenish_allies') for team in ('Y', 'X'))
        if has_moon:
            self._round_mp_regeneration(init_list)
        effects = self._get_effects()

        # ── hp_threshold_grant: spend summon MP to grant AUX when ally HP is low ──
        # 必须在 replenish_allies 之前执行：补满会把 HP 拉回上限，使 <50% 条件永远不成立。
        for eff in list(effects):
            hpt = eff.get('hp_threshold_grant')
            if not hpt or not isinstance(hpt, dict): continue
            tid = eff.get('targetUserId', '')
            if not tid: continue
            hp = self._get_combat_hp(tid)
            if hp is None or hp <= 0: continue
            ach = self.get_char(tid)
            max_hp = ach.get_attr('体力上限', hp) or hp
            hp_pct = float(hp) / float(max_hp) if max_hp > 0 else 1.0
            if hp_pct >= hpt.get('ratio', 0.5): continue
            # Check for existing buff of this AUX type
            target_aux = hpt.get('aux_code', 0)
            if any(e2.get('auxCode') == target_aux and e2.get('targetUserId') == tid
                   and e2.get('remainingRounds', 0) > 0 for e2 in effects): continue
            team = next((ee.get('team', 'Y') for ee in init_list if ee['userId'] == tid), 'Y')
            cost = hpt.get('cost_moon', 0)
            moons = self._find_summons_by_field(team, 'replenish_allies')
            if not moons: continue
            moon_char = moons[0][1]
            moon_mp = moon_char.get_attr('魔力', 0) or 0
            if moon_mp >= cost:
                moon_char.set_attr('魔力', moon_mp - cost)
                self._apply_immunity_buff(tid, moons[0][0])
                msgs.append(f'hp_threshold_grant: 消耗{cost}MP，{ach.name} 获得 AUX{target_aux}！(剩余:{moon_mp - cost})')

        # ── replenish_allies: summon spends MP to top up allies with linked buff ──
        for entry in init_list:
            if not entry.get('isSummon'): continue
            if (self._get_combat_hp(entry['userId']) or 0) <= 0: continue
            tmpl_data = self._get_summon_template(entry.get('name', '')) or {}
            if not tmpl_data.get('replenish_allies'): continue
            moon_uid = entry['userId']
            moon_char = self.get_char(moon_uid)
            moon_mp = moon_char.get_attr('魔力', 0) or 0
            if moon_mp <= 0: continue
            team = entry.get('team', 'Y')
            template_name = entry.get('name', '')
            for ally in init_list:
                if ally.get('team') != team: continue
                aid = ally['userId']
                if not template_name or not self._has_linked_buff(aid, template_name): continue
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
                msgs.append(f'{template_name}·补满 → {ach.name}: HP+{hp_part} MP+{mp_part} (剩余:{moon_mp})')
                if moon_mp <= 0: break
            # Maintenance MP drain
            maint = tmpl_data.get('maintenance_mp', 0) or 0
            if maint > 0:
                moon_mp = moon_char.get_attr('魔力', 0) or 0
                moon_mp -= maint
                if moon_mp <= 0:
                    moon_char.set_attr('魔力', 0)
                    if not tmpl_data.get('keep_on_empty'):
                        self._set_combat_hp(moon_uid, 0)
                        msgs.append(f'{template_name} 月能耗尽，消逝...')
                    else:
                        msgs.append(f'{template_name} 月能归零，等待充能')
                else:
                    moon_char.set_attr('魔力', moon_mp)
                    msgs.append(f'{template_name} 维持消耗 {maint} MP (剩余:{moon_mp})')

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

        if not has_moon:
            self._round_mp_regeneration(init_list)
        self._reconcile_summon_groups()
        return '\n'.join(msgs) if msgs else ''

    # ---- Summon system (stub — overridden by subclasses) ----
    def _create_summon(self, caster_id, template_name, summon_group=None, shared_roll=None, shared_rank=None):
        return None  # Override in FullBattleEngine / FastBattleEngine

    def _summon_attack(self, summon_id):
        pass  # Override

    def _apply_zone_effects(self):
        pass  # Override

    def _process_zone_specials(self):
        return self._settle_zone_enters()

    def _check_trinity_merge(self):
        pass  # Override


    # ---- Shared zone healing ----
    def _zone_heal_targets(self, eff, il, kind):
        """Yield eligible (uid, entry) for a zone heal, including hidden summons."""
        team = eff.get('sourceTeam')
        if team is None:
            src = eff.get('sourceUserId')
            team = self._battle_team_of(src) if src else 'Y'
        cp = parse_coord(self._zone_caster_coord(eff, il))
        if not cp:
            return []
        radius = eff.get('radius', 0) or 0
        picked = []
        seen = set()
        # Hidden summon-group members are not in initiative but are valid allies.
        entries = list(il)
        for sid in getattr(self, '_summon_members', {}):
            ent = self._unit_il_entry(sid, il)
            if ent is not None:
                entries.append(ent)
        for entry in entries:
            uid = entry.get('userId', '')
            base = self._resolve_uid(uid)
            if base in seen:
                continue
            seen.add(base)
            if entry.get('team') != team:
                continue
            if entry.get('isSummon') and entry.get('ignited'):
                continue
            ep = parse_coord(entry.get('coord', '') or '')
            if not ep:
                continue
            if max(abs(ep[0] - cp[0]), abs(ep[1] - cp[1])) > radius:
                continue
            if self._has_status(uid, '隔离'):
                continue
            if not self._zone_covers_uid(eff, uid, il):
                continue
            picked.append((uid, entry))
        return picked

    def _settle_zone_heals(self, eff, il, tick_results, *, allow_dying=False,
                           dying_only=False, pulse_id=None):
        """Settle one configured pulse once per target, HP and MP independently."""
        pulse_id = str(pulse_id if pulse_id is not None else (self._get_state() or {}).get('round', 1))
        healed = eff.setdefault('healedTargets', {'hp': {}, 'mp': {}})
        spell_name = eff.get('spellName', '')
        for kind, key in (('hp', 'tickHealHp'), ('mp', 'tickHealMp')):
            dice = eff.get(key)
            if not dice:
                continue
            done = healed.setdefault(kind, {})
            for uid, entry in self._zone_heal_targets(eff, il, kind):
                if dying_only and not self._is_dying(uid):
                    continue
                if done.get(uid) == pulse_id:
                    continue
                amount = roll_dice(dice)
                if kind == 'hp':
                    amount = int(amount * self._get_buff_heal_pct(uid, 'hp'))
                    amount += self._get_buff_heal_flat(uid, 'hp')
                    outcome = self._apply_hp_heal_amount(uid, amount, allow_dying=allow_dying)
                    actual, absorbed = outcome['applied'], outcome['absorbed_excess']
                else:
                    amount = int(amount * self._get_buff_heal_pct(uid, 'mp'))
                    amount += self._get_buff_heal_flat(uid, 'mp')
                    actual = self._restore_resource(uid, amount)
                    absorbed = 0
                done[uid] = pulse_id
                tname = entry.get('displayName', entry.get('name', uid))
                tick_results.append({
                    'spell_name': spell_name, 'target_name': tname,
                    'type': f'heal_{kind}', 'dice': dice, 'roll': amount,
                    'amount': actual, 'absorbed_excess': absorbed,
                    'hp': self._get_combat_hp(uid) or 0,
                    'hp_max': self.get_char(uid).get_attr('体力上限', 1) if self.get_char(uid) else 1,
                    'alive': True,
                })

    # ---- Item/cake system (shared by both engines) ----
    def _inventory_item_allowed(self, entry, owner_uid, user_uid=None, target_uid=None):
        """Return whether an inventory stack may be used by this team."""
        source_team = entry.get('sourceTeam')
        if not source_team:
            return True  # Legacy items, including starting personal inventory.
        return source_team == self._battle_team_of(user_uid or owner_uid)

    def _apply_item_heal(self, item_name, target_id, metadata=None):
        """Apply an item's healing effect through the shared HP-heal path."""
        from characters_data import ITEM_TEMPLATES
        ITEM_TEMPLATES = getattr(self, '_item_templates_snapshot', ITEM_TEMPLATES)
        item = ITEM_TEMPLATES[item_name]
        tchar = self.get_char(target_id)
        tname = tchar.name if tchar else target_id
        hp_heal = int(roll_dice(item.get('回复hp', '0')) * self._get_buff_heal_pct(target_id, 'hp'))
        hp_heal += self._get_buff_heal_flat(target_id, 'hp')
        mp_heal = int(roll_dice(item.get('回复mp', '0')) * self._get_buff_heal_pct(target_id, 'mp'))
        mp_heal += self._get_buff_heal_flat(target_id, 'mp')
        san_heal = roll_dice(item.get('回复san', '0'))
        meta = metadata or {}
        # Tasting is a consumption event, not a spell: only cakes marked by
        # the maker contribute the extra healing, and only once per cake.
        extra_dice = meta.get('消费追加回复hp', '')
        if extra_dice and not meta.get('消费追加已触发'):
            hp_heal += int(roll_dice(str(extra_dice)) * self._get_buff_heal_pct(target_id, 'hp'))
            meta['消费追加已触发'] = 1
        parts = []
        if hp_heal > 0:
            outcome = self._apply_hp_heal_amount(target_id, hp_heal)
            if outcome['rescued']:
                parts.append(f"脱离濒死 +{outcome['applied']} HP")
            elif outcome['applied']:
                parts.append(f"{outcome['applied']} HP")
        if mp_heal > 0:
            self._restore_resource(target_id, mp_heal)
            parts.append(f'{mp_heal} MP')
        if san_heal > 0:
            self._restore_resource(target_id, san_heal, 'san')
            parts.append(f'{san_heal} SAN')
        # Apply buff if item template specifies one (e.g. 闪避+10 from cake).
        buff_msg = ''
        skill_mod = item.get('技能加减值', '')
        aux_type = item.get('辅助效果', '')
        aux_val = item.get('辅助效果值', '')
        buff_dur = item.get('buff持续回合', 0)
        if (skill_mod or aux_type) and buff_dur > 0:
            effects = self._get_effects()
            existing = None
            for e in effects:
                if (e.get('type') == 'buff'
                        and self._resolve_uid(e.get('targetUserId', '')) == self._resolve_uid(target_id)
                        and e.get('spellName') == f'食用【{item_name}】'):
                    existing = e; break
            if existing:
                existing['remainingRounds'] = max(existing.get('remainingRounds', 0), buff_dur)
            else:
                effects.append({
                    'type': 'buff', 'remainingRounds': buff_dur,
                    'skillMod': skill_mod,
                    'auxType': aux_type, 'auxVal': aux_val,
                    'auxCode': AUX_NAME_TO_CODE.get(aux_type, 0),
                    'sourceUserId': target_id, 'targetUserId': target_id,
                    'spellName': f'食用【{item_name}】', 'spellIndex': 0,
                    'persistent': 0, 'stackable': 0,
                })
            self._set_effects(effects)
            buff_msg = f"，{skill_mod or (aux_type + ' ' + aux_val)}({buff_dur}回合)"
        return f"食用【{item_name}】→ {tname} 回复 {'+'.join(parts) or '0'}{buff_msg}"

    def _eat_cake(self, eater_id, target_id=None):
        """Consume a usable cake from inventory, or a same-team crafted pool.
        Returns (result_text, success)."""
        from characters_data import ITEM_TEMPLATES
        ITEM_TEMPLATES = getattr(self, '_item_templates_snapshot', ITEM_TEMPLATES)
        tid = target_id or eater_id
        if not self.get_char(tid) or self._battle_team_of(tid) != self._battle_team_of(eater_id):
            return ('只能给同队存活或濒死单位食用蛋糕', False)
        if (self._get_combat_hp(tid) or 0) <= 0 and not self._is_dying(tid):
            return ('目标已死亡，无法食用蛋糕', False)

        # 1) Prefer the eater's own inventory; only same-team entries are usable.
        eater_char = self.get_char(eater_id)
        for i, entry in enumerate(eater_char.inventory):
            item_name = entry['item']
            if item_name not in ITEM_TEMPLATES or entry['count'] <= 0:
                continue
            if not self._inventory_item_allowed(entry, eater_id, eater_id, tid):
                continue
            result, ok = self._consume_inventory_item(eater_id, i, item_name, tid)
            if ok:
                return (result, True)

        # 2) Fall back to ready crafted items belonging to the eater's team.
        team = self._battle_team_of(eater_id)
        effects = self._get_effects()
        for i, e in enumerate(effects):
            if e.get('type') != 'create': continue
            if e.get('craftRoundsRemaining', 1) > 0: continue
            if e.get('sourceTeam') and e['sourceTeam'] != team: continue
            tmpl = e.get('template', '')
            if not tmpl or tmpl not in ITEM_TEMPLATES: continue
            result = self._apply_item_heal(tmpl, tid, metadata=e)
            effects.pop(i); self._set_effects(effects)
            return (result, True)
        return ("无可用的蛋糕", False)

    def _has_ready_cake(self, uid=None, target_id=None, item_kind=None):
        """Check usable cakes for a team (or personal inventory for uid)."""
        from characters_data import ITEM_TEMPLATES
        ITEM_TEMPLATES = getattr(self, '_item_templates_snapshot', ITEM_TEMPLATES)
        if uid is None:
            for char in self.characters.values():
                for entry in char.inventory:
                    if entry.get('item', '') in ITEM_TEMPLATES and entry.get('count', 0) > 0:
                        return True
        else:
            if self._find_personal_item(uid, item_kind=item_kind):
                return True
            team = self._battle_team_of(uid)
            if any(e.get('type') == 'create' and e.get('craftRoundsRemaining', 1) <= 0
                   and e.get('template', '') in ITEM_TEMPLATES
                   and (not e.get('sourceTeam') or e['sourceTeam'] == team)
                   for e in self._get_effects()):
                return True
            return False
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

    def setup_battle(self, team_a, team_b, map_size=None, season_status=None):
        if map_size is None:
            from combat_settings import value as battle_setting
            map_size = battle_setting("DEFAULT_MAP_SIZE")
        self._pending_battle_events = []
        self._zone_tick_results = []
        self._inspiration_pending = set()
        self._inspiration_settled = set()
        self._ai_failed_commands = set()
        w, h = map(int, map_size.split("x"))
        self.group_id = f"battle_{random.randint(1000,9999)}"
        # ── 新战斗重置召唤账本 / 分组 / unique 记录（必须在被动施放之前）──
        self._summon_groups = {}
        self._summon_members = {}
        self._summon_total_spawned = {}
        self._revive_use_ledger = set()
        self._summoned_once = {}
        self._summoned_templates = {}
        self._graveyard = {}
        for uid in team_a + team_b:
            self.get_char(uid)._once_consumed = set()
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

        for uid in all_chars:
            if self._is_y14(uid):
                self._y14_initialize_phase(uid)

        # Recalculate actions after passives (may have changed MOV via buffs)
        acts = self._get_actions()
        for uid in all_chars:
            acts[uid] = {"主动": self._calc_main_actions(uid), "附加": 3}
        self._set_actions(acts)

        self._set_state({"phase":"active", "round":1, "activeIndex":0})
        self._resolve_inventory_sources()
        # Skip dead entries to find first valid active
        self._skip_to_valid_active()

    def _resolve_inventory_sources(self):
        """Bind starting-item provenance to its actual owner/team."""
        for uid, char in self.characters.items():
            if not hasattr(char, 'inventory'):
                continue
            owner = self._resolve_uid(uid)
            team = self._battle_team_of(owner)
            for entry in char.inventory:
                if entry.get('sourceTeam') is None:
                    entry['sourceTeam'] = team
                if entry.get('sourceUserId') in (None, ''):
                    entry['sourceUserId'] = owner

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
        # ── protect_allies_on_hit: summon's allies gain immunity when it is attacked ──
        def_entry = self._unit_il_entry(def_uid, self._get_initiative())
        if def_entry and def_entry.get('isSummon'):
            def_tmpl = self._get_summon_template(def_entry.get('name', '')) or {}
            if def_tmpl.get('protect_allies_on_hit'):
                owner_id = def_entry.get('ownerId')
                def_name = def_entry.get('name', '')
                il_clone = self._get_initiative()
                for e_clone in il_clone:
                    if e_clone['userId'] != def_uid:
                        is_same_type = e_clone.get('isSummon') and (self._get_summon_template(e_clone.get('name', '')) or {}).get('protect_allies_on_hit')
                        is_owner_of = e_clone.get('baseUserId', e_clone['userId']) == owner_id or e_clone['userId'] == owner_id
                        if is_same_type or is_owner_of:
                            self._apply_immunity_buff(e_clone['userId'], def_uid)
                            lines.append(f'  {def_name}被攻击 → {e_clone.get("displayName", e_clone.get("name", "?"))} 获得免疫一次伤害！')
        # ── 射程移动 ──
        profile = self._y14_attack_profile(atk_uid)
        atk_range = profile[1] if profile else self._get_attack_range(skill_name=skill_name)
        can_atk, _orig_coord = self._move_to_attack_range(atk_uid, def_uid, atk_range)
        if not can_atk:
            lines.append(f'{aname} 无法接近目标！（射程={atk_range}）')
            return (def_uid, atk_uid, lines)
        # ── 飞行近战限制 ──
        can_melee, fly_penalty = ((True, False) if self._is_y14(atk_uid) and
                                 getattr(achar, 'phase', 2) == 1 else self._can_melee(atk_uid, def_uid))
        if not can_melee:
            lines.append(f'{aname} 无法近战飞行目标 {dname}！')
            return (def_uid, atk_uid, lines)
        if fly_penalty:
            # 飞行近战惩罚骰并入统一净额结算（不再与命令后缀拼成 'pb'）
            bp_suffix = 'p'

        # ── 幻造兵武 (phantom creation): basic attacks also benefit from 幻造 buff ──
        # Check attacker's own buff first; if attacker is a summon, also check owner's buff
        atk_effects = self._get_effects()
        phantom_ce = None
        phantom_owner_uid = None
        for ce in atk_effects:
            if ce.get('type') == 'buff' and ce.get('spellName') == '幻造兵武':
                if ce.get('targetUserId') == atk_uid:
                    phantom_ce = ce
                    break
                # If attacker is a summon, check if owner has the buff
                atk_entry = self._unit_il_entry(atk_uid, self._get_initiative())
                if atk_entry and atk_entry.get('isSummon'):
                    owner_id = atk_entry.get('ownerId', '')
                    if ce.get('targetUserId') == owner_id:
                        phantom_ce = ce
                        phantom_owner_uid = owner_id
        if phantom_ce:
            writing_char = self.get_char(phantom_owner_uid) if phantom_owner_uid else achar
            writing_val = int(writing_char.get_attr('写作', 0) or 0) if writing_char else 0
            if writing_val > 0:
                self._note_inspiration_event(atk_uid)
                w_uid = phantom_owner_uid or atk_uid
                atk_buffs_hz = self._get_active_buffs(w_uid)
                bp_str_hz = _calc_net_bp(atk_buffs_hz, '', '写作')
                roll, bp_detail = roll_d100(bp_str_hz)
                w_rank = success_rank(roll, self._apply_buff_skill_mod(w_uid, writing_val, '写作'))
                if w_rank >= 4:      phantom_val = roll_dice('2d6');  w_label = '大成功'
                elif w_rank == 3:    phantom_val = 6 + roll_dice('1d6'); w_label = '极难成功'
                elif w_rank == 2:    phantom_val = roll_dice('1d6');  w_label = '困难成功'
                elif w_rank == 1:    phantom_val = roll_dice('1d3');  w_label = '成功'
                elif w_rank == -2:   phantom_val = -roll_dice('1d2'); w_label = '大失败'
                else:                phantom_val = 0;                  w_label = '失败'
                bp_note = f', {bp_detail}' if bp_detail else ''
                if phantom_val != 0:
                    sign = '+' if phantom_val > 0 else ''
                    dmg_dice = f"{dmg_dice}{sign}{phantom_val}"
                    lines.append(f'  幻造兵武·写作检定: D100={roll}/{writing_val}{bp_note} {w_label}！伤害{sign}{phantom_val}')
                else:
                    lines.append(f'  幻造兵武·写作检定: D100={roll}/{writing_val}{bp_note} {w_label}')

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
                    str(e.get('客体', 0)).upper() in ('4', '5', '24', '45', 'R', 'RP')
                    for e in ds.get('effects', []) if e.get('type') == 1
                ) else def_uid
                react_out = self._execute_spell(def_uid, react_target, ds)
                lines.append(f'  [{dname} 反应] {react_out.strip()}')
        eff_skill = self._apply_buff_skill_mod(atk_uid, skill_val, skill_name)
        atk_buffs = self._get_active_buffs(atk_uid)
        # 行动力优势合并到攻击BP（不再字符串拼接，统一净额结算）
        ap_atk_b, ap_def_p = self._calc_action_power_bp(atk_uid, def_uid)
        eff_bp = _calc_net_bp(atk_buffs, bp_suffix, skill_name,
                              extra_tokens=[ap_atk_b] if ap_atk_b else None)
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
        dodge_val = self._apply_buff_skill_mod(def_uid, dodge_val, '闪避')
        bmv = self._apply_buff_skill_mod(def_uid, bmv, bmn)
        def_buffs = self._get_active_buffs(def_uid)
        _, ap_def_p2 = self._calc_action_power_bp(atk_uid, def_uid)

        # ── 格挡可用性检查 ──
        block_name, block_val = self._get_block_skill(def_uid)
        block_val = self._apply_buff_skill_mod(def_uid, block_val, block_name)
        # 防御方反应骰：按实际反应技能名结算，不再把攻方技能名传给防御方
        _def_extra = [ap_def_p2] if ap_def_p2 else None
        def_bp_dodge = _calc_net_bp(def_buffs, '', '闪避', extra_tokens=_def_extra)
        def_bp_counter = _calc_net_bp(def_buffs, '', bmn, extra_tokens=_def_extra)
        def_bp_block = _calc_net_bp(def_buffs, '', block_name, extra_tokens=_def_extra)
        can_block = (dchar.get_attr('可格挡', 0) == 1
                     and block_name is not None and block_val > 0)
        block_hp = (self._unit_il_entry(def_uid, self._get_initiative()) or {}).get('shield_block_hp', 0)
        # Check for block_redirect (summon takes damage instead of defender)
        _block_redirect = self._get_block_redirect_target(def_uid)
        can_block = can_block and (block_hp > 0 or _block_redirect is not None)

        # ── 击晕：禁止所有反应 → 攻击者直接命中 ──
        if not self._can_react(def_uid):
            lines.append(f'  {dname} 击晕中，无法反应！')
            react_choice = None
        else:
            # ── 三维反应选择 (dodge / counter / block) ──
            defender = self._unit_il_entry(def_uid, self._get_initiative()) or {}
            ai_dw = self._ai_react_dodge_w.get(def_uid, defender.get('react_dodge_w', 50))
            ai_cw = self._ai_react_counter_w.get(def_uid, defender.get('react_counter_w', 50))
            ai_bw = self._ai_react_block_w.get(def_uid, defender.get('react_block_w', 0)) if can_block else 0
            total_w = ai_dw + ai_cw + ai_bw
            if total_w > 0:
                r = random.random() * total_w
                if r < ai_dw: react_choice = 'dodge'
                elif r < ai_dw + ai_cw: react_choice = 'counter'
                else: react_choice = 'block'
            else:
                react_choice = 'dodge' if dodge_val >= bmv else 'counter'

        if react_choice == 'dodge':
            rr, rd = roll_d100(def_bp_dodge); react_rank = success_rank(rr, dodge_val)
            rbs = f", {rd}" if rd else ""
            lines.append(f"{dname} 的反应【闪避={dodge_val}】:"); lines.append(f"  D100={rr}/{dodge_val}{rbs} {rank_text(react_rank)}")
            eff_atk = atk_rank
            if react_rank > 0: eff_atk -= react_rank
            if react_rank == -2: eff_atk += (2 if eff_atk == -1 else 1)
            eff_atk = max(-2, min(4, eff_atk))
            if eff_atk <= 0:
                if react_rank > 0 and atk_rank > 0:
                    self._y14_event(def_uid, 'dodge')
                lines.append(f"  {dname} 闪避成功！" if react_rank > 0 else "  攻击未命中。")
                return (def_uid, atk_uid, lines)
            winner_rank, winner_uid, loser_uid = eff_atk, atk_uid, def_uid
            loser_name, winner_name, winner_roll, is_counter = dname, aname, atk_result, False
        elif react_choice == 'counter':
            rr, rd = roll_d100(def_bp_counter); react_rank = success_rank(rr, bmv)
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
                # 反击骰按防御方的反击技能名解析（支持 伤害值a/b + damage_suffix_map），
                # 不能直接读 伤害值——只声明了 伤害值a/b 的角色会退化成 1d4。
                cdmg = self._get_damage_dice(def_uid, bmn)
                cpen = dchar.get_attr("伤害贯穿",1)
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
                    achp = max(0, achp - aeff); self._set_combat_hp(def_uid, achp,
                        source_dmg=aeff if self._is_y14(def_uid) or self._is_y14(atk_uid) else 0,
                        damage_source=atk_uid)
                    lines.append(f"  {aname} 造成伤害: {adetail} → {aeff}点")
                    lines.append(f"  {dname} HP: {achp}")

                    # ---- Defender counter-hits attacker ----
                    if not self._is_untargetable(atk_uid):
                        cdmg = self._get_damage_dice(def_uid, bmn)
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
                        dchp = max(0, dchp - deff); self._set_combat_hp(atk_uid, dchp,
                            source_dmg=deff if self._is_y14(def_uid) or self._is_y14(atk_uid) else 0,
                            damage_source=def_uid, is_counter=True)
                        lines.append(f"  {dname} 反击伤害: {ddetail} → {deff}点")
                        lines.append(f"  {aname} HP: {dchp}")
                    else:
                        lines.append(f"  {aname} 不可选中，{dname} 的反击无法命中。")

                    if not self._is_untargetable(atk_uid):
                        lines.append(f"  双方同时命中! ({rank_text(eff_atk)} vs {rank_text(eff_react)})")
                    else:
                        lines.append(f"  仅 {aname} 的攻击命中。")
                    return (atk_uid, def_uid, lines)
                else:
                    lines.append("  无人得手！")
                    return (None, None, lines)
        else:  # react_choice == 'block'
            # ── 格挡反应 ──
            rr, rd = roll_d100(def_bp_block); react_rank = success_rank(rr, block_val)
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
                    rt_entry = self._unit_il_entry(_block_redirect, self._get_initiative())
                    rt_name = rt_entry.get('displayName', '召唤物') if rt_entry else '召唤物'
                    rt_block_hp = rt_entry.get('shield_block_hp', 0) if rt_entry else 0
                    if blk_eff > 0 and rt_block_hp > 0:
                        absorbed = min(blk_eff, rt_block_hp)
                        blk_eff -= absorbed
                        self._set_summon_entry_fields(_block_redirect, shield_block_hp=rt_block_hp - absorbed)
                        lines.append(f"  {rt_name} 盾牌吸收: {absorbed}点 (剩余护盾HP: {rt_block_hp - absorbed})")
                    cur_hp = self._get_combat_hp(_block_redirect) or 10
                    cur_hp = max(0, cur_hp - blk_eff); self._set_combat_hp(_block_redirect, cur_hp, source_dmg=blk_eff, damage_source=atk_uid)
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
                    entry = self._unit_il_entry(def_uid, self._get_initiative())
                    self._set_summon_entry_fields(def_uid, shield_block_hp=block_hp - absorbed)
                    lines.append(f"  盾牌吸收: {absorbed}点 (剩余护盾HP: {block_hp - absorbed})")
                # 溢出伤害扣HP（不触发致死骰）
                cur_hp = self._get_combat_hp(def_uid) or 10
                cur_hp = max(0, cur_hp - blk_eff); self._set_combat_hp(def_uid, cur_hp, source_dmg=blk_eff, damage_source=atk_uid)
                if blk_eff > 0:
                    lines.append(f"  穿透伤害: {blk_detail} → {blk_eff}点")
                else:
                    lines.append(f"  伤害全部被吸收: {blk_detail}")
                lines.append(f"  {dname} HP: {cur_hp}")
                if cur_hp <= 0:
                    loser_entry = self._unit_il_entry(def_uid, self._get_initiative())
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
        lines.append(f"  伤害: {dmg_detail} → {eff_dmg}点")
        # Chant interrupt for basic attacks
        ci_msgs_ba = self._check_chant_interrupt(loser_uid)
        for m in ci_msgs_ba: lines.append(f"  {m}")
        self._set_combat_hp(loser_uid, cur_hp, source_dmg=eff_dmg,
                            damage_source=winner_uid, is_counter=is_counter)
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
        # ── New damage formulas (computed before _resolve_db) ──
        if eff.get('damage_fixed'):
            dmg_dice = str(eff['damage_fixed'])
        elif eff.get('damage_moon_mult'):
            # 月光王座：伤害 = 月能×倍率（月能已在 _execute_spell 一次性消耗并存于 spell['_moon_dmg']）
            dmg_dice = str(spell.get('_moon_dmg', 1) if spell else 1)
        elif eff.get('damage_mp_multiplier'):
            mp_spent = spell.get('_actual_mp_cost', 0) if spell else 0
            dmg_val = mp_spent * eff['damage_mp_multiplier']
            dmg_dice = str(dmg_val)
        elif eff.get('damage_pct_caster_max_hp'):
            if char:
                max_hp = char.get_attr('体力上限', char.get_attr('体力', 10)) or 10
                dmg_val = max(1, max_hp * eff['damage_pct_caster_max_hp'] // 100)
                dmg_dice = str(dmg_val)
        elif eff.get('damage_pct_target_max_hp'):
            tchar = self.get_char(target_id)
            if tchar:
                t_max_hp = tchar.get_attr('体力上限', tchar.get_attr('体力', 10)) or 10
                dmg_val = max(1, t_max_hp * eff['damage_pct_target_max_hp'] // 100)
                dmg_dice = str(dmg_val)
        elif eff.get('damage_pct_caster_cur_hp'):
            if char:
                cur_hp_raw = self._get_combat_hp(caster_id) or 1
                dmg_val = max(1, cur_hp_raw * eff['damage_pct_caster_cur_hp'] // 100)
                dmg_dice = str(dmg_val)
        elif eff.get('damage_table') == 'moon_phase':
            # 血月斩击·月相表：掷 1d8 选月相，吸取月相结算后等量治疗施法者
            # 吸取量暂存 eff['_moon_drain']，随 PvP ReactionNeeded 的 effect 往返存活
            phase_name, phase_dice, phase_drain = _MOON_PHASE_TABLE[random.randint(0, len(_MOON_PHASE_TABLE) - 1)]
            dmg_dice = phase_dice
            eff['_moon_drain'] = roll_dice(phase_dice) if phase_drain else 0
            out += f'  月相判定: {phase_name}（伤害骰 {phase_dice}）\n'
        dmg_dice = _resolve_db(dmg_dice, _get_db_str(char))
        pen = eff.get('可贯穿性', 0)
        leth = eff.get('致死值', 0)
        sr = eff.get('成功率', 0)
        dur = eff.get('持续回合', 0)
        dot_dice = eff.get('每回合伤害骰', '')
        ls = float(eff.get('吸血比例', '0') or '0')

        if eff.get('幻造'):
            dmg_dice, phantom_output = self._phantom_spell_adjustment(caster_id, eff, spell, dmg_dice)
            out += phantom_output

        friend_behavior = eff.get('友方行为', '')
        is_friendly = False
        if friend_behavior:
            il = self._get_initiative()
            ce = self._unit_il_entry(caster_id, il)
            te = self._unit_il_entry(target_id, il)
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
            check_roll, bp_detail = self._roll_spell_success_bp(caster_id, eff, spell)
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
                                            atk_rank=atk_rank, atk_roll=check_roll, spell=spell,
                                            moon_drain=eff.get('_moon_drain', 0))
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
        dodge_val = self._apply_buff_skill_mod(target_id, dodge_val, '闪避')
        bmv = self._apply_buff_skill_mod(target_id, bmv, bmn)

        # ── 格挡可用性 ──
        block_name, block_val = self._get_block_skill(target_id)
        block_val = self._apply_buff_skill_mod(target_id, block_val, block_name)
        can_block = can_block and (dchar.get_attr('可格挡', 0) == 1
                     and block_name is not None and block_val > 0)
        block_hp = (self._unit_il_entry(target_id, self._get_initiative()) or {}).get('shield_block_hp', 0)
        # Check for block_redirect (summon takes damage instead of defender)
        _block_redirect = self._get_block_redirect_target(target_id)
        can_block = can_block and (block_hp > 0 or _block_redirect is not None)

        # ── 玄武/三合一等: 护盾格挡无视"不可反应"标记 ──
        def_entry = self._unit_il_entry(target_id, self._get_initiative())
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
        defender = self._unit_il_entry(target_id, self._get_initiative()) or {}
        ai_dw = self._ai_react_dodge_w.get(target_id, defender.get('react_dodge_w', 50))
        ai_cw = self._ai_react_counter_w.get(target_id, defender.get('react_counter_w', 50))
        ai_bw = self._ai_react_block_w.get(target_id, defender.get('react_block_w', 0)) if can_block else 0
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

    def _spell_reaction_extra(self, eff):
        """Dice tokens an attack effect imposes on the defender's reaction.

        AUX 33 (反应惩罚骰) applies to this attack only (dodge/counter/block).
        Legacy data used 技能惩罚骰 with a dice value ('p') for the same
        purpose; keep reading that form so old cards behave identically.
        """
        aux_type = _eff_get(eff, 'auxType')
        aux_val = _eff_get(eff, 'auxVal')
        if not aux_type:
            return []
        if AUX_NAME_TO_CODE.get(aux_type) == 33:
            return [aux_val]
        if aux_type == '技能惩罚骰' and _parse_bp_token(aux_val):
            return [aux_val]
        return []

    def _calc_spell_reaction_bp(self, target_id, eff, skill_name):
        """Net reaction dice for a spell attack's defender (per-reaction skill)."""
        def_buffs = self._get_active_buffs(target_id)
        return _calc_net_bp(def_buffs, '', skill_name,
                            extra_tokens=self._spell_reaction_extra(eff) + [getattr(self, '_manual_reaction_bp', '')])

    def _resolve_spell_dodge(self, caster_id, target_id, eff, spell, dmg_dice,
                              dodge_val, atk_rank=4):
        """Roll defender dodge vs spell attack rank. Returns (dodged, countered, lines)."""
        achar = self.get_char(caster_id)
        dchar = self.get_char(target_id)
        lines = []

        def_bp = self._calc_spell_reaction_bp(target_id, eff, '闪避')
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
            if react_rank > 0 and atk_rank > 0:
                self._y14_event(target_id, 'dodge')
            lines.append(f"  {dchar.name} 闪避了法术！" if react_rank > 0 else "  法术未命中。")
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

        def_bp = self._calc_spell_reaction_bp(target_id, eff, counter_skill)
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
            self._set_combat_hp(target_uid, cur_hp, source_dmg=eff_dmg,
                                damage_source=target_id if target_uid == caster_id else caster_id,
                                is_counter=target_uid == caster_id)
            lines.append(f"  {label}伤害: {dmg_detail} → {eff_dmg}点")
            lines.append(f"  {self.get_char(target_uid).name} HP: {cur_hp}")

        if eff_react > eff_atk and eff_react > 0:
            # Defender wins cleanly — counter damage to caster, spell negated
            if self._is_untargetable(caster_id):
                lines.append(f"  {dchar.name} 反击成功！但 {achar.name} 不可选中，无法命中。法术被击破。")
                return (False, True, lines)
            # 反击骰按防御方的反击技能名解析（原实现直接用 体格 解析 db，会忽略
            # str_attrs['db'] 与 伤害值a/b，只声明 a/b 的角色会退化成 1d4）
            cdmg = self._get_damage_dice(target_id, counter_skill)
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
                cdmg = self._get_damage_dice(target_id, counter_skill)
                cpen = dchar.get_attr("伤害贯穿", 1)
                _apply_counter_dmg(react_rank, rr, cdmg, cpen, caster_id, "反击")
            else:
                lines.append(f"  {achar.name} 不可选中，{dchar.name} 的反击无法命中。")
            if not self._is_untargetable(caster_id):
                lines.append(f"  互命中！{dchar.name} 的反击与 {achar.name} 的法术同时命中。")
            else:
                lines.append(f"  仅 {achar.name} 的法术命中。")
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

        def_bp = self._calc_spell_reaction_bp(target_id, eff, block_skill)
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
                rt_entry = self._unit_il_entry(_block_redirect, self._get_initiative())
                rt_name = rt_entry.get('displayName', '召唤物') if rt_entry else '召唤物'
                rt_block_hp = rt_entry.get('shield_block_hp', 0) if rt_entry else 0
                if blk_eff > 0 and rt_block_hp > 0:
                    absorbed = min(blk_eff, rt_block_hp)
                    blk_eff -= absorbed
                    self._set_summon_entry_fields(_block_redirect, shield_block_hp=rt_block_hp - absorbed)
                    lines.append(f"  {rt_name} 盾牌吸收: {absorbed}点 (剩余护盾HP: {rt_block_hp - absorbed})")
                if blk_eff > 0:
                    cur_hp = self._get_combat_hp(_block_redirect) or 10
                    cur_hp = max(0, cur_hp - blk_eff)
                    self._set_combat_hp(_block_redirect, cur_hp, source_dmg=blk_eff, damage_source=caster_id)
                    lines.append(f"  {rt_name} 承担穿透伤害: {blk_eff}点, HP: {cur_hp}")
                else:
                    lines.append(f"  {rt_name} 完全承担了伤害")
                return (True, False, lines)
            # ── 标准逻辑：shield_block HP 吸收 ──
            if blk_eff > 0 and block_hp > 0:
                absorbed = min(blk_eff, block_hp)
                blk_eff -= absorbed
                entry = self._unit_il_entry(target_id, self._get_initiative())
                self._set_summon_entry_fields(target_id, shield_block_hp=block_hp - absorbed)
                lines.append(f"  盾牌吸收: {absorbed}点 (剩余护盾HP: {block_hp - absorbed})")
            # 溢出伤害扣HP（不触发致死）
            if blk_eff > 0:
                cur_hp = self._get_combat_hp(target_id) or 10
                cur_hp = max(0, cur_hp - blk_eff)
                self._set_combat_hp(target_id, cur_hp, source_dmg=blk_eff, damage_source=caster_id)
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
            return self._move_explicit(uid, parts[1])
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
            target = self._smart_target(uid, spell)
            return self._execute_spell(uid, target, spell)
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
        me = self._unit_il_entry(uid, il)
        if not me: return "No position"
        enemies = self._enemy_target_entries(uid)
        if not enemies:
            all_teams = [(e.get('name', e.get('userId', '?')), e.get('team', '?')) for e in il]
            _engine_debug_log(f"_basic_attack: {char.name}({uid}) team={me.get('team','?')} "
                  f"NO ENEMIES! all teams: {all_teams}")
            return "No enemies"
        t = enemies[0]; tid = t["userId"]
        # Weapon-aware skill selection (for characters with weapon switching)
        bn, bv = self._get_attack_skill(uid, char)
        dd = self._get_damage_dice(uid, bn); p = char.get_attr("伤害贯穿",1); l = char.get_attr("致死骰",1) or 0
        _, _, lines = self._coc7_attack(uid, tid, bn, bv, dd, p, l)
        return "\n".join(lines)

    def _create_summon(self, caster_id, template_name, summon_group=None, shared_roll=None, shared_rank=None):
        tmpls = getattr(self, '_summon_templates_snapshot', _SUMMON_TEMPLATES)
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
            # 只从还有名额的实际模板中抽取（None=无限，0=已满）
            options = [n for n in options
                       if self._get_summon_quota(caster_id, n, tmpls.get(n) or {})[0] != 0]
            if not options:
                return None
            template_name = random.choice(options)
            tmpl = tmpls.get(template_name)  # tmpl is now the resolved template (e.g. '朱雀') — no unique_per_caster!
            if not tmpl: return None
            # Track as summoned (unique_per_caster: once per battle — survives death)
            if meta_unique:  # Use the saved flag from meta template
                self._summoned_once.setdefault(caster_id, set()).add(template_name)
        # ── 数量上限最终校验（在 _meta 解析出实际模板之后）──
        # 直接调用 / 被动 / 延迟召唤也必须受限；返回 None 时调用方不得记成功。
        _quota, _quota_reason = self._get_summon_quota(caster_id, template_name, tmpl)
        if _quota == 0:
            _engine_debug_log(f'_create_summon blocked: owner={caster_id} tmpl={template_name} {_quota_reason}')
            return None
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
        owner_base = self._resolve_uid(caster_id)
        self._reconcile_summon_groups()
        existing_rep = self._find_summon_group_by(owner_base, template_name)
        init_list = self._get_initiative()
        ce = self._unit_il_entry(caster_id, init_list)
        group_entry = (self._unit_il_entry(
            self._summon_groups[existing_rep]['representative_uid'], init_list)
            if existing_rep is not None else None)
        coord = (group_entry or {}).get('coord', 'A1')
        if existing_rep is None and ce and ce.get('coord'):
            point = parse_coord(ce['coord'])
            md = self._get_map()
            if point and md:
                size = 2 if tmpl.get('size_2x2') else 1
                candidates = ((point[0] + dc, point[1] + dr)
                              for radius in range(1, 6)
                              for dc in range(-radius, radius + 1)
                              for dr in range(-radius, radius + 1)
                              if abs(dc) + abs(dr) == radius)
                spot = next(((col, row) for col, row in candidates
                             if 0 <= col and col + size <= md.get('width', 26)
                             and 0 <= row and row + size <= md.get('height', 99)
                             and all(format_coord(col + dc, row + dr) not in md.get('occupants', {})
                                     and format_coord(col + dc, row + dr) not in md.get('obstacles', {})
                                     for dc in range(size) for dr in range(size))), None)
                if spot is None:
                    return None
                coord = format_coord(*spot)
                md['occupants'][coord] = sid
                self._set_map(md)
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
        # ── Add to the live/repaired group found before reserving any map cell ──
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
            if tmpl.get('不可指定'):
                summon_char2.set_attr('不可指定', 1)
            if parsed and parsed[0].get('dice'):
                summon_char2.set_str('伤害值', parsed[0]['dice'])
            summon_char2.set_attr('额外行动数', tmpl.get('额外行动数', tmpl.get('回合行动数', 0)))
            if tmpl.get('无主动行动'):
                summon_char2.set_attr('无主动行动', 1)
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
                for _k in ['剑', '闪避', '力量', '敏捷', '体质', '体型', '意志', '幸运',
                       '魔力', '魔力上限', 'MOV', '行动力']:
                    _v = caster.get_attr(_k) if caster else None
                    if _v is not None:
                        summon_char2.set_attr(_k, _v)
                if caster:
                    summon_char2.set_str('伤害值', caster.get_str('伤害值') or '1d4')
                    summon_char2.set_str('db', caster.get_str('db') or '0')
            # Hidden members share the group's footprint; no extra cell is reserved.
            self._register_summon_member_state(sid, tmpl, owner_base)
            mg2 = tmpl.get('merge_group', '')
            if mg2:
                if not hasattr(self, '_summoned_templates'):
                    self._summoned_templates = {}
                st2 = self._summoned_templates
                if caster_id not in st2: st2[caster_id] = {}
                if mg2 not in st2[caster_id]: st2[caster_id][mg2] = set()
                st2[caster_id][mg2].add(template_name)
            self._register_summon_spawned(caster_id, template_name)
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
        if tmpl.get('无主动行动'):
            summon_char.set_attr('无主动行动', 1)
        if tmpl.get('不可指定'):
            summon_char.set_attr('不可指定', 1)
        if tmpl.get('unlimited_mp'):
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
            for _k in ['剑', '闪避', '力量', '敏捷', '体质', '体型', '意志', '幸运',
                       '魔力', '魔力上限', 'MOV', '行动力']:
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
            'ownerId': owner_base, 'representative_uid': sid,
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
        self._register_summon_member_state(sid, tmpl, owner_base)
        self._register_summon_spawned(caster_id, template_name)
        return sid

    def _get_summon_entry(self, summon_id):
        """Read a physical member, sharing only position with its initiative rep."""
        summon_id = self._resolve_uid(summon_id)
        il = self._get_initiative()
        entry = next((e for e in il if self._resolve_uid(e['userId']) == summon_id), None)
        if entry is not None:
            return entry
        data = self._summon_members.get(summon_id)
        if data is None:
            return None
        key = self._summon_group_key(summon_id)
        group = self._summon_groups.get(key, {})
        rep = group.get('representative_uid', key)
        rep_entry = next((e for e in il if e['userId'] == rep), None)
        saved = group.get('initiative_entry', {})
        tmpl = self._get_summon_template(data.get('name', '')) or {}
        skills = data.get('skills', [])
        first = (skills or [{}])[0]
        view = dict(data)
        view.update({
            'userId': summon_id, 'baseUserId': summon_id, 'isSummon': True,
            'displayName': data.get('display_name', data.get('name', summon_id)),
            'team': data.get('team', (rep_entry or saved).get('team', 'Y')),
            'ownerId': data.get('ownerId', group.get('ownerId', '')),
            'coord': (rep_entry or saved).get('coord') or data.get('coord', ''),
            'skill_name': first.get('name', '斗殴'), 'skill_val': first.get('val', 25),
            'dmg_dice': first.get('dice', '1d4'),
            'zone_skills': [s for s in skills if s.get('skill_type') == 'zone_heal'],
            'zone_cooldown': data.get('zone_cooldown', 0),
            'summon_mp': data.get('summon_mp', tmpl.get('MP', 0)),
            'react_dodge_w': data.get('react_dodge_w', tmpl.get('react_dodge', 50)),
            'react_counter_w': data.get('react_counter_w', tmpl.get('react_counter', 50)),
            'react_block_w': data.get('react_block_w', tmpl.get('react_block', 0)),
            'shield_block': data.get('shield_block', tmpl.get('shield_block', 0)),
            'shield_block_hp': data.get('shield_block_hp', tmpl.get('shield_block', 0)),
            'shield_block_rate': data.get('shield_block_rate', tmpl.get('shield_block_rate', 0.70)),
            'ignore_unreactable_block': data.get('ignore_unreactable_block', tmpl.get('ignore_unreactable_block', 0)),
            '_synthetic': True,
        })
        return view

    def _summon_attack(self, summon_id):
        entry = self._get_summon_entry(summon_id)
        if not entry: return ["未找到召唤物"]
        sname = entry.get("displayName", entry.get("name", summon_id))
        lines = []

        # Zone skill check — summon can cast a zone instead of attacking
        zone_cooldown = entry.get("zone_cooldown", 0)
        if zone_cooldown > 0:
            entry["zone_cooldown"] = zone_cooldown - 1
            self._set_summon_entry_fields(summon_id, zone_cooldown=entry['zone_cooldown'])
        if entry.get("zone_cooldown", 0) <= 0:
            zone_skills = entry.get("zone_skills", [])
            if zone_skills:
                zsk = zone_skills[0]
                mp_cost = zsk.get("mp_cost", 0)
                summon_mp = entry.get("summon_mp", 0)
                if summon_mp >= mp_cost:
                    entry["summon_mp"] = summon_mp - mp_cost
                    entry["zone_cooldown"] = zsk.get("cooldown_rounds", 0) + zsk.get("zone_duration", 3)
                    self._set_summon_entry_fields(summon_id, summon_mp=entry['summon_mp'],
                                                  zone_cooldown=entry['zone_cooldown'])
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

        il = self._get_initiative()
        enemies = self._enemy_target_entries(summon_id)
        if not enemies: return ["无可用目标"]
        tid = enemies[0]["userId"]
        skills = entry.get("skills",[])
        sk_name = ""
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
        # Battle spirit penalty dice live on the stable group; fall back to the
        # representative entry for legacy states and ungrouped summons.
        _bs_group = self._summon_groups.get(self._summon_group_key(summon_id)) or {}
        pens = _bs_group.get('battle_spirit_penalty_dice',
                             entry.get('battle_spirit_penalty_dice', 0))
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
                # Extra hits repeat the same attack check, so the battle-spirit
                # penalty suffix applies here too (bonus/penalty dice fold once).
                extra_roll, _ = self._roll_check_d100(
                    summon_id, sk_label,
                    extra_tokens=[bp_suffix] if bp_suffix else None)
                if extra_roll > sv: continue
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
        il = self._get_initiative(); entry = self._unit_il_entry(target_id, il)
        if not entry: return dmg
        block_hp = entry.get("shield_block_hp",0)
        if block_hp <= 0: return dmg
        rate = entry.get("shield_block_rate", 0.70)
        if random.random() < rate:
            absorbed = min(dmg, block_hp)
            self._set_summon_entry_fields(target_id, shield_block_hp=block_hp - absorbed)
            return dmg - absorbed
        return dmg

    def _is_flying(self, uid):
        il = self._get_initiative(); entry = self._unit_il_entry(uid, il)
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
            cp = parse_coord(self._zone_caster_coord(eff, il))
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
                for entry in self._battle_unit_entries():
                    ec = entry.get("coord",""); ep = parse_coord(ec) if ec else None
                    if not ep: continue
                    if max(abs(ep[0]-cp[0]),abs(ep[1]-cp[1])) > eff["radius"]: continue
                    # 隔离：不受领域效果影响
                    if self._has_status(entry['userId'], '隔离'):
                        continue
                    # 客体/范围筛选（不满足者不占用去重键）
                    if not self._zone_covers_uid(eff, entry['userId'], il):
                        continue
                    # ally_safe: skip allies if zone is marked ally-safe
                    if eff.get('ally_safe') and entry.get('team') == zone_team:
                        continue
                    # Dedup: skip if this (coord, spellName) already processed by another non-stackable zone
                    pos_key = (self._resolve_uid(entry['userId']), spell_name, eff.get('effectKey', 'a'))
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
            if eff.get('roundPulsesRemaining', 1) > 0:
                pulse = f"r{(self._get_state() or {}).get('round', 1)}"
                self._settle_zone_heals(eff, il, tick_results, pulse_id=pulse)
                eff['roundPulsesRemaining'] = max(0, int(eff.get('roundPulsesRemaining', 1)) - 1)
            # Attribute debuff per round (属性削减, e.g. 闪避-30/行动力-5)
            # 由 _apply_zone_attr_penalties 在循环后统一按属性名与敌我/范围结算。
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
        # 领域属性削减统一结算（含敌我过滤、范围、非叠加取最强）
        self._apply_zone_attr_penalties(il)
        self._set_initiative(il)
        self._zone_tick_results = tick_results
        self._queue_zone_events(tick_results)

    def _process_zone_specials(self):
        return self._settle_zone_enters()

    def _check_trinity_merge(self):
        """Generic summon merge: summons with same merge_group merge when only one survives."""
        tmpls = getattr(self, '_summon_templates_snapshot', _SUMMON_TEMPLATES)
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

    def setup_battle(self, team_a, team_b, map_size=None, season_status=None):
        if map_size is None:
            from combat_settings import value as battle_setting
            map_size = battle_setting("DEFAULT_MAP_SIZE")
        self._pending_battle_events = []
        self._zone_tick_results = []
        self._inspiration_pending = set()
        self._inspiration_settled = set()
        self._ai_failed_commands = set()
        w, h = map(int, map_size.split("x"))
        self.group_id = f"fast_{random.randint(10000,99999)}"
        # ── 新战斗重置召唤账本 / 分组 / unique 记录 ──
        self._summon_groups = {}
        self._summon_members = {}
        self._summon_total_spawned = {}
        self._revive_use_ledger = set()
        self._summoned_once = {}
        self._summoned_templates = {}
        self._graveyard = {}
        for uid in team_a + team_b:
            self.get_char(uid)._once_consumed = set()
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
        for uid in all_chars:
            if self._is_y14(uid):
                self._y14_initialize_phase(uid)
                acts = self._get_actions()
                acts[uid] = {"主动": self._calc_main_actions(uid), "附加": 3}
                self._set_actions(acts)

        self._set_state({"phase":"active", "round":1, "activeIndex":0})
        self._skip_to_valid_active()

    # ---- Summon system (same as FullBattleEngine) ----
    def _create_summon(self, caster_id, template_name, summon_group=None, shared_roll=None, shared_rank=None):
        tmpls = getattr(self, '_summon_templates_snapshot', _SUMMON_TEMPLATES)
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
            # 只从还有名额的实际模板中抽取（None=无限，0=已满）
            options = [n for n in options
                       if self._get_summon_quota(caster_id, n, tmpls.get(n) or {})[0] != 0]
            if not options:
                return None
            template_name = random.choice(options)
            tmpl = tmpls.get(template_name)  # tmpl is now the resolved template (e.g. '朱雀') — no unique_per_caster!
            if not tmpl: return None
            # Track as summoned (unique_per_caster: once per battle — survives death)
            if meta_unique:  # Use the saved flag from meta template
                self._summoned_once.setdefault(caster_id, set()).add(template_name)
        # ── 数量上限最终校验（在 _meta 解析出实际模板之后）──
        # 直接调用 / 被动 / 延迟召唤也必须受限；返回 None 时调用方不得记成功。
        _quota, _quota_reason = self._get_summon_quota(caster_id, template_name, tmpl)
        if _quota == 0:
            _engine_debug_log(f'_create_summon blocked: owner={caster_id} tmpl={template_name} {_quota_reason}')
            return None
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
        owner_base = self._resolve_uid(caster_id)
        self._reconcile_summon_groups()
        existing_rep = self._find_summon_group_by(owner_base, template_name)
        init_list = self._get_initiative()
        ce = self._unit_il_entry(caster_id, init_list)
        group_entry = (self._unit_il_entry(
            self._summon_groups[existing_rep]['representative_uid'], init_list)
            if existing_rep is not None else None)
        coord = (group_entry or {}).get('coord', 'A1')
        if existing_rep is None and ce and ce.get('coord'):
            point = parse_coord(ce['coord'])
            md = self._get_map()
            if point and md:
                size = 2 if tmpl.get('size_2x2') else 1
                candidates = ((point[0] + dc, point[1] + dr)
                              for radius in range(1, 6)
                              for dc in range(-radius, radius + 1)
                              for dr in range(-radius, radius + 1)
                              if abs(dc) + abs(dr) == radius)
                spot = next(((col, row) for col, row in candidates
                             if 0 <= col and col + size <= md.get('width', 26)
                             and 0 <= row and row + size <= md.get('height', 99)
                             and all(format_coord(col + dc, row + dr) not in md.get('occupants', {})
                                     and format_coord(col + dc, row + dr) not in md.get('obstacles', {})
                                     for dc in range(size) for dr in range(size))), None)
                if spot is None:
                    return None
                coord = format_coord(*spot)
                md['occupants'][coord] = sid
                self._set_map(md)
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
        # ── Add to the live/repaired group found before reserving any map cell ──
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
            if tmpl.get('不可指定'):
                summon_char2.set_attr('不可指定', 1)
            if parsed and parsed[0].get('dice'):
                summon_char2.set_str('伤害值', parsed[0]['dice'])
            summon_char2.set_attr('额外行动数', tmpl.get('额外行动数', tmpl.get('回合行动数', 0)))
            if tmpl.get('无主动行动'):
                summon_char2.set_attr('无主动行动', 1)
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
                for _k in ['剑', '闪避', '力量', '敏捷', '体质', '体型', '意志', '幸运',
                       '魔力', '魔力上限', 'MOV', '行动力']:
                    _v = caster.get_attr(_k) if caster else None
                    if _v is not None:
                        summon_char2.set_attr(_k, _v)
                if caster:
                    summon_char2.set_str('伤害值', caster.get_str('伤害值') or '1d4')
                    summon_char2.set_str('db', caster.get_str('db') or '0')
            # Hidden members share the group's footprint; no extra cell is reserved.
            self._register_summon_member_state(sid, tmpl, owner_base)
            mg2 = tmpl.get('merge_group', '')
            if mg2:
                if not hasattr(self, '_summoned_templates'):
                    self._summoned_templates = {}
                st2 = self._summoned_templates
                if caster_id not in st2: st2[caster_id] = {}
                if mg2 not in st2[caster_id]: st2[caster_id][mg2] = set()
                st2[caster_id][mg2].add(template_name)
            self._register_summon_spawned(caster_id, template_name)
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
        if tmpl.get('不可指定'):
            summon_char_fast.set_attr('不可指定', 1)
        if parsed and parsed[0].get('dice'):
            summon_char_fast.set_str('伤害值', parsed[0]['dice'])
        summon_char_fast.set_attr('额外行动数', tmpl.get('额外行动数', tmpl.get('回合行动数', 0)))
        # Copy template-based skill attrs to character (needed for get_best_melee/reactions)
        # 之前 Fast 路径漏了这一步：蜂鸟 闪避65 / 爪击70 只进了 entry，没进 Character，
        # 导致训练时召唤物的命中/闪避仍按默认值计算。
        _tmpl_skill_keys_f = ['斗殴', '闪避', '格挡', '盾击', '鞭', '爪击']
        for _k in _tmpl_skill_keys_f:
            if _k in tmpl:
                summon_char_fast.set_attr(_k, tmpl[_k])
        for _ps in parsed:
            _sn = _ps.get('name', '')
            if _sn and _ps.get('skill_type', 'attack') == 'attack':
                summon_char_fast.set_attr(_sn, _ps['val'])
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
        # ── Create summon group for first summon of this type ──
        # Full 引擎在此登记；Fast 之前漏了这一步，导致训练路径下生灵/蜂鸟
        # 每只各占一个先攻槽、各有一份动作池（与线上的合组语义分叉）。
        self._summon_groups[sid] = {
            'members': [sid], 'template_name': template_name,
            'ownerId': self._resolve_uid(caster_id),
            'representative_uid': sid,
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
        self._register_summon_member_state(sid, tmpl, owner_base)
        self._register_summon_spawned(caster_id, template_name)
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
            cp = parse_coord(self._zone_caster_coord(eff, il))
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
                for entry in self._battle_unit_entries():
                    ec = entry.get("coord",""); ep = parse_coord(ec) if ec else None
                    if not ep: continue
                    if max(abs(ep[0]-cp[0]),abs(ep[1]-cp[1])) > eff["radius"]: continue
                    # 隔离：不受领域效果影响
                    if self._has_status(entry['userId'], '隔离'):
                        continue
                    # 客体/范围筛选（不满足者不占用去重键）
                    if not self._zone_covers_uid(eff, entry['userId'], il):
                        continue
                    # ally_safe: skip allies if zone is marked ally-safe
                    if eff.get('ally_safe') and entry.get('team') == zone_team:
                        continue
                    # Dedup: skip if this (coord, spellName) already processed by another non-stackable zone
                    pos_key = (self._resolve_uid(entry['userId']), spell_name, eff.get('effectKey', 'a'))
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
            if eff.get('roundPulsesRemaining', 1) > 0:
                pulse = f"r{(self._get_state() or {}).get('round', 1)}"
                self._settle_zone_heals(eff, il, tick_results, pulse_id=pulse)
                eff['roundPulsesRemaining'] = max(0, int(eff.get('roundPulsesRemaining', 1)) - 1)
            # Attribute debuff per round (属性削减, e.g. 闪避-30/行动力-5)
            # 由 _apply_zone_attr_penalties 在循环后统一按属性名与敌我/范围结算。
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
        # 领域属性削减统一结算（含敌我过滤、范围、非叠加取最强）
        self._apply_zone_attr_penalties(il)
        self._set_initiative(il)

    # ---- Zone enter triggers (generalized) + 三合一 (generalized) ----
    def _process_zone_specials(self):
        return self._settle_zone_enters()

    def _check_trinity_merge(self):
        """Generic summon merge: summons with same merge_group merge when only one survives."""
        tmpls = getattr(self, '_summon_templates_snapshot', _SUMMON_TEMPLATES)
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
        il = self._get_initiative(); entry = self._unit_il_entry(uid, il)
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
        # bp_suffix: 攻击检定的奖励/惩罚骰（如飞行近战惩罚骰），与 Full 引擎一致。
        # 注意与 pen（伤害贯穿）区分——两者含义完全不同。
        bp_suffix = ''
        # ── 射程移动 ──
        profile = self._y14_attack_profile(atk_uid)
        atk_range = profile[1] if profile else self._get_attack_range(skill_name=skill_name)
        can_atk, _orig_coord = self._move_to_attack_range(atk_uid, def_uid, atk_range)
        if not can_atk:
            return (def_uid, atk_uid, 0, f"射程不足 (range={atk_range})")
        # ── 飞行近战限制 ──
        can_melee, fly_penalty = ((True, False) if self._is_y14(atk_uid) and
                                 getattr(achar, 'phase', 2) == 1 else self._can_melee(atk_uid, def_uid))
        if not can_melee:
            return (def_uid, atk_uid, 0, f"无法近战飞行目标")
        if fly_penalty:
            bp_suffix = 'p'  # 飞行近战惩罚骰
        # ── 幻造兵武 (phantom creation): basic attacks also benefit from 幻造 buff ──
        atk_effects2 = self._get_effects()
        for ce in atk_effects2:
            if ce.get('type') == 'buff' and ce.get('spellName') == '幻造兵武' \
               and ce.get('targetUserId') == atk_uid:
                writing_val2 = int(achar.get_attr('写作', 0) or 0)
                if writing_val2 > 0:
                    self._note_inspiration_event(atk_uid)
                    atk_buffs_hz2 = self._get_active_buffs(atk_uid)
                    bp_str_hz2 = _calc_net_bp(atk_buffs_hz2, '', '写作')
                    roll2, _ = roll_d100(bp_str_hz2)
                    w_rank2 = success_rank(roll2, self._apply_buff_skill_mod(atk_uid, writing_val2, '写作'))
                    if w_rank2 >= 4:      phantom_val2 = roll_dice('2d6')
                    elif w_rank2 == 3:    phantom_val2 = 6 + roll_dice('1d6')
                    elif w_rank2 == 2:    phantom_val2 = roll_dice('1d6')
                    elif w_rank2 == 1:    phantom_val2 = roll_dice('1d3')
                    elif w_rank2 == -2:   phantom_val2 = -roll_dice('1d2')
                    else:                 phantom_val2 = 0
                    if phantom_val2 != 0:
                        sign2 = '+' if phantom_val2 > 0 else ''
                        dmg_dice = f"{dmg_dice}{sign2}{phantom_val2}"
                break
        # AUX code 4: merge bonus damage dice
        bonus_dice = self._get_buff_dmg_dice_bonus(atk_uid)
        if bonus_dice:
            dmg_dice = f"{dmg_dice}+{bonus_dice}" if dmg_dice else bonus_dice
        eff_skill = self._apply_buff_skill_mod(atk_uid, skill_val, skill_name)
        atk_buffs = self._get_active_buffs(atk_uid)
        # 行动力优势合并到攻击BP（同样合并飞行近战惩罚骰 bp_suffix）
        ap_atk_b, ap_def_p = self._calc_action_power_bp(atk_uid, def_uid)
        eff_bp = _calc_net_bp(atk_buffs, bp_suffix, skill_name,
                              extra_tokens=[ap_atk_b] if ap_atk_b else None)
        crit_pct, crit_flat = self._get_buff_crit_adjustment(atk_uid)
        atk_result, atk_bp_detail = roll_d100(eff_bp); atk_rank = success_rank(atk_result, eff_skill, crit_pct, crit_flat)
        bp_str = f", {atk_bp_detail}" if atk_bp_detail else ""
        # 即使攻击失败也允许防御方反应——大失败闪避/格挡可能使攻方意外命中，
        # 成功反击也可能在攻方失败时命中造成伤害。
        if atk_rank <= 0 and not getattr(self, '_allow_failed_reaction', True):
            return (def_uid, atk_uid, 0, f"D100={atk_result}/{eff_skill}{bp_str} {rank_text(atk_rank)}")

        dodge_val = dchar.get_attr("闪避",25); bmn, bmv = dchar.get_best_melee()
        dodge_val = self._apply_buff_skill_mod(def_uid, dodge_val, '闪避')
        bmv = self._apply_buff_skill_mod(def_uid, bmv, bmn)
        def_buffs = self._get_active_buffs(def_uid)
        _, ap_def_p2 = self._calc_action_power_bp(atk_uid, def_uid)
        _def_extra = [ap_def_p2] if ap_def_p2 else None

        # ── 格挡可用性 ──
        block_name, block_val = self._get_block_skill(def_uid)
        block_val = self._apply_buff_skill_mod(def_uid, block_val, block_name)
        can_block = (dchar.get_attr('可格挡', 0) == 1
                     and block_name is not None and block_val > 0)
        block_hp = (self._unit_il_entry(def_uid, self._get_initiative()) or {}).get('shield_block_hp', 0)
        # Check for block_redirect (summon takes damage instead of defender)
        _block_redirect = self._get_block_redirect_target(def_uid)
        can_block = can_block and (block_hp > 0 or _block_redirect is not None)
        def_bp_dodge = _calc_net_bp(def_buffs, '', '闪避', extra_tokens=_def_extra)
        def_bp_counter = _calc_net_bp(def_buffs, '', bmn, extra_tokens=_def_extra)
        def_bp_block = _calc_net_bp(def_buffs, '', block_name, extra_tokens=_def_extra)

        # ── 击晕禁止所有反应 ──
        if not self._can_react(def_uid):
            return (def_uid, atk_uid, 0, f"击晕中，无法反应")
        # Reaction: use stored character weights (trainable, three-way)
        defender = self._unit_il_entry(def_uid, self._get_initiative()) or {}
        dw = self._react_dw.get(def_uid, defender.get('react_dodge_w', 50))
        cw = self._react_cw.get(def_uid, defender.get('react_counter_w', 50))
        bw = self._react_bw.get(def_uid, defender.get('react_block_w', 0)) if can_block else 0
        total_w = dw + cw + bw
        if total_w > 0:
            r = random.random() * total_w
            if r < dw: react_choice = 'dodge'
            elif r < dw + cw: react_choice = 'counter'
            else: react_choice = 'block'
        else:
            react_choice = 'dodge' if dodge_val >= bmv else 'counter'

        if react_choice == 'dodge':
            rr, rd_detail = roll_d100(def_bp_dodge); react_rank = success_rank(rr, dodge_val)
            eff_atk = atk_rank
            if react_rank > 0: eff_atk -= react_rank
            if react_rank == -2: eff_atk += (2 if eff_atk == -1 else 1)
            eff_atk = max(-2, min(4, eff_atk))
            if eff_atk <= 0:
                if react_rank > 0 and atk_rank > 0:
                    self._y14_event(def_uid, 'dodge')
                return (def_uid, atk_uid, 0, f"D100={rr}/{dodge_val} dodge rank={react_rank}")
            winner_rank, winner_uid, loser_uid = eff_atk, atk_uid, def_uid
            is_counter = False
        elif react_choice == 'counter':
            rr, rd_detail = roll_d100(def_bp_counter); react_rank = success_rank(rr, bmv)
            eff_atk, eff_react = atk_rank, react_rank
            if react_rank == -2: eff_atk += (2 if eff_atk == -1 else 1)
            if eff_atk == -2: eff_react = min(4, eff_react + 1)
            eff_atk, eff_react = max(-2, min(4, eff_atk)), max(-2, min(4, eff_react))
            if eff_react > eff_atk and eff_react > 0:
                winner_rank, winner_uid, loser_uid = eff_react, def_uid, atk_uid
                cdmg = self._get_damage_dice(def_uid, bmn)
                cpen = dchar.get_attr("伤害贯穿",1); cleth = dchar.get_attr("致死骰",1) or 0
                dmg_dice, pen, leth, is_counter = cdmg, cpen, cleth, True
            elif eff_atk > eff_react and eff_atk > 0:
                winner_rank, winner_uid, loser_uid, is_counter = eff_atk, atk_uid, def_uid, False
            else: return (None, None, 0, "draw")

        else:  # react_choice == 'block'
            # ── 格挡反应 (训练引擎) ──
            rr, rd_detail = roll_d100(def_bp_block); react_rank = success_rank(rr, block_val)
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
                    rt_entry = self._unit_il_entry(_block_redirect, self._get_initiative())
                    rt_block_hp = rt_entry.get('shield_block_hp', 0) if rt_entry else 0
                    if blk_eff > 0 and rt_block_hp > 0:
                        absorbed = min(blk_eff, rt_block_hp)
                        blk_eff -= absorbed
                        self._set_summon_entry_fields(_block_redirect, shield_block_hp=rt_block_hp - absorbed)
                    cur_hp = self._get_combat_hp(_block_redirect) or 10
                    cur_hp = max(0, cur_hp - blk_eff)
                    self._set_combat_hp(_block_redirect, cur_hp, source_dmg=blk_eff, damage_source=atk_uid)
                    return (def_uid, atk_uid, blk_eff, f"block redirect to {rt_entry.get('name','?')} dmg:{blk_eff}")
                # ── 标准逻辑 ──
                if blk_eff > 0 and block_hp > 0:
                    absorbed = min(blk_eff, block_hp)
                    blk_eff -= absorbed
                    entry = self._unit_il_entry(def_uid, self._get_initiative())
                    self._set_summon_entry_fields(def_uid, shield_block_hp=block_hp - absorbed)
                cur_hp = self._get_combat_hp(def_uid) or 10
                cur_hp = max(0, cur_hp - blk_eff)
                self._set_combat_hp(def_uid, cur_hp, source_dmg=blk_eff, damage_source=atk_uid)
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
        self._set_combat_hp(loser_uid, cur_hp, source_dmg=eff_dmg,
                            damage_source=winner_uid, is_counter=is_counter)

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
        profile = self._y14_attack_profile(uid)
        atk_range = profile[1] if profile else self._get_attack_range(skill_name=bn_tmp)
        reachable = []
        for e in enemies:
            # _can_melee 返回 (can_melee, fly_penalty) 元组，必须解包——
            # 直接判真值会把 (False, False) 当成可用，使飞行过滤失效。
            can_melee, _fly_penalty = ((True, False) if self._is_y14(uid) and
                                       getattr(char, 'phase', 2) == 1 else self._can_melee(uid, e["userId"]))
            if can_melee:
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
        bn, bv = char.get_best_melee(); bv = self._apply_buff_skill_mod(uid, bv, bn)
        # 奖惩骰参与普通攻击命中判定（原先只掷裸随机数）
        atk_bp = _calc_net_bp(self._get_active_buffs(uid), '', bn)
        hit_roll, _ = roll_d100(atk_bp)
        if success_rank(hit_roll, bv) <= 0:
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
        self._set_combat_hp(tid, cur_hp, source_dmg=eff_dmg, damage_source=uid)
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
                    self._apply_hp_heal_amount(uid, heal)
        return ""

    def _fast_move(self, uid):
        coord = self._next_move_coord(uid)
        if coord:
            return self._move_explicit(uid, coord)
        return ActionExecutionResult('没有可执行的移动', False, 'invalid_move', '附加')
