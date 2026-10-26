"""SQLite-Schema und Hilfsfunktionen."""
import json
import sqlite3
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import config

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS import_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,              -- 'foerder' | 'vergabe'
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,              -- 'running' | 'ok' | 'failed' | 'partial'
    message TEXT,
    stats TEXT
);

CREATE TABLE IF NOT EXISTS import_lock (
    source TEXT PRIMARY KEY,
    acquired_at TEXT NOT NULL
);

-- Förderprogramme (Förderdatenbank)
CREATE TABLE IF NOT EXISTS programs (
    id TEXT PRIMARY KEY,               -- Repository-Pfad + Name aus dem Export
    title TEXT NOT NULL,
    teaser TEXT,
    summary TEXT,
    eligibility TEXT,                  -- gsb:regulatoryFWork (Text)
    legal_text TEXT,                   -- gsb:bodyText (Text)
    procedure_text TEXT,               -- gsb:procDescription (Text)
    keywords TEXT,
    funding_types TEXT NOT NULL,       -- JSON-Liste (Slugs)
    regions TEXT NOT NULL,             -- JSON-Liste (Slugs Fördergebiet)
    funder_levels TEXT NOT NULL,       -- JSON-Liste (bund/land/eu)
    eligible TEXT NOT NULL,            -- JSON-Liste (Förderberechtigte)
    areas TEXT NOT NULL,               -- JSON-Liste (Förderbereich)
    company_sizes TEXT NOT NULL,       -- JSON-Liste (Unternehmensgröße)
    organisations TEXT NOT NULL,       -- JSON-Liste von Namen
    contacts TEXT NOT NULL,            -- JSON-Liste {name, email, phone}
    links TEXT NOT NULL,               -- JSON-Liste {title, url} (validiert)
    source_url TEXT NOT NULL,
    date_of_issue TEXT,
    content_hash TEXT NOT NULL,
    raw_xml BLOB NOT NULL,             -- zlib-komprimierter Originaldatensatz
    active INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

-- Kontakt- und Linkdokumente aus dem Förderexport. Sie werden dauerhaft vorgehalten, weil nicht
-- jeder Exportstand alle referenzierten Dokumente mitliefert.
CREATE TABLE IF NOT EXISTS fdb_refs (
    key TEXT PRIMARY KEY,              -- Repository-Pfad + Name
    kind TEXT NOT NULL,                -- contact | link
    data TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

-- Volltextindex (Trigram = Teilwortsuche, wichtig für deutsche Komposita).
-- Der Text wird vorab mit textutil.fold normalisiert.
CREATE VIRTUAL TABLE IF NOT EXISTS programs_fts USING fts5(
    id UNINDEXED, title, teaser, keywords, body, tokenize = 'trigram'
);

-- Bekanntmachungen (Bekanntmachungsservice)
CREATE TABLE IF NOT EXISTS notices (
    notice_id TEXT NOT NULL,
    version TEXT NOT NULL,
    version_num INTEGER NOT NULL,
    procedure_id TEXT NOT NULL,
    form_type TEXT NOT NULL,           -- competition | change | result | planning | cont-modif | dir-awa-pre ...
    notice_type TEXT NOT NULL,         -- z. B. cn-standard, can-standard, pin-only
    subtype TEXT,
    category TEXT NOT NULL,            -- ausschreibung | vorinformation | aenderung | ergebnis | aufhebung
    pub_day TEXT NOT NULL,             -- Exporttag (YYYY-MM-DD)
    issue_datetime TEXT,
    title TEXT,
    description TEXT,
    buyer_name TEXT,
    buyer_city TEXT,
    buyer_nuts TEXT,
    nature TEXT,
    cpv TEXT NOT NULL,                 -- JSON-Liste
    estimated_value REAL,
    currency TEXT,
    document_url TEXT,
    changed_notice TEXT,               -- referenzierte Bekanntmachung (ID-Version)
    change_reason TEXT,
    lots TEXT NOT NULL,                -- JSON-Liste der Lose
    raw_xml BLOB NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (notice_id, version)
);
CREATE INDEX IF NOT EXISTS notices_proc ON notices(procedure_id);
CREATE INDEX IF NOT EXISTS notices_changed ON notices(changed_notice);

-- Aktueller bekannter Stand je Verfahren (nach jedem Import neu berechnet)
CREATE TABLE IF NOT EXISTS procedures (
    procedure_id TEXT PRIMARY KEY,
    notice_id TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,              -- offen | frist_unbekannt | frist_abgelaufen | vorinformation | vergeben | aufgehoben | sonstiges
    category TEXT NOT NULL,
    next_deadline TEXT,
    notice_count INTEGER NOT NULL,
    regions TEXT NOT NULL,             -- JSON-Liste Bundesland-Codes (Ausführungsort)
    region_unknown INTEGER NOT NULL,
    foreign_place INTEGER NOT NULL,
    history TEXT NOT NULL              -- JSON-Liste aller bekannten Bekanntmachungen
);

CREATE VIRTUAL TABLE IF NOT EXISTS procedures_fts USING fts5(
    procedure_id UNINDEXED, title, body, tokenize = 'trigram'
);

CREATE TABLE IF NOT EXISTS vergabe_days (
    day TEXT PRIMARY KEY,
    status TEXT NOT NULL,              -- ok | failed
    attempts INTEGER NOT NULL DEFAULT 0,
    notice_count INTEGER,
    error TEXT,
    updated_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 60000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def compress(text: bytes) -> bytes:
    return zlib.compress(text, 6)


def decompress(blob: bytes) -> str:
    return zlib.decompress(blob).decode("utf-8", "replace")


def jdump(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def jload(value):
    return json.loads(value) if value else []


# --- Import-Protokoll und Sperre -------------------------------------------

def start_run(conn, source: str) -> int:
    cur = conn.execute(
        "INSERT INTO import_runs(source, started_at, status) VALUES (?, ?, 'running')",
        (source, now_iso()),
    )
    return cur.lastrowid


def finish_run(conn, run_id: int, status: str, message: str = "", stats: dict | None = None):
    conn.execute(
        "UPDATE import_runs SET finished_at=?, status=?, message=?, stats=? WHERE id=?",
        (now_iso(), status, message[:2000], jdump(stats or {}), run_id),
    )


def acquire_lock(conn, source: str, stale_after_seconds: int = 3 * 3600) -> bool:
    """Prozessübergreifende Sperre, damit CLI und Server nicht parallel importieren."""
    with transaction(conn):
        row = conn.execute("SELECT acquired_at FROM import_lock WHERE source=?", (source,)).fetchone()
        if row:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(row["acquired_at"])
            if age.total_seconds() < stale_after_seconds:
                return False
            conn.execute("DELETE FROM import_lock WHERE source=?", (source,))
        conn.execute("INSERT INTO import_lock(source, acquired_at) VALUES (?, ?)", (source, now_iso()))
    return True


def release_lock(conn, source: str) -> None:
    conn.execute("DELETE FROM import_lock WHERE source=?", (source,))


def last_run(conn, source: str, status: str | None = None):
    if status:
        return conn.execute(
            "SELECT * FROM import_runs WHERE source=? AND status=? ORDER BY id DESC LIMIT 1",
            (source, status),
        ).fetchone()
    return conn.execute(
        "SELECT * FROM import_runs WHERE source=? ORDER BY id DESC LIMIT 1", (source,)
    ).fetchone()
