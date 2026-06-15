#!/usr/bin/env python3
"""
Migrate existing .session_handoff.md into SQLite history and
move it to canonical location: WorkflowBase/memory/handoff/.session_handoff.md
"""
import json
import sqlite3
import shutil
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path("/Users/gary/项目开发/AdarianMigration/adarian mvp")
OLD_HANDOFF = PROJECT_ROOT / ".session_handoff.md"
NEW_HANDOFF = PROJECT_ROOT / "WorkflowBase" / "memory" / "handoff" / ".session_handoff.md"
DB_PATH = PROJECT_ROOT / "WorkflowBase" / "memory" / "handoff" / "handoffs.db"

print(f"= Handoff migration =")
print(f"  Source: {OLD_HANDOFF}")
print(f"  Dest:   {NEW_HANDOFF}")
print(f"  DB:     {DB_PATH}")
print()

# 1. Ensure SQLite schema
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
conn = sqlite3.connect(str(DB_PATH))
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
conn.execute("CREATE INDEX IF NOT EXISTS idx_handoffs_project ON handoffs(project_root)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_handoffs_archived ON handoffs(archived_at)")
conn.commit()

# 2. Check if old handoff exists and is different from what's in the new location
old_content = None
if OLD_HANDOFF.is_file():
    old_content = OLD_HANDOFF.read_text(encoding="utf-8").strip()
    print(f"  Read old handoff: {len(old_content)} chars")
else:
    print("  ⚠️  No old .session_handoff.md found")

new_content = None
if NEW_HANDOFF.is_file():
    new_content = NEW_HANDOFF.read_text(encoding="utf-8").strip()
    print(f"  New location already has: {len(new_content)} chars")

# 3. Insert old handoff into SQLite as initial historical record
if old_content:
    # Extract metadata from handoff content
    import re
    session_start = None
    session_end = None
    m = re.match(r"^# Session Handoff\s*[—–-]\s*(.+?)\s*→\s*(.+?)(?:\s*\(|$)", old_content)
    if m:
        session_start = m.group(1).strip()
        session_end = m.group(2).strip()

    conn.execute(
        """INSERT INTO handoffs
           (project_root, session_start, session_end, content,
            record_protocol, tags, source_file)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            str(PROJECT_ROOT),
            session_start,
            session_end,
            old_content,
            None,  # record_protocol
            '["migrated"]',
            str(OLD_HANDOFF),
        ),
    )
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    print(f"  ✅ Inserted to SQLite: row {row_id}")
else:
    print("  ⏭️  Nothing to insert")

# 4. Move .session_handoff.md to canonical location (if old exists)
if old_content and (not new_content or old_content != new_content):
    NEW_HANDOFF.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(OLD_HANDOFF), str(NEW_HANDOFF))
    # Remove old
    OLD_HANDOFF.unlink()
    print(f"  ✅ Moved: {OLD_HANDOFF.name} → WorkflowBase/memory/handoff/")
elif old_content and new_content:
    print(f"  ⏭️  Same content, not moving")
else:
    print(f"  ⏭️  Nothing to move")

# 5. Verify
conn2 = sqlite3.connect(str(DB_PATH))
count = conn2.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0]
latest = conn2.execute(
    "SELECT id, session_start, session_end, archived_at FROM handoffs ORDER BY id DESC LIMIT 1"
).fetchone()
conn2.close()
print()
print(f"= Verification =")
print(f"  Total records in handoffs.db: {count}")
if latest:
    print(f"  Latest: row {latest[0]}, {latest[1]} → {latest[2]} (archived {latest[3]})")
print(f"  .session_handoff.md at new location: {NEW_HANDOFF.is_file()} ({NEW_HANDOFF.stat().st_size if NEW_HANDOFF.is_file() else 0} bytes)")
print(f"  Old location still exists: {OLD_HANDOFF.is_file()}")
print()
print("Done.")
