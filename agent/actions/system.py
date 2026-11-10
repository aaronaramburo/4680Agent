"""System commands, by allow-list.

The agent runs on a public server, so there is no shell here at all: each
"command" is a named Python function, and the validator only accepts these names.
"""
import platform
import sys
from datetime import datetime

from .. import config


def _current_time(ws):
    now = datetime.now().astimezone()
    return {"local_time": now.strftime("%Y-%m-%d %H:%M:%S"), "weekday": now.strftime("%A"),
            "timezone": str(now.tzinfo)}


def _system_info(ws):
    return {"os": f"{platform.system()} {platform.release()}", "machine": platform.machine(),
            "python": sys.version.split()[0]}


def _workspace_usage(ws):
    files = ws.list_files()
    return {
        "files": len(files),
        "file_limit": config.MAX_FILES,
        "bytes_used": sum(p.stat().st_size for p in files),
        "tasks": ws.query("SELECT COUNT(*) AS n FROM tasks")[0]["n"],
    }


COMMANDS = {
    "current_time": _current_time,
    "system_info": _system_info,
    "workspace_usage": _workspace_usage,
}


def run_command(ws, command):
    return COMMANDS[command](ws)
