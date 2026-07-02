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
    rank_text, avg_damage, success_rank, roll_d100, max_damage,
    CN_NUMS, CAT_LETTERS,
)

# ── Skill lists (mirrored from test_harness for completeness) ──
MELEE_SKILLS = ['格斗','斗殴','斧','链锯','连枷','绞索','矛','剑','鞭']
ALL_COMBAT_SKILLS = MELEE_SKILLS + [
    '射击','射击:弓','射击:手枪','射击:重武器','射击:火焰喷射器',
    '射击:机枪','射击:步霰','射击:冲锋枪','投掷','爆破','炮术'
]

# ── Flask app ──
app = Flask(__name__)

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

    # ── 1) Load spells, damage dice, special attrs from chData ──
    serial = js_data.get('serial', '')
    mgid = js_data.get('attrs', {}).get('魔法少女序号', 0)

    ch_data = _find_chdata_by_serial(serial)
    if not ch_data and mgid:
        ch_data = _find_chdata_by_mgid(mgid)

    if ch_data:
        _load_spells_from_chdata(engine, uid, ch_data)

        # Damage dice from chData (str_attrs)
        for k, v in ch_data.get('str_attrs', {}).items():
            char.set_str(k, v)

        # Special attributes from chData
        for k in ('飞行', '技能不消耗主动', '召唤物HP单独显示'):
            if k in ch_data.get('attrs', {}):
                char.set_attr(k, ch_data['attrs'][k])

    # ── 2) COC base stats from JS (player's actual card values) ──
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

    # String attributes from JS (e.g. 伤害值 — fallback if not in chData)
    for k, v in js_data.get('str_attrs', {}).items():
        if not char.get_str(k):  # don't overwrite chData values
            char.set_str(k, v)

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
        eff_bp = bp_suffix or ""
        for b in atk_buffs:
            if b.get("auxCode") == 16 and b.get("auxVal") and not eff_bp:
                eff_bp = str(b["auxVal"])

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
        def_bp = ""
        for b in def_buffs:
            if b.get("auxCode") == 16 and b.get("auxVal") and not def_bp:
                def_bp = str(b["auxVal"])

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
            lr = random.randint(1, max(2, cur_hp * 2))
            if lr <= int(exp_dmg):
                cur_hp = 0
                lines.append(f"  致死骰: 成功! {loser_name}死亡")
            else:
                cur_hp = max(0, cur_hp - eff_dmg)
                lines.append(f"  致死骰: 失败")
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
    """Try to load PvP Q-learning weights for AI opponents."""
    try:
        weight_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ai_weights_pvp.json')
        if not os.path.exists(weight_path):
            return None
        with open(weight_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        from collections import defaultdict
        Q = defaultdict(lambda: defaultdict(float))
        for ck, qdict in data.get('Q', {}).items():
            for key, val in qdict.items():
                parts = key.split('__')
                state_str, action = parts[0], parts[1]
                state = tuple(int(v) for v in state_str.split('|'))
                if len(state) == 10:
                    state = state + (1,)
                Q[ck][(state, action)] = val
        return Q
    except Exception:
        return None


def _ai_decide_action(engine, uid: str, Q=None) -> str:
    """Decide an AI action for the given character. Returns a command string like '.s2' or '.s0'."""
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
        # Try to encode state and find best action
        try:
            # Use the same state encoding as ai_trainer.py
            from ai_trainer_pvp import encode_state, get_available_actions
            st = encode_state(engine, uid)
            avail = get_available_actions(engine, uid)
            if avail:
                import random as _random
                best_action = max(avail, key=lambda a: Q.get(f'{st}__{a[0]}', {}).get(a[1], 0) if isinstance(Q, dict) else 0)
                return best_action[1] if isinstance(best_action, tuple) else '.s0'
        except Exception:
            pass

    # Fallback: random among available
    return random.choice(available)


def _run_ai_turns(engine, player_uid: str, Q=None) -> list:
    """Advance battle through all AI turns until it's the player's turn again or battle ends.
    Returns list of output strings, one per AI action.
    """
    outputs = []
    max_steps = 50  # safety limit
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

        # Check if it's the player's turn
        if uid == player_uid:
            break

        # Check HP
        hp = engine._get_combat_hp(uid) or 0
        if hp <= 0:
            engine._end_turn(uid)
            outputs.append(f"{entry.get('name', uid)} 已阵亡，跳过回合。")
            continue

        # Summon auto-attack
        if entry.get('isSummon'):
            engine._summon_attack(uid)
            engine._end_turn(uid)
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
                    except ReactionNeeded:
                        # AI reaction: use the parent class's original _coc7_attack
                        # which auto-decides based on weights
                        # For now, force dodge
                        engine.__class__ = FullBattleEngine  # temporarily switch
                        try:
                            _, _, lines = FullBattleEngine._coc7_attack(engine, uid,
                                enemies[0]["userId"], bn, bv, dd, p, l)
                            outputs.append("\n".join(lines))
                        finally:
                            engine.__class__ = PvPFullBattleEngine
            else:
                out = engine._use_skill(uid, sn, '')
                if isinstance(out, str):
                    outputs.append(out)
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
#  API Endpoints
# ═══════════════════════════════════════════════════════════════

@app.route('/api/pvp/ping', methods=['GET'])
def ping():
    return jsonify({'status': 'ok', 'service': 'autocombat PvP engine'})

@app.route('/api/pvp/create', methods=['POST'])
def create_battle():
    """Initialize a new PvP or PvE battle."""
    data = request.get_json(force=True)
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

        # ── PvE mode: build player team + AI opponents ──
        human_uid = None  # which uid the human player controls
        if mode == 'pve':
            player_serials = data.get('player_serials', [])
            human_serial = data.get('human_serial', '')
            ally_serials = data.get('ally_serials', [])

            # If no player_serials (old format), infer from the single JS character
            if not player_serials:
                player_char = engine.get_char(all_uids[0]) if all_uids else None
                if player_char:
                    player_serials = [player_char.serial or '']
                    human_serial = player_char.serial or ''

            # Track all team serials (used to exclude from AI opponents)
            team_serials = set(player_serials + ally_serials)

            from characters_data_pvp import load_character_to_engine

            # ── Load ally characters (from chData) ──
            for a_serial in ally_serials:
                a_data = _find_chdata_by_serial(a_serial)
                if a_data:
                    a_uid = f"ally_{a_serial}_{random.randint(1000, 9999)}"
                    load_character_to_engine(engine, a_data, a_uid)
                    engine.get_char(a_uid).name = a_data.get('name', 'AI队友')
                    all_uids.append(a_uid)

            # ── Load additional player team members (from chData) ──
            for p_serial in player_serials:
                if p_serial == human_serial:
                    continue  # human character loaded from JS data
                p_data = _find_chdata_by_serial(p_serial)
                if p_data:
                    p_uid = f"team_{p_serial}_{random.randint(1000, 9999)}"
                    load_character_to_engine(engine, p_data, p_uid)
                    engine.get_char(p_uid).name = p_data.get('name', p_serial)
                    all_uids.append(p_uid)

            # ── Identify human_uid ──
            human_uid = all_uids[0] if all_uids else None  # first JS-loaded char
            engine._human_uid = human_uid  # store on engine for AI turn logic

            # ── Generate AI opponent team (same size as player team) ──
            team_size = len(all_uids)  # player + ally + team members
            # Pick AI opponents (different from team serials)
            ai_candidates = [c for c in characters_data_pvp.ALL_CHARACTERS
                             if c.get('serial', '') not in team_serials]
            if len(ai_candidates) < team_size:
                # Fallback: allow any character not matching team prefix
                team_prefixes = {s[0] for s in team_serials if s}
                ai_candidates = [c for c in characters_data_pvp.ALL_CHARACTERS
                                 if c.get('serial', '')[:1] not in team_prefixes]
            random.shuffle(ai_candidates)
            for i in range(min(team_size, len(ai_candidates))):
                ai_data = ai_candidates[i]
                ai_uid = f"ai_{random.randint(1000, 9999)}"
                load_character_to_engine(engine, ai_data, ai_uid)
                engine.get_char(ai_uid).name = ai_data.get('name', 'AI对手')
                engine.get_char(ai_uid).serial = ai_data.get('serial', '')
                all_uids.append(ai_uid)

        # ── Setup map ──
        map_data = data.get('map')
        if not map_data:
            # Default 10x10 map
            map_data = {'width': 10, 'height': 10, 'entryRow': 5, 'obstacles': {}, 'occupants': {}}

        # ── Setup initiative ──
        init_list = data.get('initiative', [])
        if not init_list:
            # Auto-generate initiative
            team_a = [u for u in all_uids if not u.startswith('ai_')]
            team_b = [u for u in all_uids if u.startswith('ai_')]
            # Team Y: all non-ai_ prefixed (human + allies + team members)
            # Team X: all ai_ prefixed (AI opponents, or PvP team B)
            team_a = [u for u in all_uids if not u.startswith('ai_')]
            team_b = [u for u in all_uids if u.startswith('ai_')]

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
        for uid in all_uids:
            s = chars_info[uid]
            output_lines.append(f"{s['name']} HP:{s['hp']}/{s['hp_max']} MP:{s['mp']}/{s['mp_max']} SAN:{s['san']}")
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
        if active_entry['userId'] != player_id:
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
                # Find target
                enemies = [e for e in il
                           if e.get('team') != active_entry.get('team', 'Y')
                           and (engine._get_combat_hp(e['userId']) or 0) > 0]
                if not enemies:
                    return jsonify({'error': True, 'message': '没有可攻击的敌人'}), 400

                tid = target if target and any(e['userId'] == target for e in enemies) else enemies[0]['userId']
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
                    pending_attack = {
                        'attacker_name': e.data['atk_name'],
                        'defender_name': e.data['def_name'],
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
                    return jsonify({'error': True, 'message': f'未找到技能{sn}'}), 404

                timing = spell.get('时机', '2')
                is_passive = has_timing(timing, '1')
                if not has_timing(timing, '2') and not is_passive:
                    return jsonify({'error': True, 'message': f'【{spell["name"]}】不能在主动作阶段使用'}), 400

                try:
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
                        return jsonify({'error': True, 'message': f'未找到技能{sn}'}), 404
                    timing = spell.get('时机', '2')
                    if not has_timing(timing, '3'):
                        return jsonify({'error': True, 'message': f'【{spell["name"]}】不能在附加动作阶段使用'}), 400
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
            result['output'] += '\n\n[AI回合]\n' + '\n'.join(auto_turns)
            # Refresh again after AI turns
            refreshed2 = _serialize_battle_state(engine)
            result['current_turn'] = refreshed2['current_turn']
            result['state'] = refreshed2['state']

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

            # Deduct action
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

                # Check if turn should advance
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

            refreshed = _serialize_battle_state(engine)
            return jsonify({
                'output': output,
                'hp_changes': hp_changes,
                'current_turn': refreshed['current_turn'],
                'state': refreshed['state'],
                'needs_reaction': False,
            })

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


@app.route('/api/pvp/<battle_id>/end', methods=['POST'])
def end_battle(battle_id):
    """Clean up a battle instance."""
    with _lock:
        _battles.pop(battle_id, None)
        _pending_reactions.pop(battle_id, None)
    return jsonify({'status': 'ok', 'message': '战斗已清理'})


# ── Main (for testing) ──
if __name__ == '__main__':
    import math as _math  # used in create_battle when no init_list
    print('Starting autocombat PvP battle server on http://127.0.0.1:8889')
    app.run(host='0.0.0.0', port=8889, debug=False)
