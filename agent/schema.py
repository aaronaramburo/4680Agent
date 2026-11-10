"""Tool registry: the single source of truth for what the agent may do.

The system prompt's tool catalog and the validator are both generated from
this table, so the model is never told about a tool the code cannot check.
"""
from dataclasses import dataclass, field

READ = "read"                # no side effects
WRITE = "write"              # creates or changes data, recoverable
DESTRUCTIVE = "destructive"  # deletes or overwrites; needs user confirmation


@dataclass(frozen=True)
class Param:
    type: type
    description: str
    required: bool = True
    enum: tuple = ()
    max_len: int = 0


@dataclass(frozen=True)
class Tool:
    name: str
    domain: str
    description: str
    risk: str
    params: dict = field(default_factory=dict)


_PRIORITY = ("low", "medium", "high")

TOOLS = {t.name: t for t in [
    # --- files (sandboxed workspace) ---
    Tool("list_files", "files", "List every file in the workspace with its size.", READ),
    Tool("read_file", "files", "Read a text file from the workspace.", READ, {
        "path": Param(str, "relative path, e.g. notes/biology.md", max_len=200),
    }),
    Tool("write_file", "files",
         "Create a file with the given content. Overwriting an existing file needs user confirmation.",
         WRITE, {
             "path": Param(str, "relative path ending in .txt, .md, .csv or .json", max_len=200),
             "content": Param(str, "full text of the file", max_len=50_000),
         }),
    Tool("append_file", "files", "Append text to the end of a file (creates it if missing).", WRITE, {
        "path": Param(str, "relative path", max_len=200),
        "content": Param(str, "text to append", max_len=50_000),
    }),
    Tool("delete_file", "files", "Permanently delete a file. Needs user confirmation.", DESTRUCTIVE, {
        "path": Param(str, "relative path", max_len=200),
    }),
    # --- database (SQLite task tracker) ---
    Tool("add_task", "database", "Add an assignment or to-do to the task tracker.", WRITE, {
        "title": Param(str, "short description of the task", max_len=200),
        "course": Param(str, "course code or name", required=False, max_len=60),
        "due_date": Param(str, "YYYY-MM-DD", required=False, max_len=10),
        "priority": Param(str, "defaults to medium", required=False, enum=_PRIORITY),
    }),
    Tool("list_tasks", "database", "List tasks, soonest due date first.", READ, {
        "status": Param(str, "defaults to open", required=False, enum=("open", "done", "all")),
        "course": Param(str, "only tasks for this course", required=False, max_len=60),
    }),
    Tool("update_task", "database",
         "Change fields on a task. Set status to done to complete it.", WRITE, {
             "id": Param(int, "task id from list_tasks"),
             "title": Param(str, "new title", required=False, max_len=200),
             "course": Param(str, "new course", required=False, max_len=60),
             "due_date": Param(str, "YYYY-MM-DD", required=False, max_len=10),
             "priority": Param(str, "new priority", required=False, enum=_PRIORITY),
             "status": Param(str, "new status", required=False, enum=("open", "done")),
         }),
    Tool("delete_task", "database", "Permanently delete a task. Needs user confirmation.", DESTRUCTIVE, {
        "id": Param(int, "task id from list_tasks"),
    }),
    # --- external APIs ---
    Tool("get_weather", "api", "Current weather and a 7-day forecast for a city (Open-Meteo).", READ, {
        "city": Param(str, "city name, e.g. Pomona", max_len=80),
    }),
    Tool("lookup_topic", "api", "Short encyclopedia summary of a topic (Wikipedia).", READ, {
        "topic": Param(str, "topic or article title", max_len=120),
    }),
    # --- system (allow-listed commands only) ---
    Tool("run_command", "system", "Run one allow-listed system command.", READ, {
        "command": Param(str, "which command to run",
                         enum=("current_time", "system_info", "workspace_usage")),
    }),
]}


def tool_catalog() -> str:
    """Render the registry as text for the system prompt (stable order)."""
    lines = []
    for tool in TOOLS.values():
        lines.append(f"- {tool.name} [{tool.domain}, {tool.risk}]: {tool.description}")
        for name, p in tool.params.items():
            kind = " | ".join(f'"{v}"' for v in p.enum) if p.enum else p.type.__name__
            need = "required" if p.required else "optional"
            lines.append(f"    {name} ({kind}, {need}): {p.description}")
    return "\n".join(lines)
