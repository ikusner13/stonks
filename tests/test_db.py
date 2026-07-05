from app import config, db


def test_connect_uses_monkeypatched_db_path_and_enables_wal(
    monkeypatch, tmp_path
):
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
