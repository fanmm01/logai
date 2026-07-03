import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'autocombat'))
import ai_battle

ai_battle.DELAY_STANDINGS_DISPLAY = 0
ai_battle.DELAY_PRELIM_TURN = 0
ai_battle.DELAY_PRELIM_BETWEEN_BATTLES = 0
ai_battle.DELAY_DRAW_DISPLAY = 0
ai_battle.MAX_ROUNDS_PRELIM = 25
ai_battle.PRELIM_WORKERS = 8

char_wins = {}
char_battles = {}
total_matches = []

for run in range(1, 11):
    print(f'Run {run}/10...')
    t = ai_battle.Tournament(0)
    t.init_characters()
    t.run_phases()
    # count wins and battles from prelim-only results
    wins = {}
    battles = {}
    for a_name, b_name, winner, rounds, map_sz, phase, is_bo in t.results:
        a_chars = a_name.split('+')
        b_chars = b_name.split('+')
        if winner == a_name:
            win_chars = a_chars
        else:
            win_chars = b_chars
        all_chars = a_chars + b_chars
        for ch in all_chars:
            battles[ch] = battles.get(ch, 0) + 1
        for ch in win_chars:
            wins[ch] = wins.get(ch, 0) + 1
    for ch in set(list(wins.keys()) + list(battles.keys())):
        char_wins[ch] = char_wins.get(ch, 0) + wins.get(ch, 0)
        char_battles[ch] = char_battles.get(ch, 0) + battles.get(ch, 0)
    total_matches.append(len(t.results))

print('\n=== 10-run summary ===')
print('Total prelim battles per run:', total_matches)
print('Average prelim battles per run:', sum(total_matches)/len(total_matches))

avg_wins = {ch: char_wins[ch] / 10.0 for ch in sorted(char_wins)}
avg_battles = {ch: char_battles[ch] / 10.0 for ch in sorted(char_battles)}

sorted_chars = sorted(avg_wins.items(), key=lambda x: (-x[1], x[0]))
print('\nTop 20 characters by average wins per run:')
for ch, avg in sorted_chars[:20]:
    print(f'  {ch}: avg wins = {avg:.2f}, avg battles = {avg_battles[ch]:.2f}, win rate = {avg / avg_battles[ch] * 100:.1f}%')

# Compute overall mean average wins across characters
mean_avg_wins = sum(avg_wins.values()) / len(avg_wins)
print(f'\nOverall average wins per character per tournament: {mean_avg_wins:.2f}')

# Save top 50
with open(os.path.join(os.path.dirname(__file__), 'temp_prelim_avg_results.txt'), 'w', encoding='utf-8') as f:
    f.write('Run totals: ' + str(total_matches) + '\n')
    f.write('Average prelim battles per run: ' + str(sum(total_matches)/len(total_matches)) + '\n')
    f.write('Top 50 by avg wins:\n')
    for ch, avg in sorted_chars[:50]:
        f.write(f'{ch}: avg wins = {avg:.2f}, avg battles = {avg_battles[ch]:.2f}, win rate = {avg / avg_battles[ch] * 100:.1f}%\n')
print('\nResults written to temp_prelim_avg_results.txt')
