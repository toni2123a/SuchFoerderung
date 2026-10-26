"""Einfacher Hintergrund-Zeitplan: Import beim Start (falls keine Daten), danach täglich."""
import logging
import threading
from datetime import datetime, timezone

from . import config, db
from .importers import foerder, vergabe

log = logging.getLogger(__name__)
_stop = threading.Event()
_thread: threading.Thread | None = None
state = {"running": set()}


def _hours_since(ts: str | None) -> float:
    if not ts:
        return float("inf")
    return (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() / 3600


def due_sources(conn) -> list[str]:
    due = []
    ok = db.last_run(conn, "foerder", "ok")
    if not ok or _hours_since(ok["finished_at"]) >= config.REFRESH_AFTER_HOURS:
        last = db.last_run(conn, "foerder")
        # nach einem Fehlschlag frühestens nach einer Stunde erneut versuchen
        if not last or last["status"] != "failed" or _hours_since(last["finished_at"]) >= 1:
            due.append("foerder")
    if vergabe.days_to_import(conn):
        last = db.last_run(conn, "vergabe")
        if not last or last["status"] == "ok" or _hours_since(last["finished_at"]) >= 1:
            due.append("vergabe")
    return due


def run_source(source: str) -> dict:
    conn = db.connect()
    try:
        db.init_db(conn)
        state["running"].add(source)
        if source == "foerder":
            return foerder.run_import(conn)
        return vergabe.run_import(conn)
    finally:
        state["running"].discard(source)
        conn.close()


def _loop():
    while not _stop.is_set():
        try:
            conn = db.connect()
            try:
                sources = due_sources(conn)
            finally:
                conn.close()
            for source in sources:
                if _stop.is_set():
                    break
                log.info("Starte geplanten Import: %s", source)
                result = run_source(source)
                log.info("Import %s beendet: %s", source, result.get("status"))
        except Exception:
            log.exception("Fehler im Zeitplan")
        _stop.wait(config.SCHEDULER_CHECK_SECONDS)


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="import-scheduler", daemon=True)
    _thread.start()


def stop():
    _stop.set()
