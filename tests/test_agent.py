"""Offline tests: the model is scripted, so these run without an API key."""
import json

import anthropic
import httpx2
import pytest
import requests

from agent import config
from agent.actions import ActionError, database, files, web
from agent.core import Agent, AgentError
from agent.executor import execute
from agent.interpreter import Action, PlanError, parse_step
from agent.llm import LLMClient, LLMError, LLMResult
from agent.safety import SafetyError, effective_risk, resolve_path
from agent.schema import TOOLS
from agent.workspace import Workspace


class ScriptedLLM:
    """Stands in for LLMClient: returns queued responses and records what it was sent."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.seen = []

    def complete(self, system, messages):
        self.seen.append(list(messages))
        text = self.responses.pop(0)
        if not isinstance(text, str):
            text = json.dumps(text)
        return LLMResult(text=text, content=text, model="scripted")


def step(thought="t", actions=(), reply=""):
    return {"thought": thought, "actions": [{"tool": t, "args": a} for t, a in actions], "reply": reply}


@pytest.fixture
def ws(tmp_path):
    """A workspace with three files and four tasks, as if a student had been using it."""
    ws = Workspace("test", base=tmp_path)
    for name, text in {
        "notes/biology_ch3.md": "Mitochondria: ATP production via cellular respiration.",
        "notes/cs4680_prompting.md": "ReAct: reason, act, observe.",
        "old_draft.txt": "Scratch draft.",
    }.items():
        files.import_file(ws, name, text.encode())
    for title in ("AI agent project", "Chapter 3 quiz", "Lab report", "Reading response 6"):
        database.add_task(ws, title)
    return ws


def test_new_workspace_starts_empty(tmp_path):
    fresh = Workspace("fresh", base=tmp_path)
    assert fresh.list_files() == [] and fresh.query("SELECT * FROM tasks") == []


# ---------- uploads ----------

def test_upload_is_saved_and_readable(ws):
    files.import_file(ws, "syllabus.md", "﻿# Syllabus".encode("utf-8"))   # BOM from Windows editors
    assert run(ws, "read_file", path="syllabus.md").result["content"] == "# Syllabus"


@pytest.mark.parametrize("name, data, error", [
    ("photo.png", b"\x89PNG", SafetyError),               # not an allowed type
    ("../outside.txt", b"x", SafetyError),                # escapes the sandbox
    ("notes.txt", b"\xff\xfe\x00binary", ActionError),    # not text
    ("big.txt", b"x" * (config.MAX_FILE_BYTES + 1), SafetyError),
])
def test_bad_uploads_are_refused(ws, name, data, error):
    with pytest.raises(error):
        files.import_file(ws, name, data)
    assert len(ws.list_files()) == 3


def run(ws, tool, **args):
    return execute(Action(tool, args, TOOLS[tool].risk), ws)


# ---------- interpreter ----------

def test_parses_plain_and_fenced_json():
    body = '{"thought": "x", "actions": [{"tool": "list_files", "args": {}}], "reply": ""}'
    assert parse_step(body).actions[0].tool == "list_files"
    assert parse_step(f"Sure!\n```json\n{body}\n```").actions[0].tool == "list_files"


@pytest.mark.parametrize("text, expected", [
    ("I deleted everything for you!", "did not contain a JSON object"),
    (step(actions=[("rm_rf", {"path": "/"})]), "unknown tool"),
    (step(actions=[("read_file", {})]), "missing required argument 'path'"),
    (step(actions=[("read_file", {"path": "a.txt", "sudo": True})]), "no argument 'sudo'"),
    (step(actions=[("delete_task", {"id": "1; DROP TABLE tasks"})]), "must be int"),
    (step(actions=[("delete_task", {"id": True})]), "must be int"),
    (step(actions=[("add_task", {"title": "x", "due_date": "next friday"})]), "YYYY-MM-DD"),
    (step(actions=[("add_task", {"title": "x", "due_date": "2026-02-30"})]), "YYYY-MM-DD"),
    (step(actions=[("add_task", {"title": "x", "priority": "urgent"})]), "must be one of"),
    (step(actions=[("run_command", {"command": "rm -rf /"})]), "must be one of"),
    (step(actions=[("update_task", {"id": 1})]), "at least one field"),
    (step(actions=[("list_files", {})] * (config.MAX_ACTIONS_PER_STEP + 1)), "Too many actions"),
    (step(), "must contain the answer"),
    ({"thought": "x", "actions": "list_files", "reply": ""}, "must be a list"),
])
def test_rejects_invalid_output(text, expected):
    with pytest.raises(PlanError) as e:
        parse_step(text if isinstance(text, str) else json.dumps(text))
    assert expected in str(e.value)


# ---------- safety ----------

@pytest.mark.parametrize("path", [
    "../secrets.txt", "notes/../../x.txt", "/etc/passwd", "C:\\Windows\\win.ini", "~/x.txt",
    ".env", "notes/.hidden.txt", "script.py", "run.exe", "a/b/c/d.txt", "",
])
def test_paths_outside_sandbox_are_refused(ws, path):
    with pytest.raises(SafetyError):
        resolve_path(ws.files, path)


def test_blocked_path_never_touches_disk(ws, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me")
    action = run(ws, "delete_file", path="../../outside.txt")
    assert action.status == "blocked" and outside.read_text() == "keep me"


def test_overwrite_is_escalated_to_destructive(ws):
    new = Action("write_file", {"path": "new.md", "content": "x"}, "write")
    existing = Action("write_file", {"path": "old_draft.txt", "content": "x"}, "write")
    assert effective_risk(new, ws.files) == "write"
    assert effective_risk(existing, ws.files) == "destructive"


def test_file_size_limit(ws):
    action = run(ws, "write_file", path="big.txt", content="x" * (config.MAX_FILE_BYTES + 1))
    assert action.status == "blocked" and not (ws.files / "big.txt").exists()


# ---------- executors ----------

def test_file_round_trip(ws):
    assert run(ws, "write_file", path="notes/new.md", content="hello").status == "ok"
    assert run(ws, "append_file", path="notes/new.md", content="world").status == "ok"
    assert run(ws, "read_file", path="notes/new.md").result["content"] == "hello\nworld"
    assert run(ws, "delete_file", path="notes/new.md").status == "ok"
    assert run(ws, "read_file", path="notes/new.md").status == "error"


def test_task_lifecycle(ws):
    task = run(ws, "add_task", title="Essay", course="ENG2100", due_date="2026-12-01").result["task"]
    assert task["priority"] == "medium" and task["status"] == "open"
    run(ws, "update_task", id=task["id"], status="done")
    open_ids = [t["id"] for t in run(ws, "list_tasks").result["tasks"]]
    assert task["id"] not in open_ids
    assert run(ws, "list_tasks", status="done").result["count"] == 1
    assert run(ws, "delete_task", id=task["id"]).status == "ok"
    assert run(ws, "delete_task", id=task["id"]).status == "error"


def test_sql_injection_is_stored_as_text(ws):
    run(ws, "add_task", title="x'); DROP TABLE tasks;--")
    assert run(ws, "list_tasks", status="all").result["count"] == 5


def test_system_commands(ws):
    assert run(ws, "run_command", command="workspace_usage").result["files"] == 3
    assert "python" in run(ws, "run_command", command="system_info").result


def test_api_failure_is_reported_not_raised(ws, monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError()
    monkeypatch.setattr(web.requests, "get", boom)
    action = run(ws, "get_weather", city="Pomona")
    assert action.status == "error" and "Could not reach" in action.result


# ---------- agent loop ----------

def test_multi_step_request(ws):
    llm = ScriptedLLM(
        step(actions=[("read_file", {"path": "notes/biology_ch3.md"})]),
        step(actions=[("write_file", {"path": "guide.md", "content": "# Guide"})]),
        step(reply="Wrote guide.md"),
    )
    agent = Agent(llm, ws)
    agent.begin("make a study guide")
    first = agent.next_step()
    agent.execute(first, approved=True)
    assert "Mitochondria" in llm_last_user(agent)      # observation reached the model
    agent.execute(agent.next_step(), approved=True)
    assert agent.next_step().reply == "Wrote guide.md"
    assert (ws.files / "guide.md").read_text() == "# Guide"


def test_destructive_action_waits_and_can_be_cancelled(ws):
    agent = Agent(ScriptedLLM(step(actions=[("delete_file", {"path": "old_draft.txt"})])), ws)
    agent.begin("delete my old draft")
    proposed = agent.next_step()
    assert proposed.needs_confirmation
    assert (ws.files / "old_draft.txt").exists()        # nothing ran yet
    agent.execute(proposed, approved=False)
    assert proposed.actions[0].status == "cancelled"
    assert (ws.files / "old_draft.txt").exists()
    assert "declined" in llm_last_user(agent)


def test_destructive_action_runs_when_approved(ws):
    agent = Agent(ScriptedLLM(step(actions=[("delete_file", {"path": "old_draft.txt"})])), ws)
    agent.begin("delete my old draft")
    agent.execute(agent.next_step(), approved=True)
    assert not (ws.files / "old_draft.txt").exists()


def test_invalid_output_is_repaired(ws):
    llm = ScriptedLLM("Sure, deleting now!", step(actions=[("format_disk", {})]), step(reply="ok"))
    agent = Agent(llm, ws)
    agent.begin("hi")
    assert agent.next_step().reply == "ok"
    assert "failed validation" in llm.seen[1][-1]["content"]
    assert "unknown tool 'format_disk'" in llm.seen[2][-1]["content"]


def test_gives_up_after_repeated_invalid_output(ws):
    agent = Agent(ScriptedLLM(*["nope"] * (config.MAX_REPAIR_ATTEMPTS + 1)), ws)
    agent.begin("hi")
    with pytest.raises(AgentError):
        agent.next_step()


def test_step_limit_stops_a_runaway_loop(ws):
    looping = step(actions=[("list_files", {})])
    agent = Agent(ScriptedLLM(*[looping] * (config.MAX_STEPS + 1)), ws)
    agent.begin("loop forever")
    for _ in range(config.MAX_STEPS):
        agent.execute(agent.next_step(), approved=True)
    assert "step limit" in llm_last_user(agent)
    final = agent.next_step()
    assert final.actions == [] and "step limit" in final.reply


def test_everything_is_audited(ws):
    agent = Agent(ScriptedLLM("garbage", step(actions=[("delete_task", {"id": 1})]), step(reply="done")), ws)
    agent.begin("delete task 1")
    agent.execute(agent.next_step(), approved=True)
    agent.next_step()
    events = [e["event"] for e in agent.audit.read()]
    assert events == ["user_request", "llm_call", "validation_failed", "llm_call", "step_planned",
                      "confirmation", "action_executed", "llm_call", "step_planned"]


def llm_last_user(agent):
    return agent.history[-1]["content"]


# ---------- LLM client ----------

class FlakyMessages:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def fake_client(*outcomes):
    messages = FlakyMessages(*outcomes)
    client = type("Client", (), {})()
    client.beta = type("Beta", (), {"messages": messages})()
    return client, messages


def api_error(cls, status, headers=None):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(status, headers=headers or {}, request=request), body=None)


def ok_response(text='{"thought": "", "actions": [], "reply": "hi"}', stop_reason="end_turn"):
    block = type("Block", (), {"type": "text", "text": text})()
    usage = type("Usage", (), {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0})()
    return type("Response", (), {"content": [block], "usage": usage, "model": "m",
                                 "stop_reason": stop_reason})()


def test_rate_limit_is_retried_with_server_delay():
    client, messages = fake_client(
        api_error(anthropic.RateLimitError, 429, {"retry-after": "7"}),
        api_error(anthropic.InternalServerError, 529),
        ok_response())
    waits, notices = [], []
    llm = LLMClient(client=client, sleep=waits.append, on_retry=lambda *a: notices.append(a))
    result = llm.complete("sys", [{"role": "user", "content": "hi"}])
    assert result.attempts == 3 and messages.calls == 3
    assert waits[0] == 7 and len(notices) == 2


def test_retries_are_bounded():
    client, messages = fake_client(*[api_error(anthropic.InternalServerError, 500)] * 10)
    llm = LLMClient(client=client, max_retries=2, sleep=lambda s: None)
    with pytest.raises(LLMError, match="after 3 attempts"):
        llm.complete("sys", [])
    assert messages.calls == 3


def test_bad_key_fails_fast_with_clear_message():
    client, messages = fake_client(api_error(anthropic.AuthenticationError, 401))
    with pytest.raises(LLMError, match="API key"):
        LLMClient(client=client, sleep=lambda s: None).complete("sys", [])
    assert messages.calls == 1


def test_refusal_and_truncation_are_detected():
    client, _ = fake_client(ok_response(stop_reason="refusal"), ok_response(stop_reason="max_tokens"))
    llm = LLMClient(client=client)
    assert llm.complete("sys", []).refused
    with pytest.raises(LLMError, match="cut off"):
        llm.complete("sys", [])
