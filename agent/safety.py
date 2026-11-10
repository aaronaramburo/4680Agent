"""Safety checks that run in code, independent of anything the model says."""
from pathlib import Path

from . import config
from .schema import DESTRUCTIVE


class SafetyError(Exception):
    """The action is not allowed. str(e) explains why."""


def resolve_path(root: Path, relative: str) -> Path:
    """Map a model-supplied path to a real path inside the sandbox, or refuse."""
    raw = relative.strip().replace("\\", "/")
    if not raw:
        raise SafetyError("Empty path.")
    if raw.startswith(("/", "~")) or ":" in raw:
        raise SafetyError("Absolute paths are not allowed; files live inside the workspace.")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise SafetyError("'..' is not allowed; files live inside the workspace.")
    if len(parts) > config.MAX_PATH_DEPTH:
        raise SafetyError(f"Path is nested too deep (max {config.MAX_PATH_DEPTH} levels).")
    if any(p.startswith(".") for p in parts):
        raise SafetyError("Hidden files and folders are not allowed.")

    root = root.resolve()
    target = root.joinpath(*parts).resolve()
    # Final containment check after resolving symlinks.
    if root not in target.parents:
        raise SafetyError("That path is outside the workspace.")
    if target.suffix.lower() not in config.ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(config.ALLOWED_EXTENSIONS))
        raise SafetyError(f"Only {allowed} files are allowed.")
    return target


def effective_risk(action, files_root: Path) -> str:
    """Static risk from the registry, raised when the action would overwrite data."""
    if action.tool == "write_file":
        try:
            if resolve_path(files_root, action.args["path"]).exists():
                return DESTRUCTIVE
        except SafetyError:
            pass  # the executor will block it and report why
    return action.risk


def describe(action) -> str:
    """One-line, human-readable description for the confirmation prompt."""
    a = action.args
    if action.tool == "delete_file":
        return f"Delete the file **{a['path']}**"
    if action.tool == "write_file":
        return f"Overwrite the existing file **{a['path']}**"
    if action.tool == "delete_task":
        return f"Delete task **#{a['id']}** from the tracker"
    return f"Run {action.tool}"
