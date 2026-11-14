#!/usr/bin/env python3
"""
Q-Learning AI自训练系统 (ai_trainer.py)
=========================================
50代 × 100场对战，用HP差分奖励训练当前启用角色的行动策略。
使用 characters_data 的数据集进行训练。
输出 ai_weights.json 供 ai_battle.py 使用。
"""

import sys, os, random, math, json, itertools, time, copy
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from battle_engine import (CombatEngine, FastBattleEngine, roll_dice, roll_d100,
    is_in_melee_range, has_timing, parse_coord, format_coord, avg_damage,
    success_rank, rank_text, _get_attack_range, chebyshev_dist, _resolve_db, _get_db_str,
    ActionExecutionResult, action_committed)
from characters_data import ALL_CHARACTERS, load_character_to_engine, SUMMON_TEMPLATES
import battle_engine
battle_engine._SUMMON_TEMPLATES = SUMMON_TEMPLATES

# ============================================================
#  Hyperparameters
# ============================================================
GENERATIONS = 50
BATTLES_PER_GEN = 100
ALPHA = 0.1       # Learning rate
GAMMA = 0.9       # Discount factor
OUTPUT_FILE = 'ai_weights.json'  # Can be overridden by wrapper

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


def fine_bucket(val, max_val):
    """Bucket a ratio 0-1 into 0-9 (10 levels, ~10% each)."""
    if max_val <= 0: return 0
    r = val / max_val
    if r < 0.10: return 0   # 0-10%
    if r < 0.20: return 1   # 10-20%
    if r < 0.30: return 2   # 20-30%
    if r < 0.40: return 3   # 30-40%
    if r < 0.50: return 4   # 40-50%
    if r < 0.60: return 5   # 50-60%
    if r < 0.70: return 6   # 60-70%
    if r < 0.80: return 7   # 70-80%
    if r < 0.90: return 8   # 80-90%
    return 9                 # 90-100%


def get_character_combat_value(engine, uid):
    """Estimate a character's combat value as their best avg damage per action.
    Used to differentiate healing priorities: healing a DPS is more valuable
    than healing a support."""
    char = engine.get_char(uid)
    if not char: return 5.0  # default average

    # Best melee damage
    try:
        bn, bv = engine._get_attack_skill(uid, char)
        basic_dice = engine._get_damage_dice(uid, bn)
        basic_avg = avg_damage(basic_dice) if basic_dice else 0.0
    except Exception:
        basic_avg = 0.0

    # Best skill damage
    spells = char.spells or engine.load_spells(uid)
    best_skill_avg = 0.0
    for s in spells:
        if has_timing(s.get('时机', '2'), '2'):
            for eff in s.get('effects', []):
                if eff.get('type') == 1:
                    dmg_dice = eff.get('伤害骰', '')
                    if dmg_dice:
                        dmg_dice = _resolve_db(dmg_dice, _get_db_str(char))
                        best_skill_avg = max(best_skill_avg, avg_damage(dmg_dice))

    return max(basic_avg, best_skill_avg, 1.0)


# Precomputed average combat value for normalization
_COMBAT_VALUE_AVG = 8.0


def cake_reward_modifier(engine, actor_uid, ak):
    """Compute reward modifier for cake actions (EAT_CAKE / GIVE_CAKE).

    Two factors:
    1. Target combat value — healing a high-DPS ally is more valuable
       than self-healing a low-damage support.
    2. Overheal penalty — healing a near-full-HP target is mostly wasted,
       scaled by the target's HP ratio before the heal.

    Returns a multiplier in [0.0, ~2.5].
    """
    base_ak, target_strat = parse_action(ak)

    if base_ak == 'EAT_CAKE':
        target_uid = actor_uid
    elif base_ak == 'GIVE_CAKE':
        init_list = engine._get_initiative()
        my_entry = next((e for e in init_list if e['userId'] == actor_uid), None)
        if not my_entry: return 1.0
        my_team = my_entry.get('team', 'Y')
        teammates = [e for e in init_list if e['team'] == my_team
                     and e['userId'] != actor_uid
                     and not e.get('isSummon')
                     and ((engine._get_combat_hp(e['userId']) or 0) > 0
                          or engine._is_dying(e['userId']))]
        if not teammates: return 1.0
        target_uid = select_teammate_by_strategy(engine, actor_uid, target_strat, teammates)
        if not target_uid: return 1.0
    else:
        return 1.0

    # ── Target HP ratio (BEFORE the heal took effect) ──
    # We read current HP after the action, so we approximate:
    # the heal already happened; use the result HP minus typical cake
    # heal (~2d8+4 ≈ 13) to estimate pre-heal HP. This is approximate
    # but directionally correct.
    target_hp = engine._get_combat_hp(target_uid) or 0
    target_char = engine.get_char(target_uid)
    target_max_hp = target_char.get_attr('体力上限', max(target_hp, 1))
    hp_ratio = target_hp / max(target_max_hp, 1)

    # ── Overheal penalty ──
    if hp_ratio >= 0.95:
        overheal_factor = 0.10   # almost all wasted
    elif hp_ratio >= 0.85:
        overheal_factor = 0.25
    elif hp_ratio >= 0.70:
        overheal_factor = 0.50
    elif hp_ratio >= 0.50:
        overheal_factor = 0.75
    elif hp_ratio >= 0.30:
        overheal_factor = 0.90
    else:
        overheal_factor = 1.00   # critical save, full value

    # ── Target combat value ──
    target_cv = get_character_combat_value(engine, target_uid)
    cv_factor = target_cv / _COMBAT_VALUE_AVG

    return cv_factor * overheal_factor


def targeting_reward_modifier(engine, uid, ak):
    """Apply reward modifier based on target selection strategy.

    Differentiates T0-T3 so the Q-table learns that attacking player
    characters is usually better than attacking summons.

    T0 (lowest HP player):  Bonus  — focus fire on weakened players
    T1 (nearest):           Neutral — tactical positioning
    T2 (highest threat):    Bonus  — correct threat identification
    T3 (lowest HP summon):  Penalty— summon killing is often wasteful
    """
    base_ak, target_strat = parse_action(ak)

    # Only modify attack actions (not MOVE, EAT_CAKE, GIVE_CAKE, END_TURN, ZONE)
    if not (base_ak == 'BASIC_ATTACK' or base_ak.startswith('SKILL_')
            or base_ak.startswith('SUMMON_SKILL_')):
        return 1.0

    init_list = engine._get_initiative()
    # 隐藏召唤组员没有独立先攻条目——与线上一致地通过合成条目解析其阵营。
    my_entry = engine._unit_il_entry(uid, init_list)
    if not my_entry:
        return 1.0

    enemies = [e for e in engine._enemy_target_entries(uid)
               if (engine._get_combat_hp(e['userId']) or 0) > 0]

    has_non_summon = any(not e.get('isSummon') for e in enemies)

    if target_strat == 'T3':
        # Attacking lowest-HP summon: penalize when players exist
        if has_non_summon:
            return 0.55
        return 1.0  # only summons available — no penalty
    elif target_strat == 'T0':
        # Attacking lowest-HP player: bonus for focus fire
        if has_non_summon:
            return 1.15
        return 1.0
    elif target_strat == 'T2':
        # Threat-based targeting: mild bonus (usually correct)
        return 1.05
    # T1 (nearest): neutral — no modifier
    return 1.0


def encode_state(engine, uid):
    """Encode combat state for a character into a 22-dim hashable key."""
    char = engine.get_char(uid)
    init_list = engine._get_initiative()
    my_entry = next((e for e in init_list if e['userId'] == uid), None)
    if not my_entry: return None
    my_team = my_entry.get('team', 'Y')

    # Dim 0: own HP (fine bucket, 10 levels)
    my_hp = engine._get_combat_hp(uid) or 0
    my_max_hp = char.get_attr('体力上限', max(my_hp, 1))
    my_b = fine_bucket(my_hp, my_max_hp)

    # Dim 1-2: team HP (coarse bucket)
    team_hps = [engine._get_combat_hp(e['userId']) or 0 for e in init_list if e['team'] == my_team]
    enemy_hps = [engine._get_combat_hp(e['userId']) or 0 for e in init_list if e['team'] != my_team]
    team_avg = sum(team_hps) / max(1, len(team_hps))
    enemy_avg = sum(enemy_hps) / max(1, len(enemy_hps))
    team_max = max(team_hps + [1])
    enemy_max = max(enemy_hps + [1])
    tb = bucket(team_avg, team_max)
    eb = bucket(enemy_avg, enemy_max)

    # Dim 3: distance
    my_coord = my_entry.get('coord', '')
    enemies = [e for e in init_list if e['team'] != my_team and (engine._get_combat_hp(e['userId']) or 0) > 0]
    dist = 3
    if enemies and my_coord:
        for e in enemies:
            ec = e.get('coord', '')
            if ec:
                mp_xy, ep_xy = parse_coord(my_coord), parse_coord(ec)
                if mp_xy and ep_xy:
                    d = abs(mp_xy[0] - ep_xy[0]) + abs(mp_xy[1] - ep_xy[1])
                    if d == 0: dist = min(dist, 0)
                    elif d <= 2: dist = min(dist, 1)
                    else: dist = min(dist, 2)

    # Dim 4: MP ratio
    cur_mp = char.get_attr('魔力', 0) or 0
    max_mp = char.get_attr('魔力上限', max(cur_mp, 1))
    mp_b = bucket(cur_mp, max_mp)

    # Dim 5: n_enemies (exact, capped at 6)
    n_enemies = min(len(enemies), 6)

    # Dim 6: n_allies (living non-summon allies on same team, excluding self)
    allies = [e for e in init_list if e['team'] == my_team
              and e['userId'] != uid
              and not e.get('isSummon')
              and (engine._get_combat_hp(e['userId']) or 0) > 0]
    n_allies = min(len(allies), 6)

    # Spells
    spells = char.spells or engine.load_spells(uid)

    # Dim 7: n_skills (available active skills, capped at 4)
    n_skills = min(len([s for s in spells
                        if has_timing(s.get('时机','2'), '2')
                        and not has_timing(s.get('时机','2'), '1')]), 4)

    # Dim 8: skill_power = best damage skill / basic attack ratio
    bn, bv = engine._get_attack_skill(uid, char)
    basic_dice = engine._get_damage_dice(uid, bn)
    basic_avg = avg_damage(basic_dice) if basic_dice else 1.0
    basic_avg = max(basic_avg, 0.5)
    best_skill_dmg = 0.0
    has_unreactable = False
    for s in spells:
        if has_timing(s.get('时机','2'), '2'):
            for eff in s.get('effects', []):
                if eff.get('type') == 1:
                    dmg_dice = eff.get('伤害骰', '')
                    if dmg_dice:
                        dmg_dice = _resolve_db(dmg_dice, _get_db_str(char))
                        dmg_avg = avg_damage(dmg_dice)
                        best_skill_dmg = max(best_skill_dmg, dmg_avg)
                    if eff.get('可闪避性', eff.get('可反应性', 1)) == 0:
                        has_unreactable = True
    ratio = best_skill_dmg / basic_avg if best_skill_dmg > 0 else 0.0
    if ratio < 0.5: skill_power = 0
    elif ratio < 1.0: skill_power = 1
    elif ratio < 1.5: skill_power = 2
    elif ratio < 2.0: skill_power = 3
    elif ratio < 3.0: skill_power = 4
    elif ratio < 4.0: skill_power = 5
    elif ratio < 6.0: skill_power = 6
    elif ratio < 8.0: skill_power = 7
    elif ratio < 10.0: skill_power = 8
    else: skill_power = 9

    # Dim 9: has_unreactable damage skill
    has_ur = int(has_unreactable)

    # Dim 10: has_heal
    has_heal = int(any(any(e.get('type') in (3, 8) and e.get('回复hp')
                           for e in s.get('effects', []))
                       and has_timing(s.get('时机','2'), '2') for s in spells))

    # Dim 11: has_buff
    has_buff = int(any(any(e.get('type') == 4 for e in s.get('effects', []))
                       and has_timing(s.get('时机','2'), '2') for s in spells))

    # Dim 12: buffs_active
    buffs_active = int(len(engine._get_active_buffs(uid)) > 0)

    # Dim 13: has_cake — actor-scoped: personal inventory + own team's crafted pool
    has_cake = int(hasattr(engine, '_has_ready_cake') and engine._has_ready_cake(uid))

    # Dim 14: phase
    phase = getattr(char, 'phase', 1)

    # Dim 15: is_dying
    is_dying = int(engine._is_dying(uid) is not None)

    # Dim 16: has_ally_effect — skill that targets allies with non-pure-damage effects
    ALLY_TARGET_CODES = {'2', '3', '12', '13', '25', '125', '23', '14', '15', '35', '45'}
    has_ally_effect = 0
    for s in spells:
        if not has_timing(s.get('时机', '2'), '2'):
            continue
        for eff in s.get('effects', []):
            obj = str(eff.get('客体', '')).strip()
            # Check if target includes ally
            if obj in ALLY_TARGET_CODES and obj not in ('4', '5', '14', '15', '45'):
                # Has a non-damage element: heal, buff, summon, zone, etc.
                if eff.get('type') in (3, 4, 5, 8) or eff.get('回复hp') or eff.get('回复mp') or eff.get('友方行为'):
                    has_ally_effect = 1
                    break
        if has_ally_effect:
            break

    # Dim 17-21: control status flags
    has_silence = int(engine._has_status(uid, '沉默'))
    has_disarm = int(engine._has_status(uid, '缴械'))
    has_stun = int(engine._has_status(uid, '击晕'))
    has_root = int(engine._has_status(uid, '禁锢'))
    is_flying = int(engine._is_flying(uid))

    return (my_b, tb, eb, dist, mp_b, n_enemies, n_allies, n_skills,
            skill_power, has_ur, int(has_heal), int(has_buff), int(buffs_active),
            has_cake, phase, is_dying, has_ally_effect,
            has_silence, has_disarm, has_stun, has_root, is_flying)


def encode_summon_state(engine, uid):
    """Encode combat state for a summon entity into a hashable key.

    Hidden group members have no initiative slot of their own; resolve them via
    the engine's synthetic-entry helper so every living member can be trained.
    """
    init_list = engine._get_initiative()
    entry = engine._unit_il_entry(uid, init_list)
    if not entry: return None  # sentinel: summon not in initiative
    my_team = entry.get('team', 'Y')

    my_hp = engine._get_combat_hp(uid) or 0
    # 合成条目可能不带 max_hp；退回到角色的体力上限，最后才用当前 HP。
    my_max_hp = entry.get('max_hp')
    if my_max_hp is None:
        _ch = engine.get_char(uid)
        my_max_hp = _ch.get_attr('体力上限', 0) or my_hp or 1
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

    # Summons don't cast zones, always 0
    has_zone_active = 0

    # Dying state
    is_dying = int(engine._is_dying(uid) is not None)

    # Number of skills this summon has (0=1 skill, 1=2, 2=3+)
    skills = entry.get('skills', [])
    n_skills = min(len(skills) - 1, 2) if skills else 0
    if n_skills < 0: n_skills = 0

    # Ignited flag
    ignited = 1 if entry.get('ignited') else 0

    # Battle spirit penalty dice (from 环花暖)
    bs_penalty = min(entry.get('battle_spirit_penalty_dice', 0), 2)

    return (my_b, eb, dist, n_enemies, n_skills, ignited, bs_penalty, has_zone_active, is_dying)


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
    engine._summon_templates_snapshot = summon_templates
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
        # Phase2 injection for 木落(Y9): 30% chance to start in Phase2
        if c['serial'] == 'Y9' and random.random() < 0.3:
            char = engine.get_char(uid)
            char.phase = 2
            char.spells = None  # force reload of Phase2 spells
    engine.setup_battle(a_uids, b_uids, map_size)
    engine._set_actions({uid: {'主动': 2, '附加': 3} for uid in a_uids + b_uids})
    for uid in a_uids + b_uids:
        if engine._is_y14(uid):
            engine._get_actions()[uid]['主动'] = engine._calc_main_actions(uid)
    engine._react_dw = {uid: 50 for uid in a_uids + b_uids}
    engine._react_cw = {uid: 50 for uid in a_uids + b_uids}
    engine._react_bw = {uid: 0 for uid in a_uids + b_uids}

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

        # ── 派发前先做召唤组维护并刷新快照 ──
        # 代表阵亡提升/缺失条目修复/全灭组删除都在这里统一完成，
        # 之后重新读取 initiative/state，避免拿过期 activeIndex 派发行动。
        engine._reconcile_summon_groups()
        init_list = engine._get_initiative()
        state = engine._get_state()
        if not init_list or not state or state.get('phase') != 'active':
            # 引擎维护可能已判定战斗结束（phase='ended'），记录结果供终局奖励。
            _res = getattr(engine, '_battle_result', None)
            if _res and _res.get('winner'):
                winner = _res['winner']
            break
        round_count = state.get('round', 1)

        if round_count != getattr(engine, '_last_zone_round', 0):
            has_zones = any(e.get('type') == 'zone' for e in engine._get_effects())
            has_summons = any(e.get('isSummon') for e in engine._get_initiative())
            if has_zones: engine._process_zone_specials()
            if has_summons:
                engine._check_trinity_merge()
                # 主人死亡后的清理统一走 _remove_summons_of_owner：它会同步
                # 组/成员/地图并保留当前行动者索引（不再手写先攻过滤）。
                engine._reconcile_summon_groups()
                dead_owners = {e['userId'] for e in engine._get_initiative()
                               if not e.get('isSummon') and engine._is_final_dead(e['userId'])}
                for owner_uid in dead_owners:
                    engine._remove_summons_of_owner(owner_uid)
                if dead_owners:
                    engine._reconcile_summon_groups()
                    init_list = engine._get_initiative()
                    state = engine._get_state()
                    if not init_list or not state or state.get('phase') != 'active': break
            for attr in list(engine.__dict__.keys()):
                if attr.startswith('_xingshan_attacked_'): delattr(engine, attr)
            engine._last_zone_round = round_count

        dead_owners_r = {e['userId'] for e in init_list
                         if not e.get('isSummon') and engine._is_final_dead(e['userId'])}
        for owner_uid in dead_owners_r:
            engine._remove_summons_of_owner(owner_uid)
        if dead_owners_r:
            engine._reconcile_summon_groups()
            init_list = engine._get_initiative()
            state = engine._get_state()
            if not init_list or not state or state.get('phase') != 'active': break

        y_alive = sum(1 for e in init_list if e['team'] == 'Y' and (engine._get_combat_hp(e['userId']) or 0) > 0)
        x_alive = sum(1 for e in init_list if e['team'] == 'X' and (engine._get_combat_hp(e['userId']) or 0) > 0)
        if y_alive == 0: winner = 'X'; break
        if x_alive == 0: winner = 'Y'; break

        if state['activeIndex'] >= len(init_list):
            state['activeIndex'] = state['activeIndex'] % max(1, len(init_list))
        entry = init_list[state['activeIndex']]
        uid = entry['userId']
        hp = engine._get_combat_hp(uid) or 0
        my_team = entry.get('team', 'Y')
        is_summon = entry.get('isSummon', False)

        if hp <= 0:
            old_round = state.get('round', 1)
            engine._end_turn(uid)
            if (engine._get_state() or {}).get('round', old_round) > old_round:
                engine._reset_round_actions(character_main=2)
            continue

        if is_summon:
            # ── 共享动作、全员出手（训练路径）──
            # 一次组动作让当前存活成员各执行一次，整组只扣一次主动作。
            group_key = engine._summon_group_key(uid)
            summon_name = entry.get('name', uid)

            def _one_member(m_uid):
                st_m = encode_summon_state(engine, m_uid)
                av_m = get_summon_actions(engine, m_uid)
                if st_m is None or not av_m:
                    engine._summon_attack(m_uid)
                    return ([], False, None)
                ak_m, an_m = random.choice(av_m)
                prev_diff = engine.hp_diff(my_team) - engine.hp_diff('X' if my_team == 'Y' else 'Y')
                execute_summon_action(engine, m_uid, ak_m)
                curr_diff = engine.hp_diff(my_team) - engine.hp_diff('X' if my_team == 'Y' else 'Y')
                max_hp = max(abs(prev_diff), abs(curr_diff), 1)
                reward = (curr_diff - prev_diff) / max_hp
                reward *= targeting_reward_modifier(engine, m_uid, ak_m)
                next_st = encode_summon_state(engine, m_uid)
                if next_st is not None:
                    updates.append(('summon', summon_name, st_m, ak_m, reward, next_st))
                return ([], False, None)

            r = engine._execute_summon_group_action(group_key, _one_member)
            # 组动作期间可能发生代表阵亡/主人死亡，重新取快照后再推进。
            init_list = engine._get_initiative()
            state = engine._get_state()
            if not init_list or not state or state.get('phase') != 'active': break
            # 共享动作未耗尽则继续由该组行动；否则推进到下一个先攻条目
            if r.get('action_spent') and r.get('remaining', 0) > 0:
                if state.get('activeIndex', 0) >= len(init_list):
                    state['activeIndex'] %= len(init_list)
                    engine._set_state(state)
                continue
            old_round = state.get('round', 1)
            engine._end_turn(uid)
            if (engine._get_state() or {}).get('round', old_round) > old_round:
                engine._reset_round_actions(character_main=2)
            continue

        actions = engine._get_actions().get(uid, {'主动': 2, '附加': 3})
        if actions['主动'] <= 0 and actions.get('附加', 0) <= 0:
            engine._end_turn(uid); continue

        st = encode_state(engine, uid)
        if st is None: engine._end_turn(uid); continue
        available = affordable_actions(engine, uid, get_available_actions(engine, uid))
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
        result = execute_training_action(engine, uid, ak)
        if not action_committed(result):
            continue
        acts_now = engine._get_my_actions(uid)
        if acts_now.get('主动', 0) <= 0: engine._end_turn(uid)
        curr_diff = engine.hp_diff(my_team) - engine.hp_diff('X' if my_team == 'Y' else 'Y')
        max_hp = max(abs(prev_diff), abs(curr_diff), 1)
        reward = (curr_diff - prev_diff) / max_hp
        if ak.startswith('SKILL_'):
            # Tiered skill bonus based on skill_power (dim 8 of state)
            sp = st[8] if st and len(st) > 8 else 0
            reward += 0.01 * (sp + 1)
            # Healing bonus: boost when team HP is low
            skill_num = int(ak.split('_')[1])
            char = engine.get_char(uid)
            spells = char.spells or engine.load_spells(uid)
            spell = next((s for s in spells if s['index'] == skill_num), None)
            if spell and any(e.get('type') in (3, 8) and e.get('回复hp') for e in spell.get('effects', [])):
                team_hp_b = st[1] if st and len(st) > 1 else 3
                reward *= 1.0 + 0.3 * (3 - team_hp_b) / 3  # up to 1.3x when team badly hurt
        # Cake reward modifier: target combat value + overheal penalty
        if ak.startswith('EAT_CAKE') or ak.startswith('GIVE_CAKE'):
            reward *= cake_reward_modifier(engine, uid, ak)
        # Targeting strategy modifier: T0 bonus / T3 penalty
        reward *= targeting_reward_modifier(engine, uid, ak)
        next_st = encode_state(engine, uid)
        # Use serial for char key
        ck = None
        for c in all_char_data:
            if char_map_dict[c['serial']] == uid: ck = c['serial']; break
        if ck and next_st is not None: updates.append((table_type, ck, st, ak, reward, next_st))

    # Terminal rewards
    if winner is None:
        # 引擎在维护/回合推进中可能已直接判定胜负，补记结果供终局更新使用。
        _res = getattr(engine, '_battle_result', None)
        if _res and _res.get('winner'):
            winner = _res['winner']
    if winner:
        for s in team_a + team_b:
            uid = char_map_dict[s]
            ck = s
            st = encode_state(engine, uid)
            if st is None: continue
            mt2 = 'Y' if s in team_a else 'X'
            term_r = 3.0 if winner == mt2 else -3.0
            # Determine table type for terminal update
            init_list_end = engine._get_initiative()
            my_entry_end = engine._unit_il_entry(uid, init_list_end)
            my_team_end = my_entry_end.get('team', mt2) if my_entry_end else mt2
            living_allies_end = [e for e in engine._battle_unit_entries()
                                 if e['team'] == my_team_end
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

    target_strats = [('T0', '最低HP玩家'), ('T1', '最近'), ('T2', '最高威胁'), ('T3', '最低HP召唤物')]

    # Build static base cache (spell list + basic attack + move, without MP check)
    # Use phase-aware key so Phase 1 and Phase 2 have separate caches
    if phase_key not in _ACTION_CACHE:
        base = []
        for s in spells:
            timing = s.get('时机', '2')
            if (has_timing(timing, '2') or has_timing(timing, '3')) and not has_timing(timing, '1'):
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
    has_y14_target = not engine._is_y14(uid) or bool(engine._enemy_target_entries(uid))
    for ak, an in _ACTION_CACHE[phase_key]:
        if ak.startswith('BASIC_ATTACK') and not has_y14_target:
            continue
        if ak.startswith('SKILL_'):
            idx = int(ak.split('_')[1])
            sp = next((s for s in spells if s['index'] == idx), None)
            if sp:
                if engine._is_y14(uid):
                    if engine._y14_preflight(uid, sp):
                        continue
                    if all(str(e.get('客体', '')) == '1' for e in sp.get('effects', [])) and not ak.endswith('__T0'):
                        continue
                if engine._revive_preflight(uid, sp):
                    continue
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
                # 召唤数量上限：满额/累计耗尽才禁选；有名额时允许补召第二只
                summon_blocked = False
                for eff in sp.get('effects', []):
                    if eff.get('type') == 5:
                        tmpl = eff.get('召唤物模板', '')
                        if not tmpl:
                            continue
                        remain, _reason = engine._get_summon_quota(uid, tmpl)
                        if remain == 0:
                            summon_blocked = True
                            break
                if summon_blocked:
                    continue
        filtered.append((ak, an))

    # ── 射程过滤: 仅对敌方伤害动作检查可达性 ──
    my_entry = next((e for e in il if e['userId'] == uid), None)
    my_coord = my_entry.get('coord', '') if my_entry else ''
    enemies_range = [e for e in il if e['team'] != (my_entry.get('team','Y') if my_entry else 'Y')
                     and (engine._get_combat_hp(e['userId']) or 0) > 0]
    if enemies_range and my_coord:
        mv = engine._get_move_power(uid)
        # 预解析敌方坐标（避免每次chebyshev_dist重复parse）
        enemy_coords = []
        for e in enemies_range:
            ec = e.get('coord', '')
            if ec:
                ep = parse_coord(ec)
                if ep: enemy_coords.append(ep)
        my_ep = parse_coord(my_coord)
        range_filtered = []
        for ak, an in filtered:
            need_range_check = False
            atk_range = 2
            if ak.startswith('SKILL_'):
                idx = int(ak.split('_')[1])
                sp = next((s for s in spells if s['index'] == idx), None)
                if sp:
                    dmg_eff = next((e for e in sp.get('effects', [])
                                    if e.get('type') == 1
                                    and str(e.get('客体',0)) in ('4','5','45')), None)
                    if dmg_eff:
                        need_range_check = True
                        atk_range = _get_attack_range(spell_effect=dmg_eff)
            elif ak.startswith('BASIC_ATTACK'):
                need_range_check = True
                profile = engine._y14_attack_profile(uid)
                atk_range = profile[1] if profile else _get_attack_range(skill_name=engine._get_attack_skill(uid, char)[0])
            if need_range_check and my_ep:
                max_dist = atk_range + mv
                reachable = any(
                    max(abs(my_ep[0]-ep[0]), abs(my_ep[1]-ep[1])) <= max_dist
                    for ep in enemy_coords
                )
                if not reachable:
                    continue
            range_filtered.append((ak, an))
        filtered = range_filtered

    # ── Status filter: remove illegal actions based on control status ──
    status_filtered = []
    for ak, an in filtered:
        if ak.startswith('SKILL_') and not engine._can_cast_active_skill(uid):
            continue
        if ak.startswith('BASIC_ATTACK') and not engine._can_basic_attack(uid):
            continue
        if ak == 'MOVE_TOWARD' and not engine._can_move(uid):
            continue
        if ak == 'MOVE_TOWARD' and not engine._next_move_coord(uid):
            continue
        status_filtered.append((ak, an))
    filtered = status_filtered

    # CAKE actions: available when ready cakes exist (type 6 制造物)
    if hasattr(engine, '_has_ready_cake') and engine._has_ready_cake(uid):
        # EAT_CAKE: 自己食用蛋糕 → self-heal
        filtered.append(('EAT_CAKE__SELF', '食用蛋糕→自己'))
        # GIVE_CAKE: 送出蛋糕给队友 → support/heal teammate
        teammate_strats = [('T0', '最低HP队友'), ('T1', '最近队友'), ('T2', '最低MP队友')]
        # Only add GIVE_CAKE if there are living teammates
        il = engine._get_initiative()
        my_entry_gc = next((e for e in il if e['userId'] == uid), None)
        if my_entry_gc:
            my_team_gc = my_entry_gc.get('team', 'Y')
            living_teammates = [e for e in engine._battle_unit_entries() if e['team'] == my_team_gc
                                and engine._resolve_uid(e['userId']) != engine._resolve_uid(uid)
                                and not e.get('isSummon')
                                and ((engine._get_combat_hp(e['userId']) or 0) > 0
                                     or engine._is_dying(e['userId']))]
            if living_teammates:
                for ts, tl in teammate_strats:
                    filtered.append((f'GIVE_CAKE__{ts}', f'送出蛋糕→{tl}'))

    failed_marker, failed_keys = getattr(engine, '_training_failed_actions', (None, set()))
    state = engine._get_state() or {}
    marker = (uid, state.get('round'), state.get('activeIndex'))
    if failed_marker == marker:
        filtered = [(ak, an) for ak, an in filtered if ak not in failed_keys]
    return filtered


def parse_action(action_key):
    """Parse action key into (base_action, target_strategy)."""
    if '__' in action_key:
        base, target = action_key.split('__')
        return base, target
    return action_key, 'T0'


def action_slot_for(engine, uid, action_key):
    """Which budget slot an action consumes: '附加' or '主动'.

    - Cake eat/give and explicit movement always use an additional action.
    - A dual-timing skill (e.g. timing '23') prefers 附加 while one remains,
      and falls back to 主动 only when additional actions are exhausted.
    - Everything else uses a main action.
    """
    base, _ = parse_action(action_key)
    if base in ('EAT_CAKE', 'GIVE_CAKE', 'MOVE_TOWARD'):
        return '附加'
    acts = engine._get_my_actions(uid) if hasattr(engine, '_get_my_actions') else {}
    if base.startswith('SKILL_'):
        try:
            sn = int(base.split('_')[1])
        except (IndexError, ValueError):
            return '主动'
        char = engine.get_char(uid)
        spells = (char.spells if char and char.spells else None) or engine.load_spells(uid)
        spell = next((s for s in spells if s['index'] == sn), None)
        timing = spell.get('时机', '2') if spell else ''
        if has_timing(timing, '3'):
            if not has_timing(timing, '2') or acts.get('附加', 0) > 0:
                return '附加'
    return '主动'


def affordable_actions(engine, uid, actions):
    """Filter an action list to those whose budget slot still has points."""
    acts = engine._get_my_actions(uid)
    if acts.get('主动', 0) <= 0:
        return []
    out = []
    for ak, an in actions:
        if acts.get(action_slot_for(engine, uid, ak), 0) > 0:
            out.append((ak, an))
    return out


def _compute_threat(engine, enemy_entry):
    """计算敌方单位的单回合伤害期望（考虑命中率、行动轮数）。

    公式: threat = max(普攻期望, 技能期望) × 额外行动数
    其中: 普攻期望 = avg_damage(伤害值) × 斗殴/100
          技能期望 = max(avg_damage(伤害骰) × 成功率/100)
    召唤物: threat = Σ(avg_damage(dice) × val/100 × hits)
    """
    uid = enemy_entry['userId']

    # 召唤物：直接从 entry 取 skills
    if enemy_entry.get('isSummon'):
        total_threat = 0.0
        for sk in enemy_entry.get('skills', []):
            dice = sk.get('dice', '1d4')
            val = sk.get('val', 50)
            hits = sk.get('hits', 1)
            dmg_avg = avg_damage(dice)
            hit_rate = val / 100.0
            total_threat += dmg_avg * hit_rate * hits
        return total_threat if total_threat > 0 else enemy_entry.get('dex', 50)

    # 玩家角色
    char = engine.get_char(uid)
    if not char:
        return enemy_entry.get('dex', 50)  # fallback

    # 基本攻击期望
    bn, bv = engine._get_attack_skill(uid, char)
    basic_dice = engine._get_damage_dice(uid, bn)
    basic_avg = avg_damage(basic_dice) if basic_dice else 1.0
    basic_hit = bv / 100.0 if bv > 0 else 0.5
    basic_threat = basic_avg * basic_hit

    # 技能期望：取所有主动伤害技能的最大值（多效果法术累加）
    spells = char.spells or engine.load_spells(uid)
    best_skill_threat = 0.0
    for s in spells:
        if not has_timing(s.get('时机', '2'), '2'):
            continue
        spell_threat = 0.0
        for eff in s.get('effects', []):
            if eff.get('type') != 1:
                continue
            dmg_dice = eff.get('伤害骰', '')
            if not dmg_dice:
                continue
            dmg_dice = _resolve_db(dmg_dice, _get_db_str(char))
            dmg_avg = avg_damage(dmg_dice)
            sr = eff.get('成功率', 0)
            hit_rate = sr / 100.0 if sr > 0 else 1.0  # sr=0 = 必中
            spell_threat += dmg_avg * hit_rate
        if spell_threat > best_skill_threat:
            best_skill_threat = spell_threat

    best_threat = max(basic_threat, best_skill_threat)

    # 行动轮数：优先用引擎的动态行动数，fallback 到额外行动数属性
    actions_per_round = 1
    if hasattr(engine, '_get_dynamic_action_count'):
        actions_per_round = engine._get_dynamic_action_count(uid)
    if actions_per_round <= 1:
        extra = char.get_attr('额外行动数', None)
        if extra is None:
            extra = char.get_attr('回合行动数', 0)
        actions_per_round = extra or 0

    return best_threat * actions_per_round


def select_target_by_strategy(engine, uid, strategy, enemies):
    """Select target from enemy list based on strategy. Returns enemy userId or None."""
    if not enemies: return None
    if strategy == 'T0':  # 最低HP (only non-summon players)
        non_summons = [e for e in enemies if not e.get('isSummon')]
        if non_summons:
            return min(non_summons, key=lambda e: engine._get_combat_hp(e['userId']) or 9999)['userId']
        return min(enemies, key=lambda e: engine._get_combat_hp(e['userId']) or 9999)['userId']
    elif strategy == 'T1':  # 最近
        return enemies[0]['userId']
    elif strategy == 'T2':  # 最高威胁 = 单回合伤害期望最大
        return max(enemies, key=lambda e: _compute_threat(engine, e))['userId']
    elif strategy == 'T3':  # 最低HP召唤物
        summons = [e for e in enemies if e.get('isSummon')]
        if summons:
            return min(summons, key=lambda e: engine._get_combat_hp(e['userId']) or 9999)['userId']
        return enemies[0]['userId'] if enemies else None
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
        return min(teammates, key=lambda e: engine._get_combat_hp(e['userId']) if engine._get_combat_hp(e['userId']) is not None else 9999)['userId']
    elif strategy == 'T1':  # 最近队友
        entry = engine._unit_il_entry(uid, engine._get_initiative()) or {}
        origin = entry.get('coord', '')
        return min(teammates, key=lambda e: chebyshev_dist(origin, e.get('coord', ''))
                   if parse_coord(origin) and parse_coord(e.get('coord', '')) else float('inf'))['userId']
    elif strategy == 'T2':  # 最低MP队友
        return min(teammates, key=lambda e: engine.get_char(e['userId']).get_attr('魔力', 9999) if engine.get_char(e['userId']) else 9999)['userId']
    return teammates[0]['userId'] if teammates else None


# ---- Summon action space ----

def get_summon_actions(engine, uid):
    """Get available actions for a summon entity. Returns list of (action_key, description)."""
    entry = engine._unit_il_entry(uid, engine._get_initiative())
    if not entry: return []
    skills = entry.get('skills', [])
    if not skills: return []

    target_strats = [('T0', '最低HP玩家'), ('T1', '最近'), ('T2', '最高威胁'), ('T3', '最低HP召唤物')]
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
    """Execute a summon action. Returns list of detail lines for display."""
    init_list = engine._get_initiative()
    entry = engine._unit_il_entry(uid, init_list)
    if not entry: return []
    sname = entry.get('name', uid)
    lines = []

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
                    new_cooldown = sk.get("cooldown_rounds", 0) + sk.get("zone_duration", 3)
                    # 持久化到实际条目/隐藏组员元数据；合成条目写入不会自动落盘。
                    engine._set_summon_entry_fields(
                        uid, summon_mp=summon_mp - mp_cost, zone_cooldown=new_cooldown)
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
                    lines.append(f"{sname} 的【{sk.get('name', '治愈领域')}】（领域）")
                    lines.append(f"  → 创建治愈领域（半径{sk.get('zone_radius',8)}格，持续{sk.get('zone_duration',3)}回合，剩余MP:{summon_mp - mp_cost}）")
                else:
                    lines.append(f"{sname} MP不足，无法施放领域")
        return lines

    enemy_entries = engine._enemy_target_entries(uid)
    if not enemy_entries: return ["无可用目标"]

    if not base.startswith('SUMMON_SKILL_'):
        return engine._summon_attack(uid)

    skill_idx = int(base.split('_')[-1])
    skills = entry.get('skills', [])
    if skill_idx >= len(skills):
        return engine._summon_attack(uid)

    sk = skills[skill_idx]
    # Zone heal skills should not be used as attack — delegate to _summon_attack
    if sk.get("skill_type") == "zone_heal":
        return engine._summon_attack(uid)

    # Select target by strategy (enemy_entries统一包含隐藏的组成员)
    tid = select_target_by_strategy(engine, uid, target_strat, enemy_entries)
    if not tid: tid = enemy_entries[0]['userId']
    tname = next((e.get('name', tid) for e in enemy_entries if e['userId'] == tid), tid)

    sv = sk['val']; dmg_dice = sk['dice']
    sk_name = sk.get('name', '攻击')
    on_whiff_aoe = sk.get('on_whiff_aoe_dmg', '')
    on_whiff_mp = sk.get('on_whiff_mp_cost', 0)
    leth_val = entry.get("lethality", 0) or sk.get("lethality", 0)

    # Ignited summons use their ignite damage dice
    if entry.get('ignited'):
        dmg_dice = entry.get('ignite_dmg_dice', dmg_dice)

    # Battle spirit penalty dice
    pens = entry.get('battle_spirit_penalty_dice', 0)
    bp_suffix = f"p{pens}" if pens > 1 else ("p" if pens == 1 else "")

    # ── Delegate to _coc7_attack for unified combat (dice / damage / shield / lethality / reaction) ──
    pen = 1
    _, _, atk_lines = engine._coc7_attack(uid, tid, sk_name, sv, dmg_dice, pen, leth_val, bp_suffix=bp_suffix)
    lines.extend(atk_lines)

    # On-whiff AoE (post-attack: fires if target survived and no damage was dealt)
    if on_whiff_aoe:
        dmg_dealt = any("造成" in l for l in atk_lines)
        if not dmg_dealt:
            owner_id = entry.get('ownerId')
            if owner_id:
                oc = engine.get_char(owner_id); omp = oc.get_attr('魔力', 0) or 0
                if omp >= on_whiff_mp:
                    oc.set_attr('魔力', omp - on_whiff_mp)
                    lines.append(f"  消耗{on_whiff_mp}MP触发溅射!")
                    for enemy in enemy_entries:
                        aoe_dmg = roll_dice(on_whiff_aoe) // 2
                        ehp = engine._get_combat_hp(enemy['userId']) or 10
                        engine._set_combat_hp(enemy['userId'], max(0, ehp - aoe_dmg))
                        ename = enemy.get('name', enemy['userId'])
                        lines.append(f"  溅射 {ename}: {on_whiff_aoe}//2={aoe_dmg}点 → HP:{max(0, ehp - aoe_dmg)}")

    return lines

def execute_action(engine, uid, action_key):
    """Execute action with target selection. Action key may have __T suffix."""
    base, target_strat = parse_action(action_key)
    # Get enemies for target selection
    init_list = engine._get_initiative()
    my_entry = engine._unit_il_entry(uid, init_list)
    my_team = my_entry.get('team', 'Y') if my_entry else 'Y'
    # 敌方候选统一走引擎 helper：包含隐藏组成员、与线上目标规则一致。
    enemies = engine._enemy_target_entries(uid)
    tid = select_target_by_strategy(engine, uid, target_strat, enemies)

    if base == 'BASIC_ATTACK':
        # Use full COC7 attack for training accuracy
        char = engine.get_char(uid); bn, bv = engine._get_attack_skill(uid, char)
        dd = engine._get_damage_dice(uid, bn); p = char.get_attr("伤害贯穿",1); l = char.get_attr("致死骰",1) or 0
        if not tid: return ActionExecutionResult('没有攻击目标', False, 'no_target')
        if hasattr(engine, '_fast_coc7_attack'):
            engine._fast_coc7_attack(uid, tid, bn, bv, dd, p, l)
        else:
            engine._coc7_attack(uid, tid, bn, bv, dd, p, l)
        return ""
    elif base == 'MOVE_TOWARD':
        return _fast_move(engine, uid)
    elif base == 'END_TURN':
        engine._end_turn(uid)
        return ''
    elif base.startswith('SKILL_'):
        skill_num = int(base.split('_')[1])
        spells = engine.load_spells(uid)
        spell = next((s for s in spells if s['index'] == skill_num), None)
        if spell:
            reason = engine._revive_preflight(uid, spell)
            if reason:
                return ActionExecutionResult(reason, False, 'no_revive_target')
            timing = spell.get('时机', '2')
            acts_now = engine._get_my_actions(uid) if hasattr(engine, '_get_my_actions') else {}
            if has_timing(timing, '3') and (not has_timing(timing, '2')
                                            or acts_now.get('附加', 0) > 0):
                return engine._additional_action(uid, f's{skill_num}')
            target = tid if tid is not None else engine._smart_target(uid, spell)
            return engine._execute_spell(uid, target, spell)
    elif base == 'EAT_CAKE':
        if hasattr(engine, '_eat_cake') and engine._has_ready_cake(uid):
            text, ok = engine._eat_cake(uid)
            return ActionExecutionResult(text, ok, '' if ok else 'no_cake', '附加')
        return ActionExecutionResult('无可用的蛋糕', False, 'no_cake', '附加')
    elif base == 'GIVE_CAKE':
        if hasattr(engine, '_eat_cake') and engine._has_ready_cake(uid):
            teammates = [e for e in engine._battle_unit_entries()
                         if e.get('team') == my_team and not e.get('isSummon')
                         and engine._resolve_uid(e['userId']) != engine._resolve_uid(uid)
                         and ((engine._get_combat_hp(e['userId']) or 0) > 0
                              or engine._is_dying(e['userId']))]
            if teammates:
                tm_id = select_teammate_by_strategy(engine, uid, target_strat, teammates)
                if tm_id:
                    text, ok = engine._eat_cake(uid, tm_id)
                    return ActionExecutionResult(text, ok, '' if ok else 'no_cake', '附加')
        return ActionExecutionResult('无可给予的蛋糕或队友', False, 'no_cake_target', '附加')
    return ActionExecutionResult('未知或不可执行的动作', False, 'invalid_action')


def execute_training_action(engine, uid, action_key):
    """Charge once on commit; suppress rejected choices until another action succeeds."""
    slot = action_slot_for(engine, uid, action_key)
    acts = engine._get_my_actions(uid)
    if acts.get('主动', 0) <= 0 or acts.get(slot, 0) <= 0:
        return ActionExecutionResult('动作已用尽', False, 'no_actions', slot)
    state = engine._get_state() or {}
    marker = (uid, state.get('round'), state.get('activeIndex'))
    result = execute_action(engine, uid, action_key)
    if action_committed(result):
        engine._consume_action(uid, slot)
        engine._training_failed_actions = (None, set())
    else:
        old_marker, keys = getattr(engine, '_training_failed_actions', (None, set()))
        keys = set(keys) if old_marker == marker else set()
        keys.add(action_key)
        engine._training_failed_actions = (marker, keys)
        if len(keys) >= 5:
            engine._end_turn(uid)
    return result

def _fast_move(engine, uid):
    """Move one step toward nearest enemy."""
    coord = engine._next_move_coord(uid)
    if coord:
        return engine._move_explicit(uid, coord)
    return ActionExecutionResult('没有可执行的移动', False, 'invalid_move', '附加')


# ============================================================
#  Summon skill parser (shared helper)
# ============================================================

def _parse_summon_skills(tmpl):
    """Parse summon skills from a SUMMON_TEMPLATES entry into the format
    used by initiative entries and _summon_attack / execute_summon_action."""
    skills_raw = tmpl.get("skills", ["斗殴:50 1d4"])
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
            parts = str(sk_raw).split()
            nv = parts[0].split(":")
            parsed.append({
                "name": nv[0], "val": int(nv[1]) if len(nv) > 1 else 50,
                "dice": parts[1] if len(parts) > 1 else "1d4",
                "hits": 1, "on_whiff_aoe_dmg": "", "on_whiff_mp_cost": 0,
                "skill_type": "attack", "zone_heal_hp": "", "zone_radius": 0,
                "zone_duration": 0, "mp_cost": 0, "cooldown_rounds": 0,
            })
    return parsed


# ============================================================
#  Summon-only battle runner (for ProcessPoolExecutor)
# ============================================================

def _mp_run_summon_battle(args):
    """Standalone summon-vs-summon battle for Q-training summon actions.
    Creates pseudo-initiative entries directly from SUMMON_TEMPLATES —
    no Character objects, no spells, no .hs processing.

    Args: (team_a_templates, team_b_templates, map_size, summon_templates)
    Returns: list of ('summon', template_name, state, action, reward, next_state)
    """
    team_a, team_b, map_size, summon_templates = args
    engine = FastBattleEngine()
    engine._summon_templates_snapshot = summon_templates
    if summon_templates:
        import battle_engine as _be
        _be._SUMMON_TEMPLATES = summon_templates

    import math as _math

    w, h = map(int, map_size.split("x"))
    gid = f"sumbat_{random.randint(10000, 99999)}"
    engine.group_id = gid
    engine._set_map({
        "width": w, "height": h, "entryRow": _math.ceil(h / 2),
        "obstacles": {}, "occupants": {},
    })

    init_list = []
    map_data = engine._get_map()
    all_uids = []

    def _make_entry(tmpl_name, team_label, col, idx, count):
        tmpl = summon_templates.get(tmpl_name)
        if not tmpl or tmpl.get("_meta"):
            return None
        sid = f"sum_{team_label}_{idx}_{random.randint(1000, 9999)}"
        hp = tmpl.get("HP", 10)
        dex = tmpl.get("DEX", 50)
        skills = _parse_summon_skills(tmpl)
        row = min(h - 1, _math.ceil(h / 2) + idx - count // 2)
        coord = format_coord(col, row)
        map_data["occupants"][coord] = sid
        engine._init_combat_hp(sid, hp)
        return {
            "userId": sid, "name": tmpl_name, "team": team_label, "dex": dex,
            "initRoll": dex + random.randint(1, 20), "coord": coord,
            "isSummon": True, "ownerId": sid, "skills": skills,
            "skill_name": skills[0]["name"], "skill_val": skills[0]["val"],
            "dmg_dice": skills[0]["dice"],
            "zone_skills": [sk for sk in skills if sk.get("skill_type") == "zone_heal"],
            "zone_cooldown": 0, "summon_mp": tmpl.get("MP", 0),
            "react_dodge_w": tmpl.get("react_dodge", 50),
            "react_counter_w": tmpl.get("react_counter", 50),
            "shield_block": tmpl.get("shield_block", 0),
            "shield_block_hp": tmpl.get("shield_block", 0),
            "flying": tmpl.get("flying", False),
            "max_hp": hp,
        }

    for i, name in enumerate(team_a):
        e = _make_entry(name, "Y", 1, i, len(team_a))
        if e: init_list.append(e); all_uids.append(e["userId"])
    for i, name in enumerate(team_b):
        e = _make_entry(name, "X", w - 2, i, len(team_b))
        if e: init_list.append(e); all_uids.append(e["userId"])

    if len(init_list) < 2:
        return []  # Need at least 2 summons to fight

    engine._set_map(map_data)
    init_list.sort(key=lambda e: (-e["initRoll"], -e["dex"]))
    engine._set_initiative(init_list)
    engine._set_actions({
        e["userId"]: {"主动": summon_templates.get(e["name"], {}).get("行动次数", 1), "附加": 1}
        for e in init_list
    })
    engine._set_state({"phase": "active", "round": 1, "activeIndex": 0})

    updates = []
    round_count = 0
    step_count = 0

    while round_count < 20 and step_count < 300:
        step_count += 1
        state = engine._get_state()
        if not state or state.get("phase") != "active":
            break
        il = engine._get_initiative()
        if not il:
            break
        round_count = state.get("round", 1)

        # Win condition
        y_alive = sum(1 for e in il if e["team"] == "Y" and (engine._get_combat_hp(e["userId"]) or 0) > 0)
        x_alive = sum(1 for e in il if e["team"] == "X" and (engine._get_combat_hp(e["userId"]) or 0) > 0)
        if y_alive == 0 or x_alive == 0:
            break

        entry = il[state["activeIndex"]]
        uid = entry["userId"]
        hp = engine._get_combat_hp(uid) or 0
        my_team = entry.get("team", "Y")

        if hp <= 0:
            state["activeIndex"] = (state["activeIndex"] + 1) % len(il)
            if state["activeIndex"] == 0:
                state["round"] = state.get("round", 1) + 1
                engine._tick_down()
                engine._apply_zone_effects()
                acts_r = engine._get_actions()
                for k in list(acts_r.keys()):
                    acts_r[k] = {"主动": 2, "附加": 3}
                engine._set_actions(acts_r)
            engine._set_state(state)
            continue

        # Summon Q-training turn
        st = encode_summon_state(engine, uid)
        if st is None: engine._end_turn(uid); continue
        av = get_summon_actions(engine, uid)
        if av:
            ak, an = random.choice(av)
            prev_diff = engine.hp_diff(my_team) - engine.hp_diff("X" if my_team == "Y" else "Y")
            execute_summon_action(engine, uid, ak)
            curr_diff = engine.hp_diff(my_team) - engine.hp_diff("X" if my_team == "Y" else "Y")
            max_hp = max(abs(prev_diff), abs(curr_diff), 1)
            reward = (curr_diff - prev_diff) / max_hp
            next_st = encode_summon_state(engine, uid)
            sname = entry.get("name", uid)
            if next_st is not None:
                updates.append(("summon", sname, st, ak, reward, next_st))
        else:
            engine._summon_attack(uid)

        state["activeIndex"] = (state["activeIndex"] + 1) % len(il)
        if state["activeIndex"] == 0:
            state["round"] = state.get("round", 1) + 1
            engine._tick_down()
            engine._apply_zone_effects()
            acts_r = engine._get_actions()
            for k in list(acts_r.keys()):
                acts_r[k] = {"主动": 2, "附加": 3}
            engine._set_actions(acts_r)
        engine._set_state(state)

    return updates


# ============================================================
#  Q-Learning Trainer
# ============================================================

def merge_untrained_tables(previous, trained, characters, summons):
    """Replace trained entries while preserving every untrained entry verbatim."""
    result = {}
    for name, active in [('Q_solo', characters), ('Q_team', characters), ('Q_summon', summons)]:
        old = previous.get(name, previous.get('Q', {}) if name != 'Q_summon' else {})
        if not isinstance(old, dict):
            raise ValueError(f'原权重文件中的 {name} 不是有效训练表')
        result[name] = {key: value for key, value in old.items() if key not in active}
        result[name].update(trained[name])
    return result


class QTrainer:
    def __init__(self):
        self.Q_solo = defaultdict(lambda: defaultdict(float))   # 单人/最后一人 Q[char_id][(state, action)]
        self.Q_team = defaultdict(lambda: defaultdict(float))   # 多人 Q[char_id][(state, action)]
        self.Q_summon = defaultdict(lambda: defaultdict(float)) # 召唤物 Q[summon_name][(state, action)]
        self.char_map = {}
        self.characters = []
        self.summon_templates = {}
        self.training_summons = []
        self.preserved_characters = []
        self.preserved_summons = []
        self.win_counts = defaultdict(int)
        self.battle_counts = defaultdict(int)
        self._react_dw = defaultdict(lambda: 50)  # Trainable dodge weight
        self._react_cw = defaultdict(lambda: 50)  # Trainable counter weight
        self._react_bw = defaultdict(lambda: 0)   # Trainable block weight

    def init_characters(self):
        # Resolve the live canonical module at training start. A from-imported
        # ALL_CHARACTERS list becomes stale after browser saves rebind it.
        import characters_data as data
        self.characters = copy.deepcopy(data.ALL_CHARACTERS)
        self.summon_templates = copy.deepcopy(data.SUMMON_TEMPLATES)
        self.char_map.clear()
        referenced = set()

        def collect(value):
            if isinstance(value, dict):
                template = value.get('召唤物模板')
                if isinstance(template, str) and template in self.summon_templates:
                    referenced.add(template)
                for nested in value.values():
                    collect(nested)
            elif isinstance(value, (list, tuple)):
                for nested in value:
                    collect(nested)

        collect(self.characters)
        self.training_summons = [name for name, template in self.summon_templates.items()
                                 if name in referenced and not template.get('_meta')]
        engine = CombatEngine()
        engine._summon_templates_snapshot = self.summon_templates
        for c in self.characters:
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
        engine._summon_templates_snapshot = self.summon_templates
        a_uids = [self.char_map[s] for s in team_a]
        b_uids = [self.char_map[s] for s in team_b]
        for c in self.characters:
            uid = self.char_map[c['serial']]
            load_character_to_engine(engine, c, uid)
            if not c.get('pre_transformed'): engine.process_command(uid, '.hs')
        engine.setup_battle(a_uids, b_uids, map_size)
        engine._set_actions({uid: {'主动':2,'附加':3} for uid in a_uids+b_uids})
        for uid in a_uids + b_uids:
            if engine._is_y14(uid):
                engine._get_actions()[uid]['主动'] = engine._calc_main_actions(uid)
        # Inject trainable reaction weights
        engine._react_dw = {uid: self._react_dw[uid] for uid in a_uids+b_uids}
        engine._react_cw = {uid: self._react_cw[uid] for uid in a_uids+b_uids}
        engine._react_bw = {uid: self._react_bw[uid] for uid in a_uids+b_uids}
        updates = []; winner = None; rc = 0; sc = 0
        while rc < 20 and sc < 200:
            sc += 1; state = engine._get_state()
            if not state or state.get('phase')!='active': break
            il = engine._get_initiative()
            if not il: break; rc = state.get('round',1)
            # ── 派发前先做召唤组维护并刷新快照 ──
            engine._reconcile_summon_groups()
            il = engine._get_initiative()
            state = engine._get_state()
            if not il or not state or state.get('phase') != 'active': break
            rc = state.get('round', 1)
            if rc != getattr(engine,'_last_zone_round',0):
                if any(e.get('type')=='zone' for e in engine._get_effects()): engine._process_zone_specials()
                if any(e.get('isSummon') for e in il):
                    engine._check_trinity_merge()
                    # 主人死亡的清理统一走 _remove_summons_of_owner（保留行动者索引）。
                    engine._reconcile_summon_groups()
                    do = {e['userId'] for e in engine._get_initiative()
                          if not e.get('isSummon') and engine._is_final_dead(e['userId'])}
                    for owner_uid in do:
                        engine._remove_summons_of_owner(owner_uid)
                    if do:
                        engine._reconcile_summon_groups()
                        il = engine._get_initiative()
                        state = engine._get_state()
                        if not il or not state or state.get('phase') != 'active': break
                for a in list(engine.__dict__.keys()):
                    if a.startswith('_xingshan_attacked_'): delattr(engine, a)
                engine._last_zone_round = rc
            do2 = {e['userId'] for e in il
                   if not e.get('isSummon') and engine._is_final_dead(e['userId'])}
            for owner_uid in do2:
                engine._remove_summons_of_owner(owner_uid)
            if do2:
                engine._reconcile_summon_groups()
                il = engine._get_initiative()
                state = engine._get_state()
                if not il or not state or state.get('phase') != 'active': break
            ya = sum(1 for e in il if e['team']=='Y' and (engine._get_combat_hp(e['userId'])or 0)>0)
            xa = sum(1 for e in il if e['team']=='X' and (engine._get_combat_hp(e['userId'])or 0)>0)
            if ya==0: winner='X'; break
            if xa==0: winner='Y'; break
            entry = il[state['activeIndex']]; uid = entry['userId']
            hp = engine._get_combat_hp(uid) or 0; mt = entry.get('team','Y')
            if hp<=0:
                old_round = state.get('round', 1)
                engine._end_turn(uid)
                if (engine._get_state() or {}).get('round', old_round) > old_round:
                    engine._reset_round_actions(character_main=2)
                continue
            if entry.get('isSummon'):
                # Summon Q-training —— 与 _mp_run_battle 一样按共享组动作池行动，
                # 一次组动作只扣一次主动作，存活成员各出手一次。
                group_key = engine._summon_group_key(uid)
                summon_name = entry.get('name', uid)

                def _one_member(m_uid):
                    st = encode_summon_state(engine, m_uid)
                    if st is None:
                        # 合成条目不可用：退回普通召唤攻击，不额外推进回合。
                        engine._summon_attack(m_uid)
                        return ([], False, None)
                    av = get_summon_actions(engine, m_uid)
                    if not av:
                        engine._summon_attack(m_uid)
                        return ([], False, None)
                    TEMPERATURE = 1.0
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
                    execute_summon_action(engine, m_uid, ak)
                    cd = engine.hp_diff(mt) - engine.hp_diff('X' if mt=='Y' else 'Y')
                    reward = (cd-pd)/max(abs(pd),abs(cd),1)
                    reward *= targeting_reward_modifier(engine, m_uid, ak)
                    ns = encode_summon_state(engine, m_uid)
                    if ns is not None:
                        updates.append(('summon', summon_name, st, ak, reward, ns))
                    return ([], False, None)

                r = engine._execute_summon_group_action(group_key, _one_member)
                # 组动作期间可能发生代表阵亡/主人死亡，重新取快照后再推进。
                il = engine._get_initiative()
                state = engine._get_state()
                if not il or not state or state.get('phase') != 'active': break
                # 共享动作未耗尽则继续由该组行动；否则推进到下一个先攻条目。
                if r.get('action_spent') and r.get('remaining', 0) > 0:
                    if state.get('activeIndex', 0) >= len(il):
                        state['activeIndex'] %= len(il)
                        engine._set_state(state)
                    continue
                old_round = state.get('round', 1)
                engine._end_turn(uid)
                if (engine._get_state() or {}).get('round', old_round) > old_round:
                    engine._reset_round_actions(character_main=2)
                continue
            acts = engine._get_actions().get(uid,{'主动':2,'附加':3})
            if acts['主动']<=0 and acts.get('附加',0)<=0:
                engine._end_turn(uid); continue
            ck = self._char_key(uid); st = encode_state(engine, uid)
            if st is None: engine._end_turn(uid); continue
            av = affordable_actions(engine, uid, get_available_actions(engine, uid))
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
            q_vals = []
            for aak, aan in av:
                q = (engine._y14_action_q(qt, st, aak) if engine._is_y14(uid)
                     else qt.get((st, aak), 0.0))
                # P1: Optimism bias for strategic actions — encourage AI to explore them
                if q == 0.0 and not engine._is_y14(uid):
                    an_str = str(aan)
                    if '卡带切换' in an_str:
                        q = 4.0  # High bias: cartridge switching unlocks new abilities
                    elif '模式切换' in an_str:
                        q = 2.0  # Medium bias: mode toggling improves effectiveness
                    elif '切换·' in an_str:
                        q = 2.0  # Medium bias: weapon switching for 林白
                q_vals.append(q)

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
            result = execute_training_action(engine, uid, ak)
            if not action_committed(result):
                continue
            if engine._get_my_actions(uid).get('主动', 0) <= 0: engine._end_turn(uid)
            cd = engine.hp_diff(mt) - engine.hp_diff('X' if mt=='Y' else 'Y')
            reward = (cd-pd)/max(abs(pd),abs(cd),1)
            if ak.startswith('SKILL_'):
                # Tiered skill bonus based on skill_power (dim 8 of state)
                sp = st[8] if st and len(st) > 8 else 0
                reward += 0.01 * (sp + 1)
                # Healing bonus: boost when team HP is low
                skill_num = int(ak.split('_')[1])
                char = engine.get_char(uid)
                spells = char.spells or engine.load_spells(uid)
                spell = next((s for s in spells if s['index'] == skill_num), None)
                if spell and any(e.get('type') in (3, 8) and e.get('回复hp') for e in spell.get('effects', [])):
                    team_hp_b = st[1] if st and len(st) > 1 else 3
                    reward *= 1.0 + 0.3 * (3 - team_hp_b) / 3
            # Cake reward modifier: target combat value + overheal penalty
            if ak.startswith('EAT_CAKE') or ak.startswith('GIVE_CAKE'):
                reward *= cake_reward_modifier(engine, uid, ak)
            # Targeting strategy modifier: T0 bonus / T3 penalty
            reward *= targeting_reward_modifier(engine, uid, ak)
            ns = encode_state(engine, uid)
            if ns is not None:
                updates.append((table_type, ck, st, ak, reward, ns))
        # Update reaction weights based on outcome
        if winner is None:
            _res = getattr(engine, '_battle_result', None)
            if _res and _res.get('winner'):
                winner = _res['winner']
        if winner:
            for uid in a_uids + b_uids:
                ck = self._char_key(uid); won = (uid in a_uids and winner=='Y') or (uid in b_uids and winner=='X')
                if won:
                    self._react_dw[ck] = min(200, self._react_dw[ck] + 0.5)
                    self._react_cw[ck] = min(200, self._react_cw[ck] + 0.5)
                    self._react_bw[ck] = min(200, self._react_bw[ck] + 0.5)
                else:
                    self._react_dw[ck] = max(1, self._react_dw[ck] - 0.1)
                    self._react_cw[ck] = max(1, self._react_cw[ck] - 0.1)
                    self._react_bw[ck] = max(0, self._react_bw[ck] - 0.1)
        if winner:
            for s in team_a+team_b:
                uid = self.char_map[s]
                ck = self._char_key(uid)
                st = encode_state(engine, uid)
                if st is None: continue
                mt2 = 'Y' if s in team_a else 'X'
                tr = 3.0 if winner==mt2 else -3.0
                # Determine table type for terminal state
                il_end = engine._get_initiative()
                me_end = engine._unit_il_entry(uid, il_end)
                my_team_end = me_end.get('team', mt2) if me_end else mt2
                living_allies_end = [e for e in engine._battle_unit_entries()
                                     if e['team'] == my_team_end
                                     and e['userId'] != uid
                                     and not e.get('isSummon')
                                     and (engine._get_combat_hp(e['userId']) or 0) > 0]
                end_table = 'solo' if not living_allies_end else 'team'
                for ak, _ in get_available_actions(engine, uid):
                    updates.append((end_table, ck, st, ak, tr, None))
                # Cross-pollinate other table (matching _mp_run_battle behavior)
                other_table = 'team' if end_table == 'solo' else 'solo'
                for ak, _ in get_available_actions(engine, uid):
                    updates.append((other_table, ck, st, ak, tr * 0.3, None))
        return updates

    def train(self):
        def log(msg):
            print(msg, flush=True)
        log('Q-Learning AI Trainer')
        log(f'Generations: {GENERATIONS}, Battles/gen: {BATTLES_PER_GEN}')
        log(f'Alpha={ALPHA}, Gamma={GAMMA}, Temperature=1.0')
        log('')
        singles = list(self.char_map.keys())
        if len(singles) < 6:
            raise ValueError('默认训练包含 3 对 3 抽样，需要至少启用 6 名角色')
        log(f'Training characters: {singles}')
        log(f'Training summons: {self.training_summons}')
        weight_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), OUTPUT_FILE)
        previous = {}
        if os.path.exists(weight_path):
            with open(weight_path, encoding='utf-8') as saved:
                previous = json.load(saved)
            if not isinstance(previous, dict):
                raise ValueError('原权重文件不是有效训练表')
        preserved = merge_untrained_tables(previous, {'Q_solo': {}, 'Q_team': {}, 'Q_summon': {}}, singles, self.training_summons)
        self.preserved_characters = sorted(set(preserved['Q_solo']) | set(preserved['Q_team']))
        self.preserved_summons = sorted(preserved['Q_summon'])
        log(f'Preserved character tables (not trained): {self.preserved_characters}')
        log(f'Preserved summon tables (not trained): {self.preserved_summons}')
        stats_log = []
        NUM_WORKERS = 12
        # Prepare picklable data for subprocesses
        all_char_data = self.characters
        char_map_dict = dict(self.char_map)
        _ACTION_CACHE.clear()
        for gen in range(1, GENERATIONS + 1):
            gen_battles = 0
            total_reward = 0.0
            # Build all character battle specs
            specs = []
            for _ in range(BATTLES_PER_GEN):
                if random.random() < 0.7:  # 70% 1v1 for better solo Q-learning
                    a,b = random.sample(singles,2)
                    specs.append(([a],[b],random.choice(['6x6','8x8','10x10'])))
                else:
                    ch = random.sample(singles,6)
                    specs.append((ch[:3],ch[3:],random.choice(['10x10','20x20'])))
            # Build summon-only battle specs (1v1 / 2v2 independent summon fights)
            summon_pool = self.training_summons
            NUM_SUMMON_BATTLES = BATTLES_PER_GEN // 2
            summon_specs = []
            for _ in range(NUM_SUMMON_BATTLES if summon_pool else 0):
                if len(summon_pool) < 4 or random.random() < 0.5:
                    a,b = random.sample(summon_pool, 2) if len(summon_pool) >= 2 else summon_pool * 2
                    summon_specs.append(([a],[b],random.choice(['6x6','8x8','10x10'])))
                else:
                    ch = random.sample(summon_pool,4)
                    summon_specs.append((ch[:2],ch[2:],random.choice(['10x10','20x20'])))
            # Submit ALL battles at once — no waiting between batches
            all_ups = []
            char_tasks = [(ta,tb,ms,all_char_data,char_map_dict,self.summon_templates) for ta,tb,ms in specs]
            summon_tasks = [(ta,tb,ms,self.summon_templates) for ta,tb,ms in summon_specs]
            total_tasks = len(char_tasks) + len(summon_tasks)
            completed = 0
            with ProcessPoolExecutor(max_workers=NUM_WORKERS) as ex:
                futs = {}
                for i, t in enumerate(char_tasks):
                    futs[ex.submit(_mp_run_battle, t)] = ('char', i)
                for i, t in enumerate(summon_tasks):
                    futs[ex.submit(_mp_run_summon_battle, t)] = ('summon', i)
                for fut in as_completed(futs):
                    battle_type, _ = futs[fut]
                    try:
                        ups = fut.result()
                        if ups:
                            all_ups.extend(ups)
                            if battle_type == 'char':
                                gen_battles += 1
                    except Exception as e:
                        import traceback
                        print(f'[WARN] {battle_type} battle worker failed: {e}', flush=True)
                        traceback.print_exc()
                    completed += 1
                    if completed % 20 == 0 or completed == total_tasks:
                        recent = all_ups[-1000:] if len(all_ups) > 1000 else all_ups
                        r_sofar = sum(u[4] for u in recent) / max(1, len(recent))
                        log(f'  [{completed}/{total_tasks}] char_battles={gen_battles} avg_reward~{r_sofar:.3f}')
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
        def serialize_q(qdict, allowed):
            result = {}
            for ck, qd in qdict.items():
                if ck not in allowed:
                    continue
                result[ck] = {'|'.join(str(x) for x in s) + '__' + ak: v
                              for (s, ak), v in qd.items() if isinstance(s, tuple)}
            return result
        q_solo = serialize_q(self.Q_solo, self.char_map)
        q_team = serialize_q(self.Q_team, self.char_map)
        q_summon = serialize_q(self.Q_summon, self.training_summons)
        tables = merge_untrained_tables(previous, {'Q_solo': q_solo, 'Q_team': q_team, 'Q_summon': q_summon}, singles, self.training_summons)
        pending_path = weight_path + '.tmp'
        with open(pending_path, 'w', encoding='utf-8') as f:
            json.dump({**tables,
                       'stats': stats_log, 'params': {
                'generations': GENERATIONS, 'battles_per_gen': BATTLES_PER_GEN,
                'characters': singles, 'summons': self.training_summons,
                'preserved_characters': self.preserved_characters, 'preserved_summons': self.preserved_summons,
                'alpha': ALPHA, 'gamma': GAMMA, 'temperature': 1.0
            }}, f, ensure_ascii=False, indent=2)
        os.replace(pending_path, weight_path)
        log(f'Saved Q-tables to {weight_path}')
        return {'solo': self.Q_solo, 'team': self.Q_team, 'summon': self.Q_summon}

def load_q_table(path=None):
    """Load trained Q-tables for use in battle.
    Returns dict: {'solo': Q_solo, 'team': Q_team, 'summon': Q_summon}
    Backward-compat with old single-Q format.
    """
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), OUTPUT_FILE)
    if not os.path.exists(path): return None
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    def parse_qdict(qdict):
        """Parse a serialized Q-dict (string-keyed) into (state, action) tuple dict."""
        Q = defaultdict(lambda: defaultdict(float))
        skipped = 0
        for ck, entries in qdict.items():
            for key, val in entries.items():
                parts = key.split('__')
                if len(parts) < 2:
                    skipped += 1; continue
                state_str, action = parts[0], '__'.join(parts[1:])
                try:
                    state = tuple(int(v) for v in state_str.split('|'))
                except ValueError:
                    skipped += 1; continue  # skip corrupted entries (e.g. 'dead' state)
                # Backward compat: pad old dimensions to 22
                if len(state) < 22:
                    padding_needed = 22 - len(state)
                    state = state + (0,) * padding_needed
                Q[ck][(state, action)] = val
        if skipped:
            print(f'[load_q_table] Skipped {skipped} malformed Q-entries (will be cleaned on next save)')
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

def main(expected_serials=None):
    random.seed(42)
    trainer = QTrainer()
    trainer.init_characters()
    if expected_serials is not None and list(trainer.char_map) != expected_serials:
        raise ValueError('训练角色名单与启用角色快照不一致，请重新启动训练')
    print(f'Characters loaded: {list(trainer.char_map.keys())}', flush=True)
    trainer.train()
    return {'characters': list(trainer.char_map), 'summons': trainer.training_summons,
            'preserved_characters': trainer.preserved_characters, 'preserved_summons': trainer.preserved_summons}

if __name__ == '__main__':
    main()
