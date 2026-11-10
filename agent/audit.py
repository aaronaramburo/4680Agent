"""Append-only audit log (JSON lines). Every decision the agent makes lands here."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

_MAX_FIELD = 500
_KEY_SHAPED = re.compile(r"sk-[A-Za-z0-9_\-]{16,}")


def redact(text: str) -> str:
    """Mask anything shaped like an API key before it is logged or shown."""
    return _KEY_SHAPED.sub("[redacted key]", text)


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, **fields):
        entry = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event}
        entry.update({k: _clip(v) for k, v in fields.items()})
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def read(self) -> list:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]


def _clip(value):
    """Keep entries readable: long strings (file bodies) are shortened, with a marker."""
    if isinstance(value, str):
        value = redact(value)
    if isinstance(value, str) and len(value) > _MAX_FIELD:
        return value[:_MAX_FIELD] + f"... [{len(value)} chars total]"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value]
    return value
