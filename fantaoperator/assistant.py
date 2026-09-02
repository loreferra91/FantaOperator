from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Mapping, Sequence

from .engine import compare_players, optimize_lineup


def _source_line(latest_sync: Mapping[str, Any] | None) -> str:
    if not latest_sync:
        return "Nessun aggiornamento voti verificato. Importa una fonte ufficiale in **Voti & dati**."
    status = latest_sync.get("status", "N/D")
    checked = str(latest_sync.get("checked_at", "")).replace("T", " ").replace("+00:00", " UTC")
    origin = "Import locale, non verificato sul Web" if latest_sync.get("provenance") == "IMPORT_LOCALE" else "Feed configurato; provenienza dichiarata dal feed"
    if status == "ERRORE":
        return f"Ultimo tentativo fallito: **{checked}**. I dati precedenti restano in archivio, non sono stati riverificati."
    return f"Fonte: **{latest_sync.get('source_name', 'N/D')} / {latest_sync.get('edition', '')}** · Stato: **{status}** · Ultima acquisizione: **{checked}**. {origin}."


def _votes(records: Sequence[Mapping[str, Any]], only_live: bool = False) -> str:
    visible = [r for r in records if not only_live or r.get("status") in {"LIVE", "PROVVISORIO"}]
    if not visible:
        return "Non risultano voti disponibili per questa giornata."
    lines = ["| Giocatore | Voto | Fantavoto | Stato |", "|---|---:|---:|---|"]
    for row in visible:
        vote = "S.V." if row['official_vote'] is None else f"{row['official_vote']:.1f}"
        fv = "N/D" if row['fantavote'] is None else f"{row['fantavote']:.1f}"
        lines.append(f"| {row['name']} | {vote} | {fv} | {row['status']} |")
    return "\n".join(lines)


def _changes(latest_sync: Mapping[str, Any] | None) -> str:
    if not latest_sync:
        return "Nessun aggiornamento precedente da confrontare."
    if latest_sync.get("status") == "ERRORE":
        return "Verifica non riuscita: non è possibile stabilire se esistono rettifiche."
    changes = json.loads(str(latest_sync.get("changes_json") or "[]"))
    if not changes:
        return "Nessuna modifica rilevata rispetto alla verifica precedente."
    lines = []
    for change in changes[:20]:
        details = []
        for field, values in change.get("fields", {}).items():
            details.append(f"{field}: {values.get('prima')} → {values.get('dopo')}")
        lines.append(f"- **{change.get('player')}** — " + "; ".join(details))
    return "Modifiche rilevate:\n" + "\n".join(lines)


def respond(db, league: dict, matchday: int, query: str) -> str:
    """Dynamic vote requests always attempt retrieval before consulting stored rows."""
    from .updater import refresh_votes
    dynamic = bool(re.search(r"/VOTI|/AGGIORNAVOTI|\b(voti|voto|fantavoti|fantavoto|bonus|malus|gol|assist|ammonizioni|espulsioni|rigori|autogol|clean sheet|punteggio|statistiche)\b", query, re.I))
    notice = ""
    if dynamic:
        result = refresh_votes(db, league, matchday)
        if not result["ok"]:
            notice = f"⚠️ **Non verificato adesso.** {result['error']}\n\n"
            if query.strip().upper().startswith("/AGGIORNAVOTI"):
                return notice + "Nessun confronto nuovo eseguito: non mostro rettifiche precedenti come appena rilevate."
        else:
            notice = f"Feed ricontrollato per **{league['season']} · giornata {matchday}**.\n\n"
    routed_query = "/VOTI" if dynamic and not query.strip().startswith("/") else query
    return notice + answer(routed_query, league=league, roster=db.roster(league["id"]),
        records=db.records(league["id"], matchday), latest_sync=db.latest_sync(league["id"], matchday))


def answer(
    query: str,
    *,
    league: Mapping[str, Any],
    roster: Sequence[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
    latest_sync: Mapping[str, Any] | None,
) -> str:
    clean = query.strip()
    command = clean.split(maxsplit=1)[0].upper() if clean else ""
    if command in {"/VOTI", "/VOTILIVE"}:
        body = _votes(records, only_live=command == "/VOTILIVE")
        if latest_sync and any(row.get("checked_at") != latest_sync.get("checked_at") for row in records):
            body += "\n\n⚠️ Alcuni record risalgono ad acquisizioni precedenti. Consulta i timestamp in Voti & dati."
        return f"### 🎯 Verdetto\n{body}\n\n{_source_line(latest_sync)}"
    if command == "/AGGIORNAVOTI":
        return f"### 🎯 Verdetto\n{_changes(latest_sync)}\n\n{_source_line(latest_sync)}"
    if command in {"/FORMAZIONE", "/GIORNATA"} or any(word in clean.lower() for word in ("formazione", "schierare", "titolare")):
        formation, selected, total = optimize_lineup(roster)
        names = ", ".join(str(p["name"]) for p in selected)
        return (
            f"### 🎯 Verdetto\n**{formation}** · {total:.1f} fantapunti attesi\n\n"
            f"**Undici:** {names}.\n\n### ⚠️ Rischio\n"
            "Controlla convocazioni e ballottaggi prima della deadline. " + _source_line(latest_sync)
        )
    names = {str(p["name"]).lower(): p for p in roster}
    mentioned = [p for name, p in names.items() if name in clean.lower()]
    if len(mentioned) >= 2 or command == "/COMPARE":
        if len(mentioned) < 2:
            return "Indica due giocatori presenti in rosa per effettuare il confronto."
        result = compare_players(mentioned[0], mentioned[1])
        return (
            f"### 🎯 Verdetto\n**{result['verdict']}** lo switch {mentioned[0]['name']} → {mentioned[1]['name']}.\n\n"
            f"### 💰 Value\nDelta corretto per titolarità e rischio: **{result['delta']:+.2f}**.\n\n"
            f"### ⚠️ Rischio\nConfidence **{result['confidence']}%**. Verifica sempre i dati dinamici prima della scelta finale."
        )
    if command in {"/ASTA", "/ASTALIVE", "/VALUE"} or "asta" in clean.lower():
        targets = sorted(roster, key=lambda p: (float(p.get("expected", 0)) / max(float(p.get("price", 1)), 1)), reverse=True)
        best = targets[0] if targets else None
        if not best:
            return "La rosa non contiene giocatori valutabili."
        return (
            f"### 🎯 Verdetto\nIl miglior value attuale è **{best['name']}**.\n\n"
            f"### 💰 Prezzo / Value\nValore stimato {best['price']} crediti, tier {best['tier']}, "
            f"{best['expected']:.1f} FP attesi. Fissa lo stop loss prima del rilancio.\n\n"
            "### ✅ Azione\nUsa la War Room per calcolare il prezzo massimo sul budget residuo."
        )
    return (
        "### 🎯 Verdetto\nPosso operare sui dati salvati della tua lega.\n\n"
        "### ✅ Azione\nUsa **/FORMAZIONE**, **/VOTI**, **/VOTILIVE**, **/AGGIORNAVOTI** o chiedimi un confronto tra due giocatori della rosa.\n\n"
        + _source_line(latest_sync)
    )
