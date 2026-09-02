"""One update pipeline shared by Streamlit, assistant commands and the CLI worker."""
from __future__ import annotations

import argparse
import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Lock

from .official_votes import parse_votes
from .sources import fetch_url, payload_hash
from .vote_store import context

_update_lock = Lock()


@contextmanager
def collector_lock(db):
    # macOS/Linux advisory lock also prevents an old in-flight worker response
    # from overtaking a newer response acquired by the Streamlit process.
    import fcntl
    with _update_lock, db.path.with_suffix(".sync.lock").open("a") as lockfile:
        fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN)


def import_votes(db, league, matchday, payload, filename, content_type="", *,
                 source_url="", remote=False, default_status="PROVVISORIO"):
    with collector_lock(db):
        return _import_votes(db, league, matchday, payload, filename, content_type,
            source_url=source_url, remote=remote, default_status=default_status)


def _import_votes(db, league, matchday, payload, filename, content_type="", *,
                  source_url="", remote=False, default_status="PROVVISORIO"):
    batch = parse_votes(payload, filename, content_type, provider=league["vote_provider"],
                        edition=league["vote_edition"], season=league["season"], matchday=matchday,
                        remote=remote, default_status=default_status)
    result = db.import_records(league["id"], matchday, batch.records,
        source_name=league["vote_provider"], source_url=source_url,
        payload_hash=payload_hash(payload), default_status=default_status,
        provenance="FEED_CONFIGURATO" if remote else "IMPORT_LOCALE", expected_context=context(league),
        expected_source_url=league["source_url"] if remote else None)
    return {**result, "warnings": batch.warnings}


def refresh_votes(db, league, matchday):
    if not league.get("source_url"):
        return {"ok": False, "error": "Nessun feed configurato: verifica Web non disponibile. I dati salvati non sono aggiornati in questa richiesta."}
    # Serializes network-to-commit within this process; SQLite transactions protect writes across processes.
    with collector_lock(db):
        try:
            url = str(league["source_url"]).replace("{season}", league["season"]).replace("{matchday}", str(matchday))
            payload, mime, final_url = fetch_url(url)
            result = _import_votes(db, league, matchday, payload, final_url, mime, source_url=final_url, remote=True)
            return {"ok": True, **result}
        except Exception as exc:
            # Expected validation messages are safe; unknown libraries may include response bodies/secrets.
            error = str(exc) if isinstance(exc, ValueError) else "Errore nel collector; aggiornamento non applicato"
            db.log_failed_sync(league["id"], matchday, league["vote_provider"], league["source_url"], error,
                               expected_context=context(league), expected_source_url=league["source_url"])
            return {"ok": False, "error": error}


def sync_due(league, latest):
    minutes = int(league.get("auto_sync_minutes") or 0)
    if not minutes or not league.get("source_url"):
        return False
    if not latest:
        return True
    try:
        checked = datetime.fromisoformat(latest["checked_at"].replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - checked).total_seconds() >= minutes * 60
    except (ValueError, KeyError, TypeError):
        return True


def run_due(db):
    results = []
    for summary in db.leagues():
        league = db.league(summary["id"])
        day = league["matchday"]
        if sync_due(league, db.latest_sync(league["id"], day)):
            results.append({"league_id": league["id"], "matchday": day, **refresh_votes(db, league, day)})
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="Collector FantaOperator: nessuna dipendenza GitHub o chiave AI")
    parser.add_argument("--db", help="Database SQLite (default FANTAOPERATOR_DB)")
    parser.add_argument("--league", type=int, default=1)
    parser.add_argument("--matchday", type=int)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--once", action="store_true", help="Verifica subito la fonte della lega")
    modes.add_argument("--watch", action="store_true", help="Aggiorna tutte le leghe agli intervalli configurati finché il processo resta attivo")
    args = parser.parse_args(argv)
    from .database import Database
    db = Database(args.db)
    if args.once:
        league = db.league(args.league)
        result = refresh_votes(db, league, args.matchday or league["matchday"])
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1
    try:
        while True:
            for result in run_due(db):
                # Worker logs contain only outcome counts, never cookies or source URLs.
                print(json.dumps({k: result[k] for k in ("league_id", "matchday", "ok", "rows", "changed", "error") if k in result}), flush=True)
            time.sleep(5)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
