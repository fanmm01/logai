"""Isolated structured batch simulation worker; one JSON update per battle."""
import contextlib
import io
import json
import os
import random
import sys
import time


def main():
    params = json.loads(sys.stdin.readline())
    wire = sys.stdout
    with open(os.devnull, 'w', encoding='utf-8') as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
        from sim import run_battle_pair
        from ai_battle import Tournament
        random.seed(params['seed'])
        tournament = Tournament()
        tournament.init_characters()
        wins_a = wins_b = rounds = timeouts = 0
        started = time.monotonic()
        for i in range(params['n']):
            a, b, avg, out, _ = run_battle_pair(tournament, params['team_a'], params['team_b'],
                                               params['map_size'], 1, season_status=params['season'])
            wins_a += a
            wins_b += b
            rounds += avg
            timeouts += out
            result = dict(wins_a=wins_a, wins_b=wins_b, rate_a=wins_a / (i + 1),
                          rate_b=wins_b / (i + 1), average_rounds=rounds / (i + 1), timeouts=timeouts,
                          elapsed=round(time.monotonic() - started, 2))
            print(json.dumps(dict(completed=i + 1, result=result)), file=wire, flush=True)


if __name__ == '__main__':
    main()
