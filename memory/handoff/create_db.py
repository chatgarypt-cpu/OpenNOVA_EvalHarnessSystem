#!/usr/bin/env python3
"""Create handoffs.db schema. Idempotent — safe to re-run."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "handoffs.db"


def get_db_path() -> Path:
    return DB_PATH


def create_schema(db_path: Path | None = None) -> str:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS handoffs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            project_root TEXT    NOT NULL,
            session_start TEXT,
            session_end   TEXT,
            content       TEXT   NOT NULL,
            record_protocol TEXT,
            tags          TEXT,
            source_file   TEXT,
            archived_at   TEXT   DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_handoffs_project
        ON handoffs(project_root)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_handoffs_archived
        ON handoffs(archived_at)
    """)
    conn.commit()
    conn.close()
    return str(path)


if __name__ == "__main__":
    path = create_schema()
    print(f"Schema created: {path}")
