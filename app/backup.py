"""Daily SQLite backups with rotation, via sqlite3's online backup API."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def run_backup(db_path: Path, backup_dir: Path, keep: int, today: str) -> Path:
    """Write a dated SQLite backup, then rotate older backups beyond ``keep``."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    output = backup_dir / f"stocks-{today}.db"

    with sqlite3.connect(db_path) as source, sqlite3.connect(output) as target:
        source.backup(target)

    backups = sorted(backup_dir.glob("stocks-*.db"))
    retained = max(keep, 0)
    for old in backups[: max(0, len(backups) - retained)]:
        if old != output:
            old.unlink()

    return output
