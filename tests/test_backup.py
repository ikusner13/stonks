import sqlite3

from app.backup import run_backup


def _create_source(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE holdings (symbol TEXT PRIMARY KEY, shares REAL)")
        conn.execute("INSERT INTO holdings VALUES ('AAPL', 10)")


def _symbols(path):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT symbol, shares FROM holdings").fetchall()


def test_run_backup_creates_valid_sqlite_backup(tmp_path):
    source = tmp_path / "stocks.db"
    backup_dir = tmp_path / "backups"
    _create_source(source)

    output = run_backup(source, backup_dir, keep=14, today="2026-07-05")

    assert output == backup_dir / "stocks-2026-07-05.db"
    assert _symbols(output) == [("AAPL", 10.0)]


def test_run_backup_same_day_overwrites(tmp_path):
    source = tmp_path / "stocks.db"
    backup_dir = tmp_path / "backups"
    _create_source(source)

    output = run_backup(source, backup_dir, keep=14, today="2026-07-05")
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE holdings SET shares = 12 WHERE symbol = 'AAPL'")

    second = run_backup(source, backup_dir, keep=14, today="2026-07-05")

    assert second == output
    assert _symbols(output) == [("AAPL", 12.0)]
    assert sorted(p.name for p in backup_dir.glob("stocks-*.db")) == ["stocks-2026-07-05.db"]


def test_run_backup_rotation_prunes_oldest_beyond_keep(tmp_path):
    source = tmp_path / "stocks.db"
    backup_dir = tmp_path / "backups"
    _create_source(source)

    for day in ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04"]:
        run_backup(source, backup_dir, keep=3, today=day)

    assert sorted(p.name for p in backup_dir.glob("stocks-*.db")) == [
        "stocks-2026-07-02.db",
        "stocks-2026-07-03.db",
        "stocks-2026-07-04.db",
    ]


def test_run_backup_keep_one_leaves_exactly_today(tmp_path):
    source = tmp_path / "stocks.db"
    backup_dir = tmp_path / "backups"
    _create_source(source)

    run_backup(source, backup_dir, keep=1, today="2026-07-04")
    run_backup(source, backup_dir, keep=1, today="2026-07-05")

    assert sorted(p.name for p in backup_dir.glob("stocks-*.db")) == ["stocks-2026-07-05.db"]
