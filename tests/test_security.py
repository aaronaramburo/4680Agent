"""Pre-deploy checks: nothing secret is in the files that get pushed, and budgets hold."""
import re
import subprocess
from pathlib import Path

import pytest

from agent import config, core
from agent.audit import AuditLog
from agent.core import Agent, AgentError
from agent.workspace import Workspace
from tests.test_agent import ScriptedLLM, step

ROOT = Path(__file__).resolve().parent.parent
NEVER_PUSHED = {".env", "secrets.toml"}
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", "data", ".venv", "venv", ".claude"}

SECRET_PATTERNS = {
    "Anthropic key": r"sk-ant-[A-Za-z0-9_\-]{20,}",
    "OpenAI-style key": r"sk-[A-Za-z0-9]{32,}",
    "AWS access key": r"AKIA[0-9A-Z]{16}",
    "Google API key": r"AIza[0-9A-Za-z_\-]{35}",
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{36,}",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "connection string with password": r"(postgres|postgresql|mysql|mongodb(\+srv)?|redis)://[^\s\"']+:[^\s\"']+@",
}


def pushed_files():
    for path in ROOT.rglob("*"):
        if path.is_file() and path.name not in NEVER_PUSHED and not SKIP_DIRS & set(path.parts):
            yield path


def test_no_secrets_in_files_that_get_pushed():
    findings = []
    for path in pushed_files():
        text = path.read_bytes().decode("utf-8", errors="ignore")
        findings += [f"{name} in {path.relative_to(ROOT)}"
                     for name, pattern in SECRET_PATTERNS.items() if re.search(pattern, text)]
    assert not findings, findings


def test_secret_files_are_git_ignored():
    ignored = (ROOT / ".gitignore").read_text().split()
    assert {".env", ".streamlit/secrets.toml", "data/"} <= set(ignored)
    if (ROOT / ".git").exists():
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout
        assert not {".env", ".streamlit/secrets.toml"} & set(tracked.split())


def test_env_example_has_no_value():
    line = next(l for l in (ROOT / ".env.example").read_text().splitlines()
                if l.startswith("ANTHROPIC_API_KEY"))
    assert line.strip() == "ANTHROPIC_API_KEY="


def test_key_is_only_read_in_one_place():
    readers = [p.relative_to(ROOT).as_posix() for p in pushed_files()
               if p.suffix == ".py" and "tests" not in p.parts
               and re.search(r"(getenv|environ|secrets)[^\n]*ANTHROPIC_API_KEY",
                             p.read_text(encoding="utf-8"))]
    assert readers == ["app.py"]


def test_tracebacks_are_hidden_from_visitors():
    assert 'showErrorDetails = "none"' in (ROOT / ".streamlit" / "config.toml").read_text()


def test_audit_log_masks_key_shaped_strings(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    fake_key = "sk-ant-" + "api03-" + "a" * 30   # assembled so the scan above doesn't flag this file
    log.record("llm_error", error=f"invalid x-api-key: {fake_key}")
    assert "sk-ant" not in (tmp_path / "audit.jsonl").read_text()
    assert "[redacted key]" in log.read()[0]["error"]


def test_daily_budget_survives_new_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_LLM_CALLS_PER_DAY", 2)
    monkeypatch.setitem(core._daily, "day", None)
    for n in range(2):  # two different visitors each spend one call
        agent = Agent(ScriptedLLM(step(reply="hi")), Workspace(f"s{n}", base=tmp_path))
        agent.begin("hi")
        agent.next_step()
    third = Agent(ScriptedLLM(step(reply="hi")), Workspace("s3", base=tmp_path))
    third.begin("hi")
    with pytest.raises(AgentError, match="budget for today"):
        third.next_step()
    monkeypatch.setitem(core._daily, "day", None)
