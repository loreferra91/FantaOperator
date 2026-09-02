from __future__ import annotations

import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from fantaoperator.assistant import respond
from fantaoperator.database import Database
from fantaoperator.engine import ScoringRules, calculate_fantavote
from fantaoperator.official_votes import normalize_rows, parse_votes
from fantaoperator.sources import fetch_url, safe_url
from fantaoperator.updater import import_votes, refresh_votes, run_due
from fantaoperator.league_scraper import fetch_private_export, read_authorized_session


def xlsx(rows, sheets=None):
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    for name in sheets or []:
        workbook.create_sheet(name)
    data = io.BytesIO()
    workbook.save(data)
    workbook.close()
    return data.getvalue()


class ParserTests(unittest.TestCase):
    def parse(self, payload, filename="votes.json", **kwargs):
        return parse_votes(payload, filename, "", provider="Fantacalcio.it", edition="Redazione Fantacalcio",
                           season="2026-27", matchday=3, **kwargs)

    def test_xlsx_repeated_team_headers_and_sv(self):
        payload = xlsx([
            ["Voti Fantacalcio 3ª giornata di campionato"], ["Roma"],
            ["Cod.", "Ruolo", "Nome", "Voto", "Gf", "Rp", "Amm"],
            [1, "A", "Rossi", 6.5, 1, 0, 1], [2, "C", "Bianchi", "S.V.", 0, 0, 0],
            [3, "ATT", "Allenatore", 6, 0, 0, 0],
            ["Milan"], ["Cod.", "Ruolo", "Nome", "Voto", "Gf", "Rp", "Amm"],
            [4, "P", "Verdi", 7, 0, 1, 0],
        ])
        batch = self.parse(payload, "Voti_Fantacalcio_Stagione_2026-27_Giornata_3.xlsx")
        self.assertEqual(len(batch.records), 3)
        self.assertEqual(batch.records[0]["role"], "ATT")
        self.assertEqual(batch.records[0]["team"], "Roma")
        self.assertIsNone(batch.records[1]["vote"])
        self.assertEqual(batch.records[2]["penalties_saved"], 1)
        self.assertTrue(batch.warnings)

    def test_wrong_matchday_in_filename(self):
        with self.assertRaisesRegex(ValueError, "matchday"):
            self.parse(xlsx([["Nome", "Voto"], ["Rossi", 7]]), "Giornata_2.xlsx")

    def test_ambiguous_xlsx_sheets_rejected(self):
        with self.assertRaisesRegex(ValueError, "più fogli"):
            self.parse(xlsx([["Nome", "Voto"], ["Rossi", 7]], ["Altra redazione"]), "votes.xlsx")

    def test_formula_rejected(self):
        with self.assertRaisesRegex(ValueError, "formule"):
            self.parse(xlsx([["Nome", "Voto"], ["Rossi", "=6+1"]]), "votes.xlsx")

    def test_ambiguous_penalty_column_rejected(self):
        with self.assertRaisesRegex(ValueError, "ambigua"):
            normalize_rows([{"player": "Rossi", "vote": 6, "rf": 1}])

    def test_duplicate_players_nan_fraction_negative_and_bad_status(self):
        for field, value in [("goals", -1), ("assists", 1.5), ("vote", "NaN"), ("vote", 11),
                             ("status", "FINALISSIMO"), ("clean_sheet", "forse")]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                normalize_rows([{"player": "Rossi", "vote": 6, field: value}])
        with self.assertRaisesRegex(ValueError, "duplicato"):
            normalize_rows([{"player": "Rossi", "vote": 6}, {"player": "rossi", "vote": 7}])

    def test_decimal_comma(self):
        batch = self.parse(b"player;vote;assists\nRossi;6,5;1\n", "votes.csv")
        self.assertEqual(batch.records[0]["vote"], 6.5)

    def test_duplicate_and_unknown_headers_rejected(self):
        for payload, filename in [
            (b"player,vote,vote\nRossi,6,9\n", "votes.csv"),
            (b"player,vote,voto\nRossi,6,9\n", "votes.csv"),
            (b'{"player":"Rossi","vote":6,"vote":9}', "votes.json"),
            (b'{"player":"Rossi","vote":6,"voto":9}', "votes.json"),
            (b'{"player":"Rossi","vote":6,"bonus_sconosciuto":5}', "votes.json"),
        ]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.parse(payload, filename)

    def test_ragged_csv_rejected(self):
        for payload in (b"player,vote\nRossi,6,1\n", b"player,vote\nRossi\n"):
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "colonne CSV"):
                self.parse(payload, "votes.csv")

    def test_json_bom_metadata_kept(self):
        data = {"provider":"Fantacalcio.it","edition":"Redazione Fantacalcio","season":"2026-27", "matchday":3,
                "records":[{"player":"Rossi","vote":7}]}
        self.assertEqual(self.parse(json.dumps(data).encode("utf-8-sig"), remote=True).records[0]["vote"], 7)

    def test_remote_requires_identity_metadata(self):
        with self.assertRaisesRegex(ValueError, "privo"):
            self.parse(b'[{"player":"Rossi","vote":7}]', remote=True)

    def test_penalty_scoring_not_double_counted_and_sv_not_zero(self):
        rules = ScoringRules(goal=4, penalty_scored=2, penalty_missed=-5)
        self.assertEqual(calculate_fantavote(6, goals=2, penalties_scored=1, penalties_missed=1, rules=rules), 7)
        self.assertIsNone(calculate_fantavote(None, goals=1))


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "collector.db")
        self.db.save_league(1, {"season": "2026-27", "source_url": "https://feed.example/{season}/{matchday}.json", "auto_sync_minutes": 5}, {})
        self.league = self.db.league(1)

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, vote=6, **changes):
        data = {"schema_version": 1, "provider": "Fantacalcio.it", "edition": "Redazione Fantacalcio",
                "season": "2026-27", "matchday": 3, "records": [{"player": "Rossi", "vote": vote, "goals": 1}]}
        data.update(changes)
        return json.dumps(data).encode()

    def imported(self, payload=None, **kwargs):
        return import_votes(self.db, self.league, 3, payload or self.payload(), "votes.json", **kwargs)

    def test_identical_payload_no_changes_and_sv_rectification(self):
        self.imported()
        self.assertEqual(self.imported()["changed"], 0)
        result = self.imported(self.payload(None))
        self.assertEqual(result["changed"], 1)
        self.assertIsNone(self.db.records(1, 3)[0]["fantavote"])

    def test_provider_and_edition_and_period_mismatch_rejected(self):
        for key, value in [("provider", "Gazzetta"), ("edition", "Voto Italia"), ("season", "2025-26"), ("matchday", 2)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.imported(self.payload(**{key: value}))
        self.assertEqual(self.db.records(1, 3), [])

    def test_season_and_edition_isolated_and_old_values_preserved(self):
        self.imported()
        self.db.save_league(1, {"season": "2027-28"}, {})
        self.assertEqual(self.db.records(1, 3), [])
        self.assertIsNone(self.db.latest_sync(1, 3))
        self.db.save_league(1, {"season": "2026-27", "vote_edition": "Voto Italia"}, {})
        self.assertEqual(self.db.records(1, 3), [])
        self.db.save_league(1, {"vote_edition": "Redazione Fantacalcio"}, {})
        self.assertEqual(self.db.records(1, 3)[0]["fantavote"], 9)

    def test_atomic_bad_batch_leaves_no_partial_write(self):
        self.imported()
        bad = self.payload(records=[{"player": "Nuovo", "vote": 7}, {"player": "Rossi", "vote": 99}])
        with self.assertRaises(ValueError):
            self.imported(bad)
        self.assertEqual(len(self.db.records(1, 3)), 1)
        self.assertEqual(self.db.records(1, 3)[0]["official_vote"], 6)

    def test_latest_sync_is_day_scoped(self):
        self.imported()
        self.assertIsNone(self.db.latest_sync(1, 2))

    def test_remote_failure_preserves_previous_and_does_not_claim_no_changes(self):
        self.imported()
        with patch("fantaoperator.updater.fetch_url", side_effect=ValueError("Fonte non accessibile")):
            response = respond(self.db, self.league, 3, "/AGGIORNAVOTI")
        self.assertIn("Non verificato adesso", response)
        self.assertIn("Nessun confronto nuovo", response)
        self.assertEqual(self.db.records(1, 3)[0]["official_vote"], 6)
        self.assertEqual(self.db.latest_sync(1, 3)["status"], "ERRORE")

    def test_vote_command_fetches_before_answer_and_natural_language(self):
        with patch("fantaoperator.updater.fetch_url", return_value=(self.payload(7), "application/json", "https://feed.example/2026-27/3.json")) as fetch:
            response = respond(self.db, self.league, 3, "Quali sono i voti?")
        fetch.assert_called_once_with("https://feed.example/2026-27/3.json")
        self.assertIn("10.0", response)
        self.assertEqual(self.db.latest_sync(1, 3)["provenance"], "FEED_CONFIGURATO")

    def test_worker_updates_when_due_then_obeys_interval(self):
        with patch("fantaoperator.updater.fetch_url", return_value=(self.payload(), "application/json", "https://feed.example/data.json")) as fetch:
            self.assertEqual(len(run_due(self.db)), 1)
            self.assertEqual(run_due(self.db), [])
            self.assertEqual(fetch.call_count, 1)

    def test_inflight_config_change_rejected(self):
        self.db.save_league(1, {"season": "2027-28"}, {})
        with self.assertRaisesRegex(ValueError, "Configurazione modificata"):
            self.imported()

    def test_mixed_status_not_final_and_null_kept(self):
        self.imported(self.payload(records=[{"player": "Rossi", "vote": 7, "status": "DEFINITIVO"},
                                           {"player": "Bianchi", "vote": None, "status": "LIVE"}]))
        self.assertEqual(self.db.latest_sync(1, 3)["status"], "LIVE")

    def test_error_audit_has_no_signed_query(self):
        self.db.save_league(1, {"source_url": "https://feed.example/data?token=TOPSECRET"}, {})
        league = self.db.league(1)
        with patch("fantaoperator.updater.fetch_url", side_effect=RuntimeError("TOPSECRET")):
            result = refresh_votes(self.db, league, 3)
        self.assertNotIn("TOPSECRET", json.dumps(result))
        self.assertNotIn("TOPSECRET", json.dumps(self.db.latest_sync(1, 3)))

    def test_records_persist_across_database_reopen(self):
        self.imported()
        reopened = Database(self.db.path)
        self.assertEqual(reopened.records(1, 3), self.db.records(1, 3))
        self.assertEqual(reopened.latest_sync(1, 3), self.db.latest_sync(1, 3))

    def test_inflight_url_change_rejected(self):
        def changed_url(_url):
            self.db.save_league(1, {"source_url":"https://new.example/data.json"}, {})
            return self.payload(), "application/json", "https://feed.example/data.json"
        with patch("fantaoperator.updater.fetch_url", side_effect=changed_url):
            result = refresh_votes(self.db, self.league, 3)
        self.assertFalse(result["ok"])
        self.assertIn("URL del feed modificato", result["error"])
        self.assertEqual(self.db.records(1, 3), [])
        self.assertIsNone(self.db.latest_sync(1, 3))

    def test_manual_import_waits_for_inflight_download(self):
        fetching, release, importing = Event(), Event(), Event()
        def fetch(_url):
            fetching.set()
            if not release.wait(5):
                raise TimeoutError()
            return self.payload(6), "application/json", "https://feed.example/data.json"
        def local_import():
            importing.set()
            return self.imported(self.payload(8))
        with patch("fantaoperator.updater.fetch_url", side_effect=fetch), ThreadPoolExecutor(2) as pool:
            remote = pool.submit(refresh_votes, self.db, self.league, 3)
            self.assertTrue(fetching.wait(5))
            local = pool.submit(local_import)
            self.assertTrue(importing.wait(5))
            release.set()
            self.assertTrue(remote.result(timeout=5)["ok"])
            self.assertEqual(local.result(timeout=5)["changed"], 1)
        self.assertEqual(self.db.records(1, 3)[0]["official_vote"], 8)


class AccessTests(unittest.TestCase):
    def test_private_addresses_blocked(self):
        for url in ("http://127.0.0.1/data", "http://[::1]/data", "http://169.254.169.254/latest"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetch_url(url)

    def test_cookie_never_sent_to_third_party(self):
        with patch("fantaoperator.sources.validate_url"), self.assertRaisesRegex(ValueError, "domini esatti"):
            fetch_url("https://evil.example/data", cookie="secret=REDACTED")

    def test_league_adapter_rejects_wrong_host_before_reading_session(self):
        with patch("fantaoperator.league_scraper.read_authorized_session") as session:
            with self.assertRaises(ValueError):
                fetch_private_export("https://leghe.fantacalcio.it.evil.example/", league_slug="test", season="2026-27", matchday=3)
            session.assert_not_called()

    def test_missing_session_explicit(self):
        with patch.dict("os.environ", {"FANTACALCIO_COOKIE": ""}), self.assertRaisesRegex(ValueError, "non configurata"):
            read_authorized_session()

    def test_safe_url_removes_secrets(self):
        self.assertEqual(safe_url("https://user:password@example.test/votes?token=secret#key"), "https://example.test/votes")


if __name__ == "__main__":
    unittest.main()
