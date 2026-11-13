"""Run pinned, isolated browser tasks. stdout is exclusively JSON-lines."""
import contextlib
import io
import json
import logging
import random
import re
import sys
import time


def main(params=None, wire=None):
    params = params if params is not None else json.loads(sys.stdin.readline())
    wire = wire or sys.stdout

    def report(**row):
        print(json.dumps(row, ensure_ascii=False), file=wire, flush=True)

    class Log(io.TextIOBase):
        pending = ''

        def write(self, text):
            self.pending += text
            while '\n' in self.pending:
                line, self.pending = self.pending.split('\n', 1)
                if line.strip():
                    report(log=line)
            return len(text)

        def flush(self):
            pass

        def reconfigure(self, **kwargs):
            pass

    with contextlib.redirect_stdout(Log()), contextlib.redirect_stderr(Log()):
        import ai_battle as ab
        if params.get('mode') == 'verbose' or params.get('verbose'):
            for handler in ab.logger.handlers:
                handler.setLevel(logging.DEBUG)
        from sim import run_battle_pair, compute_matrix, compute_avg_points_ranking, compute_contribution_from_matrix
        from characters_data import ALL_CHARACTERS
        random.seed(params.get('seed'))
        mode = params.get('mode', 'single')
        if mode == 'train':
            import ai_trainer
            ai_trainer.main()
            report(completed=1, result={'message': '训练完成'})
            return
        t = ab.Tournament()
        t.init_characters()
        season = params.get('season')
        if mode == 'game':
            t.char_season_status = params.get('char_season') or {s: season if season is not None else random.randint(0, 100) for s in t.char_map}
            t._parallel_prelim = True
            replay = []

            class RoundLog(logging.Handler):
                def emit(self, record):
                    line = record.getMessage()
                    if re.match(r'^\s*第\d+回合 Y: ', line):
                        replay.append(line)
                        report(round=line)

            handler = RoundLog()
            ab.logger.addHandler(handler)
            try:
                _, _, _, result = t.battle(params['team_a'], params['team_b'], params.get('map_size', '10x10'),
                    display_level=ab.DisplayLevel.DEBUG if params.get('verbose') else ab.DisplayLevel.ROUND, delay=0,
                    max_rounds=params.get('max_rounds', ab.MAX_ROUNDS_KNOCKOUT))
            finally:
                ab.logger.removeHandler(handler)
            report(completed=1, result=result, replay=replay)
        elif mode == 'verbose':
            t.char_season_status = {s: season if season is not None else random.randint(0, 100) for s in t.char_map}
            _, _, _, result = t.battle(params['team_a'], params['team_b'], params.get('map_size', '10x10'),
                display_level=ab.DisplayLevel.DEBUG, delay=0)
            report(completed=1, result=result)
        elif mode == 'table':
            teams = params['teams']
            char_season = {s: season if season is not None else random.randint(0, 100) for s in t.char_map}
            names = {c['serial']: c['name'] for c in ALL_CHARACTERS}
            team_names = ['+'.join(names.get(s, s) for s in team) for team in teams]
            matrix = compute_matrix(t, teams, params['n'], params.get('map_size', '10x10'), char_season,
                                    lambda done, matrix: report(completed=done, result={'teams': team_names, 'matrix': matrix}))
            report(completed=len(teams)*(len(teams)-1)//2, result=dict(teams=team_names, matrix=matrix,
                   rankings=compute_avg_points_ranking(matrix, team_names),
                   contribution=compute_contribution_from_matrix(matrix, teams, names)))
        elif mode == 'prelim':
            from sim import prelim_runtime, new_prelim_totals, accumulate_prelim_rankings, compute_prelim_averages
            modes = params.get('formats', ['1v1', '2v2', '3v3'])
            totals = new_prelim_totals(modes)
            with prelim_runtime(params.get('workers', 8), season):
                for i in range(params['n']):
                    tournament = ab.Tournament(tournament_type=0, enabled_modes=modes)
                    tournament.init_characters()
                    tournament.run_phases(write_results_flag=False)
                    accumulate_prelim_rankings(totals, tournament._rankings_info)
                    report(completed=i+1, result={'rankings': compute_prelim_averages(totals, i+1)})
        else:
            a = b = rounds = timeouts = 0
            started = time.monotonic()
            char_season = {s: season if season is not None else random.randint(0, 100) for s in t.char_map}
            for i in range(params['n']):
                wa, wb, avg, out, _ = run_battle_pair(t, params['team_a'], params['team_b'], params.get('map_size', '10x10'), 1, season_status=char_season)
                a += wa
                b += wb
                rounds += avg
                timeouts += out
                report(completed=i+1, result=dict(wins_a=a, wins_b=b, rate_a=a/(i+1), rate_b=b/(i+1),
                       average_rounds=rounds/(i+1), timeouts=timeouts, elapsed=time.monotonic()-started))


if __name__ == '__main__':
    if '--server' in sys.argv:
        for line in sys.stdin:
            main(json.loads(line), sys.stdout)
    else:
        main()
