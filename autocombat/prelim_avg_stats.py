#!/usr/bin/env python3
"""
预赛赛段10轮平均积分与胜率统计
==============================
运行10轮预赛（仅预赛，跳过淘汰赛），统计每个角色的:
  - 平均积分 (avg points)     — 胜=3分, 负=0分
  - 平均胜场 (avg wins)
  - 平均场次 (avg battles)
  - 胜率 (win rate)           — 胜场/场次

包含1v1、2v2、3v3全部预赛格式。

Usage:
  python prelim_avg_stats.py [-n N] [--workers W]

Options:
  -n N        运行轮数 (默认10)
  --workers W 并行线程数 (默认8, 设为1为串行)
"""

import os, sys, time, argparse, threading

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'autocombat'))

import ai_battle
from characters_data import ALL_CHARACTERS

# ── 预赛配置（速度优先） ──
ai_battle.DELAY_STANDINGS_DISPLAY = 0
ai_battle.DELAY_PRELIM_TURN = 0
ai_battle.DELAY_PRELIM_BETWEEN_BATTLES = 0
ai_battle.DELAY_DRAW_DISPLAY = 0
ai_battle.MAX_ROUNDS_PRELIM = 25

# ── 线程安全的结果收集 ──
# Tournament.battle() 在并行模式 (_parallel_prelim=True) 下不会写入 self.results,
# 因此 monkey-patch 以始终收集结果。
_collected_results: list = []
_results_lock = threading.Lock()
_original_battle = ai_battle.Tournament.battle


def _battle_tracking(self, team_a, team_b, map_size, phase_label='',
                     best_of=1, display_level=None, delay=0, max_rounds=None):
    """Wrapped battle() that always records the result regardless of _parallel_prelim."""
    # 处理默认参数
    if display_level is None:
        display_level = ai_battle.DisplayLevel.WINLOSS
    winner, loser, rds, result = _original_battle(
        self, team_a, team_b, map_size, phase_label,
        best_of=best_of, display_level=display_level,
        delay=delay, max_rounds=max_rounds,
    )
    a_name = '+'.join(team_a)
    b_name = '+'.join(team_b)
    with _results_lock:
        _collected_results.append((
            a_name, b_name, winner, rds, map_size, phase_label,
            best_of > 1, result,
        ))
    return (winner, loser, rds, result)


ai_battle.Tournament.battle = _battle_tracking


def run_one_prelim(run_idx: int, total: int):
    """运行一轮预赛，返回该轮的逐角色统计字典。

    Returns:
        dict: serial -> {'wins': int, 'battles': int, 'points': float}
    """
    global _collected_results
    _collected_results = []  # 清空上一轮的结果

    print(f'[Run {run_idx}/{total}] 初始化...', flush=True)
    t = ai_battle.Tournament(0)  # type=0 → 仅预赛，跳过淘汰赛
    t.init_characters()

    # 降低日志噪音 → WARNING 级别只显示错误
    ai_battle.CONSOLE_LOG_LEVEL = ai_battle.logging.WARNING

    t0 = time.time()
    t.run_phases()
    elapsed = time.time() - t0

    # 恢复日志级别
    ai_battle.CONSOLE_LOG_LEVEL = ai_battle.logging.INFO

    # 从收集到的结果中统计
    stats = {}  # serial → {'wins': int, 'battles': int, 'points': float}

    for a_name, b_name, winner, rounds, map_sz, phase, is_bo, full_result in _collected_results:
        a_chars = a_name.split('+')
        b_chars = b_name.split('+')

        # 计数场次（每个参与角色 +1 场）
        for ch in a_chars + b_chars:
            if ch not in stats:
                stats[ch] = {'wins': 0, 'battles': 0, 'points': 0.0}
            stats[ch]['battles'] += 1

        # 胜者加分（胜=3分）
        win_chars = a_chars if winner == a_name else b_chars
        for ch in win_chars:
            if ch not in stats:
                stats[ch] = {'wins': 0, 'battles': 0, 'points': 0.0}
            stats[ch]['wins'] += 1
            stats[ch]['points'] += 3.0

    match_count = len(_collected_results)
    print(f'[Run {run_idx}/{total}] 完成: {match_count} 场预赛, 耗时 {elapsed:.0f}s', flush=True)
    return stats


def main():
    parser = argparse.ArgumentParser(description='预赛10轮平均积分与胜率统计')
    parser.add_argument('-n', type=int, default=10, help='运行轮数 (默认10)')
    parser.add_argument('--workers', type=int, default=8, help='并行线程数 (默认8, 1=串行)')
    args = parser.parse_args()

    NUM_RUNS = args.n
    if NUM_RUNS <= 0:
        parser.error(f'轮数必须 > 0, 收到: {NUM_RUNS}')
    ai_battle.PRELIM_WORKERS = max(1, args.workers)

    print(f'配置: {NUM_RUNS}轮, {ai_battle.PRELIM_WORKERS}并行线程')
    print()

    # ── 累积统计 ──
    total_stats = {}  # serial → {'wins': int, 'battles': int, 'points': float}

    overall_start = time.time()
    for run in range(1, NUM_RUNS + 1):
        run_stats = run_one_prelim(run, NUM_RUNS)
        for ch, s in run_stats.items():
            if ch not in total_stats:
                total_stats[ch] = {'wins': 0, 'battles': 0, 'points': 0.0}
            total_stats[ch]['wins'] += s['wins']
            total_stats[ch]['battles'] += s['battles']
            total_stats[ch]['points'] += s['points']

    overall_elapsed = time.time() - overall_start

    # ── 名称映射 ──
    name_map = {c['serial']: c['name'] for c in ALL_CHARACTERS}

    # ── 排序: 平均积分降序 → 胜率降序 ──
    def sort_key(ch):
        s = total_stats[ch]
        avg_pts = s['points'] / max(1, NUM_RUNS)
        win_rate = s['wins'] / max(1, s['battles'])
        return (-avg_pts, -win_rate)

    sorted_chars = sorted(total_stats.keys(), key=sort_key)

    # ── 输出结果 ──
    sep = '=' * 78
    header = f'\n{sep}\n预赛 {NUM_RUNS} 轮平均积分与胜率统计\n{sep}'
    print(header)
    col_headers = f'{"排名":<4} {"编号":<6} {"名称":<8} {"平均积分":>8} {"平均胜场":>8} {"平均场次":>8} {"胜率":>8}'
    print(col_headers)
    print('-' * 58)

    output_lines = [header, '', col_headers, '-' * 58]

    for rank, ch in enumerate(sorted_chars, 1):
        s = total_stats[ch]
        avg_pts = s['points'] / max(1, NUM_RUNS)
        avg_wins = s['wins'] / max(1, NUM_RUNS)
        avg_battles = s['battles'] / max(1, NUM_RUNS)
        win_rate = s['wins'] / max(1, s['battles']) * 100
        name = name_map.get(ch, ch)
        line = f'{rank:<4} {ch:<6} {name:<8} {avg_pts:>8.1f} {avg_wins:>8.1f} {avg_battles:>8.1f} {win_rate:>7.1f}%'
        print(line)
        output_lines.append(line)

    # ── 汇总统计 ──
    all_win_rates = []
    for ch in sorted_chars:
        s = total_stats[ch]
        if s['battles'] > 0:
            all_win_rates.append(s['wins'] / s['battles'] * 100)

    total_matches_per_run = sum(s['battles'] for s in total_stats.values()) / max(1, NUM_RUNS) / 2
    avg_win_rate_str = f'{sum(all_win_rates) / len(all_win_rates):.1f}%' if all_win_rates else 'N/A'
    summary = (
        f'\n{"-" * 58}\n'
        f'  总角色数:       {len(sorted_chars)}\n'
        f'  平均每轮场次:   {total_matches_per_run:.0f}\n'
        f'  平均胜率:       {avg_win_rate_str}\n'
        f'  总耗时:         {overall_elapsed:.0f}s ({overall_elapsed / max(1, NUM_RUNS):.0f}s/轮)\n'
        f'  并行线程:       {ai_battle.PRELIM_WORKERS}\n'
        f'{sep}\n'
    )
    print(summary)
    output_lines.append(summary)

    # ── 保存到文件 ──
    result_dir = os.path.dirname(os.path.abspath(__file__))
    result_path = os.path.join(result_dir, 'prelim_avg_stats_results.txt')
    with open(result_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(output_lines))
    print(f'结果已保存到: {result_path}')


if __name__ == '__main__':
    main()
