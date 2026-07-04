#!/usr/bin/env python3
"""
Quick battle simulator — run N battles between teams and report win rates.

Usage:
  python sim.py TEAM_A vs TEAM_B [-n N] [-m WxH] [-s SEED] [--season S]
  python sim.py --mode table --format 1v1 [-n N] [-s SEED] [--season S]
  python sim.py --mode table --format 2v2 --teams "A+B,C+D,..." [-n N] [--season S]

Options:
  -n N         Battles per matchup (default 100 for single, 50 for table)
  -m WxH       Map size (default 10x10)
  -s SEED      Random seed
  --season S   Season average status 0-100 (default random). Higher = better AI.
  --mode       'single' (default) or 'table' (NxN win-rate matrix)
  --format     1v1 | 2v2 | 3v3
  --teams      Comma-separated team list for 2v2/3v3 table mode

Examples:
  python sim.py Y5+Y6+Y7+Y8 vs Y9+Y12 -n 100
  python sim.py --mode table --format 1v1 -n 50
  python sim.py --mode table --format 2v2 --teams "Y5+Y6,Y7+Y8,Y1+Y2" -n 30
"""

import sys, os, random, time, json, csv, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ai_battle import BattleEngine, Tournament, AIController, set_terminal_quiet
from characters_data import ALL_CHARACTERS, load_character_to_engine


def parse_single_args(argv):
    """Parse single-match args. Returns (team_a, team_b, num, map_size, seed, season_status)."""
    args = ' '.join(argv[1:])
    parts = re.split(r'\s+vs\s+', args, flags=re.IGNORECASE)
    if len(parts) != 2:
        print('Usage: python sim.py TEAM_A vs TEAM_B [-n N] [-m WxH] [-s SEED] [--season S]')
        sys.exit(1)
    left, right = parts[0].strip(), parts[1].strip()
    num, map_size, seed, season_status = 100, '10x10', random.randint(0, 99999), None
    m_n = re.search(r'-n\s+(\d+)', right)
    if m_n: num = int(m_n.group(1)); right = right.replace(m_n.group(0), '')
    m_m = re.search(r'-m\s+(\S+)', right)
    if m_m: map_size = m_m.group(1); right = right.replace(m_m.group(0), '')
    m_s = re.search(r'-s\s+(\d+)', right)
    if m_s: seed = int(m_s.group(1)); right = right.replace(m_s.group(0), '')
    m_season = re.search(r'--season\s+(\d+)', right)
    if m_season: season_status = int(m_season.group(1)); right = right.replace(m_season.group(0), '')
    team_a = [t for t in re.split(r'[\s+]+', left) if t and not t.startswith('-')]
    team_b = [t for t in re.split(r'[\s+]+', right) if t and not t.startswith('-')]
    return team_a, team_b, num, map_size, seed, season_status


def parse_table_args(argv):
    """Parse table-mode args."""
    fmt = '1v1'; teams_list = None; num = 50; map_size = '10x10'
    seed = random.randint(0, 99999); season_status = None
    i = 2
    while i < len(argv):
        if argv[i] == '--format' and i + 1 < len(argv):
            fmt = argv[i + 1]; i += 2
        elif argv[i] == '--teams' and i + 1 < len(argv):
            teams_list = argv[i + 1]; i += 2
        elif argv[i] == '-n' and i + 1 < len(argv):
            num = int(argv[i + 1]); i += 2
        elif argv[i] == '-m' and i + 1 < len(argv):
            map_size = argv[i + 1]; i += 2
        elif argv[i] == '-s' and i + 1 < len(argv):
            seed = int(argv[i + 1]); i += 2
        elif argv[i] == '--season' and i + 1 < len(argv):
            season_status = int(argv[i + 1]); i += 2
        else:
            i += 1
    return fmt, teams_list, num, map_size, seed, season_status


def run_battle_pair(t, team_a, team_b, map_size, N, quiet=True, season_status=None):
    """Run N battles between two teams. Returns (wins_a, wins_b, avg_rds, timeouts, elapsed).

    season_status can be:
      - None: pure uniform random per character (backward compat)
      - int: same season bias for all characters
      - dict: {serial: int} per-character season status
    """
    a_uids = [t.char_map[s] for s in team_a]
    b_uids = [t.char_map[s] for s in team_b]

    def _setup(engine):
        for c in ALL_CHARACTERS:
            uid = t.char_map[c['serial']]
            load_character_to_engine(engine, c, uid)
            if not c.get('pre_transformed'):
                engine.process_command(uid, '.hs')
        for uid in a_uids: t.ai_map[uid].team = 'Y'
        for uid in b_uids: t.ai_map[uid].team = 'X'
        engine._ai_react_dodge_w = {}; engine._ai_react_counter_w = {}
        for uid in a_uids + b_uids:
            ai = t.ai_map.get(uid)
            if ai:
                engine._ai_react_dodge_w[uid] = ai.react_dodge_w
                engine._ai_react_counter_w[uid] = ai.react_counter_w
        # Build per-uid season status mapping
        if isinstance(season_status, dict):
            uid_season = {}
            for serial, uid in zip(team_a + team_b, a_uids + b_uids):
                uid_season[uid] = season_status.get(serial, 50)
        else:
            uid_season = season_status  # None or int
        engine.setup_battle(a_uids, b_uids, map_size, season_status=uid_season)

    if quiet: set_terminal_quiet(True)
    wins_a = wins_b = timeouts = total_rds = 0
    t0 = time.time()
    for _ in range(N):
        eng = BattleEngine(delay=0, quiet=True)
        _setup(eng)
        r = eng.run_battle(t.ai_map)
        if r['winner'] == 'Y': wins_a += 1
        else: wins_b += 1
        total_rds += r.get('rounds', 0)
        if r.get('timeout'): timeouts += 1
    elapsed = time.time() - t0
    if quiet: set_terminal_quiet(False)
    return wins_a, wins_b, total_rds / N, timeouts, elapsed


def compute_contribution_from_matrix(matrix, teams, nm):
    """Compute 3 contribution algorithms from win-rate matrix.
    Algorithm 1: within-format in/out rate diff
    Algorithm 2: cross-format (3v3 with vs 2v2 without) — approximated
    Algorithm 3: efficiency average"""
    all_serials = set()
    for t in teams:
        for s in t: all_serials.add(s)

    # Algorithm 1: avg win rate when in team vs overall avg
    char_rates = {}
    for s in all_serials:
        rates = []
        for i, ti in enumerate(teams):
            if s not in ti: continue
            for j, tj in enumerate(teams):
                if i == j: continue
                rates.append(matrix[i][j])
        char_rates[s] = sum(rates) / len(rates) if rates else 0

    overall_avg = sum(sum(row) for row in matrix) / max(1, len(matrix) ** 2 - len(matrix))
    algo1 = [(s, nm.get(s, s), round(char_rates.get(s, 0) - overall_avg, 4)) for s in all_serials]
    algo1.sort(key=lambda x: -x[2])

    # Algorithm 2: approximated cross-format
    algo2 = [(s, nm.get(s, s), round(char_rates.get(s, 0) - 0.5, 4)) for s in all_serials]
    algo2.sort(key=lambda x: -x[2])

    # Algorithm 3: normalized rate
    mx = max(char_rates.values()) if char_rates else 1
    algo3 = [(s, nm.get(s, s), round(char_rates.get(s, 0) / max(0.01, mx), 4)) for s in all_serials]
    algo3.sort(key=lambda x: -x[2])

    return {'algo1': algo1, 'algo2': algo2, 'algo3': algo3}


def print_contribution(contrib):
    """Print contribution ranking."""
    names = {'algo1': '算法一: 同格式内胜率差',
             'algo2': '算法二: 跨格式近似',
             'algo3': '算法三: 归一化胜率'}
    for key in ['algo1', 'algo2', 'algo3']:
        print(f'\n  {names[key]}')
        print(f'  {"排名":<4} {"角色":<8} {"名称":<6} {"贡献值":>8}')
        print(f'  {"-"*28}')
        for rank, (s, name, val) in enumerate(contrib[key], 1):
            print(f'  {rank:<4} {s:<8} {name:<6} {val:>+8.4f}')


def mode_single(argv):
    team_a, team_b, N, map_size, seed, season_status = parse_single_args(argv)
    random.seed(seed)
    print('初始化...', flush=True)
    t = Tournament(); t.init_characters()
    # Build per-character season status
    if season_status is None:
        char_season = {s: random.randint(0, 100) for s in t.char_map}
    else:
        char_season = {s: int(season_status) for s in t.char_map}
    nm = {c['serial']: c['name'] for c in ALL_CHARACTERS}
    a_disp = '+'.join(nm.get(s, s) for s in team_a)
    b_disp = '+'.join(nm.get(s, s) for s in team_b)
    # Show season status for involved characters
    status_str = ', '.join(f'{nm.get(s,s)}:{char_season.get(s,"?")}' for s in team_a + team_b)
    print(f'{a_disp} v.s. {b_disp} — {N}场 [{map_size}]  seed={seed}', flush=True)
    print(f'  赛季状态: {status_str}', flush=True)
    wins_a, wins_b, avg_rds, timeouts, elapsed = run_battle_pair(t, team_a, team_b, map_size, N, quiet=False, season_status=char_season)
    rate_a = wins_a / N * 100; rate_b = wins_b / N * 100
    print(f'\n{"="*55}')
    print(f'  {a_disp}  v.s.  {b_disp}')
    print(f'  {N}场 [{map_size}]  seed={seed}')
    print(f'{"="*55}')
    print(f'  {a_disp:30s}  胜: {wins_a:4d}场 ({rate_a:5.1f}%)')
    print(f'  {b_disp:30s}  胜: {wins_b:4d}场 ({rate_b:5.1f}%)')
    print(f'  平均回合数: {avg_rds:.1f}  超时: {timeouts}场')
    print(f'  耗时: {elapsed:.1f}s  ({N/elapsed:.0f} b/s)')
    print(f'{"="*55}')
    if wins_a > wins_b: print(f'  >>> {a_disp} 优势 ({rate_a:.1f}%)')
    elif wins_b > wins_a: print(f'  >>> {b_disp} 优势 ({rate_b:.1f}%)')
    else: print(f'  >>> 平局')
    print()


def mode_table(argv):
    fmt, teams_str, N, map_size, seed, season_status = parse_table_args(argv)
    random.seed(seed)
    print('初始化...', flush=True)
    t = Tournament(); t.init_characters()
    # Build per-character season status
    if season_status is None:
        char_season = {s: random.randint(0, 100) for s in t.char_map}
    else:
        char_season = {s: int(season_status) for s in t.char_map}
    nm = {c['serial']: c['name'] for c in ALL_CHARACTERS}

    # Build team list
    if fmt == '1v1':
        teams = [[s] for s in sorted(t.char_map.keys())]
    else:
        if not teams_str:
            print('2v2/3v3 table mode needs --teams "A+B,C+D,..."'); sys.exit(1)
        teams = [re.split(r'[\s+]+', ts.strip()) for ts in teams_str.split(',')]

    # Validate
    valid = set(t.char_map.keys())
    for team in teams:
        for s in team:
            if s not in valid:
                print(f'无效角色: {s}. 可用: {sorted(valid)}'); sys.exit(1)

    team_names = ['+'.join(nm.get(s, s) for s in team) for team in teams]
    n = len(teams)
    # Show per-character season status
    status_str = ', '.join(f'{nm.get(s,s)}:{char_season.get(s,"?")}' for s in sorted(t.char_map.keys()))
    print(f'胜率矩阵: {fmt}  {n}×{n}  每格{N}场 [{map_size}]  seed={seed}', flush=True)
    print(f'  赛季状态: {status_str}', flush=True)

    # Compute matrix
    matrix = [[0.0] * n for _ in range(n)]
    total = n * (n - 1)
    done = 0
    t0 = time.time()

    for i in range(n):
        for j in range(n):
            if i == j: matrix[i][j] = 0.5; continue
            wins_a, wins_b, _, _, _ = run_battle_pair(t, teams[i], teams[j], map_size, N, quiet=True, season_status=char_season)
            matrix[i][j] = wins_a / N
            matrix[j][i] = wins_b / N  # symmetric, already computed
            done += 1
            elapsed = time.time() - t0
            remain = total - done
            eta = remain / max(1, done) * elapsed
            print(f'  [{done}/{total}] {team_names[i]} vs {team_names[j]}: '
                  f'{wins_a/N*100:.0f}%-{wins_b/N*100:.0f}%  ETA {eta:.0f}s', flush=True)

    elapsed = time.time() - t0

    # ASCII table
    print(f'\n{"="*80}')
    print(f'胜率矩阵: {fmt}  {n}队  每格{N}场 [{map_size}]  seed={seed}')
    print(f'{"="*80}')
    header = '          ' + ''.join(f'{tname:>10s}' for tname in team_names)
    print(header)
    print('-' * len(header))
    for i in range(n):
        row = f'{team_names[i]:>10s}'
        for j in range(n):
            if i == j: row += f'     {"—":>5s}'
            else: row += f'  {matrix[i][j]*100:>7.1f}%'
        print(row)
    print(f'{"="*80}')

    # CSV output
    csv_path = f'sim_matrix_{fmt}.csv'
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow([''] + team_names)
        for i in range(n):
            w.writerow([team_names[i]] + [f'{matrix[i][j]:.4f}' if i != j else '—' for j in range(n)])
    print(f'\nCSV: {csv_path}')

    # JSON output
    json_path = f'sim_matrix_{fmt}.json'
    json_data = {
        'format': fmt, 'teams': team_names, 'n_per_cell': N,
        'map_size': map_size, 'seed': seed, 'elapsed_s': round(elapsed, 1),
        'matrix': {team_names[i]: {team_names[j]: round(matrix[i][j], 4)
                   for j in range(n) if i != j} for i in range(n)}
    }
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    print(f'JSON: {json_path}')

    # Contribution ranking
    print(f'\n{"="*80}')
    print('团队贡献率排行榜')
    print(f'{"="*80}')
    contrib = compute_contribution_from_matrix(matrix, teams, nm)
    print_contribution(contrib)
    print()

    print(f'总耗时: {elapsed:.1f}s')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--mode':
        if len(sys.argv) > 2 and sys.argv[2] == 'table':
            mode_table(sys.argv)
        else:
            print('Usage: --mode table --format 1v1|2v2|3v3 [--teams "..."]')
    else:
        mode_single(sys.argv)
