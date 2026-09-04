from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .engine import ScoringRules
from .vote_store import VoteStore
from .official_votes import season_name
from .workspace import WorkspaceStore, validate_league


SEED_PLAYERS = [
    ("Maignan", "POR", "Milan", 6.2, 98, "Basso", 27, "A", 2),
    ("Provedel", "POR", "Lazio", 5.8, 93, "Basso", 18, "B", -1),
    ("Bastoni", "DIF", "Inter", 6.0, 95, "Basso", 18, "A", 5),
    ("Bremer", "DIF", "Juventus", 5.8, 94, "Basso", 16, "A", -2),
    ("Di Lorenzo", "DIF", "Napoli", 5.9, 92, "Medio", 14, "B", 1),
    ("Buongiorno", "DIF", "Napoli", 5.9, 89, "Medio", 13, "B", 4),
    ("Dimarco", "DIF", "Inter", 6.5, 86, "Medio", 25, "A", 8),
    ("Calafiori", "DIF", "Bologna", 5.9, 82, "Medio", 10, "C", 6),
    ("Zaccagni", "CEN", "Lazio", 7.1, 88, "Medio", 32, "A", 9),
    ("Milinković-Savić", "CEN", "Lazio", 7.0, 94, "Basso", 36, "S", 4),
    ("Barella", "CEN", "Inter", 7.3, 96, "Basso", 38, "S", 7),
    ("Politano", "CEN", "Napoli", 6.6, 78, "Alto", 19, "B", -3),
    ("Ricci", "CEN", "Torino", 5.8, 91, "Basso", 12, "C", 13),
    ("Orsolini", "CEN", "Bologna", 6.2, 84, "Medio", 17, "B", 14),
    ("Koopmeiners", "ATT", "Juventus", 6.8, 87, "Medio", 48, "A", 6),
    ("Dybala", "ATT", "Roma", 7.4, 74, "Alto", 69, "S", 3),
    ("Lautaro", "ATT", "Inter", 8.7, 97, "Basso", 124, "S", 10),
    ("Lookman", "ATT", "Atalanta", 7.6, 86, "Medio", 26, "A", 18),
    ("Retegui", "ATT", "Atalanta", 6.9, 88, "Medio", 42, "A", 11),
    ("Castro", "ATT", "Bologna", 6.3, 79, "Medio", 20, "B", 15),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database(VoteStore, WorkspaceStore):
    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or os.getenv("FANTAOPERATOR_DB")
        self.path = Path(configured or Path(__file__).resolve().parents[1] / "data" / "fantaoperator.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS leagues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    platform TEXT NOT NULL DEFAULT 'Fantacalcio.it',
                    mode TEXT NOT NULL DEFAULT 'Classic',
                    participants INTEGER NOT NULL DEFAULT 8,
                    budget INTEGER NOT NULL DEFAULT 500,
                    matchday INTEGER NOT NULL DEFAULT 1,
                    vote_provider TEXT NOT NULL DEFAULT 'Fantacalcio.it',
                    source_url TEXT NOT NULL DEFAULT '',
                    auto_sync_minutes INTEGER NOT NULL DEFAULT 0,
                    scoring_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS players (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    role TEXT NOT NULL,
                    team TEXT NOT NULL DEFAULT '',
                    expected REAL NOT NULL DEFAULT 6,
                    start_probability INTEGER NOT NULL DEFAULT 75,
                    risk TEXT NOT NULL DEFAULT 'Medio',
                    price INTEGER NOT NULL DEFAULT 1,
                    tier TEXT NOT NULL DEFAULT 'C',
                    trend INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS roster (
                    league_id INTEGER NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
                    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                    purchase_cost INTEGER NOT NULL DEFAULT 1,
                    owned INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (league_id, player_id)
                );
                CREATE TABLE IF NOT EXISTS matchday_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    league_id INTEGER NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
                    matchday INTEGER NOT NULL,
                    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                    official_vote REAL NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('LIVE','PROVVISORIO','DEFINITIVO')),
                    goals INTEGER NOT NULL DEFAULT 0,
                    assists INTEGER NOT NULL DEFAULT 0,
                    yellow_cards INTEGER NOT NULL DEFAULT 0,
                    red_cards INTEGER NOT NULL DEFAULT 0,
                    own_goals INTEGER NOT NULL DEFAULT 0,
                    goals_conceded INTEGER NOT NULL DEFAULT 0,
                    penalties_saved INTEGER NOT NULL DEFAULT 0,
                    clean_sheet INTEGER NOT NULL DEFAULT 0,
                    custom_bonus REAL NOT NULL DEFAULT 0,
                    custom_malus REAL NOT NULL DEFAULT 0,
                    fantavote REAL NOT NULL,
                    source_name TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    source_hash TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    UNIQUE (league_id, matchday, player_id)
                );
                CREATE TABLE IF NOT EXISTS sync_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    league_id INTEGER NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
                    matchday INTEGER NOT NULL,
                    source_name TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    rows_received INTEGER NOT NULL DEFAULT 0,
                    rows_changed INTEGER NOT NULL DEFAULT 0,
                    payload_hash TEXT NOT NULL DEFAULT '',
                    changes_json TEXT NOT NULL DEFAULT '[]',
                    checked_at TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS assistant_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    league_id INTEGER NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            self.initialize_votes(db)
            self.initialize_workspace(db)
            count = db.execute("SELECT COUNT(*) FROM leagues").fetchone()[0]
            if count == 0:
                now = utc_now()
                rules = json.dumps(ScoringRules().as_dict(), ensure_ascii=False)
                cursor = db.execute(
                    """INSERT INTO leagues
                    (name, platform, mode, participants, budget, matchday, vote_provider, scoring_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("Lega degli Otto", "Fantacalcio.it", "Classic", 8, 500, 3, "Fantacalcio.it", rules, now, now),
                )
                league_id = int(cursor.lastrowid)
                for player in SEED_PLAYERS:
                    p = db.execute(
                        """INSERT INTO players
                        (name, role, team, expected, start_probability, risk, price, tier, trend)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        player,
                    )
                    db.execute(
                        "INSERT INTO roster (league_id, player_id, purchase_cost, owned) VALUES (?, ?, ?, 1)",
                        (league_id, int(p.lastrowid), player[6]),
                    )

    def leagues(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM leagues ORDER BY id")]

    def league(self, league_id: int) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM leagues WHERE id = ?", (league_id,)).fetchone()
        if row is None:
            raise KeyError(f"League {league_id} not found")
        result = dict(row)
        result["scoring"] = json.loads(result.pop("scoring_json") or "{}")
        return result

    def save_league(self, league_id: int, values: Mapping[str, Any], scoring: Mapping[str, Any]) -> None:
        allowed = {
            "name", "platform", "mode", "participants", "budget", "matchday",
            "vote_provider", "source_url", "auto_sync_minutes", "season", "vote_edition",
        }
        fields = validate_league({key: values[key] for key in allowed if key in values})
        if "season" in fields:
            fields["season"] = season_name(fields["season"])
        for key in ("vote_provider", "vote_edition"):
            if key in fields and not str(fields[key]).strip():
                raise ValueError("Provider e redazione sono obbligatori")
        fields["scoring_json"] = json.dumps(ScoringRules.from_mapping(scoring).as_dict(), ensure_ascii=False)
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self.connect() as db:
            db.execute(f"UPDATE leagues SET {assignments} WHERE id = ?", (*fields.values(), league_id))
            self._recalculate_locked(db, league_id)

    def roster(self, league_id: int) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT p.*, r.purchase_cost FROM roster r
                JOIN players p ON p.id = r.player_id
                WHERE r.league_id = ? AND r.owned = 1
                ORDER BY CASE p.role WHEN 'POR' THEN 1 WHEN 'DIF' THEN 2 WHEN 'CEN' THEN 3 ELSE 4 END, p.expected DESC""",
                (league_id,),
            )
            return [dict(row) for row in rows]

    def update_player(self, player_id: int, values: Mapping[str, Any], league_id: int, purchase_cost: int) -> None:
        allowed = {"name", "role", "team", "expected", "start_probability", "risk", "price", "tier", "trend"}
        fields = {key: values[key] for key in allowed if key in values}
        with self.connect() as db:
            if fields:
                assignments = ", ".join(f"{key} = ?" for key in fields)
                db.execute(f"UPDATE players SET {assignments} WHERE id = ?", (*fields.values(), player_id))
            db.execute(
                "UPDATE roster SET purchase_cost = ? WHERE league_id = ? AND player_id = ?",
                (int(purchase_cost), league_id, player_id),
            )


    def assistant_messages(self, league_id: int, limit: int = 40) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT role, content, created_at FROM assistant_log WHERE league_id = ? ORDER BY id DESC LIMIT ?",
                (league_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def add_assistant_message(self, league_id: int, role: str, content: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO assistant_log (league_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (league_id, role, content, utc_now()),
            )

    def clear_assistant(self, league_id: int) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM assistant_log WHERE league_id = ?", (league_id,))
