import pytest

from app import config, db


def test_connect_uses_monkeypatched_db_path_and_enables_wal(monkeypatch, tmp_path):
    db_path = tmp_path / "stocks.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)

    with db.connect() as conn:
        conn.execute("CREATE TABLE sample (value TEXT)")
        conn.execute("INSERT INTO sample (value) VALUES ('ok')")
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

    assert journal_mode == "wal"
    assert busy_timeout == 5000

    with db.connect() as conn:
        row = conn.execute("SELECT value FROM sample").fetchone()

    assert db_path.exists()
    assert row["value"] == "ok"


def test_cash_defaults_and_round_trips(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "stocks.db")
    db.init_db()

    assert db.get_cash() == 0.0

    db.set_cash(1234.56)

    assert db.get_cash() == 1234.56
    assert db.get_setting("cash") == "1234.56"


def test_set_cash_rejects_negative_amount(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "stocks.db")
    db.init_db()

    with pytest.raises(ValueError):
        db.set_cash(-0.01)

    assert db.get_cash() == 0.0


def test_get_cash_tolerates_bad_setting(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "stocks.db")
    db.init_db()
    db.set_setting("cash", "not-a-number")

    assert db.get_cash() == 0.0
