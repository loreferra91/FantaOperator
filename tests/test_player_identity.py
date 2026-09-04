import json
import tempfile
import unittest
from pathlib import Path

from test_diretta_rosters import article

from fantaoperator.analytics import merge_player_catalog, possible_duplicate
from fantaoperator.database import Database
from fantaoperator.diretta_rosters import DIRETTA_ROSTERS_URL, parse_diretta_rosters
from fantaoperator.workspace import lineup_score


class PlayerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "identity.db")
        self.db.save_league(1, {"season": "2026-27", "vote_provider": "Gazzetta"}, {})
        self.directory = parse_diretta_rosters(article(), source_url=DIRETTA_ROSTERS_URL,
                                              season="2026-27").records
        attackers = [p for p in self.directory if p["team"] == "Roma" and p["role"] == "ATT"]
        attackers[0]["name"] = "Mario Rossi"
        attackers[1]["name"] = "Luca Bianchi"
        self.save_catalog()

    def save_catalog(self):
        self.db.replace_squad_catalog("2026-27", "Diretta.it", self.directory,
                                      source_url=DIRETTA_ROSTERS_URL, source_hash="test")

    def save_lineup(self):
        roles = ["POR"] + ["DIF"] * 4 + ["CEN"] * 3 + ["ATT"] * 3
        rows = [{"name": f"Giocatore {i}", "role": role, "team": "Roma"}
                for i, role in enumerate(roles)]
        rows[-1]["name"] = "Mario Rossi"
        rows.append({"name": "Luca Bianchi", "team": "Roma", "role": "ATT"})
        self.db.replace_roster(1, rows)
        roster = self.db.roster(1)
        self.db.save_lineup(1, 1, "4-3-3", [p["id"] for p in roster if p["name"] != "Luca Bianchi"],
                            [p["id"] for p in roster if p["name"] == "Luca Bianchi"])
        return self.db.saved_lineup(1, 1)

    def votes(self, *, team="Roma", missing_starter=False):
        self.db.import_records(1, 1, [
            {"player": "Rossi M.", "role": "ATT", "team": team, "vote": None if missing_starter else 7,
             "provider_player_id": "42"},
            {"player": "Bianchi L.", "role": "ATT", "team": "Roma", "vote": 6,
             "provider_player_id": "43"},
        ], source_name="Gazzetta", source_url="", payload_hash="votes", default_status="PROVVISORIO")

    def test_saved_starter_recovers_vote_without_resaving_or_next_sync(self):
        before = self.save_lineup()
        self.votes()
        saved = self.db.saved_lineup(1, 1)
        self.assertEqual(lineup_score(saved["players"], self.db.records(1, 1))["total"], 7)
        for field in ("players", "bench"):
            for old, new in zip(before[field], saved[field]):
                self.assertEqual({k: v for k, v in old.items() if k not in ("vote_provider", "provider_player_id")},
                                 {k: v for k, v in new.items() if k not in ("vote_provider", "provider_player_id")})
        self.assertEqual(saved["saved_at"], before["saved_at"])
        exported = json.loads(self.db.export_workspace(1))["lineups"][0]
        self.assertEqual(next(p for p in exported["players"] if p["name"] == "Mario Rossi")["provider_player_id"], "42")

    def test_sync_persists_bench_and_sold_players_and_survives_backup(self):
        before = self.save_lineup()
        self.db.replace_roster(1, [])
        self.votes(missing_starter=True)
        self.db.link_roster_to_votes(1)
        with self.db.connect() as db:
            persisted = db.execute("SELECT players_json,bench_json FROM saved_lineups WHERE league_id=1").fetchone()
            self.assertEqual(next(p for p in json.loads(persisted[0]) if p["name"] == "Mario Rossi")["provider_player_id"], "42")
            self.assertEqual(json.loads(persisted[1])[0]["provider_player_id"], "43")
        backup = self.db.export_workspace(1)
        snapshot = json.loads(backup)["lineups"][0]
        self.assertEqual(next(p for p in snapshot["players"] if p["name"] == "Mario Rossi")["provider_player_id"], "42")
        self.assertEqual(snapshot["bench"][0]["provider_player_id"], "43")
        self.assertEqual(snapshot["saved_at"], before["saved_at"])
        restored = Database(Path(self.temp.name) / "restored.db")
        restored.restore_workspace(1, backup.encode())
        saved = restored.saved_lineup(1, 1)
        score = lineup_score(saved["players"], self.db.records(1, 1), saved["bench"], 1)
        self.assertEqual(score["total"], 6)
        self.assertEqual(len(score["substitutions"]), 1)

    def test_sync_does_not_touch_other_seasons_or_existing_ids(self):
        self.save_lineup()
        with self.db.connect() as db:
            db.execute("""INSERT INTO saved_lineups
                SELECT league_id,'2025-26',matchday,formation,players_json,saved_at,bench_json
                FROM saved_lineups WHERE league_id=1 AND season='2026-27'""")
            old = db.execute("SELECT players_json FROM saved_lineups WHERE season='2025-26'").fetchone()[0]
            players = json.loads(old)
            players[-1].update(vote_provider="Other", provider_player_id="existing")
            db.execute("UPDATE saved_lineups SET players_json=? WHERE season='2026-27'", (json.dumps(players),))
        self.votes()
        self.db.link_roster_to_votes(1)
        with self.db.connect() as db:
            self.assertEqual(db.execute("SELECT players_json FROM saved_lineups WHERE season='2025-26'").fetchone()[0], old)
        self.assertEqual(self.db.saved_lineup(1, 1)["players"][-1]["provider_player_id"], "existing")

    def test_ambiguous_name_never_receives_a_vote(self):
        self.save_lineup()
        next(p for p in self.directory if p["name"] == "Luca Bianchi")["name"] = "Marco Rossi"
        self.save_catalog()
        self.votes()
        self.db.link_roster_to_votes(1)
        saved = self.db.saved_lineup(1, 1)
        mario = next(p for p in saved["players"] if p["name"] == "Mario Rossi")
        self.assertFalse(mario["provider_player_id"])
        self.assertIsNone(lineup_score(saved["players"], self.db.records(1, 1))["total"])

    def test_transfer_has_one_offer_without_assigning_unverified_votes(self):
        self.votes(team="Milan")
        offers = [p for p in self.db.complete_player_catalog(1)
                  if "Rossi" in p["name"]]
        self.assertEqual([(p["name"], p["team"], p["provider_player_id"]) for p in offers],
                         [("Rossi M.", "Milan", "42")])
        self.assertEqual(offers[0]["directory_provider"], "")
        self.assertTrue(any(p["name"] == "Mario Rossi" for p in self.db.catalog_players("2026-27")))

    def test_transfer_cannot_be_bought_twice_in_either_order(self):
        full = {"name": "Mario Rossi", "team": "Roma", "role": "ATT"}
        short = {"name": "Rossi M.", "team": "Milan", "role": "ATT",
                 "vote_provider": "Gazzetta", "provider_player_id": "42"}
        for first, second in ((full, short), (short, full)):
            with self.subTest(first=first["name"]):
                self.db.replace_roster(1, [])
                self.db.acquire_player(1, first, 10)
                before = self.db.export_workspace(1)
                with self.assertRaises(ValueError):
                    self.db.acquire_player(1, second, 12)
                self.assertEqual(self.db.export_workspace(1), before)
                with self.assertRaises(ValueError):
                    self.db.replace_roster(1, [first, second])
                self.assertEqual(self.db.export_workspace(1), before)

    def test_duplicate_exchange_and_self_exchange_are_atomic(self):
        self.db.replace_roster(1, [{"name": "Mario Rossi", "role": "ATT", "team": "Roma"},
                                  {"name": "Altro", "role": "ATT"}])
        incoming = {"name": "Rossi M.", "role": "ATT", "team": "Milan",
                    "vote_provider": "Gazzetta", "provider_player_id": "42"}
        for outgoing in self.db.roster(1):
            before = self.db.export_workspace(1)
            with self.assertRaises(ValueError):
                self.db.exchange_player(1, outgoing["id"], incoming, 1)
            self.assertEqual(self.db.export_workspace(1), before)

    def test_distinct_verified_ids_are_allowed_and_provider_is_part_of_id(self):
        first = {"name": "Mario Rossi", "role": "ATT", "team": "Roma",
                 "vote_provider": "Gazzetta", "provider_player_id": "42"}
        for provider, player_id in (("Gazzetta", "99"), ("Other", "42")):
            second = {"name": "Rossi M.", "role": "ATT", "team": "Milan",
                      "vote_provider": provider, "provider_player_id": player_id}
            self.assertFalse(possible_duplicate(first, second))
            self.db.replace_roster(1, [first])
            self.db.acquire_player(1, second, 1)
            self.assertEqual(len(self.db.roster(1)), 2)

    def test_same_id_alias_is_rejected_even_with_a_different_name(self):
        first = {"name": "Mario Rossi", "role": "ATT", "vote_provider": "Gazzetta", "provider_player_id": "42"}
        self.db.acquire_player(1, first, 1)
        with self.assertRaises(ValueError):
            self.db.acquire_player(1, {**first, "name": "Nome cambiato"}, 1)

    def test_matching_homonym_at_another_team_does_not_remove_directory_player(self):
        directory = [{"name": "Mario Rossi", "team": "Roma", "role": "ATT"},
                     {"name": "Marco Rossi", "team": "Milan", "role": "ATT"}]
        stats = [{"name": "Rossi M.", "team": "Milan", "role": "ATT",
                  "vote_provider": "Gazzetta", "provider_player_id": "42"}]
        result = merge_player_catalog(directory, stats)
        self.assertEqual(len(result), 2)
        self.assertFalse(next(p for p in result if p["name"] == "Mario Rossi")["provider_player_id"])
        self.assertEqual(next(p for p in result if p["name"] == "Marco Rossi")["provider_player_id"], "42")


if __name__ == "__main__":
    unittest.main()
