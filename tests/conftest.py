import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, db  # noqa: E402


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    # Die Testexporte sind klein; Plausibilitätsgrenze entsprechend absenken
    monkeypatch.setattr(config, "FDB_MIN_PROGRAMS", 3)
    c = db.connect(tmp_path / "test.sqlite3")
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture
def write_zip(tmp_path):
    from tests.fixtures import make_zip

    counter = {"n": 0}

    def _write(files: dict) -> Path:
        counter["n"] += 1
        p = tmp_path / f"export{counter['n']}.zip"
        p.write_bytes(make_zip(files))
        return p

    return _write
