"""FastAPI-Anwendung: JSON-API und statisches Frontend."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, regions, scheduler, search
from .importers import vergabe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("chancenfinder")
STATIC = config.BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect()
    db.init_db(conn)
    # Verwaiste Sperren und "running"-Einträge eines abgebrochenen Laufs aufräumen
    conn.execute("DELETE FROM import_lock")
    conn.execute("UPDATE import_runs SET status='failed', message='Abgebrochen (Neustart)', finished_at=? "
                 "WHERE status='running'", (db.now_iso(),))
    conn.close()
    if config.SCHEDULER_ENABLED:
        scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="Chancenfinder", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
    return response


def _conn():
    conn = db.connect()
    db.init_db(conn)
    return conn


def _run_info(row):
    if not row:
        return None
    return {"status": row["status"], "started_at": row["started_at"], "finished_at": row["finished_at"],
            "message": row["message"]}


@app.get("/api/status")
def status():
    conn = _conn()
    try:
        cov = vergabe.coverage(conn)
        return {
            "foerder": {
                "last_success": _run_info(db.last_run(conn, "foerder", "ok")),
                "last_run": _run_info(db.last_run(conn, "foerder")),
                "running": "foerder" in scheduler.state["running"]
                           or bool(conn.execute("SELECT 1 FROM import_lock WHERE source='foerder'").fetchone()),
                "count": conn.execute("SELECT count(*) FROM programs WHERE active=1").fetchone()[0],
            },
            "vergabe": {
                "last_success": _run_info(db.last_run(conn, "vergabe", "ok") or db.last_run(conn, "vergabe", "partial")),
                "last_run": _run_info(db.last_run(conn, "vergabe")),
                "running": "vergabe" in scheduler.state["running"]
                           or bool(conn.execute("SELECT 1 FROM import_lock WHERE source='vergabe'").fetchone()),
                "count": conn.execute("SELECT count(*) FROM procedures").fetchone()[0],
                "open_count": conn.execute(
                    "SELECT count(*) FROM procedures WHERE status IN ('offen','frist_unbekannt')").fetchone()[0],
                "coverage": cov,
            },
        }
    finally:
        conn.close()


@app.get("/api/options")
def options():
    return {
        "bundeslaender": regions.BUNDESLAENDER,
        "applicants": {k: v["label"] for k, v in search.APPLICANTS.items()},
        "topics": {k: v["label"] for k, v in search.TOPICS.items()},
        "funding_types": search.FUNDING_TYPES,
    }


class FoerderRequest(BaseModel):
    applicant: str
    land: str
    description: str = Field(min_length=3, max_length=2000)
    topic: str | None = None
    budget: float | None = Field(default=None, ge=0, le=1e11)
    employees: int | None = Field(default=None, ge=0, le=10_000_000)
    started: str = Field(default="unbekannt", pattern="^(ja|nein|unbekannt)$")
    funding_type: str | None = None
    keyword: str | None = Field(default=None, max_length=200)


class AuftragRequest(BaseModel):
    offering: str = Field(min_length=3, max_length=2000)
    lands: list[str] = Field(default_factory=list, max_length=16)
    keywords: str | None = Field(default=None, max_length=500)
    cpv: str | None = Field(default=None, max_length=300)
    qualifications: str | None = Field(default=None, max_length=1000)
    status: str = Field(default="teilnahme", pattern="^(teilnahme|offen|alle)$")
    land_filter: str | None = None
    keyword: str | None = Field(default=None, max_length=200)


def _last_update(conn, source):
    row = db.last_run(conn, source, "ok") or (db.last_run(conn, source, "partial") if source == "vergabe" else None)
    return row["finished_at"] if row else None


# Suchanfragen werden per POST übertragen, damit Freitexte nicht in URLs oder Zugriffsprotokollen landen.
@app.post("/api/foerderung/suche")
def foerder_suche(req: FoerderRequest):
    conn = _conn()
    try:
        if req.topic and req.topic not in search.TOPICS:
            raise HTTPException(422, "Unbekannter Themenbereich")
        if req.funding_type and req.funding_type not in search.FUNDING_TYPES:
            raise HTTPException(422, "Unbekannte Förderart")
        try:
            res = search.search_programs(conn, search.FoerderQuery(**req.model_dump()))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        res["last_update"] = _last_update(conn, "foerder")
        res["data_available"] = conn.execute("SELECT count(*) FROM programs WHERE active=1").fetchone()[0] > 0
        return res
    finally:
        conn.close()


@app.post("/api/auftraege/suche")
def auftrag_suche(req: AuftragRequest):
    conn = _conn()
    try:
        try:
            res = search.search_procedures(conn, search.AuftragQuery(**req.model_dump()))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        res["last_update"] = _last_update(conn, "vergabe")
        res["coverage"] = vergabe.coverage(conn)
        res["data_available"] = conn.execute("SELECT count(*) FROM procedures").fetchone()[0] > 0
        return res
    finally:
        conn.close()


@app.get("/api/foerderung/detail")
def foerder_detail(id: str = Query(max_length=500)):
    conn = _conn()
    try:
        d = search.program_detail(conn, id)
        if not d:
            raise HTTPException(404, "Programm nicht gefunden")
        d["last_update"] = _last_update(conn, "foerder")
        return d
    finally:
        conn.close()


@app.get("/api/auftraege/detail")
def auftrag_detail(id: str = Query(max_length=200)):
    conn = _conn()
    try:
        d = search.procedure_detail(conn, id)
        if not d:
            raise HTTPException(404, "Verfahren nicht gefunden")
        d["last_update"] = _last_update(conn, "vergabe")
        return d
    finally:
        conn.close()


@app.exception_handler(Exception)
async def unhandled(request, exc):
    log.exception("Unerwarteter Fehler bei %s", request.url.path)
    return JSONResponse({"detail": "Interner Fehler"}, status_code=500)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
