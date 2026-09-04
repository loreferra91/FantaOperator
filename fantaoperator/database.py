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
from .squad_store import SquadStore
from .official_votes import season_name
from .workspace import WorkspaceStore, validate_league


LEGACY_DEMO_NAMES = {
    "Maignan", "Provedel", "Bastoni", "Bremer", "Di Lorenzo", "Buongiorno", "Dimarco", "Calafiori",
    "Zaccagni", "Milinković-Savić", "Barella", "Politano", "Ricci", "Orsolini", "Koopmeiners", "Dybala",
    "Lautaro", "Lookman", "Retegui", "Castro",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database(VoteStore, SquadStore, WorkspaceStore):
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
            self.initialize_squads(db)
            self.initialize_workspace(db)
            league_columns = {row["name"] for row in db.execute("PRAGMA table_info(leagues)")}
            for column, definition in (("bench_size", "INTEGER NOT NULL DEFAULT 7"),
                                       ("max_substitutions", "INTEGER NOT NULL DEFAULT 3"),
                                       ("defense_modifier_enabled", "INTEGER NOT NULL DEFAULT 0"),
                                       ("defense_threshold_low", "REAL NOT NULL DEFAULT 6.0"),
                                       ("defense_threshold_mid", "REAL NOT NULL DEFAULT 6.5"),
                                       ("defense_threshold_high", "REAL NOT NULL DEFAULT 7.0"),
                                       ("defense_bonus_low", "REAL NOT NULL DEFAULT 1.0"),
                                       ("defense_bonus_mid", "REAL NOT NULL DEFAULT 3.0"),
                                       ("defense_bonus_high", "REAL NOT NULL DEFAULT 6.0")):
                if column not in league_columns:
                    db.execute(f"ALTER TABLE leagues ADD COLUMN {column} {definition}")
            player_columns = {row["name"] for row in db.execute("PRAGMA table_info(players)")}
            for column in ("vote_provider", "provider_player_id"):
                if column not in player_columns:
                    db.execute(f"ALTER TABLE players ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            # Early builds inserted this exact obsolete roster. Remove it only when
            # it is untouched and has no saved work; user-created rosters are kept.
            demo_rows = db.execute("""SELECT p.name FROM roster r JOIN players p ON p.id=r.player_id
                WHERE r.league_id=1 AND r.owned=1""").fetchall()
            has_personal_work = db.execute("SELECT 1 FROM saved_lineups WHERE league_id=1 LIMIT 1").fetchone() or db.execute(
                "SELECT 1 FROM roster_transactions WHERE league_id=1 LIMIT 1").fetchone()
            if not has_personal_work and {row[0] for row in demo_rows} == LEGACY_DEMO_NAMES:
                db.execute("UPDATE roster SET owned=0 WHERE league_id=1")
                db.execute("UPDATE leagues SET name='La mia lega' WHERE id=1 AND name='Lega degli Otto'")
            count = db.execute("SELECT COUNT(*) FROM leagues").fetchone()[0]
            if count == 0:
                now = utc_now()
                rules = json.dumps(ScoringRules().as_dict(), ensure_ascii=False)
                db.execute(
                    """INSERT INTO leagues
                    (name, platform, mode, participants, budget, matchday, vote_provider, scoring_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("La mia lega", "Fantacalcio.it", "Classic", 8, 500, 2, "Fantacalcio.it", rules, now, now),
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
            "bench_size", "max_substitutions",
            "defense_modifier_enabled", "defense_threshold_low", "defense_threshold_mid", "defense_threshold_high",
            "defense_bonus_low", "defense_bonus_mid", "defense_bonus_high",
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
