"""A per-session sandbox: one folder of files, one SQLite database, one audit log.

It starts empty. Files arrive by upload or are written by the agent; tasks are
added through the agent.

Each browser session gets its own, so visitors to the deployed app cannot
see or damage each other's data.
"""
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from . import config


class Workspace:
    def __init__(self, session_id: str, base: Path = None):
        self.root = (base or config.SESSIONS_DIR) / session_id
        self.files = self.root / "files"
        self.db_path = self.root / "tasks.db"
        self.audit_path = self.root / "audit.jsonl"
        if not self.root.exists():
            self._create()

    def _create(self):
        self.files.mkdir(parents=True)
        self.execute("""CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            course TEXT,
            due_date TEXT,
            priority TEXT NOT NULL DEFAULT 'medium',
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        """Run one statement and commit. SQL is always ours; values are always bound."""
        with closing(sqlite3.connect(self.db_path)) as db:
            cursor = db.execute(sql, params)
            db.commit()
            return cursor

    def query(self, sql: str, params=()) -> list:
        with closing(sqlite3.connect(self.db_path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(sql, params)]

    def reset(self):
        shutil.rmtree(self.root, ignore_errors=True)
        self._create()

    def list_files(self) -> list:
        return sorted(p for p in self.files.rglob("*") if p.is_file())


def purge_stale_sessions(base: Path = None, ttl: int = config.SESSION_TTL_SECONDS):
    """Delete sandboxes nobody has touched in a day."""
    base = base or config.SESSIONS_DIR
    if not base.exists():
        return
    cutoff = time.time() - ttl
    for folder in base.iterdir():
        if not folder.is_dir():
            continue
        audit = folder / "audit.jsonl"
        last_used = (audit if audit.exists() else folder).stat().st_mtime
        if last_used < cutoff:
            shutil.rmtree(folder, ignore_errors=True)
