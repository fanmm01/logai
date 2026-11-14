#!/usr/bin/env python3
"""
Step 7: AI战斗模拟 HTTP服务器 + 实时日志系统
===============================================
启动后按配置监听，手动 POST /start 开始锦标赛。
GET /       → 查看当前状态
POST /start → 开始全部战斗（手动触发）
GET /log    → 查看实时日志

战斗顺序:
  按配置创建各赛制循环预赛与加赛，再生成附加赛、淘汰赛和铜牌赛。
  地图、队表、循环轮数、BoN、回合上限和展示间隔统一由设置模块读取。

用法:
  python ai_battle.py                        # 全部赛制 (1v1 + 2v2 + 3v3)
  python ai_battle.py --modes=1v1            # 仅 1v1
  python ai_battle.py --modes=1v1,2v2        # 1v1 + 2v2
  python ai_battle.py --modes 3v3 --time 3   # 仅 3v3，跑 3 次
  python ai_battle.py --modes=2v2 --type=0   # 仅 2v2 预赛，跳过淘汰赛
"""

import sys, os, random, math, json, re, time, threading, logging, itertools
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from collections import defaultdict

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

# Force unbuffered output for real-time log visibility
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from battle_engine import (CombatEngine, FullBattleEngine, roll_dice, parse_coord, format_coord,
    is_in_melee_range, has_timing, rank_text, avg_damage, success_rank, roll_d100, max_damage,
    season_status_roll, action_committed)
from characters_data import ALL_CHARACTERS, load_character_to_engine
from ai_trainer import (load_q_table, encode_state, get_available_actions, execute_action,
                            action_slot_for, affordable_actions, select_teammate_by_strategy)
from ai_trainer import encode_summon_state, get_summon_actions, execute_summon_action

# Display enum values are protocol identifiers, not configurable defaults.
class DisplayLevel:
    WINLOSS = 0
    ROUND = 1
    DEBUG = 2

from combat_settings import (value as settings_value, snapshot as settings_snapshot,
    settings_scope, pinned, file_path, season_statuses, LIVE as _LIVE_SETTINGS)
from combat_defaults import DEFAULTS as _SETTING_DEFAULTS


def battle_setting(name):
    # Explicit legacy overrides remain supported, without duplicate defaults.
    return globals()[name] if name in globals() else settings_value(name, live=name in _LIVE_SETTINGS)


def __getattr__(name):
    if name in _SETTING_DEFAULTS or name == 'CUSTOM_BATTLE_DELAY':
        return battle_setting(name)
    raise AttributeError(name)


logger = logging.getLogger('ai_battle')
logger.setLevel(logging.DEBUG)

# File handler
fh = logging.FileHandler(file_path('LOG_FILE'), encoding='utf-8', mode='w')
fh.setLevel(battle_setting('FILE_LOG_LEVEL'))
fh._combat_setting = 'FILE_LOG_LEVEL'
fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', '%H:%M:%S'))
logger.addHandler(fh)

# Console handler — summary only (adjust CONSOLE_LOG_LEVEL above)
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(battle_setting('CONSOLE_LOG_LEVEL'))
ch._combat_setting = 'CONSOLE_LOG_LEVEL'
ch.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S'))
logger.addHandler(ch)

# Thread-local flag to suppress terminal output for custom battles
_terminal_quiet = threading.local()

def set_terminal_quiet(v):
    _terminal_quiet.value = v

def is_terminal_quiet():
    return getattr(_terminal_quiet, 'value', False)

class TerminalQuietFilter(logging.Filter):
    def filter(self, record):
        return not is_terminal_quiet()

ch.addFilter(TerminalQuietFilter())

# In-memory log buffer for HTTP viewing
log_buffer = []
log_lock = threading.Lock()

class BufferedLogHandler(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        with log_lock:
            log_buffer.append(msg)
            if len(log_buffer) > battle_setting('LOG_BUFFER_LIMIT'):
                del log_buffer[:-battle_setting('LOG_BUFFER_LIMIT')]

buf_handler = BufferedLogHandler()
buf_handler.setLevel(battle_setting('WEB_LOG_LEVEL'))
buf_handler._combat_setting = 'WEB_LOG_LEVEL'
buf_handler.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S'))
logger.addHandler(buf_handler)

# Simple log buffer — matches terminal output (CONSOLE_LOG_LEVEL)
simple_log_buffer = []
simple_log_lock = threading.Lock()

class SimpleBufferedLogHandler(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        with simple_log_lock:
            simple_log_buffer.append(msg)
            if len(simple_log_buffer) > battle_setting('LOG_BUFFER_LIMIT'):
                del simple_log_buffer[:-battle_setting('LOG_BUFFER_LIMIT')]

simple_handler = SimpleBufferedLogHandler()
simple_handler.setLevel(battle_setting('CONSOLE_LOG_LEVEL'))
simple_handler._combat_setting = 'CONSOLE_LOG_LEVEL'
simple_handler.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S'))
logger.addHandler(simple_handler)


_logging_override = None


def battle_log(level, msg):
    # File-backed levels also update standalone CLI processes on their next record.
    for handler in logger.handlers:
        key = getattr(handler, '_combat_setting', None)
        if key:
            handler.setLevel(_logging_override if _logging_override is not None else battle_setting(key))
    getattr(logger, level)(msg)


ALL_MODES = ('1v1', '2v2', '3v3')


def _parse_modes(raw):
    """Parse a comma/plus/space separated mode list like '1v1,2v2' or '3v3'."""
    parts = [p.strip().lower() for p in re.split(r'[,+;/\s]+', raw) if p.strip()]
    if not parts:
        print(f'--modes 需要至少一个赛制: {", ".join(ALL_MODES)}', file=sys.stderr)
        sys.exit(1)
    if len(parts) == 1 and parts[0] in ('all', '*'):
        return list(ALL_MODES)
    unknown = [p for p in parts if p not in ALL_MODES]
    if unknown:
        print(f'不支持的赛制: {", ".join(unknown)}。可用: {", ".join(ALL_MODES)} (或 all)', file=sys.stderr)
        sys.exit(1)
    # Preserve canonical order and drop duplicates
    return [m for m in ALL_MODES if m in parts]


def parse_cli_args(argv):
    port = battle_setting('AI_SERVER_PORT')
    tournament_type = battle_setting('DEFAULT_TOURNAMENT_TYPE')
    run_times = battle_setting('DEFAULT_RUN_TIMES')
    enabled_modes = battle_setting('DEFAULT_ENABLED_MODES')
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith('--modes='):
            enabled_modes = _parse_modes(arg.split('=', 1)[1])
        elif arg == '--modes':
            i += 1
            if i >= len(argv):
                print('--modes requires a value, e.g. --modes=1v1,2v2', file=sys.stderr)
                sys.exit(1)
            enabled_modes = _parse_modes(argv[i])
        elif arg.startswith('--type='):
            val = arg.split('=', 1)[1]
            if val not in ('0', '1'):
                print(f'Unsupported --type value: {val}. Use 0 or 1.', file=sys.stderr)
                sys.exit(1)
            tournament_type = int(val)
        elif arg == '--type':
            i += 1
            if i >= len(argv):
                print('--type requires a value 0 or 1.', file=sys.stderr)
                sys.exit(1)
            val = argv[i]
            if val not in ('0', '1'):
                print(f'Unsupported --type value: {val}. Use 0 or 1.', file=sys.stderr)
                sys.exit(1)
            tournament_type = int(val)
        elif arg.startswith('--time='):
            try:
                run_times = int(arg.split('=', 1)[1])
                if run_times < 1:
                    print(f'--time must be >= 1, got: {run_times}', file=sys.stderr)
                    sys.exit(1)
            except ValueError:
                print(f'Invalid --time value: {arg}', file=sys.stderr)
                sys.exit(1)
        elif arg == '--time':
            i += 1
            if i >= len(argv):
                print('--time requires a value.', file=sys.stderr)
                sys.exit(1)
            try:
                run_times = int(argv[i])
                if run_times < 1:
                    print(f'--time must be >= 1, got: {run_times}', file=sys.stderr)
                    sys.exit(1)
            except ValueError:
                print(f'Invalid --time value: {argv[i]}', file=sys.stderr)
                sys.exit(1)
        elif arg.startswith('--port='):
            try:
                port = int(arg.split('=', 1)[1])
            except ValueError:
                print(f'Invalid port: {arg}', file=sys.stderr)
                sys.exit(1)
        elif arg in ('--port', '-p'):
            i += 1
            if i >= len(argv):
                print(f'{arg} requires a port number.', file=sys.stderr)
                sys.exit(1)
            try:
                port = int(argv[i])
            except ValueError:
                print(f'Invalid port: {argv[i]}', file=sys.stderr)
                sys.exit(1)
        elif arg.startswith('-'):
            battle_log('info', f'忽略未知参数: {arg}')
        else:
            if port == battle_setting('AI_SERVER_PORT'):
                try:
                    port = int(arg)
                except ValueError:
                    battle_log('info', f'忽略位置参数: {arg}')
            else:
                battle_log('info', f'忽略多余位置参数: {arg}')
        i += 1
    return port, tournament_type, run_times, enabled_modes


# ============================================================
#  AI Controller (inline, same as before)
# ============================================================

class AIController:
    Q_SOLO = None   # Single / last-person-standing Q-table
    Q_TEAM = None   # Multi-player Q-table
    Q_SUMMON = None # Summon Q-table

    @classmethod
    def load_q_table(cls):
        if cls.Q_SOLO is None:
            loaded = load_q_table()
            if loaded:
                if isinstance(loaded, dict) and 'solo' in loaded:
                    # New dual-table format
                    cls.Q_SOLO = loaded['solo']
                    cls.Q_TEAM = loaded['team']
                    cls.Q_SUMMON = loaded.get('summon', defaultdict(lambda: defaultdict(float)))
                    total_solo = sum(len(v) for v in cls.Q_SOLO.values())
                    total_team = sum(len(v) for v in cls.Q_TEAM.values())
                    total_summon = sum(len(v) for v in cls.Q_SUMMON.values())
                    chars_solo = list(cls.Q_SOLO.keys())
                    battle_log('info', f'Q-tables loaded: solo={total_solo} team={total_team} summon={total_summon} for {len(chars_solo)} chars')
                else:
                    # Old single-table format: use same for both
                    cls.Q_SOLO = loaded
                    cls.Q_TEAM = loaded
                    cls.Q_SUMMON = defaultdict(lambda: defaultdict(float))
                    total = sum(len(v) for v in loaded.values()) if loaded else 0
                    chars = list(loaded.keys()) if loaded else []
                    battle_log('info', f'Q-table loaded (old format): {total} entries for {len(chars)} chars ({", ".join(chars[:5])}...)')
                if (not cls.Q_SOLO or all(len(v) == 0 for v in cls.Q_SOLO.values())) and \
                   (not cls.Q_TEAM or all(len(v) == 0 for v in cls.Q_TEAM.values())):
                    battle_log('info', '========================================')
                    battle_log('info', '!! Q表为空 !! 所有角色将使用规则AI')
                    battle_log('info', '请先运行: python ai_trainer.py')
                    battle_log('info', '========================================')
                    cls.Q_SOLO = None
                    cls.Q_TEAM = None
                    cls.Q_SUMMON = None
            else:
                battle_log('info', '========================================')
                battle_log('info', '!! 未找到 ai_weights.json !!')
                battle_log('info', '所有角色使用规则AI (无训练)')
                battle_log('info', '请先运行: python ai_trainer.py')
                battle_log('info', '========================================')

    def __init__(self, char_id, char_data, team):
        self._settings = settings_snapshot()
        self.char_id = char_id
        self.char_data = char_data
        self.team = team
        # Reaction weights: [dodge_weight, counter_weight] — trained, not hardcoded
        self.react_dodge_w = battle_setting('AI_DODGE_WEIGHT')
        self.react_counter_w = battle_setting('AI_COUNTER_WEIGHT')

    @pinned
    def decide_action(self, engine, user_id):
        """Use Q-table if available, fall back to rule-based."""
        if engine._get_my_actions(user_id).get('主动', 0) <= 0:
            return '.i end'
        cmd = self._decide_action(engine, user_id)
        failed = getattr(engine, '_ai_failed_commands', set())
        if cmd not in failed:
            return cmd
        for key, _ in affordable_actions(engine, user_id, get_available_actions(engine, user_id)):
            alternative = self._command_for_action(engine, user_id, key)
            if alternative and alternative not in failed:
                return alternative
        return '.i end'

    @pinned
    def _decide_action(self, engine, user_id):
        if AIController.Q_SOLO is not None or AIController.Q_TEAM is not None:
            ck = self.char_data.get('serial', self.char_id)
            if (AIController.Q_SOLO and ck in AIController.Q_SOLO) or \
               (AIController.Q_TEAM and ck in AIController.Q_TEAM):
                return self._q_decide(engine, user_id, ck)
        return self._rule_decide(engine, user_id)

    @pinned
    def _q_decide(self, engine, user_id, ck):
        """Q-table based decision. Routes to solo or team table based on living allies."""
        state = engine._get_state()
        if not state or state.get('phase') != 'active': return '.i end'

        # Dying characters can only use healing items (or end turn)
        if engine._is_dying(user_id):
            if engine._has_healing_item(user_id):
                return '.a eat'
            return '.i end'
        actions = engine._get_actions()
        my_acts = engine._get_my_actions(user_id) if hasattr(engine, '_get_my_actions') else             actions.get(user_id, {'主动': 2, '附加': 3})
        if my_acts['主动'] <= 0: return '.i end'

        st = encode_state(engine, user_id)
        available = get_available_actions(engine, user_id)
        if hasattr(available, '__iter__') and available:
            available = affordable_actions(engine, user_id, available)
        if not available: return '.i end'

        # Determine solo vs team: count living non-summon allies (exclude self)
        init_list = engine._get_initiative()
        my_entry = next((e for e in init_list if e['userId'] == user_id), None)
        if not my_entry: return self._rule_decide(engine, user_id)
        my_team = my_entry.get('team', 'Y')
        living_allies = [e for e in init_list if e.get('team') == my_team
                         and e['userId'] != user_id
                         and not e.get('isSummon')
                         and (engine._get_combat_hp(e['userId']) or 0) > 0]
        if not living_allies:
            if AIController.Q_SOLO is None or ck not in AIController.Q_SOLO:
                return self._rule_decide(engine, user_id)
            qdict = AIController.Q_SOLO[ck]
        else:
            if AIController.Q_TEAM is None or ck not in AIController.Q_TEAM:
                return self._rule_decide(engine, user_id)
            qdict = AIController.Q_TEAM[ck]

        if not qdict or all(v == 0.0 for v in qdict.values()):
            return self._rule_decide(engine, user_id)

        # Filter MOVE_TOWARD if already moved this turn (max 1 move)
        moved = getattr(engine, '_moved_this_turn', set())
        actions_pool = [(ak, an) for ak, an in available if not (ak == 'MOVE_TOWARD' and user_id in moved)]
        # Repair-focused states (low HP / low MP, or an ally in dying) prioritise
        # support actions whose slot is still affordable over raw damage.
        actions_pool = self._prioritize_support(engine, user_id, actions_pool)
        if not actions_pool: actions_pool = available

        # Weighted random sampling based on Q-values (softmax / Boltzmann)
        TEMPERATURE = battle_setting('AI_TEMPERATURE')
        q_vals = [(engine._y14_action_q(qdict, st, ak) if engine._is_y14(user_id)
                   else engine._y16_action_q(qdict, st, ak) if engine._is_y16(user_id)
                   else qdict.get((st, ak), 0.0)) for ak, an in actions_pool]

        # 状态 deformation: pollute Q-value pool with random +/- noise, then act on polluted weights
        char = engine.get_char(user_id)
        zt = char.get_attr('状态', battle_setting('AI_DEFAULT_STATUS')) if char else battle_setting('AI_DEFAULT_STATUS')
        deform_prob = (100 - zt) / 100.0 * battle_setting('AI_STATE_NOISE')
        if random.random() < deform_prob:
            scale = max(max(abs(q) for q in q_vals), 1.0)
            q_vals = [q + random.uniform(-deform_prob, deform_prob) * scale for q in q_vals]

        max_q = max(q_vals) if q_vals else 0
        exp_vals = [math.exp((q - max_q) / TEMPERATURE) for q in q_vals]
        total = sum(exp_vals)
        if total > 0:
            probs = [e / total for e in exp_vals]
        else:
            probs = None  # uniform
        idx = random.choices(range(len(actions_pool)), weights=probs, k=1)[0]
        ak, an = actions_pool[idx]

        return self._command_for_action(engine, user_id, ak)

    @pinned
    def _command_for_action(self, engine, user_id, ak):
        my_acts = engine._get_my_actions(user_id)
        # Parse action: may have __T suffix for target strategy
        base_ak = ak.split('__')[0] if '__' in ak else ak
        if base_ak == 'BASIC_ATTACK': return '.s0'
        if base_ak == 'MOVE_TOWARD': return self._move_cmd(engine, user_id) or '.s0'
        if base_ak == 'EAT_CAKE':
            return f'.a eat {user_id}'
        if base_ak == 'GIVE_CAKE':
            target = self._select_cake_target(engine, user_id, ak.split('__')[1] if '__' in ak else 'T0')
            return f'.a give {target}' if target else '.i end'
        if base_ak == 'END_TURN': return '.i end'
        if base_ak == 'SKILL_12_THROW':
            return '.s12 mode=throw'
        if base_ak.startswith('SKILL_'):
            skill_num = int(base_ak.split('_')[1])
            spells = engine.load_spells(user_id) or []
            spell = next((s for s in spells if s['index'] == skill_num), None)
            if spell:
                timing = spell.get('时机', '2')
                # Dual-timing skills use the additional slot while available,
                # preserving main actions; timing-3-only must use it.
                if has_timing(timing, '3') and (not has_timing(timing, '2')
                                                or my_acts.get('附加', 0) > 0):
                    if engine._is_y17(user_id) and engine._y17_spell_kind(user_id, spell) == 'store':
                        target = engine._y17_heal_target(user_id)
                        return f'.a s{skill_num} target={target}' if target else '.i end'
                    return f'.a s{skill_num}'
                if has_timing(timing, '1') or has_timing(timing, '2'):
                    if engine._is_y17(user_id) and engine._y17_spell_kind(user_id, spell) == 'store':
                        target = engine._y17_heal_target(user_id)
                        return f'.s{skill_num} target={target}' if target else '.i end'
                    return f'.s{skill_num}'
            return self._rule_decide(engine, user_id)
        return self._rule_decide(engine, user_id)

    @pinned
    def _prioritize_support(self, engine, user_id, actions):
        """Keep only support actions when the team clearly needs recovery.

        Generic priority from CharacterData-driven state (HP ratios and ready
        cakes), never by role name or skill index.
        """
        char = engine.get_char(user_id)
        if not char:
            return actions
        my_hp = engine._get_combat_hp(user_id) or 0
        max_hp = char.get_attr('体力上限', my_hp) or my_hp or 1
        my_mp = char.get_attr('魔力', 0) or 0
        max_mp = char.get_attr('魔力上限', my_mp) or my_mp or 1
        team = engine._battle_team_of(user_id)
        ally_dying = any(e.get('team') == team and not e.get('isSummon')
                         and e['userId'] != user_id and engine._is_dying(e['userId'])
                         for e in engine._get_initiative())
        urgent = my_hp < max_hp * battle_setting('AI_SUPPORT_HP_THRESHOLD') or my_mp < max_mp * battle_setting('AI_SUPPORT_MP_THRESHOLD') or ally_dying
        if not urgent:
            return actions
        support = [(ak, an) for ak, an in actions
                   if ak.split('__')[0] in ('EAT_CAKE', 'GIVE_CAKE')]
        if not support:
            return actions
        return support

    @pinned
    def _move_cmd(self, engine, user_id):
        coord = engine._next_move_coord(user_id)
        return f'.a m {coord}' if coord else None

    @pinned
    def _rule_decide(self, engine, user_id):
        state = engine._get_state()
        if not state or state.get('phase') != 'active':
            return '.i end'

        if engine._is_dying(user_id):
            if engine._has_healing_item(user_id):
                return '.a eat'
            return '.i end'

        init_list = engine._get_initiative()
        actions = engine._get_actions()
        my_acts = engine._get_my_actions(user_id)
        if my_acts.get('主动', 0) <= 0:
            return '.i end'
        char = engine.get_char(user_id)
        my_entry = next((e for e in init_list if e['userId'] == user_id), None)
        if not my_entry: return '.i end'

        if engine._is_y17(user_id):
            available = affordable_actions(engine, user_id, get_available_actions(engine, user_id))
            if not available:
                return '.i end'
            return self._command_for_action(engine, user_id, available[0][0])

        my_team_rd = my_entry.get('team', 'Y')
        enemies = [e for e in init_list if e.get('team') != my_team_rd]
        my_coord = my_entry.get('coord', 'A1')

        # Build blocked-spell sets (same logic as get_available_actions dynamic filters)
        active_spell_names = set()
        effects = engine._get_effects()
        for e in effects:
            if e.get('remainingRounds', 0) > 0 or e.get('persistent', 0):
                sn = e.get('spellName', '')
                if sn: active_spell_names.add(sn)
        my_summons = set()
        for e in init_list:
            if e.get('isSummon') and e.get('ownerId') == user_id and (engine._get_combat_hp(e['userId']) or 0) > 0:
                my_summons.add(e.get('name', ''))

        def _spell_blocked(sp):
            """True if spell has active same-name effect or a summon template is at its cap.

            同模板仍有名额时允许补召（如第二根藤蔓），只有满额/累计耗尽才禁选。"""
            if engine._y14_preflight(user_id, sp):
                return True
            if engine._revive_preflight(user_id, sp):
                return True
            if sp['name'] in active_spell_names:
                return True
            for eff in sp.get('effects', []):
                if eff.get('type') == 5:
                    tmpl = eff.get('召唤物模板', '')
                    if tmpl:
                        remain, _reason = engine._get_summon_quota(user_id, tmpl)
                        if remain == 0:
                            return True
            return False

        if my_acts['主动'] > 0:
            spells = char.spells or engine.load_spells(user_id)

            # 状态 deformation check
            zt = char.get_attr('状态', battle_setting('AI_DEFAULT_STATUS')) if char else battle_setting('AI_DEFAULT_STATUS')
            deform_prob = (100 - zt) / 100.0 * battle_setting('AI_STATE_NOISE')
            if random.random() < deform_prob:
                from ai_trainer import get_available_actions
                avail = affordable_actions(engine, user_id, get_available_actions(engine, user_id))
                if avail:
                    ak_deform, _ = random.choice(avail)
                    base_deform = ak_deform.split('__')[0] if '__' in ak_deform else ak_deform
                    if base_deform == 'BASIC_ATTACK': return '.s0'
                    if base_deform == 'MOVE_TOWARD': return self._move_cmd(engine, user_id) or '.s0'
                    if base_deform.startswith('SKILL_'):
                        sn = int(base_deform.split('_')[1])
                        return f'.s{sn}'
                    if base_deform == 'EAT_CAKE':
                        target = self._select_cake_target(engine, user_id, 'SELF')
                        return f'.a eat {target}' if target else '.i end'

            # Heal according to the configured HP threshold.
            my_hp = engine._get_combat_hp(user_id) or battle_setting('AI_HP_FALLBACK')
            max_hp = char.get_attr('体力上限', my_hp)
            if my_hp < max_hp * battle_setting('AI_HEAL_HP_THRESHOLD'):
                heal_spells = [s for s in spells if s.get('effects') and any(
                    e['type'] in (3,8) and e.get('回复hp') for e in s['effects'])
                    and not _spell_blocked(s)]
                if heal_spells and has_timing(heal_spells[0].get('时机','2'), '2'):
                    return f'.s{heal_spells[0]["index"]}'

            # Damage spell
            dmg_spells = [s for s in spells if s.get('effects') and any(
                e['type'] == 1 for e in s['effects']) and has_timing(s.get('时机','2'), '2')
                and not _spell_blocked(s)]
            if dmg_spells and enemies:
                return f'.s{dmg_spells[0]["index"]}'

            # Summon
            summon_spells = [s for s in spells if s.get('effects') and any(
                e['type'] == 5 for e in s['effects']) and has_timing(s.get('时机','2'), '2')
                and not _spell_blocked(s)]
            if summon_spells:
                return f'.s{summon_spells[0]["index"]}'

            # Item thresholds are configurable independently of spell healing.
            if hasattr(engine, '_has_ready_cake') and engine._has_ready_cake(user_id):
                my_mp = char.get_attr('魔力', 0) or 0
                max_mp = char.get_attr('魔力上限', my_mp) or my_mp
                if my_hp < max_hp * battle_setting('AI_ITEM_HP_THRESHOLD') or my_mp < max_mp * battle_setting('AI_ITEM_MP_THRESHOLD'):
                    target = self._select_cake_target(engine, user_id, 'SELF')
                    return f'.a eat {target}'

            # Move toward nearest enemy
            if enemies and my_acts['附加'] > 0:
                moved = getattr(engine, '_moved_this_turn', set())
                if user_id not in moved:
                    nearest = self._nearest(engine, user_id, enemies)
                    if nearest:
                        ec = nearest.get('coord', '')
                        if ec and my_coord and not is_in_melee_range(my_coord, ec):
                            cmd = self._move_toward(my_coord, ec, user_id)
                            if cmd: return cmd

            return '.s0'

        if my_acts['附加'] > 0:
            spells = char.spells or engine.load_spells(user_id)
            buff_spells = [s for s in spells if s.get('effects') and any(
                e['type'] == 4 for e in s['effects']) and has_timing(s.get('时机','2'), '3')]
            if buff_spells and not enemies:
                return f'.a s{buff_spells[0]["index"]}'
            if enemies:
                moved = getattr(engine, '_moved_this_turn', set())
                if user_id not in moved:
                    nearest = self._nearest(engine, user_id, enemies)
                    if nearest:
                        ec = nearest.get('coord', '')
                        if ec and my_coord and not is_in_melee_range(my_coord, ec):
                            cmd = self._move_toward(my_coord, ec, user_id)
                            if cmd: return cmd

        return '.i end'

    @pinned
    def _select_cake_target(self, engine, user_id, strategy):
        """Select cake target from allies based on strategy."""
        if strategy == 'SELF':
            return user_id
        allies = [e for e in engine._battle_unit_entries()
                  if e.get('team') == engine._battle_team_of(user_id)
                  and engine._resolve_uid(e['userId']) != engine._resolve_uid(user_id)
                  and not e.get('isSummon')
                  and ((engine._get_combat_hp(e['userId']) or 0) > 0 or engine._is_dying(e['userId']))]
        return select_teammate_by_strategy(engine, user_id, strategy, allies)

    @pinned
    def _nearest(self, engine, user_id, enemies):
        init_list = engine._get_initiative()
        my_entry = next((e for e in init_list if e['userId'] == user_id), None)
        if not my_entry or not my_entry.get('coord'): return enemies[0] if enemies else None
        my_coord = my_entry['coord']
        best, best_dist = None, float('inf')
        for e in enemies:
            ec = e.get('coord', '')
            if ec:
                mp, ep = parse_coord(my_coord), parse_coord(ec)
                if mp and ep:
                    d = abs(mp[0]-ep[0]) + abs(mp[1]-ep[1])
                    if d < best_dist:
                        best_dist = d
                        best = e
        return best or (enemies[0] if enemies else None)

    @pinned
    def _move_toward(self, my_coord, target_coord, user_id):
        mp, tp = parse_coord(my_coord), parse_coord(target_coord)
        if not mp or not tp: return None
        dc = 0 if tp[0]==mp[0] else (1 if tp[0]>mp[0] else -1)
        dr = 0 if tp[1]==mp[1] else (1 if tp[1]>mp[1] else -1)
        for dcol, drow in [(dc,0), (0,dr), (dc,dr)]:
            nc, nr = mp[0]+dcol, mp[1]+drow
            if 0 <= nc < 26 and 0 <= nr < 99:
                return f'.a m {format_coord(nc, nr)}'
        return None


# ============================================================
#  Battle Engine
# ============================================================

def round_hp_line(engine, round_number, initiative=None):
    """The canonical ai_battle round-start HP display, also used by browser replays."""
    il = initiative if initiative is not None else engine._get_initiative()
    # Collect summon HP by owner (living summons only)
    summon_total = {}       # ownerId → total summon HP
    summon_detail = {}       # ownerId → {summon_name: hp} for 斯瑞提卡
    for e in engine._battle_unit_entries():
        if not e.get('isSummon'): continue
        if e.get('_suppressed', False): continue  # 跳过被抑制的召唤物槽
        owner = engine._resolve_uid(e.get('ownerId', ''))
        if not owner: continue
        shp = engine._get_combat_hp(e['userId']) or 0
        if shp <= 0: continue
        summon_total[owner] = summon_total.get(owner, 0) + shp
        sname = re.sub(r'\s*\(行动\d+\)\s*', '', e.get('name', '?'))
        d = summon_detail.setdefault(owner, {})
        d[sname] = d.get(sname, 0) + shp

    y_hp_parts = []
    x_hp_parts = []
    seen_y_uids = set()
    seen_x_uids = set()
    for e in il:
        if e.get('isSummon'): continue
        if e.get('_suppressed', False): continue  # 不显示被抑制条目
        base = e.get('baseUserId', e['userId'])
        uid = e['userId']
        if e.get('team') == 'Y':
            if base in seen_y_uids:
                continue
            seen_y_uids.add(base)
        else:
            if base in seen_x_uids:
                continue
            seen_x_uids.add(base)
        hp = engine._get_combat_hp(uid) or 0
        name = e.get('name', uid)
        # Strip " (行动N)" suffix from multi-action slot names
        name = re.sub(r'\s*\(行动\d+\)\s*', '', name)
        # Append summon HP suffix
        shp = summon_total.get(engine._resolve_uid(uid), 0)
        if shp > 0:
            char = engine.get_char(uid)
            if char and char.get_attr('召唤物HP单独显示', 0) == 1:
                details = summon_detail.get(uid, {})
                parts = ''.join(f'{sn}{sh}' for sn, sh in details.items())
                hp_str = f'{name}(HP:{hp}|{parts})'
            else:
                hp_str = f'{name}(HP:{hp}|召{shp})'
        else:
            hp_str = f'{name}(HP:{hp})'
        if e.get('team') == 'Y':
            y_hp_parts.append(hp_str)
        else:
            x_hp_parts.append(hp_str)
    return f'    第{round_number}回合 Y: {", ".join(y_hp_parts)} | X: {", ".join(x_hp_parts)}'


class BattleEngine(FullBattleEngine):
    def __init__(self, display_level=None, delay=None, quiet=False, max_rounds=None):
        self._settings = settings_snapshot()
        super().__init__()
        self.max_rounds = max_rounds if max_rounds is not None else battle_setting('MAX_ROUNDS_PRELIM')
        self.display_level = battle_setting('DEFAULT_DISPLAY_LEVEL') if display_level is None else display_level  # DisplayLevel: 0=WINLOSS, 1=ROUND, 2=DEBUG
        self.delay = battle_setting('DEFAULT_BATTLE_DELAY') if delay is None else delay  # Seconds between turns (0 = skip)
        self.quiet = quiet  # Suppress terminal output (for custom battles)

    @pinned
    def _timeout_verdict(self):
        """达到配置回合上限 → 按非召唤物HP占最大体力比例（队内人均）判定胜负
        使用原始队伍名单（含已阵亡角色，HP比=0），而非仅存活角色。
        多动角色只算一次。"""
        roster = getattr(self, '_team_roster', None)
        y_sum = 0.0; x_sum = 0.0
        y_cnt = 0; x_cnt = 0
        if roster:
            # Use original team roster — dead chars have combat_hp=0 → ratio=0
            for uid, team in roster:
                chp = self._get_combat_hp(uid) or 0
                char = self.get_char(uid)
                mhp = char.get_attr('体力上限', chp) if char else chp
                if mhp > 0:
                    ratio = chp / mhp
                    if team == 'Y':
                        y_sum += ratio; y_cnt += 1
                    else:
                        x_sum += ratio; x_cnt += 1
        else:
            # Fallback: iterate initiative list (legacy path)
            for e in self._get_initiative():
                if e.get('isSummon'):
                    continue
                uid = e['userId']
                base = uid.rsplit('__act', 1)[0] if '__act' in str(uid) else uid
                chp = self._get_combat_hp(uid) or 0
                char = self.get_char(uid)
                mhp = char.get_attr('体力上限', chp) if char else chp
                if mhp > 0:
                    ratio = chp / mhp
                    if e.get('team') == 'Y':
                        y_sum += ratio; y_cnt += 1
                    else:
                        x_sum += ratio; x_cnt += 1
        y_avg = y_sum / y_cnt if y_cnt > 0 else 0.0
        x_avg = x_sum / x_cnt if x_cnt > 0 else 0.0
        if y_avg > x_avg:
            winner = 'Y'
        elif x_avg > y_avg:
            winner = 'X'
        else:
            # Equal ratios (including 0.00 vs 0.00) → random winner
            winner = random.choice(['Y', 'X'])
        # Use original team roster (all participants) rather than surviving initiative
        roster = getattr(self, '_team_roster', None)
        y_names_list = []; x_names_list = []
        if roster:
            for uid, team in roster:
                char = self.get_char(uid)
                name = char.name if char and char.name else uid
                name = re.sub(r'\s*\(行动\d+\)\s*', '', name)
                if team == 'Y':
                    y_names_list.append(name)
                else:
                    x_names_list.append(name)
        y_names = '+'.join(y_names_list) or '?'
        x_names = '+'.join(x_names_list) or '?'
        battle_log('info', f'  [超时] Y队[{y_names}] X队[{x_names}] Y队人均HP比={y_avg:.2f} X队人均HP比={x_avg:.2f} → {winner}胜' + (' (平局随机)' if y_avg == x_avg else ''))
        return {'winner': winner, 'rounds': self.max_rounds, 'timeout': True,
                'y_ratio': round(y_avg, 4), 'x_ratio': round(x_avg, 4)}

    @pinned
    def _run_character_ai_turn(self, uid, ai, emit):
        base = self._resolve_uid(uid)
        self._ai_failed_commands = set()
        failures = 0
        try:
            while True:
                state = self._get_state()
                il = self._get_initiative()
                if not state or state.get('phase') != 'active' or not il:
                    return
                if il[state['activeIndex']]['userId'] != uid:
                    return
                if self._get_my_actions(base).get('主动', 0) <= 0 or self._is_dying(base):
                    self._end_turn(uid)
                    return
                cmd = ai.decide_action(self, base)
                if cmd == '.i end':
                    emit('      -> 结束回合')
                    self._end_turn(uid)
                    return
                result = self._execute_ai_command(base, cmd)
                for line in str(result).splitlines():
                    if line.strip():
                        emit(f'        {line.strip()}')
                for line in self._drain_battle_events():
                    emit(f'        {line}')
                if getattr(result, 'committed', True):
                    self._consume_action(base, result.slot)
                    self._ai_failed_commands.clear()
                    failures = 0
                    if cmd.startswith('.a m '):
                        if not hasattr(self, '_moved_this_turn'):
                            self._moved_this_turn = set()
                        self._moved_this_turn.add(base)
                else:
                    failures += 1
                    self._ai_failed_commands.add(cmd)
                    if failures >= battle_setting('AI_MAX_FAILURES'):
                        emit('      -> 无可执行动作，结束回合')
                        self._end_turn(uid)
                        return
        finally:
            self._ai_failed_commands = set()
            for line in self._drain_battle_events():
                emit(f'        {line}')

    @pinned
    def run_battle(self, ai_map):
        """AI-driven tournament battle loop with display_level-controlled logging.
        display_level: 0=WINLOSS (result only), 1=ROUND (+round HP), 2=DEBUG (+all actions)."""
        if self.quiet:
            set_terminal_quiet(True)
        # Build log filter based on display_level
        dl = self.display_level
        if dl <= DisplayLevel.WINLOSS:
            _bl = (lambda level, msg: None)  # no-op: only tournament-level result shown
        elif dl == DisplayLevel.ROUND:
            _bl = (lambda level, msg: battle_log(level, msg) if level == 'info' else None)
        else:  # DisplayLevel.DEBUG
            _bl = battle_log
        try:
            rc = 0; last_round = 0; header_shown = False; step_count = 0
            while rc < self.max_rounds and step_count < battle_setting('BATTLE_MAX_STEPS'):
                if self.delay > 0: time.sleep(self.delay)
                self._reconcile_summon_groups()
                state = self._get_state()
                if not state or state.get('phase') != 'active': break
                il = self._get_initiative()
                if not il:
                    # Initiative empty (all dead/removed) — ensure end-check ran
                    if not getattr(self, '_battle_result', None):
                        end_result = self._check_battle_end()
                        if end_result:
                            self._battle_result = end_result
                    break
                for event in self._drain_battle_events():
                    _bl('debug', f'        {event}')
                rc = state.get('round', 1); step_count += 1
                if rc != last_round:
                    last_round = rc; self._moved_this_turn = set()
                    if not header_shown:
                        header_shown = True
                        import re as _re_hd
                        _ACT_SUFFIX_RE = _re_hd.compile(r'\s*\(行动\d+\)\s*')
                        y_header_seen = set(); x_header_seen = set()
                        y_header = []; x_header = []
                        for _he in il:
                            if _he.get('isSummon') or _he.get('_suppressed'): continue
                            _hbase = _he.get('baseUserId', _he['userId'])
                            if _he.get('team') == 'Y':
                                if _hbase in y_header_seen: continue
                                y_header_seen.add(_hbase)
                                _hn = _ACT_SUFFIX_RE.sub('', _he.get('name','?'))
                                if (self._get_combat_hp(_he['userId'])or 0)>0: y_header.append(_hn)
                            else:
                                if _hbase in x_header_seen: continue
                                x_header_seen.add(_hbase)
                                _hn = _ACT_SUFFIX_RE.sub('', _he.get('name','?'))
                                if (self._get_combat_hp(_he['userId'])or 0)>0: x_header.append(_hn)
                        y_display = '+'.join(y_header) if y_header else '?'
                        x_display = '+'.join(x_header) if x_header else '?'
                        _bl('info', f'  ---- Y队：{y_display} v.s. X队：{x_display} ----')
                    if self.display_level >= DisplayLevel.ROUND:
                        _bl('info', round_hp_line(self, rc, il))
                dead_owners = {e['userId'] for e in il
                               if not e.get('isSummon') and self._is_final_dead(e['userId'])}
                # Also track resolved base UIDs so we can clean up summons whose ownerId
                # might be the base uid (even when dead_owners contains __act suffixed entries)
                dead_base_uids = {self._resolve_uid(uid) for uid in dead_owners}
                if dead_owners:
                    for owner in dead_base_uids:
                        self._remove_summons_of_owner(owner)
                    state = self._get_state()
                    il = self._get_initiative()
                # Reconciliation owns grouped summon death/removal; do not discard
                # a representative before its surviving members have been promoted.
                self._reconcile_summon_groups()
                state = self._get_state()
                il = self._get_initiative()
                # Check battle end: count non-summon alive characters per team
                end_result = self._check_battle_end()
                if end_result:
                    y_chars_d = [e.get('name','?') for e in il if e.get('team')=='Y' and not e.get('isSummon')]
                    x_chars_d = [e.get('name','?') for e in il if e.get('team')=='X' and not e.get('isSummon')]
                    y_disp = '+'.join(y_chars_d) if y_chars_d else '?'
                    x_disp = '+'.join(x_chars_d) if x_chars_d else '?'
                    if end_result.get('mutual_death'):
                        y_ov = end_result.get('y_overflow', 0)
                        x_ov = end_result.get('x_overflow', 0)
                        tie_info = ''
                        if end_result.get('tie'):
                            tie_info = ' [随机胜者]'
                        elif end_result.get('tie_pct'):
                            tie_info = ' [溢出比判定]'
                        battle_log('info', f'  [同归于尽] Y队[{y_disp}]溢出{y_ov} X队[{x_disp}]溢出{x_ov}{tie_info} → {end_result["winner"]}胜')
                    else:
                        # Normal end: file-only (debug) in prelim; terminal+file in knockout
                        battle_log('debug', f'  [战斗结束] Y队[{y_disp}] X队[{x_disp}] → {end_result["winner"]}胜 ({rc}回合)')
                    return {'winner': end_result['winner'], 'rounds': rc,
                            'mutual_death': end_result.get('mutual_death', False),
                            'y_overflow': end_result.get('y_overflow', 0),
                            'x_overflow': end_result.get('x_overflow', 0)}
                entry = il[state['activeIndex']]; uid = entry['userId']

                # 跳过被抑制的行动槽（动态行动数未激活的额外槽，玩家和召唤物统一处理）
                if entry.get('_suppressed', False):
                    self._end_turn(uid)
                    continue

                char_name = entry.get('name', uid); hp = self._get_combat_hp(uid) or 0
                is_summon = entry.get('isSummon', False)
                if hp <= 0:
                    if is_summon:
                        self._reconcile_summon_groups()
                        if any(e['userId'] == uid for e in self._get_initiative()) and not self._get_summon_group(uid):
                            # Legacy standalone summon, with no group registration.
                            self._end_turn(uid)
                            self._replace_initiative_preserving_actor(
                                [e for e in self._get_initiative() if e['userId'] != uid])
                        continue
                    self._end_turn(uid); continue

                # 处理前即时重算：首个行动槽出现时根据当前行动力调整抑制状态
                if entry.get('actionIdx', 0) == 0:
                    self._sync_initiative_slots(uid)

                if is_summon:
                    # ── 共享动作、全员出手 ──
                    # 一组召唤物只有一个先攻入口和一份动作池；每次组动作让当前存活成员
                    # 各出手一次。成员死亡立即减少本批及后续批次的出手数。
                    group_key = self._summon_group_key(uid)
                    team_tag = entry.get('team', '?')
                    summon_name = entry.get('name', uid)

                    def _one_member(m_uid):
                        if AIController.Q_SUMMON is not None:
                            m_entry = self._unit_il_entry(m_uid, self._get_initiative())
                            m_name = (m_entry or {}).get('name', summon_name)
                            if m_name in AIController.Q_SUMMON:
                                st = encode_summon_state(self, m_uid)
                                av = get_summon_actions(self, m_uid)
                                sdict = AIController.Q_SUMMON[m_name]
                                q_vals = [sdict.get((st, ak), 0.0) for ak, an in av]
                                zt = random.randint(battle_setting('AI_SUMMON_STATUS_MIN'), battle_setting('AI_SUMMON_STATUS_MAX'))
                                deform_prob = (100 - zt) / 100.0 * battle_setting('AI_STATE_NOISE')
                                if q_vals and random.random() < deform_prob:
                                    scale = max(max(abs(q) for q in q_vals), 1.0)
                                    q_vals = [q + random.uniform(-deform_prob, deform_prob) * scale
                                              for q in q_vals]
                                if q_vals:
                                    max_q = max(q_vals)
                                    exp_vals = [math.exp((q - max_q) / 1.0) for q in q_vals]
                                    total = sum(exp_vals)
                                    probs = [e / total for e in exp_vals] if total > 0 else None
                                    idx = random.choices(range(len(av)), weights=probs, k=1)[0]
                                    ak, an = av[idx]
                                    return (execute_summon_action(self, m_uid, ak), False, None)
                        return (self._summon_attack(m_uid), False, None)

                    r = self._execute_summon_group_action(group_key, _one_member)
                    if r.get('lines'):
                        _bl('debug', f'    [{team_tag}] {summon_name}(召) 行动 '
                                     f'(组动作, 剩余{r.get("remaining", 0)})')
                        for rline in r['lines']:
                            if isinstance(rline, str) and rline.strip():
                                _bl('debug', f'        {rline.strip()}')
                    # 共享动作未耗尽则保持该组为当前行动者，继续下一批成员
                    if r.get('action_spent') and r.get('remaining', 0) > 0:
                        continue
                    self._end_turn(uid); continue
                team_tag = entry.get('team', '?')
                _bl('debug', f'    [{team_tag}] {char_name} (HP:{hp}) 行动')
                # Resolve base uid for ai_map and actions lookups (handles __actN suffixed slots)
                _base_uid = entry.get('baseUserId', uid)
                ai = ai_map.get(_base_uid)
                if ai:
                    ai.team = entry['team']
                    self._run_character_ai_turn(uid, ai, lambda line: _bl('debug', line))
                else:
                    self._end_turn(uid)
            # Check if battle ended normally (phase='ended' set by _end_turn → _check_battle_end)
            br = getattr(self, '_battle_result', None)
            if br:
                il_final = self._get_initiative()
                y_chars_d = [e.get('name','?') for e in il_final if e.get('team')=='Y' and not e.get('isSummon')]
                x_chars_d = [e.get('name','?') for e in il_final if e.get('team')=='X' and not e.get('isSummon')]
                y_disp = '+'.join(y_chars_d) if y_chars_d else '?'
                x_disp = '+'.join(x_chars_d) if x_chars_d else '?'
                if br.get('mutual_death'):
                    y_ov = br.get('y_overflow', 0)
                    x_ov = br.get('x_overflow', 0)
                    battle_log('info', f'  [同归于尽] Y队[{y_disp}]溢出{y_ov} X队[{x_disp}]溢出{x_ov} → {br["winner"]}胜')
                else:
                    # Normal end: file-only (debug) in prelim; terminal+file in knockout
                    battle_log('debug', f'  [战斗结束] Y队[{y_disp}] X队[{x_disp}] → {br["winner"]}胜')
                return {'winner': br['winner'], 'rounds': rc,
                        'mutual_death': br.get('mutual_death', False),
                        'y_overflow': br.get('y_overflow', 0),
                        'x_overflow': br.get('x_overflow', 0)}
            return self._timeout_verdict()
        finally:
            if self.quiet:
                set_terminal_quiet(False)

# ============================================================
#  Tournament runner
# ============================================================

class Tournament:
    def __init__(self, tournament_type=None, run_times=None, enabled_modes=None, settings=None):
        self._settings = settings if settings is not None else settings_snapshot()
        self.char_map = {}
        self.ai_map = {}
        self.results = []
        self.running = False
        self._state_lock = threading.Lock()  # protects results/running across threads
        self.start_time = None
        self._parallel_prelim = False  # True during parallel prelim (skip side effects)
        self.tournament_type = self._settings['DEFAULT_TOURNAMENT_TYPE'] if tournament_type is None else tournament_type
        self.run_times = self._settings['DEFAULT_RUN_TIMES'] if run_times is None else run_times
        self.run_index = 0  # Current run number (1-indexed)
        self.all_run_brackets = []  # Accumulated bracket info for each run
        self._current_bracket_info = {}  # Per-run bracket draw info
        self._pre_paired_brackets = set()  # Labels with pre-paired QF
        self._rankings_info = {}  # Per-run prelim rankings
        self._run_stats = []  # Per-run (win_counts, battle_counts) for average stats
        self._results_start_idx = 0  # Track where current run's results start
        self.char_season_status = {}  # Per-run per-character season avg status {serial: 0-100}
        self.enabled_modes = enabled_modes if enabled_modes is not None else self._settings['DEFAULT_ENABLED_MODES']  # Filter which formats to run

    @pinned
    def init_characters(self):
        engine = CombatEngine()
        AIController.load_q_table()  # Load trained weights if available
        for c in ALL_CHARACTERS:
            uid = f"char_{c['serial']}"
            load_character_to_engine(engine, c, uid)
            if not c.get('pre_transformed'):
                engine.process_command(uid, '.hs')
            self.char_map[c['serial']] = uid
            self.ai_map[uid] = AIController(uid, c, 'Y')
        battle_log('info', f'Loaded {len(self.char_map)} characters')

    @pinned
    def _resolve_clones(self, team_a_serials, team_b_serials, engine):
        """Resolve serials to uids, cloning characters that appear on both sides
        (or multiple times on the same side). Each clone gets independent HP/MP/spells.
        Returns (a_uids, b_uids)."""
        a_uids = []; b_uids = []
        serial_use_index = {}  # serial → how many times used so far

        def _resolve_one(serial, team_list):
            base_uid = self.char_map[serial]
            idx = serial_use_index.get(serial, 0)
            serial_use_index[serial] = idx + 1
            if idx == 0:
                team_list.append(base_uid)
                return
            # Need a clone — load independent copy into engine
            clone_uid = f"{base_uid}_clone{idx}"
            c_data = next(c for c in ALL_CHARACTERS if c['serial'] == serial)
            load_character_to_engine(engine, c_data, clone_uid)
            if not c_data.get('pre_transformed'):
                engine.process_command(clone_uid, '.hs')
            # Clone AI controller with same reaction weights as original
            orig_ai = self.ai_map.get(base_uid)
            clone_ai = AIController(clone_uid, c_data, 'Y')
            if orig_ai:
                clone_ai.react_dodge_w = orig_ai.react_dodge_w
                clone_ai.react_counter_w = orig_ai.react_counter_w
            self.ai_map[clone_uid] = clone_ai
            team_list.append(clone_uid)

        for s in team_a_serials:
            _resolve_one(s, a_uids)
        for s in team_b_serials:
            _resolve_one(s, b_uids)
        return a_uids, b_uids

    @pinned
    def battle(self, team_a, team_b, map_size, phase_label='', best_of=1, display_level=None, delay=None, max_rounds=None):
        # Look up display names
        name_map = {c['serial']: c['name'] for c in ALL_CHARACTERS}
        a_names = [name_map.get(s, s) for s in team_a]
        b_names = [name_map.get(s, s) for s in team_b]
        a_name = '+'.join(team_a)
        b_name = '+'.join(team_b)
        a_display = '+'.join(a_names)
        b_display = '+'.join(b_names)
        if not self._parallel_prelim:
            battle_log('info', f'  [Battle] Y队：{a_display} v.s. X队：{b_display} [{map_size}]')
        engine = BattleEngine(display_level=display_level, delay=delay, max_rounds=max_rounds)
        # Only load fighting characters (not all 12) for speed
        fighting_serials = set(team_a + team_b)
        for c in ALL_CHARACTERS:
            if c['serial'] not in fighting_serials:
                continue
            uid = self.char_map[c['serial']]
            load_character_to_engine(engine, c, uid)
            if not c.get('pre_transformed'):
                engine.process_command(uid, '.hs')
        # Resolve teams — clone characters that appear on both sides
        a_uids, b_uids = self._resolve_clones(team_a, team_b, engine)
        for uid in a_uids: self.ai_map[uid].team = 'Y'
        for uid in b_uids: self.ai_map[uid].team = 'X'
        # Inject reaction weights into engine for _coc7_attack
        engine._ai_react_dodge_w = {}
        engine._ai_react_counter_w = {}
        engine._ai_react_block_w = {}
        for uid in a_uids + b_uids:
            ai = self.ai_map.get(uid)
            if ai:
                engine._ai_react_dodge_w[uid] = ai.react_dodge_w
                engine._ai_react_counter_w[uid] = ai.react_counter_w
                engine._ai_react_block_w[uid] = getattr(ai, 'react_block_w', 0)
        # Build per-uid season status mapping from per-character dict
        uid_season = {}
        for serial, uid in zip(team_a + team_b, a_uids + b_uids):
            uid_season[uid] = self.char_season_status.get(serial, battle_setting('SEASON_FALLBACK_STATUS'))
        engine.setup_battle(a_uids, b_uids, map_size, season_status=uid_season)
        result = engine.run_battle(self.ai_map)
        # Update reaction weights: winners get +1 to their reaction type (skip in parallel prelim)
        if not self._parallel_prelim:
            for uid in a_uids + b_uids:
                ai = self.ai_map.get(uid)
                if not ai: continue
                uid_team = 'Y' if uid in a_uids else 'X'
                won = result['winner'] == uid_team
                if won:
                    ai.react_dodge_w += battle_setting('AI_REACTION_REWARD')
                    ai.react_counter_w += battle_setting('AI_REACTION_REWARD')
                else:
                    ai.react_dodge_w = max(battle_setting('AI_REACTION_MIN_WEIGHT'), ai.react_dodge_w - battle_setting('AI_REACTION_PENALTY'))
                    ai.react_counter_w = max(battle_setting('AI_REACTION_MIN_WEIGHT'), ai.react_counter_w - battle_setting('AI_REACTION_PENALTY'))
        winner = a_name if result['winner'] == 'Y' else b_name
        loser = b_name if result['winner'] == 'Y' else a_name
        if not self._parallel_prelim:
            battle_log('info', f'    → {winner} 胜 ({result["rounds"]}回合)')
            self.results.append((a_name, b_name, winner, result['rounds'], map_size, phase_label, best_of > 1))
        return (winner, loser, result['rounds'], result)

    @pinned
    def _best_of_n(self, team_a, team_b, map_size, phase_label, n=None):
        """Best-of-N series. Returns winner string like 'Y1+Y2'."""
        n = battle_setting('BO_N_FINAL') if n is None else n
        wins_a, wins_b = 0, 0
        needed = n // 2 + 1
        a_str = '+'.join(team_a); a_disp = self._team_display(team_a)
        b_disp = self._team_display(team_b)
        battle_log('info', f'  ┌ Bo{n}: {a_disp} vs {b_disp}')
        for game in range(1, n + 1):
            try:
                winner, loser, rds, _ = self.battle(team_a, team_b, map_size, f'{phase_label} G{game}', best_of=n, display_level=battle_setting("DISPLAY_KNOCKOUT"), delay=battle_setting("DELAY_KNOCKOUT_TURN"), max_rounds=battle_setting("MAX_ROUNDS_KNOCKOUT"))
            except Exception as e:
                battle_log('error', f'  !! Bo{n} G{game}异常跳过: {e}')
                continue  # skip this game without awarding a win to either side
            if winner == a_str: wins_a += 1
            else: wins_b += 1
            w_disp = self._team_display(winner.split('+'))
            battle_log('info', f'  │ G{game}: {w_disp} ({wins_a}-{wins_b})')
            if wins_a >= needed or wins_b >= needed: break
            if battle_setting('DELAY_KNOCKOUT_BETWEEN_GAMES') > 0:
                time.sleep(battle_setting('DELAY_KNOCKOUT_BETWEEN_GAMES'))
        final_winner = a_str if wins_a >= needed else '+'.join(team_b)
        fw_disp = self._team_display(final_winner.split('+'))
        battle_log('info', f'  └ Bo{n}: {fw_disp} {wins_a}-{wins_b}')
        return final_winner

    @pinned
    def _best_of_seven(self, team_a, team_b, map_size, phase_label):
        """Backward-compatible entry point using the configured final series length."""
        return self._best_of_n(team_a, team_b, map_size, phase_label, n=battle_setting('BO_N_FINAL'))

    @pinned
    def _run_interleaved_series(self, all_series):
        """Run BoN series one complete series at a time, cycling formats.
        all_series: list of {ta, tb, n, label}. Mutates to add 'winner'."""
        for s in all_series:
            winner = self._best_of_n(s['ta'], s['tb'], battle_setting('KNOCKOUT_MAP_SIZE'), s['label'], n=s['n'])
            s['winner'] = winner
            s['a_str'] = '+'.join(s['ta'])
            s['b_str'] = '+'.join(s['tb'])

    @pinned
    def _run_knockout_bracket(self, brackets, phase_label_prefix=''):
        """Run all knockout brackets interleaved — QF games mixed across formats, then SF, etc.
        brackets: list of {label, top_teams, map_size}"""
        # Step 1: All draws and QF setup (per-slot interleaved: QF1 across formats, then QF2, etc.)
        all_qf_series = []
        all_qf_by_slot = {0: [], 1: [], 2: [], 3: []}  # slot → [(label, team_a, team_b)]
        all_sf_participants = []  # [(label, [team_list, ...])]

        pre_paired = getattr(self, '_pre_paired_brackets', set())

        for br in brackets:
            label = br['label']; top = br['top_teams']; ms = br['map_size']
            seeds = top[:4]
            challengers = top[4:8] if len(top) >= 8 else []

            if len(challengers) >= 4:
                if label in pre_paired:
                    # Combined draw already done in _build_top8_with_playin —
                    # challengers are already paired to seeds in order: [chal_for_s0, chal_for_s1, chal_for_s2, chal_for_s3]
                    seed_names = ['#1', '#2', '#3', '#4']
                    battle_log('info', f'[{label}] 八进四 (已由合并抽签确定):')
                    for si in range(4):
                        sn = self._team_display(seeds[si])
                        cn = self._team_display(challengers[si])
                        battle_log('info', f'  {seed_names[si]}({sn}) vs ({cn})')
                        all_qf_by_slot[si].append({
                            'ta': seeds[si], 'tb': challengers[si],
                            'n': battle_setting("BO_N_QF"), 'label': f'{label} QF{si+1}',
                            'bracket_label': label,
                        })
                    battle_log('info', '')
                else:
                    battle_log('info', f'[{label}] 抽签:')
                    remaining_seeds = list(range(4))
                    seed_names = ['#1', '#2', '#3', '#4']
                    qf_pairs = []
                    for ci, chal in enumerate(challengers):
                        weights = [battle_setting('KNOCKOUT_DRAW_WEIGHTS')[ci][s] for s in remaining_seeds]
                        chosen = random.choices(remaining_seeds, weights=weights, k=1)[0]
                        remaining_seeds.remove(chosen)
                        qf_pairs.append((seeds[chosen], chal))
                        cn = self._team_display(chal); sn = self._team_display(seeds[chosen])
                        battle_log('info', f'  {seed_names[chosen]}({sn}) vs rank{5+ci}({cn})')
                    battle_log('info', '')
                    battle_log('info', f'(抽签结果展示{battle_setting("DELAY_DRAW_DISPLAY")}秒...)')
                    time.sleep(battle_setting('DELAY_DRAW_DISPLAY'))

                    for si, (seed, chal) in enumerate(qf_pairs):
                        all_qf_by_slot[si].append({
                            'ta': seed, 'tb': chal,
                            'n': battle_setting("BO_N_QF"), 'label': f'{label} QF{si+1}',
                            'bracket_label': label,
                        })
            else:
                # No QF needed — seeds go straight to SF
                all_sf_participants.append((label, seeds))

        # Step 2: Flatten QF series: QF1 across formats, then QF2, etc.
        for si in range(4):
            all_qf_series.extend(all_qf_by_slot[si])

        # Step 2b: Run all QF series (one complete series at a time)
        if all_qf_series:
            battle_log('info', '')
            battle_log('info', f'--- 四分之一决赛 Bo{battle_setting("BO_N_QF")}（跨赛制穿插）---')
            self._run_interleaved_series(all_qf_series)

        # Step 3: Build SF matchups from QF results
        for br in brackets:
            label = br['label']; top = br['top_teams']
            if len(top) >= 8:
                br_qf = [s for s in all_qf_series if s.get('bracket_label') == label]
                sf_teams = [s['winner'].split('+') if '+' in s['winner'] else [s['winner']] for s in br_qf]
                all_sf_participants.append((label, sf_teams))
            # else: already added above (no QF case)

        # Step 4: Build SF series (SF1 across formats, then SF2)
        all_sf_series = []
        sf_losers = []
        for label, sf_teams in all_sf_participants:
            if len(sf_teams) < 4:
                battle_log('error', f'淘汰赛{label}半决赛需要至少4队，实际{len(sf_teams)}队，跳过')
                continue
            t0, t1, t2, t3 = sf_teams[0], sf_teams[1], sf_teams[2], sf_teams[3]
            all_sf_series.append({
                'ta': t0, 'tb': t3, 'n': battle_setting('BO_N_SF'),
                'label': f'{label} SF1', 'bracket_label': label, 'slot': 1,
            })
            all_sf_series.append({
                'ta': t1, 'tb': t2, 'n': battle_setting('BO_N_SF'),
                'label': f'{label} SF2', 'bracket_label': label, 'slot': 2,
            })

        if all_sf_series:
            all_sf_series.sort(key=lambda x: x.pop('slot', 0))
            battle_log('info', '')
            battle_log('info', f'--- 半决赛 Bo{battle_setting("BO_N_SF")}（跨赛制穿插）---')
            self._run_interleaved_series(all_sf_series)

        # Step 5: Collect winners/losers per bracket, build Bronze + Final
        bronze_series = []
        final_series = []
        for br in brackets:
            label = br['label']
            br_sf = [s for s in all_sf_series if s.get('bracket_label') == label]
            if len(br_sf) < 2: continue
            sf1, sf2 = br_sf[0], br_sf[1]

            sf1_winner_team = sf1['winner'].split('+') if '+' in sf1['winner'] else [sf1['winner']]
            sf2_winner_team = sf2['winner'].split('+') if '+' in sf2['winner'] else [sf2['winner']]
            sf1_loser = sf1['tb'] if sf1['winner'] == sf1['a_str'] else sf1['ta']
            sf2_loser = sf2['tb'] if sf2['winner'] == sf2['a_str'] else sf2['ta']

            bronze_series.append({
                'ta': sf1_loser, 'tb': sf2_loser, 'n': battle_setting('BO_N_BRONZE'),
                'label': f'{label} Bronze', 'bracket_label': label,
            })
            final_series.append({
                'ta': sf1_winner_team, 'tb': sf2_winner_team, 'n': battle_setting('BO_N_FINAL'),
                'label': f'{label} Final', 'bracket_label': label,
            })

        if bronze_series:
            battle_log('info', '')
            battle_log('info', f'--- 铜牌赛 Bo{battle_setting("BO_N_BRONZE")}（跨赛制穿插）---')
            self._run_interleaved_series(bronze_series)

        if final_series:
            battle_log('info', '')
            battle_log('info', f'--- 决赛 Bo{battle_setting("BO_N_FINAL")}（跨赛制穿插）---')
            self._run_interleaved_series(final_series)

        # Announce results
        for br in brackets:
            label = br['label']
            br_final = [s for s in final_series if s.get('bracket_label') == label]
            br_bronze = [s for s in bronze_series if s.get('bracket_label') == label]
            if br_final:
                champion = br_final[0]['winner']
                silver = br_final[0]['b_str'] if champion == br_final[0]['a_str'] else br_final[0]['a_str']
            else: champion = silver = '—'
            bronze = br_bronze[0]['winner'] if br_bronze else '—'
            battle_log('info', f'[{label}] 冠军: {self._team_display(champion.split("+")) if champion != "—" else "—"}'
                       f'  亚军: {self._team_display(silver.split("+")) if silver != "—" else "—"}'
                       f'  季军: {self._team_display(bronze.split("+")) if bronze != "—" else "—"}')


    @pinned
    def _run_phase_with_format(self, phase_name, teams, map_sizes, label_prefix, rr_rounds=None):
        rr_rounds = battle_setting('RR_ROUNDS_1V1') if rr_rounds is None else rr_rounds
        rrc = '单' if rr_rounds==1 else ('双' if rr_rounds==2 else str(rr_rounds))
        battle_log('info', f"[{phase_name}] {len(teams)}支队伍 | {rrc}循环 → Bo{battle_setting('BO_N_FINAL')}决赛 + 铜牌赛")
        results = []
        for map_size in map_sizes:
            battle_log('info', f'  --- 地图 {map_size} ---')
            standings = {i: 0 for i in range(len(teams))}
            battle_log('info', f'  [小组赛 双循环]')
            for i, j in itertools.combinations(range(len(teams)), 2):
                for rr in range(2):
                    ta = teams[i]; tb = teams[j]
                    team_size = len(ta)  # 1→1v1, 2→2v2, 3→3v3
                    fmt_tag = f'{team_size}v{team_size}'
                    fmt_rounds = battle_setting('MAX_ROUNDS_PRELIM_BY_FORMAT').get(fmt_tag, battle_setting('MAX_ROUNDS_PRELIM'))
                    winner, loser, rds, _ = self.battle(ta, tb, map_size, f'{label_prefix} RR {map_size}', display_level=battle_setting("DISPLAY_PRELIM"), delay=battle_setting("DELAY_PRELIM_TURN"), max_rounds=fmt_rounds)
                    winner_set = set(winner.split('+'))
                    for idx in [i, j]:
                        team_set = set(teams[idx])
                        if team_set == winner_set or team_set.issubset(winner_set):
                            standings[idx] += 1
                            break
                    time.sleep(battle_setting('DELAY_PRELIM_BETWEEN_BATTLES'))
            ranked = sorted(standings.items(), key=lambda x: -x[1])
            rank_names = [(r[0], '+'.join(teams[r[0]]), r[1]) for r in ranked]
            battle_log('info', f'  排名: {[(n,pts) for _,n,pts in rank_names]}')

            # Play-in: ranks 7-10 fight for last 2 knockout spots
            playin_advancers = []
            if False:
                playin_teams = [teams[r[0]] for r in ranked[6:10]]
                playin_advancers = self._run_playin(playin_teams, map_size, f'{label_prefix} {map_size}')

            if len(ranked) >= 8:
                # Build top8: ranks 1-6 + playin qualifiers (or ranks 7-8 if no playin)
                top8 = [teams[r[0]] for r in ranked[:6]]
                if playin_advancers:
                    for team, _ in playin_advancers:
                        top8.append(team)
                else:
                    top8.extend([teams[r[0]] for r in ranked[6:8]])
                champion, silver, bronze = self._run_knockout_bracket(top8, map_size, f'{label_prefix} {map_size}')
            elif len(ranked) >= 4:
                top4 = [teams[r[0]] for r in ranked[:4]]
                champion, silver, bronze = self._run_knockout_bracket(top4, map_size, f'{label_prefix} {map_size}')
                battle_log('info', f'  [Champion] 冠军: {champion}')
                battle_log('info', f'  [Silver]   亚军: {silver}')
                battle_log('info', f'  [Bronze]   季军: {bronze}')
            elif len(ranked) >= 2:
                t0 = teams[ranked[0][0]]; t1 = teams[ranked[1][0]]
                champion = self._best_of_n(t0, t1, map_size, f'{label_prefix} {map_size} Final', n=battle_setting("BO_N_FINAL"))
                battle_log('info', f'  [Champion] 冠军: {champion}')
                silver = '+'.join(t1 if champion == '+'.join(t0) else t0)
                bronze = '—'
            else:
                champion = silver = bronze = '—'

            if len(ranked) >= 8:
                pass  # champion/silver/bronze already logged in _run_knockout_bracket
            results.append({
                'phase': phase_name, 'map': map_size,
                'ranked': ranked, 'rank_names': rank_names, 'teams': teams,
                'champion': champion, 'silver': silver, 'bronze': bronze,
                'playin_advancers': playin_advancers,
            })
        return results

    @pinned
    def _build_rr_pool(self, phase_name, teams, map_sizes, label_prefix, rr_rounds=None):
        """Build list of (label, map_size, ta, tb) for RR matches in a phase.
        rr_rounds: number of times each pair fights per map (1=single, 2=double, 4=quad)."""
        rr_rounds = battle_setting('RR_ROUNDS_1V1') if rr_rounds is None else rr_rounds
        pool = []
        for map_size in map_sizes:
            for i, j in itertools.combinations(range(len(teams)), 2):
                for _ in range(rr_rounds):
                    pool.append((f'{label_prefix} RR {map_size}', map_size,
                                 teams[i], teams[j], i, j))
        return pool

    @pinned
    def _allocate_individual_bonus_matches(self, singles, rr_rounds, target_matches, map_size=None, label='1v1 Alloc'):
        map_size = battle_setting('BONUS_MAP_SIZE') if map_size is None else map_size
        n_players = len(singles)
        if n_players <= 1:
            return []
        base_matches_each = rr_rounds * (n_players - 1)
        deficits = [max(0, target_matches - base_matches_each) for _ in range(n_players)]
        total_deficit = sum(deficits)
        if total_deficit == 0:
            return []

        import heapq
        heap = [(-d, idx) for idx, d in enumerate(deficits) if d > 0]
        heapq.heapify(heap)
        allocated = []
        while len(heap) >= 2:
            d1, i1 = heapq.heappop(heap)
            d2, i2 = heapq.heappop(heap)
            d1 = -d1; d2 = -d2
            allocated.append((label, map_size, [singles[i1]], [singles[i2]], i1, i2))
            d1 -= 1; d2 -= 1
            if d1 > 0:
                heapq.heappush(heap, (-d1, i1))
            if d2 > 0:
                heapq.heappush(heap, (-d2, i2))

        if heap:
            dlast, ilast = heapq.heappop(heap)
            dlast = -dlast
            partner = 0 if ilast != 0 else (1 if n_players > 1 else 0)
            for _ in range(dlast):
                allocated.append((label, map_size, [singles[ilast]], [singles[partner]], ilast, partner))
        return allocated

    @pinned
    def _allocate_team_bonus_matches(self, teams, rr_rounds, bonus_count, map_size=None, label='Alloc'):
        map_size = battle_setting('BONUS_MAP_SIZE') if map_size is None else map_size
        if bonus_count <= 0:
            return []

        n = len(teams)
        extra_matches = [0] * n
        allocated = []
        for _ in range(bonus_count):
            best_pairs = []
            best_score = None
            for i in range(n):
                for j in range(i + 1, n):
                    score = extra_matches[i] + extra_matches[j]
                    if best_score is None or score < best_score:
                        best_score = score
                        best_pairs = [(i, j)]
                    elif score == best_score:
                        best_pairs.append((i, j))
            if not best_pairs:
                break
            i, j = random.choice(best_pairs)
            allocated.append((label, map_size, teams[i], teams[j], i, j))
            extra_matches[i] += 1
            extra_matches[j] += 1
        return allocated

    @pinned
    def _team_display(self, team_list):
        """Convert team serial list ['Y1','Y2'] to display string '初音(Y1)+春晌(Y2)'."""
        parts = []
        for serial in team_list:
            uid = self.char_map.get(serial, '')
            ai = self.ai_map.get(uid)
            if ai:
                parts.append(f'{ai.char_data["name"]}({serial})')
            else:
                parts.append(serial)
        return '+'.join(parts)

    @pinned
    def _calc_points(self, result):
        """Calculate (winner_pts, loser_pts) from battle result.
        Normal win: 3-0. Timeout win: HP-ratio split, max 2.5:0.5, min 1.5:1.5."""
        if not result.get('timeout'):
            return (3.0, 0.0)
        yr = result.get('y_ratio', 0); xr = result.get('x_ratio', 0)
        total = yr + xr
        if total <= 0: return (1.5, 1.5)  # fallback: equal split when both ratios are 0
        w_share = yr / total if result['winner'] == 'Y' else xr / total
        w_pts = 1.5 + (w_share - 0.5) * 2.0
        w_pts = max(1.5, min(2.5, w_pts))
        l_pts = 3.0 - w_pts
        return (round(w_pts, 2), round(l_pts, 2))

    @pinned
    def run_phases(self, write_results_flag=True):
        self.running = True
        self.start_time = datetime.now()
        self.run_index += 1  # Increment run counter (starts at 0)

        # Generate per-character season average status for this run
        if battle_setting('SEASON_AVG_STATUS') is None:
            self.char_season_status = {s: random.randint(0, 100) for s in self.char_map}
        elif isinstance(battle_setting('SEASON_AVG_STATUS'), dict):
            self.char_season_status = dict(battle_setting('SEASON_AVG_STATUS'))  # copy
        else:
            self.char_season_status = {s: int(battle_setting('SEASON_AVG_STATUS')) for s in self.char_map}

        singles = list(self.char_map.keys())
        battle_log('info', '=' * 60)
        battle_log('info', '锦标赛 — 单循环预赛 + 积分制 + 八强淘汰赛')
        battle_log('info', '  胜=3分 败=0分 | 超时按HP占比分配(1.5~2.5)')
        battle_log('info', '=' * 60)
        # Log per-character season status
        nm = {c['serial']: c['name'] for c in ALL_CHARACTERS}
        status_lines = [f'{nm.get(s,s)}({s}):{self.char_season_status.get(s,"?")}' for s in singles]
        battle_log('info', f'  赛季平均状态(分角色): {", ".join(status_lines)}')
        battle_log('info', '')

        # Per-player standings (1v1 points)
        player_pts = {s: 0.0 for s in singles}
        player_wins = {s: 0 for s in singles}
        player_battles = {s: 0 for s in singles}

        # ---- Build all RR match pools ----

        # Phase 1: 1v1 — RR，按配置补齐每人的目标场数。
        p1_pool = []
        if '1v1' in self.enabled_modes:
            teams_1v1 = [[s] for s in singles]
            n_players = len(singles)
            effective_rr_1v1 = min(battle_setting('RR_ROUNDS_1V1'), battle_setting('TARGET_1V1_MATCHES') // (n_players - 1)) if n_players > 1 else battle_setting('RR_ROUNDS_1V1')
            p1_pool = self._build_rr_pool('1v1', teams_1v1, [battle_setting('DEFAULT_MAP_SIZE')], '1v1', rr_rounds=effective_rr_1v1)
            allocated_bonus = self._allocate_individual_bonus_matches(singles, effective_rr_1v1, battle_setting('TARGET_1V1_MATCHES'), battle_setting('BONUS_MAP_SIZE'), '1v1 Alloc')
            p1_pool = allocated_bonus + p1_pool
            battle_log('info',
                f'[1v1] {n_players}人 × {effective_rr_1v1}轮循环 = {len(p1_pool)-len(allocated_bonus)}场'
                f' + {len(allocated_bonus)}场调配 = {len(p1_pool)}场 (每人目标{battle_setting("TARGET_1V1_MATCHES")}场)')
        else:
            battle_log('info', '[1v1] 已跳过（未启用）')
            teams_1v1 = []

        # Phase 2: 2v2 — use shared team table definitions
        p2_pool = []
        if '2v2' in self.enabled_modes:
            teams_2v2 = [list(team) for team in battle_setting('TEAM_TABLES_2V2') if all(s in self.char_map for s in team)]
            p2_pool = self._build_rr_pool('2v2', teams_2v2, battle_setting('MAP_SIZES_2V2'), '2v2', rr_rounds=battle_setting('RR_ROUNDS_2V2'))
            allocated_bonus_2v2 = self._allocate_team_bonus_matches(teams_2v2, battle_setting('RR_ROUNDS_2V2'), battle_setting('RANDOM_BONUS_2V2'), battle_setting('BONUS_MAP_SIZE'), '2v2 Alloc')
            p2_pool = allocated_bonus_2v2 + p2_pool
            rrc = '单' if battle_setting('RR_ROUNDS_2V2')==1 else ('双' if battle_setting('RR_ROUNDS_2V2')==2 else str(battle_setting('RR_ROUNDS_2V2')))
            battle_log('info', f'[2v2] {len(teams_2v2)}队 {rrc}循环{len(p2_pool)-len(allocated_bonus_2v2)}场 + {len(allocated_bonus_2v2)}场调配 = {len(p2_pool)}场')
        else:
            battle_log('info', '[2v2] 已跳过（未启用）')
            teams_2v2 = []

        # Phase 3: 3v3 — use shared team table definitions
        p3_pool = []
        if '3v3' in self.enabled_modes:
            teams_3v3 = [list(team) for team in battle_setting('TEAM_TABLES_3V3') if all(s in self.char_map for s in team)]
            p3_pool = self._build_rr_pool('3v3', teams_3v3, battle_setting('MAP_SIZES_3V3'), '3v3', rr_rounds=battle_setting('RR_ROUNDS_3V3'))
            allocated_bonus_3v3 = self._allocate_team_bonus_matches(teams_3v3, battle_setting('RR_ROUNDS_3V3'), battle_setting('RANDOM_BONUS_3V3'), battle_setting('BONUS_MAP_SIZE'), '3v3 Alloc')
            p3_pool = allocated_bonus_3v3 + p3_pool
            rrc = '单' if battle_setting('RR_ROUNDS_3V3')==1 else ('双' if battle_setting('RR_ROUNDS_3V3')==2 else str(battle_setting('RR_ROUNDS_3V3')))
            battle_log('info', f'[3v3] {len(teams_3v3)}队 {rrc}循环{len(p3_pool)-len(allocated_bonus_3v3)}场 + {len(allocated_bonus_3v3)}场调配 = {len(p3_pool)}场')
        else:
            battle_log('info', '[3v3] 已跳过（未启用）')
            teams_3v3 = []

        # ---- Shuffle all RR pools together ----
        full_pool = []
        for label, ms, ta, tb, i, j in p1_pool:
            full_pool.append(('1v1', label, ms, ta, tb, i, j))
        for label, ms, ta, tb, i, j in p2_pool:
            full_pool.append(('2v2', label, ms, ta, tb, i, j))
        for label, ms, ta, tb, i, j in p3_pool:
            full_pool.append(('3v3', label, ms, ta, tb, i, j))

        random.shuffle(full_pool)
        total_matches = len(full_pool)
        battle_log('info', f'预赛总计 {total_matches} 场, 随机穿插进行')
        battle_log('info', '')

        # ---- Execute RR matches with point scoring (all formats) ----
        # Track team-level standings for 2v2/3v3 knockout (pts, wins, battles like 1v1)
        v2_standings = {i: {'pts': 0.0, 'wins': 0, 'battles': 0} for i in range(len(teams_2v2))}
        v3_standings = {i: {'pts': 0.0, 'wins': 0, 'battles': 0} for i in range(len(teams_3v3))}

        use_parallel = battle_setting('PRELIM_WORKERS') > 1

        if use_parallel:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            self._parallel_prelim = True
            standings_lock = threading.Lock()
            battle_log('info', f'预赛并行模式: {battle_setting("PRELIM_WORKERS")} workers')
            # Submit all battles
            future_map = {}
            try:
                with ThreadPoolExecutor(max_workers=battle_setting('PRELIM_WORKERS')) as executor:
                    for args in full_pool:
                        phase_tag, label, map_size, ta, tb, i, j = args
                        fmt_max_rounds = battle_setting('MAX_ROUNDS_PRELIM_BY_FORMAT').get(phase_tag, battle_setting('MAX_ROUNDS_PRELIM'))
                        fut = executor.submit(self.battle, ta, tb, map_size, label, 1,
                                              battle_setting('DISPLAY_PRELIM'), battle_setting('DELAY_PRELIM_TURN'), fmt_max_rounds)
                        future_map[fut] = args
                        if battle_setting('DELAY_PRELIM_BETWEEN_BATTLES') > 0:
                            time.sleep(battle_setting('DELAY_PRELIM_BETWEEN_BATTLES'))
                    # Collect results
                    completed = 0
                    for fut in as_completed(future_map):
                        completed += 1
                        phase_tag, label, map_size, ta, tb, i, j = future_map[fut]
                        try:
                            winner, loser, rds, result = fut.result()
                        except Exception as e:
                            battle_log('error', f'  !! 战斗异常跳过: {label} — {e}')
                            continue
                        # Process standings (protected by lock for thread-safe dict updates)
                        with standings_lock:
                            if phase_tag == '1v1':
                                w_pts, l_pts = self._calc_points(result)
                                w_player = winner; l_player = loser
                                player_pts[w_player] = player_pts.get(w_player, 0) + w_pts
                                player_pts[l_player] = player_pts.get(l_player, 0) + l_pts
                                player_wins[w_player] = player_wins.get(w_player, 0) + 1
                                player_battles[w_player] = player_battles.get(w_player, 0) + 1
                                player_battles[l_player] = player_battles.get(l_player, 0) + 1
                            elif phase_tag == '2v2':
                                w_pts, l_pts = self._calc_points(result)
                                ws = set(winner.split('+')); ls = set(loser.split('+'))
                                for idx in [i, j]:
                                    ts = set(teams_2v2[idx])
                                    if ts == ws:
                                        v2_standings[idx]['pts'] += w_pts
                                        v2_standings[idx]['wins'] += 1
                                        v2_standings[idx]['battles'] += 1
                                    elif ts == ls:
                                        v2_standings[idx]['pts'] += l_pts
                                        v2_standings[idx]['battles'] += 1
                            elif phase_tag == '3v3':
                                w_pts, l_pts = self._calc_points(result)
                                ws = set(winner.split('+')); ls = set(loser.split('+'))
                                for idx in [i, j]:
                                    ts = set(teams_3v3[idx])
                                    if ts == ws:
                                        v3_standings[idx]['pts'] += w_pts
                                        v3_standings[idx]['wins'] += 1
                                        v3_standings[idx]['battles'] += 1
                                    elif ts == ls:
                                        v3_standings[idx]['pts'] += l_pts
                                        v3_standings[idx]['battles'] += 1
                        # Record result (parallel path skips append inside battle())
                        a_name = '+'.join(ta)
                        b_name = '+'.join(tb)
                        self.results.append((a_name, b_name, winner, rds, map_size, label, False))
                        if completed % battle_setting('PRELIM_PROGRESS_INTERVAL') == 0:
                            battle_log('info', f'  预赛进度: {completed}/{total_matches}')
            finally:
                self._parallel_prelim = False
        else:
            completed = 0
            for phase_tag, label, map_size, ta, tb, i, j in full_pool:
                completed += 1
                try:
                    fmt_max_rounds = battle_setting('MAX_ROUNDS_PRELIM_BY_FORMAT').get(phase_tag, battle_setting('MAX_ROUNDS_PRELIM'))
                    winner, loser, rds, result = self.battle(ta, tb, map_size, label, display_level=battle_setting('DISPLAY_PRELIM'), delay=battle_setting('DELAY_PRELIM_TURN'), max_rounds=fmt_max_rounds)
                    if battle_setting('DELAY_PRELIM_BETWEEN_BATTLES') > 0:
                        time.sleep(battle_setting('DELAY_PRELIM_BETWEEN_BATTLES'))
                except Exception as e:
                    battle_log('error', f'  !! 战斗异常跳过: {label} — {e}')
                    import traceback
                    battle_log('error', traceback.format_exc()[-300:])
                    continue
                # 1v1: award points to individual players
                if phase_tag == '1v1':
                    w_pts, l_pts = self._calc_points(result)
                    w_player = winner; l_player = loser
                    player_pts[w_player] = player_pts.get(w_player, 0) + w_pts
                    player_pts[l_player] = player_pts.get(l_player, 0) + l_pts
                    player_wins[w_player] = player_wins.get(w_player, 0) + 1
                    player_battles[w_player] = player_battles.get(w_player, 0) + 1
                    player_battles[l_player] = player_battles.get(l_player, 0) + 1
                # 2v2/3v3: award team-level standings with point system (same as 1v1)
                elif phase_tag == '2v2':
                    w_pts, l_pts = self._calc_points(result)
                    ws = set(winner.split('+')); ls = set(loser.split('+'))
                    for idx in [i, j]:
                        ts = set(teams_2v2[idx])
                        if ts == ws:
                            v2_standings[idx]['pts'] += w_pts
                            v2_standings[idx]['wins'] += 1
                            v2_standings[idx]['battles'] += 1
                        elif ts == ls:
                            v2_standings[idx]['pts'] += l_pts
                            v2_standings[idx]['battles'] += 1
                elif phase_tag == '3v3':
                    w_pts, l_pts = self._calc_points(result)
                    ws = set(winner.split('+')); ls = set(loser.split('+'))
                    for idx in [i, j]:
                        ts = set(teams_3v3[idx])
                        if ts == ws:
                            v3_standings[idx]['pts'] += w_pts
                            v3_standings[idx]['wins'] += 1
                            v3_standings[idx]['battles'] += 1
                        elif ts == ls:
                            v3_standings[idx]['pts'] += l_pts
                            v3_standings[idx]['battles'] += 1
                if completed % battle_setting('PRELIM_PROGRESS_INTERVAL') == 0:
                    battle_log('info', f'  预赛进度: {completed}/{total_matches}')

        # ---- Display all standings and run all knockouts ----

        # 1v1 standings
        if '1v1' in self.enabled_modes:
            battle_log('info', '')
            battle_log('info', '=' * 60)
            battle_log('info', '预赛结束 — 1v1 积分排名')
            ranked_1v1 = sorted(player_pts.items(), key=lambda x: (-x[1], -player_wins.get(x[0], 0)))
            for rank, (player, pts) in enumerate(ranked_1v1, 1):
                w = player_wins.get(player, 0); b = player_battles.get(player, 0)
                uid = self.char_map.get(player, '')
                ai = self.ai_map.get(uid)
                name = ai.char_data.get('name', player) if ai else player
                battle_log('info', f'  {rank:2d}. {player} {name}  {pts:.1f}分  {w}胜/{b}场')
        else:
            ranked_1v1 = []

        # 2v2 standings
        if '2v2' in self.enabled_modes:
            battle_log('info', '')
            battle_log('info', '=' * 60)
            battle_log('info', '预赛结束 — 2v2 积分排名')
            ranked_2v2 = sorted(v2_standings.items(), key=lambda x: (-x[1]['pts'], -x[1]['wins']))
            for rank, (idx, s) in enumerate(ranked_2v2, 1):
                names = []
                for serial in teams_2v2[idx]:
                    uid = self.char_map.get(serial, '')
                    ai = self.ai_map.get(uid)
                    names.append(f'{ai.char_data["name"]}({serial})' if ai else serial)
                team_str = '+'.join(names)
                battle_log('info', f'  {rank:2d}. {team_str}  {s["pts"]:.1f}分  {s["wins"]}胜/{s["battles"]}场')
        else:
            ranked_2v2 = []

        # 3v3 standings
        if '3v3' in self.enabled_modes:
            battle_log('info', '')
            battle_log('info', '=' * 60)
            battle_log('info', '预赛结束 — 3v3 积分排名')
            ranked_3v3 = sorted(v3_standings.items(), key=lambda x: (-x[1]['pts'], -x[1]['wins']))
            for rank, (idx, s) in enumerate(ranked_3v3, 1):
                names = []
                for serial in teams_3v3[idx]:
                    uid = self.char_map.get(serial, '')
                    ai = self.ai_map.get(uid)
                    names.append(f'{ai.char_data["name"]}({serial})' if ai else serial)
                team_str = '+'.join(names)
                battle_log('info', f'  {rank:2d}. {team_str}  {s["pts"]:.1f}分  {s["wins"]}胜/{s["battles"]}场')
        else:
            ranked_3v3 = []

        battle_log('info', '')
        battle_log('info', f'(排名展示{battle_setting("DELAY_STANDINGS_DISPLAY")}秒...)')
        time.sleep(battle_setting('DELAY_STANDINGS_DISPLAY'))

        # Collect ranking info (also needed by prelim-only mode / sim.py multi-run stats)
        self._rankings_info = {
            '1v1': [(rank, serial, pts, player_wins.get(serial, 0), player_battles.get(serial, 0),
                     self._team_display([serial]))
                    for rank, (serial, pts) in enumerate(ranked_1v1, 1)],
            '2v2': [(rank, idx, s['pts'], s['wins'], s['battles'],
                     self._team_display(teams_2v2[idx]))
                    for rank, (idx, s) in enumerate(ranked_2v2, 1)],
            '3v3': [(rank, idx, s['pts'], s['wins'], s['battles'],
                     self._team_display(teams_3v3[idx]))
                    for rank, (idx, s) in enumerate(ranked_3v3, 1)],
        }

        if self.tournament_type == 0:
            battle_log('info', '')
            battle_log('info', '=' * 60)
            battle_log('info', '已启用 --type=0：仅执行预赛阶段，跳过淘汰赛阶段。')
            battle_log('info', '=' * 60)
            elapsed = (datetime.now() - self.start_time).total_seconds()
            battle_log('info', f'预赛阶段完成！共执行 {total_matches} 场预赛战斗，耗时{elapsed:.0f}秒')
            self.running = False
            return

        # ---- Run knockouts for each format (5-12 play-in → 8-team bracket) ----
        all_brackets = []
        # Helper: run 5-12 play-in → 5v12, 6v11, 7v10, 8v9, winners + ranks 1-4 = top8
        # Combined draw: play-in pairing and QF seed assignment merged into one draw
        def _build_top8_with_playin(ranked_list, get_team_fn, label, map_size=None):
            map_size = battle_setting('KNOCKOUT_MAP_SIZE') if map_size is None else map_size
            if len(ranked_list) >= 12:
                seeds = [get_team_fn(ranked_list[i][0]) for i in range(4)]
                challengers = [get_team_fn(ranked_list[i][0]) for i in range(4, 12)]

                # Combined draw: assign 2 challengers to each seed using weights
                seed_names = ['#1', '#2', '#3', '#4']
                remaining = list(range(8))  # indices into challengers (0=rank5, ..., 7=rank12)
                seed_assignments = {0: [], 1: [], 2: [], 3: []}  # seed_idx → [challenger_idx, ...]

                battle_log('info', label + ' 淘汰赛抽签 (附加赛+八进四合并):')
                for seed_idx in range(4):
                    for _ in range(2):
                        weights = [battle_setting('KNOCKOUT_DRAW_WEIGHTS')[ri][seed_idx] for ri in remaining]
                        chosen_pos = random.choices(range(len(remaining)), weights=weights, k=1)[0]
                        ri = remaining.pop(chosen_pos)
                        seed_assignments[seed_idx].append(ri)

                # Store bracket info for saving
                bracket_draw_info = {
                    'label': label, 'seeds': [], 'playin_pairs': [],
                }
                for seed_idx in range(4):
                    ci1, ci2 = seed_assignments[seed_idx]
                    sn = self._team_display(seeds[seed_idx])
                    cn1 = self._team_display(challengers[ci1])
                    cn2 = self._team_display(challengers[ci2])
                    battle_log('info', f'  {seed_names[seed_idx]}({sn}) ← 附加赛: {cn1} vs {cn2}')
                    bracket_draw_info['seeds'].append({
                        'seed_name': seed_names[seed_idx],
                        'seed_team': '+'.join(seeds[seed_idx]),
                        'seed_display': sn,
                    })
                    bracket_draw_info['playin_pairs'].append({
                        'seed': seed_names[seed_idx],
                        'team_a': '+'.join(challengers[ci1]),
                        'team_a_display': cn1,
                        'team_b': '+'.join(challengers[ci2]),
                        'team_b_display': cn2,
                    })
                # Store for later saving
                if not hasattr(self, '_current_bracket_info'):
                    self._current_bracket_info = {}
                self._current_bracket_info[label] = bracket_draw_info

                battle_log('info', '')
                battle_log('info', f'(抽签结果展示{battle_setting("DELAY_DRAW_DISPLAY")}秒...)')
                time.sleep(battle_setting('DELAY_DRAW_DISPLAY'))

                # Run play-in: the two challengers in each seed slot fight
                new_challengers = []
                for seed_idx in range(4):
                    ci1, ci2 = seed_assignments[seed_idx]
                    ta = challengers[ci1]
                    tb = challengers[ci2]
                    winner = self._best_of_n(ta, tb, map_size, label + f' Playin S{seed_idx+1}', n=battle_setting("BO_N_PLAYIN"))
                    new_challengers.append(winner.split('+') if '+' in winner else [winner])
                # Return seeds + winners paired in order: seed0, seed1, seed2, seed3, win0, win1, win2, win3
                # This pre-paired order tells _run_knockout_bracket to skip the QF draw
                result = seeds + new_challengers
                # Mark as pre-paired
                if not hasattr(self, '_pre_paired_brackets'):
                    self._pre_paired_brackets = set()
                self._pre_paired_brackets.add(label)
                return result
            elif len(ranked_list) >= 8:
                return [get_team_fn(r[0]) for r in ranked_list[:8]]
            return [get_team_fn(r[0]) for r in ranked_list[:4]]
        # 1v1 knockout
        if '1v1' in self.enabled_modes and len(ranked_1v1) >= 4:
            top_1v1 = _build_top8_with_playin(ranked_1v1, lambda s: [s], '1v1')
            all_brackets.append(('1v1', top_1v1, battle_setting('KNOCKOUT_MAP_SIZE')))

        # 2v2 knockout
        if '2v2' in self.enabled_modes and len(ranked_2v2) >= 4:
            top_2v2 = _build_top8_with_playin(ranked_2v2, lambda i: teams_2v2[i], '2v2')
            all_brackets.append(('2v2', top_2v2, battle_setting('KNOCKOUT_MAP_SIZE')))

        # 3v3 knockout
        if '3v3' in self.enabled_modes and len(ranked_3v3) >= 4:
            top_3v3 = _build_top8_with_playin(ranked_3v3, lambda i: teams_3v3[i], '3v3')
            all_brackets.append(('3v3', top_3v3, battle_setting('KNOCKOUT_MAP_SIZE')))

        # ---- Execute knockouts with round-robin interleaving across formats ----
        if all_brackets:
            battle_log('info', '')
            battle_log('info', '=' * 60)
            battle_log('info', '淘汰赛阶段（跨赛制均匀穿插）')
            for label, teams, ms in all_brackets:
                battle_log('info', f'  {label}: {len(teams)}队')
            battle_log('info', '=' * 60)

            # Save bracket info before elimination starts
            self._save_bracket_info()

            brackets = [{'label': label, 'top_teams': top_teams, 'map_size': ms}
                         for label, top_teams, ms in all_brackets]
            self._run_knockout_bracket(brackets)

        # ---- Final summary: contribution ranking ----
        battle_log('info', '')
        battle_log('info', '=' * 60)
        battle_log('info', '团队贡献率排行榜')
        battle_log('info', '=' * 60)

        contrib = self._compute_contribution_ranking()

        algo_names = {
            'algo1': '算法一: 同格式内有/无胜率差',
            'algo2': '算法二: 跨格式有/无胜率差 (3v3有 vs 2v2无)',
            'algo3': '算法三: 个人贡献效率 (HP剩余比例平均)',
        }
        for algo_key in ['algo1', 'algo2', 'algo3']:
            battle_log('info', '')
            battle_log('info', f'  {algo_names[algo_key]}')
            battle_log('info', f'  {"排名":<4} {"角色":<8} {"名称":<6} {"贡献值":>8}')
            battle_log('info', f'  {"-"*30}')
            for rank, (serial, name, val) in enumerate(contrib[algo_key], 1):
                if val is None:
                    battle_log('info', f'  {rank:<4} {serial:<8} {name:<6} {"数据不足":>8}')
                else:
                    battle_log('info', f'  {rank:<4} {serial:<8} {name:<6} {val:>+8.4f}')

        self._snapshot_run_stats()
        if write_results_flag:
            self.write_results()
        elapsed = (datetime.now() - self.start_time).total_seconds()
        battle_log('info', f'锦标赛完成！{len(self.results)}场战斗，耗时{elapsed:.0f}秒')
        self.running = False

    @pinned
    def _compute_contribution_ranking(self):
        """Compute 3 contribution ranking algorithms from self.results.
        Returns {'algo1': [(serial, name, value), ...], 'algo2': [...], 'algo3': [...]}"""
        from collections import defaultdict

        # Parse results: each result is (a_name, b_name, winner, rounds, map_size, phase, is_bo)
        # a_name/b_name are like 'Y1+Y2+Y3'
        all_chars = [c['serial'] for c in ALL_CHARACTERS]
        name_map = {c['serial']: c['name'] for c in ALL_CHARACTERS}

        # Per-character: battles where they were in team, and battles where they weren't
        # For each format (2v2, 3v3)
        char_in_wins = defaultdict(lambda: defaultdict(int))   # char_in_wins[char][format] = wins
        char_in_total = defaultdict(lambda: defaultdict(int))  # char_in_total[char][format] = battles
        char_out_wins = defaultdict(lambda: defaultdict(int))
        char_out_total = defaultdict(lambda: defaultdict(int))

        # Algorithm 2 (cross-format): 3v3 with char vs 2v2 without char
        char_in_3v3_wins = defaultdict(int)
        char_in_3v3_total = defaultdict(int)
        char_out_2v2_wins = defaultdict(int)
        char_out_2v2_total = defaultdict(int)

        # Algorithm 3: efficiency per battle
        char_eff_sum = defaultdict(float)
        char_eff_count = defaultdict(int)

        for res in self.results:
            a_name, b_name, winner, rounds, map_size, phase_label, is_bo = res
            # Determine format from team sizes
            a_chars = a_name.split('+')
            b_chars = b_name.split('+')
            fmt = len(a_chars)  # 1, 2, or 3
            if fmt < 2: continue  # Skip 1v1

            # Which side won
            a_won = winner == a_name

            # Algorithm 3: approximate HP ratio from rounds
            # (rough: winner had more HP remaining)
            a_eff = 0.5 + (0.3 if a_won else -0.3)  # simplified efficiency
            b_eff = 0.5 + (0.3 if not a_won else -0.3)

            fmt_key = f'{fmt}v{fmt}'

            for ch in a_chars:
                if a_won:
                    char_in_wins[ch][fmt_key] += 1
                    char_in_3v3_wins[ch] += 1
                char_in_total[ch][fmt_key] += 1
                char_in_3v3_total[ch] += 1
                char_eff_sum[ch] += a_eff
                char_eff_count[ch] += 1

            for ch in b_chars:
                if not a_won:
                    char_in_wins[ch][fmt_key] += 1
                    char_in_3v3_wins[ch] += 1
                char_in_total[ch][fmt_key] += 1
                char_in_3v3_total[ch] += 1
                char_eff_sum[ch] += b_eff
                char_eff_count[ch] += 1

            # "Out" stats: other chars in same-format battles
            for ch in all_chars:
                if ch not in a_chars and ch not in b_chars:
                    char_out_total[ch][fmt_key] += 1
                    # Can't determine who "out" chars were rooting for; use 50/50 as baseline
                    char_out_wins[ch][fmt_key] += 0.5

        # Also compute 2v2-out for algorithm 2
        for res in self.results:
            a_name, b_name, winner, rounds, map_size, phase_label, is_bo = res
            a_chars = a_name.split('+')
            b_chars = b_name.split('+')
            if len(a_chars) != 2: continue
            a_won = winner == a_name
            for ch in all_chars:
                if ch not in a_chars and ch not in b_chars:
                    char_out_2v2_total[ch] += 1
                    char_out_2v2_wins[ch] += 0.5

        # ---- Algorithm 1: Same-format in/out win rate diff ----
        algo1 = []
        for ch in all_chars:
            in_w = sum(char_in_wins[ch].values())
            in_t = sum(char_in_total[ch].values())
            out_w = sum(char_out_wins[ch].values())
            out_t = sum(char_out_total[ch].values())
            if in_t < battle_setting('CONTRIBUTION_MIN_SAMPLES') or out_t < battle_setting('CONTRIBUTION_MIN_SAMPLES'):
                algo1.append((ch, name_map.get(ch, ch), None))
                continue
            in_rate = in_w / in_t
            out_rate = out_w / out_t
            diff = in_rate - out_rate
            algo1.append((ch, name_map.get(ch, ch), round(diff, 4)))

        # ---- Algorithm 2: Cross-format (3v3 with vs 2v2 without) ----
        algo2 = []
        for ch in all_chars:
            in_w = char_in_3v3_wins[ch]
            in_t = char_in_3v3_total[ch]
            out_w = char_out_2v2_wins[ch]
            out_t = char_out_2v2_total[ch]
            if in_t < battle_setting('CONTRIBUTION_MIN_SAMPLES') or out_t < battle_setting('CONTRIBUTION_MIN_SAMPLES'):
                algo2.append((ch, name_map.get(ch, ch), None))
                continue
            diff = in_w / in_t - out_w / out_t
            algo2.append((ch, name_map.get(ch, ch), round(diff, 4)))

        # ---- Algorithm 3: Efficiency average ----
        algo3 = []
        for ch in all_chars:
            if char_eff_count[ch] < battle_setting('CONTRIBUTION_MIN_SAMPLES'):
                algo3.append((ch, name_map.get(ch, ch), None))
            else:
                avg = char_eff_sum[ch] / char_eff_count[ch]
                algo3.append((ch, name_map.get(ch, ch), round(avg, 4)))

        # Sort each by value descending
        algo1.sort(key=lambda x: (x[2] is not None, x[2] or 0), reverse=True)
        algo2.sort(key=lambda x: (x[2] is not None, x[2] or 0), reverse=True)
        algo3.sort(key=lambda x: (x[2] is not None, x[2] or 0), reverse=True)

        return {'algo1': algo1, 'algo2': algo2, 'algo3': algo3}

    @pinned
    def _save_bracket_info(self):
        """Save tournament bracket and rankings to BRACKET_FILE before elimination starts.
        Appends for multiple runs (--time=N)."""
        lines = []
        lines.append('=' * 70)
        lines.append(f'  淘汰赛赛图与排名 — 第 {self.run_index}/{self.run_times} 次运行')
        lines.append(f'  时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
        lines.append('=' * 70)
        lines.append('')

        # Rankings
        rank_labels = {'1v1': '1v1 个人积分排名', '2v2': '2v2 队伍积分排名', '3v3': '3v3 队伍积分排名'}
        for fmt, label in rank_labels.items():
            info = self._rankings_info.get(fmt, [])
            if info:
                lines.append(f'--- {label} ---')
                for entry in info:
                    rank, key, pts, wins, battles, display = entry
                    lines.append(f'  {rank:2d}. {display}  {pts:.1f}分  {wins}胜/{battles}场')
                lines.append('')

        # Visual bracket diagram
        bracket_info = getattr(self, '_current_bracket_info', {})
        if bracket_info:
            lines.append('=' * 70)
            lines.append('  淘汰赛对阵图（附加赛+八进四合并抽签）')
            lines.append('=' * 70)
            lines.append('')
            for label, info in bracket_info.items():
                lines.append(f'  [{label}]')
                lines.append('')
                seeds = info['seeds']
                ppairs = info['playin_pairs']
                # Build the bracket tree for this format
                self._render_bracket_tree(lines, seeds, ppairs)
                lines.append('')
        lines.append('')

        # Write/append to file
        mode = 'a' if self.run_index > 1 else 'w'
        with open(file_path('BRACKET_FILE'), mode, encoding='utf-8') as f:
            f.write('\n'.join(lines))
        battle_log('info', f'赛图与排名已保存至 {file_path("BRACKET_FILE")} (第{self.run_index}次)')

    @pinned
    def _render_bracket_tree(self, lines, seeds, playin_pairs):
        """Render a visual bracket tree for one format.
        seeds: list of {seed_name, seed_display}
        playin_pairs: list of {seed, team_a_display, team_b_display}"""
        def _trunc(s, maxlen=None):
            maxlen = battle_setting('RESULT_NAME_WIDTH') if maxlen is None else maxlen
            return s if len(s) <= maxlen else s[:maxlen-1] + '…'

        s = [_trunc(seeds[i]['seed_display']) for i in range(4)]
        pa = [_trunc(playin_pairs[i]['team_a_display']) for i in range(4)]
        pb = [_trunc(playin_pairs[i]['team_b_display']) for i in range(4)]

        lines.append('    ============ 淘汰赛对阵图 ============')
        lines.append('')
        lines.append('    ---- 四分之一决赛 ----')
        lines.append(f'    QF1:  #1 {s[0]}  vs  附加赛①胜者')
        lines.append(f'    QF2:  #2 {s[1]}  vs  附加赛②胜者')
        lines.append(f'    QF3:  #3 {s[2]}  vs  附加赛③胜者')
        lines.append(f'    QF4:  #4 {s[3]}  vs  附加赛④胜者')
        lines.append('')
        lines.append('    ---- 半决赛 ----')
        lines.append(f'    SF1:  QF1胜者  vs  QF4胜者')
        lines.append(f'    SF2:  QF2胜者  vs  QF3胜者')
        lines.append('')
        lines.append('    ---- 决赛 / 季军赛 ----')
        lines.append(f'    冠军战:  SF1胜者  vs  SF2胜者')
        lines.append(f'    季军战:  SF1败者  vs  SF2败者')
        lines.append('')
        lines.append('    ---- 附加赛对阵（胜者晋级对应QF）----')
        lines.append(f'    附加赛①:  {pa[0]}  vs  {pb[0]}   → QF1 vs #1 {s[0]}')
        lines.append(f'    附加赛②:  {pa[1]}  vs  {pb[1]}   → QF2 vs #2 {s[1]}')
        lines.append(f'    附加赛③:  {pa[2]}  vs  {pb[2]}   → QF3 vs #3 {s[2]}')
        lines.append(f'    附加赛④:  {pa[3]}  vs  {pb[3]}   → QF4 vs #4 {s[3]}')

    @pinned
    def _snapshot_run_stats(self):
        """Snapshot win/battle counts for the current run (results since last snapshot)."""
        start = getattr(self, '_results_start_idx', 0)
        run_results = self.results[start:]
        win_counts = defaultdict(int)
        battle_counts = defaultdict(int)
        for a, b, winner, rounds, map_sz, phase, is_bo7 in run_results:
            for c in a.split('+'): battle_counts[c] += 1
            if winner == a:
                for c in a.split('+'): win_counts[c] += 1
            for c in b.split('+'): battle_counts[c] += 1
            if winner == b:
                for c in b.split('+'): win_counts[c] += 1
        self._run_stats.append((dict(win_counts), dict(battle_counts)))
        self._results_start_idx = len(self.results)

    @pinned
    def _compute_average_stats(self):
        """Compute per-character average wins/battles/rate across all runs.
        Returns list of (serial, name, avg_wins, avg_battles, avg_rate) sorted by avg_wins desc."""
        if not self._run_stats:
            return []
        name_map = {c['serial']: c['name'] for c in ALL_CHARACTERS}
        n_runs = len(self._run_stats)
        all_chars = set()
        for wc, bc in self._run_stats:
            all_chars.update(wc.keys())
            all_chars.update(bc.keys())
        result = []
        for ch in sorted(all_chars):
            total_w = sum(stats[0].get(ch, 0) for stats in self._run_stats)
            total_b = sum(stats[1].get(ch, 0) for stats in self._run_stats)
            avg_w = total_w / n_runs
            avg_b = total_b / n_runs
            avg_rate = (total_w / total_b * 100) if total_b > 0 else 0
            result.append((ch, name_map.get(ch, ch), avg_w, avg_b, avg_rate))
        result.sort(key=lambda x: -x[2])  # Sort by avg_wins desc
        return result

    @pinned
    def _append_average_stats_to_bracket(self):
        """Append average stats across all runs to BRACKET_FILE."""
        if self.run_times <= 1:
            return
        avg_stats = self._compute_average_stats()
        if not avg_stats:
            return
        lines = []
        lines.append('')
        lines.append('=' * 70)
        lines.append(f'  全部 {self.run_times} 次运行 — 平均胜场数及排名')
        lines.append('=' * 70)
        lines.append(f'  {"排名":<4} {"角色":<8} {"名称":<6} {"平均胜场":>8} {"平均场次":>8} {"胜率":>8}')
        lines.append(f'  {"-"*50}')
        for rank, (serial, name, avg_w, avg_b, avg_rate) in enumerate(avg_stats, 1):
            lines.append(f'  {rank:<4} {serial:<8} {name:<6} {avg_w:>8.1f} {avg_b:>8.1f} {avg_rate:>7.1f}%')
        lines.append('')
        with open(file_path('BRACKET_FILE'), 'a', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        battle_log('info', f'平均统计已追加至 {file_path("BRACKET_FILE")}')

    @pinned
    def write_results(self):
        lines = ['=' * 70,
                 '  AI战斗模拟 锦标赛结果',
                 f"  全阶段: 循环预赛 + Bo{battle_setting('BO_N_FINAL')}决赛 + 铜牌赛",
                 '=' * 70,
                 f'总运行次数: {self.run_times}',
                 f'总战斗数: {len(self.results)}', '']

        # ---- Section 1: Rankings (prelim standings) ----
        rank_labels = {'1v1': '1v1 个人积分排名', '2v2': '2v2 队伍积分排名', '3v3': '3v3 队伍积分排名'}
        rankings_info = getattr(self, '_rankings_info', {})
        if rankings_info:
            lines.append('=' * 70)
            lines.append('  预赛积分排名')
            lines.append('=' * 70)
            for fmt, label in rank_labels.items():
                info = rankings_info.get(fmt, [])
                if info:
                    lines.append(f'\n--- {label} ---')
                    for entry in info:
                        rank, key, pts, wins, battles, display = entry
                        lines.append(f'  {rank:2d}. {display}  {pts:.1f}分  {wins}胜/{battles}场')
            lines.append('')

        # ---- Section 2: Bracket draw info ----
        bracket_info = getattr(self, '_current_bracket_info', {})
        if bracket_info:
            lines.append('=' * 70)
            lines.append('  淘汰赛赛图（附加赛+八进四合并抽签）')
            lines.append('=' * 70)
            for label, info in bracket_info.items():
                lines.append(f'\n[{label}]')
                for i, seed in enumerate(info['seeds']):
                    pp = info['playin_pairs'][i]
                    lines.append(f'  {seed["seed_name"]}({seed["seed_display"]})')
                    lines.append(f'    ← 附加赛: {pp["team_a_display"]} vs {pp["team_b_display"]}')
            lines.append('')

        # ---- Section 3: Character win rates ----
        lines.append('=' * 70)
        lines.append('  角色胜率')
        lines.append('=' * 70)
        win_counts = defaultdict(int)
        battle_counts = defaultdict(int)
        for a, b, winner, rounds, map_sz, phase, is_bo7 in self.results:
            for c in a.split('+'): battle_counts[c] += 1;
            if winner == a:
                for c in a.split('+'): win_counts[c] += 1
            for c in b.split('+'): battle_counts[c] += 1
            if winner == b:
                for c in b.split('+'): win_counts[c] += 1
        for c in sorted(win_counts.keys()):
            w, btl = win_counts[c], battle_counts[c]
            rate = f'{w/btl*100:.1f}%' if btl else 'N/A'
            lines.append(f'  {c}: {w}/{btl} ({rate})')

        # ---- Section 4: Battle details ----
        lines.append('')
        lines.append('=' * 70)
        lines.append('  战斗详情')
        lines.append('=' * 70)
        name_map = {c['serial']: c['name'] for c in ALL_CHARACTERS}
        for a, b, winner, rounds, map_sz, phase, is_bo7 in self.results:
            bo7_tag = ' [系列赛]' if is_bo7 else ''
            a_disp = '+'.join(name_map.get(s, s) for s in a.split('+'))
            b_disp = '+'.join(name_map.get(s, s) for s in b.split('+'))
            w_disp = '+'.join(name_map.get(s, s) for s in winner.split('+'))
            lines.append(f'  [{map_sz}]{bo7_tag} {a_disp} vs {b_disp} → {w_disp} ({rounds}回合)')

        # ---- Section 5: Contribution ranking ----
        contrib = self._compute_contribution_ranking()
        lines.append('')
        lines.append('=' * 70)
        lines.append('  团队贡献率排行榜')
        lines.append('=' * 70)
        algo_names = {
            'algo1': '算法一: 同格式内有/无胜率差',
            'algo2': '算法二: 跨格式有/无胜率差 (3v3有 vs 2v2无)',
            'algo3': '算法三: 个人贡献效率 (HP剩余比例平均)',
        }
        for algo_key in ['algo1', 'algo2', 'algo3']:
            lines.append(f'\n  {algo_names[algo_key]}')
            lines.append(f'  {"排名":<4} {"角色":<8} {"名称":<6} {"贡献值":>8}')
            lines.append(f'  {"-"*30}')
            for rank, (serial, name, val) in enumerate(contrib[algo_key], 1):
                if val is None:
                    lines.append(f'  {rank:<4} {serial:<8} {name:<6} {"数据不足":>8}')
                else:
                    lines.append(f'  {rank:<4} {serial:<8} {name:<6} {val:>+8.4f}')

        # ---- Section 6: Average stats across runs (only when --time > 1) ----
        if self.run_times > 1:
            avg_stats = self._compute_average_stats()
            if avg_stats:
                lines.append('')
                lines.append('=' * 70)
                lines.append(f'  全部 {self.run_times} 次运行 — 平均胜场数及排名')
                lines.append('=' * 70)
                lines.append(f'  {"排名":<4} {"角色":<8} {"名称":<6} {"平均胜场":>8} {"平均场次":>8} {"胜率":>8}')
                lines.append(f'  {"-"*50}')
                for rank, (serial, name, avg_w, avg_b, avg_rate) in enumerate(avg_stats, 1):
                    lines.append(f'  {rank:<4} {serial:<8} {name:<6} {avg_w:>8.1f} {avg_b:>8.1f} {avg_rate:>7.1f}%')

        with open(file_path('RESULT_FILE'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        battle_log('info', f'结果已写入 {file_path("RESULT_FILE")}')


# ============================================================
#  HTTP Server
# ============================================================

tournament = Tournament()
server_thread = None

class BattleHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress default HTTP logging

    def _send(self, code, body, content_type='text/plain; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body.encode('utf-8'))

    def do_GET(self):
        if self.path == '/':
            with tournament._state_lock:
                status = 'running' if tournament.running else 'idle'
                total = len(tournament.results)
                running_flag = tournament.running
                char_count = len(tournament.char_map)
            q_status = '已加载' if (AIController.Q_TABLE and any(len(v)>0 for v in AIController.Q_TABLE.values())) else '未训练(规则AI)'
            body = f"""AI战斗模拟服务器 — {status}
================================
角色数: {char_count}
已完成战斗: {total}
AI模式: {q_status}
运行中: {running_flag}

端点:
  GET  /       — 状态
  POST /start  — 开始锦标赛
  GET  /log    — 实时日志 ({len(log_buffer)}条)
  GET  /log/stream — 实时日志流(SSE)
  GET  /results — 比赛结果
  GET  /game/Y1_vs_Y2&num=3 — 自定义比赛

{'锦标赛已在进行中！' if running_flag else '发送 POST /start 开始战斗'}
"""
            self._send(200, body)

        elif self.path == '/log':
            with log_lock:
                body = '\n'.join(log_buffer[-100:])
            self._send(200, body or '(暂无日志)')

        elif self.path == '/results':
            if os.path.exists(file_path('RESULT_FILE')):
                with open(file_path('RESULT_FILE'), 'r', encoding='utf-8') as f:
                    body = f.read()
                self._send(200, body)
            else:
                self._send(200, '(暂无结果)')

        elif self.path == '/log/stream':
            # SSE (Server-Sent Events) — real-time log streaming
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            last_idx = 0
            while tournament.running or last_idx < len(log_buffer):
                with log_lock:
                    new_entries = log_buffer[last_idx:]
                    last_idx = len(log_buffer)
                for entry in new_entries:
                    self.wfile.write(f'data: {entry}\n\n'.encode('utf-8'))
                    self.wfile.flush()
                if not tournament.running and last_idx >= len(log_buffer):
                    self.wfile.write(b'data: [DONE]\n\n')
                    self.wfile.flush()
                    break
                time.sleep(battle_setting('LOG_STREAM_INTERVAL'))

        elif self.path == '/log/stream/simple':
            # SSE stream matching terminal output (CONSOLE_LOG_LEVEL only)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            last_idx = 0
            while tournament.running or last_idx < len(simple_log_buffer):
                with simple_log_lock:
                    new_entries = simple_log_buffer[last_idx:]
                    last_idx = len(simple_log_buffer)
                for entry in new_entries:
                    self.wfile.write(f'data: {entry}\n\n'.encode('utf-8'))
                    self.wfile.flush()
                if not tournament.running and last_idx >= len(simple_log_buffer):
                    self.wfile.write(b'data: [DONE]\n\n')
                    self.wfile.flush()
                    break
                time.sleep(battle_setting('LOG_STREAM_INTERVAL'))

        elif self.path.startswith('/game/'):
            self._handle_custom_game()

        else:
            self._send(404, 'Not Found')

    def _handle_custom_game(self):
        """Parse /game/Y10&Y2_vs_Y8&Y12&num=7&map=10x10 and run custom match."""
        import re
        path = self.path[6:]  # Remove '/game/'
        parts = path.split('_vs_')
        if len(parts) != 2:
            self._send(400, '格式: /game/Y1&Y2_vs_Y3&Y4&num=7&map=10x10')
            return
        team_a_raw = parts[0]
        team_b_raw = parts[1]
        team_a = team_a_raw.split('&')
        # Parse team B and options
        team_b = []
        num = battle_setting('CUSTOM_DEFAULT_GAMES')
        map_size = battle_setting('DEFAULT_MAP_SIZE')
        for token in team_b_raw.split('&'):
            if token.startswith('num='): num = int(token[4:])
            elif token.startswith('map='): map_size = token[4:]
            else: team_b.append(token)
        if not team_a or not team_b:
            self._send(400, '至少每方1人')
            return

        # Validate serials
        valid = set(tournament.char_map.keys())
        for s in team_a + team_b:
            if s not in valid:
                self._send(400, f'无效角色: {s}. 可用: {sorted(valid)}')
                return

        num = max(1, min(num, battle_setting('CUSTOM_MAX_GAMES')))

        def run_custom():
            set_terminal_quiet(True)
            try:
                # Look up display names for logging
                nm = {c['serial']: c['name'] for c in ALL_CHARACTERS}
                a_disp = '+'.join(nm.get(s, s) for s in team_a)
                b_disp = '+'.join(nm.get(s, s) for s in team_b)

                def _setup_engine(engine):
                    """Load characters, set teams, inject reaction weights.
                    Returns (a_uids, b_uids) resolved with clones for mirror matches."""
                    for c in ALL_CHARACTERS:
                        uid = tournament.char_map[c['serial']]
                        load_character_to_engine(engine, c, uid)
                        if not c.get('pre_transformed'): engine.process_command(uid, '.hs')
                    # Resolve teams — clone characters that appear on both sides
                    a_uids, b_uids = tournament._resolve_clones(team_a, team_b, engine)
                    for uid in a_uids: tournament.ai_map[uid].team = 'Y'
                    for uid in b_uids: tournament.ai_map[uid].team = 'X'
                    engine._ai_react_dodge_w = {}; engine._ai_react_counter_w = {}
                    for uid in a_uids + b_uids:
                        ai = tournament.ai_map.get(uid)
                        if ai:
                            engine._ai_react_dodge_w[uid] = ai.react_dodge_w
                            engine._ai_react_counter_w[uid] = ai.react_counter_w
                            engine._ai_react_block_w[uid] = getattr(ai, 'react_block_w', 0)
                    # Build per-uid season status mapping from per-character dict
                    uid_season = {}
                    for serial, uid in zip(team_a + team_b, a_uids + b_uids):
                        uid_season[uid] = tournament.char_season_status.get(serial, battle_setting('SEASON_FALLBACK_STATUS'))
                    engine.setup_battle(a_uids, b_uids, map_size, season_status=uid_season)
                    return a_uids, b_uids

                # Phase 0: Silent preview — estimate win rates
                if battle_setting('CUSTOM_PREVIEW_BATTLES') > 0:
                    battle_log('info', f'[Custom Preview] 运行 {battle_setting("CUSTOM_PREVIEW_BATTLES")} 场预览...')
                    p_a = p_b = 0
                    for _ in range(battle_setting('CUSTOM_PREVIEW_BATTLES')):
                        eng = BattleEngine(display_level=DisplayLevel.WINLOSS, delay=0, quiet=True)
                        _setup_engine(eng)
                        r = eng.run_battle(tournament.ai_map)
                        if r['winner'] == 'Y': p_a += 1
                        else: p_b += 1
                    rate_a = p_a / battle_setting('CUSTOM_PREVIEW_BATTLES') * 100
                    rate_b = p_b / battle_setting('CUSTOM_PREVIEW_BATTLES') * 100
                    battle_log('info', f'  [Preview] {a_disp}: {rate_a:.1f}% ({p_a}胜) | {b_disp}: {rate_b:.1f}% ({p_b}胜)')
                    a_name = '+'.join(team_a); b_name = '+'.join(team_b)
                    fav = a_name if p_a >= p_b else b_name
                    battle_log('info', f'  [Preview] 优势方: {fav}')

                # Phase 1: Actual match
                a_wins = b_wins = 0
                battle_log('info', f'[Custom] {"/".join(team_a)} vs {"/".join(team_b)} Bo{num} [{map_size}]')
                for g in range(1, num + 1):
                    engine = BattleEngine(display_level=battle_setting('DISPLAY_CUSTOM'), delay=battle_setting('DELAY_CUSTOM_TURN'), quiet=True)
                    _setup_engine(engine)
                    result = engine.run_battle(tournament.ai_map)
                    w = '+'.join(team_a) if result['winner'] == 'Y' else '+'.join(team_b)
                    if result['winner'] == 'Y': a_wins += 1
                    else: b_wins += 1
                    battle_log('info', f'  G{g}: {w} ({a_wins}-{b_wins}) [{result["rounds"]}rds]')
                    if a_wins > num//2 or b_wins > num//2: break
                final = '+'.join(team_a) if a_wins >= b_wins else '+'.join(team_b)
                battle_log('info', f'  [Custom Result] {final} {a_wins}-{b_wins}')
            finally:
                set_terminal_quiet(False)

        threading.Thread(target=run_custom, daemon=True).start()

        # Look up names for display
        name_map = {c['serial']: c['name'] for c in ALL_CHARACTERS}
        a_names = [name_map.get(s, s) for s in team_a]
        b_names = [name_map.get(s, s) for s in team_b]
        a_disp = '+'.join(a_names)
        b_disp = '+'.join(b_names)
        title = f'{a_disp} v.s. {b_disp} — Bo{num} [{map_size}]'

        html = f'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#1a1a2e;color:#e0e0e0;font-family:Consolas,monospace;height:100vh;display:flex;flex-direction:column}}
.header{{background:#16213e;padding:8px 16px;border-bottom:1px solid #0f3460}}
.header h1{{font-size:16px;color:#e94560;margin:0}}
.header .info{{font-size:12px;color:#888;margin-top:2px}}
#log{{flex:1;overflow-y:auto;padding:8px 16px;font-size:13px;line-height:1.5;white-space:pre-wrap;word-break:break-all}}
#log .time{{color:#555}}
#log .info{{color:#4ecca3}}
#log .debug{{color:#888}}
#log .error{{color:#e94560}}
.status{{background:#0f3460;padding:4px 16px;font-size:11px;color:#888;display:flex;justify-content:space-between}}
.status .dot{{color:#4ecca3}}
</style>
</head>
<body>
<div class="header"><h1>{title}</h1><div class="info">Y队：{a_disp} v.s. X队：{b_disp} | Bo{num} | {map_size}</div></div>
<div id="log"><div class="info">等待比赛开始...</div></div>
<div class="status"><span>实时日志</span><span class="dot" id="status">● 监听中</span></div>
<script>
const log=document.getElementById('log');
const status=document.getElementById('status');
const evt=new EventSource('/log/stream');
evt.onmessage=function(e){{
  if(e.data==='[DONE]'){{status.textContent='✓ 完成';status.style.color='#4ecca3';return}}
  if(e.data.startsWith('[KEEPALIVE]'))return;
  const cls=e.data.includes('[INFO]')?'info':e.data.includes('[ERROR]')?'error':'debug';
  const div=document.createElement('div');
  div.className=cls;div.textContent=e.data;
  log.appendChild(div);
  log.scrollTop=log.scrollHeight;
  // Trim old lines to keep DOM light
  while(log.children.length>{battle_setting('WEB_LOG_LINE_LIMIT')})log.removeChild(log.firstChild);
}};
evt.onerror=function(){{status.textContent='⚠ 重连中...';status.style.color='#e94560'}};
</script>
</body>
</html>'''
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(html.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def do_POST(self):
        if self.path == '/start':
            if tournament.running:
                self._send(200, '锦标赛已在运行中！')
                return

            if not tournament.char_map:
                tournament.init_characters()

            def run():
                try:
                    tournament.run_index += 1
                    tournament._current_bracket_info = {}
                    tournament._pre_paired_brackets = set()
                    tournament._rankings_info = {}
                    tournament.run_phases(write_results_flag=True)
                except Exception as e:
                    battle_log('error', f'错误: {e}')
                    import traceback
                    battle_log('error', traceback.format_exc())

            t = threading.Thread(target=run, daemon=True)
            t.start()
            self._send(200, '锦标赛已启动！访问 GET / 查看状态，GET /log 查看日志')

        else:
            self._send(404, 'Not Found')


def start_server(port=None):
    port = battle_setting('AI_SERVER_PORT') if port is None else port
    global server_thread
    server = ThreadingHTTPServer((battle_setting('AI_SERVER_HOST'), port), BattleHandler)
    battle_log('info', f'AI战斗服务器启动: http://{battle_setting("AI_SERVER_HOST")}:{port}')
    battle_log('info', f'本机: http://localhost:{port}')
    battle_log('info', f'实时日志流: http://<IP>:{port}/log/stream (SSE)')
    battle_log('info', f'POST /start 开始锦标赛')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        battle_log('info', '服务器关闭')
        server.shutdown()


if __name__ == '__main__':
    port, tournament_type, run_times, enabled_modes = parse_cli_args(sys.argv[1:])
    tournament = Tournament(tournament_type, run_times, enabled_modes=enabled_modes)
    tournament.init_characters()
    battle_log('info', f'启用赛制: {" + ".join(enabled_modes)}')

    # Auto-start tournament in background thread
    def auto_start():
        time.sleep(battle_setting('AUTO_START_DELAY'))
        for run_idx in range(1, run_times + 1):
            tournament.run_index = run_idx
            battle_log('info', '')
            battle_log('info', '=' * 60)
            battle_log('info', f'第 {run_idx}/{run_times} 次锦标赛运行')
            battle_log('info', '=' * 60)
            # Re-initialize characters for fresh state each run
            if run_idx > 1:
                tournament.init_characters()
            # Reset per-run bracket tracking
            tournament._current_bracket_info = {}
            tournament._pre_paired_brackets = set()
            tournament._rankings_info = {}
            # Only write results on the final run
            is_last = (run_idx == run_times)
            tournament.run_phases(write_results_flag=is_last)
        # After all runs, append average stats to bracket_info.txt
        if run_times > 1:
            tournament._append_average_stats_to_bracket()
        battle_log('info', '')
        battle_log('info', f'全部 {run_times} 次运行完成！')

    threading.Thread(target=auto_start, daemon=True).start()

    start_server(port)
