#!/usr/bin/env python3
"""Multi-round prelim average stats (sim.py --mode prelim) regression tests."""
import contextlib
import io
import logging
import os
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ai_battle opens its log file in 'w' mode at import; redirect it so user logs stay intact.
_orig_file_handler_init = logging.FileHandler.__init__
_LOG_REDIRECT = os.path.join(tempfile.gettempdir(), 'test_sim_prelim_ai_battle.log')


def _quiet_file_handler_init(self, filename, *args, **kwargs):
    if os.path.basename(str(filename)) == 'ai_battle.log':
        return _orig_file_handler_init(self, _LOG_REDIRECT, *args, **kwargs)
    return _orig_file_handler_init(self, filename, *args, **kwargs)


logging.FileHandler.__init__ = _quiet_file_handler_init
try:
    import ai_battle
    import sim
finally:
    logging.FileHandler.__init__ = _orig_file_handler_init


def _snap(pts, wins, battles, display, rank=1, key='Y1'):
    return (rank, key, pts, wins, battles, display)


class ParsePrelimArgsTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(sim.parse_prelim_args(['sim.py', '--mode', 'prelim']),
                         (10, 8, None, None, ['1v1', '2v2', '3v3']))

    def test_explicit_values_including_zero(self):
        argv = ['sim.py', '--mode', 'prelim', '-n', '3', '--workers', '1',
                '-s', '0', '--season', '0']
        self.assertEqual(sim.parse_prelim_args(argv),
                         (3, 1, 0, 0, ['1v1', '2v2', '3v3']))

    def test_format_single_and_multiple(self):
        for argv, expected in (
                (['--format', '1v1'], ['1v1']),
                (['--format', '3v3'], ['3v3']),
                (['--format', '1v1,3v3'], ['1v1', '3v3']),
                (['--format', '3v3+2v2'], ['2v2', '3v3']),
                (['--format', 'all'], ['1v1', '2v2', '3v3']),
                (['--format', '1v1', '--format', '2v2'], ['1v1', '2v2']),
                (['--format', '2v2', '--format', '1v1'], ['1v1', '2v2']),
        ):
            with self.subTest(argv=argv):
                modes = sim.parse_prelim_args(['sim.py', '--mode', 'prelim'] + argv)[4]
                self.assertEqual(modes, expected)

    def test_invalid_format_exits(self):
        for raw in ('4v4', '1v1,foo', ''):
            with self.subTest(raw=raw):
                with self.assertRaises(SystemExit):
                    sim.parse_prelim_args(['sim.py', '--mode', 'prelim', '--format', raw])

    def test_invalid_values_exit(self):
        for argv in (['-n', '0'], ['--workers', '0'], ['--season', '101'],
                     ['--season', '-1']):
            with self.assertRaises(SystemExit):
                sim.parse_prelim_args(['sim.py', '--mode', 'prelim'] + argv)

    def test_non_integer_value_exits(self):
        with self.assertRaises(SystemExit):
            sim.parse_prelim_args(['sim.py', '--mode', 'prelim', '-n', 'abc'])


class AccumulateTests(unittest.TestCase):
    def test_averages_and_cumulative_win_rate(self):
        totals = sim.new_prelim_totals()
        sim.accumulate_prelim_rankings(totals, {
            '1v1': [_snap(2.5, 1, 1, '名称(Y1)')],
            '2v2': [], '3v3': [],
        })
        sim.accumulate_prelim_rankings(totals, {
            '1v1': [_snap(1.5, 1, 3, '名称(Y1)')],
            '2v2': [], '3v3': [],
        })
        rows = sim.compute_prelim_averages(totals, 2)['1v1']
        display, avg_pts, avg_wins, avg_battles, win_rate, key, _order = rows[0]
        self.assertEqual(avg_pts, 2.0)
        self.assertEqual(avg_wins, 1.0)
        self.assertEqual(avg_battles, 2.0)
        self.assertAlmostEqual(win_rate, 50.0)  # 2/4 accumulated, not mean of 100%, 33.3%
        self.assertEqual(key, 'Y1')

    def test_timeout_points_are_kept(self):
        totals = sim.new_prelim_totals()
        sim.accumulate_prelim_rankings(totals, {'1v1': [_snap(2.13, 1, 1, 'x(Y1)')]})
        self.assertAlmostEqual(sim.compute_prelim_averages(totals, 1)['1v1'][0][1], 2.13)

    def test_team_identity_and_zero_battles(self):
        totals = sim.new_prelim_totals()
        rankings = {
            '1v1': [],
            '2v2': [_snap(3, 1, 1, 'A(Y1)+B(Y2)', rank=1, key=0),
                    _snap(0, 0, 0, 'C(Y3)+D(Y4)', rank=2, key=1)],
            '3v3': [_snap(1.5, 0, 1, 'A(Y1)+B(Y2)+C(Y3)', rank=1, key=0)],
        }
        sim.accumulate_prelim_rankings(totals, rankings)
        rows = sim.compute_prelim_averages(totals, 1)
        self.assertEqual(rows['2v2'][0][0], 'A(Y1)+B(Y2)')
        zero = rows['2v2'][1]
        self.assertEqual((zero[2], zero[3], zero[4]), (0.0, 0.0, 0.0))
        self.assertEqual(rows['3v3'][0][0], 'A(Y1)+B(Y2)+C(Y3)')

    def test_display_change_raises(self):
        totals = sim.new_prelim_totals()
        sim.accumulate_prelim_rankings(totals, {'1v1': [], '2v2': [
            _snap(3, 1, 1, 'A(Y1)+B(Y2)', key=0)], '3v3': []})
        with self.assertRaises(RuntimeError):
            sim.accumulate_prelim_rankings(totals, {'1v1': [], '2v2': [
                _snap(0, 0, 1, 'A(Y1)+C(Y3)', key=0)], '3v3': []})

    def test_ranking_and_tie_order(self):
        totals = sim.new_prelim_totals()
        totals['1v1']['Y9'] = {'key': 'Y9', 'display': 'n(Y9)', 'pts': 3.0,
                               'wins': 1, 'battles': 1}
        totals['1v1']['Y2'] = {'key': 'Y2', 'display': 'n(Y2)', 'pts': 3.0,
                               'wins': 2, 'battles': 2}
        totals['1v1']['Y7'] = {'key': 'Y7', 'display': 'n(Y7)', 'pts': 3.0,
                               'wins': 2, 'battles': 2}
        rows = sim.compute_prelim_averages(totals, 1)['1v1']
        # equal pts, higher wins first; exact tie keeps ALL_CHARACTERS order
        self.assertEqual([r[5] for r in rows], ['Y2', 'Y7', 'Y9'])


class RenderTests(unittest.TestCase):
    def _report(self):
        rankings = {
            '1v1': [('名称(Y1)', 246.4, 82.0, 82.0, 50.0, 'Y1', 0)],
            '2v2': [('甲(Y1)+乙(Y2)', 313.6, 62.6, 82.0, 76.4, 0, 0)],
            '3v3': [('甲(Y1)+乙(Y2)+丙(Y3)', 200.0, 40.0, 80.0, 50.0, 0, 0)],
        }
        report = sim.render_prelim_report(rankings, 10, 1850, 12.0, 8, 123, None)
        return report

    def test_formal_layout_and_headers(self):
        report = self._report()
        lines = report.split('\n')
        self.assertIn('预赛战绩预览 — 10 轮平均 (1v1, 2v2, 3v3)', lines[1])
        titles = [line for line in lines if line.startswith('预赛结束')]
        self.assertEqual(titles, ['预赛结束 — 1v1 积分排名',
                                  '预赛结束 — 2v2 积分排名',
                                  '预赛结束 — 3v3 积分排名'])
        self.assertIn('  1. Y1 ', report)  # 1v1 keeps formal "编号 名称"
        self.assertIn('  1. 甲(Y1)+乙(Y2)  ', report)
        self.assertIn('246.4分  82.0胜/82.0场  胜率50.0%', report)
        self.assertIn('313.6分  62.6胜/82.0场  胜率76.4%', report)
        self.assertIn('\n=' * 0 + '=' * 60, report)
        self.assertTrue(report.endswith('\n'))

    def test_report_written_verbatim(self):
        report = self._report()
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'out.txt')
            with mock.patch.object(sim, 'PRELIM_RESULT_FILE', path), \
                    mock.patch.object(sim, 'render_prelim_report', return_value=report), \
                    mock.patch.object(sim, 'Tournament', FakeTournament), \
                    mock.patch.object(sim, 'prelim_runtime',
                                      side_effect=lambda *a: contextlib.nullcontext()):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    sim.mode_prelim(['sim.py', '--mode', 'prelim', '-n', '1'])
            with open(path, encoding='utf-8') as f:
                self.assertEqual(f.read(), report)
        self.assertEqual(out.getvalue(), report)


class RuntimeRestoreTests(unittest.TestCase):
    NAMES = ('PRELIM_WORKERS', 'SEASON_AVG_STATUS', 'DELAY_PRELIM_TURN',
             'DELAY_PRELIM_BETWEEN_BATTLES', 'DELAY_STANDINGS_DISPLAY')

    def _assert_restored(self, saved, saved_level):
        for name, expected in saved.items():
            self.assertEqual(getattr(ai_battle, name), expected, name)
        self.assertEqual(ai_battle.logger.level, saved_level)

    def test_restored_after_success(self):
        saved = {name: getattr(ai_battle, name) for name in self.NAMES}
        saved_level = ai_battle.logger.level
        with sim.prelim_runtime(2, 30):
            self.assertEqual(ai_battle.PRELIM_WORKERS, 2)
            self.assertEqual(ai_battle.SEASON_AVG_STATUS, 30)
            self.assertEqual(ai_battle.DELAY_STANDINGS_DISPLAY, 0)
            self.assertEqual(ai_battle.logger.level, logging.WARNING)
        self._assert_restored(saved, saved_level)

    def test_restored_after_exception(self):
        saved = {name: getattr(ai_battle, name) for name in self.NAMES}
        saved_level = ai_battle.logger.level
        with self.assertRaises(ValueError):
            with sim.prelim_runtime(1, None):
                raise ValueError('boom')
        self._assert_restored(saved, saved_level)

    def test_restored_when_tournament_init_fails(self):
        saved = {name: getattr(ai_battle, name) for name in self.NAMES}
        saved_level = ai_battle.logger.level
        with mock.patch.object(sim, 'Tournament', side_effect=RuntimeError('init')), \
                mock.patch.object(sim, 'PRELIM_RESULT_FILE', 'unused.txt'):
            with self.assertRaises(RuntimeError):
                sim.mode_prelim(['sim.py', '--mode', 'prelim', '-n', '1'])
        self._assert_restored(saved, saved_level)


class FakeTournament:
    """Deterministic stand-in for Tournament(0) running the formal prelim path."""
    instances = []

    def __init__(self, tournament_type=1, run_times=1, enabled_modes=None):
        assert tournament_type == 0
        self.char_map = {}
        self.results = []
        self.enabled_modes = enabled_modes or ['1v1', '2v2', '3v3']
        self._rankings_info = {}
        FakeTournament.instances.append(self)

    def init_characters(self):
        pass

    def run_phases(self, write_results_flag=True):
        assert write_results_flag is False
        all_info = {
            '1v1': [_snap(3.0, 1, 1, '甲(Y1)', 1, 'Y1'), _snap(0.0, 0, 1, '乙(Y2)', 2, 'Y2')],
            '2v2': [_snap(1.7, 0, 1, '甲(Y1)+乙(Y2)', 1, 0)],
            '3v3': [_snap(2.0, 1, 1, '甲(Y1)+乙(Y2)+丙(Y3)', 1, 0)],
        }
        self.results = [object()] * (2 * len(self.enabled_modes))
        self._rankings_info = {mode: all_info[mode] for mode in self.enabled_modes}


class ModePrelimTests(unittest.TestCase):
    def test_multi_round_cli_report_and_file(self):
        FakeTournament.instances = []
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'result.txt')
            with mock.patch.object(sim, 'Tournament', FakeTournament), \
                    mock.patch.object(sim, 'PRELIM_RESULT_FILE', path):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    sim.mode_prelim(['sim.py', '--mode', 'prelim', '-n', '2',
                                     '--workers', '1', '-s', '7', '--season', '0'])
            with open(path, encoding='utf-8') as f:
                saved = f.read()
        self.assertEqual(out.getvalue(), saved)
        self.assertEqual(len(FakeTournament.instances), 2)
        self.assertIn('总场次: 12 (6.0/轮)', out.getvalue())
        y1_name = next(c['name'] for c in sim.ALL_CHARACTERS if c['serial'] == 'Y1')
        self.assertIn(f'1. Y1 {y1_name}  3.0分  1.0胜/1.0场  胜率100.0%', out.getvalue())
        self.assertIn('1. 甲(Y1)+乙(Y2)  1.7分  0.0胜/1.0场  胜率0.0%', out.getvalue())
        self.assertEqual(out.getvalue().count('预赛结束 — 1v1 积分排名'), 1)
        self.assertIn('[1/2] 完成: 6 场预赛', err.getvalue())
        self.assertIn('[2/2] 完成: 6 场预赛', err.getvalue())

    def test_single_mode_runs_only_that_format(self):
        for fmt in ('1v1', '2v2', '3v3'):
            with self.subTest(fmt=fmt):
                FakeTournament.instances = []
                with tempfile.TemporaryDirectory() as td:
                    path = os.path.join(td, 'result.txt')
                    with mock.patch.object(sim, 'Tournament', FakeTournament), \
                            mock.patch.object(sim, 'PRELIM_RESULT_FILE', path):
                        out, err = io.StringIO(), io.StringIO()
                        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                            sim.mode_prelim(['sim.py', '--mode', 'prelim', '-n', '1',
                                             '--format', fmt])
                    with open(path, encoding='utf-8') as f:
                        saved = f.read()
                report = out.getvalue()
                self.assertEqual(report, saved)
                self.assertEqual(FakeTournament.instances[0].enabled_modes, [fmt])
                titles = [l for l in report.split('\n') if l.startswith('预赛结束')]
                self.assertEqual(titles, [f'预赛结束 — {fmt} 积分排名'])
                self.assertIn(f'({fmt})', report.split('\n')[1])
                self.assertIn('总场次: 2 (2.0/轮)', report)

    def test_two_modes_run_both(self):
        FakeTournament.instances = []
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'result.txt')
            with mock.patch.object(sim, 'Tournament', FakeTournament), \
                    mock.patch.object(sim, 'PRELIM_RESULT_FILE', path):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    sim.mode_prelim(['sim.py', '--mode', 'prelim', '-n', '1',
                                     '--format', '1v1,3v3'])
            with open(path, encoding='utf-8') as f:
                saved = f.read()
        report = out.getvalue()
        self.assertEqual(report, saved)
        self.assertEqual(FakeTournament.instances[0].enabled_modes, ['1v1', '3v3'])
        titles = [l for l in report.split('\n') if l.startswith('预赛结束')]
        self.assertEqual(titles, ['预赛结束 — 1v1 积分排名',
                                  '预赛结束 — 3v3 积分排名'])
        self.assertNotIn('2v2 积分排名', report)
        self.assertIn('总场次: 4 (4.0/轮)', report)


if __name__ == '__main__':
    unittest.main()
