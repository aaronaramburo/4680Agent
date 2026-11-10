"""Action interpreter: turns raw model text into a validated Step.

Nothing the model writes is executed until it passes through here. Any
problem is collected into PlanError.errors, which the agent sends back to
the model so it can correct itself.
"""
import json
from dataclasses import dataclass, field
from datetime import date

from .config import MAX_ACTIONS_PER_STEP
from .schema import TOOLS


class PlanError(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class Action:
    tool: str
    args: dict
    risk: str = ""
    status: str = "pending"   # pending | ok | error | blocked | cancelled
    result: object = None

    @property
    def domain(self):
        return TOOLS[self.tool].domain


@dataclass
class Step:
    thought: str
    actions: list = field(default_factory=list)
    reply: str = ""

    @property
    def needs_confirmation(self):
        return any(a.risk == "destructive" and a.status == "pending" for a in self.actions)


def extract_json(text: str) -> dict:
    """Find the first JSON object in the text, tolerating fences or stray prose."""
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text[start:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    raise PlanError(["The response did not contain a JSON object."])


def parse_step(text: str) -> Step:
    obj = extract_json(text)
    errors = []

    unknown = set(obj) - {"thought", "actions", "reply"}
    if unknown:
        errors.append(f"Unexpected top-level keys: {sorted(unknown)}.")
    thought = obj.get("thought", "")
    reply = obj.get("reply", "")
    raw_actions = obj.get("actions", [])
    if not isinstance(thought, str):
        errors.append('"thought" must be a string.')
    if not isinstance(reply, str):
        errors.append('"reply" must be a string.')
    if not isinstance(raw_actions, list):
        raise PlanError(errors + ['"actions" must be a list.'])
    if len(raw_actions) > MAX_ACTIONS_PER_STEP:
        errors.append(f"Too many actions ({len(raw_actions)}); the limit is {MAX_ACTIONS_PER_STEP} per step.")

    actions = []
    for i, raw in enumerate(raw_actions, 1):
        problems = _validate_action(raw)
        if problems:
            errors += [f"Action {i}: {p}" for p in problems]
        else:
            actions.append(Action(raw["tool"], raw.get("args", {}), TOOLS[raw["tool"]].risk))

    if not raw_actions and isinstance(reply, str) and not reply.strip():
        errors.append('With no actions, "reply" must contain the answer for the student.')
    if errors:
        raise PlanError(errors)
    return Step(thought, actions, reply)


def _validate_action(raw) -> list:
    if not isinstance(raw, dict):
        return ['must be an object with "tool" and "args".']
    name = raw.get("tool")
    if name not in TOOLS:
        return [f"unknown tool {name!r}. Valid tools: {', '.join(TOOLS)}."]
    args = raw.setdefault("args", {})
    if not isinstance(args, dict):
        return ['"args" must be an object.']

    # An explicit null means "not provided".
    for key in [k for k, v in args.items() if v is None]:
        del args[key]

    tool, problems = TOOLS[name], []
    for key in args:
        if key not in tool.params:
            problems.append(f"{name} has no argument {key!r}.")
    for key, spec in tool.params.items():
        if key not in args:
            if spec.required:
                problems.append(f"{name} is missing required argument {key!r}.")
            continue
        value = args[key]
        # bool is a subclass of int in Python; reject it explicitly
        if not isinstance(value, spec.type) or isinstance(value, bool):
            problems.append(f"{name}.{key} must be {spec.type.__name__}.")
            continue
        if spec.enum and value not in spec.enum:
            problems.append(f"{name}.{key} must be one of {list(spec.enum)}.")
        if spec.type is str:
            if spec.required and not value.strip() and key != "content":
                problems.append(f"{name}.{key} must not be empty.")
            if spec.max_len and len(value) > spec.max_len:
                problems.append(f"{name}.{key} is too long ({len(value)} > {spec.max_len} characters).")
        if spec.type is int and value < 1:
            problems.append(f"{name}.{key} must be a positive integer.")
        if key == "due_date" and not _is_iso_date(value):
            problems.append(f"{name}.due_date must be a real date in YYYY-MM-DD format.")
    if name == "update_task" and not (set(args) - {"id"}):
        problems.append("update_task needs at least one field to change.")
    return problems


def _is_iso_date(value: str) -> bool:
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False
