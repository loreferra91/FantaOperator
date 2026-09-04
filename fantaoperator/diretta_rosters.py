"""Strict importer for the public Diretta.it Serie A 2026/27 squad article."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from .official_votes import season_name
from .public_votes import Page
from .sources import fetch_url, payload_hash

PROVIDER = "Diretta.it"
DIRETTA_ROSTERS_URL = "https://www.diretta.it/news/calcio-serie-a-serie-a-2026-27-tutte-le-rose-complete-le-20-squadre-divise-per-ruolo/8UPVU78M/"
EXPECTED_SEASON = "2026-27"
EXPECTED_TEAMS = {
    "Atalanta", "Bologna", "Cagliari", "Como", "Fiorentina", "Frosinone", "Genoa", "Inter",
    "Juventus", "Lazio", "Lecce", "Milan", "Monza", "Napoli", "Parma", "Roma", "Sassuolo",
    "Torino", "Udinese", "Venezia",
}
ROLE_LABELS = {"Portieri": "POR", "Difensori": "DIF", "Centrocampisti": "CEN", "Attaccanti": "ATT"}


@dataclass(frozen=True)
class SquadBatch:
    records: list[dict]
    warnings: tuple[str, ...]
    article_updated_at: str


def is_diretta_rosters_url(url: str) -> bool:
    try:
        expected = urlsplit(DIRETTA_ROSTERS_URL)
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname == expected.hostname
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment
                and parsed.path.rstrip("/") == expected.path.rstrip("/"))
    except (TypeError, ValueError):
        return False


def _one(nodes, label):
    if len(nodes) != 1:
        raise ValueError(f"Pagina rose cambiata o incompleta: {label}")
    return nodes[0]


def _clean(value: str, label: str) -> str:
    value = " ".join(value.replace("\xa0", " ").split()).strip(" ,.")
    if not value or len(value) > 120 or any(c in value for c in "<>\r\n"):
        raise ValueError(f"Valore non valido nella pagina rose: {label}")
    return value


def parse_diretta_rosters(payload: bytes, *, source_url: str, season: str) -> SquadBatch:
    if not is_diretta_rosters_url(source_url):
        raise ValueError("Le rose complete richiedono l'URL Diretta.it esatto in HTTPS")
    if season_name(season) != EXPECTED_SEASON:
        raise ValueError("La pagina Diretta.it disponibile riguarda soltanto la stagione 2026-27")
    page = Page(payload)
    heading = _one(page.root.find("h1"), "titolo").text()
    if not re.search(r"Serie A 2026/27, tutte le rose complete", heading, re.I):
        raise ValueError("Titolo o stagione della pagina rose non riconosciuti")
    bodies = [node for node in page.root.find("div") if node.attrs.get("itemprop") == "articleBody"
              and node.attrs.get("data-testid") == "fp-newsArticle-body"]
    body = _one(bodies, "corpo articolo")
    updated = _one([node for node in page.root.find() if node.attrs.get("itemprop") == "dateModified"],
                   "data aggiornamento").attrs.get("data-content", "")
    try:
        datetime.fromisoformat(updated.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Data di aggiornamento della pagina rose non valida") from None

    records, warnings, seen, roles_by_player = [], [], set(), {}
    team_headers, role_paragraphs = [], {}
    current_team = None
    for node in body.children:
        if node.tag == "h2":
            current_team = _clean(node.text(), "squadra")
            if current_team not in EXPECTED_TEAMS:
                raise ValueError(f"Squadra inattesa nella pagina rose: {current_team}")
            team_headers.append(current_team)
        elif node.tag == "p" and current_team:
            text = node.text()
            label, separator, values = text.partition(":")
            label = _clean(label, "ruolo")
            if not separator or label not in ROLE_LABELS:
                continue
            role = ROLE_LABELS[label]
            role_paragraphs[(current_team, role)] = role_paragraphs.get((current_team, role), 0) + 1
            names = [_clean(name, "giocatore") for name in values.split(",")]
            for name in names:
                identity = (current_team.casefold(), name.casefold())
                previous_role = roles_by_player.get(identity)
                if previous_role and previous_role != role:
                    raise ValueError(f"Ruolo contraddittorio per {name} ({current_team})")
                roles_by_player[identity] = role
                if identity in seen:
                    warnings.append(f"Duplicato nella fonte ignorato: {name} ({current_team})")
                    continue
                seen.add(identity)
                key = hashlib.sha256(f"{current_team.casefold()}|{name.casefold()}".encode()).hexdigest()[:24]
                records.append({"player_key": key, "name": name, "role": role, "team": current_team})
    teams = {row["team"] for row in records}
    if teams != EXPECTED_TEAMS or len(team_headers) != 20 or len(set(team_headers)) != 20:
        missing = ", ".join(sorted(EXPECTED_TEAMS - teams))
        raise ValueError(f"Pagina rose incompleta: squadre mancanti {missing or 'non riconoscibili'}")
    for team in EXPECTED_TEAMS:
        roles = {row["role"] for row in records if row["team"] == team}
        if roles != set(ROLE_LABELS.values()) or any(role_paragraphs.get((team, role)) != 1 for role in ROLE_LABELS.values()):
            raise ValueError(f"Pagina rose incompleta: ruoli mancanti per {team}")
    if len(records) < 400:
        raise ValueError("Pagina rose incompleta: numero di giocatori insufficiente")
    return SquadBatch(records, tuple(warnings), updated)


def sync_diretta_rosters(db, season: str):
    try:
        payload, _mime, final_url = fetch_url(DIRETTA_ROSTERS_URL, allow_html=True)
        if not is_diretta_rosters_url(final_url):
            raise ValueError("Redirect Diretta.it inatteso: aggiornamento non applicato")
        batch = parse_diretta_rosters(payload, source_url=final_url, season=season)
        result = db.replace_squad_catalog(season, PROVIDER, batch.records, source_url=final_url,
            source_hash=payload_hash(payload), article_updated_at=batch.article_updated_at, warnings=batch.warnings)
        return {**result, "linked": sum(db.link_roster_to_votes(league["id"]) for league in db.leagues()
                                        if db.league(league["id"])["season"] == season_name(season))}
    except Exception as exc:
        error = str(exc) if isinstance(exc, ValueError) else "Errore nel catalogo rose; aggiornamento non applicato"
        db.log_failed_squad_sync(season, PROVIDER, DIRETTA_ROSTERS_URL, error)
        return {"ok": False, "error": error}
