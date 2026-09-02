"""Private export adapter. Not a verified replacement for Fantacalcio's private API.

No endpoint guessing, browser-cookie extraction or website JavaScript execution.
An authorized endpoint must expose the normalized contract described in README.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from .official_votes import season_name
from .sources import fetch_url, payload_hash, safe_url


def read_authorized_session(cookie_file: str | None = None) -> str:
    if cookie_file:
        path = Path(cookie_file)
        if path.stat().st_mode & 0o077:
            raise ValueError("File sessione accessibile ad altri utenti: imposta permessi 600")
        if path.stat().st_size > 16384:
            raise ValueError("File sessione troppo grande")
        cookie = path.read_text(encoding="utf-8").strip()
    else:
        cookie = os.environ.get("FANTACALCIO_COOKIE", "").strip()
    if not cookie or any(c in cookie for c in "\r\n") or "=" not in cookie:
        raise ValueError("Sessione autorizzata non configurata o non valida")
    return cookie


def fetch_private_export(url: str, *, league_slug: str, season: str, matchday: int,
                         cookie_file: str | None = None) -> dict:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "leghe.fantacalcio.it" or parsed.port not in (None, 443):
        raise ValueError("Export privato ammesso solo su https://leghe.fantacalcio.it")
    cookie = read_authorized_session(cookie_file)
    payload, _, final_url = fetch_url(url, cookie=cookie)
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Formato export privato non supportato: nessun dato estratto") from None
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Schema export privato non supportato; adattatore specifico ancora da validare")
    if data.get("league") != league_slug or season_name(data.get("season")) != season_name(season) or data.get("matchday") != matchday:
        raise ValueError("Export relativo a un'altra lega, stagione o giornata")
    for key in ("rosters", "lineups", "results"):
        if not isinstance(data.get(key), list):
            raise ValueError(f"Export privo della lista {key}")
    # Raw private API formats are deliberately not passed to the vote store or assistant.
    return {"schema_version": 1, "league": league_slug, "season": season_name(season),
            "matchday": matchday, **{key: data[key] for key in ("rosters", "lineups", "results")},
            "source_url": safe_url(final_url), "source_hash": payload_hash(payload)}
