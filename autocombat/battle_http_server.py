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

import sys, os, json, uuid, threading, random, math, time
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
#  Debug logging to file
# ═══════════════════════════════════════════════════════════════
_DEBUG_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'battle_debug.log')
_DEBUG_LOCK = threading.Lock()

def _debug_log(msg: str):
    """Append a timestamped message to the debug log file."""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    line = f"[{ts}] {msg}"
    with _DEBUG_LOCK:
        try:
            with open(_DEBUG_LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
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
import characters_data_pvp
sys.modules['characters_data'] = characters_data_pvp

from flask import Flask, request, jsonify

# Now import from battle_engine — its lazy imports get characters_data_pvp
from battle_engine import (
    FullBattleEngine, CombatEngine,
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
    runtime = ['体力','体力上限','魔力','魔力上限','理智','行动力','体格','等级',
               '回合行动数','闪避','伤害贯穿','可反击','状态']
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
    """Return {player_id: [{uid, name, serial}, ...]} mapping for all human controllers."""
    controllers = getattr(engine, '_player_controllers', {})
    result: dict = {}
    for base_uid, player_list in controllers.items():
        if not isinstance(player_list, list):
            continue
        # Use character's real name (not initiative entry name which may have action labels)
        char = engine.get_char(base_uid)
        name = char.name if char else base_uid
        serial = getattr(char, 'serial', '') if char else ''
        for pid in player_list:
            if pid not in result:
                result[pid] = []
            result[pid].append({'uid': base_uid, 'name': name, 'serial': serial})
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
    if active_entry and actions:
        current_turn = {
            'userId': active_uid,
            'name': active_entry.get('name', '?'),
            'actions': actions.get(active_uid, {'主动': 0, '附加': 0}),
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
            return (def_uid, atk_uid, lines)

        dodge_val = dchar.get_attr("闪避", 25)
        bmn, bmv = dchar.get_best_melee()
        dodge_val = self._apply_buff_skill_mod(def_uid, dodge_val)
        bmv = self._apply_buff_skill_mod(def_uid, bmv)
        def_buffs = self._get_active_buffs(def_uid)
        def_bp = _calc_net_bp(def_buffs, "", skill_name)

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
                                 can_dodge, can_counter, can_block=False, atk_rank=4, prefix_lines=None):
        """PvP override: raise ReactionNeeded for human defenders.
        For AI defenders, delegate to base (auto-resolve)."""
        dchar = self.get_char(target_id)
        def_base = target_id.split('__act')[0] if '__act' in str(target_id) else target_id
        def_controllers = getattr(self, '_player_controllers', {}).get(def_base, [])

        if not def_controllers:
            # AI defender -> delegate to base FullBattleEngine auto-resolve
            return super()._trigger_spell_reaction(
                caster_id, target_id, eff, spell, dmg_dice,
                can_dodge, can_counter, can_block, atk_rank, prefix_lines=prefix_lines)

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
        can_physically_block = can_physically_block and block_hp > 0
        # 玄武/三合一: 即使法术标记为不可反应/不可格挡，仍可格挡
        can_block_eff = can_physically_block and (can_block or ignore_unreact)

        def_buffs = self._get_active_buffs(target_id)
        def_bp = _calc_net_bp(def_buffs, "", "")

        raise ReactionNeeded(
            reaction_type='spell',
            atk_uid=caster_id, def_uid=target_id,
            atk_name=achar.name, def_name=dchar.name,
            atk_rank=atk_rank, atk_result=1,       # spell auto-success rank=4
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

    def _create_summon(self, caster_id, template_name):
        """Override: auto-bind new summons to players who .asfull the caster."""
        summon_id = super()._create_summon(caster_id, template_name)
        if summon_id:
            full_ctrls = getattr(self, '_full_controllers', {})
            if caster_id in full_ctrls:
                if not hasattr(self, '_player_controllers'):
                    self._player_controllers = {}
                self._player_controllers[summon_id] = list(full_ctrls[caster_id])
        return summon_id


# ═══════════════════════════════════════════════════════════════
#  PvE AI helpers
# ═══════════════════════════════════════════════════════════════

def _load_q_table_pvp():
    """Try to load PvP Q-learning weights for AI opponents.
    Returns dict: {'solo': Q_solo, 'team': Q_team, 'summon': Q_summon}
    """
    try:
        weight_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ai_weights_pvp.json')
        if not os.path.exists(weight_path):
            return None
        with open(weight_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        from collections import defaultdict

        def parse_qdict(qdict):
            Q = defaultdict(lambda: defaultdict(float))
            for ck, entries in qdict.items():
                for key, val in entries.items():
                    parts = key.split('__')
                    state_str, action = parts[0], parts[1]
                    state = tuple(int(v) for v in state_str.split('|'))
                    if len(state) < 16:
                        padding_needed = 16 - len(state)
                        state = state + (0,) * padding_needed
                    Q[ck][(state, action)] = val
            return Q

        if 'Q_solo' in data and 'Q_team' in data:
            return {
                'solo': parse_qdict(data['Q_solo']),
                'team': parse_qdict(data['Q_team']),
                'summon': parse_qdict(data.get('Q_summon', {})),
            }
        elif 'Q' in data:
            Q = parse_qdict(data['Q'])
            return {'solo': Q, 'team': Q, 'summon': defaultdict(lambda: defaultdict(float))}
        return None
    except Exception:
        return None


def _ai_decide_action(engine, uid: str, Q=None) -> str:
    """Decide an AI action for the given character. Returns a command string like '.s2' or '.s0'.
    Q should be dict: {'solo': Q_solo, 'team': Q_team, 'summon': Q_summon}
    """
    char = engine.get_char(uid)
    spells = char.spells or engine.load_spells(uid)

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
        return '.i end'

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
                        if base_ak == 'BASIC_ATTACK': return '.s0'
                        if base_ak == 'MOVE_TOWARD': return '.s0'
                        if base_ak == 'EAT_CAKE':
                            # Only eat if character has healing items
                            if engine._has_healing_item(uid):
                                return '.a eat'
                        if base_ak == 'GIVE_CAKE':
                            # Only give if character has healing items
                            if engine._has_healing_item(uid):
                                return '.a give'
                        if base_ak.startswith('SKILL_'):
                            sn = int(base_ak.split('_')[1])
                            # Validate: skill must be usable as a main action (timing 2)
                            spell = next((s for s in spells if s['index'] == sn), None)
                            if spell and has_timing(spell.get('时机', '2'), '2'):
                                return f'.s{sn}'
                            # Invalid for main action → fall through to random fallback
        except Exception:
            pass

    # Fallback: random among available
    return random.choice(available)


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
    max_steps = 50  # safety limit
    last_round = start_round

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
            _debug_log(f"_run_ai_turns: BREAK human turn — {entry.get('name', uid)} team={entry.get('team', '?')}")
            break

        # Round start display
        current_round = state.get('round', 1)
        if current_round != last_round:
            last_round = current_round
            outputs.append('\n' + engine._get_initiative_display())

        # Turn announcement
        outputs.append(_build_turn_announcement(engine, uid))

        # Check HP
        hp = engine._get_combat_hp(uid) or 0
        if hp <= 0:
            engine._end_turn(uid)
            outputs.append(f"{entry.get('displayName', entry.get('name', uid))} 已阵亡，跳过回合。")
            continue

        # Summon: use Q-table if available, else auto-attack
        if entry.get('isSummon'):
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
                                engine._end_turn(uid)
                                outputs.append(f"召唤物 {entry.get('name', uid)} Q-行动: {an}")
                                if summon_lines:
                                    outputs.append("\n".join(summon_lines))
                                continue
                    except Exception:
                        pass
            summon_lines = engine._summon_attack(uid)
            engine._end_turn(uid)
            if summon_lines:
                outputs.append(f"召唤物 {entry.get('name', uid)}：\n" + "\n".join(summon_lines))
            else:
                outputs.append(f"召唤物 {entry.get('name', uid)} 自动攻击。")
            continue

        # AI decides and executes
        try:
            cmd = _ai_decide_action(engine, base_uid, Q)
        except Exception:
            cmd = '.s0'

        if cmd == '.i end':
            engine._end_turn(uid)
            # Round/turn display will be generated at the start of the next iteration
        elif cmd.startswith('.a '):
            # ── Additional action (.a eat / .a give / .a m) ──
            sub = cmd[3:]
            actions = engine._get_actions()
            my_acts = actions.get(uid, {'主动': 0, '附加': 0})
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
                my_acts['附加'] = max(0, my_acts.get('附加', 0) - 1)
                engine._set_actions(actions)
            elif sub.startswith('give'):
                # .a give <target_name>
                parts = sub.split()
                if len(parts) >= 2:
                    target_name = ' '.join(parts[1:])
                    target_entry = next((e for e in il if e.get('name', '') == target_name), None)
                    if target_entry:
                        result_text, success = engine._eat_cake(uid, target_entry['userId'])
                        outputs.append(result_text)
                        my_acts['附加'] = max(0, my_acts.get('附加', 0) - 1)
                        engine._set_actions(actions)
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
                outputs.append(f"{entry.get('name', uid)} 移动至 {coord}")
                my_acts['附加'] = max(0, my_acts.get('附加', 0) - 1)
                engine._set_actions(actions)
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
                        _, _, lines = engine._coc7_attack(uid, enemies[0]["userId"], bn, bv, dd, p, l)
                        outputs.append("\n".join(lines))
                    except ReactionNeeded as e:
                        def_uid = e.data.get('def_uid', '')
                        # Check if defender is human (in human_ids or has controllers)
                        def_base = def_uid.split('__act')[0] if '__act' in str(def_uid) else def_uid
                        def_controllers = getattr(engine, '_player_controllers', {}).get(def_base, [])
                        is_human_defender = def_uid in human_ids or bool(def_controllers)
                        if is_human_defender:
                            # ── Human defender → store pending reaction, stop AI turns ──
                            e.data['battle_id'] = engine.group_id
                            e.data['target_uid'] = def_uid
                            _pending_reactions[engine.group_id] = e.data
                            outputs.append('\n'.join(e.data['prefix_lines']))
                            outputs.append(f"\n@{e.data['def_name']} 请做出反应：\n.e 闪避 / .e c 反击")
                            # Note: action is NOT decremented here — submit_reaction handles it
                            # after the human player's reaction choice is resolved.
                            return outputs  # stop and let caller handle the pending reaction
                        else:
                            # ── AI defender → auto-resolve reaction ──
                            import random as _random
                            choice = _random.choice(['dodge', 'counter'])
                            _, _, lines = engine.resolve_reaction(e.data, choice)
                            outputs.append("\n".join(lines))
                    # Decrement main action after basic attack (only if attack was made)
                    actions = engine._get_actions()
                    my_acts = actions.get(uid, {'主动': 0, '附加': 0})
                    my_acts['主动'] = max(0, my_acts.get('主动', 0) - 1)
                    engine._set_actions(actions)
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
                        my_acts = actions.get(uid, {'主动': 0, '附加': 0})
                        if my_acts.get('附加', 0) > 0:
                            out = engine._execute_spell(uid, engine._smart_target(uid, spell), spell)
                            if isinstance(out, str):
                                outputs.append(out)
                            my_acts['附加'] = max(0, my_acts.get('附加', 0) - 1)
                            engine._set_actions(actions)
                else:
                    try:
                        out = engine._use_skill(uid, sn, '')
                    except ReactionNeeded as e:
                        def_uid_rn = e.data.get('def_uid', '')
                        def_base_rn = def_uid_rn.split('__act')[0] if '__act' in str(def_uid_rn) else def_uid_rn
                        def_controllers_rn = getattr(engine, '_player_controllers', {}).get(def_base_rn, [])
                        is_human_defender_rn = def_uid_rn in human_ids or bool(def_controllers_rn)
                        if is_human_defender_rn:
                            e.data['battle_id'] = engine.group_id
                            _pending_reactions[engine.group_id] = e.data
                            outputs.append('\n'.join(e.data.get('prefix_lines', [])))
                            opts_rn = []
                            if e.data.get('can_dodge', True):
                                opts_rn.append('.e \u95ea\u907f / .e d')
                            if e.data.get('can_counter', True):
                                opts_rn.append('.e \u53cd\u51fb / .e c')
                            react_prompt_rn = '\n' + '  '.join(opts_rn)
                            outputs.append(f"\n@{e.data['def_name']} \u8bf7\u505a\u51fa\u53cd\u5e94\uff1a\n{react_prompt_rn}")
                            return outputs
                        else:
                            import random as _random_rn
                            opts_rn2 = []
                            if e.data.get('can_dodge', True):
                                opts_rn2.append('dodge')
                            if e.data.get('can_counter', True):
                                opts_rn2.append('counter')
                            choice_rn = _random_rn.choice(opts_rn2) if opts_rn2 else 'dodge'
                            dodged_rn, countered_rn, lines_rn = engine.resolve_spell_reaction(e.data, choice_rn)
                            outputs.append('\n'.join(lines_rn))
                        out = ''
                    if isinstance(out, str) and out:
                        outputs.append(out)
                    is_passive_only = has_timing(spell.get('时机', '2'), '1') and not has_timing(spell.get('时机', '2'), '2')
                    if not is_passive_only:
                        actions = engine._get_actions()
                        my_acts = actions.get(uid, {'主动': 0, '附加': 0})
                        my_acts['主动'] = max(0, my_acts.get('主动', 0) - 1)
                        engine._set_actions(actions)
        else:
            out = engine._end_turn(uid)
            if isinstance(out, str):
                outputs.append(out)

        # After action, check if turn should end
        actions = engine._get_actions()
        my_acts = actions.get(uid, {'主动': 0, '附加': 0})
        if my_acts.get('主动', 0) <= 0:
            try:
                engine._end_turn(uid)
            except Exception:
                pass

    return outputs


# ═══════════════════════════════════════════════════════════════
#  Initiative & target numbering helpers
# ============================================================
#  All characters in initiative order are assigned a global 1-based
#  number.  Targeting uses this number (e.g. .s0 3 = attack #3).
# ============================================================

def _build_global_list(engine) -> list:
    """Return ALL living characters in initiative order, each with a global index.

    Each entry: {index, userId, name, serial, team, hp, hpMax, mp, mpMax, san,
                 isSummon, initRoll, dex, isActive}
    """
    il = engine._get_initiative()
    state = engine._get_state()
    active_idx = state.get('activeIndex', 0) if state else 0
    result = []
    for i, e in enumerate(il):
        uid = e['userId']
        hp = engine._get_combat_hp(uid)
        if hp is None:
            continue
        if hp <= 0 and not engine._is_dying(uid):
            continue
        char = engine.get_char(uid)
        result.append({
            'index': len(result) + 1,
            'origIndex': i,
            'userId': uid,
            'name': e.get('name', uid),
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
            'isActive': (i == active_idx),
        })
    return result


def _fmt_char_name(engine, entry: dict) -> str:
    """Format a character as 【序号】名称.  Falls back to bare name if no serial."""
    uid = entry.get('userId', '')
    if uid:
        char = engine.get_char(uid)
        serial = getattr(char, 'serial', '') if char else ''
        if serial:
            return f"【{serial}】{entry.get('name', uid)}"
    return entry.get('name', '?')


def _initiative_list_text(engine) -> str:
    """Formatted initiative order with global numbers (all characters).

    Uses engine.team_label_mode to decide team tag style:
      'neutral' → [Y队]/[X队]   (multi_pvp)
      'friendly' → [己方]/[敌方]  (PvE, legacy pvp)
    """
    entries = _build_global_list(engine)
    if not entries:
        return '\n（无存活角色）'
    lines = ['\n=== 先攻顺序 ===']
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


def _enemy_list_text(engine, player_uid: str) -> str:
    """Enemy-only list using the same global initiative numbers."""
    entries = _build_global_list(engine)
    player_entry = next((e for e in engine._get_initiative() if e['userId'] == player_uid), None)
    player_team = player_entry.get('team', 'Y') if player_entry else 'Y'
    enemies = [e for e in entries if e['team'] != player_team]
    # Use neutral labels ('敌方' vs '对方') for multi_pvp; friendly for PvE
    label_mode = getattr(engine, 'team_label_mode', 'friendly')
    enemy_label = '对方' if label_mode == 'neutral' else '敌方'
    if not enemies:
        return f'\n=== {enemy_label}已全部阵亡 ==='
    lines = [f'\n=== {enemy_label}目标 ===']
    for e in enemies:
        tag = ' [召唤]' if e['isSummon'] else ''
        serial = e.get('serial', '')
        name_part = f"【{serial}】{e['name']}" if serial else e['name']
        lines.append(f"  [{e['index']}] {name_part}{tag}  HP:{e['hp']}/{e['hpMax']}")
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
    actions = engine._get_actions()
    my_acts = actions.get(uid, {'主动': 0, '附加': 0})

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
        lines.append('=== 可用行动 ===')

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

    # Enemy list (always show for both human and AI)
    enemy_text = _enemy_list_text(engine, uid)
    if enemy_text:
        lines.append(enemy_text)

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
                n_actions = char.get_attr('回合行动数', 1)
                for ai in range(n_actions):
                    entry_id = uid if ai == 0 else f"{uid}__act{ai}"
                    row = min(h - 1, math.ceil(h / 2) + i - len(team_a_uids) // 2)
                    coord = format_coord(1, row)
                    label = f" (行动{ai+1})" if ai > 0 else ""
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
                n_actions = char.get_attr('回合行动数', 1)
                for ai in range(n_actions):
                    entry_id = uid if ai == 0 else f"{uid}__act{ai}"
                    row = min(h - 1, math.ceil(h / 2) + i - len(team_b_uids) // 2)
                    coord = format_coord(w - 2, row)
                    label = f" (行动{ai+1})" if ai > 0 else ""
                    dex_val = char.get_attr('敏捷', 50)
                    init_roll = random.randint(1, 100)
                    il.append({'userId': entry_id, 'baseUserId': uid,
                               'name': char.name + label, 'actionIdx': ai, 'team': 'X',
                               'dex': dex_val, 'initRoll': init_roll,
                               'initRank': success_rank(init_roll, dex_val),
                               'coord': coord, 'serial': char.serial})
                    if ai == 0:
                        map_data['occupants'][coord] = uid
            il.sort(key=lambda e: (-e['initRank'], -e['dex'], -e['initRoll']))
            engine._set_map(map_data)
            engine._set_initiative(il)
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
                    n_actions = char.get_attr('回合行动数', 1)
                    for ai in range(n_actions):
                        entry_id = uid if ai == 0 else f"{uid}__act{ai}"
                        row = min(h - 1, math.ceil(h / 2) + i - len(team_a) // 2)
                        coord = format_coord(1, row)
                        label = f" (行动{ai+1})" if ai > 0 else ""
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
                    n_actions = char.get_attr('回合行动数', 1)
                    for ai in range(n_actions):
                        entry_id = uid if ai == 0 else f"{uid}__act{ai}"
                        row = min(h - 1, math.ceil(h / 2) + i - len(team_b) // 2)
                        coord = format_coord(w - 2, row)
                        label = f" (行动{ai+1})" if ai > 0 else ""
                        dex_val = char.get_attr('敏捷', 50)
                        init_roll = random.randint(1, 100)
                        il.append({'userId': entry_id, 'baseUserId': uid,
                                   'name': char.name + label, 'actionIdx': ai, 'team': 'X',
                                   'dex': dex_val, 'initRoll': init_roll,
                                   'initRank': success_rank(init_roll, dex_val),
                                   'coord': coord})
                        if ai == 0:
                            map_data['occupants'][coord] = uid
                il.sort(key=lambda e: (-e['initRank'], -e['dex'], -e['initRoll']))
                engine._set_map(map_data)
                engine._set_initiative(il)
                init_list = il
            else:
                engine._set_map(map_data)
                engine._set_initiative(init_list)
                il = init_list

        # ── Player controllers for pvp mode (each character's userId is the player) ──
        if mode == 'pvp' and not getattr(engine, '_player_controllers', None):
            engine._player_controllers = {}
            for uid in all_uids:
                engine._player_controllers[uid] = [uid]

        # ── Store battle (preparation phase by default) ──
        _battles[group_id] = engine
        _pending_reactions.pop(group_id, None)

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
        output_lines.append("\n准备阶段 — 可以 .as/.unbind 绑定解绑，.a m 移动位置，.app <序号> <Y|X> 添加角色，.rem <序号> 移除角色。（.app/.rem 战斗开始后也可使用，.rem 视同死亡）")
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

    with _lock:
        engine = _battles.get(battle_id)
        if not engine:
            return jsonify({'error': True, 'message': '战斗不存在，请重新开始'}), 404

        # Check pending reaction
        pending = _pending_reactions.get(battle_id)
        if pending:
            def_uid = pending.get('def_uid', '')
            target_uid = pending.get('target_uid', '')
            controllers = getattr(engine, '_player_controllers', {}).get(def_uid, [])
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

        actions = engine._get_actions()
        my_acts = actions.get(entry_uid, {'主动': 0, '附加': 0})

        output = ''
        needs_reaction = False
        pending_attack = None
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
                my_acts['主动'] = 0
                my_acts['附加'] = 0
                engine._set_actions(actions)
                output = engine._end_turn(entry_uid)
                if isinstance(output, str):
                    pass
                else:
                    output = f"{char_name} 结束回合（剩余主动×{remaining_main} 附加×{remaining_extra}）"
                turn_advanced = True

            elif action == '.s0':
                # ── Basic melee attack ──
                if my_acts.get('主动', 0) <= 0:
                    return jsonify({'error': True, 'message': '主动作次数已用尽！'}), 400
                char = engine.get_char(effective_uid)
                bn, bv = char.get_best_melee()
                # Find target — check in order: explicit userId, serial from args, numeric index from args, first enemy
                enemies = [e for e in il
                           if e.get('team') != active_entry.get('team', 'Y')
                           and (engine._get_combat_hp(e['userId']) or 0) > 0
                           and not engine._is_untargetable(e['userId'])]
                if not enemies:
                    return jsonify({'error': True, 'message': '没有可攻击的敌人'}), 400

                tid = None
                # 1) Explicit target userId (from @mention)
                if target and any(e['userId'] == target for e in enemies):
                    tid = target
                # 2) Serial number from args (e.g. .s0 Y5)
                if not tid:
                    tid = _resolve_target_by_serial(engine, effective_uid, args)
                # 3) Numeric index from args (e.g. .s0 1)
                if not tid:
                    tgt_idx = _parse_target_index(args)
                    if tgt_idx is not None:
                        tid = _resolve_enemy_index(engine, effective_uid, tgt_idx)
                        if not tid:
                            return jsonify({'error': True, 'message': f'无效的敌方编号: {tgt_idx}'}), 400
                # 4) Default: first living enemy (only when no explicit target given)
                if not tid:
                    if args and args.strip():
                        return jsonify({'error': True, 'message': '目标不可选中或无效，请重新选择'}), 400
                    tid = enemies[0]['userId']

                dd = engine._get_damage_dice(effective_uid, bn)
                p = char.get_attr('伤害贯穿', 1)
                l = char.get_attr('致死骰', 1) or 0

                try:
                    _, _, lines = engine._coc7_attack(effective_uid, tid, bn, bv, dd, p, l)
                    output = '\n'.join(lines)
                    my_acts['主动'] = my_acts.get('主动', 0) - 1
                except ReactionNeeded as e:
                    e.data['battle_id'] = battle_id
                    e.data['target_uid'] = tid
                    # ── Determine if defender is human-controlled ──
                    def_uid = e.data.get('def_uid', '')
                    if not _is_human_controlled(engine, def_uid):
                        # AI defender → auto-choose dodge or counter (50/50, matches parent class default)
                        import random as _random
                        choice = _random.choice(['dodge', 'counter'])
                        _, _, lines = engine.resolve_reaction(e.data, choice)
                        output = '\n'.join(lines)
                        my_acts['主动'] = my_acts.get('主动', 0) - 1
                    else:
                        # Human defender → store pending reaction for player input
                        def_char = engine.get_char(def_uid)
                        defender_serial = getattr(def_char, 'serial', '') if def_char else ''
                        pending_attack = {
                            'attacker_name': e.data['atk_name'],
                            'defender_name': e.data['def_name'],
                            'defender_id': e.data['def_uid'],
                            'defender_serial': defender_serial,
                            'skill_name': e.data['skill_name'],
                            'skill_value': e.data['skill_val'],
                            'atk_roll': e.data['atk_result'],
                            'atk_rank': e.data['atk_rank'],
                            'atk_rank_text': rank_text(e.data['atk_rank']),
                            'dodge_val': e.data['dodge_val'],
                            'counter_val': e.data['counter_val'],
                            'damage_dice': e.data['dmg_dice'],
                            'penetration': e.data['pen'],
                        }
                        _pending_reactions[battle_id] = e.data
                        needs_reaction = True
                        output = '\n'.join(e.data['prefix_lines'])
                        output += f"\n@{e.data['def_name']} 请做出反应：\n.e 闪避 / .e c 反击"

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

                try:
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
                        if args_stripped:
                            return jsonify({'error': True, 'message': '目标不可选中或无效，请重新选择'}), 400
                        tgt = engine._smart_target(effective_uid, spell)
                    out = engine._execute_spell(effective_uid, tgt, spell)
                    output = out if isinstance(out, str) else str(out)
                    if not is_passive:
                        my_acts['主动'] = my_acts.get('主动', 0) - 1
                except ReactionNeeded as e:
                    e.data['battle_id'] = battle_id
                    def_uid_sp = e.data.get('def_uid', '')
                    if not _is_human_controlled(engine, def_uid_sp):
                        # AI defender -> auto-resolve spell reaction
                        import random as _random_sp
                        opts = []
                        if e.data.get('can_dodge', True):
                            opts.append('dodge')
                        if e.data.get('can_counter', True):
                            opts.append('counter')
                        choice_sp = _random_sp.choice(opts) if opts else 'dodge'
                        dodged_sp, countered_sp, lines_sp = engine.resolve_spell_reaction(e.data, choice_sp)
                        output = '\n'.join(lines_sp)
                        if not is_passive:
                            my_acts['主动'] = my_acts.get('主动', 0) - 1
                    else:
                        # Human defender -> store pending reaction
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
                            'can_dodge': e.data.get('can_dodge', True),
                            'can_counter': e.data.get('can_counter', True),
                        }
                        _pending_reactions[battle_id] = e.data
                        needs_reaction = True
                        opts_prompt = []
                        if e.data.get('can_dodge', True):
                            opts_prompt.append('.e \u95ea\u907f / .e d')
                        if e.data.get('can_counter', True):
                            opts_prompt.append('.e \u53cd\u51fb / .e c')
                        react_prompt = '\n' + '  '.join(opts_prompt)
                        output = '\n'.join(e.data.get('prefix_lines', []))
                        output += f"\n@{e.data['def_name']} \u8bf7\u505a\u51fa\u53cd\u5e94\uff1a\n{react_prompt}"

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
                    my_acts['附加'] = my_acts.get('附加', 0) - 1
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
                        my_acts['附加'] = my_acts.get('附加', 0) - 1
                    except ReactionNeeded as e:
                        e.data['battle_id'] = battle_id
                        def_uid_as = e.data.get('def_uid', '')
                        if not _is_human_controlled(engine, def_uid_as):
                            # AI defender -> auto-resolve spell reaction
                            import random as _random_as
                            opts_as = []
                            if e.data.get('can_dodge', True):
                                opts_as.append('dodge')
                            if e.data.get('can_counter', True):
                                opts_as.append('counter')
                            choice_as = _random_as.choice(opts_as) if opts_as else 'dodge'
                            dodged_as, countered_as, lines_as = engine.resolve_spell_reaction(e.data, choice_as)
                            output = '\n'.join(lines_as)
                            my_acts['附加'] = my_acts.get('附加', 0) - 1
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
                            _pending_reactions[battle_id] = e.data
                            needs_reaction = True
                            opts_prompt_as = []
                            if e.data.get('can_dodge', True):
                                opts_prompt_as.append('.e 闪避 / .e d')
                            if e.data.get('can_counter', True):
                                opts_prompt_as.append('.e 反击 / .e c')
                            react_prompt_as = '\n' + '  '.join(opts_prompt_as)
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
                        my_acts['附加'] = my_acts.get('附加', 0) - 1
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
                        my_acts['附加'] = my_acts.get('附加', 0) - 1
                else:
                    return jsonify({'error': True, 'message': f'不支持的附加动作: {sub}'}), 400

            elif action.startswith('.g '):
                # ── Transfer craft item ──
                item_name = action[3:].strip()
                out, success = engine._eat_cake(effective_uid)
                output = out if isinstance(out, str) else str(out)

            else:
                return jsonify({'error': True, 'message': f'不支持的动作: {action}'}), 400

            # ── Save actions ──
            engine._set_actions(actions)

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
                        if round_after != round_before:
                            result['output'] += '\n\n' + engine._get_initiative_display()
                        result['output'] += '\n\n' + _build_turn_announcement(engine, active_final_uid)

        return jsonify(result)


@app.route('/api/pvp/<battle_id>/react', methods=['POST'])
def submit_reaction(battle_id):
    """Submit a reaction choice (dodge/counter) for a pending attack."""
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

        pending = _pending_reactions.pop(battle_id, None)
        if not pending:
            return jsonify({'error': True, 'message': '没有待处理的反应'}), 400

        # Track round before reaction resolution (for turn announcement)
        state_before = engine._get_state()
        round_before = state_before.get('round', 1) if state_before and state_before.get('phase') == 'active' else 0

        is_spell_reaction = pending.get('reaction_type') == 'spell'

        if is_spell_reaction and choice == 'counter' and not pending.get('can_counter', True):
            return jsonify({'error': True, 'message': '该法术不可反击！请使用闪避'}), 400
        if is_spell_reaction and choice == 'dodge' and not pending.get('can_dodge', True):
            return jsonify({'error': True, 'message': '该法术不可闪避！请使用反击'}), 400

        def_uid = pending.get('def_uid', '')
        controllers = getattr(engine, '_player_controllers', {}).get(def_uid, [])
        if def_uid != player_id and player_id not in controllers:
            return jsonify({'error': True, 'message': '这个反应不是你的'}), 403

        try:
            if is_spell_reaction:
                dodged_sp, countered_sp, lines_sp = engine.resolve_spell_reaction(pending, choice)
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
                        spell_index=pending.get('spell_index', pending.get('spell', {}).get('index', 0)))
                    if dmg_lines:
                        output += '\n' + dmg_lines

                # ── Deduct action from the attacker (who cast the spell) ──
                il = engine._get_initiative()
                state = engine._get_state()
                idx = state.get('activeIndex', 0) if state else 0
                if idx < len(il):
                    atk_entry = il[idx]
                    atk_uid = atk_entry['userId']
                    actions = engine._get_actions()
                    my_acts = actions.get(atk_uid, {'主动': 0, '附加': 0})
                    my_acts['主动'] = max(0, my_acts.get('主动', 0) - 1)
                    engine._set_actions(actions)

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
                output = '\n'.join(lines)

                # Deduct action from the attacker (who triggered the reaction)
                il = engine._get_initiative()
                state = engine._get_state()
                idx = state.get('activeIndex', 0) if state else 0
                if idx < len(il):
                    atk_entry = il[idx]
                    atk_uid = atk_entry['userId']
                    actions = engine._get_actions()
                    my_acts = actions.get(atk_uid, {'主动': 0, '附加': 0})
                    my_acts['主动'] = max(0, my_acts.get('主动', 0) - 1)
                    engine._set_actions(actions)
    
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
            if not needs_reaction:
                il_final = engine._get_initiative()
                st_final = engine._get_state()
                if st_final and il_final and st_final.get('phase') == 'active':
                    idx_final = st_final.get('activeIndex', 0)
                    if idx_final < len(il_final):
                        active_final = il_final[idx_final]
                        active_final_uid = active_final['userId']
                        if _is_human_controlled(engine, active_final_uid):
                            round_after = st_final.get('round', 1)
                            if round_after != round_before:
                                result['output'] += '\n' + engine._get_initiative_display()
                            result['output'] += '\n' + _build_turn_announcement(engine, active_final_uid)

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
        actions = engine._get_actions()

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
            my_acts = actions.get(uid, {'主动': 0, '附加': 0})
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

        # Find by serial (Y1-Y12)
        if serial:
            for e in il:
                if e.get('isSummon'): continue
                char = engine.get_char(e.get('baseUserId', e['userId']))
                if char and char.serial and char.serial.upper() == serial.upper():
                    target_uid = e['userId']
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
        il.sort(key=lambda e: (-e['initRank'], -e['dex'], -e['initRoll']))
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

        # ── Set real actions (per initiative entry, including __act variants) ──
        engine._set_actions({e['userId']: {'主动': 2, '附加': 3} for e in il})

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

        # ── Build response ──
        active_entry = il[0] if il else None
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
                            result['output'] += '\n' + _build_turn_announcement(engine, active_entry2['userId'])

        # ── Check for pending reactions ──
        pending = _pending_reactions.get(battle_id)
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

    if not hasattr(engine, '_player_controllers'):
        engine._player_controllers = {}

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


@app.route('/api/pvp/<battle_id>/end', methods=['POST'])
def end_battle(battle_id):
    """Clean up a battle instance."""
    with _lock:
        _battles.pop(battle_id, None)
        _pending_reactions.pop(battle_id, None)
    return jsonify({'status': 'ok', 'message': '战斗已清理'})


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
    print('Starting autocombat PvP battle server on http://0.0.0.0:8889')
    app.run(host='0.0.0.0', port=8889, debug=False, threaded=True)
