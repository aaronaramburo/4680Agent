"""Action executor: dispatches a validated Action to its handler.

Handlers never raise past this point. Every outcome becomes a status on the
Action (ok / error / blocked / cancelled) plus a result the model can read.
"""
from .actions import ActionError, database, files, system, web
from .safety import SafetyError
from .schema import TOOLS

_HANDLERS = {
    "list_files": files.list_files,
    "read_file": files.read_file,
    "write_file": files.write_file,
    "append_file": files.append_file,
    "delete_file": files.delete_file,
    "add_task": database.add_task,
    "list_tasks": database.list_tasks,
    "update_task": database.update_task,
    "delete_task": database.delete_task,
    "get_weather": web.get_weather,
    "lookup_topic": web.lookup_topic,
    "run_command": system.run_command,
}
assert set(_HANDLERS) == set(TOOLS), "every registered tool needs a handler, and vice versa"


def execute(action, workspace):
    try:
        action.result = _HANDLERS[action.tool](workspace, **action.args)
        action.status = "ok"
    except SafetyError as e:
        action.status, action.result = "blocked", f"Blocked by safety check: {e}"
    except ActionError as e:
        action.status, action.result = "error", str(e)
    except Exception as e:  # a bug in a handler must not take the app down
        action.status, action.result = "error", f"Unexpected {type(e).__name__} while running {action.tool}."
    return action
