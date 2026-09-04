"""Persistent, replace-only storage for the public Serie A player directory."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .analytics import merge_player_catalog
from .official_votes import season_name
from .sources import safe_url


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class SquadStore:
    def initialize_squads(self, db) -> None:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS squad_catalog (
                season TEXT NOT NULL, provider TEXT NOT NULL, player_key TEXT NOT NULL,
                name TEXT NOT NULL, role TEXT NOT NULL, team TEXT NOT NULL,
                source_url TEXT NOT NULL, source_hash TEXT NOT NULL,
                article_updated_at TEXT NOT NULL, checked_at TEXT NOT NULL,
                PRIMARY KEY(season, provider, player_key)
            );
            CREATE TABLE IF NOT EXISTS squad_sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                season TEXT NOT NULL, provider TEXT NOT NULL, source_url TEXT NOT NULL,
                status TEXT NOT NULL, teams INTEGER NOT NULL DEFAULT 0,
                players INTEGER NOT NULL DEFAULT 0, source_hash TEXT NOT NULL DEFAULT '',
                article_updated_at TEXT NOT NULL DEFAULT '', checked_at TEXT NOT NULL,
                warnings_json TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS squad_sync_scope ON squad_sync_runs
                (season, provider, id);
        """)

    def replace_squad_catalog(self, season, provider, rows, *, source_url, source_hash,
                              article_updated_at="", warnings=()):
        season = season_name(season)
        rows = [dict(row) for row in rows]
        teams = {row["team"] for row in rows}
        if len(teams) != 20 or len(rows) < 400:
            raise ValueError("Catalogo rose incompleto: aggiornamento non applicato")
        stamp = now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM squad_catalog WHERE season=? AND provider=?", (season, provider))
            db.executemany("""INSERT INTO squad_catalog
                (season,provider,player_key,name,role,team,source_url,source_hash,article_updated_at,checked_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", [
                (season, provider, row["player_key"], row["name"], row["role"], row["team"],
                 safe_url(source_url), source_hash, article_updated_at, stamp) for row in rows
            ])
            db.execute("""INSERT INTO squad_sync_runs
                (season,provider,source_url,status,teams,players,source_hash,article_updated_at,checked_at,warnings_json)
                VALUES (?,?,?,'OK',?,?,?,?,?,?)""",
                (season, provider, safe_url(source_url), len(teams), len(rows), source_hash,
                 article_updated_at, stamp, json.dumps(list(warnings), ensure_ascii=False)))
        return {"ok": True, "teams": len(teams), "players": len(rows), "checked_at": stamp,
                "article_updated_at": article_updated_at, "warnings": list(warnings)}

    def log_failed_squad_sync(self, season, provider, source_url, error):
        with self.connect() as db:
            db.execute("""INSERT INTO squad_sync_runs
                (season,provider,source_url,status,checked_at,error)
                VALUES (?,?,?,'ERRORE',?,?)""",
                (season_name(season), provider, safe_url(source_url), now(), str(error)[:500]))

    def catalog_players(self, season, provider=None):
        query = "SELECT * FROM squad_catalog WHERE season=?"
        params = [season_name(season)]
        if provider:
            query += " AND provider=?"
            params.append(provider)
        query += " ORDER BY team, CASE role WHEN 'POR' THEN 1 WHEN 'DIF' THEN 2 WHEN 'CEN' THEN 3 ELSE 4 END, name"
        with self.connect() as db:
            return [dict(row) for row in db.execute(query, params)]

    def latest_squad_sync(self, season, provider=None):
        query = "SELECT * FROM squad_sync_runs WHERE season=?"
        params = [season_name(season)]
        if provider:
            query += " AND provider=?"
            params.append(provider)
        query += " ORDER BY id DESC LIMIT 1"
        with self.connect() as db:
            row = db.execute(query, params).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["warnings"] = json.loads(result.pop("warnings_json") or "[]")
        return result

    def complete_player_catalog(self, league_id):
        league = self.league(league_id)
        return merge_player_catalog(self.catalog_players(league["season"]), self.season_statistics(league_id))

    def link_roster_to_votes(self, league_id):
        """Attach Gazzetta stable IDs to full-name Diretta players when unambiguous."""
        catalog = self.complete_player_catalog(league_id)
        linked = 0
        from .analytics import normalized_name
        by_identity = {}
        for row in catalog:
            if row.get("provider_player_id"):
                key = (normalized_name(row["name"]), normalized_name(row.get("team", "")), row.get("role"))
                by_identity.setdefault(key, []).append(row)
        with self.connect() as db:
            players = db.execute("""SELECT p.id,p.name,p.team,p.role,p.provider_player_id FROM roster r
                JOIN players p ON p.id=r.player_id WHERE r.league_id=? AND r.owned=1""", (league_id,)).fetchall()
            for player in players:
                if player["provider_player_id"]:
                    continue
                key = (normalized_name(player["name"]), normalized_name(player["team"]), player["role"])
                matches = by_identity.get(key, [])
                if len(matches) == 1:
                    db.execute("UPDATE players SET vote_provider=?,provider_player_id=? WHERE id=?",
                               (matches[0]["vote_provider"], matches[0]["provider_player_id"], player["id"]))
                    linked += 1
        return linked
