"""Shared, validated settings and context-local snapshots for concurrent battles."""
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import sys
import threading

from combat_defaults import DEFAULTS

ALIASES = {'CUSTOM_BATTLE_DELAY': 'DELAY_CUSTOM_TURN'}
READONLY = {'AI_SERVER_PORT', 'AI_SERVER_HOST', 'LOG_FILE', 'RESULT_FILE', 'BRACKET_FILE', 'RANDOM_BONUS_1V1'}
LIVE = {'TOURNAMENT_EVENT_DELAY', 'ROOM_AI_DELAY', 'LOG_STREAM_INTERVAL', 'TOURNAMENT_PAUSE_POLL',
        'LOG_BUFFER_LIMIT', 'WEB_LOG_LINE_LIMIT', 'CONSOLE_LOG_LEVEL', 'FILE_LOG_LEVEL', 'WEB_LOG_LEVEL'}
_snapshot = ContextVar('combat_settings_snapshot', default=None)
_default_store = None

LABELS = {
    'DELAY_PRELIM_TURN': '预赛行动间隔（秒）', 'DELAY_KNOCKOUT_TURN': '淘汰赛行动间隔（秒）',
    'DELAY_CUSTOM_TURN': '自定义战斗行动间隔（秒）', 'DELAY_PRELIM_BETWEEN_BATTLES': '预赛场间等待（秒）',
    'DELAY_KNOCKOUT_BETWEEN_GAMES': '系列赛局间等待（秒）', 'DELAY_DRAW_DISPLAY': '抽签展示时间（秒）',
    'DELAY_STANDINGS_DISPLAY': '排名展示时间（秒）', 'DISPLAY_PRELIM': '预赛显示等级',
    'DISPLAY_KNOCKOUT': '淘汰赛显示等级', 'DISPLAY_CUSTOM': '自定义战斗显示等级',
    'CONSOLE_LOG_LEVEL': '终端日志等级', 'FILE_LOG_LEVEL': '文件日志等级', 'WEB_LOG_LEVEL': 'Web 日志等级',
    'CUSTOM_PREVIEW_BATTLES': '自定义战斗预览场数', 'RR_ROUNDS_1V1': '1v1 循环轮数上限',
    'RR_ROUNDS_2V2': '2v2 循环轮数', 'RR_ROUNDS_3V3': '3v3 循环轮数', 'TARGET_1V1_MATCHES': '1v1 每角色目标场数',
    'RANDOM_BONUS_1V1': '1v1 随机加赛（已停用）', 'RANDOM_BONUS_2V2': '2v2 额外场数',
    'RANDOM_BONUS_3V3': '3v3 额外场数', 'MAP_SIZES_2V2': '2v2 循环赛地图', 'MAP_SIZES_3V3': '3v3 循环赛地图',
    'PRELIM_WORKERS': '预赛并发数', 'SEASON_AVG_STATUS': '赛季平均状态', 'MAX_ROUNDS_PRELIM': '预赛默认回合上限',
    'MAX_ROUNDS_KNOCKOUT': '淘汰赛回合上限', 'MAX_ROUNDS_PRELIM_BY_FORMAT': '分赛制预赛回合上限',
    'KNOCKOUT_DRAW_WEIGHTS': '淘汰赛抽签权重', 'BO_N_PLAYIN': '附加赛 BoN', 'BO_N_QF': '四分之一决赛 BoN',
    'BO_N_SF': '半决赛 BoN', 'BO_N_BRONZE': '季军赛 BoN', 'BO_N_FINAL': '决赛 BoN',
    'TEAM_TABLES_2V2': '2v2 预设队伍', 'TEAM_TABLES_3V3': '3v3 预设队伍',
    'TOURNAMENT_EVENT_DELAY': '锦标赛逐回合基础等待（秒）', 'DEFAULT_MAP_SIZE': '默认对战地图',
    'KNOCKOUT_MAP_SIZE': '淘汰赛地图', 'BONUS_MAP_SIZE': '加赛地图', 'DEFAULT_BATTLE_DELAY': '通用战斗行动间隔（秒）',
    'DEFAULT_DISPLAY_LEVEL': '通用显示等级', 'DEFAULT_TOURNAMENT_TYPE': '默认赛事类型', 'DEFAULT_RUN_TIMES': '默认赛事重复次数',
    'DEFAULT_ENABLED_MODES': '默认启用赛制', 'AI_SERVER_PORT': 'AI 服务默认端口', 'AI_SERVER_HOST': 'AI 服务监听地址',
    'LOG_FILE': 'AI 日志文件', 'RESULT_FILE': '赛果文件', 'BRACKET_FILE': '签表文件', 'LOG_BUFFER_LIMIT': '服务端日志保留条数',
    'WEB_LOG_LINE_LIMIT': '日志页面保留条数', 'LOG_STREAM_INTERVAL': '日志流轮询间隔（秒）', 'AUTO_START_DELAY': '自动启动等待（秒）',
    'CUSTOM_DEFAULT_GAMES': '自定义战斗默认场数', 'CUSTOM_MAX_GAMES': '自定义战斗场数上限',
    'AI_TEMPERATURE': 'AI 采样温度', 'AI_STATE_NOISE': 'AI 状态扰动系数', 'AI_DEFAULT_STATUS': '缺失角色状态的默认值',
    'SEASON_FALLBACK_STATUS': '缺失赛季状态的默认值', 'AI_DODGE_WEIGHT': '初始闪避权重', 'AI_COUNTER_WEIGHT': '初始反击权重',
    'AI_REACTION_REWARD': '胜利反应权重增量', 'AI_REACTION_PENALTY': '失败反应权重减量', 'AI_REACTION_MIN_WEIGHT': '反应权重下限',
    'AI_SUPPORT_HP_THRESHOLD': '支援优先生命比例', 'AI_SUPPORT_MP_THRESHOLD': '支援优先魔力比例',
    'AI_HEAL_HP_THRESHOLD': '治疗优先生命比例', 'AI_ITEM_HP_THRESHOLD': '使用物品生命比例', 'AI_ITEM_MP_THRESHOLD': '使用物品魔力比例',
    'AI_MAX_FAILURES': 'AI 连续失败上限', 'BATTLE_MAX_STEPS': '战斗执行步数上限', 'PRELIM_PROGRESS_INTERVAL': '预赛进度输出间隔（场）',
    'RESULT_NAME_WIDTH': '结果表名称显示宽度', 'TOURNAMENT_PAUSE_POLL': '锦标赛暂停检查间隔（秒）',
    'SIM_SINGLE_COUNT': '批量模拟默认场数', 'SIM_TABLE_COUNT': '胜率矩阵每组默认场数', 'SIM_PRELIM_COUNT': '多轮预赛默认轮数',
    'SIM_DEFAULT_MODE': '模拟默认模式', 'SIM_DEFAULT_FORMAT': '模拟默认赛制', 'ROOM_DEFAULT_SPEED': '默认播放倍率',
    'ROOM_AI_DELAY': '房间 AI 基础行动间隔（秒）', 'ROOM_HISTORY_LIMIT': '房间事件保留条数', 'ROOM_IDLE_HOURS': '闲置房间保留时间（小时）',
    'DEFAULT_RESTIM_MODE': '默认检定公布时机', 'ALLOW_FAILED_REACTION': '默认允许失败攻击触发反应',
    'AUTO_REACTION_CHOICE': '默认自动反应方式', 'AUTO_REACTION_COUNT': '默认自动反应次数',
    'AI_SUMMON_STATUS_MIN': '召唤物随机状态下限', 'AI_SUMMON_STATUS_MAX': '召唤物随机状态上限',
    'AI_HP_FALLBACK': '规则 AI 缺失生命时的回退值', 'CONTRIBUTION_MIN_SAMPLES': '贡献统计最小样本数',
}


def group(key):
    if key.startswith('TEAM_TABLES'): return '队伍表'
    if key in READONLY: return '启动与兼容信息'
    if key.startswith('AI_') or key == 'BATTLE_MAX_STEPS': return 'AI 行为'
    if 'LOG' in key or key.startswith('DISPLAY') or key in ('RESULT_NAME_WIDTH', 'DEFAULT_DISPLAY_LEVEL'): return '显示与日志'
    if key.startswith('SIM_'): return '模拟默认值'
    if key.startswith(('BO_N', 'KNOCKOUT', 'MAX_ROUNDS')): return '淘汰赛与回合限制'
    if key.startswith(('RR_', 'TARGET_', 'RANDOM_', 'MAP_SIZES', 'PRELIM_', 'SEASON_')): return '预赛与赛季'
    if key.startswith(('DELAY_', 'TOURNAMENT_')) or key in ('AUTO_START_DELAY', 'DEFAULT_BATTLE_DELAY'): return '播放与等待'
    return '对战默认值'


def metadata():
    choices = {'SIM_DEFAULT_MODE': ['single', 'verbose', 'table', 'prelim'], 'SIM_DEFAULT_FORMAT': ['1v1', '2v2', '3v3'],
               'AUTO_REACTION_CHOICE': ['dodge', 'counter', 'block'], 'ROOM_DEFAULT_SPEED': [1, 2, 4],
               'DEFAULT_RESTIM_MODE': [0, 1], 'DEFAULT_TOURNAMENT_TYPE': [0, 1]}
    result = {}
    for key, default in DEFAULTS.items():
        options = choices.get(key)
        if 'DISPLAY' in key and key not in ('DELAY_DRAW_DISPLAY', 'DELAY_STANDINGS_DISPLAY'):
            options = [0, 1, 2]
        if key.endswith('LOG_LEVEL'): options = [10, 20, 30, 40, 50]
        result[key] = dict(label=LABELS[key], group=group(key), default=copy.deepcopy(default),
                           readonly=key in READONLY, apply='live' if key in LIVE else 'startup' if key in READONLY else 'new_task',
                           choices=options, kind='teams' if key.startswith('TEAM_TABLES') else 'matrix' if key == 'KNOCKOUT_DRAW_WEIGHTS'
                           else 'season' if key == 'SEASON_AVG_STATUS' else 'json' if isinstance(default, (list, dict)) else
                           'boolean' if type(default) is bool else 'number' if isinstance(default, (int, float)) else 'text')
    return result


def validate(values, serials=None):
    if not isinstance(values, dict) or set(values) != set(DEFAULTS):
        raise ValueError('配置字段不完整或包含未知字段')
    specs = metadata()
    def number(v): return type(v) in (int, float) and math.isfinite(v)
    def map_size(v): return isinstance(v, str) and re.fullmatch(r'(?:[8-9]|1[0-6])x(?:[8-9]|1[0-6])', v)
    for key, v in values.items():
        d, spec = DEFAULTS[key], specs[key]
        error = spec['label'] + '：值无效'
        if spec['choices'] is not None and (type(v) is not type(spec['choices'][0]) or v not in spec['choices']): raise ValueError(error)
        if key.startswith('TEAM_TABLES'):
            if not isinstance(v, list) or len(v) > 256: raise ValueError(error)
            seen = set()
            for team in v:
                if not isinstance(team, list) or not 1 <= len(team) <= 6 or any(not isinstance(s, str) for s in team) or len(team) != len(set(team)):
                    raise ValueError(error + '，每队需 1–6 名不同角色')
                if serials is not None and any(s not in serials for s in team): raise ValueError(error + '，角色不存在')
                identity = tuple(sorted(team))
                if identity in seen: raise ValueError(error + '，存在重复队伍')
                seen.add(identity)
        elif key == 'SEASON_AVG_STATUS':
            candidates = v.values() if isinstance(v, dict) else [] if v is None else [v]
            if isinstance(v, dict) and any(not isinstance(k, str) or (serials is not None and k not in serials) for k in v): raise ValueError(error)
            if any(type(x) is not int or not 0 <= x <= 100 for x in candidates): raise ValueError(error)
        elif key == 'KNOCKOUT_DRAW_WEIGHTS':
            if not isinstance(v, list) or len(v) != 8 or any(not isinstance(row, list) or len(row) != 4 or any(not number(x) or x <= 0 for x in row) for row in v):
                raise ValueError(error + '，需要 8×4 正数矩阵')
        elif key == 'MAX_ROUNDS_PRELIM_BY_FORMAT':
            if not isinstance(v, dict) or set(v) != {'1v1', '2v2', '3v3'} or any(type(x) is not int or x < 1 for x in v.values()): raise ValueError(error)
        elif key.startswith('MAP_SIZES'):
            if not isinstance(v, list) or not v or any(not map_size(x) for x in v) or len(v) != len(set(v)): raise ValueError(error)
        elif key.endswith('MAP_SIZE'):
            if not map_size(v): raise ValueError(error)
        elif key == 'DEFAULT_ENABLED_MODES':
            if not isinstance(v, list) or not v or any(x not in ('1v1', '2v2', '3v3') for x in v) or len(v) != len(set(v)): raise ValueError(error)
        elif type(d) is bool:
            if type(v) is not bool: raise ValueError(error)
        elif isinstance(d, (int, float)):
            duration = key.startswith('DELAY_') or key in ('LOG_STREAM_INTERVAL', 'AUTO_START_DELAY', 'TOURNAMENT_EVENT_DELAY', 'TOURNAMENT_PAUSE_POLL', 'DEFAULT_BATTLE_DELAY', 'ROOM_AI_DELAY', 'ROOM_IDLE_HOURS')
            if not number(v) or v < 0 or (type(d) is int and type(v) is not int and not duration): raise ValueError(error)
            if ('THRESHOLD' in key or key == 'AI_STATE_NOISE') and v > 1: raise ValueError(error)
            zero_ok = key.startswith(('DELAY_', 'RANDOM_', 'DISPLAY_')) or key.endswith('LOG_LEVEL') or key in {
                'CUSTOM_PREVIEW_BATTLES', 'DEFAULT_DISPLAY_LEVEL', 'DEFAULT_RESTIM_MODE', 'DEFAULT_TOURNAMENT_TYPE',
                'TOURNAMENT_EVENT_DELAY', 'DEFAULT_BATTLE_DELAY', 'ROOM_AI_DELAY', 'AUTO_START_DELAY', 'AI_STATE_NOISE',
                'AI_DODGE_WEIGHT', 'AI_COUNTER_WEIGHT', 'AI_REACTION_REWARD', 'AI_REACTION_PENALTY', 'AI_DEFAULT_STATUS', 'SEASON_FALLBACK_STATUS', 'AI_SUMMON_STATUS_MIN', 'AI_SUMMON_STATUS_MAX'} or 'THRESHOLD' in key
            if not zero_ok and v == 0: raise ValueError(error + '，必须大于零')
            if key.startswith('BO_N') and v % 2 != 1: raise ValueError(error + '，BoN 必须是正奇数')
            if key == 'PRELIM_WORKERS' and v > 32: raise ValueError(error + '，最大 32')
            if key == 'AUTO_REACTION_COUNT' and v > 999: raise ValueError(error + '，最大 999')
            if key.startswith('SIM_') and key.endswith('COUNT') and v > 1000: raise ValueError(error + '，最大 1000')
            if key in ('AI_DEFAULT_STATUS', 'SEASON_FALLBACK_STATUS', 'AI_SUMMON_STATUS_MIN', 'AI_SUMMON_STATUS_MAX') and v > 100: raise ValueError(error)
        elif not isinstance(v, str): raise ValueError(error)
    if values['AI_DODGE_WEIGHT'] + values['AI_COUNTER_WEIGHT'] <= 0: raise ValueError('初始反应权重不能同时为零')
    if values['CUSTOM_DEFAULT_GAMES'] > values['CUSTOM_MAX_GAMES']: raise ValueError('默认场数不能超过场数上限')
    if values['AI_SUMMON_STATUS_MIN'] > values['AI_SUMMON_STATUS_MAX']: raise ValueError('召唤物状态下限不能超过上限')
    return copy.deepcopy(values)


def instance_name():
    instance = os.environ.get('AUTOCOMBAT_UI_INSTANCE', '')
    if not instance and '--port' in sys.argv and Path(sys.argv[0]).stem not in ('ai_battle', 'sim'):
        pos = sys.argv.index('--port')
        instance = sys.argv[pos + 1] if pos + 1 < len(sys.argv) else ''
    if not instance and Path(sys.argv[0]).stem not in ('ai_battle', 'sim'):
        instance = next((arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--port=')), '')
    return instance if instance.isdigit() else '8889'


class SettingsStore:
    def __init__(self, root=None, instance=None):
        self.root = Path(root) if root else Path(__file__).resolve().parent
        instance = instance or instance_name()
        self.path = self.root / '.ui-runtime' / ('settings.json' if instance == '8889' else f'settings-{instance}.json')
        self.lock = threading.RLock()
        self._cached = None
        self._stamp = None

    def read(self, copy_values=True):
        with self.lock:
            stat = self.path.stat() if self.path.exists() else None
            stamp = (stat.st_mtime_ns, stat.st_size) if stat else None
            if self._cached is None or self._stamp != stamp:
                saved = json.loads(self.path.read_text(encoding='utf-8')) if stat else {}
                if not isinstance(saved, dict): raise ValueError('设置文件必须是 JSON 对象')
                self._cached = validate({**copy.deepcopy(DEFAULTS), **saved})
                self._stamp = stamp
            return copy.deepcopy(self._cached) if copy_values else self._cached

    def version(self, values=None):
        return hashlib.sha256(json.dumps(values if values is not None else self.read(), sort_keys=True).encode()).hexdigest()

    def write(self, values, version=None, serials=None):
        with self.lock:
            if version is not None and version != self.version(): raise RuntimeError('配置已被其他页面修改，请重新加载后保存')
            values = validate(values, serials)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + '.' + secrets.token_hex(8) + '.tmp')
            try:
                temp.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(temp, self.path)
            finally:
                temp.unlink(missing_ok=True)
            return values


def configure(store):
    global _default_store
    _default_store = store


def default_store():
    global _default_store
    if _default_store is None: _default_store = SettingsStore()
    return _default_store


def snapshot():
    current = _snapshot.get()
    return copy.deepcopy(current if current is not None else default_store().read())


def value(key, live=False):
    key = ALIASES.get(key, key)
    current = None if live else _snapshot.get()
    return copy.deepcopy((current if current is not None else default_store().read(copy_values=False))[key])


@contextmanager
def settings_scope(values=None):
    token = _snapshot.set(values if values is not None else snapshot())
    try: yield
    finally: _snapshot.reset(token)


def pinned(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        with settings_scope(getattr(self, '_settings', None)):
            return method(self, *args, **kwargs)
    return run


_UNSET = object()


def valid_season(chosen):
    if chosen is None: return True
    candidates = chosen.values() if isinstance(chosen, dict) else [chosen]
    return (not isinstance(chosen, dict) or all(isinstance(k, str) for k in chosen)) and all(type(v) is int and 0 <= v <= 100 for v in candidates)


def season_statuses(serials, override=_UNSET):
    import random
    chosen = value('SEASON_AVG_STATUS') if override is _UNSET else override
    return {s: random.randint(0, 100) if chosen is None else chosen.get(s, value('SEASON_FALLBACK_STATUS')) if isinstance(chosen, dict) else chosen for s in serials}


def file_path(key):
    return str(default_store().root / value(key))
