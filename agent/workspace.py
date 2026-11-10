"""A per-session sandbox: one folder of files, one SQLite database, one audit log.

Each browser session gets its own, so visitors to the deployed app cannot
see or damage each other's data.
"""
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from . import config

_SEED_FILES = {
    "notes/cs4680_prompting.md": """# CS4680 - Prompt engineering notes

## Core techniques
- **Role prompting**: tell the model who it is and who it is talking to.
- **Few-shot**: 2-5 worked examples beat a paragraph of description.
- **Chain of thought**: ask for reasoning before the answer.
- **Structured output**: specify the exact format (JSON schema) you will parse.
- **ReAct**: reason -> act -> observe, in a loop. This is how agents work.

## Agent safety
- Never execute model output without validating it.
- Destructive actions need a human in the loop.
- Log everything.

## To review before the final
- Difference between zero-shot and few-shot
- When does chain of thought hurt? (simple lookups, latency-sensitive calls)
""",
    "notes/biology_ch3.md": """# Biology - Chapter 3: Cell structure

- Prokaryotes: no nucleus, no membrane-bound organelles (bacteria, archaea).
- Eukaryotes: nucleus + organelles (plants, animals, fungi, protists).
- Mitochondria: ATP production via cellular respiration; has its own DNA.
- Chloroplasts: photosynthesis; only in plants and algae.
- Ribosomes: protein synthesis; free or bound to rough ER.
- Golgi apparatus: modifies, sorts and ships proteins.
- Cell membrane: phospholipid bilayer, selectively permeable.

Quiz Thursday covers membranes and organelles.
""",
    "old_draft.txt": "Scratch draft from September. Safe to delete.\n",
}

_SEED_TASKS = [
    ("Assignment 4: AI agent project", "CS4680", "2026-11-20", "high"),
    ("Chapter 3 quiz", "BIO1210", "2026-11-12", "medium"),
    ("Lab report: osmosis", "BIO1210", "2026-11-17", "medium"),
    ("Reading response 6", "ENG2100", "2026-11-14", "low"),
]


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
        for rel, text in _SEED_FILES.items():
            path = self.files / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.execute("""CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            course TEXT,
            due_date TEXT,
            priority TEXT NOT NULL DEFAULT 'medium',
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        for task in _SEED_TASKS:
            self.execute(
                "INSERT INTO tasks (title, course, due_date, priority) VALUES (?, ?, ?, ?)", task)

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
