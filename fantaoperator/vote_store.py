"""Versioned vote storage; legacy tables remain untouched and recoverable."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .engine import ScoringRules, calculate_fantavote
from .official_votes import SCORING_FIELDS, STATUSES, normalize_rows, season_name
from .sources import safe_url
from .analytics import season_statistics


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def context(league: dict) -> tuple:
    return (league["id"], league["season"], league["vote_provider"], league["vote_edition"])


def score(row: dict, rules: ScoringRules):
    return calculate_fantavote(row["vote"], **{key: row[key] for key in SCORING_FIELDS}, rules=rules)


class VoteStore:
    def initialize_votes(self, db):
        columns = {row["name"] for row in db.execute("PRAGMA table_info(leagues)")}
        year = datetime.now().year - (datetime.now().month < 7)
        for name, default in (("season", f"{year}-{(year+1)%100:02d}"), ("vote_edition", "Redazione Fantacalcio")):
            if name not in columns:
                db.execute(f"ALTER TABLE leagues ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS vote_records (
                league_id INTEGER NOT NULL REFERENCES leagues(id), season TEXT NOT NULL,
                provider TEXT NOT NULL, edition TEXT NOT NULL, matchday INTEGER NOT NULL,
                player_key TEXT NOT NULL, data_json TEXT NOT NULL,
                PRIMARY KEY(league_id, season, provider, edition, matchday, player_key)
            );
            CREATE TABLE IF NOT EXISTS vote_sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                league_id INTEGER NOT NULL REFERENCES leagues(id), season TEXT NOT NULL,
                source_name TEXT NOT NULL, edition TEXT NOT NULL, matchday INTEGER NOT NULL,
                source_url TEXT NOT NULL, status TEXT NOT NULL, provenance TEXT NOT NULL,
                rows_received INTEGER NOT NULL DEFAULT 0, rows_changed INTEGER NOT NULL DEFAULT 0,
                payload_hash TEXT NOT NULL DEFAULT '', changes_json TEXT NOT NULL DEFAULT '[]',
                checked_at TEXT NOT NULL, error TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS vote_sync_scope ON vote_sync_runs
                (league_id, season, source_name, edition, matchday, id);
        """)

    def records(self, league_id, matchday):
        league = self.league(league_id)
        with self.connect() as db:
            rows = db.execute("""SELECT data_json FROM vote_records WHERE
                league_id=? AND season=? AND provider=? AND edition=? AND matchday=?
                ORDER BY player_key""", (*context(league), matchday))
            return [json.loads(row[0]) for row in rows]

    def season_records(self, league_id):
        league = self.league(league_id)
        with self.connect() as db:
            rows = db.execute("""SELECT data_json FROM vote_records WHERE
                league_id=? AND season=? AND provider=? AND edition=?
                ORDER BY matchday, player_key""", context(league))
            return [json.loads(row[0]) for row in rows]

    def season_statistics(self, league_id):
        return season_statistics(self.season_records(league_id))

    def latest_sync(self, league_id, matchday=None):
        rows = self.sync_history(league_id, limit=1, matchday=matchday)
        return rows[0] if rows else None

    def sync_history(self, league_id, limit=20, matchday=None):
        league = self.league(league_id)
        day = league["matchday"] if matchday is None else matchday
        with self.connect() as db:
            rows = db.execute("""SELECT * FROM vote_sync_runs WHERE league_id=? AND season=?
                AND source_name=? AND edition=? AND matchday=? ORDER BY id DESC LIMIT ?""",
                (*context(league), day, limit))
            return [dict(row) for row in rows]

    def import_records(self, league_id, matchday, rows, *, source_name, source_url,
                       payload_hash, default_status, provenance="IMPORT_LOCALE", expected_context=None,
                       expected_source_url=None):
        normalized = normalize_rows(rows, default_status)
        if not 1 <= matchday <= 38:
            raise ValueError("Giornata non valida")
        if provenance not in {"IMPORT_LOCALE", "FEED_CONFIGURATO", "PAGINA_UFFICIALE"}:
            raise ValueError("Provenienza non valida")
        checked_at, changes = now(), []
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            league = dict(db.execute("SELECT * FROM leagues WHERE id=?", (league_id,)).fetchone())
            if source_name != league["vote_provider"]:
                raise ValueError("Provider diverso da quello configurato nella lega")
            if expected_context is not None and tuple(expected_context) != context(league):
                raise ValueError("Configurazione modificata durante il download: ripeti la verifica")
            if expected_source_url is not None and expected_source_url != league["source_url"]:
                raise ValueError("URL del feed modificato durante il download: ripeti la verifica")
            scope = (*context(league), matchday)
            rules = ScoringRules.from_mapping(json.loads(league["scoring_json"]))
            previous_rows = db.execute("""SELECT player_key, data_json FROM vote_records WHERE
                league_id=? AND season=? AND provider=? AND edition=? AND matchday=?""", scope)
            existing = {r[0]: json.loads(r[1]) for r in previous_rows}
            for item in normalized:
                key = f"id:{item['provider_player_id']}" if item.get("provider_player_id") else item["player"].casefold()
                record = {**item, "name": item["player"], "official_vote": item["vote"],
                          "fantavote": score(item, rules), "source_name": source_name,
                          "source_url": safe_url(source_url), "source_hash": payload_hash,
                          "checked_at": checked_at, "provenance": provenance,
                          "season": league["season"], "edition": league["vote_edition"], "matchday": matchday}
                previous = existing.get(key)
                if previous:
                    fields = {k: {"prima": previous.get(k), "dopo": record.get(k)}
                              for k in ("official_vote", "fantavote", "provider_fantavote", "status", *SCORING_FIELDS)
                              if previous.get(k) != record.get(k)}
                else:
                    fields = {"nuovo": {"prima": None, "dopo": record["fantavote"]}}
                if fields:
                    changes.append({"player": record["name"], "fields": fields})
                existing[key] = record
                db.execute("""INSERT INTO vote_records VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(league_id, season, provider, edition, matchday, player_key)
                    DO UPDATE SET data_json=excluded.data_json""",
                    (*scope, key, json.dumps(record, ensure_ascii=False, allow_nan=False)))
            # A mixed dataset is no more definitive than its least consolidated record.
            status = min((r["status"] for r in existing.values()), key=STATUSES.index)
            db.execute("""INSERT INTO vote_sync_runs
                (league_id, season, source_name, edition, matchday, source_url, status,
                 provenance, rows_received, rows_changed, payload_hash, changes_json, checked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*scope, safe_url(source_url), status, provenance, len(normalized), len(changes),
                 payload_hash, json.dumps(changes, ensure_ascii=False), checked_at))
        return {"rows": len(normalized), "changed": len(changes), "changes": changes, "checked_at": checked_at}

    def log_failed_sync(self, league_id, matchday, source_name, source_url, error, *, expected_context=None,
                        expected_source_url=None):
        league = self.league(league_id)
        if expected_context is not None and tuple(expected_context) != context(league):
            return
        if expected_source_url is not None and expected_source_url != league["source_url"]:
            return
        with self.connect() as db:
            db.execute("""INSERT INTO vote_sync_runs
                (league_id, season, source_name, edition, matchday, source_url, status, provenance, checked_at, error)
                VALUES (?, ?, ?, ?, ?, ?, 'ERRORE', 'FEED_CONFIGURATO', ?, ?)""",
                (*context(league), matchday, safe_url(source_url), now(), error[:500]))

    def _recalculate_locked(self, db, league_id):
        league = dict(db.execute("SELECT * FROM leagues WHERE id=?", (league_id,)).fetchone())
        rules = ScoringRules.from_mapping(json.loads(league["scoring_json"]))
        rows = db.execute("""SELECT rowid, data_json FROM vote_records WHERE
            league_id=? AND season=? AND provider=? AND edition=?""", context(league)).fetchall()
        for row in rows:
            record = json.loads(row[1])
            record["fantavote"] = score(record, rules)
            db.execute("UPDATE vote_records SET data_json=? WHERE rowid=?", (json.dumps(record, ensure_ascii=False), row[0]))
