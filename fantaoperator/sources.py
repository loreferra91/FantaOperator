from __future__ import annotations

import csv
import hashlib
import io
import json
import ipaddress
import socket
import ssl
import certifi
from typing import Any
from urllib.parse import urlparse, urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler
from urllib.error import HTTPError, URLError


ALIASES = {
    "giocatore": "player", "nome": "player", "name": "player",
    "voto": "vote", "voto ufficiale": "vote", "official_vote": "vote",
    "ruolo": "role", "squadra": "team", "stato": "status",
    "gol": "goals", "goal": "goals", "assist": "assists",
    "ammonizioni": "yellow_cards", "ammonizione": "yellow_cards",
    "espulsioni": "red_cards", "espulsione": "red_cards",
    "autogol": "own_goals", "gol subiti": "goals_conceded",
    "rigori parati": "penalties_saved", "clean sheet": "clean_sheet",
    "rigori segnati": "penalties_scored", "rigori sbagliati": "penalties_missed",
    "gf": "goals", "gs": "goals_conceded", "rp": "penalties_saved",
    "amm": "yellow_cards", "esp": "red_cards", "au": "own_goals", "ass": "assists",
    "r": "role", "cod.": "provider_player_id", "official vote": "vote",
    "stagione": "season", "giornata": "matchday", "redazione": "edition",
    "bonus personalizzato": "custom_bonus", "malus personalizzato": "custom_malus",
}

MAX_PAYLOAD = 5 * 1024 * 1024


def payload_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def normalize_key(key: str) -> str:
    cleaned = " ".join(str(key).strip().lower().replace("_", " ").split())
    return ALIASES.get(cleaned, cleaned.replace(" ", "_"))


def normalized_fields(raw: dict) -> dict:
    result = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Intestazione mancante o non valida")
        normalized = normalize_key(key)
        if normalized in result:
            raise ValueError(f"Intestazioni duplicate o equivalenti: {normalized}")
        result[normalized] = value
    return result


def load_json(payload: bytes | str):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Campo JSON duplicato: {key}")
            result[key] = value
        return result
    return json.loads(payload, object_pairs_hook=unique_pairs)


def parse_payload(payload: bytes, filename: str = "data.csv", content_type: str = "") -> list[dict[str, Any]]:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("File troppo grande: limite 5 MB")
    text = payload.decode("utf-8-sig")
    is_json = filename.lower().endswith(".json") or "json" in content_type.lower() or text.lstrip().startswith(("[", "{"))
    if is_json:
        data = load_json(text)
        if isinstance(data, dict):
            data = data.get("players") or data.get("records") or data.get("data") or [data]
        if not isinstance(data, list):
            raise ValueError("Il JSON deve contenere una lista di record")
        raw_rows = data
    else:
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        fields = [normalize_key(key) for key in reader.fieldnames or []]
        if len(fields) != len(set(fields)):
            raise ValueError("Intestazioni CSV duplicate o equivalenti")
        raw_rows = []
        for raw in reader:
            if None in raw or any(value is None for value in raw.values()):
                raise ValueError("Numero di colonne CSV non corrispondente alle intestazioni")
            raw_rows.append(raw)
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise ValueError("Ogni record deve essere un oggetto")
        row = normalized_fields(raw)
        if row.get("player") in (None, ""):
            raise ValueError("Nome giocatore mancante in una riga")
        rows.append(row)
    if not rows:
        raise ValueError("Nessun record giocatore riconosciuto")
    if any("vote" not in row or row["vote"] == "" for row in rows):
        raise ValueError("Colonna voto mancante in una o più righe")
    return rows


def safe_url(url: str) -> str:
    """Only a credential-free URL may enter audit records or UI messages."""
    try:
        parsed = urlsplit(url)
        return urlunsplit((parsed.scheme, parsed.netloc.split("@")[-1], parsed.path, "", ""))
    except ValueError:
        return "[URL non valido]"


def validate_url(url: str, *, allow_private: bool = False) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Inserisci un URL HTTP o HTTPS valido")
    if parsed.username or parsed.password:
        raise ValueError("Credenziali nell'URL non ammesse")
    if not allow_private:
        try:
            addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        except OSError:
            raise ValueError("Dominio della fonte non risolvibile") from None
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError("Indirizzi locali o riservati non ammessi come fonte remota")


def fetch_url(url: str, timeout: int = 12, *, allow_private: bool = False,
              cookie: str | None = None) -> tuple[bytes, str, str]:
    validate_url(url, allow_private=allow_private)
    if cookie:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in {"www.fantacalcio.it", "leghe.fantacalcio.it"} or parsed.port not in (None, 443):
            raise ValueError("Sessione autorizzata solo sui domini esatti Fantacalcio.it in HTTPS")
        if any(c in cookie for c in "\r\n"):
            raise ValueError("Formato sessione non valido")

    class CheckedRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if cookie:
                raise ValueError("Redirect bloccato: sessione non inoltrata. Verifica l'accesso autorizzato.")
            validate_url(newurl, allow_private=allow_private)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    headers = {"User-Agent": "FantaOperator/2.0", "Cache-Control": "no-cache", "Accept": "application/json,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    if cookie:
        headers["Cookie"] = cookie
    request = Request(url, headers=headers)
    tls = ssl.create_default_context()
    tls.load_verify_locations(cafile=certifi.where())
    try:
        with build_opener(CheckedRedirect(), HTTPSHandler(context=tls)).open(request, timeout=timeout) as response:
            content_length = int(response.headers.get("Content-Length") or 0)
            if content_length > MAX_PAYLOAD:
                raise ValueError("Sorgente troppo grande: limite 5 MB")
            payload = response.read(MAX_PAYLOAD + 1)
            if len(payload) > MAX_PAYLOAD:
                raise ValueError("Sorgente troppo grande: limite 5 MB")
            content_type = response.headers.get("Content-Type", "")
            if "html" in content_type or payload.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                raise ValueError("La fonte restituisce una pagina HTML/login, non un export voti. Accesso o formato da verificare.")
            return payload, content_type, response.geturl()
    except HTTPError as exc:
        raise ValueError(f"Fonte non accessibile (HTTP {exc.code}); nessun dato aggiornato") from None
    except (URLError, TimeoutError, OSError):
        raise ValueError("Connessione alla fonte non riuscita; nessun dato aggiornato") from None


def csv_template() -> bytes:
    return (
        "player,role,team,vote,status,goals,assists,yellow_cards,red_cards,own_goals,"
        "goals_conceded,penalties_saved,penalties_scored,penalties_missed,clean_sheet,custom_bonus,custom_malus\n"
        "Esempio Giocatore,CEN,Squadra,6.5,PROVVISORIO,1,0,0,0,0,0,0,0,0,false,0,0\n"
    ).encode("utf-8")
