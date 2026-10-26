"""Kommandozeile für manuelle Aktualisierung.

    python -m app.cli update                 # beide Quellen
    python -m app.cli update --source foerder
    python -m app.cli update --source vergabe --day 2026-10-23
    python -m app.cli status
"""
import argparse
import json
import logging
import sys

from . import db
from .importers import foerder, vergabe


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Chancenfinder – Datenimport")
    sub = parser.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("update", help="Daten aus den offiziellen Quellen aktualisieren")
    up.add_argument("--source", choices=["foerder", "vergabe", "alle"], default="alle")
    up.add_argument("--day", action="append", help="Bestimmten Tag (YYYY-MM-DD) für Vergabedaten laden; mehrfach möglich")
    up.add_argument("--zip", help="Lokale Förderexport-ZIP-Datei statt Download verwenden")
    sub.add_parser("status", help="Datenstand anzeigen")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    conn = db.connect()
    db.init_db(conn)
    if args.cmd == "status":
        out = {
            "foerder": dict(db.last_run(conn, "foerder") or {}),
            "vergabe": dict(db.last_run(conn, "vergabe") or {}),
            "vergabe_abdeckung": vergabe.coverage(conn),
            "programme_aktiv": conn.execute("SELECT count(*) FROM programs WHERE active=1").fetchone()[0],
            "verfahren": conn.execute("SELECT count(*) FROM procedures").fetchone()[0],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    exit_code = 0
    if args.source in ("foerder", "alle"):
        res = foerder.run_import(conn, zip_path=args.zip)
        print("Förderdatenbank:", res.get("status"), res.get("message", ""), f"({res.get('programs', '-')} Programme)")
        exit_code |= res.get("status") not in ("ok", "skipped")
    if args.source in ("vergabe", "alle"):
        res = vergabe.run_import(conn, days=args.day)
        print("Bekanntmachungsservice:", res.get("status"), res.get("message", ""))
        exit_code |= res.get("status") not in ("ok", "skipped")
    return int(exit_code)


if __name__ == "__main__":
    sys.exit(main())
