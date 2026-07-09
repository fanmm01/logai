#!/usr/bin/env python3
"""
PvP/PvE Battle HTTP Server (battle_http_server.py)
====================================================
Flask HTTP API wrapping battle_engine.py's FullBattleEngine for interactive play.
Receives character data + player actions from SealDice autocombat.js (via .setab 2),
processes them through the full COC7 battle engine, returns formatted results.

Does NOT modify battle_engine.py, characters_data.py, ai_battle.py, or ai_trainer.py.
Uses sys.modules trick to redirect battle_engine's lazy imports to characters_data_pvp.
"""

import sys, os, json, uuid, threading, random, math, time, importlib, re
from datetime import datetime

# Ensure we can import from the autocombat directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ═══════════════════════════════════════════════════════════════
#  配置项
# ═══════════════════════════════════════════════════════════════

# 濒死机制：HP≤0 时进行 CON 豁免判定，成功则进入濒死状态（可被治疗拉回）。
# 设为 False 则 HP≤0 直接死亡。
USE_DYING_SYSTEM = True

# AI 回合间隔秒数（JS 端 AI 回合延迟开关启用时使用）
AI_TURN_DELAY_MIN = 5
AI_TURN_DELAY_MAX = 15

# ═══════════════════════════════════════════════════════════════
#  Debug logging to file (with rotation: 10MB max, 3 backups)
# ═══════════════════════════════════════════════════════════════
import logging.handlers
_DEBUG_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'battle_debug.log')
_DEBUG_LOCK = threading.Lock()

_debug_file_logger = logging.getLogger('battle_debug')
_debug_file_logger.setLevel(logging.DEBUG)
_debug_file_logger.propagate = False  # don't leak to root logger
try:
    _handler = logging.handlers.RotatingFileHandler(
        _DEBUG_LOG_PATH, maxBytes=10*1024*1024, backupCount=3, encoding='utf-8'
    )
    _handler.setFormatter(logging.Formatter('%(message)s'))
    _debug_file_logger.addHandler(_handler)
except Exception:
    pass  # fall back to no file logging if handler setup fails

def _debug_log(msg: str):
    """Append a timestamped message to the debug log file (with rotation)."""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    line = f"[{ts}] {msg}"
    with _DEBUG_LOCK:
        try:
            _debug_file_logger.debug(line)
        except Exception:
            pass  # silently ignore log write errors
    # Also print to stdout for immediate console visibility
    try:
        print(line, file=sys.stdout, flush=True)
    except UnicodeEncodeError:
        # Windows GBK console can't handle some Unicode chars → fall back to ascii-safe
        print(line.encode('ascii', errors='replace').decode('ascii'), file=sys.stdout, flush=True)

# ── Module redirection: battle_engine's lazy "from characters_data import ..." ──
# must happen BEFORE importing from battle_engine
# WARNING: 此hack依赖 battle_engine.py 在函数内部使用延迟导入 characters_data。
# 如果 battle_engine.py 改为顶层导入，此hack将失效，届时需考虑让引擎支持数据模块注入。
import characters_data_pvp
sys.modules['characters_data'] = characters_data_pvp

from flask import Flask, request, jsonify

# Now import from battle_engine — its lazy imports get characters_data_pvp
from battle_engine import (
    FullBattleEngine, CombatEngine, MAX_DYNAMIC_ACTIONS,
    roll_dice, parse_coord, format_coord,
    is_in_melee_range, has_timing, has_object,
    rank_text, avg_damage, success_rank, roll_d100, max_damage, _calc_net_bp,
    CN_NUMS, CAT_LETTERS,
)

# ── Skill lists (mirro发现，目前似乎进red from test_harness for completeness) ──
MELEE_SKILLS = ['格斗','斗殴','斧','链锯','连枷','绞索','矛','剑','鞭']
ALL_COMBAT_SKILLS = MELEE_SKILLS + [
    '射击','射击:弓','射击:手枪','射击:重武器','射击:火焰喷射器',
    '射击:机枪','射击:步霰','射击:冲锋枪','投掷','爆破','炮术'
]

# ── Flask app ──
app = Flask(__name__)

# ── Error handlers: return JSON instead of HTML for API errors ──
@app.errorhandler(400)
def bad_request(e):
    return jsonify({'error': True, 'message': '请求格式错误'}), 400

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': True, 'message': '接口不存在'}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({'error': True, 'message': '不支持的请求方法'}), 405

@app.errorhandler(500)
def internal_error(e):
    import traceback
    app.logger.error(f'500 Internal Server Error:\n{traceback.format_exc()}')
    return jsonify({'error': True, 'message': f'服务器内部错误: {str(e)}'}), 500

# ═══════════════════════════════════════════════════════════════
#  Battle store
# ═══════════════════════════════════════════════════════════════

_battles: dict = {}           # group_id → FullBattleEngine instance
_pending_reactions: dict = {} # group_id → saved reaction state dict
_lock = threading.Lock()

# Team storage for .bteam command
_teams: dict = {}            # team_id (e.g. "T1") → {name, members: [serial,...], creator}
_next_team_id: int = 1
_teams_lock = threading.Lock()

# ═══════════════════════════════════════════════════════════════
#  Character data helpers
# ═══════════════════════════════════════════════════════════════

def _find_chdata_by_serial(serial: str) -> dict | None:
    """Find a character definition in characters_data_pvp by serial (e.g. 'Y1')."""
    for c in characters_data_pvp.ALL_CHARACTERS:
        if c.get('serial') == serial:
            return c
    return None

def _find_chdata_by_mgid(mgid: int) -> dict | None:
    """Find a character definition by 魔法少女序号."""
    for c in characters_data_pvp.ALL_CHARACTERS:
        if c.get('attrs', {}).get('魔法少女序号') == mgid:
            return c
    return None

def _load_spells_from_chdata(engine, uid: str, ch_data: dict):
    """Load spells and summon templates from chData into engine, without touching
    COC base stats.  Mirrors the spell-loading portion of load_character_to_engine
    in characters_data_pvp.py."""
    char = engine.get_char(uid)

    # Load summon templates referenced by this character's spells
    summon_names = set()
    for spell in ch_data.get('spells', []):
        for eff in spell.get('effects', []):
            tmpl = eff.get('召唤物模板', '')
            if tmpl and tmpl in characters_data_pvp.SUMMON_TEMPLATES:
                summon_names.add(tmpl)
    for ti, tmpl_name in enumerate(sorted(summon_names), 1):
        tmpl = characters_data_pvp.SUMMON_TEMPLATES[tmpl_name]
        prefix = f"召唤物模板{CN_NUMS[ti]}"
        char.set_str(f"{prefix}名称", tmpl_name)
        for k in ['HP','MP','SAN','STR','CON','SIZ','DEX','APP','INT','POW','EDU',
                  '闪避','MOV','行动次数','可反击','可反应']:
            if k in tmpl:
                char.set_attr(f"{prefix}{k}", tmpl[k])
        for si, sk in enumerate(tmpl.get('skills', []), 1):
            if isinstance(sk, dict):
                char.set_str(f"{prefix}技能{si}", f"{sk.get('name','')}:{sk.get('val',50)} {sk.get('dice','1d4')}")
            else:
                char.set_str(f"{prefix}技能{si}", sk)

    # Load spell definitions
    for si, spell in enumerate(ch_data.get('spells', []), 1):
        prefix = f"技能{CN_NUMS[si]}"
        char.set_str(f"{prefix}名称", spell['name'])
        char.set_str(f"{prefix}时机", spell.get('timing', '2'))
        char.set_attr(f"{prefix}类别", spell.get('category', 1))

        # MP cost (int or dice string)
        mp_val = spell.get('消耗mp', 0)
        if isinstance(mp_val, str):
            char.set_str(f"{prefix}消耗mp", mp_val)
        else:
            char.set_attr(f"{prefix}消耗mp", mp_val)

        # SAN cost
        san_val = spell.get('消耗san', 0)
        if isinstance(san_val, str):
            char.set_str(f"{prefix}消耗san", san_val)
        else:
            char.set_attr(f"{prefix}消耗san", san_val)

        char.set_attr(f"{prefix}默认延续性", spell.get('default_persist', 0))
        char.set_attr(f"{prefix}吟唱回合", spell.get('吟唱回合', 0))
        char.set_attr(f"{prefix}阶段", spell.get('phase', 0))

        mpf = spell.get('_mp_formula', '')
        if mpf:
            char.set_str(f"{prefix}_mp_formula", mpf)

        # Load each effect category
        for ei, eff in enumerate(spell.get('effects', [])):
            letter = CAT_LETTERS[ei]
            prefix_l = f"{prefix}类别{letter}"

            # Effect type (>8 stored as attribute since spell loading only checks
            # int keys for type; types 9/10/11 are special triggers)
            if 'type' in eff and eff['type'] > 8:
                char.set_attr(f"{prefix_l}type", eff['type'])

            for k, v in eff.items():
                if k == 'type':
                    continue  # skip type for normal categories (inferred from category)
                if isinstance(v, bool):
                    char.set_attr(f"{prefix_l}{k}", 1 if v else 0)
                elif isinstance(v, int):
                    char.set_attr(f"{prefix_l}{k}", v)
                elif isinstance(v, float):
                    char.set_attr(f"{prefix_l}{k}", v)
                elif isinstance(v, (list, dict)):
                    import json
                    char.set_str(f"{prefix_l}{k}", json.dumps(v, ensure_ascii=False))
                elif isinstance(v, str):
                    char.set_str(f"{prefix_l}{k}", v)

    engine.load_spells(uid)


def merge_character(engine, uid: str, js_data: dict) -> object:
    """Merge JS character card data with Python chData spell definitions.

    js_data contains ONLY COC base stats + combat skills + runtime HP/MP.
    Spells, damage dice, and special attributes come from characters_data_pvp.
    """
    char = engine.get_char(uid)
    char.name = js_data.get('name', uid)
    char.serial = js_data.get('serial', '')

    # ── 1) Load JS data FIRST (player's actual card values take priority) ──
    # COC base stats from JS
    coc_base = ['力量','体质','体型','敏捷','外貌','教育','智力','意志','幸运']
    for k in coc_base:
        if k in js_data.get('attrs', {}):
            char.set_attr(k, js_data['attrs'][k])

    # Runtime state from JS
    # NOTE: 行动力 and 回合行动数 are NOT loaded from JS card — they come from
    # PVP balance data (chData) to ensure correct dynamic action count calculation.
    runtime = ['体力','体力上限','魔力','魔力上限','理智','体格','等级',
               '闪避','伤害贯穿','可反击','状态']
    for k in runtime:
        if k in js_data.get('attrs', {}):
            char.set_attr(k, js_data['attrs'][k])

    # Combat skills from JS (player's actual skill values)
    for sk in ALL_COMBAT_SKILLS:
        if sk in js_data.get('attrs', {}):
            char.set_attr(sk, js_data['attrs'][sk])

    # String attributes from JS (.bta 自定义伤害值优先)
    for k, v in js_data.get('str_attrs', {}).items():
        char.set_str(k, v)

    # ── 2) Fill defaults from chData (only fill what JS didn't provide) ──
    serial = js_data.get('serial', '')
    mgid = js_data.get('attrs', {}).get('魔法少女序号', 0)

    ch_data = _find_chdata_by_serial(serial)
    if not ch_data and mgid:
        ch_data = _find_chdata_by_mgid(mgid)

    if ch_data:
        _load_spells_from_chdata(engine, uid, ch_data)

        # Damage dice from chData — only fill if not already set by JS
        for k, v in ch_data.get('str_attrs', {}).items():
            if not char.get_str(k):
                char.set_str(k, v)

        # Special attributes from chData — only fill if not already set
        for k in ('飞行', '召唤物HP单独显示'):
            if k in ch_data.get('attrs', {}) and char.get_attr(k, None) is None:
                char.set_attr(k, ch_data['attrs'][k])

        # attrs from chData — only fill if not already set by JS
        for k, v in ch_data.get('attrs', {}).items():
            if char.get_attr(k, None) is None:
                char.set_attr(k, v)

        # Inventory items from chData (物品栏)
        for inv_entry in ch_data.get('inventory', []):
            item_name = inv_entry.get('item', '')
            count = inv_entry.get('count', 1)
            if item_name and count > 0:
                engine.add_item_to_inventory(uid, item_name, count)

    engine.load_spells(uid)
    return char


def _char_summary(engine, uid: str) -> dict:
    """Return a summary of a character's HP/MP/SAN for display."""
    char = engine.get_char(uid)
    hp = engine._get_combat_hp(uid) or char.get_attr('体力', 10)
    hp_max = char.get_attr('体力上限', hp)
    mp = char.get_attr('魔力', 0) or 0
    mp_max = char.get_attr('魔力上限', mp)
    san = char.get_attr('理智', 50)
    return {
        'name': char.name,
        'hp': hp, 'hp_max': hp_max,
        'mp': mp, 'mp_max': mp_max,
        'san': san,
    }


def _get_all_human_player_ids(engine) -> set:
    """Collect all player_ids registered as controllers across all characters."""
    controllers = getattr(engine, '_player_controllers', {})
    result = set()
    for player_list in controllers.values():
        if isinstance(player_list, list):
            result.update(player_list)
    return result


def _is_human_controlled(engine, uid: str) -> bool:
    """Return True if any human player controls this character."""
    base = uid.split('__act')[0] if '__act' in str(uid) else uid
    controllers = getattr(engine, '_player_controllers', {}).get(base, [])
    return bool(controllers)


def _get_player_controlled_characters(engine) -> dict:
    """Return {player_id: [{uid, name, serial, count}, ...]} mapping for all human controllers.
    Duplicate same-name entries (e.g. summons) are deduplicated with a count field."""
    controllers = getattr(engine, '_player_controllers', {})
    result: dict = {}
    for base_uid, player_list in controllers.items():
        if not isinstance(player_list, list):
            continue
        char = engine.get_char(base_uid)
        name = char.name if char else base_uid
        serial = getattr(char, 'serial', '') if char else ''
        for pid in player_list:
            if pid not in result:
                result[pid] = []
            # Deduplicate by name
            existing = next((e for e in result[pid] if e['name'] == name), None)
            if existing:
                existing['count'] = existing.get('count', 1) + 1
            else:
                result[pid].append({'uid': base_uid, 'name': name, 'serial': serial, 'count': 1})
    return result


def _serialize_battle_state(engine) -> dict:
    """Full battle state serialization for JS display."""
    state = engine._get_state()
    il = engine._get_initiative()
    actions = engine._get_actions()
    effects = engine._get_effects()
    mp = engine._get_map()
    chars = {}
    for e in il:
        uid = e['userId']
        chars[uid] = _char_summary(engine, uid)

    active_uid = None
    active_entry = None
    if state and il:
        idx = state.get('activeIndex', 0)
        if 0 <= idx < len(il):
            active_entry = il[idx]
            active_uid = active_entry['userId']

    current_turn = None
    if active_entry:
        current_turn = {
            'userId': active_uid,
            'name': active_entry.get('name', '?'),
            'actions': engine._get_my_actions(active_uid),
        }

    return {
        'state': state,
        'initiative': il,
        'map': mp,
        'characters': chars,
        'effects': effects,
        'current_turn': current_turn,
    }


# ═══════════════════════════════════════════════════════════════
#  Reaction handling
# ═══════════════════════════════════════════════════════════════

class ReactionNeeded(Exception):
    """Raised when the engine needs a player reaction decision (PvP mode)."""
    def __init__(self, **kwargs):
        super().__init__()
        self.data = kwargs


class PvPFullBattleEngine(FullBattleEngine):
    """FullBattleEngine subclass that intercepts the AI reaction decision for PvP.

    Overrides _coc7_attack: identical to parent up to the reaction decision point,
    then raises ReactionNeeded instead of making an AI-weighted random choice.
    resolve_reaction() continues from where _coc7_attack left off.
    """

    def _coc7_attack(self, atk_uid, def_uid, skill_name, skill_val, dmg_dice, pen, leth,
                     bp_suffix="", dmg_adv=False, dmg_dis=False):
        """Override: raise ReactionNeeded at the reaction decision point (line 1210 of parent)."""
        achar = self.get_char(atk_uid)
        dchar = self.get_char(def_uid)
        aname = achar.name
        dname = dchar.name
        lines = []

        eff_skill = self._apply_buff_skill_mod(atk_uid, skill_val)
        atk_buffs = self._get_active_buffs(atk_uid)
        eff_bp = _calc_net_bp(atk_buffs, bp_suffix, skill_name)

        atk_result, bp_detail = roll_d100(eff_bp)
        atk_rank = success_rank(atk_result, eff_skill)
        bp_str = f", {bp_detail}" if bp_detail else ""
        lines.append(f"{aname} 的【{skill_name}】检定:")
        lines.append(f"  D100={atk_result}/{eff_skill}{bp_str} {rank_text(atk_rank)}")

        if atk_rank <= 0:
            fn = " [大失败! 反击等级+1]" if atk_rank == -2 else ""
            lines.append(f"  {aname} 攻击失败！{fn}")
            # 攻击失败也允许反应——继续执行以抛出 ReactionNeeded
            if not getattr(self, '_allow_failed_reaction', True):
                return (def_uid, atk_uid, lines)

        dodge_val = dchar.get_attr("闪避", 25)
        bmn, bmv = dchar.get_best_melee()
        dodge_val = self._apply_buff_skill_mod(def_uid, dodge_val)
        bmv = self._apply_buff_skill_mod(def_uid, bmv)
        def_buffs = self._get_active_buffs(def_uid)
        def_bp = _calc_net_bp(def_buffs, "", skill_name)

        # ── 格挡可用性 ──
        block_name, block_val = self._get_block_skill(def_uid)
        block_val = self._apply_buff_skill_mod(def_uid, block_val)
        def_entry = next((e for e in self._get_initiative() if e['userId'] == def_uid), None)
        can_block = (dchar.get_attr('可格挡', 0) == 1
                     and block_name is not None and block_val > 0)
        block_hp = def_entry.get('shield_block_hp', 0) if def_entry else 0
        # Check for block_redirect (summon takes damage instead of defender)
        _block_redirect = self._get_block_redirect_target(def_uid)
        can_block = can_block and (block_hp > 0 or _block_redirect is not None)

        # ── INTERCEPT: raise instead of AI random choice ──
        raise ReactionNeeded(
            atk_uid=atk_uid, def_uid=def_uid,
            atk_name=aname, def_name=dname,
            atk_rank=atk_rank, atk_result=atk_result,
            dodge_val=dodge_val, counter_val=bmv, counter_skill=bmn,
            dmg_dice=dmg_dice, pen=pen, leth=leth,
            bp_suffix=bp_suffix, dmg_adv=dmg_adv, dmg_dis=dmg_dis,
            def_bp=def_bp, atk_roll_detail=bp_detail,
            skill_name=skill_name, skill_val=skill_val,
            can_block=can_block,
            block_skill=block_name, block_val=block_val,
            block_hp=block_hp if not _block_redirect else (next((e for e in self._get_initiative() if e['userId'] == _block_redirect), {}).get('shield_block_hp', 0)),
            prefix_lines=lines,
        )
        # unreachable — return is just to satisfy type checker
        return (None, None, lines)

    def resolve_reaction(self, state: dict, choice: str):
        """Complete _coc7_attack after player chooses dodge/counter.

        This duplicates the second half of _coc7_attack (parent lines 1212-1321).
        Reference: battle_engine.py FullBattleEngine._coc7_attack lines 1212-1321.
        """
        atk_uid = state['atk_uid']
        def_uid = state['def_uid']
        lines = list(state['prefix_lines'])
        aname = state['atk_name']
        dname = state['def_name']
        dmg_dice = state['dmg_dice']
        pen = state['pen']
        leth = state['leth']
        bp_suffix = state.get('bp_suffix', '')
        dmg_adv = state.get('dmg_adv', False)
        dmg_dis = state.get('dmg_dis', False)
        atk_result = state['atk_result']
        atk_rank = state['atk_rank']
        def_bp = state.get('def_bp', '')

        if choice == 'dodge':
            # ── Dodge resolution (parent lines 1212-1221) ──
            rr, rd = roll_d100(def_bp)
            react_rank = success_rank(rr, state['dodge_val'])
            rbs = f", {rd}" if rd else ""
            lines.append(f"{dname} 的反应【闪避={state['dodge_val']}】:")
            lines.append(f"  D100={rr}/{state['dodge_val']}{rbs} {rank_text(react_rank)}")
            eff_atk = atk_rank
            if react_rank > 0:
                eff_atk -= react_rank
            if react_rank == -2:
                eff_atk += (2 if eff_atk == -1 else 1)
            eff_atk = max(-2, min(4, eff_atk))
            if eff_atk <= 0:
                lines.append(f"  {dname} 闪避成功！")
                return (def_uid, atk_uid, lines)
            winner_rank, winner_uid, loser_uid = eff_atk, atk_uid, def_uid
            loser_name, winner_name, winner_roll, is_counter = dname, aname, atk_result, False
        elif choice == 'counter':
            # ── Counter resolution (parent lines 1223-1291) ──
            dchar = self.get_char(def_uid)
            rr, rd = roll_d100(def_bp)
            react_rank = success_rank(rr, state['counter_val'])
            rbs = f", {rd}" if rd else ""
            lines.append(f"{dname} 的反应【反击:{state['counter_skill']}={state['counter_val']}】:")
            lines.append(f"  D100={rr}/{state['counter_val']}{rbs} {rank_text(react_rank)}")
            eff_atk, eff_react = atk_rank, react_rank
            if react_rank == -2:
                eff_atk += (2 if eff_atk == -1 else 1)
            if eff_atk == -2:
                eff_react = min(4, eff_react + 1)
            eff_atk, eff_react = max(-2, min(4, eff_atk)), max(-2, min(4, eff_react))

            if eff_react > eff_atk and eff_react > 0:
                # Defender wins
                winner_rank, winner_uid, loser_uid = eff_react, def_uid, atk_uid
                loser_name, winner_name = aname, dname
                winner_roll = rr
                cdmg = dchar.get_str("伤害值") or "1d4"
                cpen = dchar.get_attr("伤害贯穿", 1)
                cleth = dchar.get_attr("致死骰", 1) or 0
                dmg_dice, pen, leth, is_counter = cdmg, cpen, cleth, True
            elif eff_atk > eff_react and eff_atk > 0:
                # Attacker wins
                winner_rank, winner_uid, loser_uid, is_counter = eff_atk, atk_uid, def_uid, False
                loser_name, winner_name, winner_roll = dname, aname, atk_result
            elif eff_react == eff_atk and eff_react > 0:
                # ── Mutual hit (parent lines 1240-1291) ──
                # Attacker hits defender
                amx = max_damage(dmg_dice)
                adv = 0
                adetail = ""
                if eff_atk == 2:
                    r1, r2 = roll_dice(dmg_dice), roll_dice(dmg_dice)
                    adv = max(r1, r2)
                    adetail = f"优势({dmg_dice}={r1},{r2})→{adv}"
                elif eff_atk == 3:
                    if pen:
                        extra = roll_dice(dmg_dice)
                        adv = amx + extra
                        adetail = f"贯穿!满值{amx}+{dmg_dice}={extra}={adv}"
                    else:
                        adv = amx
                        adetail = f"{dmg_dice}满值={amx}"
                elif eff_atk == 4:
                    if atk_result == 1:
                        adv = amx * 2
                        adetail = f"大成功!满值{amx}×2={adv}"
                    elif pen:
                        adv = amx * 2
                        adetail = f"贯穿!满值{amx}×2={adv}"
                    else:
                        extra = roll_dice(dmg_dice)
                        adv = amx + extra
                        adetail = f"{dmg_dice}满值{amx}+{extra}={adv}"
                else:
                    if dmg_adv:
                        r1, r2 = roll_dice(dmg_dice), roll_dice(dmg_dice)
                        adv = max(r1, r2)
                        adetail = f"优势({dmg_dice}={r1},{r2})→{adv}"
                    elif dmg_dis:
                        r1, r2 = roll_dice(dmg_dice), roll_dice(dmg_dice)
                        adv = min(r1, r2)
                        adetail = f"劣势({dmg_dice}={r1},{r2})→{adv}"
                    else:
                        adv = roll_dice(dmg_dice)
                        adetail = f"{dmg_dice}={adv}"
                asr = self._absorb_damage_with_shield(def_uid, adv)
                aeff = asr[0]
                if asr[1] > 0:
                    lines.append(f"  护盾吸收(atk): {asr[1]}点")
                achp = self._get_combat_hp(def_uid) or 10
                achp = max(0, achp - aeff)
                self._set_combat_hp(def_uid, achp, source_dmg=aeff)
                lines.append(f"  {aname} 造成伤害: {adetail} → {aeff}点")
                lines.append(f"  {dname} HP: {achp}")

                # Defender counter-hits attacker
                cdmg = dchar.get_str("伤害值") or "1d4"
                cpen = dchar.get_attr("伤害贯穿", 1)
                cleth = dchar.get_attr("致死骰", 1) or 0
                dmx = max_damage(cdmg)
                ddmg = 0
                ddetail = ""
                if eff_react == 2:
                    r1, r2 = roll_dice(cdmg), roll_dice(cdmg)
                    ddmg = max(r1, r2)
                    ddetail = f"优势({cdmg}={r1},{r2})→{ddmg}"
                elif eff_react == 3:
                    if cpen:
                        extra = roll_dice(cdmg)
                        ddmg = dmx + extra
                        ddetail = f"贯穿!满值{dmx}+{cdmg}={extra}={ddmg}"
                    else:
                        ddmg = dmx
                        ddetail = f"{cdmg}满值={dmx}"
                elif eff_react == 4:
                    if rr == 1:
                        ddmg = dmx * 2
                        ddetail = f"大成功!满值{dmx}×2={ddmg}"
                    elif cpen:
                        ddmg = dmx * 2
                        ddetail = f"贯穿!满值{dmx}×2={ddmg}"
                    else:
                        extra = roll_dice(cdmg)
                        ddmg = dmx + extra
                        ddetail = f"{cdmg}满值{dmx}+{extra}={ddmg}"
                else:
                    ddmg = roll_dice(cdmg)
                    ddetail = f"{cdmg}={ddmg}"
                dsr = self._absorb_damage_with_shield(atk_uid, ddmg)
                deff = dsr[0]
                if dsr[1] > 0:
                    lines.append(f"  护盾吸收(def): {dsr[1]}点")
                dchp = self._get_combat_hp(atk_uid) or 10
                dchp = max(0, dchp - deff)
                self._set_combat_hp(atk_uid, dchp, source_dmg=deff)
                lines.append(f"  {dname} 反击伤害: {ddetail} → {deff}点")
                lines.append(f"  {aname} HP: {dchp}")

                lines.append(f"  双方同时命中! ({rank_text(eff_atk)} vs {rank_text(eff_react)})")
                return (atk_uid, def_uid, lines)
            else:
                lines.append("  无人得手！")
                return (None, None, lines)
        else:  # choice == 'block'
            # ── Shield block resolution (PvP) ──
            block_name = state.get('block_skill', '格挡')
            block_val = state.get('block_val', 0)
            block_hp = state.get('block_hp', 0)
            rr, rd = roll_d100(def_bp)
            react_rank = success_rank(rr, block_val)
            rbs = f", {rd}" if rd else ""
            lines.append(f"{dname} 的反应【格挡:{block_name}={block_val}】:")
            lines.append(f"  D100={rr}/{block_val}{rbs} {rank_text(react_rank)}")
            eff_atk = atk_rank
            if react_rank > 0: eff_atk -= react_rank
            if react_rank == -2: eff_atk += (2 if eff_atk == -1 else 1)
            eff_atk = max(-2, min(4, eff_atk))
            if eff_atk <= 0:
                # 格挡成功：盾牌承受伤害
                lines.append(f"  {dname} 格挡成功！盾牌承受攻击。")
                mx = max_damage(dmg_dice); blk_dmg = 0
                if atk_rank == 2: blk_dmg = max(roll_dice(dmg_dice), roll_dice(dmg_dice))
                elif atk_rank == 3: blk_dmg = mx + roll_dice(dmg_dice) if pen else mx
                elif atk_rank == 4: blk_dmg = mx * 2 if (atk_result == 1 or pen) else mx + roll_dice(dmg_dice)
                else: blk_dmg = roll_dice(dmg_dice)
                sr = self._absorb_damage_with_shield(def_uid, blk_dmg); blk_eff = sr[0]
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
                        lines.append(f"  {rt_name} 盾牌吸收: {absorbed}点")
                    cur_hp = self._get_combat_hp(_block_redirect) or 10
                    cur_hp = max(0, cur_hp - blk_eff); self._set_combat_hp(_block_redirect, cur_hp, source_dmg=blk_eff)
                    lines.append(f"  {rt_name} HP: {cur_hp}")
                    lines.append(f"  {dname} HP: {self._get_combat_hp(def_uid)}")
                    return (def_uid, atk_uid, lines)
                # ── 标准逻辑 ──
                if blk_eff > 0 and block_hp > 0:
                    absorbed = min(blk_eff, block_hp); blk_eff -= absorbed
                    entry = next((e for e in self._get_initiative() if e['userId'] == def_uid), None)
                    if entry: entry['shield_block_hp'] = block_hp - absorbed
                    lines.append(f"  盾牌吸收: {absorbed}点")
                cur_hp = self._get_combat_hp(def_uid) or 10
                cur_hp = max(0, cur_hp - blk_eff); self._set_combat_hp(def_uid, cur_hp, source_dmg=blk_eff)
                lines.append(f"  {dname} HP: {cur_hp}")
                return (def_uid, atk_uid, lines)
            else:
                # 格挡突破
                lines.append(f"  {dname} 格挡失败！攻击突破盾牌。")
                winner_rank, winner_uid, loser_uid = eff_atk, atk_uid, def_uid
                loser_name, winner_name, winner_roll, is_counter = dname, aname, atk_result, False

        # ── Single-winner damage resolution (parent lines 1293-1321) ──
        mx = max_damage(dmg_dice)
        dmg_val = 0
        dmg_detail = ""
        if winner_rank == 2:
            r1, r2 = roll_dice(dmg_dice), roll_dice(dmg_dice)
            dmg_val = max(r1, r2)
            dmg_detail = f"优势({dmg_dice}={r1},{r2})→{dmg_val}"
        elif winner_rank == 3:
            if pen:
                extra = roll_dice(dmg_dice)
                dmg_val = mx + extra
                dmg_detail = f"贯穿!满值{mx}+{dmg_dice}={extra}={dmg_val}"
            else:
                dmg_val = mx
                dmg_detail = f"{dmg_dice}满值={mx}"
        elif winner_rank == 4:
            if winner_roll == 1:
                dmg_val = mx * 2
                dmg_detail = f"大成功!满值{mx}×2={dmg_val}"
            elif pen:
                dmg_val = mx * 2
                dmg_detail = f"贯穿!满值{mx}×2={dmg_val}"
            else:
                extra = roll_dice(dmg_dice)
                dmg_val = mx + extra
                dmg_detail = f"{dmg_dice}满值{mx}+{extra}={dmg_val}"
        else:
            if dmg_adv:
                r1, r2 = roll_dice(dmg_dice), roll_dice(dmg_dice)
                dmg_val = max(r1, r2)
                dmg_detail = f"优势({dmg_dice}={r1},{r2})→{dmg_val}"
            elif dmg_dis:
                r1, r2 = roll_dice(dmg_dice), roll_dice(dmg_dice)
                dmg_val = min(r1, r2)
                dmg_detail = f"劣势({dmg_dice}={r1},{r2})→{dmg_val}"
            else:
                dmg_val = roll_dice(dmg_dice)
                dmg_detail = f"{dmg_dice}={dmg_val}"

        lines.append(f"  {winner_name} 胜利! {rank_text(winner_rank)}")
        sr = self._absorb_damage_with_shield(loser_uid, dmg_val)
        eff_dmg = sr[0]
        if sr[1] > 0:
            lines.append(f"  护盾吸收: {sr[1]}点")
        cur_hp = self._get_combat_hp(loser_uid) or 10
        exp_dmg = avg_damage(dmg_dice)
        if leth and exp_dmg > 6:
            # Lethality: d(2×cur_hp) ≤ expected_damage → instant 120% max HP death
            leth_die = max(2, cur_hp * 2)
            lr = random.randint(1, leth_die)
            if lr <= int(exp_dmg):
                cur_hp = 0
                lines.append(f"  致死骰: 1d{leth_die}={lr} ≤ {int(exp_dmg)} 成功! {loser_name}死亡")
            else:
                cur_hp = max(0, cur_hp - eff_dmg)
                lines.append(f"  致死骰: 1d{leth_die}={lr} > {int(exp_dmg)} 失败")
        else:
            cur_hp = max(0, cur_hp - eff_dmg)
        lines.append(f"  伤害: {dmg_detail} → {eff_dmg}点")
        self._set_combat_hp(loser_uid, cur_hp, source_dmg=eff_dmg)
        lines.append(f"  {loser_name} HP: {cur_hp}")
        return (winner_uid, loser_uid, lines)

    # ═══════════════════════════════════════════════════════════════
    #  Spell/Skill Damage Reaction Support (PvP overrides)
    # ═══════════════════════════════════════════════════════════════

    def _trigger_spell_reaction(self, caster_id, target_id, eff, spell, dmg_dice,
                                 can_dodge, can_counter, can_block=False, atk_rank=4, atk_roll=1, prefix_lines=None):
        """PvP override: raise ReactionNeeded for human defenders.
        For AI defenders, delegate to base (auto-resolve)."""
        dchar = self.get_char(target_id)
        def_base = target_id.split('__act')[0] if '__act' in str(target_id) else target_id
        def_controllers = getattr(self, '_player_controllers', {}).get(def_base, [])

        if not def_controllers:
            # AI defender -> delegate to base FullBattleEngine auto-resolve
            return super()._trigger_spell_reaction(
                caster_id, target_id, eff, spell, dmg_dice,
                can_dodge, can_counter, can_block, atk_rank, atk_roll=atk_roll, prefix_lines=prefix_lines)

        # Human defender -> raise ReactionNeeded
        achar = self.get_char(caster_id)
        dodge_val = dchar.get_attr("闪避", 25)
        bmn, bmv = dchar.get_best_melee()
        dodge_val = self._apply_buff_skill_mod(target_id, dodge_val)
        bmv = self._apply_buff_skill_mod(target_id, bmv)

        # ── 格挡可用性（含 ignore_unreactable_block 检查）──
        block_name, block_val = self._get_block_skill(target_id)
        block_val = self._apply_buff_skill_mod(target_id, block_val)
        def_entry = next((e for e in self._get_initiative() if e['userId'] == target_id), None)
        ignore_unreact = def_entry.get('ignore_unreactable_block', 0) if def_entry else 0
        can_physically_block = (dchar.get_attr('可格挡', 0) == 1
                                and block_name is not None and block_val > 0)
        block_hp = def_entry.get('shield_block_hp', 0) if def_entry else 0
        # Check for block_redirect (summon takes damage instead of defender)
        _block_redirect = self._get_block_redirect_target(target_id)
        can_physically_block = can_physically_block and (block_hp > 0 or _block_redirect is not None)
        # 玄武/三合一: 即使法术标记为不可反应/不可格挡，仍可格挡
        can_block_eff = can_physically_block and (can_block or ignore_unreact)

        # ── If no reaction options available at all, skip reaction entirely ──
        if not can_dodge and not can_counter and not can_block_eff:
            return (False, False, [])

        def_buffs = self._get_active_buffs(target_id)
        def_bp = _calc_net_bp(def_buffs, "", "")

        raise ReactionNeeded(
            reaction_type='spell',
            atk_uid=caster_id, def_uid=target_id,
            atk_name=achar.name, def_name=dchar.name,
            atk_rank=atk_rank, atk_roll=atk_roll, atk_result=1,
            dodge_val=dodge_val,
            counter_val=bmv, counter_skill=bmn,     # full counter capability
            dmg_dice=dmg_dice,
            pen=eff.get('可贯穿性', 0),
            leth=eff.get('致死值', 0),
            can_dodge=can_dodge,                     # data-driven
            can_counter=can_counter,                 # data-driven
            can_block=can_block_eff,                 # includes ignore_unreactable override
            block_skill=block_name, block_val=block_val,  # block info for 玄武 etc.
            spell_name=spell['name'],
            spell_index=spell['index'],
            effect=eff,
            spell=spell,
            lifesteal_ratio=float(eff.get('吸血比例', '0') or '0'),
            dot_dice=eff.get('每回合伤害骰', ''),
            dur=eff.get('持续回合', 0),
            bp_suffix='', dmg_adv=False, dmg_dis=False,
            def_bp=def_bp, atk_roll_detail='法术自动成功',
            skill_name=spell['name'], skill_val=100,
            prefix_lines=list(prefix_lines) if prefix_lines else [],
        )

    def resolve_spell_reaction(self, state: dict, choice: str):
        """Complete spell reaction after player input.
        Supports dodge, counter, AND block (for ignore_unreactable_block users like 玄武).
        Returns (dodged: bool, countered: bool, lines: list)."""
        if choice == 'dodge' and not state.get('can_dodge', True):
            return (False, False, ["该法术不可闪避！"])
        if choice == 'counter' and not state.get('can_counter', True):
            return (False, False, ["该法术不可反击！"])
        if choice == 'block' and not state.get('can_block', False):
            return (False, False, ["该法术不可格挡！"])

        if choice == 'dodge':
            return self._resolve_spell_dodge(
                state['atk_uid'], state['def_uid'],
                state.get('effect', {}), state.get('spell', {}),
                state['dmg_dice'], state['dodge_val'], state['atk_rank'])
        elif choice == 'counter':
            return self._resolve_spell_counter(
                state['atk_uid'], state['def_uid'],
                state.get('effect', {}), state.get('spell', {}),
                state['dmg_dice'], state['counter_skill'], state['counter_val'],
                state['atk_rank'])
        elif choice == 'block':
            block_hp_val = next((e for e in self._get_initiative()
                if e['userId'] == state['def_uid']), {}).get('shield_block_hp', 0)
            return self._resolve_spell_block(
                state['atk_uid'], state['def_uid'],
                state.get('effect', {}), state.get('spell', {}),
                state['dmg_dice'], state['block_skill'], state['block_val'],
                block_hp_val, state['atk_rank'])
        else:
            block_hint = ' / .e 格挡' if state.get('can_block') else ''
            return (False, False, [f'法术反应请使用 .e 闪避 / .e 反击{block_hint}'])

    def _execute_spell(self, caster_id, target_id, spell):
        """Override: catch ReactionNeeded and prepend spell header to prefix_lines."""
        try:
            return super()._execute_spell(caster_id, target_id, spell)
        except ReactionNeeded as e:
            # Build spell header that was already computed in parent before
            # the ReactionNeeded was raised (spell name, MP cost, etc.)
            char = self.get_char(caster_id)
            tname = self.get_char(target_id).name if target_id and target_id != caster_id else '自身'
            header_lines = [f'{char.name} 释放【{spell["name"]}】→ {tname}']

            # Reconstruct MP/SAN cost display (already deducted in parent)
            mp_cost_raw = spell.get('消耗mp', 0)
            mp_formula = spell.get('_mp_formula', '')
            if isinstance(mp_cost_raw, str) and ('d' in str(mp_cost_raw).lower()):
                pass  # Dice-based cost — hard to reconstruct exactly, skip
            elif mp_cost_raw:
                mp_cost = int(mp_cost_raw) if mp_cost_raw else 0
                if mp_cost > 0:
                    header_lines.append(f'  消耗 {mp_cost} MP')
            san_cost_raw = spell.get('消耗san', 0)
            if isinstance(san_cost_raw, str) and 'd' in str(san_cost_raw).lower():
                pass  # Dice-based cost — skip
            elif san_cost_raw:
                san_cost = int(san_cost_raw) if san_cost_raw else 0
                if san_cost > 0:
                    header_lines.append(f'  消耗 {san_cost} SAN')

            # Prepend header to any prefix_lines from _handle_spell_damage_effect
            existing = list(e.data.get('prefix_lines', []))
            e.data['prefix_lines'] = header_lines + existing
            raise

    def _create_summon(self, caster_id, template_name, summon_group=None, shared_roll=None, shared_rank=None):
        """Override: auto-bind new summons to players who .asfull the caster."""
        summon_id = super()._create_summon(caster_id, template_name,
            summon_group=summon_group, shared_roll=shared_roll, shared_rank=shared_rank)
        if summon_id:
            full_ctrls = getattr(self, '_full_controllers', {})
            # Resolve multi-action suffix: _full_controllers uses base_uid (e.g. Y5),
            # but caster_id may be Y5__act1 from the active initiative slot.
            base_caster = caster_id.split('__act')[0] if '__act' in str(caster_id) else caster_id
            if base_caster in full_ctrls:
                if not hasattr(self, '_player_controllers'):
                    self._player_controllers = {}
                if not hasattr(self, '_ai_overrides'):
                    self._ai_overrides = {}
                if not hasattr(self, '_ai_override_state'):
                    self._ai_override_state = {}
                self._player_controllers[summon_id] = list(full_ctrls[base_caster])
        return summon_id


# ═══════════════════════════════════════════════════════════════
#  PvE AI helpers
# ═══════════════════════════════════════════════════════════════

_Q_TABLE_PVP_CACHE = None  # module-level cache to avoid re-parsing JSON every action

def _load_q_table_pvp():
    """Try to load PvP Q-learning weights for AI opponents.
    Returns dict: {'solo': Q_solo, 'team': Q_team, 'summon': Q_summon}
    Result is cached — server restart needed to pick up weight file changes."""
    global _Q_TABLE_PVP_CACHE
    if _Q_TABLE_PVP_CACHE is not None:
        return _Q_TABLE_PVP_CACHE
    try:
        weight_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ai_weights_pvp.json')
        if not os.path.exists(weight_path):
            _Q_TABLE_PVP_CACHE = None  # cache the negative result too
            return None
        with open(weight_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        from collections import defaultdict

        def parse_qdict(qdict):
            Q = defaultdict(lambda: defaultdict(float))
            for ck, entries in qdict.items():
                for key, val in entries.items():
                    parts = key.split('__')
                    state_str = parts[0]
                    action = '__'.join(parts[1:])  # preserve e.g. "SKILL_2__T0" not just "SKILL_2"
                    state = tuple(int(v) for v in state_str.split('|'))
                    if len(state) < 16:
                        padding_needed = 16 - len(state)
                        state = state + (0,) * padding_needed
                    Q[ck][(state, action)] = val
            return Q

        if 'Q_solo' in data and 'Q_team' in data:
            result = {
                'solo': parse_qdict(data['Q_solo']),
                'team': parse_qdict(data['Q_team']),
                'summon': parse_qdict(data.get('Q_summon', {})),
            }
        elif 'Q' in data:
            Q = parse_qdict(data['Q'])
            result = {'solo': Q, 'team': Q, 'summon': defaultdict(lambda: defaultdict(float))}
        else:
            result = None
        _Q_TABLE_PVP_CACHE = result
        return result
    except Exception:
        _Q_TABLE_PVP_CACHE = None
        return None


def _is_override_action_available(engine, uid, action_str):
    """Check if an override action string is currently usable by the AI character.
    Returns (is_available: bool, reason: str).
    """
    char = engine.get_char(uid)
    if not char: return (False, '角色不存在')
    il = engine._get_initiative()
    my_entry = next((e for e in il if e['userId'] == uid), None)
    if not my_entry: return (False, '不在先攻列表中')

    a = action_str.strip()

    # ── none / NONE ──
    if a.lower() == 'none':
        return (True, '')

    # ── s0 [target] ──
    if a.startswith('s0') or a == 's0':
        bn, bv = char.get_best_melee()
        if bv <= 0:
            return (False, '没有可用的近战技能')
        # Check range: any enemy within move+range reach
        my_coord = my_entry.get('coord', '')
        my_team = my_entry.get('team', 'Y')
        enemies = [e for e in il if e['team'] != my_team
                   and (engine._get_combat_hp(e['userId']) or 0) > 0]
        if not enemies:
            return (False, '没有存活的敌方目标')
        # If target specified (e.g. "s0 Y1", "s0 R", "s0 RP"), check target exists
        parts = a.split()
        if len(parts) >= 2:
            tgt_serial = parts[1].upper()
            if tgt_serial in ('R', 'RP'):
                # Random target: check pool availability
                if tgt_serial == 'RP':
                    candidates_rp_s0 = [e for e in enemies if not e.get('isSummon')]
                    if not candidates_rp_s0:
                        return (False, '没有存活的敌方玩家目标（RP）')
                # R: always ok as long as there are enemies (already checked above)
                return (True, '')
            found = False
            for e in enemies:
                ec = engine.get_char(e.get('baseUserId', e['userId']))
                if ec and getattr(ec, 'serial', '') and ec.serial.upper() == tgt_serial:
                    if (engine._get_combat_hp(e['userId']) or 0) > 0:
                        found = True
                        break
            if not found:
                return (False, f'目标 {tgt_serial} 不存在或已阵亡')
        return (True, '')

    # ── sN [target] (skill, N >= 1) ──
    import re as _re
    sm = _re.match(r'^s(\d+)$', a.split()[0] if ' ' in a else a)
    if sm:
        sn = int(sm.group(1))
        spells = char.spells or engine.load_spells(uid)
        spell = next((s for s in spells if s['index'] == sn), None)
        if not spell:
            return (False, f'技能 s{sn} 不存在')
        timing = spell.get('时机', '2')
        if not has_timing(timing, '2'):
            return (False, f'技能 {spell.get("name", sn)} 不能作为主动作')
        # MP check
        cur_mp = char.get_attr('魔力', 0) or 0
        mp_cost = spell.get('消耗mp', 0)
        mp_formula = spell.get('_mp_formula', '')
        if mp_formula == 'ceil_2d6_div_2':
            mp_cost = 1
        elif mp_formula == 'summon_count_ceil_half':
            mp_cost = 4
        if isinstance(mp_cost, str):
            try: mp_cost = int(mp_cost)
            except: mp_cost = 0
        if mp_cost > cur_mp:
            return (False, f'MP不足（需要{mp_cost}，当前{cur_mp}）')
        # Check same-name active effect
        effects = engine._get_effects()
        for e in effects:
            if (e.get('remainingRounds', 0) > 0 or e.get('persistent', 0)):
                if e.get('spellName', '') == spell.get('name', ''):
                    return (False, f'同名效果 {spell.get("name", "")} 仍在持续')
        # Check same-template summon alive
        my_summons = set()
        for e in il:
            if e.get('isSummon') and e.get('ownerId') == uid and (engine._get_combat_hp(e['userId']) or 0) > 0:
                my_summons.add(e.get('name', ''))
        for eff in spell.get('effects', []):
            if eff.get('type') == 5:
                tmpl = eff.get('召唤物模板', '')
                if tmpl and any(sn == tmpl or sn.endswith(f"的{tmpl}") for sn in my_summons):
                    return (False, f'召唤物 {tmpl} 已达到上限')
        # If target specified, check it
        parts2 = a.split()
        if len(parts2) >= 2:
            tgt_serial2 = parts2[1].upper()
            if tgt_serial2 in ('R', 'RP'):
                # Random target: always available for skills (pool checked at execution time)
                return (True, '')
            my_team2 = my_entry.get('team', 'Y')
            enemies2 = [e for e in il if e['team'] != my_team2
                        and (engine._get_combat_hp(e['userId']) or 0) > 0]
            found2 = False
            for e in enemies2:
                ec = engine.get_char(e.get('baseUserId', e['userId']))
                if ec and getattr(ec, 'serial', '') and ec.serial.upper() == tgt_serial2:
                    if (engine._get_combat_hp(e['userId']) or 0) > 0:
                        found2 = True
                        break
            if not found2:
                return (False, f'目标 {tgt_serial2} 不存在或已阵亡')
        return (True, '')

    # ── bta xxx ── (custom attack, treat as s0)
    if a.startswith('bta'):
        bn, bv = char.get_best_melee()
        if bv <= 0:
            return (False, '没有可用的近战技能')
        my_team3 = my_entry.get('team', 'Y')
        enemies3 = [e for e in il if e['team'] != my_team3
                    and (engine._get_combat_hp(e['userId']) or 0) > 0]
        if not enemies3:
            return (False, '没有存活的敌方目标')
        return (True, '')

    # ── g / u (give item, use item) ──
    if a.lower() in ('g', 'u'):
        if not hasattr(engine, '_has_healing_item') or not engine._has_healing_item(uid):
            return (False, '没有可用的物品')
        return (True, '')

    # ── eat / give (蛋糕) ──
    if a.lower() == 'eat':
        if not hasattr(engine, '_has_ready_cake') or not engine._has_ready_cake():
            return (False, '没有可用的蛋糕')
        if not hasattr(engine, '_has_healing_item') or not engine._has_healing_item(uid):
            return (False, '没有可用的治疗物品')
        return (True, '')
    if a.lower() == 'give':
        if not hasattr(engine, '_has_ready_cake') or not engine._has_ready_cake():
            return (False, '没有可用的蛋糕')
        if not hasattr(engine, '_has_healing_item') or not engine._has_healing_item(uid):
            return (False, '没有可用的治疗物品')
        my_team4 = my_entry.get('team', 'Y')
        teammates = [e for e in il if e['team'] == my_team4
                     and e['userId'] != uid
                     and not e.get('isSummon')
                     and (engine._get_combat_hp(e['userId']) or 0) > 0]
        if not teammates:
            return (False, '没有存活的队友可以给予')
        return (True, '')

    # ── m [coord] (move) ──
    if a.lower().startswith('m'):
        if hasattr(engine, '_get_move_power') and engine._get_move_power(uid) <= 0:
            return (False, '移动力不足')
        return (True, '')

    # Unknown action: assume unavailable
    return (False, f'未知动作: {a}')


def _resolve_override_main(engine, uid, override):
    """Resolve the next action from an override config for a character.
    Returns (command, action_key) tuple, or (None, None) on fallback-needed,
    or ('.i end', None) when all groups exhausted.
    """
    import random as _random
    state = engine._get_state()
    current_round = state.get('round', 1) if state else 1

    # Init/refresh override state
    if not hasattr(engine, '_ai_override_state'):
        engine._ai_override_state = {}
    ost = engine._ai_override_state.get(uid)
    if not ost or ost.get('last_round', 0) != current_round:
        ost = {'main_group': 0, 'addi_group': 0, 'last_round': current_round}
        engine._ai_override_state[uid] = ost

    main_groups = override.get('main', [])
    addi_groups = override.get('addi', [])

    # Check if ALL groups are exhausted first (so we know this at the start)
    total_main = ost['main_group'] >= len(main_groups) if main_groups else True
    total_addi = ost['addi_group'] >= len(addi_groups) if addi_groups else True

    # ── Try main groups first ──
    while ost['main_group'] < len(main_groups):
        gidx = ost['main_group']
        group = main_groups[gidx]
        # Filter available
        avail = []
        for action_str, weight in group:
            ok, _ = _is_override_action_available(engine, uid, action_str)
            if ok:
                w = weight if weight is not None else 1
                avail.append((action_str, w))
        if avail:
            # Weighted random selection
            actions_list = [a for a, _ in avail]
            weights_list = [w for _, w in avail]
            chosen = _random.choices(actions_list, weights=weights_list, k=1)[0]
            ost['main_group'] = gidx + 1
            engine._ai_override_state[uid] = ost
            return _override_action_to_cmd(engine, uid, chosen)
        else:
            # All actions in this group unavailable — skip group, try next
            ost['main_group'] = gidx + 1
            engine._ai_override_state[uid] = ost

    # ── Then try addi groups ──
    while ost['addi_group'] < len(addi_groups):
        aidx = ost['addi_group']
        group = addi_groups[aidx]
        avail = []
        for action_str, weight in group:
            ok, _ = _is_override_action_available(engine, uid, action_str)
            if ok:
                w = weight if weight is not None else 1
                avail.append((action_str, w))
        if avail:
            actions_list = [a for a, _ in avail]
            weights_list = [w for _, w in avail]
            chosen = _random.choices(actions_list, weights=weights_list, k=1)[0]
            ost['addi_group'] = aidx + 1
            engine._ai_override_state[uid] = ost
            return _override_action_to_cmd(engine, uid, chosen)
        else:
            ost['addi_group'] = aidx + 1
            engine._ai_override_state[uid] = ost

    # All groups exhausted
    return ('.i end', None)


def _override_action_to_cmd(engine, uid, action_str):
    """Convert an override action string to a (command, action_key) tuple."""
    a = action_str.strip()

    if a.lower() == 'none':
        # Consume 1 附加 action silently, do nothing visible
        return ('.a none', 'OVERRIDE:none')

    if a.startswith('bta'):
        # Custom attack: parse bta args and treat as .s0
        return ('.s0', f'OVERRIDE:{a}')

    if a.startswith('s0') or a == 's0':
        return ('.s0', f'OVERRIDE:{a}')

    if a.startswith('s'):
        # sN [target]
        parts = a.split()
        sn = parts[0][1:]  # extract N from 'sN'
        return (f'.s{sn}', f'OVERRIDE:{a}')

    if a.lower() == 'eat':
        return ('.a eat', f'OVERRIDE:{a}')
    if a.lower() == 'give':
        return ('.a give', f'OVERRIDE:{a}')
    if a.lower().startswith('m'):
        return ('.a m', f'OVERRIDE:{a}')
    if a.lower() in ('g', 'u'):
        return ('.a eat', f'OVERRIDE:{a}')  # treat as item use

    # Unknown — end turn safely
    return ('.i end', None)


def _resolve_ai_reaction_choice(engine, def_uid, can_dodge=True, can_counter=True, can_block=False):
    """Select an AI reaction choice (dodge/counter/block) respecting .setaiact react override.
    Returns the choice string: 'dodge', 'counter', or 'block'.
    """
    import random as _random_rc

    # Check for react override
    def_char = engine.get_char(def_uid) if def_uid else None
    def_serial = getattr(def_char, 'serial', '') if def_char else ''
    react_override = None
    if def_serial:
        overrides = getattr(engine, '_ai_overrides', {})
        ov = overrides.get(def_serial)
        if ov and 'react' in ov and ov['react']:
            react_override = ov['react']

    if react_override:
        # react format: [[choice, weight|null], ...] — only first or-group
        candidates = react_override[0]
        # Map choice characters to internal names
        choice_map = {'d': 'dodge', 'c': 'counter', 'b': 'block'}
        avail = []
        for ch, weight in candidates:
            ch_lower = ch.lower()
            mapped = choice_map.get(ch_lower, ch_lower)
            # Check if this reaction type is allowed for the current attack
            if mapped == 'dodge' and not can_dodge: continue
            if mapped == 'counter' and not can_counter: continue
            if mapped == 'block' and not can_block: continue
            w = weight if weight is not None else 1
            avail.append((mapped, w))
        if avail:
            choices_list = [c for c, _ in avail]
            weights_list = [w for _, w in avail]
            return _random_rc.choices(choices_list, weights=weights_list, k=1)[0]
        # Fall through if no valid choices in override

    # Default: random between dodge and counter (existing behavior)
    opts = []
    if can_dodge: opts.append('dodge')
    if can_counter: opts.append('counter')
    if can_block: opts.append('block')
    return _random_rc.choice(opts) if opts else 'dodge'


def _ai_decide_action(engine, uid: str, Q=None):
    """Decide an AI action for the given character. Returns (command, action_key) tuple.
    command: e.g. '.s0', '.s2', '.a eat', '.i end'
    action_key: full Q-table key (e.g. 'BASIC_ATTACK__T0'), or None if no Q-table used.
    Q should be dict: {'solo': Q_solo, 'team': Q_team, 'summon': Q_summon}
    """
    char = engine.get_char(uid)
    spells = char.spells or engine.load_spells(uid) if char else []

    # ── Check .setaiact override (priority > Q-table) ──
    if char:
        overrides = getattr(engine, '_ai_overrides', {})
        override = overrides.get(char.serial) if char.serial else None
        if override and 'main' in override:
            cmd, ak = _resolve_override_main(engine, uid, override)
            if cmd is not None:
                return (cmd, ak)
            # cmd is None → all actions unavailable this turn, fall through to Q-table

    if not char or not spells:
        return ('.i end', None)

    # Gather available actions
    available = []
    # .s0 (basic melee)
    bn, bv = char.get_best_melee()
    if bv > 0:
        available.append('.s0')
    # .sN for each spell with timing 2 (主动作)
    for s in spells:
        timing = s.get('时机', '2')
        if has_timing(timing, '2'):
            available.append(f".s{s['index']}")

    if not available:
        return ('.i end', None)

    # Use Q-table if available, otherwise random weighted
    if Q:
        try:
            from ai_trainer_pvp import encode_state, get_available_actions
            st = encode_state(engine, uid)
            avail = get_available_actions(engine, uid)
            if avail:
                # Route to solo or team table based on living allies
                il = engine._get_initiative()
                my_entry = next((e for e in il if e['userId'] == uid), None)
                my_team = my_entry.get('team', 'Y') if my_entry else 'Y'
                living_allies = [e for e in il if e['team'] == my_team
                                 and e['userId'] != uid
                                 and not e.get('isSummon')
                                 and (engine._get_combat_hp(e['userId']) or 0) > 0]
                qtable = Q['solo'] if not living_allies else Q['team']
                ck = char.serial or uid
                qdict = qtable.get(ck, {})
                if qdict:
                    import random as _random
                    q_vals = [qdict.get((st, ak), 0.0) for ak, an in avail]
                    # deformation
                    zt = char.get_attr('状态', 60)
                    deform_prob = (100 - zt) / 100.0 * 0.4
                    if _random.random() < deform_prob:
                        scale = max(max(abs(q) for q in q_vals), 1.0)
                        q_vals = [q + _random.uniform(-deform_prob, deform_prob) * scale for q in q_vals]
                    if q_vals:
                        import math
                        max_q = max(q_vals)
                        exp_vals = [math.exp((q - max_q) / 1.0) for q in q_vals]
                        total = sum(exp_vals)
                        probs = [e / total for e in exp_vals] if total > 0 else None
                        idx = _random.choices(range(len(avail)), weights=probs, k=1)[0]
                        ak, an = avail[idx]
                        # Map action key to command
                        base_ak = ak.split('__')[0] if '__' in ak else ak
                        if base_ak == 'BASIC_ATTACK': return ('.s0', ak)
                        if base_ak == 'MOVE_TOWARD': return ('.s0', ak)
                        if base_ak == 'EAT_CAKE':
                            # Only eat if character has healing items
                            if engine._has_healing_item(uid):
                                return ('.a eat', ak)
                        if base_ak == 'GIVE_CAKE':
                            # Only give if character has healing items
                            if engine._has_healing_item(uid):
                                return ('.a give', ak)
                        if base_ak.startswith('SKILL_'):
                            sn = int(base_ak.split('_')[1])
                            # Validate: skill must be usable as a main action (timing 2)
                            spell = next((s for s in spells if s['index'] == sn), None)
                            if spell and has_timing(spell.get('时机', '2'), '2'):
                                return (f'.s{sn}', ak)
                            # Invalid for main action → fall through to random fallback
        except Exception:
            pass

    # Fallback: random among available (no Q-table, so no action_key)
    cmd = random.choice(available)
    return (cmd, None)


def _format_zone_summary(engine) -> str:
    """Generate a summary of all active zone effects and their tick results."""
    effects = engine._get_effects()
    zones = [e for e in effects if e.get("type") == "zone" and e.get("radius", 0) > 0]
    if not zones:
        return "── 活跃领域 ──\n  （无活跃领域）"
    tick_results = getattr(engine, '_zone_tick_results', [])
    lines = ["── 活跃领域 ──"]
    for z in zones:
        spell_name = z.get("spellName", "未知领域")
        radius = z.get("radius", 0)
        remaining = z.get("remainingRounds", 0)
        center = z.get("center", "?")
        src_uid = z.get("sourceUserId", "")
        src_name = ""
        if src_uid:
            src_char = engine.get_char(src_uid)
            src_name = src_char.name if src_char else src_uid
        parts = [f"【{spell_name}】"]
        if src_name:
            parts.append(f"施放者: {src_name}")
        parts.append(f"中心: {center}  半径: {radius}  剩余: {remaining}回合")
        lines.append("  " + "  ".join(parts))
        # Per-entity tick results for this zone
        zone_results = [r for r in tick_results if r.get('spell_name') == spell_name]
        if zone_results:
            for r in zone_results:
                tname = r['target_name']
                if r['type'] == 'damage':
                    alive_mark = '' if r['alive'] else ' → 阵亡'
                    lines.append(f"    {tname}: {r['dice']}={r['roll']} → {r['amount']}点伤害 HP: {r['hp']}/{r['hp_max']}{alive_mark}")
                elif r['type'] == 'heal_hp':
                    lines.append(f"    {tname}: {r['dice']}={r['roll']} → +{r['amount']}HP HP: {r['hp']}/{r['hp_max']}")
                elif r['type'] == 'heal_mp':
                    lines.append(f"    {tname}: {r['dice']}={r['roll']} → +{r['amount']}MP")
        elif z.get("tickDmg"):
            lines.append(f"    每回合伤害: {z['tickDmg']}（范围内无目标）")
        elif z.get("tickHealHp"):
            lines.append(f"    每回合回复HP: {z['tickHealHp']}（范围内无友方）")
        elif z.get("tickHealMp"):
            lines.append(f"    每回合回复MP: {z['tickHealMp']}（范围内无友方）")
    lines.append("")
    return "\n".join(lines)


def _extract_summon_damage_info(attack_details):
    """Robustly extract structured damage info from summon attack text lines.
    Returns dict with: hit, dmg_dice, dmg_result, eff_dmg, target_hp, summon_hp, summon_hp_max
    """
    info = {'hit': False, 'dmg_dice': '', 'dmg_result': 0, 'eff_dmg': 0,
            'target_hp': 0, 'summon_hp': 0, 'summon_hp_max': 0, 'reaction_lines': []}
    d100_count = 0
    for ld in attack_details:
        s = ld.strip()
        if not s:
            continue
        if 'D100=' in s:
            if d100_count == 0:
                if '失败' not in s and '大失败' not in s:
                    info['hit'] = True
            else:
                info['reaction_lines'].append(s)
            d100_count += 1
        elif 'HP:' in s and 'D100' not in s:
            m = re.search(r'HP:\s*(-?\d+)', s)
            if m:
                hp_val = int(m.group(1))
                # First HP line after damage is usually target HP;
                # later HP lines could be summon HP from counter damage
                if info['target_hp'] == 0:
                    info['target_hp'] = hp_val
                else:
                    info['summon_hp'] = hp_val
        elif '伤害:' in s or '造成' in s:
            # "伤害: 贯穿!满值12×2=24 → 24点" or "  造成 24 点伤害"
            # Extract dice expression and result
            dice_m = re.search(r'(\d+d\d+(?:[+\-×x]\d+)?(?:[+\-×x]\d+d\d+)?)', s)
            if dice_m:
                info['dmg_dice'] = dice_m.group(1)
            # Extract raw result before →
            result_m = re.search(r'=(\d+)\s*→', s)
            if result_m:
                info['dmg_result'] = int(result_m.group(1))
            # Extract effective damage after →
            eff_m = re.search(r'→\s*(\d+)\s*点', s)
            if eff_m:
                info['eff_dmg'] = int(eff_m.group(1))
            # Alternative: "造成 X 点伤害"
            alt_m = re.search(r'造成\s+(\d+)\s+点伤害', s)
            if alt_m:
                info['eff_dmg'] = int(alt_m.group(1))
    return info


def _format_summon_attack_block(sname, entries):
    """Format a batch of same-name summon attacks into structured display.
    Module-level function, usable from both _run_ai_turns and submit_action.
    entries: list of {name, display_name, hp, hp_max, alive, round_header,
                      enemy_text, attack_header, attack_details}
    Returns formatted string.
    """
    alive = [e for e in entries if e['alive']]
    dead_count = sum(1 for e in entries if not e['alive'])
    if not alive and dead_count == 0:
        return ''

    lines = []
    first = entries[0]
    if first.get('round_header'):
        lines.append(first['round_header'])

    display_name = first.get('display_name', sname)
    if dead_count > 0 and alive:
        lines.append(f"现在是{display_name}（{len(alive)}个存活，{dead_count}个已阵亡）的回合了。")
    elif dead_count > 0:
        lines.append(f"（{dead_count}个{display_name}已阵亡，跳过回合）")
        return '\n'.join(lines)
    else:
        lines.append(f"现在是{display_name}（{len(alive)}个存活）的回合了。")

    hp_parts = [f"{e['hp']}/{e['hp_max']}" for e in entries]
    total_hp = sum(e['hp'] for e in entries)
    total_max = sum(e['hp_max'] for e in entries)
    lines.append(f"hp：{' '.join(hp_parts)}，总 {total_hp}/{total_max}")
    if first.get('enemy_text'):
        lines.append(first['enemy_text'])

    lines.append(f"召唤物 {sname} ×{len(alive)}：")

    parsed_attacks = []
    for i, e in enumerate(alive, 1):
        details = e.get('attack_details', [])
        # Use structured extractor if available, otherwise fall back to text parsing
        dmg_info = e.get('damage_info')
        if dmg_info:
            pa = {'idx': i, 'skill': '', 'target': '', 'roll_line': '',
                  'dmg_dice': dmg_info.get('dmg_dice', ''),
                  'dmg_result': dmg_info.get('dmg_result', 0),
                  'eff_dmg': dmg_info.get('eff_dmg', 0),
                  'target_hp': dmg_info.get('target_hp', 0),
                  'reaction_lines': dmg_info.get('reaction_lines', []),
                  'hit': dmg_info.get('hit', False),
                  'attacker_dmg': 0, 'attacker_hp': e['hp'],
                  'summon_hp': dmg_info.get('summon_hp', 0),
                  'summon_hp_max': dmg_info.get('summon_hp_max', 0)}
        else:
            info = _extract_summon_damage_info(details)
            pa = {'idx': i, 'skill': '', 'target': '', 'roll_line': '',
                  'dmg_dice': info['dmg_dice'], 'dmg_result': info['dmg_result'],
                  'eff_dmg': info['eff_dmg'], 'target_hp': info['target_hp'],
                  'reaction_lines': info['reaction_lines'], 'hit': info['hit'],
                  'attacker_dmg': 0, 'attacker_hp': e['hp'],
                  'summon_hp': info['summon_hp'], 'summon_hp_max': info['summon_hp_max']}
            # Also capture the first D100 line as roll_line
            for ld in details:
                s = ld.strip()
                if 'D100=' in s and not pa['roll_line']:
                    pa['roll_line'] = s
                    break
        parsed_attacks.append(pa)

    # ── Action line: extract target from attack_header ──
    target_groups = {}
    for i, e in enumerate(alive):
        pa = parsed_attacks[i]
        hdr = e.get('attack_header', '')
        # Extract target from header: "斗殴→歌莉娅" → "歌莉娅"
        target = '敌方目标'
        if '→' in hdr:
            target = hdr.rsplit('→', 1)[-1].strip()
        if target not in target_groups:
            target_groups[target] = []
        target_groups[target].append(pa)

    action_parts = []
    for tgt, pas in target_groups.items():
        idxs = [pa['idx'] for pa in pas]
        if len(idxs) > 1 and idxs == list(range(idxs[0], idxs[-1]+1)):
            action_parts.append(f"#{idxs[0]}~#{idxs[-1]} 斗殴→{tgt}")
        else:
            for pa in pas:
                action_parts.append(f"#{pa['idx']} 斗殴→{tgt}")
    lines.append('  ' + '；'.join(action_parts))

    # ── Roll section ──
    lines.append('检定:')
    for pa in parsed_attacks:
        if pa['roll_line']:
            lines.append(f"  {pa['roll_line']}")

    # ── Reaction section: group by type+skill ──
    has_reaction = any(pa['reaction_lines'] for pa in parsed_attacks)
    if has_reaction:
        lines.append('反应：')
        # Collect all reaction entries: (type, skill, d100_line, other_lines)
        react_entries = []  # [{type, skill, d100, others}]
        for pa in parsed_attacks:
            cur = None
            for rl in pa['reaction_lines']:
                s = rl.strip()
                if 'D100=' in s:
                    cur = {'d100': s, 'others': [], 'type': '?', 'skill': '?'}
                    react_entries.append(cur)
                elif cur is not None:
                    # Determine type/skill from line
                    if '反击' in s or 'counter' in s.lower():
                        cur['type'] = '反击'
                    elif '闪避' in s or 'dodge' in s.lower():
                        cur['type'] = '闪避'
                    elif '格挡' in s or 'block' in s.lower():
                        cur['type'] = '格挡'
                    m = re.search(r'【(.+?)】', s)
                    if m:
                        cur['skill'] = m.group(1)
                    cur['others'].append(s)
        # Group by (type, skill)
        react_groups = {}
        for r in react_entries:
            key = (r['type'], r['skill'])
            react_groups.setdefault(key, []).append(r)
        for (rtype, rskill), entries in react_groups.items():
            lines.append(f"  {rtype}:{rskill}:")
            for r in entries:
                extra = ' ' + ' '.join(r['others']) if r['others'] else ''
                lines.append(f"    {r['d100']}{extra}")

    # ── Settlement ──
    success_count = sum(1 for pa in parsed_attacks if pa['hit'])
    fail_count = len(parsed_attacks) - success_count
    lines.append('---')
    parts = [f"{sname} 检定成功 {success_count} 次"]
    if fail_count > 0:
        parts.append(f"失败 {fail_count} 次")
    lines.append('，'.join(parts))

    # Damage dealt to enemy — per-target summary with dice expression + calculation
    enemy_dmg_by_target = {}  # {target_name: [(dmg_dice, dmg_result, eff_dmg, target_hp), ...]}
    for i, e in enumerate(alive):
        pa = parsed_attacks[i]
        hdr = e.get('attack_header', '')
        target = '敌方目标'
        if '→' in hdr:
            target = hdr.rsplit('→', 1)[-1].strip()
        if target not in enemy_dmg_by_target:
            enemy_dmg_by_target[target] = []
        enemy_dmg_by_target[target].append((pa.get('dmg_dice', ''), pa.get('dmg_result', 0),
                                             pa.get('eff_dmg', 0), pa.get('target_hp', 0)))
    for tgt_name, dmg_list in enemy_dmg_by_target.items():
        dice_exprs = [d[0] for d in dmg_list if d[0]]
        if not dice_exprs:
            # Even without parsed dice, show total effective damage
            total_tgt_dmg = sum(d[2] for d in dmg_list)
            if total_tgt_dmg > 0:
                lines.append(f"{tgt_name}受到的伤害: {total_tgt_dmg} 点")
            elif success_count == 0:
                lines.append(f"{tgt_name}受到的伤害: 0 点（全部未命中）")
            continue
        all_dice = '+'.join(dice_exprs)
        results = '+'.join(str(d[1]) for d in dmg_list if d[0])
        total_tgt_dmg = sum(d[2] for d in dmg_list)
        # Show final HP after damage
        final_hp = dmg_list[-1][3] if dmg_list[-1][3] > 0 else None
        hp_text = f" → HP:{final_hp}" if final_hp else ""
        if total_tgt_dmg > 0:
            lines.append(f"{tgt_name}受到的伤害: {all_dice} = {results} = {total_tgt_dmg} 点{hp_text}")
        else:
            lines.append(f"{tgt_name}受到的伤害: {all_dice} = 0 点（未命中/被闪避）{hp_text}")

    # Damage taken by summons (from counter-attacks) — use structured data when available
    summon_dmg_entries = []
    for i, e in enumerate(alive):
        pa = parsed_attacks[i]
        hp_before = e['hp']
        # Use structured summon_hp if available
        if pa.get('summon_hp', 0) > 0:
            hp_after = pa['summon_hp']
        else:
            # Fallback: scan details for HP changes
            hp_after = hp_before
            details = e.get('attack_details', [])
            for ld in details:
                if 'HP:' in ld and 'D100' not in ld:
                    m = re.search(r'HP:\s*(-?\d+)', ld)
                    if m:
                        hp_after = max(0, int(m.group(1)))
        dmg_taken = hp_before - hp_after
        if dmg_taken > 0:
            summon_dmg_entries.append((pa['idx'], dmg_taken, hp_after, e['hp_max']))
    if summon_dmg_entries:
        lines.append('召唤物受到的伤害:')
        for idx, dmg, hp, hpmax in summon_dmg_entries:
            lines.append(f"  {sname}{idx}# 受到 {dmg} 点伤害 HP: {hp}/{hpmax}")

    return '\n'.join(lines)


def _run_ai_turns(engine, player_uid: str = None, Q=None, start_round: int = 0) -> list:
    """Advance battle through all AI turns until a human-controlled character is active or battle ends.
    If an AI attack triggers a reaction from a human player, stops and stores the
    pending reaction (caller must check _pending_reactions afterward).

    When player_uid is None, auto-detects all human players from engine._player_controllers.
    start_round: the last known round number before this function was called.
                 Round display is only emitted when current_round differs from this.
                 Default 0 ensures the first round display is always emitted.
    Returns list of output strings, one per AI action.
    """
    outputs = []
    # summon_buf: [{name, display_name, hp, hp_max, alive, round_header, enemy_text, attack_header, attack_details}]
    summon_buf = []

    def _flush_summons():
        nonlocal summon_buf
        if not summon_buf:
            return
        # Group by summon name
        groups = {}
        for entry in summon_buf:
            sname = entry['name']
            groups.setdefault(sname, []).append(entry)
        for sname, entries in groups.items():
            block = _format_summon_attack_block(sname, entries)
            if block:
                outputs.append(block)
        summon_buf = []

    max_steps = 50  # safety limit
    last_round = start_round

    from ai_trainer_pvp import parse_action, select_target_by_strategy, select_teammate_by_strategy

    def _end_summon_turn(e, uid):
        """End summon action: only advance turn when main actions are exhausted."""
        acts = e._get_my_actions(uid)
        if acts.get('主动', 0) > 0:
            e._consume_action(uid, '主动')
        if acts.get('主动', 0) <= 0:
            e._end_turn(uid)

    # Collect all human player IDs (from _player_controllers dict)
    human_ids = _get_all_human_player_ids(engine)
    # Also include explicit player_uid if provided (for backward compat)
    if player_uid:
        human_ids.add(player_uid)

    for _ in range(max_steps):
        state = engine._get_state()
        if not state or state.get('phase') != 'active':
            _debug_log(f"_run_ai_turns: BREAK phase={state.get('phase') if state else 'None'}")
            break

        il = engine._get_initiative()
        idx = state.get('activeIndex', 0)
        if idx >= len(il):
            _debug_log(f"_run_ai_turns: BREAK idx={idx} >= len(il)={len(il)}")
            break
        entry = il[idx]
        uid = entry['userId']
        base_uid = entry.get('baseUserId', uid)  # resolve __act → base for character operations

        # Check if a human player controls this character
        is_human = _is_human_controlled(engine, uid) or uid in human_ids
        if is_human:
            _flush_summons()
            _debug_log(f"_run_ai_turns: BREAK human turn — {entry.get('name', uid)} team={entry.get('team', '?')}")
            break

        # Round start display + Turn announcement (merged: 非/非)
        current_round = state.get('round', 1)
        _round_header = ''
        if current_round != last_round:
            last_round = current_round
            _round_header = '\n' + engine._get_initiative_display()
            # Merge zone summary into round header (never standalone)
            zone_summary = _format_zone_summary(engine)
            if zone_summary and zone_summary.strip():
                _round_header += '\n' + zone_summary.strip()
        is_summon = entry.get('isSummon', False)

        # Check HP first for summons (to capture HP for consolidated announcement)
        hp = engine._get_combat_hp(uid) or 0
        char_for_hp = engine.get_char(uid)
        hp_max = char_for_hp.get_attr('体力上限', hp) if char_for_hp else hp

        if is_summon:
            # Buffer summon turn info; don't output individual announcements
            _summon_enemy_text = _enemy_list_text(engine, uid)
            if hp <= 0:
                # Dead summon: check if group has alive members that can act instead
                alive_members = engine._get_alive_summon_group_members(uid) if hasattr(engine, '_get_alive_summon_group_members') else []
                if alive_members:
                    # Redirect to first alive member of the same summon group
                    uid = alive_members[0]
                    entry = next((e for e in il if e['userId'] == uid), entry)
                    hp = engine._get_combat_hp(uid) or 0
                    hp_max = (engine.get_char(uid) or char_for_hp).get_attr('体力上限', hp) if (engine.get_char(uid) or char_for_hp) else hp
                    # Fall through to alive summon processing below
                else:
                    # All group members dead: buffer and skip
                    engine._end_turn(uid)
                    summon_buf.append({
                        'name': entry.get('name', uid),
                        'display_name': entry.get('name', uid),
                        'hp': hp, 'hp_max': hp_max, 'alive': False,
                        'round_header': _round_header,
                        'enemy_text': _summon_enemy_text,
                        'attack_header': '已阵亡，跳过回合。', 'attack_details': [],
                    })
                    continue
            # Alive summon: capture announcement info, attack processing follows below
        else:
            _flush_summons()
            _turn_ann = _build_turn_announcement(engine, uid)
            outputs.append(_round_header + _turn_ann)
            if hp <= 0:
                engine._end_turn(uid)
                outputs.append(f"{entry.get('displayName', entry.get('name', uid))} 已阵亡，跳过回合。")
                continue

        # Summon: check .setaiact override first, then Q-table, else auto-attack
        if entry.get('isSummon'):
            # ── Compute summon index (1-based) for this owner ──
            owner_id = entry.get('ownerId', '')
            summon_key = None
            _global_summon_idx = 0
            if owner_id:
                owner_char = engine.get_char(owner_id)
                owner_serial = getattr(owner_char, 'serial', '') if owner_char else ''
                if owner_serial:
                    # Count how many summons of this owner appear before/at this one
                    _global_summon_idx = 0
                    for e2 in il:
                        if e2.get('isSummon') and e2.get('ownerId') == owner_id:
                            _global_summon_idx += 1
                            if e2['userId'] == uid:
                                break
                    summon_key = f"{owner_serial}-{_global_summon_idx}"
            # Display label: "生灵 9#" style index
            _summon_label = f"{entry.get('name', uid)} {_global_summon_idx}#" if _global_summon_idx > 0 else entry.get('name', uid)

            # ── Check .setaiact override for this summon ──
            sum_override = None
            if summon_key:
                sum_overrides = getattr(engine, '_ai_overrides', {})
                sum_override = sum_overrides.get(summon_key)

            if sum_override and 'main' in sum_override and sum_override['main']:
                # Use override to pick summon skill
                import random as _random_sov
                main_group = sum_override['main'][0]  # summons: only first and-group (simplified)
                # Filter available summon actions
                summon_skills = entry.get('skills', [])
                avail_sov = []
                for action_str, weight in main_group:
                    # action_str: e.g. "s0", "s1", "sN"
                    # Map to summon skill index
                    a_s = action_str.strip()
                    if a_s.startswith('s') and len(a_s) > 1:
                        try:
                            s_num = int(a_s[1:])
                            if 0 <= s_num < len(summon_skills):
                                w_s = weight if weight is not None else 1
                                avail_sov.append((s_num, w_s))
                        except ValueError:
                            pass
                    elif a_s.startswith('s0') or a_s == 's0':
                        # Basic summon attack (skill[0])
                        if len(summon_skills) > 0:
                            w_s = weight if weight is not None else 1
                            avail_sov.append((0, w_s))
                if avail_sov:
                    s_nums = [s for s, _ in avail_sov]
                    s_weights = [w for _, w in avail_sov]
                    chosen_si = _random_sov.choices(s_nums, weights=s_weights, k=1)[0]
                    # Build action key from summon actions
                    from ai_trainer_pvp import get_summon_actions, parse_action, select_target_by_strategy, execute_summon_action
                    sav = get_summon_actions(engine, uid)
                    # Find the matching summon action for chosen skill index
                    match_ak = None
                    match_an = None
                    for sak, san in sav:
                        if sak.startswith(f'SUMMON_SKILL_{chosen_si}') or sak.startswith(f'SUMMON_ZONE_{chosen_si}'):
                            match_ak, match_an = sak, san
                            break
                    if match_ak:
                        # Resolve target display name
                        my_team_s = entry.get('team', 'Y')
                        enemies_s = [e for e in il if e['team'] != my_team_s and (engine._get_combat_hp(e['userId']) or 0) > 0]
                        if match_an and '→' in match_an:
                            skill_part, _ = match_an.rsplit('→', 1)
                            _, target_strat_s = parse_action(match_ak)
                            target_id_s = select_target_by_strategy(engine, uid, target_strat_s, enemies_s)
                            if target_id_s:
                                tgt_entry = next((e for e in il if e['userId'] == target_id_s), None)
                                if tgt_entry:
                                    match_an = f"{skill_part}→{tgt_entry.get('name', target_id_s)}"
                        summon_lines_s = execute_summon_action(engine, uid, match_ak)
                        _end_summon_turn(engine, uid)
                        summon_buf.append({
                            'name': entry.get('name', uid),
                            'display_name': _summon_label,
                            'hp': hp, 'hp_max': hp_max, 'alive': True,
                            'round_header': _round_header, 'enemy_text': _summon_enemy_text,
                            'attack_header': f"【策略】: {match_an}",
                            'attack_details': summon_lines_s or [],
                        })
                        continue
                # If no override actions match, fall through to Q-table / auto-attack

            if Q and Q.get('summon'):
                summon_name = entry.get('name', uid)
                sdict = Q['summon'].get(summon_name, {})
                if sdict:
                    try:
                        from ai_trainer_pvp import encode_summon_state, get_summon_actions, execute_summon_action, select_target_by_strategy, parse_action
                        st = encode_summon_state(engine, uid)
                        av = get_summon_actions(engine, uid)
                        if av:
                            import random as _random, math
                            q_vals = [sdict.get((st, ak), 0.0) for ak, an in av]
                            zt = _random.randint(40, 80)
                            deform_prob = (100 - zt) / 100.0 * 0.4
                            if _random.random() < deform_prob:
                                scale = max(max(abs(q) for q in q_vals), 1.0)
                                q_vals = [q + _random.uniform(-deform_prob, deform_prob) * scale for q in q_vals]
                            if q_vals:
                                max_q = max(q_vals)
                                exp_vals = [math.exp((q - max_q) / 1.0) for q in q_vals]
                                total = sum(exp_vals)
                                probs = [e / total for e in exp_vals] if total > 0 else None
                                idx = _random.choices(range(len(av)), weights=probs, k=1)[0]
                                ak, an = av[idx]
                                # Resolve actual target name from strategy for display
                                my_team = entry.get('team', 'Y')
                                enemies = [e for e in il if e['team'] != my_team and (engine._get_combat_hp(e['userId']) or 0) > 0]
                                if '→' in an:
                                    skill_part, strat_label = an.rsplit('→', 1)
                                    _, target_strat = parse_action(ak)
                                    target_id = select_target_by_strategy(engine, uid, target_strat, enemies)
                                    if target_id:
                                        target_entry = next((e for e in il if e['userId'] == target_id), None)
                                        if target_entry:
                                            an = f"{skill_part}→{target_entry.get('name', target_id)}"
                                summon_lines = execute_summon_action(engine, uid, ak)
                                _end_summon_turn(engine, uid)
                                summon_buf.append({
                                    'name': entry.get('name', uid),
                                    'display_name': _summon_label,
                                    'hp': hp, 'hp_max': hp_max, 'alive': True,
                                    'round_header': _round_header, 'enemy_text': _summon_enemy_text,
                                    'attack_header': f"Q-行动: {an}",
                                    'attack_details': summon_lines or [],
                                })
                                continue
                    except Exception:
                        pass
            try:
                summon_lines = engine._summon_attack(uid)
            except ReactionNeeded as e:
                def_uid_rn = e.data.get('def_uid', '')
                if _is_human_controlled(engine, def_uid_rn):
                    e.data['battle_id'] = engine.group_id
                    _pending_reactions[engine.group_id] = [e.data]
                    _flush_summons()
                    return outputs
                else:
                    choice_rn = _resolve_ai_reaction_choice(engine, def_uid_rn, can_dodge=True, can_counter=True)
                    _, _, summon_lines = engine.resolve_reaction(e.data, choice_rn)
            _end_summon_turn(engine, uid)
            summon_buf.append({
                'name': entry.get('name', uid),
                'display_name': _summon_label,
                'hp': hp, 'hp_max': hp_max, 'alive': True,
                'round_header': _round_header, 'enemy_text': _summon_enemy_text,
                'attack_header': "自动攻击：" if summon_lines else "自动攻击。",
                'attack_details': summon_lines or [],
            })
            continue

        # AI decides and executes
        try:
            cmd, action_key = _ai_decide_action(engine, base_uid, Q)
        except Exception:
            cmd, action_key = '.s0', None

        if cmd == '.i end':
            engine._end_turn(uid)
            # Round/turn display will be generated at the start of the next iteration
        elif cmd.startswith('.a '):
            # ── Additional action (.a eat / .a give / .a m) ──
            sub = cmd[3:]
            my_acts = engine._get_my_actions(uid)
            if my_acts.get('附加', 0) <= 0:
                outputs.append(f"{entry.get('name', uid)} 附加动作已用尽，结束回合。")
                engine._end_turn(uid)
            elif sub.startswith('eat'):
                # .a eat [target_name] — use healing item
                target_id = None
                parts = sub.split()
                if len(parts) >= 2:
                    target_name = ' '.join(parts[1:])
                    target_entry = next((e for e in il if e.get('name', '') == target_name), None)
                    if target_entry:
                        target_id = target_entry['userId']
                result_text, success = engine._eat_cake(uid, target_id)
                outputs.append(result_text)
                engine._consume_action(uid, '附加')
            elif sub.startswith('give'):
                # Strategy-based teammate selection (Q-table) vs name-based (legacy)
                if action_key and action_key.startswith('GIVE_CAKE'):
                    base_gc, target_strat_gc = parse_action(action_key)
                    my_team_g = entry.get('team', 'Y')
                    teammates_give = [e for e in il if e['team'] == my_team_g
                                      and e['userId'] != uid
                                      and (engine._get_combat_hp(e['userId']) or 0) > 0]
                    if teammates_give:
                        tm_id = select_teammate_by_strategy(engine, uid, target_strat_gc, teammates_give)
                        if tm_id:
                            result_text, success = engine._eat_cake(uid, tm_id)
                            outputs.append(result_text)
                            engine._consume_action(uid, '附加')
                        else:
                            outputs.append(f"{entry.get('name', uid)} 无可给予的队友目标。")
                    else:
                        outputs.append(f"{entry.get('name', uid)} 没有存活的队友来给予蛋糕。")
                else:
                    # Backward compat: name-based target from command string
                    parts = sub.split()
                    if len(parts) >= 2:
                        target_name = ' '.join(parts[1:])
                        target_entry = next((e for e in il if e.get('name', '') == target_name), None)
                        if target_entry:
                            result_text, success = engine._eat_cake(uid, target_entry['userId'])
                            outputs.append(result_text)
                            engine._consume_action(uid, '附加')
                        else:
                            outputs.append(f"{entry.get('name', uid)} 找不到给予目标: {target_name}")
                    else:
                        outputs.append(f"{entry.get('name', uid)} 用法: .a give <目标名>")
            elif sub.startswith('m '):
                coord = sub[2:].upper()
                # Update map
                mp = engine._get_map()
                if mp:
                    for c, occ in list(mp.get('occupants', {}).items()):
                        if occ == uid:
                            del mp['occupants'][c]
                    mp['occupants'][coord] = uid
                    engine._set_map(mp)
                # Update coord on current entry
                entry['coord'] = coord
                # 同步同角色其他行动槽的坐标（多动角色共享位置）
                base_uid = entry.get('baseUserId', uid)
                for e in il:
                    if e['userId'] != uid and e.get('baseUserId', e['userId']) == base_uid:
                        e['coord'] = coord
                outputs.append(f"{entry.get('name', uid)} 移动至 {coord}")
                engine._consume_action(uid, '附加')
            elif sub == 'none':
                # .setaiact override: consume 1 附加 action slot silently
                if my_acts.get('附加', 0) <= 0:
                    outputs.append(f"{entry.get('name', uid)} 附加动作已用尽（策略: none），结束回合。")
                    engine._end_turn(uid)
                else:
                    engine._consume_action(uid, '附加')
                    outputs.append(f"{entry.get('name', uid)} 【策略】附加动作跳过（none）。")
            else:
                outputs.append(f"{entry.get('name', uid)} 未知附加动作: {sub}")
                engine._end_turn(uid)
        elif cmd.startswith('.s'):
            sn = int(cmd[2:]) if len(cmd) > 2 else 0
            if sn == 0:
                # basic attack
                bn, bv = engine.get_char(uid).get_best_melee()
                dd = engine._get_damage_dice(uid, bn)
                p = engine.get_char(uid).get_attr("伤害贯穿", 1)
                l = engine.get_char(uid).get_attr("致死骰", 1) or 0
                # Find nearest enemy
                enemies = [e for e in il if e.get("team") != entry.get("team", "Y")
                           and (engine._get_combat_hp(e["userId"]) or 0) > 0
                           and not engine._is_untargetable(e['userId'])]
                if enemies:
                    try:
                        # Resolve target via strategy (Q-table) or fallback to nearest
                        target_id = enemies[0]['userId']
                        if action_key:
                            # Check for .setaiact override action with target spec
                            if action_key.startswith('OVERRIDE:'):
                                ov_action = action_key.split(':', 1)[1]  # e.g. "s0 RP", "s0 Y1", "s0"
                                ov_parts = ov_action.split()
                                if len(ov_parts) >= 2:
                                    tgt_spec = ov_parts[1].upper()
                                    if tgt_spec == 'R':
                                        import random as _rnd_ai
                                        _rnd_ai.choice(enemies) if enemies else None
                                        target_id = _rnd_ai.choice(enemies)['userId'] if enemies else enemies[0]['userId']
                                    elif tgt_spec == 'RP':
                                        import random as _rnd_ai_rp
                                        candidates_rp = [e for e in enemies if not e.get('isSummon')]
                                        if candidates_rp:
                                            target_id = _rnd_ai_rp.choice(candidates_rp)['userId']
                                    else:
                                        # Serial-based target (e.g. "s0 Y1")
                                        for e in enemies:
                                            ec = engine.get_char(e.get('baseUserId', e['userId']))
                                            if ec and getattr(ec, 'serial', '') and ec.serial.upper() == tgt_spec:
                                                target_id = e['userId']
                                                break
                            else:
                                base_act, target_strat = parse_action(action_key)
                                if base_act in ('BASIC_ATTACK', 'MOVE_TOWARD'):
                                    tid = select_target_by_strategy(engine, uid, target_strat, enemies)
                                    if tid:
                                        target_id = tid
                        _, _, lines = engine._coc7_attack(uid, target_id, bn, bv, dd, p, l)
                        outputs.append("\n".join(lines))
                    except ReactionNeeded as e:
                        def_uid = e.data.get('def_uid', '')
                        # Check if defender is human (in human_ids or has controllers)
                        def_base = def_uid.split('__act')[0] if '__act' in str(def_uid) else def_uid
                        def_controllers = getattr(engine, '_player_controllers', {}).get(def_base, [])
                        is_human_defender = def_uid in human_ids or bool(def_controllers)
                        if is_human_defender:
                            # ── Check for auto-reaction preference ──
                            auto_react = getattr(engine, '_auto_react', {}).get(def_base)
                            if auto_react and auto_react.get('remaining', 0) > 0:
                                choice = auto_react['type']
                                # Validate availability for this attack
                                can_dodge = e.data.get('can_dodge', True)
                                can_counter = e.data.get('can_counter', True)
                                can_block = e.data.get('can_block', False)
                                is_valid = ((choice == 'dodge' and can_dodge) or
                                            (choice == 'counter' and can_counter) or
                                            (choice == 'block' and can_block))
                                if not is_valid:
                                    choice = 'counter' if can_counter else 'dodge' if can_dodge else 'dodge'
                                auto_react['remaining'] -= 1
                                if auto_react['remaining'] <= 0:
                                    engine._auto_react.pop(def_base, None)
                                # Show prefix lines + auto-react notice
                                def_restim = getattr(engine, '_restim_modes', {}).get(def_base, 0)
                                if def_restim == 1:
                                    outputs.append(f"{e.data['atk_name']} 对 {e.data['def_name']} 发起攻击！")
                                outputs.append('\n'.join(e.data['prefix_lines']))
                                _, _, lines = engine.resolve_reaction(e.data, choice)
                                outputs.append("\n".join(lines))
                                auto_label = '闪避' if choice == 'dodge' else '格挡' if choice == 'block' else '反击'
                                remaining_auto = engine._auto_react.get(def_base, {}).get('remaining', 0)
                                outputs.append(f"（自动反应：{auto_label}，剩余 {remaining_auto} 次）")
                                # Action consumed at line 2073 below (fall-through)
                            else:
                                # ── Human defender → store pending reaction, stop AI turns ──
                                e.data['battle_id'] = engine.group_id
                                e.data['target_uid'] = def_uid
                                # Look up defender's restim_mode (set via .setrestim)
                                def_restim = getattr(engine, '_restim_modes', {}).get(def_base, 0)
                                e.data['restim_mode'] = def_restim
                                _pending_reactions[engine.group_id] = e.data
                                # Consume action now (attack was committed); then return before line 2073
                                engine._consume_action(uid, '主动')
                                if def_restim == 1:
                                    # Delayed display: only show attack intent
                                    outputs.append(f"{e.data['atk_name']} 对 {e.data['def_name']} 发起攻击！")
                                else:
                                    outputs.append('\n'.join(e.data['prefix_lines']))
                                outputs.append(f"\n@{e.data['def_name']} 请做出反应：\n.e 闪避 / .e c 反击")
                                _flush_summons()
                                return outputs  # stop and let caller handle the pending reaction
                        else:
                            # ── AI defender → auto-resolve reaction (with .setaiact react override) ──
                            choice = _resolve_ai_reaction_choice(engine, e.data.get('def_uid', ''), can_dodge=True, can_counter=True)
                            _, _, lines = engine.resolve_reaction(e.data, choice)
                            outputs.append("\n".join(lines))
                    # Decrement main action after basic attack (only if attack was made)
                    engine._consume_action(uid, '主动')
                else:
                    # No reachable enemy targets — log and end turn
                    my_team = entry.get('team', 'Y')
                    all_teams = [(e.get('name', e.get('userId', '?')), e.get('team', '?')) for e in il]
                    _debug_log(f"AI .s0 NO ENEMIES: {entry.get('name', uid)}({uid[:30]}) team={my_team} 全体队伍分布: {all_teams}")
                    outputs.append(f"{entry.get('name', uid)} 找不到可攻击的敌方目标，结束回合。")
                    engine._end_turn(uid)
                    continue
            else:
                # Validate skill timing BEFORE calling _use_skill, so we don't
                # decrement actions when the Q-table picks an invalid skill.
                spells = engine.get_char(uid).spells or engine.load_spells(uid)
                spell = next((s for s in spells if s['index'] == sn), None)
                if not spell:
                    outputs.append(f"未找到技能{sn}")
                elif not has_timing(spell.get('时机', '2'), '2') and not has_timing(spell.get('时机', '2'), '1'):
                    # Skill cannot be used as a main action (timing-3 only e.g. 附加动作)
                    # → don't decrement action, try附加 action instead if possible
                    outputs.append(f"【{spell['name']}】不能在主动作阶段使用")
                    # If spell has timing 3 (附加动作), use附加 action instead
                    if has_timing(spell.get('时机', '2'), '3'):
                        my_acts = engine._get_my_actions(uid)
                        if my_acts.get('附加', 0) > 0:
                            out = engine._execute_spell(uid, engine._smart_target(uid, spell), spell)
                            if isinstance(out, str):
                                outputs.append(out)
                            engine._consume_action(uid, '附加')
                else:
                    # Resolve target: use strategy from Q-table, fall back to _smart_target
                    if action_key and action_key.startswith('OVERRIDE:'):
                        # .setaiact override: check for target spec in action string
                        ov_action_sp = action_key.split(':', 1)[1]  # e.g. "s3 RP", "s3 Y1", "s3"
                        ov_parts_sp = ov_action_sp.split()
                        if len(ov_parts_sp) >= 2:
                            tgt_spec_sp = ov_parts_sp[1].upper()
                            if tgt_spec_sp in ('R', 'RP'):
                                target_id = engine._pick_random_target(uid, include_summons=(tgt_spec_sp == 'R'))
                                if not target_id:
                                    target_id = engine._smart_target(uid, spell)
                            else:
                                # Serial-based target (e.g. "s3 Y1")
                                target_id = engine._smart_target(uid, spell)  # fallback
                                il5 = engine._get_initiative()
                                mt5 = entry.get('team', 'Y')
                                for e5 in il5:
                                    if e5.get('team') == mt5: continue
                                    if (engine._get_combat_hp(e5['userId']) or 0) <= 0: continue
                                    ec5 = engine.get_char(e5.get('baseUserId', e5['userId']))
                                    if ec5 and getattr(ec5, 'serial', '') and ec5.serial.upper() == tgt_spec_sp:
                                        target_id = e5['userId']
                                        break
                        else:
                            target_id = engine._smart_target(uid, spell)
                    elif action_key:
                        base_act_sk, target_strat_sk = parse_action(action_key)
                        if base_act_sk.startswith('SKILL_'):
                            enemies_sk = [e for e in il if e.get('team') != entry.get('team', 'Y')
                                          and (engine._get_combat_hp(e['userId']) or 0) > 0
                                          and not engine._is_untargetable(e['userId'])]
                            if enemies_sk:
                                tid = select_target_by_strategy(engine, uid, target_strat_sk, enemies_sk)
                                target_id = tid if tid else engine._smart_target(uid, spell)
                            else:
                                target_id = engine._smart_target(uid, spell)
                        else:
                            target_id = engine._smart_target(uid, spell)
                    else:
                        target_id = engine._smart_target(uid, spell)
                    try:
                        out = engine._execute_spell(uid, target_id, spell)
                    except ReactionNeeded as e:
                        def_uid_rn = e.data.get('def_uid', '')
                        def_base_rn = def_uid_rn.split('__act')[0] if '__act' in str(def_uid_rn) else def_uid_rn
                        def_controllers_rn = getattr(engine, '_player_controllers', {}).get(def_base_rn, [])
                        is_human_defender_rn = def_uid_rn in human_ids or bool(def_controllers_rn)

                        if is_human_defender_rn:
                            # -- Check for auto-reaction preference --
                            auto_react_rn = getattr(engine, '_auto_react', {}).get(def_base_rn)
                            if auto_react_rn and auto_react_rn.get('remaining', 0) > 0:
                                choice_rn = auto_react_rn['type']
                                can_dodge_rn2 = e.data.get('can_dodge', True)
                                can_counter_rn2 = e.data.get('can_counter', True)
                                can_block_rn2 = e.data.get('can_block', False)
                                is_valid_rn = ((choice_rn == 'dodge' and can_dodge_rn2) or
                                               (choice_rn == 'counter' and can_counter_rn2) or
                                               (choice_rn == 'block' and can_block_rn2))
                                if not is_valid_rn:
                                    choice_rn = 'counter' if can_counter_rn2 else 'dodge' if can_dodge_rn2 else 'dodge'
                                auto_react_rn['remaining'] -= 1
                                if auto_react_rn['remaining'] <= 0:
                                    engine._auto_react.pop(def_base_rn, None)
                                def_restim_rn2 = getattr(engine, '_restim_modes', {}).get(def_base_rn, 0)
                                if def_restim_rn2 == 1:
                                    spell_name_rn2 = e.data.get('skill_name', '技能')
                                    outputs.append(f"{e.data['atk_name']} 释放【{spell_name_rn2}】→ {e.data['def_name']}！")
                                outputs.append('\n'.join(e.data.get('prefix_lines', [])))
                                dodged_rn, countered_rn, lines_rn = engine.resolve_spell_reaction(e.data, choice_rn)
                                outputs.append('\n'.join(lines_rn))
                                auto_label_rn = '闪避' if choice_rn == 'dodge' else '格挡' if choice_rn == 'block' else '反击'
                                remaining_rn = engine._auto_react.get(def_base_rn, {}).get('remaining', 0)
                                outputs.append(f"（自动反应：{auto_label_rn}，剩余 {remaining_rn} 次）")
                                out = ''
                            else:
                                e.data['battle_id'] = engine.group_id
                                e.data['target_uid'] = def_uid_rn
                                def_restim_rn = getattr(engine, '_restim_modes', {}).get(def_base_rn, 0)
                                e.data['restim_mode'] = def_restim_rn
                                _pending_reactions[engine.group_id] = e.data
                                # Consume action now (attack was committed)
                                engine._consume_action(uid, '主动')
                                if def_restim_rn == 1:
                                    spell_name_rn = e.data.get('skill_name', '技能')
                                    outputs.append(f"{e.data['atk_name']} 释放【{spell_name_rn}】→ {e.data['def_name']}！")
                                else:
                                    outputs.append('\n'.join(e.data.get('prefix_lines', [])))
                                opts_rn = []
                                if e.data.get('can_dodge', True):
                                    opts_rn.append('.e 闪避 / .e d')
                                if e.data.get('can_counter', True):
                                    opts_rn.append('.e 反击 / .e c')
                                react_prompt_rn = '\n' + '  '.join(opts_rn)
                                outputs.append(f"\n@{e.data['def_name']} 请做出反应：\n{react_prompt_rn}")
                                _flush_summons()
                                return outputs
                        else:
                            # AI defender → auto-resolve spell reaction (with .setaiact react override)
                            def_uid_rn = e.data.get('def_uid', '')
                            can_dodge_rn = e.data.get('can_dodge', True)
                            can_counter_rn = e.data.get('can_counter', True)
                            can_block_rn = e.data.get('can_block', False)
                            choice_rn = _resolve_ai_reaction_choice(engine, def_uid_rn, can_dodge_rn, can_counter_rn, can_block_rn)
                            dodged_rn, countered_rn, lines_rn = engine.resolve_spell_reaction(e.data, choice_rn)
                            outputs.append('\n'.join(lines_rn))
                        out = ''
                    if isinstance(out, str) and out:
                        outputs.append(out)
                    is_passive_only = has_timing(spell.get('时机', '2'), '1') and not has_timing(spell.get('时机', '2'), '2')
                    if not is_passive_only:
                        engine._consume_action(uid, '主动')
        else:
            out = engine._end_turn(uid)
            if isinstance(out, str):
                outputs.append(out)

        # After action, check if turn should end
        my_acts = engine._get_my_actions(uid)
        if my_acts.get('主动', 0) <= 0:
            try:
                engine._end_turn(uid)
            except Exception:
                pass

    _flush_summons()
    return outputs


# ═══════════════════════════════════════════════════════════════
#  Initiative & target numbering helpers
# ============================================================
#  All characters in initiative order are assigned a global 1-based
#  number.  Targeting uses this number (e.g. .s0 3 = attack #3).
# ============================================================

def _build_global_list(engine) -> list:
    """Return ALL living characters in initiative order, each with a global index.

    Deduplicates multi-action slots (by baseUserId) and skips suppressed entries.
    Each entry: {index, userId, name, serial, team, hp, hpMax, mp, mpMax, san,
                 isSummon, initRoll, dex, isActive}
    """
    import re
    il = engine._get_initiative()
    state = engine._get_state()
    active_idx = state.get('activeIndex', 0) if state else 0
    # Determine which base character is currently active
    active_base = None
    if 0 <= active_idx < len(il):
        active_base = il[active_idx].get('baseUserId', il[active_idx]['userId'])

    result = []
    for i, e in enumerate(il):
        # Skip suppressed entries (inactive dynamic action slots)
        if e.get('_suppressed', False):
            continue
        uid = e['userId']
        base = e.get('baseUserId', uid)
        hp = engine._get_combat_hp(uid)
        if hp is None:
            continue
        if hp <= 0 and not engine._is_dying(uid):
            continue
        char = engine.get_char(uid)
        # Strip " (行动N)" suffix from display name
        display_name = e.get('name', uid)
        display_name = re.sub(r'\s*\(行动\d+\)\s*', '', display_name)
        result.append({
            'index': len(result) + 1,
            'origIndex': i,
            'userId': uid,
            'name': display_name,
            'serial': getattr(char, 'serial', '') or e.get('serial', ''),
            'team': e.get('team', '?'),
            'hp': hp,
            'hpMax': char.get_attr('体力上限', hp) if char else hp,
            'mp': char.get_attr('魔力', 0) if char else 0,
            'mpMax': char.get_attr('魔力上限', 0) if char else 0,
            'san': char.get_attr('理智', 50) if char else 50,
            'isSummon': e.get('isSummon', False),
            'initRoll': e.get('initRoll', 0),
            'dex': e.get('dex', 50),
            'isActive': (base == active_base),
            'shield_hp': e.get('shield_block_hp', 0) or 0,
        })
    return result


def _fmt_char_name(engine, entry: dict) -> str:
    """Format a character as 【序号】名称.  Falls back to bare name if no serial."""
    import re
    uid = entry.get('userId', '')
    if uid:
        char = engine.get_char(uid)
        serial = getattr(char, 'serial', '') if char else ''
        if serial:
            display_name = re.sub(r'\s*\(行动\d+\)\s*', '', entry.get('name', uid))
            return f"【{serial}】{display_name}"
    display_name = re.sub(r'\s*\(行动\d+\)\s*', '', entry.get('name', '?'))
    return display_name


def _initiative_list_text(engine) -> str:
    """Formatted initiative order with global numbers (all characters).

    Uses engine.team_label_mode to decide team tag style:
      'neutral' → [Y队]/[X队]   (multi_pvp)
      'friendly' → [己方]/[敌方]  (PvE, legacy pvp)
    """
    entries = _build_global_list(engine)
    if not entries:
        return '\n（无存活角色）'
    lines = ['\n── 先攻顺序 ──']
    label_mode = getattr(engine, 'team_label_mode', 'friendly')
    _debug_log(f"_initiative_list_text: label_mode={label_mode!r} "
          f"teams={[(e['name'], e['team']) for e in entries]}")
    for e in entries:
        if label_mode == 'neutral':
            team_tag = '[Y队]' if e['team'] == 'Y' else '[X队]'
        else:
            team_tag = '[己方]' if e['team'] == 'Y' else '[敌方]'
        summon_tag = ' [召唤]' if e['isSummon'] else ''
        active_mark = ' ◀当前行动' if e['isActive'] else ''
        serial = e.get('serial', '')
        name_part = f"【{serial}】{e['name']}" if serial else e['name']
        lines.append(
            f"  [{e['index']}] {team_tag} {name_part}{summon_tag}  "
            f"HP:{e['hp']}/{e['hpMax']}  先攻:{e['initRoll']}{active_mark}"
        )
    return '\n'.join(lines)


def _ally_list_text(engine, player_uid: str) -> str:
    """Ally list (same team, excluding self) with HP/MP/shield."""
    entries = _build_global_list(engine)
    player_entry = next((e for e in engine._get_initiative() if e['userId'] == player_uid), None)
    player_team = player_entry.get('team', 'Y') if player_entry else 'Y'
    allies = [e for e in entries if e['team'] == player_team and e['userId'] != player_uid]
    if not allies:
        return ''
    lines = ['\n── 友方目标 ──']
    for e in allies:
        tag = ' [召唤]' if e['isSummon'] else ''
        serial = e.get('serial', '')
        name_part = f"【{serial}】{e['name']}" if serial else e['name']
        mp_str = f"  MP:{e.get('mp', '?')}/{e.get('mpMax', '?')}" if e.get('mp') is not None else ''
        shield = e.get('shield_hp', 0)
        shield_str = f'  🛡{shield}' if shield > 0 else ''
        lines.append(f"  [{e['index']}] {name_part}{tag}  HP:{e['hp']}/{e['hpMax']}{mp_str}{shield_str}")
    return '\n'.join(lines)


def _enemy_list_text(engine, player_uid: str) -> str:
    """Enemy-only list using the same global initiative numbers.
    Filters out untargetable characters (不可指定) and shows MP/shield."""
    entries = _build_global_list(engine)
    player_entry = next((e for e in engine._get_initiative() if e['userId'] == player_uid), None)
    player_team = player_entry.get('team', 'Y') if player_entry else 'Y'
    # Filter: different team AND not untargetable
    enemies = [e for e in entries if e['team'] != player_team
               and not engine._is_untargetable(e['userId'])]
    untargetable = [e for e in entries if e['team'] != player_team
                    and engine._is_untargetable(e['userId'])]
    # Use neutral labels ('敌方' vs '对方') for multi_pvp; friendly for PvE
    label_mode = getattr(engine, 'team_label_mode', 'friendly')
    enemy_label = '对方' if label_mode == 'neutral' else '敌方'
    lines = []
    if not enemies and not untargetable:
        return f'\n── {enemy_label}已全部阵亡 ──'
    if enemies:
        lines.append(f'\n── {enemy_label}目标 ──')
        for e in enemies:
            tag = ' [召唤]' if e['isSummon'] else ''
            serial = e.get('serial', '')
            name_part = f"【{serial}】{e['name']}" if serial else e['name']
            mp_str = f"  MP:{e.get('mp', '?')}/{e.get('mpMax', '?')}" if e.get('mp') is not None else ''
            shield = e.get('shield_hp', 0)
            shield_str = f'  🛡{shield}' if shield > 0 else ''
            lines.append(f"  [{e['index']}] {name_part}{tag}  HP:{e['hp']}/{e['hpMax']}{mp_str}{shield_str}")
    if untargetable:
        lines.append('\n── 不可选中 ──')
        for e in untargetable:
            serial = e.get('serial', '')
            name_part = f"【{serial}】{e['name']}" if serial else e['name']
            lines.append(f"  [{e['index']}] {name_part} （召唤物存活中，无法指定为目标）")
    return '\n'.join(lines)


def _build_turn_announcement(engine, uid: str) -> str:
    """Generate '现在是xxx的回合了' announcement with HP/MP, actions, and enemies.

    For human-controlled characters: includes full action list + enemy targets.
    For AI characters: brief HP/MP summary only.
    """
    entry = next((e for e in engine._get_initiative() if e['userId'] == uid), None)
    if not entry:
        return ''
    char = engine.get_char(uid)
    my_acts = engine._get_my_actions(uid)

    hp = engine._get_combat_hp(uid) or 0
    hp_max = char.get_attr('体力上限', hp)
    mp = char.get_attr('魔力', 0) or 0
    mp_max = char.get_attr('魔力上限', mp)

    lines = []
    lines.append(f"现在是{_fmt_char_name(engine, entry)}的回合了。")
    lines.append(f"HP: {hp}/{hp_max}  MP: {mp}/{mp_max}")

    is_human = _is_human_controlled(engine, uid)
    if is_human:
        # ── Full action list for human players ──
        base = uid.split('__act')[0] if '__act' in str(uid) else uid
        controllers = getattr(engine, '_player_controllers', {}).get(base, [])
        if controllers:
            lines.append(f"控制者: {'/'.join(controllers)}")

        lines.append('')
        lines.append('── 可用行动 ──')

        # Main actions
        main_remaining = my_acts.get('主动', 0)
        lines.append(f"主动作（剩余 {main_remaining}）:")
        bn, bv = char.get_best_melee()
        if bv > 0:
            lines.append(f"  .s0 基本攻击 [{bn}={bv}]")
        spells = char.spells or engine.load_spells(uid)
        for s in spells:
            timing = s.get('时机', '2')
            if has_timing(timing, '2'):
                mp_cost = sum(int(e.get('消耗mp', 0) or 0) for e in s.get('effects', []))
                mp_str = f"  MP:{mp_cost}" if mp_cost > 0 else ""
                lines.append(f"  .s{s['index']} {s['name']}{mp_str}")

        # Additional actions
        extra_remaining = my_acts.get('附加', 0)
        lines.append(f"附加动作（剩余 {extra_remaining}）:")
        lines.append(f"  .a m <坐标> — 移动")
        for s in spells:
            timing = s.get('时机', '2')
            if has_timing(timing, '3') and not has_timing(timing, '2'):
                lines.append(f"  .a s{s['index']} {s['name']}")
        if engine._has_healing_item(uid) if hasattr(engine, '_has_healing_item') else False:
            lines.append(f"  .a eat [目标]")

        # Other
        lines.append(f"其他:")
        lines.append(f"  .i end — 结束回合")

    # Ally list (before enemy list)
    ally_text = _ally_list_text(engine, uid)
    if ally_text:
        lines.append(ally_text)

    # Enemy list (always show for both human and AI)
    enemy_text = _enemy_list_text(engine, uid)
    if enemy_text:
        lines.append(enemy_text)

    # For AI characters: insert page break after enemy list, before action result.
    # This separates the "before action" info (HP/MP + enemy list) from the action execution.
    if not is_human:
        lines.append('【分页符】')

    return '\n'.join(lines)


def _resolve_global_index(engine, idx: int) -> str | None:
    """Resolve a global initiative index to a userId. Returns None if invalid."""
    entries = _build_global_list(engine)
    for e in entries:
        if e['index'] == idx:
            return e['userId']
    return None


def _resolve_enemy_index(engine, player_uid: str, idx: int) -> str | None:
    """Resolve a global index to an enemy userId (validates team mismatch)."""
    uid = _resolve_global_index(engine, idx)
    if not uid:
        return None
    il = engine._get_initiative()
    player_entry = next((e for e in il if e['userId'] == player_uid), None)
    player_team = player_entry.get('team', 'Y') if player_entry else 'Y'
    target_entry = next((e for e in il if e['userId'] == uid), None)
    if target_entry and target_entry.get('team') != player_team and not engine._is_untargetable(uid):
        return uid
    return None  # same team or untargetable → not a valid enemy target


def _parse_target_index(args: str) -> int | None:
    """Extract a numeric target index from command args (e.g. '1', ' 2 ')."""
    if not args:
        return None
    for part in args.strip().split():
        try:
            return int(part)
        except ValueError:
            pass
    return None


def _resolve_target_by_serial(engine, player_uid: str, args: str) -> str | None:
    """Resolve a target from args by serial number (e.g. 'Y4', 'Y5').

    Searches the initiative list for a character whose serial (or name) matches
    the first token in args. Only returns enemies (different team from player_uid).
    Returns userId if found, None otherwise.
    """
    if not args:
        return None
    token = args.strip().split()[0].upper()
    il = engine._get_initiative()
    player_entry = next((e for e in il if e['userId'] == player_uid), None)
    player_team = player_entry.get('team', 'Y') if player_entry else 'Y'
    for e in il:
        if e.get('team') == player_team:
            continue  # skip same-team
        if (engine._get_combat_hp(e['userId']) or 0) <= 0:
            continue  # skip dead
        if engine._is_untargetable(e['userId']):
            continue  # skip untargetable
        # Match by serial (stored in initiative entry)
        if e.get('serial', '').upper() == token:
            return e['userId']
        # Match by name (case-insensitive)
        if e.get('name', '').upper() == token:
            return e['userId']
    return None


# ═══════════════════════════════════════════════════════════════
#  API Endpoints
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
#  Team CRUD (.bteam)
# ═══════════════════════════════════════════════════════════════

@app.route('/api/pvp/team/create', methods=['POST'])
def team_create():
    """Create a fixed team for later use in .btast T1 vs T2."""
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({'error': True, 'message': '请求数据格式错误'}), 400
    player_id = data.get('player_id', '')
    name = data.get('name', '').strip()
    members = data.get('members', [])

    if not name:
        return jsonify({'error': True, 'message': '战队名称不能为空'}), 400
    if not members or len(members) < 1:
        return jsonify({'error': True, 'message': '战队至少需要1名成员'}), 400

    # Validate serials against chData
    valid_serials = {c.get('serial', '') for c in characters_data_pvp.ALL_CHARACTERS}
    for s in members:
        if s.upper() not in valid_serials:
            return jsonify({'error': True, 'message': f'无效角色序号: {s}'}), 400

    with _teams_lock:
        global _next_team_id
        team_id = f"T{_next_team_id}"
        _next_team_id += 1
        _teams[team_id] = {
            'name': name,
            'members': [s.upper() for s in members],
            'creator': player_id,
            'created_at': time.time(),
        }

    return jsonify({
        'team_id': team_id,
        'name': name,
        'members': _teams[team_id]['members'],
    })


@app.route('/api/pvp/team/list', methods=['GET'])
def team_list():
    """List all fixed teams."""
    with _teams_lock:
        teams = [{'id': tid, 'name': t['name'], 'members': t['members'], 'creator': t['creator']}
                 for tid, t in _teams.items()]
    return jsonify({'teams': teams})


@app.route('/api/pvp/team/delete', methods=['POST'])
def team_delete():
    """Delete a fixed team (creator only)."""
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({'error': True, 'message': '请求数据格式错误'}), 400
    player_id = data.get('player_id', '')
    team_id = data.get('team_id', '').upper()

    with _teams_lock:
        if team_id not in _teams:
            return jsonify({'error': True, 'message': f'战队 {team_id} 不存在'}), 404
        team = _teams[team_id]
        if team['creator'] != player_id:
            return jsonify({'error': True, 'message': f'只有战队创建者可以删除 {team_id}'}), 403
        del _teams[team_id]

    return jsonify({'status': 'ok', 'message': f'战队 {team_id}（{team["name"]}）已删除'})


# ── Resolve team aliases ──

def _resolve_team_tokens(tokens: list) -> list:
    """Resolve a list of tokens (serials like 'Y1' or team aliases like 'T1')
    into a flat list of serials."""
    result = []
    with _teams_lock:
        for token in tokens:
            t = token.strip().upper()
            if t in _teams:
                result.extend(_teams[t]['members'])
            else:
                result.append(t)
    return result


@app.route('/api/pvp/ping', methods=['GET'])
def ping():
    return jsonify({'status': 'ok', 'service': 'autocombat PvP engine'})

@app.route('/api/pvp/create', methods=['POST'])
def create_battle():
    """Initialize a new PvP or PvE battle."""
    try:
        data = request.get_json(force=True)
    except Exception as e:
        app.logger.error(f'JSON parse error: {e}')
        return jsonify({'error': True, 'message': '请求数据格式错误'}), 400
    if not data:
        return jsonify({'error': True, 'message': '无效的请求数据'}), 400

    group_id = data.get('group_id', str(uuid.uuid4())[:8])
    mode = data.get('mode', 'pvp')  # 'pvp' or 'pve' or 'multi_pvp'

    _debug_log(f'=== /api/pvp/create ENTRY === group={group_id} mode={mode!r}')
    _debug_log(f'  request keys: {sorted(data.keys())}')
    if mode == 'multi_pvp':
        _debug_log(f'  team_a={data.get("team_a", [])} team_b={data.get("team_b", [])}')
        _debug_log(f'  player_bindings keys={list(data.get("player_bindings", {}).keys())}')
    if data.get('characters'):
        _debug_log(f'  js_characters uids={list(data["characters"].keys())}')

    with _lock:
        if group_id in _battles:
            # Auto-stop old battle to allow new one
            old_engine = _battles.pop(group_id, None)
            _pending_reactions.pop(group_id, None)
            _debug_log(f"Auto-stopped old battle for group={group_id}")

        engine = PvPFullBattleEngine()
        engine.group_id = group_id
        engine.team_label_mode = 'neutral' if mode == 'multi_pvp' else 'friendly'
        engine.use_dying_system = USE_DYING_SYSTEM  # apply backend config toggle
        all_uids = []

        # ── Load characters ──
        js_characters = data.get('characters', {})
        for uid, js_data in js_characters.items():
            merge_character(engine, uid, js_data)
            all_uids.append(uid)

        # ── PvE mode: self (from chData) + allies vs opponents ──
        human_uid = None  # which uid the human player controls
        if mode == 'pve':
            opponent_serials = data.get('opponent_serials', [])
            self_serial = data.get('self_serial', '')
            ally_serials = data.get('ally_serials', [])
            player_uid = data.get('player_uid', '')

            from characters_data_pvp import load_character_to_engine

            # ── Load self character entirely from chData (the human player) ──
            self_data = _find_chdata_by_serial(self_serial)
            if not self_data:
                return jsonify({'error': True, 'message': f'未找到角色: {self_serial}'}), 400
            self_uid = player_uid or f"self_{self_serial}_{random.randint(1000, 9999)}"
            load_character_to_engine(engine, self_data, self_uid)
            engine.get_char(self_uid).name = self_data.get('name', self_serial)
            if not self_data.get('pre_transformed'):
                engine.process_command(self_uid, '.hs')
            all_uids.append(self_uid)
            human_uid = self_uid

            # ── Load ally characters (from chData) ──
            for a_serial in ally_serials:
                a_data = _find_chdata_by_serial(a_serial)
                if a_data:
                    a_uid = f"ally_{a_serial}_{random.randint(1000, 9999)}"
                    load_character_to_engine(engine, a_data, a_uid)
                    engine.get_char(a_uid).name = a_data.get('name', 'AI队友')
                    if not a_data.get('pre_transformed'):
                        engine.process_command(a_uid, '.hs')
                    all_uids.append(a_uid)

            # ── Load opponent characters from specified serials (or random if empty) ──
            if opponent_serials:
                for o_serial in opponent_serials:
                    o_data = _find_chdata_by_serial(o_serial)
                    if o_data:
                        o_uid = f"opp_{o_serial}_{random.randint(1000, 9999)}"
                        load_character_to_engine(engine, o_data, o_uid)
                        engine.get_char(o_uid).name = o_data.get('name', o_serial)
                        if not o_data.get('pre_transformed'):
                            engine.process_command(o_uid, '.hs')
                        all_uids.append(o_uid)
            else:
                # No opponents specified → generate random opponents (same size as player team)
                player_team_size = 1 + len(ally_serials)  # self + allies
                team_serials = {self_serial} | set(ally_serials)
                ai_candidates = [c for c in characters_data_pvp.ALL_CHARACTERS
                                 if c.get('serial', '') not in team_serials]
                if len(ai_candidates) < player_team_size:
                    team_prefixes = {s[0] for s in team_serials if s}
                    ai_candidates = [c for c in characters_data_pvp.ALL_CHARACTERS
                                     if c.get('serial', '')[:1] not in team_prefixes]
                random.shuffle(ai_candidates)
                for i in range(min(player_team_size, len(ai_candidates))):
                    ai_data = ai_candidates[i]
                    ai_uid = f"opp_{ai_data.get('serial', '')}_{random.randint(1000, 9999)}"
                    load_character_to_engine(engine, ai_data, ai_uid)
                    engine.get_char(ai_uid).name = ai_data.get('name', 'AI对手')
                    engine.get_char(ai_uid).serial = ai_data.get('serial', '')
                    if not ai_data.get('pre_transformed'):
                        engine.process_command(ai_uid, '.hs')
                    all_uids.append(ai_uid)

            engine._human_uid = human_uid  # deprecated: kept for backward compat, prefer _player_controllers
            # Register human player via unified _player_controllers
            if not hasattr(engine, '_player_controllers'):
                engine._player_controllers = {}
            if not hasattr(engine, '_ai_overrides'):
                engine._ai_overrides = {}
            if not hasattr(engine, '_ai_override_state'):
                engine._ai_override_state = {}
            engine._player_controllers[human_uid] = [player_uid]

        # ── Multi-PvP mode: team_a vs team_b with optional player bindings ──
        if mode == 'multi_pvp':
            from characters_data_pvp import load_character_to_engine

            team_a_serials = data.get('team_a', [])
            team_b_serials = data.get('team_b', [])
            player_bindings = data.get('player_bindings', {})

            if not team_a_serials or not team_b_serials:
                return jsonify({'error': True, 'message': 'multi_pvp模式需要 team_a 和 team_b'}), 400

            # Clear existing characters (from js_characters merge above, if any)
            engine.characters.clear()
            all_uids.clear()

            # Initialize player controllers
            if not hasattr(engine, '_player_controllers'):
                engine._player_controllers = {}
            if not hasattr(engine, '_ai_overrides'):
                engine._ai_overrides = {}
            if not hasattr(engine, '_ai_override_state'):
                engine._ai_override_state = {}

            # ── Load team_a characters (Team Y) ──
            for serial in team_a_serials:
                cdata = _find_chdata_by_serial(serial.upper())
                if not cdata:
                    return jsonify({'error': True, 'message': f'未找到角色: {serial}'}), 400
                uid = f"y_{serial}_{random.randint(1000, 9999)}"
                load_character_to_engine(engine, cdata, uid)
                engine.get_char(uid).name = cdata.get('name', serial)
                engine.get_char(uid).serial = serial.upper()
                if not cdata.get('pre_transformed'):
                    engine.process_command(uid, '.hs')
                all_uids.append(uid)

            # ── Load team_b characters (Team X) ──
            for serial in team_b_serials:
                cdata = _find_chdata_by_serial(serial.upper())
                if not cdata:
                    return jsonify({'error': True, 'message': f'未找到角色: {serial}'}), 400
                uid = f"x_{serial}_{random.randint(1000, 9999)}"
                load_character_to_engine(engine, cdata, uid)
                engine.get_char(uid).name = cdata.get('name', serial)
                engine.get_char(uid).serial = serial.upper()
                if not cdata.get('pre_transformed'):
                    engine.process_command(uid, '.hs')
                all_uids.append(uid)

            # ── Set player bindings ──
            for player_id, serials in player_bindings.items():
                if not isinstance(serials, list):
                    serials = [serials]
                for serial in serials:
                    target_uid = None
                    for uid in all_uids:
                        char = engine.get_char(uid)
                        if char.serial and char.serial.upper() == serial.upper():
                            target_uid = uid
                            break
                    if target_uid:
                        base_uid = target_uid.split('__act')[0] if '__act' in str(target_uid) else target_uid
                        if base_uid not in engine._player_controllers:
                            engine._player_controllers[base_uid] = []
                        if player_id not in engine._player_controllers[base_uid]:
                            engine._player_controllers[base_uid].append(player_id)

            # Get map_size
            map_size_str = data.get('map_size', '10x10')
            try:
                w_str, h_str = map_size_str.split('x')
                w, h = int(w_str), int(h_str)
            except Exception:
                w, h = 10, 10
            map_data = {'width': w, 'height': h, 'entryRow': math.ceil(h / 2),
                        'obstacles': {}, 'occupants': {}}

            # ── Setup initiative ──
            engine._set_map(map_data)
            team_a_uids = [u for u in all_uids if u.startswith('y_')]
            team_b_uids = [u for u in all_uids if u.startswith('x_')]
            il = []
            for i, uid in enumerate(team_a_uids):
                char = engine.get_char(uid)
                for ai in range(MAX_DYNAMIC_ACTIONS):
                    entry_id = uid if ai == 0 else f"{uid}__act{ai}"
                    row = min(h - 1, math.ceil(h / 2) + i - len(team_a_uids) // 2)
                    coord = format_coord(1, row)
                    label = f" (行动{ai+1})"
                    dex_val = char.get_attr('敏捷', 50)
                    init_roll = random.randint(1, 100)
                    il.append({'userId': entry_id, 'baseUserId': uid,
                               'name': char.name + label, 'actionIdx': ai, 'team': 'Y',
                               'dex': dex_val, 'initRoll': init_roll,
                               'initRank': success_rank(init_roll, dex_val),
                               'coord': coord, 'serial': char.serial})
                    if ai == 0:
                        map_data['occupants'][coord] = uid
            for i, uid in enumerate(team_b_uids):
                char = engine.get_char(uid)
                for ai in range(MAX_DYNAMIC_ACTIONS):
                    entry_id = uid if ai == 0 else f"{uid}__act{ai}"
                    row = min(h - 1, math.ceil(h / 2) + i - len(team_b_uids) // 2)
                    coord = format_coord(w - 2, row)
                    label = f" (行动{ai+1})"
                    dex_val = char.get_attr('敏捷', 50)
                    init_roll = random.randint(1, 100)
                    il.append({'userId': entry_id, 'baseUserId': uid,
                               'name': char.name + label, 'actionIdx': ai, 'team': 'X',
                               'dex': dex_val, 'initRoll': init_roll,
                               'initRank': success_rank(init_roll, dex_val),
                               'coord': coord, 'serial': char.serial})
                    if ai == 0:
                        map_data['occupants'][coord] = uid
            il.sort(key=lambda e: (-e['initRank'], -e['dex'], e['initRoll']))
            engine._set_map(map_data)
            engine._set_initiative(il)
            # 初始化动态行动槽抑制状态（multi_pvp模式）
            for uid in all_uids:
                engine._sync_initiative_slots(uid)
            # 施放被动技能（timing=1），然后重新同步（buff可能改变行动力）
            for uid in all_uids:
                spells = engine.get_char(uid).spells or engine.load_spells(uid)
                for s in spells:
                    if has_timing(s.get('时机', '2'), '1'):
                        try:
                            target = engine._smart_target(uid, s)
                            engine._execute_spell(uid, target, s)
                        except ReactionNeeded:
                            pass
            engine._sync_all_initiative_slots()
            init_list = il
            # ── DEBUG: log team assignments for multi_pvp ──
            _debug_log(f"multi_pvp CREATED: team_a={team_a_serials} team_b={team_b_serials}")
            _debug_log(f"  all_uids={all_uids}")
            _debug_log(f"  initiative teams: {[(e['name'], e['team'], e['userId'][:20]) for e in il]}")
            _debug_log(f"  player_controllers: {getattr(engine, '_player_controllers', {})}")
            for uid in all_uids:
                c = engine.get_char(uid)
                _debug_log(f"  char uid={uid[:30]} name={c.name} serial={c.serial} hp={engine._get_combat_hp(uid)}")
        # ── Setup map & initiative for non-multi_pvp modes ──
        # (multi_pvp already handles its own map & initiative above)
        if mode != 'multi_pvp':
            map_data = data.get('map')
            if not map_data:
                # Default 10x10 map
                map_data = {'width': 10, 'height': 10, 'entryRow': 5, 'obstacles': {}, 'occupants': {}}

            init_list = data.get('initiative', [])
            if not init_list:
                # Auto-generate initiative
                # Team Y: self + allies (uids not starting with 'opp_')
                # Team X: opponents (uids starting with 'opp_')
                team_a = [u for u in all_uids if not u.startswith('opp_')]
                team_b = [u for u in all_uids if u.startswith('opp_')]

                engine._set_map(map_data)
                il = []
                w, h = map_data.get('width', 10), map_data.get('height', 10)
                for i, uid in enumerate(team_a):
                    char = engine.get_char(uid)
                    for ai in range(MAX_DYNAMIC_ACTIONS):
                        entry_id = uid if ai == 0 else f"{uid}__act{ai}"
                        row = min(h - 1, math.ceil(h / 2) + i - len(team_a) // 2)
                        coord = format_coord(1, row)
                        label = f" (行动{ai+1})"
                        dex_val = char.get_attr('敏捷', 50)
                        init_roll = random.randint(1, 100)
                        il.append({'userId': entry_id, 'baseUserId': uid,
                                   'name': char.name + label, 'actionIdx': ai, 'team': 'Y',
                                   'dex': dex_val, 'initRoll': init_roll,
                                   'initRank': success_rank(init_roll, dex_val),
                                   'coord': coord})
                        if ai == 0:
                            map_data['occupants'][coord] = uid
                for i, uid in enumerate(team_b):
                    char = engine.get_char(uid)
                    for ai in range(MAX_DYNAMIC_ACTIONS):
                        entry_id = uid if ai == 0 else f"{uid}__act{ai}"
                        row = min(h - 1, math.ceil(h / 2) + i - len(team_b) // 2)
                        coord = format_coord(w - 2, row)
                        label = f" (行动{ai+1})"
                        dex_val = char.get_attr('敏捷', 50)
                        init_roll = random.randint(1, 100)
                        il.append({'userId': entry_id, 'baseUserId': uid,
                                   'name': char.name + label, 'actionIdx': ai, 'team': 'X',
                                   'dex': dex_val, 'initRoll': init_roll,
                                   'initRank': success_rank(init_roll, dex_val),
                                   'coord': coord})
                        if ai == 0:
                            map_data['occupants'][coord] = uid
                il.sort(key=lambda e: (-e['initRank'], -e['dex'], e['initRoll']))
                engine._set_map(map_data)
                engine._set_initiative(il)
                # 初始化动态行动槽抑制状态
                for uid in all_uids:
                    engine._sync_initiative_slots(uid)
                init_list = il
            else:
                engine._set_map(map_data)
                engine._set_initiative(init_list)
                il = init_list

        # ── Player controllers for pvp mode (each character's userId is the player) ──
        if mode == 'pvp' and not getattr(engine, '_player_controllers', None):
            engine._player_controllers = {}
        if not hasattr(engine, '_ai_overrides'):
            engine._ai_overrides = {}
        if not hasattr(engine, '_ai_override_state'):
            engine._ai_override_state = {}
            for uid in all_uids:
                engine._player_controllers[uid] = [uid]

        # ── Store battle (preparation phase by default) ──
        _battles[group_id] = engine
        _pending_reactions.pop(group_id, None)

        # ── Apply pre-configured AI overrides (from .setaiact outside battle) ──
        ai_overrides = data.get('ai_overrides', {})
        if ai_overrides and isinstance(ai_overrides, dict):
            if not hasattr(engine, '_ai_overrides'):
                engine._ai_overrides = {}
            engine._ai_overrides.update(ai_overrides)
            _debug_log(f"Applied {len(ai_overrides)} pre-configured AI override(s): {list(ai_overrides.keys())}")

        # ── Apply pre-configured full bindings (from .asfull outside battle) ──
        full_bindings = data.get('full_bindings', {})
        if full_bindings and isinstance(full_bindings, dict):
            if not hasattr(engine, '_full_controllers'):
                engine._full_controllers = {}
            if not hasattr(engine, '_player_controllers'):
                engine._player_controllers = {}
            for player_id, serials in full_bindings.items():
                if not isinstance(serials, list):
                    serials = [serials]
                for serial in serials:
                    for uid in all_uids:
                        char = engine.get_char(uid)
                        if char and getattr(char, 'serial', '') and char.serial.upper() == serial.upper():
                            base_uid = uid.split('__act')[0] if '__act' in str(uid) else uid
                            if base_uid not in engine._full_controllers:
                                engine._full_controllers[base_uid] = []
                            if player_id not in engine._full_controllers[base_uid]:
                                engine._full_controllers[base_uid].append(player_id)
                            if base_uid not in engine._player_controllers:
                                engine._player_controllers[base_uid] = []
                            if player_id not in engine._player_controllers[base_uid]:
                                engine._player_controllers[base_uid].append(player_id)
                            break
            _debug_log(f"Applied full_bindings for {len(full_bindings)} player(s): "
                       f"{list(engine._full_controllers.keys())}")

        # Start in preparation phase; .pr activates combat
        engine._set_state({'phase': 'preparation', 'round': 0, 'activeIndex': 0})
        # Init actions for prep-phase movement (free movement, no cost)
        engine._set_actions({uid: {'主动': 0, '附加': 999} for uid in all_uids})

        # ── Build response ──
        state_info = _serialize_battle_state(engine)
        chars_info = {}
        for uid in all_uids:
            chars_info[uid] = _char_summary(engine, uid)

        il = engine._get_initiative()
        active_entry = il[0] if il else None
        output_lines = [f"=== 战斗准备阶段 ==="]
        # Initiative order with global numbering
        init_text = _initiative_list_text(engine)
        output_lines.append(init_text)
        output_lines.append("\n准备阶段 — 可以 .as/.unbind 绑定解绑，.a m 移动位置，.app <序号> <Y|X> 添加角色，.rem <序号> 移除角色。")
        output_lines.append("输入 .pr 正式开始战斗。")

        result = {
            'battle_id': group_id,
            'human_uid': human_uid,
            'state': state_info['state'],
            'map': state_info['map'],
            'initiative': state_info['initiative'],
            'characters': chars_info,
            'current_turn': state_info['current_turn'],
            'output': '\n'.join(output_lines),
            'phase': 'preparation',
        }

        # ── Compute player bindings for display ──
        if getattr(engine, '_player_controllers', None):
            controlled = _get_player_controlled_characters(engine)
            result['controlled_characters'] = controlled
            if active_entry:
                active_base = active_entry.get('baseUserId', active_entry['userId'])
                controllers = getattr(engine, '_player_controllers', {}).get(active_base, [])
                result['next_player_ids'] = controllers
        else:
            result['next_player_ids'] = []

        # ── Final debug summary ──
        il_final = engine._get_initiative()
        _debug_log(f'=== create_battle RESPONSE === battle_id={group_id} mode={mode!r} phase=preparation')
        _debug_log(f'  final il teams: {[(e.get("name","?"), e.get("team","?")) for e in il_final]}')
        _debug_log(f'  player_controllers: {getattr(engine, "_player_controllers", {})}')
        _debug_log(f'  output length: {len(result.get("output", ""))} chars')
        _debug_log(f'  output preview: {(result.get("output", "") or "")[:300]}')
        return jsonify(result)


@app.route('/api/pvp/<battle_id>/action', methods=['POST'])
def submit_action(battle_id):
    """Submit a player action to an active battle."""
    data = request.get_json(force=True)
    if not data:
        return jsonify({'error': True, 'message': '无效的请求数据'}), 400

    player_id = data.get('player_id', '')
    action = data.get('action', '')
    target = data.get('target', '')
    args = data.get('args', '')
    count = int(data.get('count', '1') or '1')
    restim_mode = int(data.get('restim_mode', '0') or '0')
    # Also parse n# from args for backward compatibility
    import re
    m_count = re.search(r'\b(\d+)#\b', str(args))
    if m_count and count == 1:
        count = max(1, min(20, int(m_count.group(1))))
        args = re.sub(r'\b\d+#\b', '', str(args)).strip()

    with _lock:
        engine = _battles.get(battle_id)
        if not engine:
            return jsonify({'error': True, 'message': '战斗不存在，请重新开始'}), 404
        # Sync failed-attack reaction config from JS
        _afr_val = data.get('allow_failed_reaction')
        if _afr_val is not None:
            try:
                engine._allow_failed_reaction = (int(_afr_val) != 0)
            except (ValueError, TypeError):
                pass

        # Check pending reaction
        pending = _pending_reactions.get(battle_id)
        if isinstance(pending, list):
            pending = pending[0] if pending else None
        if pending:
            def_uid = pending.get('def_uid', '')
            target_uid = pending.get('target_uid', '')
            def_base_pa = def_uid.split('__act')[0] if '__act' in str(def_uid) else def_uid
            controllers = getattr(engine, '_player_controllers', {}).get(def_base_pa, [])
            if target_uid == player_id or player_id in controllers:
                return jsonify({'error': True, 'message': '请先处理待反应的动作（使用 .e d 或 .e c）',
                               'needs_reaction': True, 'pending_attack': pending}), 409

        state = engine._get_state()
        if not state or state.get('phase') not in ('active', 'preparation'):
            return jsonify({'error': True, 'message': '当前不在战斗中'}), 400

        is_prep = (state.get('phase') == 'preparation')
        round_before = state.get('round', 1) if not is_prep else 0

        il = engine._get_initiative()
        idx = state.get('activeIndex', 0)
        if idx >= len(il):
            return jsonify({'error': True, 'message': '无效的先攻索引'}), 500

        active_entry = il[idx]
        entry_uid = active_entry['userId']          # may have __act suffix; used for per-entry action tracking
        base_uid = active_entry.get('baseUserId', active_entry['userId'])
        controllers = getattr(engine, '_player_controllers', {}).get(base_uid, [])
        if not is_prep and active_entry['userId'] != player_id and player_id not in controllers:
            return jsonify({'error': True, 'message': f"不是你的回合！当前行动: {_fmt_char_name(engine, active_entry)}"}), 403

        # ── Resolve effective character uid ──
        # In multi-PvP mode, player_id is a QQ number / user ID, not the
        # character's engine userId (e.g. "y_Y4_1234").  When the player is
        # listed in _player_controllers for the active character we use the
        # base_uid; otherwise player_id IS the character uid (PvE / legacy).
        if player_id in controllers:
            effective_uid = base_uid
        else:
            effective_uid = player_id

        # Store restim_mode for this character
        if not hasattr(engine, '_restim_modes'):
            engine._restim_modes = {}
        engine._restim_modes[effective_uid] = restim_mode

        my_acts = engine._get_my_actions(entry_uid)

        # ── Batch summon mode: find controlled summons via _summon_groups or ownerId ──
        batch_summons = []
        if active_entry.get('isSummon') and player_id in controllers:
            active_base = active_entry.get('baseUserId', active_entry['userId'])
            grp = getattr(engine, '_summon_groups', {}).get(active_base)
            if not grp:
                # Fallback: check if active_base is a member of any group
                for rep_uid, g in getattr(engine, '_summon_groups', {}).items():
                    if active_base in g.get('members', []):
                        grp = g; break
            if grp:
                # ── Use group members (same-template summons share one initiative slot) ──
                members = getattr(engine, '_summon_members', {})
                for m_uid in grp.get('members', []):
                    hp = engine._get_combat_hp(m_uid) or 0
                    if hp <= 0: continue
                    if engine._is_untargetable(m_uid): continue
                    if player_id not in getattr(engine, '_player_controllers', {}).get(m_uid, []): continue
                    # Build a pseudo-entry from _summon_members or initiative
                    mdata = members.get(m_uid, {})
                    m_entry = next((e for e in il if e['userId'] == m_uid), None)
                    if m_entry:
                        batch_summons.append(m_entry)
                    elif mdata:
                        batch_summons.append({
                            'userId': m_uid, 'name': mdata.get('name', '召唤物'),
                            'skills': mdata.get('skills', []), 'team': mdata.get('team', 'Y'),
                            'isSummon': True, 'ownerId': grp.get('ownerId', ''),
                        })
                if len(batch_summons) <= 1:
                    batch_summons = []  # only one summon → no batch needed
            else:
                # ── Legacy: filter by ownerId (for summons not yet in group system) ──
                owner_id = active_entry.get('ownerId', '')
                if owner_id:
                    batch_summons = [e for e in il
                        if e.get('isSummon') and e.get('ownerId') == owner_id
                        and (engine._get_combat_hp(e['userId']) or 0) > 0
                        and not engine._is_untargetable(e['userId'])
                        and player_id in getattr(engine, '_player_controllers', {}).get(e['userId'], [])]
                    if len(batch_summons) <= 1:
                        batch_summons = []  # only one summon → no batch needed

        output = ''
        needs_reaction = False
        pending_attack = None
        pending_attacks_list = []  # for batch mode: list of reaction data dicts
        hp_changes = {}
        mp_changes = {}
        turn_advanced = False

        try:
            # ── Preparation phase: only allow movement ──
            if is_prep:
                if action.startswith('.a m '):
                    coord = action[5:].strip().upper()
                    mp = engine._get_map()
                    if mp:
                        for c, occ in list(mp.get('occupants', {}).items()):
                            if occ == effective_uid:
                                del mp['occupants'][c]
                        mp['occupants'][coord] = effective_uid
                        engine._set_map(mp)
                    output = f"{engine.get_char(effective_uid).name} 移动至 {coord}（准备阶段，不消耗行动数）"
                elif action in ('.s0', '.i end') or (action.startswith('.s') and len(action) > 2):
                    return jsonify({'error': True, 'message': '战斗尚未正式开始，请使用 .pr 开始战斗。准备阶段可用 .a m 移动位置。'}), 400
                else:
                    output = ''  # no-op for unknown prep actions
            elif action == '.i end':
                # ── End turn ──
                remaining_main = my_acts.get('主动', 0)
                remaining_extra = my_acts.get('附加', 0)
                char_name = engine.get_char(effective_uid).name
                base = engine._resolve_uid(entry_uid)
                actions_dict = engine._get_actions()
                actions_dict[base] = {'主动': 0, '附加': 0}
                engine._set_actions(actions_dict)
                output = engine._end_turn(entry_uid)
                if isinstance(output, str):
                    pass
                else:
                    output = f"{char_name} 结束回合（剩余主动×{remaining_main} 附加×{remaining_extra}）"
                turn_advanced = True

            elif action == '.s0':
                # ── Basic melee attack (supports count for multi-attack) ──
                # ── Batch summon mode: process all controlled summons ──
                if batch_summons:
                    sum_enemy_text = _enemy_list_text(engine, batch_summons[0]['userId'])
                    sum_name = active_entry.get('name', '召唤物')

                    # Determine common enemy and reaction choice (used for all summons)
                    enemies_s = [e for e in il
                               if e.get('team') != batch_summons[0].get('team', 'Y')
                               and (engine._get_combat_hp(e['userId']) or 0) > 0
                               and not engine._is_untargetable(e['userId'])]
                    if not enemies_s:
                        return jsonify({'error': True, 'message': '没有可攻击的敌人'}), 400
                    common_tid = enemies_s[0]['userId']
                    tgt_name = next((e.get('name', common_tid) for e in il if e['userId'] == common_tid), common_tid)

                    # Determine if defender is human and pre-compute AI reaction choice
                    is_human_defender = _is_human_controlled(engine, common_tid)
                    ai_choice = None
                    if not is_human_defender:
                        ai_choice = _resolve_ai_reaction_choice(engine, common_tid, can_dodge=True, can_counter=True)
                    # Now process all summons for count iterations
                    batch_entries = []
                    actual_s0_count = 0
                    for act_num in range(count):
                        if my_acts.get('主动', 0) <= 0:
                            break
                        for i, sum_entry in enumerate(batch_summons, 1):
                            sum_uid = sum_entry['userId']
                            sum_char = engine.get_char(sum_uid)
                            sum_hp = engine._get_combat_hp(sum_uid) or 0
                            sum_hp_max = sum_char.get_attr('体力上限', sum_hp) if sum_char else sum_hp
                            sum_skills = sum_entry.get('skills', [])
                            if sum_skills:
                                sum_sk = sum_skills[0]
                                sum_bn = sum_sk.get('name', '斗殴')
                                sum_bv = sum_sk.get('val', 25)
                            # ── Override with .bta custom skill / dice / pen / leth if provided ──
                            _custom_sv2 = data.get('skill_value')
                            if _custom_sv2 is not None:
                                sum_bv = int(_custom_sv2)
                            dd_s = engine._get_damage_dice(sum_uid, sum_bn)
                            p_s = sum_char.get_attr('伤害贯穿', 1)
                            l_s = sum_char.get_attr('致死骰', 1) or 0
                            _custom_dd = data.get('damage_dice', '')
                            if _custom_dd:
                                dd_s = _custom_dd
                            _custom_pen = data.get('penetration')
                            if _custom_pen is not None:
                                p_s = int(_custom_pen)
                            _custom_leth = data.get('lethality')
                            if _custom_leth is not None:
                                l_s = int(_custom_leth)
                            header_s = f"斗殴→{tgt_name}"
                            lines_s = []
                            try:
                                _, _, lines_s = engine._coc7_attack(sum_uid, common_tid, sum_bn, sum_bv, dd_s, p_s, l_s)
                            except ReactionNeeded as e_s:
                                e_s.data['battle_id'] = battle_id
                                if not is_human_defender:
                                    # Use pre-computed AI choice
                                    _, _, lines_s = engine.resolve_reaction(e_s.data, ai_choice)
                                else:
                                    def_base_s = e_s.data.get('def_uid', '').split('__act')[0] if '__act' in str(e_s.data.get('def_uid', '')) else e_s.data.get('def_uid', '')
                                    auto_react = getattr(engine, '_auto_react', {}).get(def_base_s)
                                    if auto_react and auto_react.get('remaining', 0) > 0:
                                        choice_auto = auto_react['type']
                                        auto_react['remaining'] -= 1
                                        if auto_react['remaining'] <= 0:
                                            engine._auto_react.pop(def_base_s, None)
                                        _, _, lines_s = engine.resolve_reaction(e_s.data, choice_auto)
                                        lines_s.append(f"（自动反应：{choice_auto}，剩余 {auto_react.get('remaining', 0)} 次）")
                                    else:
                                        e_s.data['restim_mode'] = restim_mode
                                        pending_attacks_list.append(e_s.data)
                                        lines_s = e_s.data.get('prefix_lines', [])
                            # Consume action once per count iteration (not per summon)
                            if i == len(batch_summons):
                                my_acts = engine._consume_action(effective_uid, '主动')
                                actual_s0_count += 1
                            # Refresh HP after attack (counter-attack may have damaged summon)
                            sum_hp_after = engine._get_combat_hp(sum_uid) or 0
                            batch_entries.append({
                                'name': sum_name,
                                'display_name': f"{sum_name} {i}#",
                                'hp': sum_hp_after, 'hp_max': sum_hp_max, 'alive': sum_hp_after > 0,
                                'round_header': '', 'enemy_text': sum_enemy_text if i == 1 and act_num == 0 else '',
                                'attack_header': header_s,
                                'attack_details': lines_s,
                            })
                    if pending_attacks_list:
                        first = pending_attacks_list[0]
                        pending_attack = {
                            'attacker_name': first['atk_name'],
                            'defender_name': first['def_name'],
                            'defender_id': first['def_uid'],
                            'defender_serial': '',
                            'skill_name': first['skill_name'],
                            'skill_value': first['skill_val'],
                            'batch_count': len(pending_attacks_list),
                        }
                        _pending_reactions[battle_id] = pending_attacks_list
                        needs_reaction = True
                    if batch_entries:
                        output = _format_summon_attack_block(sum_name, batch_entries)
                        if pending_attacks_list:
                            first = pending_attacks_list[0]
                            output += f"\n@{first['def_name']} 请做出反应：\n.e 闪避 / .e c 反击"
                            if actual_s0_count > 0:
                                output += f'\n（已执行 {actual_s0_count} 次攻击，剩余 {len(pending_attacks_list)} 次待反应）'
                    # Skip the regular .s0 handler
                else:
                    # ── Regular single-character .s0 ──
                    if my_acts.get('主动', 0) <= 0:
                        return jsonify({'error': True, 'message': '主动作次数已用尽！'}), 400
                    char = engine.get_char(effective_uid)
                    # For summons, skills are on the initiative entry, not character attrs
                    if active_entry.get('isSummon'):
                        sum_skills_reg = active_entry.get('skills', [])
                        if sum_skills_reg:
                            bn = sum_skills_reg[0].get('name', '斗殴')
                            bv = sum_skills_reg[0].get('val', 25)
                        else:
                            bn, bv = char.get_best_melee()
                    else:
                        bn, bv = char.get_best_melee()
                    enemies = [e for e in il
                               if e.get('team') != active_entry.get('team', 'Y')
                               and (engine._get_combat_hp(e['userId']) or 0) > 0
                               and not engine._is_untargetable(e['userId'])]
                    if not enemies:
                        return jsonify({'error': True, 'message': '没有可攻击的敌人'}), 400

                    if not enemies:
                        return jsonify({'error': True, 'message': '没有可攻击的敌人'}), 400

                    tid = None
                    if target and any(e['userId'] == target for e in enemies):
                        tid = target
                    if not tid:
                        tid = _resolve_target_by_serial(engine, effective_uid, args)
                    if not tid:
                        tgt_idx = _parse_target_index(args)
                        if tgt_idx is not None:
                            tid = _resolve_enemy_index(engine, effective_uid, tgt_idx)
                            if not tid:
                                return jsonify({'error': True, 'message': f'无效的敌方编号: {tgt_idx}'}), 400
                    if not tid:
                        args_stripped_s0 = (args or '').strip().upper()
                        if args_stripped_s0 in ('R', 'RP'):
                            import random as _rnd_s0
                            candidates_s0 = [e['userId'] for e in enemies]
                            if args_stripped_s0 == 'RP':
                                candidates_s0 = [e['userId'] for e in enemies if not e.get('isSummon')]
                            if candidates_s0:
                                tid = _rnd_s0.choice(candidates_s0)
                            else:
                                return jsonify({'error': True, 'message': '没有符合条件的随机目标（RP=不含召唤物）'}), 400
                        elif args and args.strip():
                            return jsonify({'error': True, 'message': '目标不可选中或无效，请重新选择'}), 400
                        else:
                            tid = enemies[0]['userId']

                    # ── Override with .bta custom skill / dice / pen / leth if provided ──
                    _custom_sv = data.get('skill_value')
                    if _custom_sv is not None:
                        bv = int(_custom_sv)
                    _custom_sn = data.get('skill_name', '')
                    if _custom_sn:
                        bn = _custom_sn
                    dd = engine._get_damage_dice(effective_uid, bn)
                    p = char.get_attr('伤害贯穿', 1)
                    l = char.get_attr('致死骰', 1) or 0
                    _custom_dd = data.get('damage_dice', '')
                    if _custom_dd:
                        dd = _custom_dd
                    _custom_pen = data.get('penetration')
                    if _custom_pen is not None:
                        p = int(_custom_pen)
                    _custom_leth = data.get('lethality')
                    if _custom_leth is not None:
                        l = int(_custom_leth)
                    bp = data.get('bp_suffix', '') or ''

                    all_s0_lines = []
                    actual_s0_count = 0
                    for _c in range(count):
                        if my_acts.get('主动', 0) <= 0:
                            break
                        # Check target still alive
                        tgt_hp = engine._get_combat_hp(tid) or 0
                        if tgt_hp <= 0:
                            break
                        try:
                            _, _, lines = engine._coc7_attack(effective_uid, tid, bn, bv, dd, p, l, bp_suffix=bp)
                            all_s0_lines.extend(lines)
                            my_acts = engine._consume_action(effective_uid, '主动')
                            actual_s0_count += 1
                        except ReactionNeeded as e:
                            e.data['battle_id'] = battle_id
                            e.data['target_uid'] = tid
                            def_uid = e.data.get('def_uid', '')
                            if not _is_human_controlled(engine, def_uid):
                                # AI defender → auto-resolve (with .setaiact react override)
                                choice = _resolve_ai_reaction_choice(engine, def_uid, can_dodge=True, can_counter=True)
                                _, _, lines = engine.resolve_reaction(e.data, choice)
                                all_s0_lines.extend(lines)
                                my_acts = engine._consume_action(effective_uid, '主动')
                                actual_s0_count += 1
                            else:
                                # ── Human defender → check auto-reaction first ──
                                def_base_s0 = def_uid.split('__act')[0] if '__act' in str(def_uid) else def_uid
                                auto_react_s0 = getattr(engine, '_auto_react', {}).get(def_base_s0)
                                if auto_react_s0 and auto_react_s0.get('remaining', 0) > 0:
                                    choice_s0 = auto_react_s0['type']
                                    auto_react_s0['remaining'] -= 1
                                    if auto_react_s0['remaining'] <= 0:
                                        engine._auto_react.pop(def_base_s0, None)
                                    _, _, lines = engine.resolve_reaction(e.data, choice_s0)
                                    all_s0_lines.extend(lines)
                                    my_acts = engine._consume_action(effective_uid, '主动')
                                    actual_s0_count += 1
                                    auto_label_s0 = '闪避' if choice_s0 == 'dodge' else '格挡' if choice_s0 == 'block' else '反击'
                                    remaining_s0 = engine._auto_react.get(def_base_s0, {}).get('remaining', 0)
                                    all_s0_lines.append(f"（自动反应：{auto_label_s0}，剩余 {remaining_s0} 次）")
                                else:
                                    # ── Collect into batch list (don't break) ──
                                    e.data['restim_mode'] = restim_mode
                                    pending_attacks_list.append(e.data)
                                    my_acts = engine._consume_action(effective_uid, '主动')
                                    actual_s0_count += 1
                                    if restim_mode == 1:
                                        all_s0_lines.append(f"{e.data['atk_name']} 对 {e.data['def_name']} 发起攻击！")
                                    else:
                                        all_s0_lines.extend(e.data['prefix_lines'])
                    # ── After loop: handle pending batch reactions ──
                    if pending_attacks_list:
                        # Build consolidated pending_attack info from first entry
                        first = pending_attacks_list[0]
                        def_char2 = engine.get_char(first.get('def_uid', ''))
                        pending_attack = {
                            'attacker_name': first['atk_name'],
                            'defender_name': first['def_name'],
                            'defender_id': first['def_uid'],
                            'defender_serial': getattr(def_char2, 'serial', '') if def_char2 else '',
                            'skill_name': first['skill_name'],
                            'skill_value': first['skill_val'],
                            'atk_roll': first['atk_result'],
                            'atk_rank': first['atk_rank'],
                            'atk_rank_text': rank_text(first['atk_rank']),
                            'dodge_val': first['dodge_val'],
                            'counter_val': first['counter_val'],
                            'damage_dice': first['dmg_dice'],
                            'penetration': first['pen'],
                            'batch_count': len(pending_attacks_list),
                        }
                        _pending_reactions[battle_id] = pending_attacks_list
                        needs_reaction = True
                        react_prompt = f"\n@{first['def_name']} 请做出反应：\n.e 闪避 / .e c 反击"
                        all_s0_lines.append(react_prompt)
                        if actual_s0_count > 0:
                            all_s0_lines.append(f'（已执行 {actual_s0_count} 次攻击，剩余 {len(pending_attacks_list)} 次待反应）')
                    output = '\n'.join(all_s0_lines) if all_s0_lines else output

            elif action.startswith('.s') and len(action) > 2:
                # ── Use skill ──
                sn = int(action[2:])
                if my_acts.get('主动', 0) <= 0:
                    return jsonify({'error': True, 'message': '主动作次数已用尽！'}), 400

                char = engine.get_char(effective_uid)
                spells = char.spells or engine.load_spells(effective_uid)
                spell = next((s for s in spells if s['index'] == sn), None)
                if not spell:
                    # Build helpful message listing available skills
                    available = sorted(s['index'] for s in spells)
                    avail_str = ', '.join(f".s{i}({next((s['name'] for s in spells if s['index']==i), '?')})" for i in available) if available else '无'
                    current_phase = getattr(char, 'phase', 1)
                    phase_hint = f' [当前阶段: {current_phase}]' if current_phase != 1 else ''
                    return jsonify({'error': True,
                        'message': f'未找到技能{sn}{phase_hint}。可用技能: {avail_str}'}), 404

                timing = spell.get('时机', '2')
                is_passive = has_timing(timing, '1')
                if not has_timing(timing, '2') and not is_passive:
                    return jsonify({'error': True, 'message': f'【{spell["name"]}】不能在主动作阶段使用'}), 400

                # Resolve target: serial from args > numeric index from args > smart target
                tgt = None
                args_stripped = (args or '').strip()
                tgt = _resolve_target_by_serial(engine, effective_uid, args)
                if not tgt:
                    tgt_idx = _parse_target_index(args)
                    if tgt_idx is not None:
                        tgt = _resolve_enemy_index(engine, effective_uid, tgt_idx)
                        if not tgt:
                            return jsonify({'error': True, 'message': f'无效的敌方编号: {tgt_idx}'}), 400
                if not tgt:
                    args_stripped_upper = args_stripped.upper()
                    if args_stripped_upper in ('R', 'RP'):
                        # Force random target selection (R=含召唤物, RP=不含)
                        tgt = engine._pick_random_target(effective_uid, include_summons=(args_stripped_upper == 'R'))
                        if not tgt:
                            return jsonify({'error': True, 'message': '没有符合条件的随机目标'}), 400
                    elif args_stripped:
                        return jsonify({'error': True, 'message': '目标不可选中或无效，请重新选择'}), 400
                    else:
                        tgt = engine._smart_target(effective_uid, spell)
                all_sp_out = []
                actual_sp = 0
                for _sc in range(count):
                    if my_acts.get('主动', 0) <= 0: break
                    tgt_hp_sp = engine._get_combat_hp(tgt) if tgt else None
                    if tgt_hp_sp is not None and tgt_hp_sp <= 0 and actual_sp > 0: break
                    try:
                        out = engine._execute_spell(effective_uid, tgt, spell)
                        all_sp_out.append(out if isinstance(out, str) else str(out))
                        if not is_passive:
                            my_acts = engine._consume_action(effective_uid, '主动')
                        actual_sp += 1
                    except ReactionNeeded as e:
                        e.data['battle_id'] = battle_id
                        e.data['target_uid'] = e.data.get('def_uid', '')
                        def_uid_sp = e.data.get('def_uid', '')
                        if not _is_human_controlled(engine, def_uid_sp):
                            # AI defender -> auto-resolve spell reaction (with .setaiact react override)
                            can_dodge_sp_ai = e.data.get('can_dodge', True)
                            can_counter_sp_ai = e.data.get('can_counter', True)
                            can_block_sp_ai = e.data.get('can_block', False)
                            choice_sp = _resolve_ai_reaction_choice(engine, def_uid_sp, can_dodge_sp_ai, can_counter_sp_ai, can_block_sp_ai)
                            dodged_sp, countered_sp, lines_sp = engine.resolve_spell_reaction(e.data, choice_sp)
                            all_sp_out.extend(lines_sp)
                            if not is_passive:
                                my_acts = engine._consume_action(effective_uid, '主动')
                            actual_sp += 1
                        else:
                            # Human defender: check auto-reaction first
                            def_base_sp2 = def_uid_sp.split('__act')[0] if '__act' in str(def_uid_sp) else def_uid_sp
                            auto_react_sp2 = getattr(engine, '_auto_react', {}).get(def_base_sp2)
                            if auto_react_sp2 and auto_react_sp2.get('remaining', 0) > 0:
                                choice_sp2 = auto_react_sp2['type']
                                can_dodge_sp2 = e.data.get('can_dodge', True)
                                can_counter_sp2 = e.data.get('can_counter', True)
                                can_block_sp2 = e.data.get('can_block', False)
                                is_valid_sp2 = ((choice_sp2 == 'dodge' and can_dodge_sp2) or
                                                (choice_sp2 == 'counter' and can_counter_sp2) or
                                                (choice_sp2 == 'block' and can_block_sp2))
                                if not is_valid_sp2:
                                    choice_sp2 = 'dodge' if can_dodge_sp2 else 'counter' if can_counter_sp2 else 'dodge'
                                auto_react_sp2['remaining'] -= 1
                                if auto_react_sp2['remaining'] <= 0:
                                    engine._auto_react.pop(def_base_sp2, None)
                                dodged_sp2, countered_sp2, lines_sp2 = engine.resolve_spell_reaction(e.data, choice_sp2)
                                all_sp_out.extend(lines_sp2)
                                if not is_passive:
                                    my_acts = engine._consume_action(effective_uid, '主动')
                                actual_sp += 1
                                auto_label_sp2 = '闪避' if choice_sp2 == 'dodge' else '格挡' if choice_sp2 == 'block' else '反击'
                                remaining_sp2 = engine._auto_react.get(def_base_sp2, {}).get('remaining', 0)
                                all_sp_out.append(f"（自动反应：{auto_label_sp2}，剩余 {remaining_sp2} 次）")
                            else:
                                # Human defender: check if any reaction options exist
                                can_dodge_sp = e.data.get('can_dodge', True)
                                can_counter_sp = e.data.get('can_counter', True)
                                can_block_sp = e.data.get('can_block', False)
                                if not can_dodge_sp and not can_counter_sp and not can_block_sp:
                                    # No reaction possible → spell connects without reaction
                                    all_sp_out.extend(e.data.get('prefix_lines', []))
                                    if not is_passive:
                                        my_acts = engine._consume_action(effective_uid, '主动')
                                    actual_sp += 1
                                else:
                                    # Store pending reaction; only show available options
                                    def_char_sp = engine.get_char(def_uid_sp)
                                    defender_serial_sp = getattr(def_char_sp, 'serial', '') if def_char_sp else ''
                                    pending_attack = {
                                    'attacker_name': e.data['atk_name'],
                                    'defender_name': e.data['def_name'],
                                    'defender_id': e.data['def_uid'],
                                    'defender_serial': defender_serial_sp,
                                    'skill_name': e.data.get('spell_name', e.data.get('skill_name', '')),
                                    'skill_value': e.data.get('skill_val', 100),
                                    'atk_roll': e.data['atk_result'],
                                    'atk_rank': e.data['atk_rank'],
                                    'atk_rank_text': rank_text(e.data['atk_rank']),
                                    'dodge_val': e.data['dodge_val'],
                                    'counter_val': e.data.get('counter_val', 0),
                                    'damage_dice': e.data['dmg_dice'],
                                    'penetration': e.data['pen'],
                                    'reaction_type': 'spell',
                                    'can_dodge': can_dodge_sp,
                                    'can_counter': can_counter_sp,
                                    'can_block': can_block_sp,
                                }
                                e.data['restim_mode'] = restim_mode
                                pending_attacks_list.append(e.data)
                                # Build single pending_attack for display
                                pending_attack = {
                                    'attacker_name': e.data['atk_name'],
                                    'defender_name': e.data['def_name'],
                                    'defender_id': e.data['def_uid'],
                                    'defender_serial': defender_serial_sp,
                                    'skill_name': e.data.get('spell_name', e.data.get('skill_name', '')),
                                    'skill_value': e.data.get('skill_val', 100),
                                    'atk_roll': e.data['atk_result'],
                                    'atk_rank': e.data['atk_rank'],
                                    'atk_rank_text': rank_text(e.data['atk_rank']),
                                    'dodge_val': e.data['dodge_val'],
                                    'counter_val': e.data.get('counter_val', 0),
                                    'damage_dice': e.data['dmg_dice'],
                                    'penetration': e.data['pen'],
                                    'reaction_type': 'spell',
                                    'can_dodge': can_dodge_sp,
                                    'can_counter': can_counter_sp,
                                    'can_block': can_block_sp,
                                    'batch_count': 1,
                                }
                                if restim_mode == 1:
                                    spell_name_sp = e.data.get('skill_name', '技能')
                                    all_sp_out.append(f"{e.data['atk_name']} 释放【{spell_name_sp}】→ {e.data['def_name']}！")
                                else:
                                    all_sp_out.extend(e.data.get('prefix_lines', []))
                # ── After loop: handle pending batch reactions ──
                if pending_attacks_list:
                    _pending_reactions[battle_id] = pending_attacks_list
                    needs_reaction = True
                    first_sp = pending_attacks_list[0]
                    opts_prompt = []
                    if first_sp.get('can_dodge', True):
                        opts_prompt.append('.e 闪避 / .e d')
                    if first_sp.get('can_counter', True):
                        opts_prompt.append('.e 反击 / .e c')
                    if first_sp.get('can_block', False):
                        opts_prompt.append('.e 格挡 / .e b')
                    react_prompt = '\n' + '  '.join(opts_prompt)
                    all_sp_out.append(f"@{first_sp['def_name']} 请做出反应：\n{react_prompt}")
                    if actual_sp > 0:
                        all_sp_out.append(f'（已执行 {actual_sp} 次技能，剩余 {len(pending_attacks_list)} 次待反应）')
                output = '\n'.join(all_sp_out) if all_sp_out else ''
            elif action.startswith('.a '):
                # ── Additional action ──
                if my_acts.get('附加', 0) <= 0:
                    return jsonify({'error': True, 'message': '附加动作次数已用尽！'}), 400

                sub = action[3:]
                if sub.startswith('m '):
                    coord = sub[2:].upper()
                    # Update map
                    mp = engine._get_map()
                    if mp:
                        for c, occ in list(mp.get('occupants', {}).items()):
                            if occ == effective_uid:
                                del mp['occupants'][c]
                        mp['occupants'][coord] = effective_uid
                        engine._set_map(mp)
                    output = f"{engine.get_char(effective_uid).name} 移动至 {coord}"
                    engine._consume_action(effective_uid, '附加')
                elif sub.startswith('s'):
                    sn = int(sub[1:])
                    char = engine.get_char(effective_uid)
                    spells = char.spells or engine.load_spells(effective_uid)
                    spell = next((s for s in spells if s['index'] == sn), None)
                    if not spell:
                        # Build helpful message listing available skills
                        available = sorted(s['index'] for s in spells)
                        avail_str = ', '.join(f".s{i}({next((s['name'] for s in spells if s['index']==i), '?')})" for i in available) if available else '无'
                        current_phase = getattr(char, 'phase', 1)
                        phase_hint = f' [当前阶段: {current_phase}]' if current_phase != 1 else ''
                        return jsonify({'error': True,
                            'message': f'未找到技能{sn}{phase_hint}。可用技能: {avail_str}'}), 404
                    timing = spell.get('时机', '2')
                    if not has_timing(timing, '3'):
                        return jsonify({'error': True, 'message': f'【{spell["name"]}】不能在附加动作阶段使用'}), 400
                    # Resolve target: serial from args > numeric index from args > smart target
                    tgt = None
                    args_stripped_extra = (args or '').strip()
                    tgt = _resolve_target_by_serial(engine, effective_uid, args)
                    if not tgt:
                        tgt_idx = _parse_target_index(args)
                        if tgt_idx is not None:
                            tgt = _resolve_enemy_index(engine, effective_uid, tgt_idx)
                            if not tgt:
                                return jsonify({'error': True, 'message': f'无效的敌方编号: {tgt_idx}'}), 400
                    if not tgt:
                        if args_stripped_extra:
                            return jsonify({'error': True, 'message': '目标不可选中或无效，请重新选择'}), 400
                        tgt = engine._smart_target(effective_uid, spell)
                    try:
                        out = engine._execute_spell(effective_uid, tgt, spell)
                        output = out if isinstance(out, str) else str(out)
                        engine._consume_action(effective_uid, '附加')
                    except ReactionNeeded as e:
                        e.data['battle_id'] = battle_id
                        def_uid_as = e.data.get('def_uid', '')
                        if not _is_human_controlled(engine, def_uid_as):
                            # AI defender -> auto-resolve spell reaction (with .setaiact react override)
                            can_dodge_as_ai = e.data.get('can_dodge', True)
                            can_counter_as_ai = e.data.get('can_counter', True)
                            can_block_as_ai = e.data.get('can_block', False)
                            choice_as = _resolve_ai_reaction_choice(engine, def_uid_as, can_dodge_as_ai, can_counter_as_ai, can_block_as_ai)
                            dodged_as, countered_as, lines_as = engine.resolve_spell_reaction(e.data, choice_as)
                            output = '\n'.join(lines_as)
                            engine._consume_action(effective_uid, '附加')
                        else:
                            # Human defender → check auto-reaction first
                            def_base_as2 = def_uid_as.split('__act')[0] if '__act' in str(def_uid_as) else def_uid_as
                            auto_react_as2 = getattr(engine, '_auto_react', {}).get(def_base_as2)
                            if auto_react_as2 and auto_react_as2.get('remaining', 0) > 0:
                                choice_as2 = auto_react_as2['type']
                                can_dodge_as2 = e.data.get('can_dodge', True)
                                can_counter_as2 = e.data.get('can_counter', True)
                                can_block_as2 = e.data.get('can_block', False)
                                is_valid_as2 = ((choice_as2 == 'dodge' and can_dodge_as2) or
                                                (choice_as2 == 'counter' and can_counter_as2) or
                                                (choice_as2 == 'block' and can_block_as2))
                                if not is_valid_as2:
                                    choice_as2 = 'dodge' if can_dodge_as2 else 'counter' if can_counter_as2 else 'dodge'
                                auto_react_as2['remaining'] -= 1
                                if auto_react_as2['remaining'] <= 0:
                                    engine._auto_react.pop(def_base_as2, None)
                                dodged_as2, countered_as2, lines_as2 = engine.resolve_spell_reaction(e.data, choice_as2)
                                output = '\n'.join(lines_as2)
                                engine._consume_action(effective_uid, '附加')
                                auto_label_as2 = '闪避' if choice_as2 == 'dodge' else '格挡' if choice_as2 == 'block' else '反击'
                                remaining_as2 = engine._auto_react.get(def_base_as2, {}).get('remaining', 0)
                                output += f"\n（自动反应：{auto_label_as2}，剩余 {remaining_as2} 次）"
                            else:
                                # Human defender -> store pending reaction
                                def_char_as = engine.get_char(def_uid_as)
                                defender_serial_as = getattr(def_char_as, 'serial', '') if def_char_as else ''
                                pending_attack = {
                                    'attacker_name': e.data['atk_name'],
                                    'defender_name': e.data['def_name'],
                                    'defender_id': e.data['def_uid'],
                                    'defender_serial': defender_serial_as,
                                    'skill_name': e.data.get('spell_name', e.data.get('skill_name', '')),
                                    'skill_value': e.data.get('skill_val', 100),
                                    'atk_roll': e.data['atk_result'],
                                    'atk_rank': e.data['atk_rank'],
                                    'atk_rank_text': rank_text(e.data['atk_rank']),
                                    'dodge_val': e.data['dodge_val'],
                                    'counter_val': e.data.get('counter_val', 0),
                                    'damage_dice': e.data['dmg_dice'],
                                    'penetration': e.data['pen'],
                                    'reaction_type': 'spell',
                                    'can_dodge': e.data.get('can_dodge', True),
                                    'can_counter': e.data.get('can_counter', True),
                                }
                                e.data['restim_mode'] = restim_mode
                                _pending_reactions[battle_id] = e.data
                                needs_reaction = True
                                opts_prompt_as = []
                                if e.data.get('can_dodge', True):
                                    opts_prompt_as.append('.e 闪避 / .e d')
                                if e.data.get('can_counter', True):
                                    opts_prompt_as.append('.e 反击 / .e c')
                                react_prompt_as = '\n' + '  '.join(opts_prompt_as)
                                if restim_mode == 1:
                                    spell_name_as = e.data.get('skill_name', '技能')
                                    output = f"{e.data['atk_name']} 释放【{spell_name_as}】→ {e.data['def_name']}！"
                                else:
                                    output = '\n'.join(e.data.get('prefix_lines', []))
                                output += f"\n@{e.data['def_name']} 请做出反应：\n{react_prompt_as}"
                                # needs_reaction is set; action NOT decremented; falls through
                                # to save actions & return pending_attack to caller
                elif sub.startswith('eat'):
                    # .a eat [目标名] — 使用物品（给自己）或给出物品（给目标）
                    target_name = sub[3:].strip()
                    target_id = None
                    if target_name:
                        il = engine._get_initiative()
                        target_entry = next((e for e in il if e.get('name', '') == target_name), None)
                        if target_entry:
                            target_id = target_entry['userId']
                        else:
                            return jsonify({'error': True, 'message': f'未找到目标: {target_name}'}), 404
                    out, success = engine._eat_cake(effective_uid, target_id)
                    output = out if isinstance(out, str) else str(out)
                    if success:
                        engine._consume_action(effective_uid, '附加')
                elif sub.startswith('give '):
                    # .a give <目标名> — 给出物品给队友
                    target_name = sub[5:].strip()
                    if not target_name:
                        return jsonify({'error': True, 'message': '用法: .a give <目标名>'}), 400
                    il = engine._get_initiative()
                    target_entry = next((e for e in il if e.get('name', '') == target_name), None)
                    if not target_entry:
                        return jsonify({'error': True, 'message': f'未找到目标: {target_name}'}), 404
                    out, success = engine._eat_cake(effective_uid, target_entry['userId'])
                    output = out if isinstance(out, str) else str(out)
                    if success:
                        engine._consume_action(effective_uid, '附加')
                else:
                    return jsonify({'error': True, 'message': f'不支持的附加动作: {sub}'}), 400

            elif action.startswith('.g '):
                # ── Transfer craft item ──
                item_name = action[3:].strip()
                out, success = engine._eat_cake(effective_uid)
                output = out if isinstance(out, str) else str(out)

            else:
                return jsonify({'error': True, 'message': f'不支持的动作: {action}'}), 400

            # ── Collect HP/MP changes ──
            for e in il:
                uid = e['userId']
                hp = engine._get_combat_hp(uid) or 0
                hp_changes[uid] = hp
                mp_changes[uid] = engine.get_char(uid).get_attr('魔力', 0)

            # ── Check if turn should advance ──
            # Skip for .i end (already called _end_turn above) and .a actions (附加动作)
            if not needs_reaction and my_acts.get('主动', 0) <= 0 and not action.startswith('.a ') and action != '.i end':
                try:
                    end_out = engine._end_turn(entry_uid)
                    if isinstance(end_out, str) and len(end_out) > 0:
                        output += '\n' + end_out
                except Exception:
                    pass
                turn_advanced = True

        except Exception as e:
            if not isinstance(e, ReactionNeeded):
                return jsonify({'error': True, 'message': f'服务器错误: {str(e)}'}), 500

        # ── PvE / Multi-PvP: run AI turns after player action ──
        auto_turns = []
        if not needs_reaction:
            # Check if next character is not human-controlled → auto-play AI turns
            state = engine._get_state()
            if state and state.get('phase') == 'active':
                il = engine._get_initiative()
                idx = state.get('activeIndex', 0)
                if idx < len(il):
                    next_entry = il[idx]

                    if not _is_human_controlled(engine, next_entry['userId']):
                        Q = _load_q_table_pvp()
                        auto_turns = _run_ai_turns(engine, None, Q)
                        # Check if _run_ai_turns stored a pending reaction (AI attacked a human)
                        pending = _pending_reactions.get(battle_id)
                        # Normalize: pending can be a dict or a list of dicts
                        if isinstance(pending, list):
                            pending = pending[0] if pending else None
                        if pending:
                            def_uid = pending.get('def_uid', '')
                            if _is_human_controlled(engine, def_uid):
                                needs_reaction = True
                                def_char2 = engine.get_char(def_uid)
                                defender_serial2 = getattr(def_char2, 'serial', '') if def_char2 else ''
                                pending_attack = {
                                    'attacker_name': pending['atk_name'],
                                    'defender_name': pending['def_name'],
                                    'defender_id': pending['def_uid'],
                                    'defender_serial': defender_serial2,
                                    'skill_name': pending['skill_name'],
                                    'skill_value': pending['skill_val'],
                                    'atk_roll': pending['atk_result'],
                                    'atk_rank': pending['atk_rank'],
                                    'atk_rank_text': rank_text(pending['atk_rank']),
                                    'dodge_val': pending['dodge_val'],
                                    'counter_val': pending['counter_val'],
                                    'damage_dice': pending['dmg_dice'],
                                    'penetration': pending['pen'],
                                }

        # ── Build response ──
        refreshed = _serialize_battle_state(engine)
        result = {
            'output': output,
            'map': refreshed['map'],
            'hp_changes': hp_changes,
            'mp_changes': mp_changes,
            'effects': refreshed['effects'],
            'current_turn': refreshed['current_turn'],
            'state': refreshed['state'],
            'turn_advanced': turn_advanced,
            'needs_reaction': needs_reaction,
        }
        if needs_reaction and pending_attack:
            result['pending_attack'] = pending_attack
        if auto_turns:
            result['auto_turns'] = auto_turns
            # NOTE: JS side appends auto_turns to output — do NOT add to result['output'] here
            # to prevent double-display.  auto_turns is passed separately for JS to use.
            # Refresh again after AI turns
            refreshed2 = _serialize_battle_state(engine)
            result['current_turn'] = refreshed2['current_turn']
            result['state'] = refreshed2['state']

        # ── Add next_player_ids for multi-player modes ──
        if getattr(engine, '_player_controllers', None):
            il = engine._get_initiative()
            st = engine._get_state()
            if st and il and st.get('phase') == 'active':
                idx = st.get('activeIndex', 0)
                if idx < len(il):
                    active_entry = il[idx]
                    active_base = active_entry.get('baseUserId', active_entry['userId'])
                    result['next_player_ids'] = getattr(engine, '_player_controllers', {}).get(active_base, [])


        # Show turn announcement if current active character is human-controlled
        if not needs_reaction:
            il_final = engine._get_initiative()
            st_final = engine._get_state()
            if st_final and il_final and st_final.get('phase') == 'active':
                idx_final = st_final.get('activeIndex', 0)
                if idx_final < len(il_final):
                    active_final = il_final[idx_final]
                    active_final_uid = active_final['userId']
                    if _is_human_controlled(engine, active_final_uid):
                        # Show round start display if round changed since action began.
                        # Note: _run_ai_turns never includes the round display when the next
                        # character is human (it breaks before adding), so we always need to
                        # add it here when the round changed, regardless of auto_turns.
                        round_after = st_final.get('round', 1)
                        turn_info = ''
                        # Only add initiative if not already shown in auto_turns
                        _init_shown = any('DEX=' in t for t in (auto_turns or []))
                        if round_after != round_before and not _init_shown:
                            turn_info += engine._get_initiative_display() + '\n\n'
                        turn_info += _build_turn_announcement(engine, active_final_uid)
                        result['turn_info'] = turn_info

        return jsonify(result)


@app.route('/api/pvp/<battle_id>/react', methods=['POST'])
def submit_reaction(battle_id):
    """Submit a reaction choice (dodge/counter/block) or set auto-reaction preference."""
    data = request.get_json(force=True)
    if not data:
        return jsonify({'error': True, 'message': '无效的请求数据'}), 400

    player_id = data.get('player_id', '')
    choice = data.get('choice', 'dodge')

    if choice not in ('dodge', 'counter', 'block'):
        return jsonify({'error': True, 'message': '选择无效，请使用 dodge、counter 或 block'}), 400

    with _lock:
        engine = _battles.get(battle_id)
        if not engine:
            return jsonify({'error': True, 'message': '战斗不存在'}), 404

        # ── set_auto_only mode: 仅设置自动反应偏好，不处理当前待反应 ──
        set_auto_only = data.get('set_auto_only', False)
        if set_auto_only:
            react_count = data.get('react_count', 0)
            react_type = data.get('react_type', '')
            serial = data.get('serial', '')
            if react_count <= 0 or react_type not in ('dodge', 'counter', 'block') or not serial:
                return jsonify({'error': True, 'message': '参数不完整'}), 400
            # Find the character by serial to get its base_uid
            il = engine._get_initiative()
            found_base = None
            for e in il:
                if e.get('isSummon'): continue
                c = engine.get_char(e.get('baseUserId', e['userId']))
                if c and getattr(c, 'serial', '') and c.serial.upper() == serial.upper():
                    found_base = e.get('baseUserId', e['userId'])
                    break
            if not found_base:
                return jsonify({'error': True, 'message': f'未在战斗中找到角色 {serial}'}), 404
            if not hasattr(engine, '_auto_react'):
                engine._auto_react = {}
            engine._auto_react[found_base] = {'type': react_type, 'remaining': react_count}
            lab_map = {'dodge': '闪避', 'counter': '反击', 'block': '格挡'}
            return jsonify({
                'status': 'ok',
                'message': f'已为 {serial} 设定自动反应：{lab_map.get(react_type, react_type)} ×{react_count}',
                'auto_react_set': True,
            })

        pending = _pending_reactions.get(battle_id)
        if not pending:
            return jsonify({'error': True, 'message': '没有待处理的反应'}), 400

        # ── Batch mode: pending is a list of reaction data dicts ──
        if isinstance(pending, list):
            _pending_reactions.pop(battle_id, None)
            first_p = pending[0]
            is_spell_batch = first_p.get('reaction_type') == 'spell'
            # Build structured entries per attack
            reaction_entries = []
            for p in pending:
                if is_spell_batch:
                    _, _, lines = engine.resolve_spell_reaction(p, choice)
                else:
                    _, _, lines = engine.resolve_reaction(p, choice)
                prefix = p.get('prefix_lines', [])
                if p.get('restim_mode') == 1 and prefix:
                    lines = list(prefix) + list(lines)
                # Extract attacker info
                atk_name = p.get('atk_name', '召唤物')
                def_name = p.get('def_name', '防御')
                reaction_entries.append({
                    'attacker': atk_name, 'defender': def_name,
                    'lines': lines,
                })
            # Group by defender
            def_groups = {}
            for r in reaction_entries:
                k = r['defender']
                def_groups.setdefault(k, []).append(r)
            out_lines = []
            for def_name, res in def_groups.items():
                out_lines.append(f"反应结果（{def_name}，{len(res)}次）：")
                for i, r in enumerate(res, 1):
                    out_lines.append(f"  #{i} {r['attacker']}:")
                    for ld in r['lines']:
                        out_lines.append(f"    {ld}")
            output = '\n'.join(out_lines)
            # Auto-react storage
            react_count = data.get('react_count', 0)
            react_type = data.get('react_type', '')
            if react_count > 0 and react_type in ('dodge', 'counter', 'block'):
                def_uid_batch = first_p.get('def_uid', '')
                def_base_batch = def_uid_batch.split('__act')[0] if '__act' in str(def_uid_batch) else def_uid_batch
                if not hasattr(engine, '_auto_react'):
                    engine._auto_react = {}
                engine._auto_react[def_base_batch] = {'type': react_type, 'remaining': react_count}

            # ── Post-reaction processing (mirrors single-reaction path) ──
            # Action was already consumed at attack time (in .s0 / _run_ai_turns).
            # Just check if turn should advance (attacker out of main actions).
            round_before_batch = 0
            st_before_batch = engine._get_state()
            if st_before_batch and st_before_batch.get('phase') == 'active':
                round_before_batch = st_before_batch.get('round', 1)

            il_batch = engine._get_initiative()
            st_batch = engine._get_state()
            idx_batch = st_batch.get('activeIndex', 0) if st_batch else 0
            if idx_batch < len(il_batch):
                atk_entry_batch = il_batch[idx_batch]
                atk_uid_batch = atk_entry_batch['userId']
                my_acts_batch = engine._get_my_actions(atk_uid_batch)

                # Check if turn should advance (attacker out of main actions)
                if my_acts_batch.get('主动', 0) <= 0:
                    try:
                        end_out_batch = engine._end_turn(atk_uid_batch)
                        if isinstance(end_out_batch, str) and len(end_out_batch) > 0:
                            output += '\n' + end_out_batch
                    except Exception:
                        pass

            # Collect HP
            hp_changes_batch = {}
            for e in engine._get_initiative():
                uid = e['userId']
                hp_changes_batch[uid] = engine._get_combat_hp(uid) or 0

            # ── Continue AI turns if it's now an AI character's turn ──
            auto_turns_batch = []
            needs_reaction_batch = False
            pending_attack_batch = None
            st2 = engine._get_state()
            if st2 and st2.get('phase') == 'active':
                il2 = engine._get_initiative()
                idx2 = st2.get('activeIndex', 0)
                if idx2 < len(il2):
                    next_entry_batch = il2[idx2]

                    if not _is_human_controlled(engine, next_entry_batch['userId']):
                        Q = _load_q_table_pvp()
                        auto_turns_batch = _run_ai_turns(engine, None, Q)
                        # Check if AI turns triggered another reaction
                        pending2_batch = _pending_reactions.get(battle_id)
                        if pending2_batch:
                            def_uid2_batch = (pending2_batch.get('def_uid', '') if isinstance(pending2_batch, dict) else
                                             pending2_batch[0].get('def_uid', '') if isinstance(pending2_batch, list) and pending2_batch else '')
                            if _is_human_controlled(engine, def_uid2_batch):
                                needs_reaction_batch = True
                                p2 = pending2_batch if isinstance(pending2_batch, dict) else pending2_batch[0]
                                def_char3 = engine.get_char(def_uid2_batch)
                                defender_serial3 = getattr(def_char3, 'serial', '') if def_char3 else ''
                                pending_attack_batch = {
                                    'attacker_name': p2['atk_name'],
                                    'defender_name': p2['def_name'],
                                    'defender_id': p2['def_uid'],
                                    'defender_serial': defender_serial3,
                                    'skill_name': p2['skill_name'],
                                    'skill_value': p2['skill_val'],
                                    'atk_roll': p2['atk_result'],
                                    'atk_rank': p2['atk_rank'],
                                    'atk_rank_text': rank_text(p2['atk_rank']),
                                    'dodge_val': p2['dodge_val'],
                                    'counter_val': p2['counter_val'],
                                    'damage_dice': p2['dmg_dice'],
                                    'penetration': p2['pen'],
                                }

            refreshed_batch = _serialize_battle_state(engine)
            result = {
                'output': output,
                'map': refreshed_batch['map'],
                'hp_changes': hp_changes_batch,
                'current_turn': refreshed_batch['current_turn'],
                'state': refreshed_batch['state'],
                'needs_reaction': needs_reaction_batch,
            }
            if needs_reaction_batch and pending_attack_batch:
                result['pending_attack'] = pending_attack_batch
            if auto_turns_batch:
                result['auto_turns'] = auto_turns_batch

            # ── Add next_player_ids for multi-player modes ──
            if getattr(engine, '_player_controllers', None):
                il3 = engine._get_initiative()
                st3 = engine._get_state()
                if st3 and il3 and st3.get('phase') == 'active':
                    idx3 = st3.get('activeIndex', 0)
                    if idx3 < len(il3):
                        active3 = il3[idx3]
                        active_base3 = active3.get('baseUserId', active3['userId'])
                        result['next_player_ids'] = getattr(engine, '_player_controllers', {}).get(active_base3, [])

            # ── Controlled characters ──
            if getattr(engine, '_player_controllers', None):
                result['controlled_characters'] = _get_player_controlled_characters(engine)

            # ── Show turn announcement if current active character is human-controlled ──
            il_final = engine._get_initiative()
            st_final = engine._get_state()
            if st_final and il_final and st_final.get('phase') == 'active':
                idx_final = st_final.get('activeIndex', 0)
                if idx_final < len(il_final):
                    active_final = il_final[idx_final]
                    active_final_uid = active_final['userId']
                    if _is_human_controlled(engine, active_final_uid):
                        round_after = st_final.get('round', 1)
                        turn_info_r = ''
                        _init_shown_r = any('DEX=' in t for t in (auto_turns_batch or []))
                        if round_after != round_before_batch and not _init_shown_r:
                            turn_info_r += engine._get_initiative_display() + '\n\n'
                        turn_info_r += _build_turn_announcement(engine, active_final_uid)
                        result['turn_info'] = turn_info_r

            return jsonify(result)

        # Track round before reaction resolution (for turn announcement)
        state_before = engine._get_state()
        round_before = state_before.get('round', 1) if state_before and state_before.get('phase') == 'active' else 0

        is_spell_reaction = pending.get('reaction_type') == 'spell'

        # ── Validate choice; do NOT consume reaction on invalid input ──
        can_dodge = pending.get('can_dodge', True)
        can_counter = pending.get('can_counter', True)
        can_block = pending.get('can_block', False)

        # Check if chosen option is valid
        choice_invalid = False
        if is_spell_reaction:
            if choice == 'counter' and not can_counter: choice_invalid = True
            elif choice == 'dodge' and not can_dodge: choice_invalid = True
            elif choice == 'block' and not can_block: choice_invalid = True
        elif not is_spell_reaction and choice == 'block' and not can_block:
            choice_invalid = True

        if choice_invalid:
            # Show only available options (don't auto-select)
            valid_opts = []
            if can_dodge: valid_opts.append('.e 闪避 / .e d')
            if can_counter: valid_opts.append('.e 反击 / .e c')
            if can_block: valid_opts.append('.e 格挡 / .e b')
            if not valid_opts:
                return jsonify({'error': True, 'message': '该攻击不可反应（无可用反应方式）'}), 400
            opts_str = '  '.join(valid_opts)
            return jsonify({'error': True, 'message': f'请指定反应类型：\n{opts_str}'}), 400

        # ── Now consume the pending reaction (validation passed) ──
        _pending_reactions.pop(battle_id, None)

        def_uid = pending.get('def_uid', '')
        def_base = def_uid.split('__act')[0] if '__act' in str(def_uid) else def_uid

        # ── Store auto-reaction preference for future attacks on this defender ──
        react_count = data.get('react_count', 0)
        react_type = data.get('react_type', '')
        if react_count > 0 and react_type in ('dodge', 'counter', 'block'):
            if not hasattr(engine, '_auto_react'):
                engine._auto_react = {}
            engine._auto_react[def_base] = {'type': react_type, 'remaining': react_count}
        controllers = getattr(engine, '_player_controllers', {}).get(def_base, [])
        if def_uid != player_id and player_id not in controllers:
            return jsonify({'error': True, 'message': '这个反应不是你的'}), 403

        try:
            if is_spell_reaction:
                dodged_sp, countered_sp, lines_sp = engine.resolve_spell_reaction(pending, choice)
                # ── .setrestim=1: prepend withheld attack roll ──
                if pending.get('restim_mode') == 1:
                    withheld = '\n'.join(pending.get('prefix_lines', []))
                    if withheld:
                        output = withheld + '\n' + '\n'.join(lines_sp)
                    else:
                        output = '\n'.join(lines_sp)
                else:
                    output = '\n'.join(lines_sp)

                # ── Apply spell damage if not fully dodged or cleanly countered ──
                if not dodged_sp and not countered_sp:
                    dmg_lines = engine._apply_spell_damage(
                        pending['atk_uid'], pending['def_uid'],
                        pending['dmg_dice'], pending.get('pen', 0), pending.get('leth', 0),
                        lifesteal_ratio=pending.get('lifesteal_ratio', 0),
                        dot_dice=pending.get('dot_dice', ''),
                        dur=pending.get('dur', 0),
                        spell_name=pending.get('spell_name', pending.get('spell', {}).get('name', '')),
                        spell_index=pending.get('spell_index', pending.get('spell', {}).get('index', 0)),
                        atk_rank=pending.get('atk_rank', 4),
                        atk_roll=pending.get('atk_roll', 1))
                    if dmg_lines:
                        output += '\n' + dmg_lines

                # ── Action was consumed at attack time; just check if turn should advance ──
                il = engine._get_initiative()
                state = engine._get_state()
                idx = state.get('activeIndex', 0) if state else 0
                if idx < len(il):
                    atk_entry = il[idx]
                    atk_uid = atk_entry['userId']
                    my_acts = engine._get_my_actions(atk_uid)

                    # Check if turn should advance (AI attacker out of actions)
                    if my_acts.get('主动', 0) <= 0:
                        try:
                            end_out = engine._end_turn(atk_uid)
                            if isinstance(end_out, str) and len(end_out) > 0:
                                output += '\n' + end_out
                        except Exception:
                            pass
            else:
                winner_uid, loser_uid, lines = engine.resolve_reaction(pending, choice)
                # ── .setrestim=1: prepend withheld attack roll ──
                if pending.get('restim_mode') == 1:
                    withheld = '\n'.join(pending.get('prefix_lines', []))
                    if withheld:
                        output = withheld + '\n' + '\n'.join(lines)
                    else:
                        output = '\n'.join(lines)
                else:
                    output = '\n'.join(lines)

                # ── Action was consumed at attack time; just check if turn should advance ──
                il = engine._get_initiative()
                state = engine._get_state()
                idx = state.get('activeIndex', 0) if state else 0
                if idx < len(il):
                    atk_entry = il[idx]
                    atk_uid = atk_entry['userId']
                    my_acts = engine._get_my_actions(atk_uid)

                    # Check if turn should advance (AI attacker out of actions)
                    if my_acts.get('主动', 0) <= 0:
                        try:
                            end_out = engine._end_turn(atk_uid)
                            if isinstance(end_out, str) and len(end_out) > 0:
                                output += '\n' + end_out
                        except Exception:
                            pass
    
            # Collect HP
            hp_changes = {}
            for e in engine._get_initiative():
                uid = e['userId']
                hp_changes[uid] = engine._get_combat_hp(uid) or 0

            # ── Continue AI turns if it's now an AI character's turn ──
            auto_turns = []
            needs_reaction = False
            pending_attack = None
            st = engine._get_state()
            if st and st.get('phase') == 'active':
                il2 = engine._get_initiative()
                idx2 = st.get('activeIndex', 0)
                if idx2 < len(il2):
                    next_entry = il2[idx2]

                    if not _is_human_controlled(engine, next_entry['userId']):
                        Q = _load_q_table_pvp()
                        auto_turns = _run_ai_turns(engine, None, Q)
                        # Check if AI turns triggered another reaction (AI attacked human again)
                        pending2 = _pending_reactions.get(battle_id)
                        if pending2:
                            def_uid2 = pending2.get('def_uid', '')
                            if _is_human_controlled(engine, def_uid2):
                                needs_reaction = True
                                def_char3 = engine.get_char(def_uid2)
                                defender_serial3 = getattr(def_char3, 'serial', '') if def_char3 else ''
                                pending_attack = {
                                    'attacker_name': pending2['atk_name'],
                                    'defender_name': pending2['def_name'],
                                    'defender_id': pending2['def_uid'],
                                    'defender_serial': defender_serial3,
                                    'skill_name': pending2['skill_name'],
                                    'skill_value': pending2['skill_val'],
                                    'atk_roll': pending2['atk_result'],
                                    'atk_rank': pending2['atk_rank'],
                                    'atk_rank_text': rank_text(pending2['atk_rank']),
                                    'dodge_val': pending2['dodge_val'],
                                    'counter_val': pending2['counter_val'],
                                    'damage_dice': pending2['dmg_dice'],
                                    'penetration': pending2['pen'],
                                }

            refreshed = _serialize_battle_state(engine)
            result = {
                'output': output,
                'map': refreshed['map'],
                'hp_changes': hp_changes,
                'current_turn': refreshed['current_turn'],
                'state': refreshed['state'],
                'needs_reaction': needs_reaction,
            }
            if needs_reaction and pending_attack:
                result['pending_attack'] = pending_attack
            if auto_turns:
                result['auto_turns'] = auto_turns

            # ── Add next_player_ids for multi-player modes ──
            if getattr(engine, '_player_controllers', None):
                il3 = engine._get_initiative()
                st3 = engine._get_state()
                if st3 and il3 and st3.get('phase') == 'active':
                    idx3 = st3.get('activeIndex', 0)
                    if idx3 < len(il3):
                        active3 = il3[idx3]
                        active_base3 = active3.get('baseUserId', active3['userId'])
                        result['next_player_ids'] = getattr(engine, '_player_controllers', {}).get(active_base3, [])

            # ── Show turn announcement if current active character is human-controlled ──
            il_final = engine._get_initiative()
            st_final = engine._get_state()
            if st_final and il_final and st_final.get('phase') == 'active':
                idx_final = st_final.get('activeIndex', 0)
                if idx_final < len(il_final):
                    active_final = il_final[idx_final]
                    active_final_uid = active_final['userId']
                    if _is_human_controlled(engine, active_final_uid):
                        round_after = st_final.get('round', 1)
                        turn_info_r = ''
                        _init_shown_r = any('DEX=' in t for t in (auto_turns or []))
                        if round_after != round_before and not _init_shown_r:
                            turn_info_r += engine._get_initiative_display() + '\n\n'
                        turn_info_r += _build_turn_announcement(engine, active_final_uid)
                        result['turn_info'] = turn_info_r

            return jsonify(result)

        except Exception as e:
            return jsonify({'error': True, 'message': f'反应处理错误: {str(e)}'}), 500


@app.route('/api/pvp/<battle_id>/hs', methods=['POST'])
def do_henshin(battle_id):
    """Execute henshin (变身) for a character."""
    data = request.get_json(force=True) or {}
    player_id = data.get('player_id', '')

    with _lock:
        engine = _battles.get(battle_id)
        if not engine:
            return jsonify({'error': True, 'message': '战斗不存在'}), 404

        if player_id and player_id in engine.characters:
            output = engine.process_command(player_id, '.hs')
            s = _char_summary(engine, player_id)
            return jsonify({
                'output': output if isinstance(output, str) else str(output),
                'character': s,
            })

        return jsonify({'error': True, 'message': '未指定角色'}), 400


@app.route('/api/pvp/<battle_id>/unh', methods=['POST'])
def do_unhenshin(battle_id):
    """Execute un-henshin (解除变身) for a character."""
    data = request.get_json(force=True) or {}
    player_id = data.get('player_id', '')

    with _lock:
        engine = _battles.get(battle_id)
        if not engine:
            return jsonify({'error': True, 'message': '战斗不存在'}), 404

        if player_id and player_id in engine.characters:
            output = engine.process_command(player_id, '.unh')
            s = _char_summary(engine, player_id)
            return jsonify({
                'output': output if isinstance(output, str) else str(output),
                'character': s,
            })

        return jsonify({'error': True, 'message': '未指定角色'}), 400


@app.route('/api/pvp/<battle_id>/state', methods=['GET'])
def get_state(battle_id):
    """Get full battle state."""
    with _lock:
        engine = _battles.get(battle_id)
        if not engine:
            return jsonify({'error': True, 'message': '战斗不存在'}), 404
        return jsonify(_serialize_battle_state(engine))


@app.route('/api/pvp/<battle_id>/alist', methods=['GET'])
def get_alist(battle_id):
    """Return available actions for a player's controlled characters."""
    player_id = request.args.get('player_id', '')
    with _lock:
        engine = _battles.get(battle_id)
        if not engine:
            return jsonify({'error': True, 'message': '战斗不存在'}), 404

        il = engine._get_initiative()

        # Find all characters controlled by this player
        controlled = []
        controllers = getattr(engine, '_player_controllers', {})
        for base_uid, player_list in controllers.items():
            if player_id in (player_list if isinstance(player_list, list) else []):
                entry = next((e for e in il if e.get('baseUserId', e['userId']) == base_uid or e['userId'] == base_uid), None)
                if entry and (engine._get_combat_hp(entry['userId']) or 0) > 0:
                    controlled.append(entry)

        # If no controller entry but player_id matches a character userId directly
        if not controlled:
            direct = next((e for e in il if (e.get('baseUserId', e['userId']) == player_id or e['userId'] == player_id) and (engine._get_combat_hp(e['userId']) or 0) > 0), None)
            if direct:
                controlled.append(direct)

        characters_info = []
        for entry in controlled:
            uid = entry['userId']
            char = engine.get_char(uid)
            my_acts = engine._get_my_actions(uid)
            spells = char.spells or engine.load_spells(uid)
            hp = engine._get_combat_hp(uid) or 0
            hp_max = char.get_attr('体力上限', hp)
            mp = char.get_attr('魔力', 0) or 0
            mp_max = char.get_attr('魔力上限', mp)

            skills = []
            # .s0 basic attack
            bn, bv = char.get_best_melee()
            if bv > 0:
                skills.append({
                    'index': 0, 'name': '基本攻击', 'skill_name': bn, 'skill_val': bv,
                    'timing': '主', 'mp_cost': 0, 'available': my_acts.get('主动', 0) > 0,
                })
            # .sN spells
            for s in spells:
                timing = s.get('时机', '2')
                timing_labels = []
                if has_timing(timing, '1'): timing_labels.append('被')
                if has_timing(timing, '2'): timing_labels.append('主')
                if has_timing(timing, '3'): timing_labels.append('附')
                timing_str = '/'.join(timing_labels) if timing_labels else '?'
                mp_cost = sum(int(e.get('消耗mp', 0) or 0) for e in s.get('effects', []))
                is_main = has_timing(timing, '2')
                is_extra = has_timing(timing, '3')
                available = (is_main and my_acts.get('主动', 0) > 0) or (is_extra and my_acts.get('附加', 0) > 0)
                skills.append({
                    'index': s['index'], 'name': s['name'],
                    'timing': timing_str, 'mp_cost': mp_cost, 'available': available,
                })

            # Check items
            has_eat = engine._has_healing_item(uid) if hasattr(engine, '_has_healing_item') else False
            has_craft = any(e.get('type') == 'create' and e.get('craftRoundsRemaining', 1) <= 0
                           for e in engine._get_effects() if e.get('sourceUserId') == uid)

            characters_info.append({
                'name': char.name,
                'serial': getattr(char, 'serial', ''),
                'hp': hp, 'hp_max': hp_max,
                'mp': mp, 'mp_max': mp_max,
                'actions': my_acts,
                'skills': skills,
                'has_eat': has_eat,
                'has_craft': has_craft,
                'has_items': bool(hasattr(char, 'inventory') and char.inventory),
            })

        # Global ops
        global_ops = ['sN', '.i end', '.a m <坐标>']
        if any(c['has_craft'] for c in characters_info):
            global_ops.append('.g')
        if any(c['has_items'] for c in characters_info) or any(c['has_eat'] for c in characters_info):
            global_ops.append('.u')
            global_ops.append('.a eat')

        return jsonify({
            'characters': characters_info,
            'global_ops': global_ops,
        })


@app.route('/api/pvp/<battle_id>/clist', methods=['GET'])
def get_clist(battle_id):
    """Return full character roster with initiative indices."""
    with _lock:
        engine = _battles.get(battle_id)
        if not engine:
            return jsonify({'error': True, 'message': '战斗不存在'}), 404

        state = engine._get_state()
        round_num = state.get('round', 1) if state else 1
        active_idx = state.get('activeIndex', 0) if state else 0

        entries = _build_global_list(engine)
        il = engine._get_initiative()
        controllers = getattr(engine, '_player_controllers', {})

        characters = []
        for e in entries:
            uid = e['userId']
            orig_idx = e.get('origIndex', e['index'] - 1)
            base_uid = il[orig_idx].get('baseUserId', uid) if orig_idx < len(il) else uid
            ctrls = controllers.get(base_uid, [])
            char = engine.get_char(uid)
            characters.append({
                'index': e['index'],
                'name': e['name'],
                'team': e['team'],
                'hp': e['hp'],
                'hp_max': e['hpMax'],
                'mp': e['mp'],
                'mp_max': e['mpMax'],
                'serial': getattr(char, 'serial', ''),
                'is_summon': e['isSummon'],
                'controller_ids': ctrls,
                'is_active': e.get('origIndex', e['index'] - 1) == active_idx,
                'owner_name': next((ie.get('name', '') for ie in il if ie.get('isSummon') and ie['userId'] == uid), ''),
            })

        return jsonify({
            'round': round_num,
            'characters': characters,
        })


@app.route('/api/pvp/<battle_id>/initiative', methods=['GET'])
def get_initiative(battle_id):
    """Return full initiative table grouped by team for .tlist command."""
    with _lock:
        engine = _battles.get(battle_id)
        if not engine:
            return jsonify({'error': True, 'message': '战斗不存在'}), 404

        state = engine._get_state()
        round_num = state.get('round', 1) if state else 1
        entries = _build_global_list(engine)
        il = engine._get_initiative()

        # Group by team and format
        teams = {}
        for e in entries:
            t = e.get('team', '?')
            teams.setdefault(t, []).append(e)

        lines = [f"===== 先攻表（第{round_num}回合）====="]
        team_labels = {'Y': 'Y队', 'X': 'X队'}
        for team in sorted(teams.keys()):
            label = team_labels.get(team, f'{team}队')
            lines.append(f"【{label}】")
            for e in teams[team]:
                serial = e.get('serial', '')
                name_part = f"【{serial}】{e['name']}" if serial else e['name']
                tag = ' (召)' if e['isSummon'] else ''
                rank_text_s = {4: '大成功', 3: '极难成功', 2: '困难成功', 1: '成功',
                              -1: '失败', -2: '大失败'}.get(e.get('initRank', 0), '')
                mp_str = f"  MP:{e.get('mp', '?')}/{e.get('mpMax', '?')}"
                shield = e.get('shield_hp', 0)
                shield_str = f'  🛡{shield}' if shield > 0 else ''
                active = ' ◀ 当前行动' if e.get('isActive') else ''
                lines.append(f"  [{e['index']}] {name_part}{tag} D100={e.get('initRoll', '?')}/DEX={e.get('dex', '?')} {rank_text_s} HP:{e['hp']}/{e['hpMax']}{mp_str}{shield_str}{active}")
                # Show summons under their owner
                owner_serial = serial
                if owner_serial:
                    for se in il:
                        if se.get('isSummon') and se.get('ownerId'):
                            owner_char = engine.get_char(se['ownerId'])
                            owner_s = getattr(owner_char, 'serial', '') if owner_char else ''
                            if owner_s == owner_serial and (engine._get_combat_hp(se['userId']) or 0) > 0:
                                lines.append(f"    └ {se.get('displayName', se.get('name', ''))} HP:{engine._get_combat_hp(se['userId']) or 0}")

        return jsonify({'display': '\n'.join(lines)})


@app.route('/api/pvp/<battle_id>/sync', methods=['POST'])
def sync_state(battle_id):
    """Push HP/MP/SAN changes from JS to Python."""
    data = request.get_json(force=True) or {}
    player_id = data.get('player_id', '')

    with _lock:
        engine = _battles.get(battle_id)
        if not engine:
            return jsonify({'error': True, 'message': '战斗不存在'}), 404

        if player_id and player_id in engine.characters:
            char = engine.get_char(player_id)
            if 'hp' in data:
                engine._set_combat_hp(player_id, data['hp'])
            if 'mp' in data:
                char.set_attr('魔力', data['mp'])
            if 'san' in data:
                char.set_attr('理智', data['san'])
            return jsonify({'status': 'ok'})

        return jsonify({'error': True, 'message': '未指定角色'}), 400


@app.route('/api/pvp/<battle_id>/bind', methods=['POST'])
def pvp_bind(battle_id):
    """Bind a player to a character during battle — immediately grants control.
    Supports binding by serial (Y1-Y12) or global initiative index."""
    data = request.get_json(force=True)
    player_id = data.get('player_id', '')
    serial = data.get('serial', '')
    init_index = data.get('init_index')
    full = data.get('full', False)  # .asfull: also control all summons

    if not player_id:
        return jsonify({'error': True, 'message': '缺少player_id'}), 400

    with _lock:
        engine = _battles.get(battle_id)
        if not engine:
            return jsonify({'error': True, 'message': '战斗不存在'}), 404

        il = engine._get_initiative()
        target_uid = None

        # Find by serial (Y1-Y12) — initiative list first
        if serial:
            for e in il:
                if e.get('isSummon'): continue
                char = engine.get_char(e.get('baseUserId', e['userId']))
                if char and char.serial and char.serial.upper() == serial.upper():
                    target_uid = e['userId']
                    break
            # Fallback: engine.characters (preparation phase, character not yet in initiative)
            if not target_uid:
                for uid, c in engine.characters.items():
                    if getattr(c, 'serial', '') and c.serial.upper() == serial.upper():
                        target_uid = uid
                        break

        # Find by global initiative index
        if not target_uid and init_index is not None:
            if 0 < init_index <= len(il):
                target_uid = il[init_index - 1]['userId']

        if not target_uid:
            return jsonify({'error': True, 'message': f'未找到序号【{serial or init_index}】'}), 404

        # Initialize controllers dict
        if not hasattr(engine, '_player_controllers'):
            engine._player_controllers = {}
        if not hasattr(engine, '_ai_overrides'):
            engine._ai_overrides = {}
        if not hasattr(engine, '_ai_override_state'):
            engine._ai_override_state = {}
        base_uid = target_uid.split('__act')[0] if '__act' in str(target_uid) else target_uid
        if base_uid not in engine._player_controllers:
            engine._player_controllers[base_uid] = []
        if player_id not in engine._player_controllers[base_uid]:
            engine._player_controllers[base_uid].append(player_id)

        # .asfull: mark for full summon control + auto-bind existing summons
        if full:
            if not hasattr(engine, '_full_controllers'):
                engine._full_controllers = {}
            engine._full_controllers[base_uid] = list(engine._player_controllers[base_uid])
            # Auto-bind existing summons
            for e in il:
                if e.get('isSummon') and e.get('ownerId') == base_uid:
                    sum_uid = e['userId']
                    if sum_uid not in engine._player_controllers:
                        engine._player_controllers[sum_uid] = []
                    if player_id not in engine._player_controllers[sum_uid]:
                        engine._player_controllers[sum_uid].append(player_id)

        char_name = engine.get_char(base_uid).name if engine.get_char(base_uid) else base_uid
        serial = getattr(engine.get_char(base_uid), 'serial', '') if engine.get_char(base_uid) else ''

        # Get all characters controlled by this player
        my_characters = []
        for b_uid, player_list in getattr(engine, '_player_controllers', {}).items():
            if player_id in (player_list if isinstance(player_list, list) else []):
                c = engine.get_char(b_uid) if engine.get_char(b_uid) else None
                my_characters.append({
                    'uid': b_uid,
                    'name': c.name if c else b_uid,
                    'serial': getattr(c, 'serial', '') if c else '',
                })

    # If this character is currently active, run AI turns to check if human input needed
    auto_turns = []
    next_player_ids = []
    state = engine._get_state()
    if state and state.get('phase') == 'active':
        il2 = engine._get_initiative()
        idx = state.get('activeIndex', 0)
        if idx < len(il2):
            active_entry = il2[idx]
            active_uid = active_entry.get('baseUserId', active_entry['userId'])
            if active_uid == base_uid:
                try:
                    auto_turns = _run_ai_turns(engine, player_uid=None)
                    # After AI turns, check who controls the next active character
                    st3 = engine._get_state()
                    il3 = engine._get_initiative()
                    if st3 and il3 and st3.get('phase') == 'active':
                        idx3 = st3.get('activeIndex', 0)
                        if idx3 < len(il3):
                            active3 = il3[idx3]
                            base3 = active3.get('baseUserId', active3['userId'])
                            next_player_ids = getattr(engine, '_player_controllers', {}).get(base3, [])
                except Exception:
                    pass

    return jsonify({
        'char_name': char_name,
        'serial': serial,
        'my_characters': my_characters,
        'auto_turns': auto_turns,
        'next_player_ids': next_player_ids,
        'initiative_text': _initiative_list_text(engine),
        'controlled_characters': _get_player_controlled_characters(engine),
    })


@app.route('/api/pvp/<battle_id>/app', methods=['POST'])
def add_character(battle_id):
    """Add a character from chData to the battle. Works in both preparation and active phases."""
    data = request.get_json(force=True)
    serial = data.get('serial', '')
    team = data.get('team', 'Y').upper()
    player_id = data.get('player_id', '')

    if not serial or team not in ('Y', 'X'):
        return jsonify({'error': True, 'message': '用法: .app <魔法少女序号> <Y|X>（如 .app Y5 Y）'}), 400

    with _lock:
        engine = _battles.get(battle_id)
        if not engine:
            return jsonify({'error': True, 'message': '战斗不存在'}), 404
        state = engine._get_state()
        phase = state.get('phase', 'preparation') if state else 'preparation'
        if not state or phase not in ('preparation', 'active'):
            return jsonify({'error': True, 'message': '当前战斗状态不允许添加角色'}), 400

        cdata = _find_chdata_by_serial(serial.upper())
        if not cdata:
            return jsonify({'error': True, 'message': f'未找到角色: {serial}'}), 400

        from characters_data_pvp import load_character_to_engine

        uid = f"app_{serial.upper()}_{random.randint(1000, 9999)}"
        load_character_to_engine(engine, cdata, uid)
        char = engine.get_char(uid)
        char.name = cdata.get('name', serial)
        char.serial = serial.upper()
        if not cdata.get('pre_transformed'):
            engine.process_command(uid, '.hs')

        # ── Auto-placement: team side of the map ──
        mp = engine._get_map()
        w, h = mp.get('width', 10), mp.get('height', 10)
        # Find a default column for this team
        if team == 'Y':
            default_coord = format_coord(0, h // 2)  # left side, center row
        else:
            default_coord = format_coord(w - 1, h // 2)  # right side, center row

        # Find nearest free cell (BFS from default)
        final_coord = default_coord
        if default_coord in mp.get('occupants', {}):
            c = parse_coord(default_coord)
            if c:
                found = False
                for dist in range(1, max(w, h)):
                    for dc in range(-dist, dist + 1):
                        for dr in range(-dist, dist + 1):
                            if abs(dc) + abs(dr) != dist:
                                continue
                            nc = (c[0] + dc, c[1] + dr)
                            nc_str = format_coord(*nc)
                            if 0 <= nc[0] < w and 0 <= nc[1] < h:
                                if nc_str not in mp.get('occupants', {}):
                                    final_coord = nc_str
                                    found = True
                                    break
                        if found: break
                    if found: break
        mp['occupants'][final_coord] = uid
        engine._set_map(mp)

        # ── Initiative & actions ──
        il = engine._get_initiative()
        dex_val = char.get_attr('敏捷', 50)
        init_roll = random.randint(1, 100)
        init_entry = {
            'userId': uid, 'name': cdata.get('name', serial), 'team': team,
            'dex': dex_val,
            'initRoll': init_roll,
            'initRank': success_rank(init_roll, dex_val),
            'coord': final_coord,
            'serial': serial.upper(),
        }
        il.append(init_entry)
        il.sort(key=lambda e: (-e['initRank'], -e['dex'], e['initRoll']))
        engine._set_initiative(il)

        actions = engine._get_actions()
        if phase == 'preparation':
            actions[uid] = {'主动': 0, '附加': 999}
        else:
            actions[uid] = {'主动': 2, '附加': 3}
        engine._set_actions(actions)

        # ── Active phase: init combat HP and auto-cast passives ──
        if phase == 'active':
            hp = char.get_attr('体力', 10)
            engine._init_combat_hp(uid, hp)
            # Auto-cast passive (timing=1) spells
            spells = char.spells or engine.load_spells(uid)
            for s in spells:
                if has_timing(s.get('时机', '2'), '1'):
                    try:
                        target = engine._smart_target(uid, s)
                        engine._execute_spell(uid, target, s)
                    except ReactionNeeded:
                        pass
            # Re-sync dynamic slots after passives (buffs may change AP)
            engine._sync_initiative_slots(uid)

        added = [{'serial': serial.upper(), 'name': cdata.get('name', serial), 'coord': final_coord, 'team': team}]

        return jsonify({
            'added': added,
            'map': engine._get_map(),
            'initiative': il,
            'phase': phase,
        })


@app.route('/api/pvp/<battle_id>/rem', methods=['POST'])
def remove_character(battle_id):
    """Remove a character from the battle. Works in both preparation and active phases.
    In active phase, the character is treated as dead — summons are also removed."""
    data = request.get_json(force=True)
    serial = data.get('serial', '').upper()
    if not serial:
        return jsonify({'error': True, 'message': '缺少魔法少女序号'}), 400

    with _lock:
        engine = _battles.get(battle_id)
        if not engine:
            return jsonify({'error': True, 'message': '战斗不存在'}), 404
        state = engine._get_state()
        phase = state.get('phase', 'preparation') if state else 'preparation'
        if not state or phase not in ('preparation', 'active'):
            return jsonify({'error': True, 'message': '当前战斗状态不允许移除角色'}), 400

        il = engine._get_initiative()
        target_entry = None
        for e in il:
            char = engine.get_char(e['userId'])
            if char and char.serial and char.serial.upper() == serial:
                target_entry = e
                break

        if not target_entry:
            return jsonify({'error': True, 'message': f'未找到角色: {serial}'}), 404

        uid = target_entry['userId']
        base_uid = target_entry.get('baseUserId', uid)
        char_name = engine.get_char(uid).name if engine.get_char(uid) else serial

        # Remove from map (all occupants matching base_uid)
        mp = engine._get_map()
        for c, occ in list(mp.get('occupants', {}).items()):
            if occ == base_uid or occ == uid:
                del mp['occupants'][c]
        engine._set_map(mp)

        if phase == 'active':
            # Treat as death: remove character + all summons + multi-action entries
            engine._remove_summons_of_owner(base_uid)
        else:
            # Preparation phase: simple removal from initiative
            il = [e for e in il
                  if e['userId'] != uid
                  and e.get('baseUserId', e['userId']) != base_uid]
            engine._set_initiative(il)

        # Remove from player controllers
        if hasattr(engine, '_player_controllers'):
            engine._player_controllers.pop(base_uid, None)
            # Also remove any __act variant entries
            for k in list(engine._player_controllers.keys()):
                if '__act' in str(k) and str(k).split('__act')[0] == base_uid:
                    engine._player_controllers.pop(k, None)

        il_after = engine._get_initiative()
        return jsonify({
            'removed': {'serial': serial, 'name': char_name},
            'map': engine._get_map(),
            'initiative': il_after,
        })


@app.route('/api/pvp/<battle_id>/pr', methods=['POST'])
def prepare_ready(battle_id):
    """Transition battle from preparation phase to active combat."""
    with _lock:
        engine = _battles.get(battle_id)
        if not engine:
            return jsonify({'error': True, 'message': '战斗不存在'}), 404

        state = engine._get_state()
        if not state or state.get('phase') != 'preparation':
            return jsonify({'error': True, 'message': '战斗不在准备阶段'}), 400

        il = engine._get_initiative()
        all_uids = [e['userId'] for e in il]
        base_uids = list({e.get('baseUserId', e['userId']) for e in il})

        # ── Activate combat HP ──
        for uid in base_uids:
            char = engine.get_char(uid)
            hp = char.get_attr('体力', 10)
            engine._init_combat_hp(uid, hp)

        # ── Set real actions (per base uid only, multi-action slots share) ──
        base_actions = {}
        for e in il:
            base = e.get('baseUserId', e['userId'])
            if base not in base_actions:
                base_actions[base] = {'主动': 2, '附加': 3}
        engine._set_actions(base_actions)

        # ── Init status ──
        for uid in base_uids:
            char = engine.get_char(uid)
            char.set_attr('状态', random.randint(0, 100))

        # ── Set active phase ──
        engine._set_state({'phase': 'active', 'round': 1, 'activeIndex': 0})

        # ── Auto-cast passive spells ──
        for uid in base_uids:
            spells = engine.get_char(uid).spells or engine.load_spells(uid)
            for s in spells:
                if has_timing(s.get('时机', '2'), '1'):
                    try:
                        target = engine._smart_target(uid, s)
                        engine._execute_spell(uid, target, s)
                    except ReactionNeeded:
                        pass

        # ── Re-sync dynamic action slots after passives (buffs may change AP) ──
        engine._sync_all_initiative_slots()

        # ── Skip to first valid (non-suppressed, alive) entry ──
        engine._skip_to_valid_active()

        # ── Build response ──
        # Use current state + fresh initiative (not stale local il from before _sync)
        st_for_active = engine._get_state()
        il_for_active = engine._get_initiative()
        active_idx = st_for_active.get('activeIndex', 0) if st_for_active else 0
        active_entry = il_for_active[active_idx] if 0 <= active_idx < len(il_for_active) else None
        output_lines = [f"=== 战斗正式开始！==="]
        output_lines.append(engine._get_initiative_display())
        output_lines.append(_initiative_list_text(engine))

        if active_entry:
            announcement = _build_turn_announcement(engine, active_entry['userId'])
            if announcement:
                output_lines.append(announcement)

        result = {
            'output': '\n'.join(output_lines),
            'state': engine._get_state(),
            'map': engine._get_map(),
            'phase': 'active',
        }

        # ── Player controllers info ──
        if getattr(engine, '_player_controllers', None):
            result['controlled_characters'] = _get_player_controlled_characters(engine)
            if active_entry:
                active_base = active_entry.get('baseUserId', active_entry['userId'])
                controllers = getattr(engine, '_player_controllers', {}).get(active_base, [])
                result['next_player_ids'] = controllers

        # ── Run AI turns if first character is AI ──
        if active_entry and not _is_human_controlled(engine, active_entry['userId']):
            Q = _load_q_table_pvp()
            ai_outputs = _run_ai_turns(engine, player_uid=None, Q=Q, start_round=1)
            if ai_outputs:
                result['output'] += '\n\n[AI回合]\n' + '\n'.join(ai_outputs)
            result['state'] = engine._get_state()
            if getattr(engine, '_player_controllers', None):
                il2 = engine._get_initiative()
                st2 = engine._get_state()
                if st2 and il2 and st2.get('phase') == 'active':
                    idx2 = st2.get('activeIndex', 0)
                    if idx2 < len(il2):
                        active_entry2 = il2[idx2]
                        active_base2 = active_entry2.get('baseUserId', active_entry2['userId'])
                        controllers2 = getattr(engine, '_player_controllers', {}).get(active_base2, [])
                        result['next_player_ids'] = controllers2
                        if _is_human_controlled(engine, active_entry2['userId']):
                            result['turn_info'] = _build_turn_announcement(engine, active_entry2['userId'])

        # ── Check for pending reactions ──
        pending = _pending_reactions.get(battle_id)
        if isinstance(pending, list):
            pending = pending[0] if pending else None
        if pending:
            result['needs_reaction'] = True
            result['pending_attack'] = {
                'attacker_name': pending.get('atk_name', ''),
                'defender_name': pending.get('def_name', ''),
                'defender_id': pending.get('def_uid', ''),
                'defender_serial': getattr(engine.get_char(pending.get('def_uid', '')), 'serial', '') if pending.get('def_uid') else '',
                'skill_name': pending.get('skill_name', ''),
                'skill_value': pending.get('skill_val', 0),
                'atk_roll': pending.get('atk_result', 0),
                'atk_rank': pending.get('atk_rank', 0),
                'atk_rank_text': rank_text(pending.get('atk_rank', 0)),
                'dodge_val': pending.get('dodge_val', 0),
                'counter_val': pending.get('counter_val', 0),
                'damage_dice': pending.get('dmg_dice', ''),
                'penetration': pending.get('pen', 0),
            }

        return jsonify(result)


@app.route('/api/pvp/<battle_id>/unbind', methods=['POST'])
def pvp_unbind(battle_id):
    """Remove a player's control of a character (or all characters) during battle."""
    engine = _battles.get(battle_id)
    if not engine:
        return jsonify({'error': True, 'message': '战斗不存在'}), 404

    data = request.get_json(force=True)
    player_id = data.get('player_id', '')
    serial = data.get('serial', '')
    init_index = data.get('init_index')

    if not player_id:
        return jsonify({'error': True, 'message': '缺少player_id'}), 400

    with _lock:
        if not hasattr(engine, '_player_controllers'):
            engine._player_controllers = {}
        if not hasattr(engine, '_ai_overrides'):
            engine._ai_overrides = {}
        if not hasattr(engine, '_ai_override_state'):
            engine._ai_override_state = {}

        unbound_characters = []

        if serial or init_index is not None:
            # Unbind from a specific character
            il = engine._get_initiative()
            target_uid = None

            if serial:
                for e in il:
                    if e.get('isSummon'):
                        continue
                    char = engine.get_char(e.get('baseUserId', e['userId']))
                    if char and char.serial and char.serial.upper() == serial.upper():
                        target_uid = e['userId']
                        break

            if not target_uid and init_index is not None:
                if 0 < init_index <= len(il):
                    target_uid = il[init_index - 1]['userId']

            if not target_uid:
                return jsonify({'error': True, 'message': f'未找到序号【{serial or init_index}】'}), 404

            base_uid = target_uid.split('__act')[0] if '__act' in str(target_uid) else target_uid
            # .unbind: also remove player from all summons if this was .asfull-bound
            full_ctrls = getattr(engine, '_full_controllers', {})
            if base_uid in full_ctrls and player_id in full_ctrls.get(base_uid, []):
                il = engine._get_initiative()
                for e in il:
                    if e.get('isSummon') and e.get('ownerId') == base_uid:
                        sum_uid = e['userId']
                        if sum_uid in engine._player_controllers:
                            s_list = engine._player_controllers[sum_uid]
                            if player_id in s_list:
                                s_list.remove(player_id)
                                if not s_list:
                                    del engine._player_controllers[sum_uid]
                # Remove from _full_controllers for this owner
                f_list = full_ctrls.get(base_uid, [])
                if player_id in f_list:
                    f_list.remove(player_id)
                    if not f_list:
                        del engine._full_controllers[base_uid]

            if base_uid in engine._player_controllers:
                ctrl_list = engine._player_controllers[base_uid]
                if player_id in ctrl_list:
                    ctrl_list.remove(player_id)
                    if not ctrl_list:
                        del engine._player_controllers[base_uid]
                    char_name = engine.get_char(base_uid).name if engine.get_char(base_uid) else base_uid
                    char_serial = getattr(engine.get_char(base_uid), 'serial', '') if engine.get_char(base_uid) else ''
                    unbound_characters.append({'uid': base_uid, 'name': char_name, 'serial': char_serial})
        else:
            # Unbind from ALL characters
            for base_uid in list(engine._player_controllers.keys()):
                ctrl_list = engine._player_controllers[base_uid]
                if player_id in ctrl_list:
                    ctrl_list.remove(player_id)
                    if not ctrl_list:
                        del engine._player_controllers[base_uid]
                    char = engine.get_char(base_uid)
                    unbound_characters.append({
                        'uid': base_uid,
                        'name': char.name if char else base_uid,
                        'serial': getattr(char, 'serial', '') if char else '',
                    })

        # Get remaining controlled characters for this player
        remaining_characters = []
        for b_uid, player_list in getattr(engine, '_player_controllers', {}).items():
            if player_id in (player_list if isinstance(player_list, list) else []):
                c = engine.get_char(b_uid) if engine.get_char(b_uid) else None
                remaining_characters.append({
                    'uid': b_uid,
                    'name': c.name if c else b_uid,
                    'serial': getattr(c, 'serial', '') if c else '',
                })

        return jsonify({
            'unbound': unbound_characters,
            'remaining': remaining_characters,
            'initiative_text': _initiative_list_text(engine),
            'controlled_characters': _get_player_controlled_characters(engine),
        })


@app.route('/api/pvp/<battle_id>/join', methods=['POST'])
def pvp_join(battle_id):
    """Alias for /bind — join a battle by serial or initiative index."""
    return pvp_bind(battle_id)


@app.route('/api/pvp/<battle_id>/setaiact', methods=['POST'])
def pvp_setaiact(battle_id):
    """Set AI override strategy for a character or summon (from .setaiact command).
    Works during preparation phase and active phase.
    Supports pre-configuration: targets not yet in battle can still receive overrides.
    Request: { player_id, target_serial, clear: bool, overrides: {main, addi, react} }
    Overrides format: main/addi = [[[action_str, weight|null], ...], ...], react = [[[d|c|b, weight|null], ...]]
    """
    data = request.get_json(force=True)
    player_id = data.get('player_id', '')
    target_serial = data.get('target_serial', '')
    clear = data.get('clear', False)
    overrides = data.get('overrides', {})

    if not player_id or not target_serial:
        return jsonify({'error': True, 'message': '缺少 player_id 或 target_serial'}), 400

    with _lock:
        engine = _battles.get(battle_id)
        if not engine:
            return jsonify({'error': True, 'message': '战斗不存在'}), 404

        il = engine._get_initiative()
        target_name = target_serial

        # ── Helper: find a character by serial, checking initiative first then engine.characters ──
        def _find_char_uid(serial_str):
            """Return (uid, name) for a character with the given serial, or (None, None)."""
            # 1. Try initiative list (active or prep phase)
            for e in il:
                if e.get('isSummon'):
                    continue
                base = e.get('baseUserId', e['userId'])
                c = engine.get_char(base)
                if c and getattr(c, 'serial', '') and c.serial.upper() == serial_str.upper():
                    return base, c.name
            # 2. Fall back to engine.characters (prep phase, or not yet in initiative)
            for uid, c in engine.characters.items():
                if getattr(c, 'serial', '') and c.serial.upper() == serial_str.upper():
                    return uid, c.name
            return None, None

        # ── Helper: check if player controls a given character uid ──
        def _player_controls(uid):
            ctrls = getattr(engine, '_player_controllers', {}).get(uid, [])
            if player_id in ctrls:
                return True
            # Also check full_controllers
            full_ctrls = getattr(engine, '_full_controllers', {}).get(uid, [])
            if player_id in full_ctrls:
                return True
            return False

        # ── Resolve target ──
        is_preconfig = False  # True when target doesn't exist yet in battle
        if '-' in target_serial:
            # Summon: Yx-N format
            parts = target_serial.split('-')
            owner_serial = parts[0].upper()
            try:
                summon_n = int(parts[1])
            except ValueError:
                return jsonify({'error': True, 'message': f'召唤物编号格式错误: {target_serial}'}), 400
            if summon_n < 1:
                return jsonify({'error': True, 'message': '召唤物编号从1开始'}), 400

            # Find the owner
            owner_uid, owner_name = _find_char_uid(owner_serial)
            if not owner_uid:
                # Owner not yet in battle — allow pre-configuration
                owner_uid = None
                is_preconfig = True
                target_name = f"{owner_serial}-{summon_n}（预配置召唤物，角色未加入）"
            else:
                # Find the N-th summon of this owner (if it already exists)
                summon_count = 0
                found = False
                found_name = None
                for e in il:
                    if e.get('isSummon') and e.get('ownerId') == owner_uid:
                        summon_count += 1
                        if summon_count == summon_n:
                            found = True
                            found_name = e.get('name', target_serial)
                            break
                if found:
                    target_name = f"{owner_serial}-{summon_n}（召唤物: {found_name}）"
                else:
                    is_preconfig = True
                    target_name = f"{owner_serial}-{summon_n}（预配置召唤物）"

            target_key = target_serial.upper()
        else:
            # Character: Yx format
            char_uid, char_name = _find_char_uid(target_serial.upper())
            if char_uid:
                target_name = f"{target_serial.upper()}（{char_name}）"
            else:
                # Character not yet added — allow pre-configuration
                is_preconfig = True
                target_name = f"{target_serial.upper()}（预配置，角色未加入）"
            target_key = target_serial.upper()

        # ── Check authorization ──
        # Pre-configuration for summons: if owner exists, must control the owner.
        # Pre-configuration for summons without owner: skip auth check.
        # Pre-configuration for characters: defer auth — player controls unknown char.
        authorized = False
        if '-' not in target_key:
            # Character target
            char_uid_auth, _ = _find_char_uid(target_key)
            if char_uid_auth:
                authorized = _player_controls(char_uid_auth)
            else:
                # Pre-config for unknown character: allow (will be validated when char joins)
                authorized = True
        else:
            # Summon target: check if player controls the owner
            parts_s = target_key.split('-')
            owner_serial_s = parts_s[0]
            owner_uid_auth, _ = _find_char_uid(owner_serial_s)
            if owner_uid_auth:
                authorized = _player_controls(owner_uid_auth)
                # Also check if summon already exists and player directly controls it
                if not authorized:
                    summon_n_auth = int(parts_s[1])
                    sc = 0
                    for e in il:
                        if e.get('isSummon') and e.get('ownerId') == owner_uid_auth:
                            sc += 1
                            if sc == summon_n_auth:
                                if _player_controls(e['userId']):
                                    authorized = True
                                break
            else:
                # Owner not in battle yet — allow pre-configuration
                authorized = True

        if not authorized:
            return jsonify({'error': True, 'message': f'你没有控制 {target_serial} 的权限'}), 403

        # ── Store override ──
        if not hasattr(engine, '_ai_overrides'):
            engine._ai_overrides = {}

        if clear:
            engine._ai_overrides.pop(target_key, None)
            # Also clear execution state
            if hasattr(engine, '_ai_override_state'):
                keys_to_del = [k for k in engine._ai_override_state if k.startswith(target_key)]
                for k in keys_to_del:
                    del engine._ai_override_state[k]

        if overrides and any(overrides.get(k) for k in ('main', 'addi', 'react')):
            # Validate and store
            valid_override = {}
            for sec in ('main', 'addi', 'react'):
                if sec in overrides and overrides[sec]:
                    valid_override[sec] = overrides[sec]
            if valid_override:
                engine._ai_overrides[target_key] = valid_override
            # Reset execution state for this target
            if hasattr(engine, '_ai_override_state'):
                keys_to_del2 = [k for k in engine._ai_override_state if k.startswith(target_key)]
                for k in keys_to_del2:
                    del engine._ai_override_state[k]

    return jsonify({
        'target_name': target_name,
        'target_serial': target_serial.upper(),
        'clear': clear,
        'preconfig': is_preconfig,
        'overrides': overrides,
    })


@app.route('/api/pvp/<battle_id>/end', methods=['POST'])
def end_battle(battle_id):
    """Clean up a battle instance."""
    with _lock:
        _battles.pop(battle_id, None)
        _pending_reactions.pop(battle_id, None)
    return jsonify({'status': 'ok', 'message': '战斗已清理'})


# ── Image Rendering Endpoint (Feature 7: pictmode) ──
# Ported from logai_server_release.py — standalone, no dependency on logai server.

_PIL_AVAILABLE = False
try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_AVAILABLE = True
except ImportError:
    pass

_FONT_PATH = None
if _PIL_AVAILABLE:
    for _fp in ['C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simhei.ttf',
                 '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
                 '/System/Library/Fonts/PingFang.ttc']:
        if os.path.exists(_fp):
            _FONT_PATH = _fp
            break

def _wrap_line_to_width(line, font, max_width):
    """Wrap a single line into multiple lines based on pixel width.
    Handles CJK characters (which can break at any character) and
    ASCII/word-based text (which prefers breaking at spaces).
    Returns a list of wrapped lines."""
    if not line:
        return ['']
    if font.getlength(line) <= max_width:
        return [line]

    result = []
    current = ''
    for ch in line:
        candidate = current + ch
        if font.getlength(candidate) > max_width:
            if current:
                result.append(current)
            current = ch
        else:
            current = candidate
    if current:
        result.append(current)
    return result


def _render_text_to_images(text, width=900, padding=50):
    """Convert plain/markdown text to a single PNG image. Returns list with one base64 string.
    【分页符】 is NOT handled here — splitting is done by sendTurnMessages in JS.
    =================== is rendered as a visual separator line.
    Long lines are automatically wrapped instead of truncated.
    """
    if not _PIL_AVAILABLE or not _FONT_PATH:
        return None
    try:
        font_normal = ImageFont.truetype(_FONT_PATH, 26)
        font_title = ImageFont.truetype(_FONT_PATH, 38)
    except Exception:
        return None

    text = text.replace('\r', '')
    raw_lines = text.split('\n')

    # First pass: wrap long lines
    max_text_width = width - 2 * padding
    wrapped_lines = []
    for line in raw_lines:
        stripped = line.strip()
        # Separator lines pass through unwrapped
        if stripped.startswith('===') and stripped.endswith('===') and len(stripped) >= 3:
            wrapped_lines.append(line)  # keep original (may have leading spaces)
            continue
        if not stripped:
            wrapped_lines.append('')
            continue
        wrapped = _wrap_line_to_width(line, font_normal, max_text_width)
        wrapped_lines.extend(wrapped)

    # Calculate height
    line_height = 34
    total_h = 80 + len(wrapped_lines) * line_height + 60
    total_h = min(max(total_h, 200), 8000)

    img = Image.new('RGB', (width, total_h), (30, 30, 30))
    draw = ImageDraw.Draw(img)

    # Header bar
    draw.rectangle([(0, 0), (width, 60)], fill=(52, 73, 94))
    draw.text((padding, 15), '战斗记录', font=font_title, fill=(255, 255, 255))

    y = 80
    for line in wrapped_lines:
        if not line.strip():
            y += line_height
            continue
        # Handle =================== panel separator → visual line
        stripped = line.strip()
        if stripped.startswith('===') and stripped.endswith('===') and len(stripped) >= 3:
            draw.line([(padding, y + 15), (width - padding, y + 15)], fill=(100, 100, 100), width=2)
            y += 30
            continue
        draw.text((padding, y), line, font=font_normal, fill=(220, 220, 220))
        y += line_height

    buf = BytesIO()
    img.save(buf, 'PNG')
    buf.seek(0)
    import base64
    return [base64.b64encode(buf.getvalue()).decode('ascii')]

from io import BytesIO

@app.route('/api/render', methods=['POST'])
def render_battle_text():
    """Render battle message text to PNG image. Returns base64-encoded images."""
    data = request.get_json(silent=True) or {}
    text = data.get('text', '')
    print(f'[render] 收到渲染请求 text_len={len(text)} PIL={_PIL_AVAILABLE} font={_FONT_PATH}', flush=True)
    if not _PIL_AVAILABLE:
        print('[render] 失败: PIL/Pillow 不可用', flush=True)
        return jsonify({'error': True, 'message': 'PIL/Pillow not available'}), 500
    if not text:
        print('[render] 失败: 文本为空', flush=True)
        return jsonify({'error': True, 'message': 'No text provided'}), 400
    images = _render_text_to_images(text)
    if images is None:
        print('[render] 失败: 字体未找到或渲染异常', flush=True)
        return jsonify({'error': True, 'message': 'Font not found'}), 500
    print(f'[render] 成功: 生成 {len(images)} 张图片 base64_len={sum(len(img) for img in images)}', flush=True)
    return jsonify({'images': images})


@app.route('/api/reload_chardata', methods=['POST'])
def reload_char_data():
    """Manually reload character data from characters_data_pvp.py without restarting the server."""
    try:
        importlib.invalidate_caches()
        importlib.reload(characters_data_pvp)
        sys.modules['characters_data'] = characters_data_pvp
        char_count = len(characters_data_pvp.ALL_CHARACTERS) if hasattr(characters_data_pvp, 'ALL_CHARACTERS') else 0
        summon_tmpl_count = len(characters_data_pvp.SUMMON_TEMPLATES) if hasattr(characters_data_pvp, 'SUMMON_TEMPLATES') else 0
        msg = f'角色卡数据已重新加载: {char_count} 个角色, {summon_tmpl_count} 个召唤物模板'
        print(f'[reload] {msg}', flush=True)
        _debug_log(msg)
        return jsonify({'ok': True, 'message': msg, 'characters': char_count, 'summon_templates': summon_tmpl_count})
    except Exception as e:
        err = f'角色卡重载失败: {e}'
        print(f'[reload] {err}', flush=True)
        _debug_log(err)
        return jsonify({'ok': False, 'message': err}), 500


# ═══════════════════════════════════════════════════════════════
#  v1.3.0 NEW: Training & Tournament endpoints
# ═══════════════════════════════════════════════════════════════

import uuid, queue

# ── Training job store ──
_train_jobs: dict = {}

def _do_train(job_id: str):
    """Run one PVP training generation in background thread."""
    try:
        _debug_log(f'[train] Job {job_id}: starting PVP training...')
        from ai_trainer import QTrainer
        import characters_data_pvp as _pvp_data
        import battle_engine as _be
        # Ensure PVP summon templates are active in battle_engine
        _be._SUMMON_TEMPLATES = _pvp_data.SUMMON_TEMPLATES

        trainer = QTrainer()
        # init_characters() uses ALL_CHARACTERS from characters_data,
        # which is redirected to characters_data_pvp via sys.modules hack.
        trainer.init_characters()
        trainer.train()
        _debug_log(f'[train] Job {job_id}: training complete')

        from ai_battle import AIController
        with _lock:
            active_battles = len(_battles)
        if active_battles == 0:
            AIController.Q_SOLO = None
            AIController.Q_TEAM = None
            AIController.Q_SUMMON = None
            AIController.load_q_table()
            _debug_log(f'[train] Job {job_id}: Q-table reloaded')
            _train_jobs[job_id] = {'status': 'completed', 'message': '训练完成！Q表已自动重载。'}
        else:
            _train_jobs[job_id] = {'status': 'completed', 'message': '训练完成！检测到活跃战斗，Q表将在下次战斗创建时自动加载。'}
    except Exception as e:
        import traceback
        _debug_log(f'[train] Job {job_id}: FAILED - {e}')
        _train_jobs[job_id] = {'status': 'failed', 'message': f'训练失败: {e}'}


@app.route('/api/train/start', methods=['POST'])
def api_train_start():
    """Start a PVP AI training session in background thread."""
    data = request.get_json(silent=True) or {}
    job_id = str(uuid.uuid4())[:8]
    _train_jobs[job_id] = {'status': 'running', 'message': '训练已启动，预计需数分钟...'}
    thread = threading.Thread(target=_do_train, args=(job_id,), daemon=True)
    thread.start()
    _debug_log(f'[train] Job {job_id}: started')
    return jsonify({'ok': True, 'job_id': job_id, 'message': '训练已启动'})


@app.route('/api/train/<job_id>/status', methods=['GET'])
def api_train_status(job_id):
    """Poll training job status."""
    job = _train_jobs.get(job_id)
    if not job:
        return jsonify({'error': True, 'message': '训练任务未找到'}), 404
    return jsonify({
        'job_id': job_id,
        'status': job.get('status', 'unknown'),
        'message': job.get('message', ''),
    })


# ── Tournament job store ──
_jour_jobs: dict = {}

def _do_jour(job_id: str, enabled_modes: list):
    """Run AI tournament in background, capturing output with phase tags."""
    try:
        _debug_log(f'[jour] Job {job_id}: starting tournament modes={enabled_modes}')

        from ai_battle import Tournament, battle_log as orig_battle_log

        current_phase = ['prelim']

        def wrapped_battle_log(level, msg):
            # Check for stop signal
            if _jour_jobs.get(job_id, {}).get('status') == 'stopped':
                raise KeyboardInterrupt()  # Break out of tournament loop
            phase = current_phase[0]
            s = str(msg)
            # Standings: "预赛结束 — XvX 积分排名"
            if '预赛结束' in s and '积分排名' in s:
                current_phase[0] = 'standings'
            # Bracket: "XvX 淘汰赛抽签"
            elif '淘汰赛抽签' in s:
                current_phase[0] = 'bracket'
            # SF+ detailed
            elif '半决赛' in s:
                current_phase[0] = 'sf_detail'
            elif '决赛' in s or '铜牌赛' in s:
                current_phase[0] = 'sf_detail'
            # KO result: "G1: ..." or "晋级" with Bo/→
            elif '晋级' in s and ('Bo' in s or '→' in s):
                current_phase[0] = 'ko_result'
            # Final
            elif '冠军:' in s or '最终排名' in s or '角色胜率' in s:
                current_phase[0] = 'final'

            _jour_jobs[job_id].setdefault('entries', []).append({
                'type': 'log', 'phase': phase, 'msg': s
            })
            orig_battle_log(level, msg)

        import ai_battle
        ai_battle.battle_log = wrapped_battle_log

        tournament = Tournament(enabled_modes=enabled_modes)
        tournament.init_characters()
        tournament.run_phases(write_results_flag=True)

        ai_battle.battle_log = orig_battle_log

        result_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'result.txt')
        bracket_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bracket_info.txt')
        results = {}
        if os.path.exists(result_path):
            with open(result_path, 'r', encoding='utf-8') as f:
                results['result'] = f.read()
        if os.path.exists(bracket_path):
            with open(bracket_path, 'r', encoding='utf-8') as f:
                results['bracket'] = f.read()

        _jour_jobs[job_id]['status'] = 'completed'
        _jour_jobs[job_id]['results'] = results
        _jour_jobs[job_id].setdefault('entries', []).append({
            'type': 'done', 'phase': 'final', 'msg': '锦标赛完成'
        })
        _debug_log(f'[jour] Job {job_id}: completed')

    except KeyboardInterrupt:
        _debug_log(f'[jour] Job {job_id}: stopped by user')
        _jour_jobs[job_id]['status'] = 'stopped'
        _jour_jobs[job_id].setdefault('entries', []).append({
            'type': 'done', 'phase': 'stopped', 'msg': '锦标赛已停止'
        })
    except Exception as e:
        import traceback
        _debug_log(f'[jour] Job {job_id}: FAILED - {e}')
        _jour_jobs[job_id]['status'] = 'failed'
        _jour_jobs[job_id].setdefault('entries', []).append({
            'type': 'error', 'phase': 'error', 'msg': str(e)
        })


@app.route('/api/jour/start', methods=['POST'])
def api_jour_start():
    """Start an AI tournament in background thread."""
    data = request.get_json(silent=True) or {}
    mode = data.get('mode', 'all')
    mode_map = {'1': ['1v1'], '2': ['2v2'], '3': ['3v3'], 'all': ['1v1', '2v2', '3v3']}
    enabled_modes = mode_map.get(mode, ['1v1', '2v2', '3v3'])
    job_id = str(uuid.uuid4())[:8]
    _jour_jobs[job_id] = {'status': 'running', 'entries': [], 'results': None}
    thread = threading.Thread(target=_do_jour, args=(job_id, enabled_modes), daemon=True)
    thread.start()
    mode_label = {'1': '1v1', '2': '2v2', '3': '3v3', 'all': '1v1+2v2+3v3'}.get(mode, 'all')
    _debug_log(f'[jour] Job {job_id}: started mode={mode_label}')
    return jsonify({'ok': True, 'job_id': job_id, 'message': f'锦标赛已启动 ({mode_label})'})


@app.route('/api/jour/<job_id>/poll', methods=['GET'])
def api_jour_poll(job_id):
    """Poll tournament output entries (consumes them)."""
    job = _jour_jobs.get(job_id)
    if not job:
        return jsonify({'error': True, 'message': '锦标赛任务未找到'}), 404
    entries = list(job.get('entries', []))
    job['entries'] = []
    return jsonify({
        'job_id': job_id,
        'status': job.get('status', 'unknown'),
        'entries': entries,
    })


@app.route('/api/jour/<job_id>/results', methods=['GET'])
def api_jour_results(job_id):
    """Get final tournament results."""
    job = _jour_jobs.get(job_id)
    if not job:
        return jsonify({'error': True, 'message': '锦标赛任务未找到'}), 404
    return jsonify({
        'job_id': job_id,
        'status': job.get('status', 'unknown'),
        'results': job.get('results', {}),
    })


@app.route('/api/jour/<job_id>/stop', methods=['POST'])
def api_jour_stop(job_id):
    """Stop a running tournament."""
    job = _jour_jobs.get(job_id)
    if not job:
        return jsonify({'error': True, 'message': '锦标赛任务未找到'}), 404
    if job.get('status') != 'running':
        return jsonify({'error': True, 'message': '锦标赛任务未在运行中'}), 400
    job['status'] = 'stopped'
    job.setdefault('entries', []).append({
        'type': 'done', 'phase': 'stopped', 'msg': '锦标赛已手动停止'
    })
    _debug_log(f'[jour] Job {job_id}: stopped by user')
    return jsonify({'ok': True, 'message': '锦标赛已停止'})


# ═══════════════════════════════════════════════════════════════
#  v1.3.0: /api/sim — 快速战斗模拟
# ═══════════════════════════════════════════════════════════════

@app.route('/api/sim', methods=['POST'])
def api_sim():
    """Run battle simulation. Accepts same parameters as sim.py CLI, via JSON body."""
    from sim import mode_single, mode_table as sim_mode_table
    import io as _io

    data = request.get_json(silent=True) or {}
    mode = data.get('mode', 'single')

    old_stdout = sys.stdout
    buf = _io.StringIO()
    sys.stdout = buf

    try:
        if mode == 'table':
            fmt = data.get('format', '1v1')
            teams_str = data.get('teams', '')
            n = int(data.get('n', 50))
            map_size = data.get('map_size', '10x10')
            seed = data.get('seed')
            season = data.get('season')

            argv = ['sim.py', '--mode', 'table']
            argv += ['--format', fmt]
            argv += ['-n', str(n)]
            argv += ['-m', map_size]
            if seed is not None: argv += ['-s', str(seed)]
            if season is not None: argv += ['--season', str(season)]
            if teams_str: argv += ['--teams', teams_str]

            sim_mode_table(argv)
        else:
            team_a = data.get('team_a', [])
            team_b = data.get('team_b', [])
            n = int(data.get('n', 100))
            map_size = data.get('map_size', '10x10')
            seed = data.get('seed')
            season = data.get('season')
            verbose = data.get('verbose', False)

            if not team_a or not team_b:
                return jsonify({'error': True, 'message': '需要 team_a 和 team_b 参数。格式: .sim Y5 vs Y1'}), 400

            argv = ['sim.py'] + list(team_a) + ['vs'] + list(team_b)
            if n != 100: argv += ['-n', str(n)]
            if map_size != '10x10': argv += ['-m', map_size]
            if seed is not None: argv += ['-s', str(seed)]
            if season is not None: argv += ['--season', str(season)]
            if verbose: argv += ['--verbose']

            mode_single(argv, verbose=verbose)

        output = buf.getvalue()
        return jsonify({'ok': True, 'output': output})
    except SystemExit:
        output = buf.getvalue()
        return jsonify({'ok': True, 'output': output or '模拟完成（无输出）'})
    except Exception as e:
        import traceback
        err_output = buf.getvalue()
        _debug_log(f'[sim] Error: {e}\n{traceback.format_exc()[:500]}')
        return jsonify({'error': True, 'message': str(e), 'output': err_output}), 500
    finally:
        sys.stdout = old_stdout


# ── Main ──
if __name__ == '__main__':
    import logging
    # Log Flask app errors to console so we can see tracebacks
    app_logger = logging.getLogger('flask.app')
    app_logger.setLevel(logging.ERROR)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    app_logger.addHandler(handler)
    # Suppress noisy Werkzeug access logs (keep error logs)
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    # ── 启动时立即重加载角色卡数据（确保最新） ──
    try:
        importlib.invalidate_caches()
        importlib.reload(characters_data_pvp)
        sys.modules['characters_data'] = characters_data_pvp
        char_count_init = len(characters_data_pvp.ALL_CHARACTERS) if hasattr(characters_data_pvp, 'ALL_CHARACTERS') else 0
        sum_tmpl_init = len(characters_data_pvp.SUMMON_TEMPLATES) if hasattr(characters_data_pvp, 'SUMMON_TEMPLATES') else 0
        print(f'[reload] 启动时角色卡数据已加载: {char_count_init} 个角色, {sum_tmpl_init} 个召唤物模板')
    except Exception as e:
        print(f'[reload] 启动时角色卡加载失败: {e}')

    # ── Periodic character data reload (every 5 minutes) ──
    def _reload_char_data_periodically():
        while True:
            time.sleep(300)  # 5 minutes
            try:
                importlib.invalidate_caches()
                importlib.reload(characters_data_pvp)
                sys.modules['characters_data'] = characters_data_pvp
                char_count = len(characters_data_pvp.ALL_CHARACTERS) if hasattr(characters_data_pvp, 'ALL_CHARACTERS') else 0
                print(f'[reload] 角色卡数据已重新加载 ({char_count} 个角色)')
            except Exception as e:
                print(f'[reload] 角色卡重载失败: {e}')

    reload_thread = threading.Thread(target=_reload_char_data_periodically, daemon=True)
    reload_thread.start()

    print('Starting autocombat PvP battle server on http://0.0.0.0:8889')
    print(f'[render] PIL可用={_PIL_AVAILABLE} 字体路径={_FONT_PATH} ({"就绪" if _PIL_AVAILABLE and _FONT_PATH else "未就绪，图片渲染将不可用"})')
    # Log loaded character data
    char_count = len(characters_data_pvp.ALL_CHARACTERS) if hasattr(characters_data_pvp, 'ALL_CHARACTERS') else 0
    summon_tmpl_count = len(characters_data_pvp.SUMMON_TEMPLATES) if hasattr(characters_data_pvp, 'SUMMON_TEMPLATES') else 0
    char_names = [c.get('name', c.get('serial', '?')) for c in characters_data_pvp.ALL_CHARACTERS] if hasattr(characters_data_pvp, 'ALL_CHARACTERS') else []
    print(f'[chardata] 已加载 {char_count} 个角色: {", ".join(char_names)}')
    print(f'[chardata] 已加载 {summon_tmpl_count} 个召唤物模板')
    app.run(host='0.0.0.0', port=8889, debug=False, threaded=True)
