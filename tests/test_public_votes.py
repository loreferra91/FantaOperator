"""Synthetic HTML contract tests, no copied provider dataset or network in CI."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fantaoperator.assistant import respond
from fantaoperator.database import Database
from fantaoperator.engine import ScoringRules, calculate_fantavote
from fantaoperator.official_votes import EDITIONS
from fantaoperator.public_votes import (
    BONUSES, PUBLIC_VOTES_URL, is_public_votes_url, parse_public_votes, public_votes_url,
)
from fantaoperator.updater import refresh_votes


def fixture(votes=("6,5", "7", "6"), card="yellow-card", events=None, season="2026/27", day=2):
    headers = "".join(f'<img title="{name}">' for name in (*EDITIONS, EDITIONS[0]))
    grades = "".join(f'<span class="player-grade {card}" data-value="{value}"></span>' for value in votes)
    events = events or {"Gol segnati": 1, "Rigori segnati": 1}
    bonuses = "".join(f'<span class="player-bonus" title="{key}" data-value="{events.get(key, 0)}"></span>'
                      for key in (*BONUSES, "Player of the match"))
    return f'''<!doctype html><html><body><!-- Season: {season} --><!-- Matchweek: {day} -->
    <table class="grades-table"><thead><tr><th><a class="team-name">Squadra esempio</a>{headers}</th></tr></thead>
    <tbody><tr><td><span class="role" data-value="a"></span><a class="player-name">Esempio &amp; Test</a></td>
    <td>{grades}</td><td>{bonuses}</td></tr>
    <tr><td><span class="role" data-value="all"></span><span class="player-name">Allenatore</span></td></tr>
    </tbody></table></body></html>'''.encode()


class PublicParserTests(unittest.TestCase):
    def parse(self, payload=None, **changes):
        kwargs = dict(source_url=PUBLIC_VOTES_URL, provider="Fantacalcio.it", edition=EDITIONS[0], season="2026-27", matchday=2)
        kwargs.update(changes)
        return parse_public_votes(payload or fixture(), **kwargs)

    def test_editions_penalties_cards_and_coaches(self):
        for edition, expected in zip(EDITIONS, (6.5, 7, 6)):
            row, = self.parse(edition=edition).records
            self.assertEqual(row["vote"], expected)
            self.assertEqual(row["player"], "Esempio & Test")
            self.assertEqual(row["goals"], 2)  # 1 open-play + 1 penalty.
            self.assertEqual(row["penalties_scored"], 1)
            self.assertEqual(row["yellow_cards"], 1)
            self.assertEqual(row["status"], "PROVVISORIO")
            self.assertIsNone(row["clean_sheet"])
        row = self.parse(fixture(card="red-card")).records[0]
        self.assertEqual((row["yellow_cards"], row["red_cards"]), (0, 1))

    def test_special_provider_codes_do_not_invent_administrative_vote(self):
        for code in ("55", "56", "S.V.", "-"):
            self.assertIsNone(self.parse(fixture(votes=(code, "7", "6"))).records[0]["vote"])
        with self.assertRaisesRegex(ValueError, "intervallo"):
            self.parse(fixture(votes=("57", "7", "6")))

    def test_context_source_and_edition_fail_closed(self):
        for kwargs in ({"matchday": 3}, {"season": "2025-26"}, {"edition": "Gazzetta"},
                       {"provider": "Altro"}, {"source_url": "https://evil.example/voti-fantacalcio-serie-a"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.parse(**kwargs)

    def test_headers_missing_votes_bonus_unknown_cards_and_login_rejected(self):
        payload = fixture()
        for broken in (
            payload.replace(b'Redazione Fantacalcio', b'Altra redazione'),
            payload.replace(b'class="player-grade yellow-card"', b'class="unavailable"', 1),
            payload.replace(b'title="Assist"', b'title="Bonus nuovo"'),
            payload.replace(b'title="Assist"', b'title="Gol segnati"'),
            payload.replace(b'data-value="6,5"', b'data-value=""'),
            payload.replace(b'yellow-card', b'unknown-card'),
            b'<html><body>Login</body></html>',
        ):
            with self.subTest(payload=broken[:60]), self.assertRaises(ValueError):
                self.parse(broken)

    def test_header_order_is_used_not_assumed(self):
        payload = fixture().replace(b'Voto Statistico', b'TEMP').replace(b'Redazione Fantacalcio', b'Voto Statistico', 1).replace(b'TEMP', b'Redazione Fantacalcio')
        self.assertEqual(self.parse(payload).records[0]["vote"], 7)

    def test_exact_public_urls(self):
        self.assertEqual(public_votes_url("2026/27", 2), PUBLIC_VOTES_URL + "/2026-27/2")
        self.assertTrue(is_public_votes_url(PUBLIC_VOTES_URL))
        self.assertTrue(is_public_votes_url(PUBLIC_VOTES_URL + "/2026-27/2"))
        for url in (PUBLIC_VOTES_URL.replace("https:", "http:"), PUBLIC_VOTES_URL + "?cookie=x",
                    PUBLIC_VOTES_URL.replace(".it/", ".it.evil.test/"), PUBLIC_VOTES_URL + "/login",
                    "https://user:pass@www.fantacalcio.it/voti-fantacalcio-serie-a", "https://[broken"):
            self.assertFalse(is_public_votes_url(url))

    def test_unknown_clean_sheet_with_configured_bonus_is_unscored(self):
        self.assertEqual(calculate_fantavote(7, clean_sheet=None), 7)
        self.assertIsNone(calculate_fantavote(7, clean_sheet=None, rules=ScoringRules(clean_sheet=1)))


class PublicCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "public.db")
        self.db.save_league(1, {"source_url": PUBLIC_VOTES_URL, "season": "2026-27"}, {})
        self.league = self.db.league(1)

    def tearDown(self):
        self.temp.cleanup()

    def fetch(self, payload=None, final_url=None):
        return patch("fantaoperator.updater.fetch_url", return_value=(payload or fixture(), "text/html", final_url or public_votes_url("2026-27", 2)))

    def test_remote_import_rectification_and_provenance(self):
        with self.fetch() as fetch:
            first = refresh_votes(self.db, self.league, 2)
            repeat = refresh_votes(self.db, self.league, 2)
        self.assertTrue(first["ok"], first)
        self.assertEqual(repeat["changed"], 0)
        fetch.assert_called_with(PUBLIC_VOTES_URL + "/2026-27/2", allow_html=True)
        self.assertEqual(self.db.records(1, 2)[0]["fantavote"], 12)
        self.assertEqual(self.db.latest_sync(1, 2)["provenance"], "PAGINA_UFFICIALE")
        with self.fetch(fixture(votes=("7", "7", "6"))):
            text = respond(self.db, self.league, 2, "/AGGIORNAVOTI")
        self.assertIn("6.5 → 7.0", text)
        self.assertIn("Pagina pubblica Fantacalcio.it", text)
        self.assertEqual(self.db.records(1, 2)[0]["fantavote"], 12.5)

    def test_wrong_day_redirect_and_inflight_change_keep_previous(self):
        with self.fetch():
            self.assertTrue(refresh_votes(self.db, self.league, 2)["ok"])
        for payload, url in ((fixture(day=1), None), (fixture(), PUBLIC_VOTES_URL), (b'<html>Login</html>', None)):
            with self.fetch(payload, url):
                result = refresh_votes(self.db, self.league, 2)
            self.assertFalse(result["ok"])
            self.assertEqual(self.db.records(1, 2)[0]["fantavote"], 12)
            self.assertEqual(self.db.latest_sync(1, 2)["status"], "ERRORE")
        self.db.save_league(1, {"source_url": "https://other.example/export.json"}, self.league["scoring"])
        with self.fetch():
            self.assertFalse(refresh_votes(self.db, self.league, 2)["ok"])

    def test_rules_recalculation_keeps_missing_clean_sheet_unknown(self):
        with self.fetch():
            refresh_votes(self.db, self.league, 2)
        self.db.save_league(1, {}, {"goal": 4, "penalty_scored": 2})
        self.assertEqual(self.db.records(1, 2)[0]["fantavote"], 12)
        self.db.save_league(1, {}, {"clean_sheet": 1})
        self.assertIsNone(self.db.records(1, 2)[0]["fantavote"])
        self.assertEqual(self.db.records(1, 2)[0]["official_vote"], 6.5)


if __name__ == "__main__":
    unittest.main()
