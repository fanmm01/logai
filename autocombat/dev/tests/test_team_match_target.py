import random
import unittest

from ai_battle import Tournament
from combat_settings import snapshot


class TeamMatchTargetTests(unittest.TestCase):
    def setUp(self):
        self.config = snapshot()
        self.tournament = Tournament(settings=self.config)

    def assert_team_target(self, team_count, rr_rounds, expected_bonus_degree, map_count=1):
        teams = [[f'T{i}'] for i in range(team_count)]
        for seed in range(10):
            with self.subTest(seed=seed, team_count=team_count):
                random.seed(seed)
                allocated = self.tournament._allocate_team_bonus_matches(
                    teams, rr_rounds, 82, map_count=map_count)
                degree = [0] * team_count
                for _, _, _, _, a, b in allocated:
                    degree[a] += 1
                    degree[b] += 1
                base = rr_rounds * (team_count - 1) * map_count
                self.assertEqual(len(allocated), team_count * expected_bonus_degree // 2)
                self.assertEqual(degree, [expected_bonus_degree] * team_count)
                self.assertEqual(base + min(degree), 82)

    def test_27_teams_receive_82_games(self):
        self.assert_team_target(27, 3, 4)

    def test_30_teams_receive_82_games(self):
        self.assert_team_target(30, 2, 24)

    def test_multiple_maps_are_counted_in_round_robin_base(self):
        self.assert_team_target(27, 1, 30, map_count=2)

    def test_odd_unpairable_deficit_is_rejected(self):
        teams = [[f'T{i}'] for i in range(27)]
        with self.assertRaisesRegex(ValueError, '无法由成对对局精确调配'):
            self.tournament._allocate_team_bonus_matches(teams, 3, 79)


if __name__ == '__main__':
    unittest.main()
