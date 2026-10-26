from datetime import date, datetime, timezone

from app import db, search
from app.importers import vergabe
from tests.fixtures import _lot, eforms_notice, simple_notice

NOW = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)
FUTURE = ("2026-11-20+01:00", "10:00:00+01:00")
PAST = ("2026-10-24+02:00", "12:00:00+02:00")


def base_files():
    return {
        # Ausschreibung mit zwei Losen und unterschiedlichen Fristen (eines abgelaufen)
        "a-01.xml": eforms_notice("aaaa1111-0000-0000-0000-000000000001", "01", "proc-a", "Unterhaltsreinigung Schulen",
                                  [_lot("LOT-0001", "Reinigung Grundschule", "Unterhaltsreinigung", deadline=PAST),
                                   _lot("LOT-0002", "Reinigung Gymnasium", "Glasreinigung", deadline=FUTURE, value="120000")]),
        # Ausschreibung mit abgelaufener Frist
        "b-01.xml": eforms_notice("bbbb2222-0000-0000-0000-000000000001", "01", "proc-b", "Gebäudereinigung Rathaus",
                                  [_lot("LOT-0000", "Gebäudereinigung", "Reinigung Rathaus", deadline=PAST)]),
        # Ausschreibung ohne Fristangabe; Auftraggeber in Berlin, Ausführung in Bayern
        "c-01.xml": eforms_notice("cccc3333-0000-0000-0000-000000000001", "01", "proc-c", "Reinigung Polizeidienststellen",
                                  [_lot("LOT-0000", "Reinigung", "Reinigung Dienststellen", deadline=None, nuts="DE212",
                                        doc="https://http://kaputt")]),
    }


def load(conn, write_zip, files, day="2026-10-23"):
    path = write_zip(files)
    res = vergabe.run_import(conn, days=[day], fetch=lambda d: path)
    vergabe.rebuild_procedures(conn, now_utc=NOW)
    return res


def proc(conn, pid):
    return conn.execute("SELECT * FROM procedures WHERE procedure_id=?", (pid,)).fetchone()


def test_parses_lots_deadlines_and_locations(conn, write_zip):
    load(conn, write_zip, base_files())
    n = conn.execute("SELECT * FROM notices WHERE procedure_id='proc-a'").fetchone()
    lots = db.jload(n["lots"])
    assert [l["id"] for l in lots] == ["LOT-0001", "LOT-0002"]
    assert lots[1]["tender_deadline"]["utc"] == "2026-11-20T09:00:00+00:00"
    assert n["estimated_value"] == 120000 and n["currency"] == "EUR"
    assert n["buyer_name"] == "Stadt Berlin, Vergabestelle"
    # Ungültige URL aus der Quelle wird verworfen
    c = conn.execute("SELECT document_url FROM notices WHERE procedure_id='proc-c'").fetchone()
    assert c["document_url"] is None
    # Lose mit unterschiedlichen Fristen: eines offen -> Verfahren offen
    assert proc(conn, "proc-a")["status"] == "offen"
    assert proc(conn, "proc-a")["next_deadline"] == "2026-11-20T09:00:00+00:00"
    assert proc(conn, "proc-b")["status"] == "frist_abgelaufen"
    assert proc(conn, "proc-c")["status"] == "frist_unbekannt"


def test_reimport_without_duplicates(conn, write_zip):
    load(conn, write_zip, base_files())
    load(conn, write_zip, base_files())
    assert conn.execute("SELECT count(*) FROM notices").fetchone()[0] == 3
    assert conn.execute("SELECT count(*) FROM procedures").fetchone()[0] == 3
    assert conn.execute("SELECT count(*) FROM procedures_fts").fetchone()[0] == 3


def test_correction_extends_expired_deadline(conn, write_zip):
    load(conn, write_zip, base_files())
    assert proc(conn, "proc-b")["status"] == "frist_abgelaufen"
    correction = eforms_notice("bbbb2222-0000-0000-0000-000000000099", "01", "proc-b", "Gebäudereinigung Rathaus",
                               [_lot("LOT-0000", "Gebäudereinigung", "Reinigung Rathaus", deadline=FUTURE)],
                               changes=("bbbb2222-0000-0000-0000-000000000001-01", "Verlängerung der Angebotsfrist"))
    load(conn, write_zip, {"b-99.xml": correction}, day="2026-10-24")
    p = proc(conn, "proc-b")
    assert p["status"] == "offen" and p["notice_id"].endswith("99") and p["category"] == "aenderung"
    history = db.jload(p["history"])
    assert len(history) == 2
    assert next(h for h in history if h["notice_id"].endswith("0001"))["superseded"] is True


def test_new_version_replaces_old_in_simple_format(conn, write_zip):
    load(conn, write_zip, {"x-1.xml": simple_notice("25960001", "1", "Winterdienst", "2026-10-25")})
    assert proc(conn, "25960001")["status"] == "frist_abgelaufen"
    load(conn, write_zip, {"x-2.xml": simple_notice("25960001", "2", "Winterdienst", "2026-11-30", changed="1")},
         day="2026-10-24")
    p = proc(conn, "25960001")
    assert p["version"] == "2" and p["status"] == "offen"
    assert db.jload(p["regions"]) == ["NI"]


def test_results_and_cancellations(conn, write_zip):
    load(conn, write_zip, base_files())
    award = eforms_notice("aaaa1111-0000-0000-0000-000000000077", "01", "proc-a", "Unterhaltsreinigung Schulen",
                          [_lot("LOT-0001", "Reinigung Grundschule", "x"), _lot("LOT-0002", "Reinigung Gymnasium", "x")],
                          root="ContractAwardNotice", list_name="result", notice_type="can-standard", subtype="29",
                          results={"LOT-0001": "selec-w", "LOT-0002": "selec-w"})
    cancel = eforms_notice("cccc3333-0000-0000-0000-000000000077", "01", "proc-c", "Reinigung Polizeidienststellen",
                           [_lot("LOT-0000", "Reinigung", "x")], root="ContractAwardNotice", list_name="result",
                           notice_type="can-standard", subtype="29", results={"LOT-0000": "clos-nw"})
    load(conn, write_zip, {"a-77.xml": award, "c-77.xml": cancel}, day="2026-10-24")
    assert proc(conn, "proc-a")["status"] == "vergeben"
    assert proc(conn, "proc-c")["status"] == "aufgehoben"
    cat = lambda pid: conn.execute(  # noqa: E731
        "SELECT category FROM notices WHERE procedure_id=? AND notice_id LIKE '%77'", (pid,)).fetchone()[0]
    assert cat("proc-a") == "ergebnis"
    assert cat("proc-c") == "aufhebung"


def test_partially_awarded_lots_keep_procedure_open(conn, write_zip):
    files = base_files()
    files["a-01.xml"] = eforms_notice("aaaa1111-0000-0000-0000-000000000001", "01", "proc-a", "Unterhaltsreinigung Schulen",
                                      [_lot("LOT-0001", "Grundschule", "x", deadline=FUTURE),
                                       _lot("LOT-0002", "Gymnasium", "x", deadline=FUTURE)])
    load(conn, write_zip, files)
    award = eforms_notice("aaaa1111-0000-0000-0000-000000000077", "01", "proc-a", "Unterhaltsreinigung Schulen",
                          [_lot("LOT-0001", "Grundschule", "x")], root="ContractAwardNotice", list_name="result",
                          notice_type="can-standard", results={"LOT-0001": "selec-w"})
    load(conn, write_zip, {"a-77.xml": award}, day="2026-10-24")
    assert proc(conn, "proc-a")["status"] == "offen"


def _search(conn, **kw):
    q = dict(offering="Reinigung von Gebäuden")
    q.update(kw)
    return search.search_procedures(conn, search.AuftragQuery(**q), now=NOW)


def test_default_search_hides_expired_and_marks_unknown(conn, write_zip):
    load(conn, write_zip, base_files())
    res = _search(conn)
    ids = {r["id"]: r for r in res["results"]}
    assert set(ids) == {"proc-a", "proc-c"}  # proc-b abgelaufen
    assert ids["proc-c"]["status_label"] == "Frist nicht angegeben"
    assert any("Frist nicht angegeben" in c for c in ids["proc-c"]["check"])
    assert any("unterschiedliche Fristen" in c for c in ids["proc-a"]["check"])
    expired = [d for d in ids["proc-a"]["deadlines"] if d["expired"]]
    assert len(expired) == 1
    assert {r["id"] for r in _search(conn, status="offen")["results"]} == {"proc-a"}
    assert {r["id"] for r in _search(conn, status="alle")["results"]} == {"proc-a", "proc-b", "proc-c"}


def test_place_of_performance_not_buyer_seat(conn, write_zip):
    load(conn, write_zip, base_files())
    # Auftraggeber sitzt in Berlin (DE300), Ausführungsort ist München (DE212)
    assert {r["id"] for r in _search(conn, lands=["BY"])["results"]} == {"proc-a", "proc-c"}
    assert _search(conn, lands=["BE"])["total"] == 0


def test_qualifications_are_never_treated_as_proof(conn, write_zip):
    load(conn, write_zip, base_files())
    r = next(x for x in _search(conn, qualifications="ISO 9001")["results"] if x["id"] == "proc-a")
    assert any("werden in den Eignungskriterien erwähnt" in c for c in r["check"])
    assert not any("ISO" in w for w in r["why"])


def test_cpv_prefix_matching(conn, write_zip):
    load(conn, write_zip, base_files())
    res = search.search_procedures(conn, search.AuftragQuery(offering="xyz Leistung", cpv="90910000"), now=NOW)
    assert {r["id"] for r in res["results"]} == {"proc-a", "proc-c"}


def test_failed_day_keeps_data_and_is_retried(conn, write_zip):
    load(conn, write_zip, base_files(), day="2026-10-22")

    def boom(day):
        raise vergabe.net.DownloadError("HTTP 503 von Testserver")

    res = vergabe.run_import(conn, days=["2026-10-23"], fetch=boom)
    assert res["status"] == "failed"
    assert conn.execute("SELECT count(*) FROM notices").fetchone()[0] == 3
    row = conn.execute("SELECT * FROM vergabe_days WHERE day='2026-10-23'").fetchone()
    assert row["status"] == "failed" and "503" in row["error"]
    todo = vergabe.days_to_import(conn, today=date(2026, 10, 26))
    assert todo == ["2026-10-23", "2026-10-24", "2026-10-25"]


def test_initial_window_is_last_seven_completed_days(conn):
    days = vergabe.days_to_import(conn, today=date(2026, 10, 26))
    assert days == ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25"]


def test_corrupt_zip_marks_day_failed(conn, tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    res = vergabe.run_import(conn, days=["2026-10-23"], fetch=lambda d: bad)
    assert res["status"] == "failed"
    assert conn.execute("SELECT status FROM vergabe_days").fetchone()[0] == "failed"
