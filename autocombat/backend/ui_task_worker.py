"""Run pinned, isolated browser tasks. stdout is exclusively JSON-lines."""
import contextlib
import io
import json
import logging
import os
import random
import re
import sys
import time
from combat_settings import settings_scope, value as setting, season_statuses


def main(params=None, wire=None):
    os.environ['AUTOCOMBAT_NO_FILE_LOG'] = '1'
    params = params if params is not None else json.loads(sys.stdin.readline())
    wire = wire or sys.stdout
    if hasattr(wire, 'reconfigure'):
        wire.reconfigure(encoding='utf-8')

    buffered_logs = []
    last_log = [time.monotonic()]

    def flush_logs():
        if buffered_logs:
            payload = json.dumps({'logs': buffered_logs}, ensure_ascii=False) if params.get('fast') else '\n'.join(json.dumps({'log': line}, ensure_ascii=False) for line in buffered_logs)
            print(payload, file=wire, flush=True)
            buffered_logs.clear()
            last_log[0] = time.monotonic()

    def report(**row):
        flush_logs()
        print(json.dumps(row, ensure_ascii=False), file=wire, flush=True)

    class Log(io.TextIOBase):
        pending = ''

        def write(self, text):
            self.pending += text
            while '\n' in self.pending:
                line, self.pending = self.pending.split('\n', 1)
                if line.strip():
                    buffered_logs.append(line)
                    if len(buffered_logs) >= 128 or time.monotonic() - last_log[0] >= .1:
                        flush_logs()
            return len(text)

        def flush(self):
            pass

        def reconfigure(self, **kwargs):
            pass

    with settings_scope(params.get('settings')), contextlib.redirect_stdout(Log()), contextlib.redirect_stderr(Log()):
        import ai_battle as ab
        # --server reuses the imported logger, but each request has a new buffer.
        for handler in ab.logger.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                handler.setStream(sys.stdout)
        ab._logging_override = logging.DEBUG if params.get('mode') == 'verbose' or params.get('verbose') else None
        if params.get('mode') == 'verbose' or params.get('verbose'):
            for handler in ab.logger.handlers:
                handler.setLevel(logging.DEBUG)
        from sim import run_battle_pair, compute_matrix_with_rounds, compute_avg_points_ranking, compute_contribution_from_matrix
        from characters_data import ALL_CHARACTERS
        random.seed(params.get('seed'))
        mode = params.get('mode', 'single')
        if mode == 'train':
            import ai_trainer
            roster = ai_trainer.main(expected_serials=params.get('enabled_serials'), target_serials=params.get('target_serials'))
            report(completed=1, result={'message': '训练完成', **roster})
            return
        t = ab.Tournament()
        t.init_characters()
        season = params.get('season', setting('SEASON_AVG_STATUS'))
        if mode == 'game':
            t.char_season_status = params.get('char_season') or season_statuses(t.char_map, season)
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
                _, _, _, result = t.battle(params['team_a'], params['team_b'], params.get('map_size', setting('DEFAULT_MAP_SIZE')),
                    display_level=ab.DisplayLevel.DEBUG if params.get('verbose') else ab.DisplayLevel.ROUND, delay=0,
                    max_rounds=params.get('max_rounds', ab.MAX_ROUNDS_KNOCKOUT))
            finally:
                ab.logger.removeHandler(handler)
            report(completed=1, result=result, replay=replay)
        elif mode == 'verbose':
            t.char_season_status = season_statuses(t.char_map, season)
            _, _, _, result = t.battle(params['team_a'], params['team_b'], params.get('map_size', setting('DEFAULT_MAP_SIZE')),
                display_level=ab.DisplayLevel.DEBUG, delay=0)
            report(completed=1, result=result)
        elif mode == 'table':
            teams = params['teams']
            char_season = season_statuses(t.char_map, season)
            names = {c['serial']: c['name'] for c in ALL_CHARACTERS}
            team_names = ['+'.join(names.get(s, s) for s in team) for team in teams]
            def matrix_progress(done, matrix, average_rounds):
                report(completed=done, result={'teams': team_names, 'matrix': matrix,
                                               'average_rounds': average_rounds,
                                               'matrix_semantics': 'matrix[行队伍][列队伍]表示行队伍战胜列队伍的概率'})

            matrix, average_rounds = compute_matrix_with_rounds(
                t, teams, params['n'], params.get('map_size', setting('DEFAULT_MAP_SIZE')),
                char_season, matrix_progress)
            result = dict(teams=team_names, matrix=matrix, average_rounds=average_rounds,
                          matrix_semantics='matrix[行队伍][列队伍]表示行队伍战胜列队伍的概率',
                          rankings=compute_avg_points_ranking(matrix, team_names),
                          contribution=compute_contribution_from_matrix(matrix, teams, names))
            report(completed=len(teams)*(len(teams)-1)//2, result=result)
        elif mode == 'prelim':
            from sim import prelim_runtime, new_prelim_totals, accumulate_prelim_rankings, compute_prelim_averages
            modes = params.get('formats', ['1v1', '2v2', '3v3'])
            totals = new_prelim_totals(modes)
            with prelim_runtime(params.get('workers', setting('PRELIM_WORKERS')), season):
                for i in range(params['n']):
                    tournament = ab.Tournament(tournament_type=0, enabled_modes=modes)
                    tournament.init_characters()
                    tournament.run_phases(write_results_flag=False)
                    accumulate_prelim_rankings(totals, tournament._rankings_info)
                    report(completed=i+1, result={'rankings': compute_prelim_averages(totals, i+1)})
        else:
            from sim import parallel_pairs
            a = b = rounds = timeouts = 0
            started = time.monotonic()
            char_season = season_statuses(t.char_map, season)
            for i, result in enumerate(parallel_pairs(t, params['team_a'], params['team_b'],
                    params.get('map_size', setting('DEFAULT_MAP_SIZE')), params['n'], char_season,
                    params.get('workers', min(8, __import__('os').cpu_count() or 1)), params['seed'])):
                wa = int(result['winner'] == 'Y')
                wb, avg, out = 1-wa, result.get('rounds', 0), bool(result.get('timeout'))
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
