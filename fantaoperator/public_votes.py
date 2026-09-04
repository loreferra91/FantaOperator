"""Parser originale della pagina pubblica Fantacalcio.it, senza login o JavaScript.

Contratto verificato il 2026-09-02: grades.page.js seleziona /stagione/giornata;
grades.page.min.css visualizza 55 come placeholder 6 senza FV e 56 come '-'.
Nessuno dei due codici viene trasformato in un voto d'ufficio.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlsplit

from .official_votes import EDITIONS, VoteBatch, normalize_rows, number, season_name
from .sources import MAX_PAYLOAD

PUBLIC_VOTES_URL = "https://www.fantacalcio.it/voti-fantacalcio-serie-a"
BONUSES = {
    "Gol segnati": "goals", "Gol subiti": "goals_conceded", "Autoreti": "own_goals",
    "Rigori segnati": "penalties_scored", "Rigori sbagliati": "penalties_missed",
    "Rigori parati": "penalties_saved", "Assist": "assists",
}


def is_public_votes_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname == "www.fantacalcio.it"
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment
                and bool(re.fullmatch(r"/voti-fantacalcio-serie-a(?:/20\d{2}-\d{2}/\d{1,2})?/?", parsed.path)))
    except ValueError:
        return False


def public_votes_url(season: str, matchday: int) -> str:
    if not isinstance(matchday, int) or isinstance(matchday, bool) or not 1 <= matchday <= 38:
        raise ValueError("Giornata non valida")
    return f"{PUBLIC_VOTES_URL}/{season_name(season)}/{matchday}"


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    parts: list[str] = field(default_factory=list)

    def find(self, tag=None, css=None):
        found = []
        for child in self.children:
            if (tag is None or child.tag == tag) and (css is None or css in child.attrs.get("class", "").split()):
                found.append(child)
            found.extend(child.find(tag, css))
        return found

    def text(self):
        return " ".join(" ".join(self.parts).split())


class Page(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, payload):
        super().__init__(convert_charrefs=True)
        if len(payload) > MAX_PAYLOAD:
            raise ValueError("Pagina troppo grande")
        self.root = Node("document")
        self.stack = [self.root]
        self.comments = []
        self.count = 0
        self.feed(payload.decode("utf-8-sig"))
        self.close()

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 100000 or len(self.stack) > 150:
            raise ValueError("Struttura HTML troppo complessa")
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for node in self.stack:
            node.parts.append(data)

    def handle_comment(self, data):
        self.comments.append(data.strip())


def one(nodes, label):
    if len(nodes) != 1:
        raise ValueError(f"Pagina voti cambiata o incompleta: {label}")
    return nodes[0]


def parse_public_votes(payload: bytes, *, source_url: str, provider: str, edition: str,
                       season: str, matchday: int) -> VoteBatch:
    if not is_public_votes_url(source_url) or provider != "Fantacalcio.it":
        raise ValueError("La pagina pubblica richiede la fonte esatta Fantacalcio.it in HTTPS")
    if edition not in EDITIONS:
        raise ValueError("Redazione non presente nella pagina ufficiale; nessuna sostituzione consentita")
    page = Page(payload)
    seasons = {season_name(c.split(":", 1)[1].strip()) for c in page.comments if c.startswith("Season:")}
    days = {c.split(":", 1)[1].strip() for c in page.comments if c.startswith("Matchweek:")}
    if seasons != {season_name(season)} or days != {str(matchday)}:
        raise ValueError("Stagione/giornata della pagina diversa da quella richiesta o non disponibile")
    tables = page.root.find("table", "grades-table")
    if not tables:
        raise ValueError("Nessun voto pubblico disponibile; pagina vuota, login o formato cambiato")
    records = []
    for table in tables:
        head = one(table.find("thead"), "intestazione")
        team = one(head.find("a", "team-name"), "squadra").text()
        # Edition order is read and verified per table, never guessed by index.
        headers = [n.attrs.get("title") for n in head.find("img") if n.attrs.get("title") in EDITIONS]
        if len(headers) != 4 or set(headers[:3]) != set(EDITIONS) or headers[3] != EDITIONS[0]:
            raise ValueError("Intestazioni redazioni/bonus cambiate; import bloccato")
        edition_index = headers[:3].index(edition)
        body = one(table.find("tbody"), "righe giocatori")
        for row in body.find("tr"):
            role = one(row.find(css="role"), "ruolo").attrs.get("data-value", "").lower()
            if role == "all":
                continue
            if role not in {"p", "d", "c", "a"}:
                raise ValueError("Ruolo non riconosciuto nella pagina pubblica")
            name = one(row.find(css="player-name"), "giocatore").text()
            votes = row.find(css="player-grade")
            if len(votes) != 3:
                raise ValueError(f"Colonne voto incomplete: {name}")
            selected = votes[edition_index]
            raw_vote = selected.attrs.get("data-value")
            if raw_vote is None or not raw_vote.strip():
                raise ValueError(f"Voto mancante: {name}")
            classes = set(selected.attrs.get("class", "").split())
            if classes - {"player-grade", "yellow-card", "red-card"}:
                raise ValueError(f"Stato voto/cartellino non riconosciuto: {name}")
            counters = {}
            for bonus in row.find(css="player-bonus"):
                title, raw = bonus.attrs.get("title"), bonus.attrs.get("data-value")
                if title not in {*BONUSES, "Player of the match"} or title in counters:
                    raise ValueError(f"Colonne bonus cambiate: {name}")
                value = number(raw, f"{title} ({name})")
                if not value.is_integer() or not 0 <= value <= 100:
                    raise ValueError(f"Conteggio bonus non valido: {name}")
                counters[title] = int(value)
            if set(counters) != {*BONUSES, "Player of the match"}:
                raise ValueError(f"Bonus incompleti: {name}")
            record = {"player": name, "team": team, "role": role,
                      "vote": None if raw_vote in {"55", "56"} else raw_vote,
                      "status": "PROVVISORIO", "clean_sheet": None,
                      "yellow_cards": int("yellow-card" in classes),
                      "red_cards": int("red-card" in classes),
                      **{field: counters[title] for title, field in BONUSES.items()}}
            # Public 'Gol segnati' excludes penalties (unlike our normalized schema).
            record["goals"] += record["penalties_scored"]
            records.append(record)
    return VoteBatch(normalize_rows(records), (
        "Pagina primaria ricontrollata; consolidamento non attestato: dati PROVVISORI, non voti live garantiti.",
        "Codici speciali 55/56 senza fantavoto lasciati senza punteggio; nessun voto d’ufficio automatico.",
        "Bonus della Redazione Fantacalcio. Clean sheet non disponibile; Player of the match e modificatori non applicati.",
    ))
