import os
import sys
import time
from collections import defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'autocombat'))
import ai_battle

ai_battle.DELAY_STANDINGS_DISPLAY = 0
ai_battle.DELAY_PRELIM_TURN = 0
ai_battle.DELAY_PRELIM_BETWEEN_BATTLES = 0
ai_battle.DELAY_DRAW_DISPLAY = 0
ai_battle.MAX_ROUNDS_PRELIM = 25
ai_battle.PRELIM_WORKERS = 8

NUM_RUNS = 10

# Overall stats
char_wins = {}
char_battles = {}
total_matches = []

# Per-mode stats: mode -> {char: wins}, {char: battles}
mode_wins = defaultdict(lambda: defaultdict(int))
mode_battles = defaultdict(lambda: defaultdict(int))

for run in range(1, NUM_RUNS + 1):
    print(f'Run {run}/{NUM_RUNS}...')
    t = ai_battle.Tournament(0)
    t.init_characters()
    t.run_phases()

    # count wins and battles from prelim-only results
    wins = {}
    battles = {}
    for a_name, b_name, winner, rounds, map_sz, phase, is_bo in t.results:
        # Extract the broad mode (e.g. '1v1', '2v2', '3v3') from phase label
        mode = phase.split()[0] if phase else 'unknown'

        a_chars = a_name.split('+')
        b_chars = b_name.split('+')
        if winner == a_name:
            win_chars = a_chars
        else:
            win_chars = b_chars
        all_chars = a_chars + b_chars
        for ch in all_chars:
            battles[ch] = battles.get(ch, 0) + 1
            mode_battles[mode][ch] += 1
        for ch in win_chars:
            wins[ch] = wins.get(ch, 0) + 1
            mode_wins[mode][ch] += 1

    for ch in set(list(wins.keys()) + list(battles.keys())):
        char_wins[ch] = char_wins.get(ch, 0) + wins.get(ch, 0)
        char_battles[ch] = char_battles.get(ch, 0) + battles.get(ch, 0)
    total_matches.append(len(t.results))

print(f'\n=== {NUM_RUNS}-run summary ===')
print('Total prelim battles per run:', total_matches)
print('Average prelim battles per run:', sum(total_matches) / len(total_matches))

# ── Overall rankings ──
avg_wins = {ch: char_wins[ch] / NUM_RUNS for ch in sorted(char_wins)}
avg_battles = {ch: char_battles[ch] / NUM_RUNS for ch in sorted(char_battles)}

if not avg_wins:
    print('\nNo battle results found — check PRELIM_WORKERS (set to 1 for serial mode)')
    sys.exit(1)

sorted_chars = sorted(avg_wins.items(), key=lambda x: (-x[1], x[0]))
print('\nTop 20 characters by average wins per run (overall):')
for ch, avg in sorted_chars[:20]:
    print(f'  {ch}: avg wins = {avg:.2f}, avg battles = {avg_battles[ch]:.2f}, win rate = {avg / avg_battles[ch] * 100:.1f}%')

mean_avg_wins = sum(avg_wins.values()) / len(avg_wins)
print(f'\nOverall average wins per character per tournament: {mean_avg_wins:.2f}')

# ── Per-mode rankings ──
mode_order = ['1v1', '2v2', '3v3']
for mode in mode_order:
    if mode not in mode_wins:
        continue
    print(f'\n--- {mode} mode ---')
    mw = mode_wins[mode]
    mb = mode_battles[mode]
    avg_mw = {ch: mw[ch] / NUM_RUNS for ch in mw}
    avg_mb = {ch: mb[ch] / NUM_RUNS for ch in mb}
    sorted_mode = sorted(avg_mw.items(), key=lambda x: (-x[1], x[0]))
    print(f'  Top 20 characters by average wins in {mode}:')
    for ch, avg in sorted_mode[:20]:
        print(f'    {ch}: avg wins = {avg:.2f}, avg battles = {avg_mb[ch]:.2f}, win rate = {avg / avg_mb[ch] * 100:.1f}%')

# ── Save results ──
with open(os.path.join(os.path.dirname(__file__), 'temp_prelim_avg_results.txt'), 'w', encoding='utf-8') as f:
    f.write(f'=== {NUM_RUNS}-run summary ===\n')
    f.write('Run totals: ' + str(total_matches) + '\n')
    f.write('Average prelim battles per run: ' + str(sum(total_matches) / len(total_matches)) + '\n\n')

    f.write('=== Overall Top 50 by avg wins ===\n')
    for ch, avg in sorted_chars[:50]:
        f.write(f'{ch}: avg wins = {avg:.2f}, avg battles = {avg_battles[ch]:.2f}, win rate = {avg / avg_battles[ch] * 100:.1f}%\n')

    for mode in mode_order:
        if mode not in mode_wins:
            continue
        f.write(f'\n=== {mode} Top 50 by avg wins ===\n')
        mw = mode_wins[mode]
        mb = mode_battles[mode]
        avg_mw = {ch: mw[ch] / NUM_RUNS for ch in mw}
        avg_mb = {ch: mb[ch] / NUM_RUNS for ch in mb}
        sorted_mode = sorted(avg_mw.items(), key=lambda x: (-x[1], x[0]))
        for ch, avg in sorted_mode[:50]:
            f.write(f'{ch}: avg wins = {avg:.2f}, avg battles = {avg_mb[ch]:.2f}, win rate = {avg / avg_mb[ch] * 100:.1f}%\n')

print('\nResults written to temp_prelim_avg_results.txt')
