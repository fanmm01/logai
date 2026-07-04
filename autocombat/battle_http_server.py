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

# Ensure we can import from the autocombat directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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

        is_dodge = (choice == 'dodge')

        if is_dodge:
            # ── Dodge resolution (parent lines 1212-1221) ──
            rr, rd = roll_d100(def_bp)
            react_rank = success_rank(rr, state['dodge_val'])
            rbs = f", {rd}" if rd else ""
            lines.append(f"{dname} 的反应【闪避={state['dodge_val']}】:")
            lines.append(f"  D100={rr}/{state['dodge_val']}{rbs} {rank_text(react_rank)}")
            eff_atk = atk_rank
            if react_rank > 1:
                eff_atk -= (react_rank - 1)
            if react_rank == -2:
                eff_atk += (1 if eff_atk == -1 else 2)
            eff_atk = max(-2, min(4, eff_atk))
            if eff_atk <= 0:
                lines.append(f"  {dname} 闪避成功！")
                return (def_uid, atk_uid, lines)
            winner_rank, winner_uid, loser_uid = eff_atk, atk_uid, def_uid
            loser_name, winner_name, winner_roll, is_counter = dname, aname, atk_result, False
        else:
            # ── Counter resolution (parent lines 1223-1291) ──
            dchar = self.get_char(def_uid)
            rr, rd = roll_d100(def_bp)
            react_rank = success_rank(rr, state['counter_val'])
            rbs = f", {rd}" if rd else ""
            lines.append(f"{dname} 的反应【反击:{state['counter_skill']}={state['counter_val']}】:")
            lines.append(f"  D100={rr}/{state['counter_val']}{rbs} {rank_text(react_rank)}")
            eff_atk, eff_react = atk_rank, react_rank
            if react_rank == -2:
                eff_atk += (1 if eff_atk == -1 else 2)
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
                self._set_combat_hp(def_uid, achp)
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
                self._set_combat_hp(atk_uid, dchp)
                lines.append(f"  {dname} 反击伤害: {ddetail} → {deff}点")
                lines.append(f"  {aname} HP: {dchp}")

                lines.append(f"  双方同时命中! ({rank_text(eff_atk)} vs {rank_text(eff_react)})")
                return (atk_uid, def_uid, lines)
            else:
                lines.append("  无人得手！")
                return (None, None, lines)

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
            lr = random.randint(1, max(2, cur_hp * 2))
            if lr <= int(exp_dmg):
                cur_hp = 0
                lines.append(f"  致死骰: 1d{max(2,cur_hp*2)}={lr} ≤ {int(exp_dmg)} 成功! {loser_name}死亡")
            else:
                cur_hp = max(0, cur_hp - eff_dmg)
                lines.append(f"  致死骰: 1d{max(2,cur_hp*2)}={lr} > {int(exp_dmg)} 失败")
        else:
            cur_hp = max(0, cur_hp - eff_dmg)
        lines.append(f"  伤害: {dmg_detail} → {eff_dmg}点")
        self._set_combat_hp(loser_uid, cur_hp)
        lines.append(f"  {loser_name} HP: {cur_hp}")
        return (winner_uid, loser_uid, lines)


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
                    if len(state) == 10:
                        state = state + (1, 0)
                    elif len(state) == 11:
                        state = state + (0,)
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
                            return '.a eat'
                        if base_ak == 'GIVE_CAKE':
                            return '.a give'
                        if base_ak.startswith('SKILL_'):
                            sn = int(base_ak.split('_')[1])
                            return f'.s{sn}'
        except Exception:
            pass

    # Fallback: random among available
    return random.choice(available)


def _run_ai_turns(engine, player_uid: str, Q=None) -> list:
    """Advance battle through all AI turns until it's the player's turn again or battle ends.
    If an AI attack triggers a reaction from the human player, stops and stores the
    pending reaction (caller must check _pending_reactions afterward).
    Returns list of output strings, one per AI action.
    """
    outputs = []
    max_steps = 50  # safety limit
    last_round = 0
    for _ in range(max_steps):
        state = engine._get_state()
        if not state or state.get('phase') != 'active':
            break

        il = engine._get_initiative()
        idx = state.get('activeIndex', 0)
        if idx >= len(il):
            break
        entry = il[idx]
        uid = entry['userId']

        # Check if it's the player's turn (check controllers list for multi-player binding)
        base_uid = entry.get('baseUserId', uid)
        controllers = getattr(engine, '_player_controllers', {}).get(base_uid, [])
        if uid == player_uid or player_uid in controllers:
            break

        # Round start display
        current_round = state.get('round', 1)
        if current_round != last_round:
            last_round = current_round
            outputs.append(engine._get_initiative_display())

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
            cmd = _ai_decide_action(engine, uid, Q)
        except Exception:
            cmd = '.s0'

        if cmd == '.i end':
            out = engine._end_turn(uid)
            outputs.append(out if isinstance(out, str) else str(out))
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
                           and (engine._get_combat_hp(e["userId"]) or 0) > 0]
                if enemies:
                    try:
                        _, _, lines = engine._coc7_attack(uid, enemies[0]["userId"], bn, bv, dd, p, l)
                        outputs.append("\n".join(lines))
                    except ReactionNeeded as e:
                        def_uid = e.data.get('def_uid', '')
                        if def_uid == player_uid:
                            # ── Human defender → store pending reaction, stop AI turns ──
                            e.data['battle_id'] = engine.group_id
                            e.data['target_uid'] = def_uid
                            _pending_reactions[engine.group_id] = e.data
                            outputs.append('\n'.join(e.data['prefix_lines']))
                            outputs.append(f"\n@{e.data['def_name']} 请做出反应：\n.e 闪避 / .e c 反击")
                            # Decrement attacker's action (the attack was declared)
                            actions = engine._get_actions()
                            my_acts = actions.get(uid, {'主动': 0, '附加': 0})
                            my_acts['主动'] = max(0, my_acts.get('主动', 0) - 1)
                            engine._set_actions(actions)
                            return outputs  # stop and let caller handle the pending reaction
                        else:
                            # ── AI defender → auto-resolve reaction ──
                            import random as _random
                            choice = _random.choice(['dodge', 'counter'])
                            _, _, lines = engine.resolve_reaction(e.data, choice)
                            outputs.append("\n".join(lines))
                # Decrement main action after basic attack
                actions = engine._get_actions()
                my_acts = actions.get(uid, {'主动': 0, '附加': 0})
                my_acts['主动'] = max(0, my_acts.get('主动', 0) - 1)
                engine._set_actions(actions)
            else:
                out = engine._use_skill(uid, sn, '')
                if isinstance(out, str):
                    outputs.append(out)
                # Decrement main action after skill use (unless passive-only)
                spells = engine.get_char(uid).spells or engine.load_spells(uid)
                spell = next((s for s in spells if s['index'] == sn), None)
                is_passive_only = spell and has_timing(spell.get('时机', '2'), '1') and not has_timing(spell.get('时机', '2'), '2')
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

    Each entry: {index, userId, name, team, hp, hpMax, mp, mpMax, san,
                 isSummon, initRoll, dex, isActive}
    """
    il = engine._get_initiative()
    state = engine._get_state()
    active_idx = state.get('activeIndex', 0) if state else 0
    result = []
    for i, e in enumerate(il):
        uid = e['userId']
        hp = engine._get_combat_hp(uid)
        if hp is None or hp <= 0:
            continue
        char = engine.get_char(uid)
        result.append({
            'index': len(result) + 1,
            'userId': uid,
            'name': e.get('name', uid),
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


def _initiative_list_text(engine) -> str:
    """Formatted initiative order with global numbers (all characters)."""
    entries = _build_global_list(engine)
    if not entries:
        return '\n（无存活角色）'
    lines = ['\n=== 先攻顺序 ===']
    for e in entries:
        team_tag = '[己方]' if e['team'] == 'Y' else '[敌方]'
        summon_tag = ' [召唤]' if e['isSummon'] else ''
        active_mark = ' ◀当前行动' if e['isActive'] else ''
        lines.append(
            f"  [{e['index']}] {team_tag} {e['name']}{summon_tag}  "
            f"HP:{e['hp']}/{e['hpMax']}  先攻:{e['initRoll']}{active_mark}"
        )
    return '\n'.join(lines)


def _enemy_list_text(engine, player_uid: str) -> str:
    """Enemy-only list using the same global initiative numbers."""
    entries = _build_global_list(engine)
    player_entry = next((e for e in engine._get_initiative() if e['userId'] == player_uid), None)
    player_team = player_entry.get('team', 'Y') if player_entry else 'Y'
    enemies = [e for e in entries if e['team'] != player_team]
    if not enemies:
        return '\n=== 敌方已全部阵亡 ==='
    lines = ['\n=== 敌方目标 ===']
    for e in enemies:
        tag = ' [召唤]' if e['isSummon'] else ''
        lines.append(f"  [{e['index']}] {e['name']}{tag}  HP:{e['hp']}/{e['hpMax']}")
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
    if target_entry and target_entry.get('team') != player_team:
        return uid
    return None  # same team → not a valid enemy target


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


# ═══════════════════════════════════════════════════════════════
#  API Endpoints
# ═══════════════════════════════════════════════════════════════

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
    mode = data.get('mode', 'pvp')  # 'pvp' or 'pve'

    with _lock:
        if group_id in _battles:
            return jsonify({'error': True, 'message': '该群的战斗已在进行中'}), 409

        engine = PvPFullBattleEngine()
        engine.group_id = group_id
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

            engine._human_uid = human_uid  # store on engine for AI turn logic

        # ── Setup map ──
        map_data = data.get('map')
        if not map_data:
            # Default 10x10 map
            map_data = {'width': 10, 'height': 10, 'entryRow': 5, 'obstacles': {}, 'occupants': {}}

        # ── Setup initiative ──
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
                row = min(h - 1, math.ceil(h / 2) + i - len(team_a) // 2)
                coord = format_coord(1, row)
                il.append({'userId': uid, 'name': char.name, 'team': 'Y',
                           'dex': char.get_attr('敏捷', 50), 'initRoll': random.randint(1, 100),
                           'coord': coord})
                map_data['occupants'][coord] = uid
            for i, uid in enumerate(team_b):
                char = engine.get_char(uid)
                row = min(h - 1, math.ceil(h / 2) + i - len(team_b) // 2)
                coord = format_coord(w - 2, row)
                il.append({'userId': uid, 'name': char.name, 'team': 'X',
                           'dex': char.get_attr('敏捷', 50), 'initRoll': random.randint(1, 100),
                           'coord': coord})
                map_data['occupants'][coord] = uid
            il.sort(key=lambda e: (-e['initRoll'], -e['dex']))
            engine._set_map(map_data)
            engine._set_initiative(il)
            init_list = il
        else:
            engine._set_map(map_data)
            engine._set_initiative(init_list)
            il = init_list

        # ── Combat HP ──
        for uid in all_uids:
            char = engine.get_char(uid)
            engine._init_combat_hp(uid, char.get_attr('体力', 10))

        # ── Actions ──
        engine._set_actions({uid: {'主动': 2, '附加': 3} for uid in all_uids})

        # ── State ──
        for uid in all_uids:
            char = engine.get_char(uid)
            char.set_attr('状态', random.randint(0, 100))

        engine._set_state({'phase': 'active', 'round': 1, 'activeIndex': 0})

        # ── Auto-cast passive spells ──
        for uid in all_uids:
            spells = engine.get_char(uid).spells or engine.load_spells(uid)
            for s in spells:
                if has_timing(s.get('时机', '2'), '1'):
                    try:
                        target = engine._smart_target(uid, s)
                        engine._execute_spell(uid, target, s)
                    except ReactionNeeded:
                        pass  # passives don't trigger reactions

        _battles[group_id] = engine
        _pending_reactions.pop(group_id, None)

        # ── Build response ──
        state_info = _serialize_battle_state(engine)
        chars_info = {}
        for uid in all_uids:
            chars_info[uid] = _char_summary(engine, uid)

        il = engine._get_initiative()
        active_entry = il[0] if il else None
        output_lines = [f"=== 第 1 回合 === 战斗开始！==="]
        # Initiative order with global numbering
        output_lines.append(_initiative_list_text(engine))
        if active_entry:
            output_lines.append(f"\n当前行动: {active_entry['name']}（主动×2 附加×3）")

        result = {
            'battle_id': group_id,
            'human_uid': human_uid,
            'state': state_info['state'],
            'map': state_info['map'],
            'initiative': state_info['initiative'],
            'characters': chars_info,
            'current_turn': state_info['current_turn'],
            'output': '\n'.join(output_lines),
        }

        # PvE mode: run AI turns if AI goes first (any non-human character)
        if mode == 'pve' and active_entry and active_entry['userId'] != human_uid:
            Q = _load_q_table_pvp()
            ai_outputs = _run_ai_turns(engine, human_uid, Q)
            if ai_outputs:
                result['output'] += '\n\n[AI回合]\n' + '\n'.join(ai_outputs)
            # Refresh state after AI turns
            refreshed = _serialize_battle_state(engine)
            result['state'] = refreshed['state']
            result['current_turn'] = refreshed['current_turn']

        # PvE: if it's now the player's turn, show enemy list
        if mode == 'pve' and human_uid:
            il_final = engine._get_initiative()
            st_final = engine._get_state()
            if st_final and il_final:
                idx_final = st_final.get('activeIndex', 0)
                if idx_final < len(il_final) and il_final[idx_final]['userId'] == human_uid:
                    result['output'] += _enemy_list_text(engine, human_uid)

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
        if pending and pending.get('target_uid') == player_id:
            return jsonify({'error': True, 'message': '请先处理待反应的动作（使用 .e d 或 .e c）',
                           'needs_reaction': True, 'pending_attack': pending}), 409

        state = engine._get_state()
        if not state or state.get('phase') != 'active':
            return jsonify({'error': True, 'message': '当前不在战斗中'}), 400

        il = engine._get_initiative()
        idx = state.get('activeIndex', 0)
        if idx >= len(il):
            return jsonify({'error': True, 'message': '无效的先攻索引'}), 500

        active_entry = il[idx]
        base_uid = active_entry.get('baseUserId', active_entry['userId'])
        controllers = getattr(engine, '_player_controllers', {}).get(base_uid, [])
        if active_entry['userId'] != player_id and player_id not in controllers:
            return jsonify({'error': True, 'message': f"不是你的回合！当前行动: {active_entry.get('name', '?')}"}), 403

        actions = engine._get_actions()
        my_acts = actions.get(player_id, {'主动': 0, '附加': 0})

        output = ''
        needs_reaction = False
        pending_attack = None
        hp_changes = {}
        mp_changes = {}
        turn_advanced = False

        try:
            if action == '.i end':
                # ── End turn ──
                remaining_main = my_acts.get('主动', 0)
                remaining_extra = my_acts.get('附加', 0)
                char_name = engine.get_char(player_id).name
                my_acts['主动'] = 0
                my_acts['附加'] = 0
                engine._set_actions(actions)
                output = engine._end_turn(player_id)
                if isinstance(output, str):
                    pass
                else:
                    output = f"{char_name} 结束回合（剩余主动×{remaining_main} 附加×{remaining_extra}）"
                turn_advanced = True

            elif action == '.s0':
                # ── Basic melee attack ──
                if my_acts.get('主动', 0) <= 0:
                    return jsonify({'error': True, 'message': '主动作次数已用尽！'}), 400
                char = engine.get_char(player_id)
                bn, bv = char.get_best_melee()
                # Find target — check in order: explicit userId, numeric index from args, first enemy
                enemies = [e for e in il
                           if e.get('team') != active_entry.get('team', 'Y')
                           and (engine._get_combat_hp(e['userId']) or 0) > 0]
                if not enemies:
                    return jsonify({'error': True, 'message': '没有可攻击的敌人'}), 400

                tid = None
                # 1) Explicit target userId (from @mention)
                if target and any(e['userId'] == target for e in enemies):
                    tid = target
                # 2) Numeric index from args (e.g. .s0 1)
                if not tid:
                    tgt_idx = _parse_target_index(args)
                    if tgt_idx is not None:
                        tid = _resolve_enemy_index(engine, player_id, tgt_idx)
                        if not tid:
                            return jsonify({'error': True, 'message': f'无效的敌方编号: {tgt_idx}'}), 400
                # 3) Default: first living enemy
                if not tid:
                    tid = enemies[0]['userId']

                dd = engine._get_damage_dice(player_id, bn)
                p = char.get_attr('伤害贯穿', 1)
                l = char.get_attr('致死骰', 1) or 0

                try:
                    _, _, lines = engine._coc7_attack(player_id, tid, bn, bv, dd, p, l)
                    output = '\n'.join(lines)
                    my_acts['主动'] = my_acts.get('主动', 0) - 1
                except ReactionNeeded as e:
                    e.data['battle_id'] = battle_id
                    e.data['target_uid'] = tid
                    # ── PvE mode: if defender is AI, auto-resolve reaction ──
                    human_uid = getattr(engine, '_human_uid', None)
                    def_uid = e.data.get('def_uid', '')
                    if human_uid and def_uid != human_uid:
                        # AI defender → auto-choose dodge or counter (50/50, matches parent class default)
                        import random as _random
                        choice = _random.choice(['dodge', 'counter'])
                        _, _, lines = engine.resolve_reaction(e.data, choice)
                        output = '\n'.join(lines)
                        my_acts['主动'] = my_acts.get('主动', 0) - 1
                    else:
                        # Human defender → store pending reaction for player input
                        pending_attack = {
                            'attacker_name': e.data['atk_name'],
                            'defender_name': e.data['def_name'],
                            'defender_id': e.data['def_uid'],
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

                char = engine.get_char(player_id)
                spells = char.spells or engine.load_spells(player_id)
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
                    # Resolve target: numeric index from args > smart target
                    tgt = None
                    tgt_idx = _parse_target_index(args)
                    if tgt_idx is not None:
                        tgt = _resolve_enemy_index(engine, player_id, tgt_idx)
                    if not tgt:
                        tgt = engine._smart_target(player_id, spell)
                    out = engine._execute_spell(player_id, tgt, spell)
                    output = out if isinstance(out, str) else str(out)
                    if not is_passive:
                        my_acts['主动'] = my_acts.get('主动', 0) - 1
                except ReactionNeeded:
                    # Shouldn't happen for spells, but handle
                    pass

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
                            if occ == player_id:
                                del mp['occupants'][c]
                        mp['occupants'][coord] = player_id
                        engine._set_map(mp)
                    output = f"{engine.get_char(player_id).name} 移动至 {coord}"
                    my_acts['附加'] = my_acts.get('附加', 0) - 1
                elif sub.startswith('s'):
                    sn = int(sub[1:])
                    char = engine.get_char(player_id)
                    spells = char.spells or engine.load_spells(player_id)
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
                    # Resolve target: numeric index from args > smart target
                    tgt = None
                    tgt_idx = _parse_target_index(args)
                    if tgt_idx is not None:
                        tgt = _resolve_enemy_index(engine, player_id, tgt_idx)
                    if not tgt:
                        tgt = engine._smart_target(player_id, spell)
                    out = engine._execute_spell(player_id, tgt, spell)
                    output = out if isinstance(out, str) else str(out)
                    my_acts['附加'] = my_acts.get('附加', 0) - 1
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
                    out, success = engine._eat_cake(player_id, target_id)
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
                    out, success = engine._eat_cake(player_id, target_entry['userId'])
                    output = out if isinstance(out, str) else str(out)
                    if success:
                        my_acts['附加'] = my_acts.get('附加', 0) - 1
                else:
                    return jsonify({'error': True, 'message': f'不支持的附加动作: {sub}'}), 400

            elif action.startswith('.g '):
                # ── Transfer craft item ──
                item_name = action[3:].strip()
                out, success = engine._eat_cake(player_id)
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
            if not needs_reaction and my_acts.get('主动', 0) <= 0 and not action.startswith('.a '):
                try:
                    end_out = engine._end_turn(player_id)
                    if isinstance(end_out, str) and len(end_out) > 0:
                        output += '\n' + end_out
                except Exception:
                    pass
                turn_advanced = True

        except Exception as e:
            if not isinstance(e, ReactionNeeded):
                return jsonify({'error': True, 'message': f'服务器错误: {str(e)}'}), 500

        # ── PvE: run AI turns after player action ──
        auto_turns = []
        if not needs_reaction:
            # Check if next character is not the human player → auto-play AI turns
            state = engine._get_state()
            if state and state.get('phase') == 'active':
                il = engine._get_initiative()
                idx = state.get('activeIndex', 0)
                if idx < len(il):
                    next_entry = il[idx]
                    human_uid = getattr(engine, '_human_uid', None)
                    if human_uid and next_entry['userId'] != human_uid:
                        Q = _load_q_table_pvp()
                        auto_turns = _run_ai_turns(engine, human_uid, Q)
                        # Check if _run_ai_turns stored a pending reaction (AI attacked human)
                        pending = _pending_reactions.get(battle_id)
                        if pending and pending.get('def_uid') == human_uid:
                            needs_reaction = True
                            pending_attack = {
                                'attacker_name': pending['atk_name'],
                                'defender_name': pending['def_name'],
                                'defender_id': pending['def_uid'],
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

        # PvE: if it's now the player's turn, show enemy list
        human_uid = getattr(engine, '_human_uid', None)
        if human_uid and not needs_reaction:
            il_final = engine._get_initiative()
            st_final = engine._get_state()
            if st_final and il_final and st_final.get('phase') == 'active':
                idx_final = st_final.get('activeIndex', 0)
                if idx_final < len(il_final) and il_final[idx_final]['userId'] == human_uid:
                    result['output'] += _enemy_list_text(engine, human_uid)

        return jsonify(result)


@app.route('/api/pvp/<battle_id>/react', methods=['POST'])
def submit_reaction(battle_id):
    """Submit a reaction choice (dodge/counter) for a pending attack."""
    data = request.get_json(force=True)
    if not data:
        return jsonify({'error': True, 'message': '无效的请求数据'}), 400

    player_id = data.get('player_id', '')
    choice = data.get('choice', 'dodge')

    if choice not in ('dodge', 'counter'):
        return jsonify({'error': True, 'message': '选择无效，请使用 dodge 或 counter'}), 400

    with _lock:
        engine = _battles.get(battle_id)
        if not engine:
            return jsonify({'error': True, 'message': '战斗不存在'}), 404

        pending = _pending_reactions.pop(battle_id, None)
        if not pending:
            return jsonify({'error': True, 'message': '没有待处理的反应'}), 400

        if pending.get('def_uid') != player_id:
            return jsonify({'error': True, 'message': '这个反应不是你的'}), 403

        try:
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

            # ── PvE: continue AI turns if it's now an AI character's turn ──
            auto_turns = []
            needs_reaction = False
            pending_attack = None
            human_uid = getattr(engine, '_human_uid', None)
            if human_uid:
                st = engine._get_state()
                il2 = engine._get_initiative()
                if st and il2 and st.get('phase') == 'active':
                    idx2 = st.get('activeIndex', 0)
                    if idx2 < len(il2) and il2[idx2]['userId'] != human_uid:
                        Q = _load_q_table_pvp()
                        auto_turns = _run_ai_turns(engine, human_uid, Q)
                        # Check if AI turns triggered another reaction (AI attacked human again)
                        pending2 = _pending_reactions.get(battle_id)
                        if pending2 and pending2.get('def_uid') == human_uid:
                            needs_reaction = True
                            pending_attack = {
                                'attacker_name': pending2['atk_name'],
                                'defender_name': pending2['def_name'],
                                'defender_id': pending2['def_uid'],
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
                'hp_changes': hp_changes,
                'current_turn': refreshed['current_turn'],
                'state': refreshed['state'],
                'needs_reaction': needs_reaction,
            }
            if needs_reaction and pending_attack:
                result['pending_attack'] = pending_attack
            if auto_turns:
                result['auto_turns'] = auto_turns
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
    engine = _battles.get(battle_id)
    if not engine:
        return jsonify({'error': True, 'message': '战斗不存在'}), 404

    data = request.get_json(force=True)
    player_id = data.get('player_id', '')
    serial = data.get('serial', '')
    init_index = data.get('init_index')

    if not player_id:
        return jsonify({'error': True, 'message': '缺少player_id'}), 400

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

    char_name = engine.get_char(base_uid).name if engine.get_char(base_uid) else base_uid

    # If this character is currently active, run AI turns to check if human input needed
    auto_turns = []
    state = engine._get_state()
    if state and state.get('phase') == 'active':
        il2 = engine._get_initiative()
        idx = state.get('activeIndex', 0)
        if idx < len(il2):
            active_entry = il2[idx]
            active_uid = active_entry.get('baseUserId', active_entry['userId'])
            if active_uid == base_uid:
                try:
                    auto_turns = _run_ai_turns(engine, player_id)
                except Exception:
                    pass

    return jsonify({
        'char_name': char_name,
        'auto_turns': auto_turns,
    })


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
