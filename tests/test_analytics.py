import unittest

from fantaoperator.analytics import season_statistics


class SeasonStatisticsTests(unittest.TestCase):
    def test_aggregates_identity_and_keeps_missing_votes_out_of_averages(self):
        rows = []
        for day, fv in enumerate((6.0, 7.0, None, 8.0), start=1):
            rows.append({'name':'Giocatore','team':'Roma','role':'ATT','source_name':'Gazzetta',
                         'provider_player_id':'42','matchday':day,'official_vote':fv,
                         'fantavote':fv,'provider_fantavote':fv,'goals':day == 4,'assists':day == 2})
        result = season_statistics(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['appearances'], 3)
        self.assertEqual(result[0]['days_present'], 4)
        self.assertEqual(result[0]['average_fantavote'], 7.0)
        self.assertEqual(result[0]['goals'], 1)
        self.assertEqual(result[0]['assists'], 1)
