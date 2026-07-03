#!/usr/bin/env python3
"""
Q-Learning AI自训练系统 (ai_trainer_pvp.py) — PvP版本
==================================================
50代 × 100场对战，用HP差分奖励训练12角色的行动策略。
使用 characters_data_pvp 的数据集进行训练。
输出 ai_weights_pvp.json 供 battle_http_server.py 使用。
"""

import sys, os, random, math, json, itertools, time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from battle_engine import (CombatEngine, FastBattleEngine, roll_dice, is_in_melee_range,
    has_timing, parse_coord, format_coord, avg_damage)
from characters_data_pvp import ALL_CHARACTERS, load_character_to_engine, SUMMON_TEMPLATES
import battle_engine
battle_engine._SUMMON_TEMPLATES = SUMMON_TEMPLATES

# ============================================================
#  Hyperparameters
# ============================================================
GENERATIONS = 50
BATTLES_PER_GEN = 100
ALPHA = 0.1       # Learning rate
GAMMA = 0.9       # Discount factor

# ============================================================
#  State Encoding
# ============================================================

def bucket(val, max_val):
    """Bucket a ratio 0-1 into 0-3."""
    if max_val <= 0: return 0
    r = val / max_val
    if r < 0.25: return 0
    if r < 0.50: return 1
    if r < 0.75: return 2
    return 3

def encode_state(engine, uid):
    """Encode combat state for a character into a hashable key."""
    char = engine.get_char(uid)
    init_list = engine._get_initiative()
    my_entry = next((e for e in init_list if e['userId'] == uid), None)
    if not my_entry: return 'dead'
    my_team = my_entry.get('team', 'Y')

    my_hp = engine._get_combat_hp(uid) or 0
    my_max_hp = char.get_attr('体力上限', max(my_hp, 1))
    my_b = bucket(my_hp, my_max_hp)

    # Team HP
    team_hps = [engine._get_combat_hp(e['userId']) or 0 for e in init_list if e['team'] == my_team]
    enemy_hps = [engine._get_combat_hp(e['userId']) or 0 for e in init_list if e['team'] != my_team]
    team_avg = sum(team_hps) / max(1, len(team_hps))
    enemy_avg = sum(enemy_hps) / max(1, len(enemy_hps))
    team_max = max(team_hps + [1])
    enemy_max = max(enemy_hps + [1])
    tb = bucket(team_avg, team_max)
    eb = bucket(enemy_avg, enemy_max)

    # Distance to nearest enemy
    my_coord = my_entry.get('coord', '')
    enemies = [e for e in init_list if e['team'] != my_team and (engine._get_combat_hp(e['userId']) or 0) > 0]
    dist = 3  # far
    if enemies and my_coord:
        for e in enemies:
            ec = e.get('coord', '')
            if ec:
                mp, ep = parse_coord(my_coord), parse_coord(ec)
                if mp and ep:
                    d = abs(mp[0] - ep[0]) + abs(mp[1] - ep[1])
                    if d == 0: dist = min(dist, 0)
                    elif d <= 2: dist = min(dist, 1)
                    else: dist = min(dist, 2)

    # Spell availability
    spells = char.spells or engine.load_spells(uid)
    has_dmg = any(any(e['type'] == 1 for e in s.get('effects', [])) and has_timing(s.get('时机', '2'), '2') for s in spells)
    has_heal = any(any(e['type'] in (3, 8) and e.get('回复hp') for e in s.get('effects', [])) and has_timing(s.get('时机', '2'), '2') for s in spells)
    has_buff = any(any(e['type'] == 4 for e in s.get('effects', [])) and has_timing(s.get('时机', '2'), '2') for s in spells)
    has_summon = any(any(e['type'] == 5 for e in s.get('effects', [])) and has_timing(s.get('时机', '2'), '2') for s in spells)

    # Active buffs
    buffs_active = len(engine._get_active_buffs(uid)) > 0

    n_enemies = min(len(enemies), 4)

    # Phase dimension (for dual-phase characters like 木落)
    phase = getattr(char, 'phase', 1)

    # Ready cake available (for 比哈米 cake-giving / eating decisions)
    has_cake = int(hasattr(engine, '_has_ready_cake') and engine._has_ready_cake())

    return (my_b, tb, eb, dist, int(has_dmg), int(has_heal), int(has_buff), int(has_summon), int(buffs_active), n_enemies, phase, has_cake)


def encode_summon_state(engine, uid):
    """Encode combat state for a summon entity into a hashable key."""
    init_list = engine._get_initiative()
    entry = next((e for e in init_list if e['userId'] == uid), None)
    if not entry: return 'dead'
    my_team = entry.get('team', 'Y')

    my_hp = engine._get_combat_hp(uid) or 0
    my_max_hp = entry.get('max_hp', max(my_hp, 1))
    my_b = bucket(my_hp, my_max_hp)

    # Enemy HP
    enemy_hps = [engine._get_combat_hp(e['userId']) or 0 for e in init_list if e['team'] != my_team]
    enemy_max = max(enemy_hps + [1])
    eb = bucket(sum(enemy_hps) / max(1, len(enemy_hps)), enemy_max)

    # Distance to nearest enemy
    my_coord = entry.get('coord', '')
    enemies = [e for e in init_list if e['team'] != my_team and (engine._get_combat_hp(e['userId']) or 0) > 0]
    dist = 3
    if enemies and my_coord:
        for e in enemies:
            ec = e.get('coord', '')
            if ec:
                mp, ep = parse_coord(my_coord), parse_coord(ec)
                if mp and ep:
                    d = abs(mp[0] - ep[0]) + abs(mp[1] - ep[1])
                    if d == 0: dist = min(dist, 0)
                    elif d <= 2: dist = min(dist, 1)
                    else: dist = min(dist, 2)

    n_enemies = min(len(enemies), 4)

    # Number of skills this summon has (0=1 skill, 1=2, 2=3+)
    skills = entry.get('skills', [])
    n_skills = min(len(skills) - 1, 2) if skills else 0
    if n_skills < 0: n_skills = 0

    # Ignited flag
    ignited = 1 if entry.get('ignited') else 0

    # Battle spirit penalty dice (from 环花暖)
    bs_penalty = min(entry.get('battle_spirit_penalty_dice', 0), 2)

    return (my_b, eb, dist, n_enemies, n_skills, ignited, bs_penalty)


# ============================================================
#  Multiprocess battle runner (module-level for pickling)
# ============================================================

def _mp_run_battle(args):
    """Standalone battle runner for ProcessPoolExecutor. Returns (updates, winner_info).
    Updates format: (table_type, ck, st, ak, reward, next_st)
    table_type: 'solo' | 'team' | 'summon'
    """
    team_a, team_b, map_size, all_char_data, char_map_dict, summon_templates = args
    engine = FastBattleEngine()
    if summon_templates:
        import battle_engine as _be
        _be._SUMMON_TEMPLATES = summon_templates
    a_uids = [char_map_dict[s] for s in team_a]
    b_uids = [char_map_dict[s] for s in team_b]
    for c in all_char_data:
        uid = char_map_dict[c['serial']]
        load_character_to_engine(engine, c, uid)
        if not c.get('pre_transformed'):
            engine.process_command(uid, '.hs')
    engine.setup_battle(a_uids, b_uids, map_size)
    engine._set_actions({uid: {'主动': 2, '附加': 3} for uid in a_uids + b_uids})
    engine._react_dw = {uid: 50 for uid in a_uids + b_uids}
    engine._react_cw = {uid: 50 for uid in a_uids + b_uids}

    updates = []
    winner = None
    round_count = 0
    step_count = 0
    while round_count < 20 and step_count < 200:
        step_count += 1
        state = engine._get_state()
        if not state or state.get('phase') != 'active': break
        init_list = engine._get_initiative()
        if not init_list: break
        round_count = state.get('round', 1)

        if round_count != getattr(engine, '_last_zone_round', 0):
            has_zones = any(e.get('type') == 'zone' for e in engine._get_effects())
            has_summons = any(e.get('isSummon') for e in engine._get_initiative())
            if has_zones: engine._process_zone_specials()
            if has_summons:
                engine._check_trinity_merge()
                dead_owners = {e['userId'] for e in engine._get_initiative()
                               if not e.get('isSummon') and (engine._get_combat_hp(e['userId']) or 0) <= 0}
                if dead_owners:
                    il = engine._get_initiative()
                    new_il = [e for e in il if not (e.get('isSummon') and e.get('ownerId') in dead_owners)]
                    engine._set_initiative(new_il)
                    if state.get('activeIndex', 0) >= len(new_il):
                        state['activeIndex'] = state['activeIndex'] % max(1, len(new_il))
                        engine._set_state(state)
            for attr in list(engine.__dict__.keys()):
                if attr.startswith('_xingshan_attacked_'): delattr(engine, attr)
            engine._last_zone_round = round_count

        dead_owners_r = {e['userId'] for e in init_list if not e.get('isSummon') and (engine._get_combat_hp(e['userId']) or 0) <= 0}
        if dead_owners_r:
            init_list = [e for e in init_list if not (e.get('isSummon') and e.get('ownerId') in dead_owners_r)]
            engine._set_initiative(init_list)
            if state.get('activeIndex', 0) >= len(init_list):
                state['activeIndex'] = state['activeIndex'] % max(1, len(init_list))
                engine._set_state(state)

        y_alive = sum(1 for e in init_list if e['team'] == 'Y' and (engine._get_combat_hp(e['userId']) or 0) > 0)
        x_alive = sum(1 for e in init_list if e['team'] == 'X' and (engine._get_combat_hp(e['userId']) or 0) > 0)
        if y_alive == 0: winner = 'X'; break
        if x_alive == 0: winner = 'Y'; break

        entry = init_list[state['activeIndex']]
        uid = entry['userId']
        hp = engine._get_combat_hp(uid) or 0
        my_team = entry.get('team', 'Y')
        is_summon = entry.get('isSummon', False)

        if hp <= 0:
            state['activeIndex'] = (state['activeIndex'] + 1) % len(init_list)
            if state['activeIndex'] == 0:
                state['round'] = state.get('round', 1) + 1
                acts_r = engine._get_actions()
                for k in list(acts_r.keys()): acts_r[k] = {'主动': 2, '附加': 3}
                engine._set_actions(acts_r)
                engine._tick_down(); engine._apply_zone_effects()
            engine._set_state(state)
            continue

        if is_summon:
            # Summon Q-training: random action in subprocess
            st = encode_summon_state(engine, uid)
            av = get_summon_actions(engine, uid)
            if av:
                ak, an = random.choice(av)
                prev_diff = engine.hp_diff(my_team) - engine.hp_diff('X' if my_team == 'Y' else 'Y')
                execute_summon_action(engine, uid, ak)
                curr_diff = engine.hp_diff(my_team) - engine.hp_diff('X' if my_team == 'Y' else 'Y')
                max_hp = max(abs(prev_diff), abs(curr_diff), 1)
                reward = (curr_diff - prev_diff) / max_hp
                next_st = encode_summon_state(engine, uid)
                summon_name = entry.get('name', uid)
                updates.append(('summon', summon_name, st, ak, reward, next_st))
            else:
                engine._summon_attack(uid)
            state['activeIndex'] = (state['activeIndex'] + 1) % len(init_list)
            if state['activeIndex'] == 0:
                state['round'] = state.get('round', 1) + 1
                acts_r = engine._get_actions()
                for k in list(acts_r.keys()): acts_r[k] = {'主动': 2, '附加': 3}
                engine._set_actions(acts_r)
                engine._tick_down(); engine._apply_zone_effects()
            engine._set_state(state)
            continue

        actions = engine._get_actions().get(uid, {'主动': 2, '附加': 3})
        if actions['主动'] <= 0: engine._end_turn(uid); continue

        st = encode_state(engine, uid)
        available = get_available_actions(engine, uid)
        if not available: engine._end_turn(uid); continue

        # Determine solo vs team for this character
        living_allies = [e for e in init_list if e['team'] == my_team
                         and e['userId'] != uid
                         and not e.get('isSummon')
                         and (engine._get_combat_hp(e['userId']) or 0) > 0]
        table_type = 'solo' if not living_allies else 'team'

        # Epsilon-greedy (no Q-table access in subprocess — random)
        ak, an = random.choice(available)

        prev_diff = engine.hp_diff(my_team) - engine.hp_diff('X' if my_team == 'Y' else 'Y')
        execute_action(engine, uid, ak)
        # 星闪 skills don't consume 主动
        is_xs_action = engine.get_char(uid).get_attr('技能不消耗主动', 0) == 1 and ak.startswith('SKILL_')
        if not is_xs_action:
            actions['主动'] -= 1
        engine._set_actions(engine._get_actions())
        if actions['主动'] <= 0: engine._end_turn(uid)
        curr_diff = engine.hp_diff(my_team) - engine.hp_diff('X' if my_team == 'Y' else 'Y')
        max_hp = max(abs(prev_diff), abs(curr_diff), 1)
        reward = (curr_diff - prev_diff) / max_hp
        next_st = encode_state(engine, uid)
        # Use serial for char key
        ck = None
        for c in all_char_data:
            if char_map_dict[c['serial']] == uid: ck = c['serial']; break
        if ck: updates.append((table_type, ck, st, ak, reward, next_st))

    # Terminal rewards
    if winner:
        for s in team_a + team_b:
            uid = char_map_dict[s]
            ck = s
            st = encode_state(engine, uid) if hasattr(engine, '_get_state') else (0,)*12
            mt2 = 'Y' if s in team_a else 'X'
            term_r = 3.0 if winner == mt2 else -3.0
            # Determine table type for terminal update
            init_list_end = engine._get_initiative()
            my_entry_end = next((e for e in init_list_end if e['userId'] == uid), None)
            my_team_end = my_entry_end.get('team', mt2) if my_entry_end else mt2
            living_allies_end = [e for e in init_list_end if e['team'] == my_team_end
                                 and e['userId'] != uid
                                 and not e.get('isSummon')
                                 and (engine._get_combat_hp(e['userId']) or 0) > 0]
            end_table = 'solo' if not living_allies_end else 'team'
            for ak, _ in get_available_actions(engine, uid):
                updates.append((end_table, ck, st, ak, term_r, None))
            # Also update the other table for terminal state (solo↔team cross-pollination)
            other_table = 'team' if end_table == 'solo' else 'solo'
            for ak, _ in get_available_actions(engine, uid):
                updates.append((other_table, ck, st, ak, term_r * 0.3, None))
    return updates

# ============================================================
#  Fast Battle Engine (no delays, no logging)
# ============================================================

# ============================================================
#  Action space
# ============================================================

# Cache available actions per character (cleared per generation)
_ACTION_CACHE = {}

def get_available_actions(engine, uid):
    """Return list of (action_key, description) — dynamic filtering each call."""
    char = engine.get_char(uid)
    ck = char.serial or uid
    current_phase = getattr(char, 'phase', 1)
    phase_key = f"{ck}_p{current_phase}"
    spells = char.spells or engine.load_spells(uid)

    target_strats = [('T0', '最低HP'), ('T1', '最近'), ('T2', '最高威胁')]

    # Build static base cache (spell list + basic attack + move, without MP check)
    # Use phase-aware key so Phase 1 and Phase 2 have separate caches
    if phase_key not in _ACTION_CACHE:
        base = []
        for s in spells:
            if has_timing(s.get('时机', '2'), '2') and not has_timing(s.get('时机', '1'), '1'):
                for ts, tl in target_strats:
                    base.append((f'SKILL_{s["index"]}__{ts}', f'{s["name"]}→{tl}'))
        for ts, tl in target_strats:
            base.append((f'BASIC_ATTACK__{ts}', f'普攻→{tl}'))
        base.append(('MOVE_TOWARD', '前进'))
        _ACTION_CACHE[phase_key] = base

    # ---- Dynamic filters (apply every call) ----

    cur_mp = char.get_attr('魔力', 0) or 0

    # Active effects: same-name spell already in effect (remainingRounds > 0 or persistent)
    effects = engine._get_effects()
    active_spell_names = set()
    for e in effects:
        if e.get('remainingRounds', 0) > 0 or e.get('persistent', 0):
            sn = e.get('spellName', '')
            if sn:
                active_spell_names.add(sn)

    # Living summons owned by this character
    il = engine._get_initiative()
    my_summons = set()
    for e in il:
        if e.get('isSummon') and e.get('ownerId') == uid and (engine._get_combat_hp(e['userId']) or 0) > 0:
            my_summons.add(e.get('name', ''))

    filtered = []
    for ak, an in _ACTION_CACHE[phase_key]:
        if ak.startswith('SKILL_'):
            idx = int(ak.split('_')[1])
            sp = next((s for s in spells if s['index'] == idx), None)
            if sp:
                # MP check (including formula-based costs)
                mp_cost = sp.get('消耗mp', 0)
                mp_formula = sp.get('_mp_formula', '')
                if mp_formula == 'ceil_2d6_div_2':
                    mp_cost = 1  # Minimum possible
                elif mp_formula == 'summon_count_ceil_half':
                    mp_cost = 4  # Minimum: ceil(6/2)=3... let's use 4 for safety
                if isinstance(mp_cost, str):
                    try: mp_cost = int(mp_cost)
                    except: mp_cost = 0
                if mp_cost > cur_mp:
                    continue
                # Same-name effect still active
                if sp['name'] in active_spell_names:
                    continue
                # Same-template summon still alive
                summon_dup = False
                for eff in sp.get('effects', []):
                    if eff.get('type') == 5:
                        tmpl = eff.get('召唤物模板', '')
                        if tmpl and any(sn == tmpl or sn.endswith(f"的{tmpl}") for sn in my_summons):
                            summon_dup = True
                            break
                if summon_dup:
                    continue
        filtered.append((ak, an))

    # CAKE actions: available when ready cakes exist (type 6 制造物)
    if hasattr(engine, '_has_ready_cake') and engine._has_ready_cake():
        # EAT_CAKE: 自己食用蛋糕 → self-heal
        filtered.append(('EAT_CAKE__SELF', '食用蛋糕→自己'))
        # GIVE_CAKE: 送出蛋糕给队友 → support/heal teammate
        teammate_strats = [('T0', '最低HP队友'), ('T1', '最近队友'), ('T2', '最低MP队友')]
        # Only add GIVE_CAKE if there are living teammates
        il = engine._get_initiative()
        my_entry_gc = next((e for e in il if e['userId'] == uid), None)
        if my_entry_gc:
            my_team_gc = my_entry_gc.get('team', 'Y')
            living_teammates = [e for e in il if e['team'] == my_team_gc
                                and e['userId'] != uid
                                and (engine._get_combat_hp(e['userId']) or 0) > 0]
            if living_teammates:
                for ts, tl in teammate_strats:
                    filtered.append((f'GIVE_CAKE__{ts}', f'送出蛋糕→{tl}'))

    return filtered


def parse_action(action_key):
    """Parse action key into (base_action, target_strategy)."""
    if '__' in action_key:
        base, target = action_key.split('__')
        return base, target
    return action_key, 'T0'


def select_target_by_strategy(engine, uid, strategy, enemies):
    """Select target from enemy list based on strategy. Returns enemy userId or None."""
    if not enemies: return None
    if strategy == 'T0':  # 最低HP
        return min(enemies, key=lambda e: engine._get_combat_hp(e['userId']) or 9999)['userId']
    elif strategy == 'T1':  # 最近
        return enemies[0]['userId']
    elif strategy == 'T2':  # 最高威胁
        return max(enemies, key=lambda e: e.get('dex', 50))['userId']
    return enemies[0]['userId'] if enemies else None


def select_teammate_by_strategy(engine, uid, strategy, teammates):
    """Select target from teammate list based on strategy. Returns teammate userId or None.
    Used for support actions like GIVE_CAKE (送出蛋糕给队友).

    Strategies:
        T0: 最低HP (lowest HP teammate) — best for healing cakes
        T1: 最近 (nearest teammate)
        T2: 最低MP (lowest MP teammate) — best for MP/SAN cakes
    """
    if not teammates: return None
    if strategy == 'T0':  # 最低HP队友
        return min(teammates, key=lambda e: engine._get_combat_hp(e['userId']) or 9999)['userId']
    elif strategy == 'T1':  # 最近队友
        return teammates[0]['userId']
    elif strategy == 'T2':  # 最低MP队友
        return min(teammates, key=lambda e: engine.get_char(e['userId']).get_attr('魔力', 9999) if engine.get_char(e['userId']) else 9999)['userId']
    return teammates[0]['userId'] if teammates else None


# ---- Summon action space ----

def get_summon_actions(engine, uid):
    """Get available actions for a summon entity. Returns list of (action_key, description)."""
    init_list = engine._get_initiative()
    entry = next((e for e in init_list if e['userId'] == uid), None)
    if not entry: return []
    skills = entry.get('skills', [])
    if not skills: return []

    target_strats = [('T0', '最低HP'), ('T1', '最近'), ('T2', '最高威胁')]
    actions = []
    for i, sk in enumerate(skills):
        if sk.get("skill_type") == "zone_heal":
            # Zone skills don't target enemies — single cast action
            actions.append((f'SUMMON_ZONE_{i}', f'{sk["name"]}（领域）'))
        else:
            for ts, tl in target_strats:
                actions.append((f'SUMMON_SKILL_{i}__{ts}', f'{sk["name"]}→{tl}'))
    return actions


def execute_summon_action(engine, uid, action_key):
    """Execute a summon action. Uses the same skill execution as _summon_attack but with Q-chosen skill/target."""
    init_list = engine._get_initiative()
    entry = next((e for e in init_list if e['userId'] == uid), None)
    if not entry: return
    my_team = entry.get('team', 'Y')

    base, target_strat = parse_action(action_key)

    # Zone cast action — create healing field
    if base.startswith('SUMMON_ZONE_'):
        skill_idx = int(base.split('_')[-1])
        skills = entry.get('skills', [])
        if skill_idx < len(skills):
            sk = skills[skill_idx]
            zone_cooldown = entry.get("zone_cooldown", 0)
            if zone_cooldown <= 0:
                mp_cost = sk.get("mp_cost", 0)
                summon_mp = entry.get("summon_mp", 0)
                if summon_mp >= mp_cost:
                    entry["summon_mp"] = summon_mp - mp_cost
                    entry["zone_cooldown"] = sk.get("cooldown_rounds", 0) + sk.get("zone_duration", 3)
                    center = entry.get("coord", "A1")
                    effects = engine._get_effects()
                    effects.append({
                        'type': 'zone', 'center': center,
                        'radius': sk.get("zone_radius", 8),
                        'remainingRounds': sk.get("zone_duration", 3),
                        'tickDmg': '', 'tickHealHp': sk.get("zone_heal_hp", "2d6"),
                        'tickHealMp': '', 'centerFollows': 1, 'filter': 3,
                        'attributeDebuff': '', 'sourceUserId': uid,
                        'spellName': sk.get("name", "治愈领域"), 'spellIndex': -1,
                        'persistent': 0, 'stackable': 0,
                    })
                    engine._set_effects(effects)
        return

    enemies = [e for e in init_list if e['team'] != my_team and (engine._get_combat_hp(e['userId']) or 0) > 0]
    if not enemies: return

    if not base.startswith('SUMMON_SKILL_'):
        engine._summon_attack(uid)
        return

    skill_idx = int(base.split('_')[-1])
    skills = entry.get('skills', [])
    if skill_idx >= len(skills):
        engine._summon_attack(uid)
        return

    sk = skills[skill_idx]
    # Zone heal skills should not be used as attack — delegate to _summon_attack
    if sk.get("skill_type") == "zone_heal":
        engine._summon_attack(uid)
        return

    # Select target by strategy
    tid = select_target_by_strategy(engine, uid, target_strat, enemies)
    if not tid: tid = enemies[0]['userId']

    sv = sk['val']; dmg_dice = sk['dice']
    hits = sk.get('hits', 1)
    on_whiff_aoe = sk.get('on_whiff_aoe_dmg', '')
    on_whiff_mp = sk.get('on_whiff_mp_cost', 0)

    # Ignited summons use their ignite damage dice
    if entry.get('ignited'):
        dmg_dice = entry.get('ignite_dmg_dice', dmg_dice)

    # Battle spirit penalty dice
    pens = entry.get('battle_spirit_penalty_dice', 0)
    if pens > 0:
        atk_roll = max(random.randint(1, 100) for _ in range(pens + 1))
    else:
        atk_roll = random.randint(1, 100)

    if atk_roll > sv: return

    total_dmg = 0
    for _ in range(hits):
        if hits > 1 and random.randint(1, 100) > sv: continue
        dmg = roll_dice(dmg_dice)
        eff_dmg, _, _ = engine._absorb_damage_with_shield(tid, dmg)
        eff_dmg = engine._apply_shield_block(tid, eff_dmg)
        total_dmg += eff_dmg

    cur_hp = engine._get_combat_hp(tid) or 10
    engine._set_combat_hp(tid, max(0, cur_hp - total_dmg))

    # On-whiff AoE
    if on_whiff_aoe and total_dmg == 0:
        owner_id = entry.get('ownerId')
        if owner_id:
            oc = engine.get_char(owner_id); omp = oc.get_attr('魔力', 0) or 0
            if omp >= on_whiff_mp:
                oc.set_attr('魔力', omp - on_whiff_mp)
                for enemy in enemies:
                    aoe_dmg = roll_dice(on_whiff_aoe) // 2
                    ehp = engine._get_combat_hp(enemy['userId']) or 10
                    engine._set_combat_hp(enemy['userId'], max(0, ehp - aoe_dmg))

def execute_action(engine, uid, action_key):
    """Execute action with target selection. Action key may have __T suffix."""
    base, target_strat = parse_action(action_key)
    # Get enemies for target selection
    init_list = engine._get_initiative()
    my_entry = next((e for e in init_list if e['userId'] == uid), None)
    my_team = my_entry.get('team', 'Y') if my_entry else 'Y'
    enemies = [e for e in init_list if e['team'] != my_team and (engine._get_combat_hp(e['userId']) or 0) > 0]
    tid = select_target_by_strategy(engine, uid, target_strat, enemies)

    if base == 'BASIC_ATTACK':
        # Use full COC7 attack for training accuracy
        char = engine.get_char(uid); bn, bv = char.get_best_melee()
        dd = engine._get_damage_dice(uid, bn); p = char.get_attr("伤害贯穿",1); l = char.get_attr("致死骰",1) or 0
        if not tid: tid = engine._find_enemy(uid)
        if tid: engine._fast_coc7_attack(uid, tid, bn, bv, dd, p, l)
        return ""
    elif base == 'MOVE_TOWARD':
        return engine._fast_move(uid)
    elif base == 'END_TURN':
        engine._end_turn(uid)
        return ''
    elif base.startswith('SKILL_'):
        skill_num = int(base.split('_')[1])
        spells = engine.load_spells(uid)
        spell = next((s for s in spells if s['index'] == skill_num), None)
        if spell:
            target = tid or engine._smart_target(uid, spell)
            return engine._execute_spell(uid, target, spell)
    elif base == 'EAT_CAKE':
        # 自己食用蛋糕 → self-heal (uses _eat_cake with no target_id)
        if hasattr(engine, '_eat_cake') and hasattr(engine, '_has_ready_cake') and engine._has_ready_cake():
            engine._eat_cake(uid)
        return ''
    elif base == 'GIVE_CAKE':
        # 送出蛋糕给队友 → heal teammate
        if hasattr(engine, '_eat_cake') and hasattr(engine, '_has_ready_cake') and engine._has_ready_cake():
            # Get living teammates for target selection
            teammates = [e for e in init_list if e['team'] == my_team
                         and e['userId'] != uid
                         and (engine._get_combat_hp(e['userId']) or 0) > 0]
            if teammates:
                tm_id = select_teammate_by_strategy(engine, uid, target_strat, teammates)
                if tm_id:
                    engine._eat_cake(uid, tm_id)
        return ''
    return ''

def _fast_move(engine, uid):
    """Move one step toward nearest enemy."""
    init_list = engine._get_initiative()
    my_entry = next((e for e in init_list if e['userId'] == uid), None)
    if not my_entry: return ''
    my_team = my_entry.get('team', 'Y')
    my_coord = my_entry.get('coord', '')
    enemies = [e for e in init_list if e['team'] != my_team and (engine._get_combat_hp(e['userId']) or 0) > 0]
    if not enemies or not my_coord: return ''
    target = enemies[0]; ec = target.get('coord', '')
    if not ec: return ''
    mp, tp = parse_coord(my_coord), parse_coord(ec)
    if not mp or not tp: return ''
    dc = 0 if tp[0] == mp[0] else (1 if tp[0] > mp[0] else -1)
    dr = 0 if tp[1] == mp[1] else (1 if tp[1] > mp[1] else -1)
    for dcol, drow in [(dc, 0), (0, dr), (dc, dr)]:
        nc, nr = mp[0] + dcol, mp[1] + drow
        if 0 <= nc < 26 and 0 <= nr < 99:
            new_coord = format_coord(nc, nr)
            map_data = engine._get_map()
            if map_data and new_coord not in map_data.get('occupants', {}):
                if my_coord in map_data.get('occupants', {}):
                    del map_data['occupants'][my_coord]
                map_data['occupants'][new_coord] = uid
                engine._set_map(map_data)
                my_entry['coord'] = new_coord
                return ''
    return ''


# ============================================================
#  Q-Learning Trainer
# ============================================================

class QTrainer:
    def __init__(self):
        self.Q_solo = defaultdict(lambda: defaultdict(float))   # 单人/最后一人 Q[char_id][(state, action)]
        self.Q_team = defaultdict(lambda: defaultdict(float))   # 多人 Q[char_id][(state, action)]
        self.Q_summon = defaultdict(lambda: defaultdict(float)) # 召唤物 Q[summon_name][(state, action)]
        self.char_map = {}
        self.win_counts = defaultdict(int)
        self.battle_counts = defaultdict(int)
        self._react_dw = defaultdict(lambda: 50)  # Trainable dodge weight
        self._react_cw = defaultdict(lambda: 50)  # Trainable counter weight

    def init_characters(self):
        engine = CombatEngine()
        for c in ALL_CHARACTERS:
            uid = f"q_{c['serial']}"
            load_character_to_engine(engine, c, uid)
            if not c.get('pre_transformed'):
                engine.process_command(uid, '.hs')
            self.char_map[c['serial']] = uid

    def _char_key(self, uid):
        for serial, u in self.char_map.items():
            if u == uid: return serial
        return uid

    def _run_one_battle(self, team_a, team_b, map_size):
        """Run a single training battle and return Q-updates.
        Updates format: (table_type, ck, st, ak, reward, next_st)
        table_type: 'solo' | 'team' | 'summon'
        """
        engine = FastBattleEngine()
        a_uids = [self.char_map[s] for s in team_a]
        b_uids = [self.char_map[s] for s in team_b]
        for c in ALL_CHARACTERS:
            uid = self.char_map[c['serial']]
            load_character_to_engine(engine, c, uid)
            if not c.get('pre_transformed'): engine.process_command(uid, '.hs')
        engine.setup_battle(a_uids, b_uids, map_size)
        engine._set_actions({uid: {'主动':2,'附加':3} for uid in a_uids+b_uids})
        # Inject trainable reaction weights
        engine._react_dw = {uid: self._react_dw[uid] for uid in a_uids+b_uids}
        engine._react_cw = {uid: self._react_cw[uid] for uid in a_uids+b_uids}
        updates = []; winner = None; rc = 0; sc = 0
        while rc < 20 and sc < 200:
            sc += 1; state = engine._get_state()
            if not state or state.get('phase')!='active': break
            il = engine._get_initiative()
            if not il: break; rc = state.get('round',1)
            if rc != getattr(engine,'_last_zone_round',0):
                if any(e.get('type')=='zone' for e in engine._get_effects()): engine._process_zone_specials()
                if any(e.get('isSummon') for e in il):
                    engine._check_trinity_merge()
                    do = {e['userId'] for e in il if not e.get('isSummon') and (engine._get_combat_hp(e['userId'])or 0)<=0}
                    if do: engine._set_initiative([e for e in il if not (e.get('isSummon') and e.get('ownerId') in do)])
                for a in list(engine.__dict__.keys()):
                    if a.startswith('_xingshan_attacked_'): delattr(engine, a)
                engine._last_zone_round = rc
            do2 = {e['userId'] for e in il if not e.get('isSummon') and (engine._get_combat_hp(e['userId'])or 0)<=0}
            if do2: il = [e for e in il if not (e.get('isSummon') and e.get('ownerId') in do2)]; engine._set_initiative(il)
            ya = sum(1 for e in il if e['team']=='Y' and (engine._get_combat_hp(e['userId'])or 0)>0)
            xa = sum(1 for e in il if e['team']=='X' and (engine._get_combat_hp(e['userId'])or 0)>0)
            if ya==0: winner='X'; break
            if xa==0: winner='Y'; break
            entry = il[state['activeIndex']]; uid = entry['userId']
            hp = engine._get_combat_hp(uid) or 0; mt = entry.get('team','Y')
            if hp<=0:
                state['activeIndex']=(state['activeIndex']+1)%len(il)
                if state['activeIndex']==0:
                    state['round']=state.get('round',1)+1
                    acts_r = engine._get_actions()
                    for k in list(acts_r.keys()): acts_r[k]={'主动':2,'附加':3}
                    engine._set_actions(acts_r)
                    engine._tick_down(); engine._apply_zone_effects()
                engine._set_state(state); continue
            if entry.get('isSummon'):
                # Summon Q-training
                st = encode_summon_state(engine, uid)
                av = get_summon_actions(engine, uid)
                if av:
                    TEMPERATURE = 1.0
                    summon_name = entry.get('name', uid)
                    q_vals = [self.Q_summon[summon_name].get((st, aak), 0.0) for aak, aan in av]
                    # 状态 deformation for summons too
                    zt = random.randint(40, 80)  # summons have ~60 average 状态
                    deform_prob = (100 - zt) / 100.0 * 0.4
                    if random.random() < deform_prob:
                        scale = max(max(abs(q) for q in q_vals), 1.0)
                        q_vals = [q + random.uniform(-deform_prob, deform_prob) * scale for q in q_vals]
                    max_q = max(q_vals) if q_vals else 0
                    exp_vals = [math.exp((q - max_q) / TEMPERATURE) for q in q_vals]
                    total = sum(exp_vals)
                    probs = [e / total for e in exp_vals] if total > 0 else None
                    idx = random.choices(range(len(av)), weights=probs, k=1)[0]
                    ak, an = av[idx]
                    pd = engine.hp_diff(mt) - engine.hp_diff('X' if mt=='Y' else 'Y')
                    execute_summon_action(engine, uid, ak)
                    cd = engine.hp_diff(mt) - engine.hp_diff('X' if mt=='Y' else 'Y')
                    reward = (cd-pd)/max(abs(pd),abs(cd),1)
                    ns = encode_summon_state(engine, uid)
                    updates.append(('summon', summon_name, st, ak, reward, ns))
                else:
                    engine._summon_attack(uid)
                state['activeIndex']=(state['activeIndex']+1)%len(il)
                if state['activeIndex']==0:
                    state['round']=state.get('round',1)+1
                    acts_r = engine._get_actions()
                    for k in list(acts_r.keys()): acts_r[k]={'主动':2,'附加':3}
                    engine._set_actions(acts_r)
                    engine._tick_down(); engine._apply_zone_effects()
                engine._set_state(state); continue
            acts = engine._get_actions().get(uid,{'主动':2,'附加':3})
            if acts['主动']<=0: engine._end_turn(uid); continue
            ck = self._char_key(uid); st = encode_state(engine, uid)
            av = get_available_actions(engine, uid)
            if not av: engine._end_turn(uid); continue

            # Determine solo vs team
            living_allies = [e for e in il if e['team'] == mt
                             and e['userId'] != uid
                             and not e.get('isSummon')
                             and (engine._get_combat_hp(e['userId']) or 0) > 0]
            qt = self.Q_solo[ck] if not living_allies else self.Q_team[ck]
            table_type = 'solo' if not living_allies else 'team'

            # Weighted random sampling based on Q-values (softmax / Boltzmann)
            TEMPERATURE = 1.0
            q_vals = [qt.get((st, aak), 0.0) for aak, aan in av]

            # 状态 deformation: pollute Q-value pool with random +/- noise, then act on polluted weights
            zt = engine.get_char(uid).get_attr('状态', 60)
            deform_prob = (100 - zt) / 100.0 * 0.4
            if random.random() < deform_prob:
                scale = max(max(abs(q) for q in q_vals), 1.0)
                q_vals = [q + random.uniform(-deform_prob, deform_prob) * scale for q in q_vals]

            max_q = max(q_vals) if q_vals else 0
            exp_vals = [math.exp((q - max_q) / TEMPERATURE) for q in q_vals]
            total = sum(exp_vals)
            if total > 0:
                probs = [e / total for e in exp_vals]
            else:
                probs = None
            idx = random.choices(range(len(av)), weights=probs, k=1)[0]
            ak, an = av[idx]
            pd = engine.hp_diff(mt) - engine.hp_diff('X' if mt=='Y' else 'Y')
            execute_action(engine, uid, ak)
            is_xs_act = engine.get_char(uid).get_attr('技能不消耗主动', 0) == 1 and ak.startswith('SKILL_')
            is_bonus_act = ak.startswith('EAT_CAKE') or ak.startswith('GIVE_CAKE')
            if is_bonus_act:
                acts['附加'] = max(0, acts.get('附加', 0) - 1); engine._set_actions(engine._get_actions())
            elif not is_xs_act:
                acts['主动']-=1; engine._set_actions(engine._get_actions())
            if acts['主动']<=0: engine._end_turn(uid)
            cd = engine.hp_diff(mt) - engine.hp_diff('X' if mt=='Y' else 'Y')
            reward = (cd-pd)/max(abs(pd),abs(cd),1)
            ns = encode_state(engine, uid)
            updates.append((table_type, ck, st, ak, reward, ns))
        # Update reaction weights based on outcome
        if winner:
            for uid in a_uids + b_uids:
                ck = self._char_key(uid); won = (uid in a_uids and winner=='Y') or (uid in b_uids and winner=='X')
                if won:
                    self._react_dw[ck] = min(200, self._react_dw[ck] + 0.5)
                    self._react_cw[ck] = min(200, self._react_cw[ck] + 0.5)
                else:
                    self._react_dw[ck] = max(1, self._react_dw[ck] - 0.1)
                    self._react_cw[ck] = max(1, self._react_cw[ck] - 0.1)
        if winner:
            for s in team_a+team_b:
                ck = self._char_key(uid := self.char_map[s])
                st = (0,)*12; mt2 = 'Y' if s in team_a else 'X'
                tr = 3.0 if winner==mt2 else -3.0
                # Determine table type for terminal state
                il_end = engine._get_initiative()
                me_end = next((e for e in il_end if e['userId'] == self.char_map[s]), None)
                my_team_end = me_end.get('team', mt2) if me_end else mt2
                living_allies_end = [e for e in il_end if e['team'] == my_team_end
                                     and e['userId'] != self.char_map[s]
                                     and not e.get('isSummon')
                                     and (engine._get_combat_hp(e['userId']) or 0) > 0]
                end_table = 'solo' if not living_allies_end else 'team'
                terminal_aks = ['BASIC_ATTACK__T0','BASIC_ATTACK__T1','END_TURN','MOVE_TOWARD',
                               'EAT_CAKE__SELF','GIVE_CAKE__T0','GIVE_CAKE__T1','GIVE_CAKE__T2']
                for ak in terminal_aks:
                    updates.append((end_table, ck, st, ak, tr, None))
        return updates

    def train(self):
        def log(msg):
            print(msg, flush=True)
        log('Q-Learning AI Trainer')
        log(f'Generations: {GENERATIONS}, Battles/gen: {BATTLES_PER_GEN}')
        log(f'Alpha={ALPHA}, Gamma={GAMMA}, Temperature=1.0')
        log('')
        singles = list(self.char_map.keys())
        stats_log = []
        NUM_WORKERS = 12
        # Prepare picklable data for subprocesses
        all_char_data = [c for c in ALL_CHARACTERS]
        char_map_dict = dict(self.char_map)
        _ACTION_CACHE.clear()
        for gen in range(1, GENERATIONS + 1):
            gen_battles = 0
            total_reward = 0.0
            # Build all battle specs
            specs = []
            for _ in range(BATTLES_PER_GEN):
                if random.random() < 0.5:
                    a,b = random.sample(singles,2)
                    specs.append(([a],[b],random.choice(['6x6','8x8','10x10'])))
                else:
                    ch = random.sample(singles,6)
                    specs.append((ch[:3],ch[3:],random.choice(['10x10','20x20'])))
            # Submit ALL battles at once — no waiting between batches
            all_ups = []
            tasks = [(ta,tb,ms,all_char_data,char_map_dict,SUMMON_TEMPLATES) for ta,tb,ms in specs]
            completed = 0
            with ProcessPoolExecutor(max_workers=NUM_WORKERS) as ex:
                futs = {ex.submit(_mp_run_battle, t): i for i, t in enumerate(tasks)}
                for fut in as_completed(futs):
                    try:
                        ups = fut.result()
                        if ups: all_ups.extend(ups); gen_battles += 1
                    except Exception: pass
                    completed += 1
                    if completed % 20 == 0 or completed == BATTLES_PER_GEN:
                        recent = all_ups[-500:] if len(all_ups) > 500 else all_ups
                        r_sofar = sum(u[4] for u in recent) / max(1, len(recent))
                        log(f'  [{completed}/{BATTLES_PER_GEN}] avg_reward~{r_sofar:.3f}')
            # Apply Q-updates sequentially (main thread, no contention)
            # Update format: (table_type, ck, st, ak, reward, next_st)
            # table_type: 'solo' | 'team' | 'summon'
            for table_type, ck, st, ak, reward, next_st in all_ups:
                if table_type == 'summon':
                    tbl = self.Q_summon
                elif table_type == 'solo':
                    tbl = self.Q_solo
                else:
                    tbl = self.Q_team
                if next_st is None:
                    old_q = tbl[ck].get((st, ak), 0.0)
                    tbl[ck][(st, ak)] = old_q + ALPHA * (reward - old_q)
                else:
                    max_next = max((v for (s2, a2), v in tbl[ck].items() if s2 == next_st), default=0.0)
                    old_q = tbl[ck].get((st, ak), 0.0)
                    tbl[ck][(st, ak)] = old_q + ALPHA * (reward + GAMMA * max_next - old_q)
                total_reward += reward
            total_actions = sum(len(v) for v in self.Q_solo.values()) + sum(len(v) for v in self.Q_team.values()) + sum(len(v) for v in self.Q_summon.values())
            avg_reward = total_reward / max(1, gen_battles * 4)
            log(f'Gen {gen:3d}: {gen_battles} battles, {total_actions} Q-entries, avg_reward={avg_reward:.3f}')
            log(f'       solo:{sum(len(v) for v in self.Q_solo.values())} team:{sum(len(v) for v in self.Q_team.values())} summon:{sum(len(v) for v in self.Q_summon.values())}')
            stats_log.append((gen, gen_battles, total_actions, avg_reward))
        # Serialize all three tables
        def serialize_q(qdict):
            result = {}
            for ck, qd in qdict.items():
                result[ck] = {'|'.join(str(x) for x in s) + '__' + ak: v
                              for (s, ak), v in qd.items()}
            return result
        q_solo = serialize_q(self.Q_solo)
        q_team = serialize_q(self.Q_team)
        q_summon = serialize_q(self.Q_summon)
        weight_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ai_weights_pvp.json')
        with open(weight_path, 'w', encoding='utf-8') as f:
            json.dump({'Q_solo': q_solo, 'Q_team': q_team, 'Q_summon': q_summon,
                       'stats': stats_log, 'params': {
                'generations': GENERATIONS, 'battles_per_gen': BATTLES_PER_GEN,
                'alpha': ALPHA, 'gamma': GAMMA, 'temperature': 1.0
            }}, f, ensure_ascii=False, indent=2)
        log(f'Saved Q-tables to {weight_path}')
        return {'solo': self.Q_solo, 'team': self.Q_team, 'summon': self.Q_summon}

def load_q_table(path=None):
    """Load trained Q-tables for use in battle.
    Returns dict: {'solo': Q_solo, 'team': Q_team, 'summon': Q_summon}
    Backward-compat with old single-Q format.
    """
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ai_weights_pvp.json')
    if not os.path.exists(path): return None
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    def parse_qdict(qdict):
        """Parse a serialized Q-dict (string-keyed) into (state, action) tuple dict."""
        Q = defaultdict(lambda: defaultdict(float))
        for ck, entries in qdict.items():
            for key, val in entries.items():
                parts = key.split('__')
                state_str, action = parts[0], parts[1]
                state = tuple(int(v) for v in state_str.split('|'))
                # Backward compat: old dims → 12
                if len(state) == 10:
                    state = state + (1, 0)
                elif len(state) == 11:
                    state = state + (0,)
                Q[ck][(state, action)] = val
        return Q

    result = {}
    if 'Q_solo' in data and 'Q_team' in data:
        # New dual-table format
        result['solo'] = parse_qdict(data['Q_solo'])
        result['team'] = parse_qdict(data['Q_team'])
        result['summon'] = parse_qdict(data.get('Q_summon', {}))
    elif 'Q' in data:
        # Old single-table format: use same Q for both solo and team
        Q = parse_qdict(data['Q'])
        result['solo'] = Q
        result['team'] = Q
        result['summon'] = defaultdict(lambda: defaultdict(float))
    else:
        return None
    return result

if __name__ == '__main__':
    random.seed(42)
    trainer = QTrainer()
    trainer.init_characters()
    print(f'Characters loaded: {list(trainer.char_map.keys())}', flush=True)
    trainer.train()
