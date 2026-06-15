#!/usr/bin/env python3
"""Migrate archived handoff files from 资产/memory_governance/handoffs/ into SQLite."""
import re
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path("/Users/gary/项目开发/AdarianMigration/adarian mvp")
ARCHIVE_DIR = PROJECT_ROOT / "资产" / "memory_governance" / "handoffs"
DB_PATH = PROJECT_ROOT / "WorkflowBase" / "memory" / "handoff" / "handoffs.db"

def extract_meta(content):
    """Extract session_start, session_end from content."""
    meta = {"session_start": None, "session_end": None}
    m = re.match(r"^# Session Handoff\s*[—–-]\s*(.+?)\s*→\s*(.+?)(?:\s*[（(]|$)", content)
    if m:
        meta["session_start"] = m.group(1).strip()
        meta["session_end"] = m.group(2).strip()
    return meta

# Get all archived files sorted by timestamp
files = sorted(ARCHIVE_DIR.glob("*.md"))
print(f"Found {len(files)} archived files in {ARCHIVE_DIR}")
print()

conn = sqlite3.connect(str(DB_PATH))

for f in files:
    content = f.read_text(encoding="utf-8").strip()
    meta = extract_meta(content)
    ts = f.stem  # filename without .md = ISO timestamp

    conn.execute(
        """INSERT INTO handoffs
           (project_root, session_start, session_end, content,
            record_protocol, tags, source_file, archived_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(PROJECT_ROOT),
            meta["session_start"],
            meta["session_end"],
            content,
            None,
            '["migrated_archive"]',
            str(f),
            ts.replace("T", " "),  # 2026-06-04T17-57-03 → 2026-06-04 17:57:03
        ),
    )
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    print(f"  ✅ row {row_id}: {f.name} ({meta['session_start']} → {meta['session_end']})")

conn.commit()

# Verify total
count = conn.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0]
print(f"\n  Total in handoffs.db: {count} rows")

# List all
print("\n  All records:")
for row in conn.execute(
    "SELECT id, session_start, session_end, archived_at, length(content) FROM handoffs ORDER BY id"
):
    print(f"    {row[0]}: {row[1]} → {row[2]}  [{row[4]} chars]  (archived: {row[3]})")

conn.close()
print("\nDone.")
