"""Playoff topology, group schedules, qualification and per-round BO contracts."""
import copy
from collections import Counter
import random
import unittest

from combat_defaults import DEFAULTS
from tournament_playoffs import (DEFAULT_CONFIG, FORMATS, build, finished, group_tables,
                                 participant, qualifiers, settle_reset, validate_config)
from ui_tournaments import bracket, ready


def fixture(fmt, seed=1, draw='ranked', reset=False):
    config = dict(copy.deepcopy(DEFAULT_CONFIG), format=fmt, draw=draw, reset_final=reset)
    ranked = list(range(18 if fmt == 'nba16' else FORMATS[fmt][1]))
    return dict(seed=seed, playoff=config, settings=copy.deepcopy(DEFAULTS),
                standings=[dict(id=i, points=100-i, wins=1) for i in ranked],
                **build(ranked, seed, DEFAULTS, config))


def win(s, a, b, side):
    s['score'][side] = s['best_of']//2+1
    s.update(winner=(a, b)[side], loser=(b, a)[side])


class PlayoffTests(unittest.TestCase):
    def test_all_formats_finish_without_deadlocks_or_reentry(self):
        for fmt in FORMATS:
            for draw in ('ranked', 'tiered', 'random', *(['weighted'] if fmt == 'playin12' else [])):
                for seed in range(8):
                    for reset in (False, True):
                        with self.subTest(fmt=fmt, draw=draw, seed=seed, reset=reset):
                            t = fixture(fmt, seed, draw, reset)
                            rng, losses = random.Random(seed), Counter()
                            ids = [s['id'] for s in t['series']]
                            self.assertEqual(len(ids), len(set(ids)))
                            for _ in range(len(ids)):
                                available = ready(t)
                                if not available:
                                    break
                                s = rng.choice(available)
                                a, b = participant(t, s['a']), participant(t, s['b'])
                                self.assertNotEqual(a, b)
                                if not s.get('group') and fmt != 'nba16':
                                    for team in (a, b):
                                        limit = 2 if fmt == 'double16' or fmt == 'hybrid12' and team < 8 else 1
                                        if s['id'] != 'BRONZE':
                                            self.assertLess(losses[team], limit)
                                win(s, a, b, rng.randrange(2))
                                if not s.get('group') and fmt != 'nba16' and s['id'] != 'BRONZE':
                                    losses[s['loser']] += 1
                                settle_reset(t)
                            self.assertTrue(all(finished(s) for s in t['series']))

    def test_double_round_robin_counts_and_unique_round_participation(self):
        for fmt, count, rounds, size in [('groups16', 4, 6, 4), ('playin18', 1, 6, 4), ('groups18', 3, 10, 6)]:
            t = fixture(fmt, draw='tiered')
            self.assertEqual(len(t['groups']), count)
            for g in t['groups']:
                matches = [s for s in t['series'] if s.get('group') == g['id']]
                self.assertEqual(len(matches), size*(size-1))
                self.assertEqual(Counter((s['a'], s['b']) for s in matches),
                                 Counter((a, b) for a in g['teams'] for b in g['teams'] if a != b))
                for r in range(1, rounds+1):
                    teams = [s[k] for s in matches if s['round'] == f'G{r}' for k in ('a', 'b')]
                    self.assertEqual(set(teams), set(g['teams']))
                    self.assertEqual(len(teams), size)

    def test_qualification_waits_for_all_groups_and_avoids_rematches(self):
        for fmt in ('groups16', 'groups18', 'playin18'):
            for draw in ('random', 'tiered', 'ranked'):
                t = fixture(fmt, draw=draw)
                self.assertIsNone(qualifiers(t))
                self.assertTrue(all(s.get('group') for s in ready(t)))
                for s in t['series']:
                    if s.get('group'):
                        win(s, s['a'], s['b'], int(s['a'] > s['b']))
                q = qualifiers(t)
                self.assertEqual(len(q), 16 if fmt == 'playin18' else 8)
                self.assertEqual(len(q), len(set(q)))
                if fmt == 'playin18':
                    self.assertTrue(set(range(14)).issubset(q))
                else:
                    groups = {team: g['id'] for g in t['groups'] for team in g['teams']}
                    for s in ready(t):
                        self.assertNotEqual(groups[participant(t, s['a'])], groups[participant(t, s['b'])])
                tables = group_tables(t)
                self.assertTrue(all(g['complete'] for g in tables))
                self.assertTrue(all(set(r['id'] for r in g['rows'][:2]).issubset(q) for g in tables))
                if fmt == 'groups18':
                    thirds = [g['rows'][2] for g in tables]
                    thirds.sort(key=lambda r: (-r['wins'], -(r['game_wins']-r['game_losses']), -r['game_wins'], r['id']))
                    self.assertTrue(set(r['id'] for r in thirds[:2]).issubset(q))

    def test_bo_defaults_and_independent_namespaced_overrides(self):
        for fmt in FORMATS:
            t = fixture(fmt)
            t['settings'].update(BO_N_PLAYIN=3, BO_N_QF=5, BO_N_SF=7, BO_N_FINAL=9, BO_N_BRONZE=11)
            t['series'] = bracket(t)
            self.assertEqual(t['series'][0]['best_of'], 3)
            self.assertEqual(next(s for s in t['series'] if s['id'] == 'FINAL')['best_of'], 9)
            if fmt not in ('hybrid12', 'double16'):
                self.assertEqual(next(s for s in t['series'] if s['id'] == 'BRONZE')['best_of'], 9)
            first = t['series'][0]['round']
            before = copy.deepcopy(t['series'])
            t['playoff']['round_bo'][fmt + '/' + first] = 13
            after = bracket(t)
            for a, b in zip(before, after):
                self.assertEqual(b['best_of'], 13 if b['round'] == first else a['best_of'])
            validate_config(t['playoff'])

    def test_reset_only_runs_after_lower_champion_wins(self):
        for side in (0, 1):
            t = fixture('double16', reset=True)
            while True:
                s = ready(t)[0]
                if s['id'] == 'FINAL':
                    break
                win(s, participant(t, s['a']), participant(t, s['b']), 0)
            win(s, participant(t, s['a']), participant(t, s['b']), side)
            settle_reset(t)
            self.assertEqual([s['id'] for s in ready(t)], ['RESET'] if side else [])
            self.assertEqual(t['series'][-1].get('skipped', False), side == 0)

    def test_nba_mode_divides_eighteen_and_runs_each_three_into_two_playin(self):
        for draw in ('random', 'tiered', 'ranked'):
            with self.subTest(draw=draw):
                t = fixture('nba16', draw=draw)
                self.assertEqual([len(side) for side in t['nba_halves']], [9, 9])
                self.assertEqual(set(t['nba_halves'][0]) | set(t['nba_halves'][1]), set(range(18)))
                self.assertFalse(set(t['nba_halves'][0]) & set(t['nba_halves'][1]))
                if draw == 'ranked':
                    self.assertEqual(t['nba_halves'], [list(range(0, 18, 2)), list(range(1, 18, 2))])
                self.assertEqual(len(ready(t)), 2)
                self.assertEqual({s['id'] for s in ready(t)}, {'PIA78', 'PIB78'})
                for gid in ('A', 'B'):
                    first = next(s for s in t['series'] if s['id'] == f'PI{gid}78')
                    win(first, first['a'], first['b'], 0)
                    second = next(s for s in t['series'] if s['id'] == f'PI{gid}9')
                    self.assertEqual({participant(t, second['a']), participant(t, second['b'])},
                                     {t['nba_halves'][0 if gid == 'A' else 1][8], first['loser']})
                self.assertIsNone(qualifiers(t))
                for gid in ('A', 'B'):
                    second = next(s for s in t['series'] if s['id'] == f'PI{gid}9')
                    win(second, participant(t, second['a']), participant(t, second['b']), 0)
                qualified = qualifiers(t)
                self.assertEqual(len(qualified), 16)
                self.assertEqual(len(set(qualified)), 16)
                self.assertEqual(len(ready(t)), 8)
                for s in ready(t):
                    self.assertIsNotNone(participant(t, s['a']))
                    self.assertIsNotNone(participant(t, s['b']))

    def test_nba_mode_adapts_to_even_odd_and_four_team_playin_fields(self):
        for count in range(16, 23):
            with self.subTest(teams=count):
                config = dict(copy.deepcopy(DEFAULT_CONFIG), format='nba16', draw='ranked')
                t = dict(seed=5, playoff=config, settings=copy.deepcopy(DEFAULTS),
                         standings=[dict(id=i, points=count-i, wins=0) for i in range(count)],
                         **build(list(range(count)), 5, DEFAULTS, config))
                self.assertEqual(sum(map(len, t['nba_halves'])), count)
                while ready(t) and any(s.get('conference') for s in ready(t)):
                    for s in ready(t):
                        if s.get('conference'):
                            win(s, participant(t, s['a']), participant(t, s['b']), 0)
                qualified = qualifiers(t)
                self.assertEqual(len(qualified), 16)
                self.assertEqual(len(set(qualified)), 16)
                self.assertEqual(len([s for s in t['series'] if s.get('conference')]),
                                 (0 if count == 16 else 2 if count == 17 else 4 if count == 18 else 5 if count == 19 else 6))
                self.assertEqual(len(ready(t)), 8)

    def test_validation_and_seed_reproducibility(self):
        for changes in (dict(draw='bad'), dict(format='bad'), dict(display='bad'), dict(reset_final=1),
                        dict(round_bo={'playin12/PI': 2}), dict(round_bo={'playin12/NO': 3}),
                        dict(format='groups18', draw='weighted')):
            with self.assertRaises(ValueError):
                validate_config(dict(copy.deepcopy(DEFAULT_CONFIG), **changes))
        for fmt in FORMATS:
            self.assertEqual(fixture(fmt, 17, 'random'), fixture(fmt, 17, 'random'))
            self.assertNotEqual(fixture(fmt, 17, 'random')['series'], fixture(fmt, 18, 'random')['series'])


if __name__ == '__main__':
    unittest.main()
