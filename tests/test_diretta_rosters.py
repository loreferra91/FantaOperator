import tempfile
import unittest
from pathlib import Path

from fantaoperator.database import Database
from fantaoperator.diretta_rosters import (
    DIRETTA_ROSTERS_URL, EXPECTED_TEAMS, PROVIDER, is_diretta_rosters_url, parse_diretta_rosters,
)
from fantaoperator.analytics import merge_player_catalog


def article(*, omit_team=None, omit_role=None, title="Serie A 2026/27, tutte le rose complete: le 20 squadre divise per ruolo"):
    blocks = []
    labels = (("Portieri", "POR"), ("Difensori", "DIF"), ("Centrocampisti", "CEN"), ("Attaccanti", "ATT"))
    for team in sorted(EXPECTED_TEAMS):
        if team == omit_team:
            continue
        blocks.append(f"<h2>{team}</h2>")
        for label, role in labels:
            if omit_role == (team, role):
                continue
            names = ", ".join(f"Nome {team} {role} {index}" for index in range(5))
            blocks.append(f"<p>{label}: {names}</p>")
    return (f"<html><head><title>{title}</title></head><body><h1>{title}</h1>"
            '<div itemProp="dateModified" data-content="2026-09-03T18:30:54.000Z"></div>'
            f'<div itemProp="articleBody" data-testid="fp-newsArticle-body">{"".join(blocks)}</div>'
            "</body></html>").encode()


class DirettaRosterTests(unittest.TestCase):
    def test_exact_url_and_complete_contract(self):
        self.assertTrue(is_diretta_rosters_url(DIRETTA_ROSTERS_URL))
        self.assertTrue(is_diretta_rosters_url(DIRETTA_ROSTERS_URL.rstrip("/")))
        for bad in (DIRETTA_ROSTERS_URL + "?x=1", DIRETTA_ROSTERS_URL.replace("https", "http"),
                    DIRETTA_ROSTERS_URL.replace("www.diretta.it", "diretta.it")):
            self.assertFalse(is_diretta_rosters_url(bad))
        batch = parse_diretta_rosters(article(), source_url=DIRETTA_ROSTERS_URL, season="2026-27")
        self.assertEqual(len(batch.records), 400)
        self.assertEqual({row["team"] for row in batch.records}, EXPECTED_TEAMS)
        self.assertEqual({row["role"] for row in batch.records}, {"POR", "DIF", "CEN", "ATT"})
        self.assertEqual(batch.article_updated_at, "2026-09-03T18:30:54.000Z")

    def test_wrong_season_missing_team_or_role_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "stagione 2026-27"):
            parse_diretta_rosters(article(), source_url=DIRETTA_ROSTERS_URL, season="2025-26")
        with self.assertRaisesRegex(ValueError, "squadre mancanti"):
            parse_diretta_rosters(article(omit_team="Roma"), source_url=DIRETTA_ROSTERS_URL, season="2026-27")
        with self.assertRaisesRegex(ValueError, "ruoli mancanti per Roma"):
            parse_diretta_rosters(article(omit_role=("Roma", "ATT")), source_url=DIRETTA_ROSTERS_URL, season="2026-27")

    def test_duplicate_is_removed_and_reported(self):
        payload = article().replace(b"Nome Roma ATT 4</p>", b"Nome Roma ATT 4, Nome Roma ATT 4</p>")
        batch = parse_diretta_rosters(payload, source_url=DIRETTA_ROSTERS_URL, season="2026-27")
        self.assertEqual(len(batch.records), 400)
        self.assertEqual(len(batch.warnings), 1)

    def test_replacement_is_atomic_and_audited(self):
        batch = parse_diretta_rosters(article(), source_url=DIRETTA_ROSTERS_URL, season="2026-27")
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "catalog.db")
            result = db.replace_squad_catalog("2026-27", PROVIDER, batch.records,
                source_url=DIRETTA_ROSTERS_URL, source_hash="abc", article_updated_at=batch.article_updated_at)
            self.assertEqual(result["players"], 400)
            with self.assertRaisesRegex(ValueError, "incompleto"):
                db.replace_squad_catalog("2026-27", PROVIDER, batch.records[:10],
                    source_url=DIRETTA_ROSTERS_URL, source_hash="bad")
            self.assertEqual(len(db.catalog_players("2026-27")), 400)
            self.assertEqual(db.latest_squad_sync("2026-27", PROVIDER)["source_hash"], "abc")

    def test_full_names_link_to_unique_gazzetta_abbreviations(self):
        directory = [
            {"name": "Charles De Ketelaere", "team": "Atalanta", "role": "ATT", "provider": PROVIDER},
            {"name": "Gleison Bremer", "team": "Juventus", "role": "DIF", "provider": PROVIDER},
            {"name": "Rasmus Højlund", "team": "Napoli", "role": "ATT", "provider": PROVIDER},
        ]
        statistics = [
            {"name": "De Ketelaere C.", "team": "atalanta", "role": "CEN", "vote_provider": "Gazzetta", "provider_player_id": "1"},
            {"name": "Bremer", "team": "juventus", "role": "DIF", "vote_provider": "Gazzetta", "provider_player_id": "2"},
            {"name": "Hojlund R.", "team": "napoli", "role": "ATT", "vote_provider": "Gazzetta", "provider_player_id": "3"},
        ]
        merged = merge_player_catalog(directory, statistics)
        self.assertEqual({row["provider_player_id"] for row in merged}, {"1", "2", "3"})
        self.assertEqual({row["name"] for row in merged}, {"Charles De Ketelaere", "Gleison Bremer", "Rasmus Højlund"})

    def test_ambiguous_abbreviation_is_not_linked(self):
        directory = [
            {"name": "Mario Rossi", "team": "Roma", "role": "CEN", "provider": PROVIDER},
            {"name": "Marco Rossi", "team": "Roma", "role": "CEN", "provider": PROVIDER},
        ]
        stat = {"name": "Rossi M.", "team": "Roma", "role": "CEN", "vote_provider": "Gazzetta", "provider_player_id": "1"}
        merged = merge_player_catalog(directory, [stat])
        self.assertTrue(all(not row["provider_player_id"] for row in merged))


if __name__ == "__main__":
    unittest.main()
