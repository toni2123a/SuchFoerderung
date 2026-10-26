from fastapi.testclient import TestClient

from app import config
from app.importers import foerder
from tests.fixtures import standard_programs


def test_api_search_and_privacy(conn, write_zip, monkeypatch, caplog):
    monkeypatch.setattr(config, "SCHEDULER_ENABLED", False)
    foerder.run_import(conn, zip_path=write_zip(standard_programs()))
    from app.main import app

    secret = "Wärmepumpe für mein Haus in der Geheimstraße 7"
    with TestClient(app) as client, caplog.at_level("DEBUG"):
        assert client.get("/").status_code == 200
        res = client.post("/api/foerderung/suche", json={"applicant": "privatperson", "land": "BY", "description": secret})
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1 and data["last_update"]
        detail = client.get("/api/foerderung/detail", params={"id": data["results"][0]["id"]})
        assert detail.status_code == 200 and detail.json()["links"][0]["url"] == "https://www.bafa.de/antrag"
        assert client.post("/api/foerderung/suche", json={"applicant": "?", "land": "BY", "description": "x y z"}).status_code == 422
        status = client.get("/api/status").json()
        assert status["foerder"]["count"] == 4 and status["vergabe"]["count"] == 0
        assert "Content-Security-Policy" in res.headers
    # Freitexte dürfen nicht in Anwendungslogs landen
    assert "Geheimstraße" not in caplog.text
