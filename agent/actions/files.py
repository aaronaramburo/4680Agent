"""File operations. Every path goes through safety.resolve_path first."""
from pathlib import Path

from .. import config
from ..safety import SafetyError, resolve_path
from . import ActionError, documents


def list_files(ws):
    files = [{"path": p.relative_to(ws.files).as_posix(), "bytes": p.stat().st_size}
             for p in ws.list_files()]
    return {"files": files, "count": len(files)}


def read_file(ws, path):
    target = resolve_path(ws.files, path)
    if not target.is_file():
        raise ActionError(f"No file named {path}. Use list_files to see what exists.")
    text = target.read_text(encoding="utf-8", errors="replace")
    result = {"path": path, "content": text[:config.MAX_READ_CHARS]}
    if len(text) > config.MAX_READ_CHARS:
        result["truncated"] = f"showing first {config.MAX_READ_CHARS} of {len(text)} characters"
    return result


def write_file(ws, path, content):
    target = resolve_path(ws.files, path)
    _check_size(content)
    existed = target.exists()
    if not existed and len(ws.list_files()) >= config.MAX_FILES:
        raise SafetyError(f"The workspace is full ({config.MAX_FILES} files). Delete something first.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"path": path, "bytes": target.stat().st_size, "overwrote_existing": existed}


def append_file(ws, path, content):
    target = resolve_path(ws.files, path)
    existing = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    return write_file(ws, path, existing + content) | {"appended_chars": len(content)}


def import_file(ws, name, data: bytes):
    """Save a file the user uploaded. Same sandbox rules as anything the agent writes.

    PDF and Word documents are stored as their extracted text, in <name>.txt.
    """
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise SafetyError(f"It is larger than {config.MAX_UPLOAD_BYTES // 1_000_000} MB.")
    if Path(name).suffix.lower() in config.DOCUMENT_EXTENSIONS:
        target = f"{name}.txt"
        resolve_path(ws.files, target)  # reject bad names before parsing anything
        text = documents.extract_text(name, data)
    else:
        target = name
        resolve_path(ws.files, target)
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ActionError("It is not a plain-text file.") from None
    return write_file(ws, target, text) | {"source": name}


def delete_file(ws, path):
    target = resolve_path(ws.files, path)
    if not target.is_file():
        raise ActionError(f"No file named {path}.")
    target.unlink()
    return {"deleted": path}


def _check_size(content):
    size = len(content.encode("utf-8"))
    if size > config.MAX_FILE_BYTES:
        raise SafetyError(f"File too large ({size} bytes; the limit is {config.MAX_FILE_BYTES}).")
