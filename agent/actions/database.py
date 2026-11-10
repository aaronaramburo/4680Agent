"""Task tracker backed by SQLite.

The model never writes SQL. It picks a tool and supplies values; the
statements are fixed here and every value is bound as a parameter.
"""
from . import ActionError

_COLUMNS = "id, title, course, due_date, priority, status"
_EDITABLE = ("title", "course", "due_date", "priority", "status")


def add_task(ws, title, course=None, due_date=None, priority="medium"):
    cursor = ws.execute(
        "INSERT INTO tasks (title, course, due_date, priority) VALUES (?, ?, ?, ?)",
        (title.strip(), course, due_date, priority))
    return {"task": _get(ws, cursor.lastrowid)}


def list_tasks(ws, status="open", course=None):
    where, params = [], []
    if status != "all":
        where.append("status = ?")
        params.append(status)
    if course:
        where.append("LOWER(course) = LOWER(?)")
        params.append(course)
    sql = f"SELECT {_COLUMNS} FROM tasks"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY due_date IS NULL, due_date, id"
    tasks = ws.query(sql, params)
    return {"tasks": tasks, "count": len(tasks)}


def update_task(ws, id, **changes):
    before = _get(ws, id)
    fields = [k for k in _EDITABLE if k in changes]  # column names come from our list, not the model
    assignments = ", ".join(f"{k} = ?" for k in fields)
    ws.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", [changes[k] for k in fields] + [id])
    return {"before": before, "task": _get(ws, id)}


def delete_task(ws, id):
    task = _get(ws, id)
    ws.execute("DELETE FROM tasks WHERE id = ?", (id,))
    return {"deleted": task}


def _get(ws, task_id):
    rows = ws.query(f"SELECT {_COLUMNS} FROM tasks WHERE id = ?", (task_id,))
    if not rows:
        raise ActionError(f"No task with id {task_id}. Use list_tasks to find the right id.")
    return rows[0]
