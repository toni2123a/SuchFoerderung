import json

from app import db, search
from app.importers import foerder
from tests.fixtures import fdb_program, standard_programs

HEIZUNG_ID = "/BMWI/FDB/Content/DE/Foerderprogramm/Bund/BAFA/heizungsfoerderung"


def test_parses_realistic_export(conn, write_zip):
    res = foerder.run_import(conn, zip_path=write_zip(standard_programs()))
    assert res["status"] == "ok", res
    assert res["programs"] == 4
    r = conn.execute("SELECT * FROM programs WHERE id=?", (HEIZUNG_ID,)).fetchone()
    assert r["title"] == "Bundesförderung für effiziente Gebäude – Heizungstausch"
    # CDATA-Hülle und HTML entfernt, Skriptinhalte verworfen
    assert "<" not in r["summary"] and "alert" not in r["summary"]
    assert "bis zu 70 Prozent" in r["summary"]
    assert json.loads(r["regions"]) == ["_bundesweit"]
    assert json.loads(r["eligible"]) == ["privatperson", "unternehmen"]
    assert json.loads(r["organisations"]) == ["Bundesamt für Wirtschaft und Ausfuhrkontrolle (BAFA)"]
    links = json.loads(r["links"])
    assert links == [{"title": "Antragsportal", "url": "https://www.bafa.de/antrag"}]  # javascript:-Link verworfen
    assert r["source_url"] == "https://www.foerderdatenbank.de/FDB/Content/DE/Foerderprogramm/Bund/BAFA/heizungsfoerderung.html"
    assert b"heizungsfoerderung" in db.decompress(r["raw_xml"]).encode()  # Original erhalten


def test_reimport_without_duplicates(conn, write_zip):
    path = write_zip(standard_programs())
    foerder.run_import(conn, zip_path=path)
    second = foerder.run_import(conn, zip_path=path)
    assert second["status"] == "ok"
    assert second["inserted"] == 0 and second["unchanged"] == 4
    assert conn.execute("SELECT count(*) FROM programs").fetchone()[0] == 4
    assert conn.execute("SELECT count(*) FROM programs_fts").fetchone()[0] == 4


def test_failed_import_keeps_existing_data(conn, write_zip, tmp_path):
    foerder.run_import(conn, zip_path=write_zip(standard_programs()))
    # 1) kein ZIP (z. B. HTML-Fehlerseite)
    bad = tmp_path / "bad.zip"
    bad.write_text("<html>Service Unavailable</html>")
    assert foerder.run_import(conn, zip_path=bad)["status"] == "failed"
    # 2) gültiges ZIP, aber unplausibel wenige Programme -> alter Bestand wird nicht deaktiviert
    few = {k: v for k, v in standard_programs().items() if "heizung" in k}
    res = foerder.run_import(conn, zip_path=write_zip(few))
    assert res["status"] == "failed" and "alter Bestand bleibt erhalten" in res["message"]
    assert conn.execute("SELECT count(*) FROM programs WHERE active=1").fetchone()[0] == 4
    assert db.last_run(conn, "foerder", "ok") is not None


def test_references_survive_export_without_contact_documents(conn, write_zip):
    # Echte Exportstände liefern nicht immer alle referenzierten Kontaktdokumente mit
    foerder.run_import(conn, zip_path=write_zip(standard_programs()))
    files = {k: v for k, v in standard_programs().items() if "/Kontakt/" not in k and "/ExternerLink/" not in k}
    assert foerder.run_import(conn, zip_path=write_zip(files))["status"] == "ok"
    r = conn.execute("SELECT organisations, links FROM programs WHERE id=?", (HEIZUNG_ID,)).fetchone()
    assert json.loads(r["organisations"]) == ["Bundesamt für Wirtschaft und Ausfuhrkontrolle (BAFA)"]
    assert len(json.loads(r["links"])) == 1


def test_removed_program_deactivated_only_after_valid_export(conn, write_zip):
    foerder.run_import(conn, zip_path=write_zip(standard_programs()))
    files = standard_programs()
    files.pop("BMWI/FDB/Content/DE/Foerderprogramm/Land/Bayern/digitalbonus.xml")
    files["BMWI/FDB/Content/DE/Foerderprogramm/Bund/neu.xml"] = fdb_program("neu", "Bund", "Neues Programm")
    res = foerder.run_import(conn, zip_path=write_zip(files))
    assert res["status"] == "ok" and res["deactivated"] == 1 and res["inserted"] == 1
    row = conn.execute("SELECT active FROM programs WHERE id LIKE '%digitalbonus'").fetchone()
    assert row["active"] == 0


EVIL = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        '<document name="evil" path="/BMWI/FDB/Content/DE/Foerderprogramm/Bund" type="gsb:ServiceOffer">&x;</document>')


def test_xml_with_entities_is_rejected(conn, write_zip, monkeypatch):
    monkeypatch.setattr(foerder.config, "FDB_MAX_ERROR_RATE", 0.5)
    files = standard_programs()
    files["BMWI/FDB/Content/DE/Foerderprogramm/Bund/evil.xml"] = EVIL
    res = foerder.run_import(conn, zip_path=write_zip(files))
    assert res["status"] == "ok"
    assert res["errors"] == 1
    assert any(x in res["error_examples"][0] for x in ("DTDForbidden", "EntitiesForbidden"))
    assert conn.execute("SELECT count(*) FROM programs WHERE id LIKE '%evil'").fetchone()[0] == 0


def test_too_many_broken_files_reject_whole_export(conn, write_zip):
    foerder.run_import(conn, zip_path=write_zip(standard_programs()))
    files = standard_programs()
    files["BMWI/FDB/Content/DE/Foerderprogramm/Bund/evil.xml"] = EVIL  # 1 von 5 = 20 % > 5 %
    files.pop("BMWI/FDB/Content/DE/Foerderprogramm/Land/Bayern/digitalbonus.xml")
    res = foerder.run_import(conn, zip_path=write_zip(files))
    assert res["status"] == "failed"
    assert conn.execute("SELECT active FROM programs WHERE id LIKE '%digitalbonus'").fetchone()[0] == 1


def _search(conn, **kw):
    q = dict(applicant="unternehmen", land="BY", description="Software einführen")
    q.update(kw)
    return search.search_programs(conn, search.FoerderQuery(**q))


def test_bundesweit_and_regional_exclusion(conn, write_zip):
    foerder.run_import(conn, zip_path=write_zip(standard_programs()))
    # Bundesweite Programme gelten für alle Länder
    for land in ("BY", "HB", "TH"):
        res = _search(conn, applicant="privatperson", land=land, description="Wärmepumpe einbauen")
        assert [r["title"] for r in res["results"]] == ["Bundesförderung für effiziente Gebäude – Heizungstausch"]
        assert "Fördergebiet: bundesweit" in res["results"][0]["why"]
    # Landesprogramm Bayern erscheint nicht in NRW (und umgekehrt)
    by = {r["title"] for r in _search(conn, land="BY")["results"]}
    nw = {r["title"] for r in _search(conn, land="NW")["results"]}
    assert "Digitalbonus Bayern" in by and "Digitalbonus Bayern" not in nw
    assert "Digitalisierung im Mittelstand NRW" in nw and "Digitalisierung im Mittelstand NRW" not in by
    assert _search(conn, land="NW")["excluded"]["region"] >= 1


def test_unknown_requirements_are_not_treated_as_fulfilled(conn, write_zip):
    foerder.run_import(conn, zip_path=write_zip(standard_programs()))
    res = _search(conn, land="SN", applicant="verein")
    titles = {r["title"]: r for r in res["results"]}
    # Programm ohne Fördergebiet und ohne Förderberechtigte: angezeigt, aber mit Prüfhinweisen
    unknown = titles["Innovationsprogramm Software"]
    assert any("Förderberechtigte sind in der Datenbank nicht angegeben" in c for c in unknown["check"])
    assert any("Fördergebiet in der Datenbank nicht eindeutig" in c for c in unknown["check"])
    assert not any("Förderberechtigt laut Datenbank" in w for w in unknown["why"])


def test_company_size_exclusion_only_when_unambiguous(conn, write_zip):
    foerder.run_import(conn, zip_path=write_zip(standard_programs()))
    big = {r["title"] for r in _search(conn, employees=400)["results"]}
    assert "Digitalbonus Bayern" not in big  # ab 250 Beschäftigten kein KMU
    mid = _search(conn, employees=120)["results"]
    bonus = next(r for r in mid if r["title"] == "Digitalbonus Bayern")
    assert any("KMU-Status hängt auch von Umsatz" in c for c in bonus["check"])
    unknown = _search(conn, employees=None)["results"]
    bonus = next(r for r in unknown if r["title"] == "Digitalbonus Bayern")
    assert any("Unternehmensgröße prüfen" in c for c in bonus["check"])


def test_started_project_shows_source_excerpt(conn, write_zip):
    foerder.run_import(conn, zip_path=write_zip(standard_programs()))
    res = _search(conn, applicant="privatperson", description="Wärmepumpe", started="ja")
    check = " ".join(res["results"][0]["check"])
    assert "bereits begonnen" in check and "Auszug aus der Quelle" in check
    assert res["results"][0]["amount_excerpts"] == ["Der Zuschuss beträgt bis zu 70 Prozent der förderfähigen Kosten."]


def test_no_padding_with_unrelated_results(conn, write_zip):
    foerder.run_import(conn, zip_path=write_zip(standard_programs()))
    res = _search(conn, description="Kühe auf dem Mond züchten")
    assert res["total"] == 0
