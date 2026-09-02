from __future__ import annotations

import unittest

from fantaoperator.engine import ScoringRules, calculate_fantavote, compare_players, optimize_lineup


class EngineTests(unittest.TestCase):
    def test_custom_fantavote_rules(self) -> None:
        rules = ScoringRules(goal=4, assist=1.5, yellow_card=-1, clean_sheet=1)
        result = calculate_fantavote(
            6.5, goals=1, assists=1, yellow_cards=1, clean_sheet=True,
            custom_bonus=0.5, custom_malus=0.25, rules=rules,
        )
        self.assertEqual(result, 12.25)

    def test_optimizer_returns_valid_eleven(self) -> None:
        players = []
        counts = {"POR": 2, "DIF": 6, "CEN": 7, "ATT": 5}
        for role, count in counts.items():
            for index in range(count):
                players.append({
                    "name": f"{role}{index}", "role": role, "expected": 6 + index / 10,
                    "start_probability": 80 + index, "risk": "Basso", "trend": index,
                })
        formation, selected, total = optimize_lineup(players)
        self.assertIn(formation, {"3-4-3", "3-5-2", "4-3-3", "4-4-2", "4-5-1", "5-3-2", "5-4-1"})
        self.assertEqual(len(selected), 11)
        self.assertGreater(total, 60)

    def test_compare_players_has_clear_verdict(self) -> None:
        weak = {"expected": 5.5, "start_probability": 60, "risk": "Alto", "trend": -5}
        strong = {"expected": 7.5, "start_probability": 95, "risk": "Basso", "trend": 10}
        result = compare_players(weak, strong)
        self.assertEqual(result["verdict"], "ACCETTA")
        self.assertGreater(result["delta"], 0)


if __name__ == "__main__":
    unittest.main()
