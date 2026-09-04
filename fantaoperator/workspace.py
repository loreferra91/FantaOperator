"""Validated roster editing, saved lineups and portable personal workspace backups."""
from __future__ import annotations

import csv
import io
import json
import math
import re
from datetime import datetime, timezone

from .engine import FORMATION_LIMITS, ScoringRules
from .official_votes import season_name
from .sources import MAX_PAYLOAD, load_json


PLAYER_FIELDS = ("name", "role", "team", "expected", "start_probability", "risk", "price", "tier", "trend", "vote_provider", "provider_player_id")


def validate_roster(rows):
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("La rosa deve contenere al massimo 100 giocatori.")
    result, names, source_ids = [], set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Riga della rosa non valida.")
        item = dict(row)
        for key in ("name", "team"):
            if not isinstance(item.get(key, ""), str):
                raise ValueError("Nome e squadra devono essere testo.")
            item[key] = item.get(key, "").strip()
            if len(item[key]) > 120:
                raise ValueError("Nome o squadra troppo lungo.")
        if not item["name"] or item["name"].casefold() in names:
            raise ValueError("Ogni giocatore deve avere un nome unico e non vuoto.")
        names.add(item["name"].casefold())
        item["vote_provider"] = str(item.get("vote_provider") or "").strip()
        item["provider_player_id"] = str(item.get("provider_player_id") or "").strip()
        if len(item["vote_provider"]) > 120 or (item["provider_player_id"] and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", item["provider_player_id"])):
            raise ValueError("Identificativo o fonte giocatore non valido.")
        if bool(item["vote_provider"]) != bool(item["provider_player_id"]):
            raise ValueError("Fonte e identificativo giocatore devono essere presenti insieme.")
        if item["provider_player_id"]:
            source_id = (item["vote_provider"], item["provider_player_id"])
            if source_id in source_ids:
                raise ValueError("La rosa contiene due volte lo stesso giocatore della fonte.")
            source_ids.add(source_id)
        for key, choices, default in (
            ("role", ("POR", "DIF", "CEN", "ATT"), ""),
            ("risk", ("Basso", "Medio", "Alto"), "Medio"),
            ("tier", ("S", "A", "B", "C", "D", "E"), "C"),
        ):
            item[key] = item.get(key, default)
            if item[key] not in choices:
                raise ValueError(f"{item['name']}: {key} non valido.")
        for key, low, high, default in (
            ("expected", 0, 30, 6), ("start_probability", 0, 100, 75),
            ("price", 0, 5000, 1), ("purchase_cost", 0, 5000, 0), ("trend", -100, 100, 0),
        ):
            try:
                value = float(item.get(key, default))
            except (TypeError, ValueError):
                raise ValueError(f"{item['name']}: {key} deve essere un numero.") from None
            if not math.isfinite(value) or not low <= value <= high or (key != "expected" and not value.is_integer()):
                raise ValueError(f"{item['name']}: {key} fuori intervallo ({low}–{high}).")
            item[key] = value if key == "expected" else int(value)
        result.append({key: item[key] for key in (*PLAYER_FIELDS, "purchase_cost")})
    return result


def roster_csv(rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=(*PLAYER_FIELDS, "purchase_cost"))
    writer.writeheader()
    for row in rows:
        # Prevent spreadsheet formulas when opening the exported file.
        writer.writerow({key: ("'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value)
                         for key, value in row.items() if key in writer.fieldnames})
    return output.getvalue()


def parse_roster_csv(payload):
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("File troppo grande: limite 5 MB.")
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    fields = reader.fieldnames or []
    if len(fields) != len(set(fields)) or not {"name", "role"} <= set(fields) or set(fields) - {*PLAYER_FIELDS, "purchase_cost"}:
        raise ValueError("Usa le colonne del modello CSV: name e role sono obbligatori.")
    rows = []
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("Numero di colonne non valido.")
        rows.append({key: (value[1:] if value.startswith(("'=", "'+", "'-", "'@")) else value)
                     for key, value in row.items() if value.strip()})
    return validate_roster(rows)


def validate_lineup(formation, players):
    limits = FORMATION_LIMITS.get(formation)
    if not limits or len(players) != 11 or len({p["name"].casefold() for p in players}) != 11:
        raise ValueError("Seleziona undici giocatori diversi e un modulo Classic valido.")
    if any(sum(p["role"] == role for p in players) != count for role, count in limits.items()):
        raise ValueError("I ruoli selezionati non rispettano il modulo.")


def lineup_score(players, records):
    scored, missing = [], []
    for player in players:
        if player.get("provider_player_id"):
            candidates = [r for r in records if r.get("provider_player_id") == player["provider_player_id"] and r.get("source_name") == player.get("vote_provider")]
        else:
            candidates = [r for r in records if r["name"].casefold() == player["name"].casefold()
                          and (not r.get("team") or not player.get("team") or r["team"].casefold() == player["team"].casefold())]
        row = candidates[0] if len(candidates) == 1 else None
        if not row or row["fantavote"] is None:
            missing.append(player["name"])
        else:
            scored.append(row)
    return {"total": round(sum(r["fantavote"] for r in scored), 2) if scored else None,
            "count": len(scored), "missing": missing, "complete": len(scored) == 11 and len(players) == 11}


class WorkspaceStore:
    def initialize_workspace(self, db):
        db.execute("""CREATE TABLE IF NOT EXISTS saved_lineups (
            league_id INTEGER NOT NULL REFERENCES leagues(id), season TEXT NOT NULL,
            matchday INTEGER NOT NULL, formation TEXT NOT NULL, players_json TEXT NOT NULL,
            saved_at TEXT NOT NULL, PRIMARY KEY(league_id, season, matchday))""")

    def _replace_roster_locked(self, db, league_id, rows):
        # Keep old player rows for historical references; ownership is reversible.
        db.execute("UPDATE roster SET owned=0 WHERE league_id=?", (league_id,))
        for row in rows:
            values = tuple(row[key] for key in PLAYER_FIELDS)
            db.execute(f"""INSERT INTO players ({','.join(PLAYER_FIELDS)}) VALUES ({','.join('?' for _ in PLAYER_FIELDS)})
                ON CONFLICT(name) DO UPDATE SET {','.join(f'{key}=excluded.{key}' for key in PLAYER_FIELDS[1:])}""", values)
            player_id = db.execute("SELECT id FROM players WHERE name=? COLLATE NOCASE", (row["name"],)).fetchone()[0]
            db.execute("""INSERT INTO roster (league_id,player_id,purchase_cost,owned) VALUES (?,?,?,1)
                ON CONFLICT(league_id,player_id) DO UPDATE SET purchase_cost=excluded.purchase_cost,owned=1""",
                (league_id, player_id, row["purchase_cost"]))

    def replace_roster(self, league_id, rows):
        rows = validate_roster(rows)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._replace_roster_locked(db, league_id, rows)

    def save_lineup(self, league_id, matchday, formation, player_ids):
        league = self.league(league_id)
        if league["mode"] != "Classic":
            raise ValueError("Il motore supporta soltanto i moduli Classic.")
        if not isinstance(matchday, int) or not 1 <= matchday <= 38:
            raise ValueError("Giornata non valida.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            roster = {p["id"]: p for p in self.roster(league_id)}
            if any(pid not in roster for pid in player_ids):
                raise ValueError("La formazione contiene giocatori non più in rosa.")
            players = [roster[pid] for pid in player_ids]
            validate_lineup(formation, players)
            stamp = datetime.now(timezone.utc).isoformat()
            db.execute("INSERT OR REPLACE INTO saved_lineups VALUES (?,?,?,?,?,?)",
                       (league_id, league["season"], matchday, formation, json.dumps(players, ensure_ascii=False), stamp))

    def saved_lineup(self, league_id, matchday):
        with self.connect() as db:
            row = db.execute("SELECT * FROM saved_lineups WHERE league_id=? AND season=? AND matchday=?",
                             (league_id, self.league(league_id)["season"], matchday)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["players"] = json.loads(result.pop("players_json"))
        return result

    def export_workspace(self, league_id):
        with self.connect() as db:
            lineups = [dict(row) for row in db.execute("SELECT season,matchday,formation,players_json,saved_at FROM saved_lineups WHERE league_id=?", (league_id,))]
        for row in lineups:
            row["players"] = json.loads(row.pop("players_json"))
        data = {"format": "fantaoperator-workspace", "version": 1, "league": self.league(league_id),
                "roster": self.roster(league_id), "lineups": lineups}
        # Feed credentials are not included in portable files; public vote URLs are safe.
        from .public_votes import is_public_votes_url
        from .gazzetta_votes import is_gazzetta_votes_url
        if not (is_public_votes_url(data["league"]["source_url"]) or is_gazzetta_votes_url(data["league"]["source_url"])):
            data["league"]["source_url"] = ""
        data["league"]["auto_sync_minutes"] = 0
        return json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)

    def restore_workspace(self, league_id, payload):
        if len(payload) > MAX_PAYLOAD:
            raise ValueError("Backup troppo grande: limite 5 MB.")
        try:
            data = load_json(payload)
            if data["format"] != "fantaoperator-workspace" or data["version"] != 1:
                raise ValueError("Formato backup non supportato.")
            rows = validate_roster(data["roster"])
            league = data["league"]
            league_fields = ("name", "platform", "mode", "participants", "budget", "matchday", "vote_provider", "vote_edition", "season")
            values = {key: league[key] for key in league_fields}
            values = validate_league(values)
            rules = ScoringRules.from_mapping(league["scoring"])
            lineups = data["lineups"]
            if not isinstance(lineups, list) or len(lineups) > 380:
                raise ValueError("Troppe formazioni nel backup.")
            prepared, seen = [], set()
            for row in lineups:
                season = season_name(row["season"])
                day = int(row["matchday"])
                if not 1 <= day <= 38 or (season, day) in seen:
                    raise ValueError("Giornata duplicata o non valida.")
                seen.add((season, day))
                players = validate_roster(row["players"])
                validate_lineup(row["formation"], players)
                prepared.append((league_id, season, day, row["formation"], json.dumps(players), datetime.now(timezone.utc).isoformat()))
        except (KeyError, TypeError, OverflowError) as exc:
            raise ValueError("Backup incompleto o non valido.") from exc
        # All validation completes before the first mutation. Failed restores are atomic.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(f"UPDATE leagues SET {','.join(f'{key}=?' for key in values)},scoring_json=?,source_url='',auto_sync_minutes=0 WHERE id=?",
                       (*values.values(), json.dumps(rules.as_dict()), league_id))
            self._replace_roster_locked(db, league_id, rows)
            db.execute("DELETE FROM saved_lineups WHERE league_id=?", (league_id,))
            db.executemany("INSERT INTO saved_lineups VALUES (?,?,?,?,?,?)", prepared)
            self._recalculate_locked(db, league_id)


def validate_league(values):
    result = dict(values)
    for key in ("name", "platform", "vote_provider", "vote_edition"):
        if key in result and (not isinstance(result[key], str) or not result[key].strip() or len(result[key]) > 120):
            raise ValueError("Nome, piattaforma, provider e redazione devono essere testi non vuoti (massimo 120 caratteri).")
    if "mode" in result and result["mode"] not in ("Classic", "Mantra"):
        raise ValueError("Modalità non valida.")
    for key, low, high in (("participants", 2, 20), ("budget", 50, 5000), ("matchday", 1, 38), ("auto_sync_minutes", 0, 60)):
        if key in result:
            try:
                number = float(result[key])
                if not math.isfinite(number) or not number.is_integer() or not low <= number <= high:
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValueError(f"{key}: valore non valido.") from None
            result[key] = int(number)
    if "season" in result:
        result["season"] = season_name(result["season"])
    if "source_url" in result and result["source_url"]:
        from urllib.parse import urlsplit
        url = urlsplit(result["source_url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Usa un URL HTTPS senza credenziali nel nome host.")
        # DNS/private address checks occur at retrieval time, including redirects.
    return result
