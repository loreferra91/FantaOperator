"""Validated roster editing, saved lineups and portable personal workspace backups."""
from __future__ import annotations

import csv
import io
import json
import math
import re
from datetime import datetime, timezone

from .analytics import possible_duplicate, resolve_player_identities
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
        if any(possible_duplicate(item, previous) for previous in result):
            raise ValueError("Possibile giocatore duplicato: verifica nome completo e identificativo della fonte.")
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


def _matching_score(player, records):
    if player.get("provider_player_id"):
        candidates = [r for r in records if r.get("provider_player_id") == player["provider_player_id"] and r.get("source_name") == player.get("vote_provider")]
    else:
        candidates = [r for r in records if r["name"].casefold() == player["name"].casefold()
                      and (not r.get("team") or not player.get("team") or r["team"].casefold() == player["team"].casefold())]
    row = candidates[0] if len(candidates) == 1 else None
    return row if row and row["fantavote"] is not None else None


def lineup_score(players, records, bench=None, max_substitutions=0, defense_modifier=None):
    """Score a Classic lineup and apply ordered, role-for-role substitutions."""
    bench = list(bench or [])
    maximum = max(0, min(int(max_substitutions), 11))
    scored, scored_players, missing, substitutions, used = [], [], [], [], set()
    for player in players:
        row = _matching_score(player, records)
        if row:
            scored.append(row)
            scored_players.append((player, row))
            continue
        replacement = None
        if len(substitutions) < maximum:
            for index, reserve in enumerate(bench):
                if index in used or reserve.get("role") != player.get("role"):
                    continue
                reserve_row = _matching_score(reserve, records)
                if reserve_row:
                    replacement = (index, reserve, reserve_row)
                    break
        if replacement:
            index, reserve, reserve_row = replacement
            used.add(index)
            scored.append(reserve_row)
            scored_players.append((reserve, reserve_row))
            substitutions.append({"out": player["name"], "in": reserve["name"], "role": player.get("role", "")})
        else:
            missing.append(player["name"])
    modifier = 0.0
    modifier_average = None
    settings = defense_modifier or {}
    if settings.get("defense_modifier_enabled") and sum(p.get("role") == "DIF" for p in players) >= 4:
        keeper_votes = [float(row["official_vote"]) for player, row in scored_players
                        if player.get("role") == "POR" and row.get("official_vote") is not None]
        defender_votes = sorted((float(row["official_vote"]) for player, row in scored_players
                                 if player.get("role") == "DIF" and row.get("official_vote") is not None), reverse=True)
        if keeper_votes and len(defender_votes) >= 3:
            modifier_average = round((keeper_votes[0] + sum(defender_votes[:3])) / 4, 2)
            bands = [(float(settings.get(f"defense_threshold_{key}", threshold)),
                      float(settings.get(f"defense_bonus_{key}", bonus)))
                     for key, threshold, bonus in (("low", 6, 1), ("mid", 6.5, 3), ("high", 7, 6))]
            modifier = max((bonus for threshold, bonus in bands if modifier_average >= threshold), default=0.0)
    base_total = sum(r["fantavote"] for r in scored) if scored else None
    return {"total": round(base_total + modifier, 2) if base_total is not None else None,
            "count": len(scored), "missing": missing, "substitutions": substitutions,
            "defense_modifier": modifier, "defense_average": modifier_average,
            "complete": len(scored) == 11 and len(players) == 11}


class WorkspaceStore:
    def initialize_workspace(self, db):
        db.execute("""CREATE TABLE IF NOT EXISTS saved_lineups (
            league_id INTEGER NOT NULL REFERENCES leagues(id), season TEXT NOT NULL,
            matchday INTEGER NOT NULL, formation TEXT NOT NULL, players_json TEXT NOT NULL,
            saved_at TEXT NOT NULL, PRIMARY KEY(league_id, season, matchday))""")
        lineup_columns = {row["name"] for row in db.execute("PRAGMA table_info(saved_lineups)")}
        if "bench_json" not in lineup_columns:
            db.execute("ALTER TABLE saved_lineups ADD COLUMN bench_json TEXT NOT NULL DEFAULT '[]'")
        db.execute("""CREATE TABLE IF NOT EXISTS roster_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_id INTEGER NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
            kind TEXT NOT NULL, player_name TEXT NOT NULL, role TEXT NOT NULL DEFAULT '',
            team TEXT NOT NULL DEFAULT '', amount INTEGER NOT NULL DEFAULT 0,
            counterparty TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
            budget_delta INTEGER NOT NULL DEFAULT 0
        )""")
        transaction_columns = {row["name"] for row in db.execute("PRAGMA table_info(roster_transactions)")}
        if "budget_delta" not in transaction_columns:
            db.execute("ALTER TABLE roster_transactions ADD COLUMN budget_delta INTEGER NOT NULL DEFAULT 0")

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

    def save_lineup(self, league_id, matchday, formation, player_ids, bench_ids=None):
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
            bench_ids = list(bench_ids or [])
            if len(bench_ids) > int(league.get("bench_size", 7)) or len(bench_ids) != len(set(bench_ids)):
                raise ValueError(f"La panchina può contenere al massimo {league.get('bench_size', 7)} giocatori diversi.")
            if set(bench_ids) & set(player_ids) or any(pid not in roster for pid in bench_ids):
                raise ValueError("Titolari e panchinari devono essere giocatori diversi presenti in rosa.")
            bench = [roster[pid] for pid in bench_ids]
            stamp = datetime.now(timezone.utc).isoformat()
            db.execute("""INSERT OR REPLACE INTO saved_lineups
                (league_id,season,matchday,formation,players_json,saved_at,bench_json)
                VALUES (?,?,?,?,?,?,?)""",
                (league_id, league["season"], matchday, formation, json.dumps(players, ensure_ascii=False), stamp,
                 json.dumps(bench, ensure_ascii=False)))

    def saved_lineup(self, league_id, matchday):
        with self.connect() as db:
            row = db.execute("SELECT * FROM saved_lineups WHERE league_id=? AND season=? AND matchday=?",
                             (league_id, self.league(league_id)["season"], matchday)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["players"] = json.loads(result.pop("players_json"))
        result["bench"] = json.loads(result.pop("bench_json", "[]"))
        # Repair reads of pre-existing snapshots even before the next sync.
        catalog = self.complete_player_catalog(league_id)
        for field in ("players", "bench"):
            result[field] = resolve_player_identities(result[field], catalog)
        return result

    def transactions(self, league_id, limit=200):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM roster_transactions WHERE league_id=? ORDER BY id DESC LIMIT ?", (league_id, limit))
            return [dict(row) for row in rows]

    def available_budget(self, league_id):
        league = self.league(league_id)
        with self.connect() as db:
            spent = int(db.execute("SELECT COALESCE(SUM(purchase_cost),0) FROM roster WHERE league_id=? AND owned=1", (league_id,)).fetchone()[0])
            adjustments = int(db.execute("SELECT COALESCE(SUM(budget_delta),0) FROM roster_transactions WHERE league_id=?", (league_id,)).fetchone()[0])
        return int(league["budget"]) - spent + adjustments

    def acquire_player(self, league_id, player, cost, *, kind="ACQUISTO", counterparty="", note=""):
        row = validate_roster([{**dict(player), "purchase_cost": cost}])[0]
        if len(counterparty) > 120 or len(note) > 500:
            raise ValueError("Controparte o nota troppo lunga.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            league = dict(db.execute("SELECT * FROM leagues WHERE id=?", (league_id,)).fetchone())
            spent = db.execute("SELECT COALESCE(SUM(purchase_cost),0) FROM roster WHERE league_id=? AND owned=1", (league_id,)).fetchone()[0]
            adjustments = db.execute("SELECT COALESCE(SUM(budget_delta),0) FROM roster_transactions WHERE league_id=?", (league_id,)).fetchone()[0]
            if row["purchase_cost"] > int(league["budget"]) - spent + adjustments:
                raise ValueError("Budget insufficiente per registrare l'acquisto.")
            current = [dict(r) for r in db.execute("""SELECT p.*,r.purchase_cost FROM roster r JOIN players p ON p.id=r.player_id
                WHERE r.league_id=? AND r.owned=1""", (league_id,))]
            if any(p["name"].casefold() == row["name"].casefold() or possible_duplicate(p, row) for p in current):
                raise ValueError("Il giocatore è già in rosa o ha un nome compatibile: verifica l'identificativo della fonte.")
            self._upsert_owned_locked(db, league_id, row)
            db.execute("""INSERT INTO roster_transactions
                (league_id,kind,player_name,role,team,amount,counterparty,note,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)""", (league_id, kind, row["name"], row["role"], row["team"], row["purchase_cost"],
                counterparty.strip(), note.strip(), datetime.now(timezone.utc).isoformat()))

    def release_player(self, league_id, player_id, amount=0, *, kind="CESSIONE", counterparty="", note=""):
        amount = int(amount)
        if amount < 0 or amount > 5000 or len(counterparty) > 120 or len(note) > 500:
            raise ValueError("Dati della cessione non validi.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT p.*,r.purchase_cost FROM roster r JOIN players p ON p.id=r.player_id
                WHERE r.league_id=? AND r.player_id=? AND r.owned=1""", (league_id, player_id)).fetchone()
            if row is None:
                raise ValueError("Il giocatore non è presente in rosa.")
            db.execute("UPDATE roster SET owned=0 WHERE league_id=? AND player_id=?", (league_id, player_id))
            db.execute("""INSERT INTO roster_transactions
                (league_id,kind,player_name,role,team,amount,counterparty,note,created_at,budget_delta)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (league_id, kind, row["name"], row["role"], row["team"], amount,
                counterparty.strip(), note.strip(), datetime.now(timezone.utc).isoformat(), amount-int(row["purchase_cost"])))

    def exchange_player(self, league_id, outgoing_id, incoming, incoming_cost, *, counterparty="", note=""):
        incoming_row = validate_roster([{**dict(incoming), "purchase_cost": incoming_cost}])[0]
        if len(counterparty) > 120 or len(note) > 500:
            raise ValueError("Controparte o nota troppo lunga.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            league = dict(db.execute("SELECT * FROM leagues WHERE id=?", (league_id,)).fetchone())
            outgoing = db.execute("""SELECT p.*,r.purchase_cost FROM roster r JOIN players p ON p.id=r.player_id
                WHERE r.league_id=? AND r.player_id=? AND r.owned=1""", (league_id, outgoing_id)).fetchone()
            if outgoing is None:
                raise ValueError("Il giocatore ceduto non è più in rosa.")
            current = [dict(row) for row in db.execute("""SELECT p.* FROM roster r JOIN players p ON p.id=r.player_id
                WHERE r.league_id=? AND r.owned=1""", (league_id,))]
            if any(p["name"].casefold() == incoming_row["name"].casefold() or possible_duplicate(p, incoming_row)
                   for p in current):
                raise ValueError("Il giocatore ricevuto è già in rosa o ha un nome compatibile: verifica l'identificativo della fonte.")
            spent = db.execute("SELECT COALESCE(SUM(purchase_cost),0) FROM roster WHERE league_id=? AND owned=1", (league_id,)).fetchone()[0]
            adjustments = db.execute("SELECT COALESCE(SUM(budget_delta),0) FROM roster_transactions WHERE league_id=?", (league_id,)).fetchone()[0]
            if spent - int(outgoing["purchase_cost"]) + incoming_row["purchase_cost"] > int(league["budget"]) + adjustments:
                raise ValueError("Il costo della rosa dopo lo scambio supererebbe il budget.")
            db.execute("UPDATE roster SET owned=0 WHERE league_id=? AND player_id=?", (league_id, outgoing_id))
            self._upsert_owned_locked(db, league_id, incoming_row)
            stamp = datetime.now(timezone.utc).isoformat()
            db.executemany("""INSERT INTO roster_transactions
                (league_id,kind,player_name,role,team,amount,counterparty,note,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)""", [
                (league_id, "SCAMBIO_USCITA", outgoing["name"], outgoing["role"], outgoing["team"], int(outgoing["purchase_cost"]), counterparty.strip(), note.strip(), stamp),
                (league_id, "SCAMBIO_ENTRATA", incoming_row["name"], incoming_row["role"], incoming_row["team"], incoming_row["purchase_cost"], counterparty.strip(), note.strip(), stamp),
            ])

    def _upsert_owned_locked(self, db, league_id, row):
        values = tuple(row[key] for key in PLAYER_FIELDS)
        db.execute(f"""INSERT INTO players ({','.join(PLAYER_FIELDS)}) VALUES ({','.join('?' for _ in PLAYER_FIELDS)})
            ON CONFLICT(name) DO UPDATE SET {','.join(f'{key}=excluded.{key}' for key in PLAYER_FIELDS[1:])}""", values)
        player_id = db.execute("SELECT id FROM players WHERE name=? COLLATE NOCASE", (row["name"],)).fetchone()[0]
        db.execute("""INSERT INTO roster (league_id,player_id,purchase_cost,owned) VALUES (?,?,?,1)
            ON CONFLICT(league_id,player_id) DO UPDATE SET purchase_cost=excluded.purchase_cost,owned=1""",
            (league_id, player_id, row["purchase_cost"]))

    def export_workspace(self, league_id):
        league = self.league(league_id)
        catalog = self.complete_player_catalog(league_id)
        with self.connect() as db:
            lineups = [dict(row) for row in db.execute("SELECT season,matchday,formation,players_json,bench_json,saved_at FROM saved_lineups WHERE league_id=?", (league_id,))]
            transactions = [dict(row) for row in db.execute("""SELECT kind,player_name,role,team,amount,counterparty,note,created_at,budget_delta
                FROM roster_transactions WHERE league_id=? ORDER BY id""", (league_id,))]
            messages = [dict(row) for row in db.execute("SELECT role,content,created_at FROM assistant_log WHERE league_id=? ORDER BY id", (league_id,))]
        for row in lineups:
            row["players"] = json.loads(row.pop("players_json"))
            row["bench"] = json.loads(row.pop("bench_json"))
            if row["season"] == league["season"]:
                for field in ("players", "bench"):
                    row[field] = resolve_player_identities(row[field], catalog)
        data = {"format": "fantaoperator-workspace", "version": 2, "league": league,
                "roster": self.roster(league_id), "lineups": lineups,
                "transactions": transactions, "assistant_messages": messages}
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
            if data["format"] != "fantaoperator-workspace" or data["version"] not in (1, 2):
                raise ValueError("Formato backup non supportato.")
            rows = validate_roster(data["roster"])
            league = data["league"]
            league_fields = ("name", "platform", "mode", "participants", "budget", "matchday", "vote_provider", "vote_edition", "season")
            if data["version"] >= 2:
                league_fields += ("bench_size", "max_substitutions", "defense_modifier_enabled",
                    "defense_threshold_low", "defense_threshold_mid", "defense_threshold_high",
                    "defense_bonus_low", "defense_bonus_mid", "defense_bonus_high")
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
                bench = validate_roster(row.get("bench", []))
                if len(bench) > int(values.get("bench_size", 7)) or {p["name"].casefold() for p in players} & {p["name"].casefold() for p in bench}:
                    raise ValueError("Panchina non valida nel backup.")
                prepared.append((league_id, season, day, row["formation"], json.dumps(players),
                                 row.get("saved_at") or datetime.now(timezone.utc).isoformat(), json.dumps(bench)))
            transactions = data.get("transactions", [])
            messages = data.get("assistant_messages", [])
            if not isinstance(transactions, list) or len(transactions) > 5000 or not isinstance(messages, list) or len(messages) > 1000:
                raise ValueError("Storico del backup non valido.")
            prepared_transactions = []
            for row in transactions:
                if not isinstance(row, dict) or not str(row.get("player_name", "")).strip() or len(str(row.get("note", ""))) > 500:
                    raise ValueError("Movimento non valido nel backup.")
                amount = int(row.get("amount", 0))
                if not 0 <= amount <= 5000:
                    raise ValueError("Importo non valido nel backup.")
                budget_delta = int(row.get("budget_delta", 0))
                if not -5000 <= budget_delta <= 5000:
                    raise ValueError("Variazione budget non valida nel backup.")
                prepared_transactions.append((league_id, str(row.get("kind", "MOVIMENTO"))[:40], str(row["player_name"])[:120],
                    str(row.get("role", ""))[:10], str(row.get("team", ""))[:120], amount,
                    str(row.get("counterparty", ""))[:120], str(row.get("note", ""))[:500], str(row.get("created_at", ""))[:60],
                    budget_delta))
            prepared_messages = []
            for row in messages:
                if not isinstance(row, dict) or row.get("role") not in ("user", "assistant") or len(str(row.get("content", ""))) > 20000:
                    raise ValueError("Messaggio non valido nel backup.")
                prepared_messages.append((league_id, row["role"], str(row.get("content", "")), str(row.get("created_at", ""))[:60]))
        except (KeyError, TypeError, OverflowError) as exc:
            raise ValueError("Backup incompleto o non valido.") from exc
        # All validation completes before the first mutation. Failed restores are atomic.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(f"UPDATE leagues SET {','.join(f'{key}=?' for key in values)},scoring_json=?,source_url='',auto_sync_minutes=0 WHERE id=?",
                       (*values.values(), json.dumps(rules.as_dict()), league_id))
            self._replace_roster_locked(db, league_id, rows)
            db.execute("DELETE FROM saved_lineups WHERE league_id=?", (league_id,))
            db.executemany("""INSERT INTO saved_lineups
                (league_id,season,matchday,formation,players_json,saved_at,bench_json) VALUES (?,?,?,?,?,?,?)""", prepared)
            db.execute("DELETE FROM roster_transactions WHERE league_id=?", (league_id,))
            db.executemany("""INSERT INTO roster_transactions
                (league_id,kind,player_name,role,team,amount,counterparty,note,created_at,budget_delta) VALUES (?,?,?,?,?,?,?,?,?,?)""", prepared_transactions)
            db.execute("DELETE FROM assistant_log WHERE league_id=?", (league_id,))
            db.executemany("INSERT INTO assistant_log (league_id,role,content,created_at) VALUES (?,?,?,?)", prepared_messages)
            self._recalculate_locked(db, league_id)


def validate_league(values):
    result = dict(values)
    for key in ("name", "platform", "vote_provider", "vote_edition"):
        if key in result and (not isinstance(result[key], str) or not result[key].strip() or len(result[key]) > 120):
            raise ValueError("Nome, piattaforma, provider e redazione devono essere testi non vuoti (massimo 120 caratteri).")
    if "mode" in result and result["mode"] not in ("Classic", "Mantra"):
        raise ValueError("Modalità non valida.")
    for key, low, high in (("participants", 2, 20), ("budget", 50, 5000), ("matchday", 1, 38), ("auto_sync_minutes", 0, 60),
                           ("bench_size", 0, 15), ("max_substitutions", 0, 11)):
        if key in result:
            try:
                number = float(result[key])
                if not math.isfinite(number) or not number.is_integer() or not low <= number <= high:
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValueError(f"{key}: valore non valido.") from None
            result[key] = int(number)
    if "defense_modifier_enabled" in result:
        result["defense_modifier_enabled"] = int(bool(result["defense_modifier_enabled"]))
    for key in ("defense_threshold_low", "defense_threshold_mid", "defense_threshold_high",
                "defense_bonus_low", "defense_bonus_mid", "defense_bonus_high"):
        if key in result:
            try:
                number = float(result[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key}: valore non valido.") from None
            if not math.isfinite(number) or not -20 <= number <= 20:
                raise ValueError(f"{key}: valore fuori intervallo.")
            result[key] = number
    thresholds = [result.get(key) for key in ("defense_threshold_low", "defense_threshold_mid", "defense_threshold_high")]
    if all(value is not None for value in thresholds) and thresholds != sorted(thresholds):
        raise ValueError("Le soglie del modificatore difesa devono essere crescenti.")
    if "season" in result:
        result["season"] = season_name(result["season"])
    if "source_url" in result and result["source_url"]:
        from urllib.parse import urlsplit
        url = urlsplit(result["source_url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Usa un URL HTTPS senza credenziali nel nome host.")
        # DNS/private address checks occur at retrieval time, including redirects.
    return result
