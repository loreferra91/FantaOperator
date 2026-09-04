"""Gazzetta's public V/FV tables, verified against the publisher on 2026-09-04."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .official_votes import NO_VOTE, VoteBatch, normalize_rows, number, season_name
from .public_votes import Page, one

PROVIDER = "Gazzetta"
EDITION = "La Gazzetta dello Sport"
BASE_URL = "https://www.gazzetta.it/calcio/fantanews/voti"
HEADERS = ("V", "G", "A", "R", "RS", "AG", "AM", "ES", "FV")


def is_gazzetta_votes_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return bool(parsed.scheme == "https" and parsed.hostname == "www.gazzetta.it"
                    and parsed.port in (None, 443) and not parsed.username and not parsed.password
                    and not parsed.query and not parsed.fragment
                    and re.fullmatch(r"/calcio/fantanews/voti/serie-a-20\d{2}-\d{2}(?:/giornata-\d{1,2})?/?", parsed.path))
    except (ValueError, TypeError):
        return False


def gazzetta_votes_url(season: str, matchday: int | None = None) -> str:
    url = f"{BASE_URL}/serie-a-{season_name(season)}"
    if matchday is None:
        return url + "/"
    if isinstance(matchday, bool) or not isinstance(matchday, int) or not 1 <= matchday <= 38:
        raise ValueError("Giornata non valida.")
    return f"{url}/giornata-{matchday}"


def _count(value, label, *, signed=False):
    if value == "-":
        return 0
    result = number(value, label)
    if not result.is_integer() or not (-100 if signed else 0) <= result <= 100:
        raise ValueError(f"Conteggio Gazzetta non valido: {label}.")
    return int(result)


def _vote(value, *, fantasy=False):
    if value.lower() in NO_VOTE:
        return None
    result = number(value, "fantavoto Gazzetta" if fantasy else "voto Gazzetta")
    low, high = (-100, 100) if fantasy else (0, 10)
    if not low <= result <= high:
        raise ValueError("Voto Gazzetta fuori intervallo.")
    return result


def parse_gazzetta_votes(payload: bytes, *, source_url: str, provider: str,
                        edition: str, season: str, matchday: int) -> VoteBatch:
    if not is_gazzetta_votes_url(source_url) or provider != PROVIDER or edition != EDITION:
        raise ValueError("La pagina Gazzetta richiede provider Gazzetta e redazione La Gazzetta dello Sport.")
    expected_season = season_name(season)
    gazzetta_votes_url(season, matchday)  # Validates the requested day too.
    path = urlsplit(source_url).path.rstrip("/")
    if path not in (urlsplit(gazzetta_votes_url(season)).path.rstrip("/"),
                    urlsplit(gazzetta_votes_url(season, matchday)).path):
        raise ValueError("Stagione/giornata nell'URL Gazzetta diversa da quella richiesta.")
    # The response's HTTP charset is ISO-8859-1, but the public page is UTF-8.
    # Decode strictly: silently substituting bytes would corrupt player identities.
    page = Page(payload)
    title = one(page.root.find("h1"), "titolo Gazzetta").text()
    match = re.fullmatch(r"Voti Fantacalcio Serie A (\d{1,2}) Giornata Stagione (20\d{2}/20\d{2})", title)
    if not match or int(match[1]) != matchday or season_name(match[2]) != expected_season:
        raise ValueError("Stagione/giornata della pagina Gazzetta diversa da quella richiesta o non disponibile.")
    # The HTML duplicates every table in a second 'matchView' layout. Parse only
    # the explicit list view; never deduplicate conflicting datasets by name.
    group = one([n for n in page.root.find(css="magicDayList")
                 if "listView" in n.attrs.get("class", "").split()], "elenco voti Gazzetta")
    tables = group.find("ul", "magicTeamList")
    if not tables:
        raise ValueError("Nessun voto Gazzetta disponibile; pagina vuota o formato cambiato.")
    records, teams = [], set()
    for table in tables:
        rows = [n for n in table.children if n.tag == "li"]
        head = one([n for n in rows if "head" in n.attrs.get("class", "").split()], "colonne Gazzetta")
        if tuple(c.text() for c in head.find(css="inParameter")) != HEADERS:
            raise ValueError("Colonne dei voti Gazzetta cambiate: import bloccato.")
        team = one(head.find(css="teamNameIn"), "squadra Gazzetta").text()
        if not team or team.casefold() in teams:
            raise ValueError("Squadra Gazzetta mancante o duplicata.")
        teams.add(team.casefold())
        for row in rows:
            if row is head:
                continue
            name = one(row.find(css="playerNameIn"), "giocatore Gazzetta").text()
            link = one(one(row.find(css="playerNameIn"), "giocatore Gazzetta").find("a"), "identità giocatore Gazzetta")
            identity = re.fullmatch(r"https://www\.gazzetta\.it/calcio/giocatori/[^/]+/(\d+)/?", link.attrs.get("href", ""))
            if not identity:
                raise ValueError(f"Identificativo Gazzetta mancante: {name}.")
            roles = [n.text().upper() for n in row.find(css="playerRole")
                     if "show-for-small" not in n.attrs.get("class", "").split()]
            if len(roles) != 1 or roles[0] not in ("POR", "DIF", "CEN", "ATT"):
                raise ValueError(f"Ruolo Gazzetta non riconosciuto: {name}.")
            role = roles[0]
            columns = row.find(css="inParameter")
            if len(columns) != 9 or "vParameter" not in columns[0].attrs.get("class", "").split() or "fvParameter" not in columns[-1].attrs.get("class", "").split():
                raise ValueError(f"Voti/bonus Gazzetta incompleti: {name}.")
            values = [c.text() for c in columns]
            goals = _count(values[1], "G", signed=True)
            penalties = _count(values[3], "R")
            if (role == "POR" and goals > 0) or (role != "POR" and goals < 0):
                raise ValueError(f"Gol fatti/subiti ambigui per il ruolo Gazzetta: {name}.")
            # G includes scored penalties (publisher examples: Dybala 2023/24 day
            # 26: V=8, G=3, R=1, FV=17). R must not be added to G a second time.
            record = {"player": name, "role": role, "team": team, "provider_player_id": identity[1],
                      "vote": _vote(values[0]), "provider_fantavote": _vote(values[8], fantasy=True),
                      "goals": max(goals, 0), "goals_conceded": max(-goals, 0),
                      "assists": _count(values[2], "A"),
                      "penalties_saved": penalties if role == "POR" else 0,
                      "penalties_scored": penalties if role != "POR" else 0,
                      "penalties_missed": _count(values[4], "RS"),
                      "own_goals": _count(values[5], "AG"),
                      "yellow_cards": _count(values[6], "AM"),
                      "red_cards": _count(values[7], "ES"),
                      "clean_sheet": None, "status": "PROVVISORIO"}
            records.append(record)
    return VoteBatch(normalize_rows(records), (
        "Voti e fantavoti pubblicati da La Gazzetta dello Sport. Consolidamento non attestato: stato PROVVISORIO.",
        "FV Gazzetta conservato separatamente dal fantavoto ricalcolato con il regolamento della lega.",
        "Clean sheet non esplicito: nessuna deduzione dal fantavoto pubblicato. S.V., sostituzioni e modificatori non applicati automaticamente.",
    ))


def configure_preferred_source(db):
    """Apply the user's project-wide Gazzetta choice once, preserving old archives.

    Kept outside Database initialization so generic collector consumers remain
    provider-neutral. A persisted marker lets later explicit setting changes win.
    """
    with db.connect() as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS app_preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("BEGIN IMMEDIATE")
        # Also upgrade already-open Streamlit sessions after a hot deploy.
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(players)")}
        for column in ("vote_provider", "provider_player_id"):
            if column not in columns:
                connection.execute(f"ALTER TABLE players ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        if connection.execute("SELECT 1 FROM app_preferences WHERE key='gazzetta_source_v1'").fetchone():
            return
        for league in connection.execute("SELECT id,season FROM leagues").fetchall():
            connection.execute("UPDATE leagues SET vote_provider=?,vote_edition=?,source_url=? WHERE id=?",
                               (PROVIDER, EDITION, gazzetta_votes_url(league["season"]), league["id"]))
        connection.execute("INSERT INTO app_preferences VALUES ('gazzetta_source_v1','applied')")
