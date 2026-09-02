from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fantaoperator.assistant import answer
from fantaoperator.database import Database


class DatabaseAssistantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "test.db")
        self.league = self.db.league(1)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_import_persists_and_detects_rectification(self) -> None:
        first = self.db.import_records(
            1, 3, [{"player": "Lautaro", "vote": 6, "goals": 1}],
            source_name=self.league["vote_provider"], source_url="https://example.test/voti.csv",
            payload_hash="hash-1", default_status="LIVE",
        )
        self.assertEqual(first["rows"], 1)
        record = self.db.records(1, 3)[0]
        self.assertEqual(record["fantavote"], 9)
        self.assertEqual(record["status"], "LIVE")

        second = self.db.import_records(
            1, 3, [{"player": "Lautaro", "vote": 6.5, "goals": 1, "status": "DEFINITIVO"}],
            source_name=self.league["vote_provider"], source_url="https://example.test/voti.csv",
            payload_hash="hash-2", default_status="DEFINITIVO",
        )
        self.assertEqual(second["changed"], 1)
        record = self.db.records(1, 3)[0]
        self.assertEqual(record["fantavote"], 9.5)
        self.assertEqual(record["status"], "DEFINITIVO")
        self.assertEqual(self.db.latest_sync(1)["payload_hash"], "hash-2")

    def test_rule_change_recalculates_existing_records(self) -> None:
        self.db.import_records(
            1, 3, [{"player": "Lautaro", "vote": 6, "goals": 1}],
            source_name=self.league["vote_provider"], source_url="", payload_hash="h", default_status="DEFINITIVO",
        )
        rules = dict(self.league["scoring"])
        rules["goal"] = 4
        self.db.save_league(1, {}, rules)
        self.assertEqual(self.db.records(1, 3)[0]["fantavote"], 10)

    def test_assistant_votes_and_lineup_commands(self) -> None:
        self.db.import_records(
            1, 3, [{"player": "Lautaro", "vote": 7, "goals": 1}],
            source_name=self.league["vote_provider"], source_url="", payload_hash="h", default_status="PROVVISORIO",
        )
        league = self.db.league(1)
        roster = self.db.roster(1)
        records = self.db.records(1, 3)
        latest = self.db.latest_sync(1)
        votes = answer("/VOTI", league=league, roster=roster, records=records, latest_sync=latest)
        lineup = answer("/FORMAZIONE", league=league, roster=roster, records=records, latest_sync=latest)
        self.assertIn("Lautaro", votes)
        self.assertIn("PROVVISORIO", votes)
        self.assertIn("Undici", lineup)


if __name__ == "__main__":
    unittest.main()
