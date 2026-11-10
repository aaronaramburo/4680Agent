"""Runs the real app.py headlessly with a scripted model: the page renders, a request
flows through, and a destructive action waits for the button click."""
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agent import config
from agent.llm import LLMClient, LLMResult
from tests.test_agent import step

APP = Path(__file__).resolve().parent.parent / "app.py"


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
    script = [
        step(actions=[("list_files", {})]),
        step(actions=[("delete_file", {"path": "old_draft.txt"})]),
        step(reply="Deleted **old_draft.txt**."),
    ]

    def complete(self, system, messages):
        text = json.dumps(script.pop(0))
        return LLMResult(text=text, content=text, model="scripted")

    monkeypatch.setattr(LLMClient, "complete", complete)
    return AppTest.from_file(str(APP), default_timeout=30).run()


def button(app, label):
    return next(b for b in app.button if b.label == label)


def test_home_screen_renders(app):
    assert not app.exception
    assert len([b for b in app.button if b.label == "Try it"]) == 4
    assert not app.error                       # no "missing API key" banner


def test_request_pauses_for_confirmation_then_runs(app):
    files = app.session_state.ws.files
    button(app, "Try it").click().run()
    assert not app.exception
    assert app.session_state.pending is not None
    assert (files / "old_draft.txt").exists()            # nothing deleted before approval
    assert app.chat_input[0].disabled                    # can't type past a pending confirmation

    button(app, "Yes, go ahead").click().run()
    assert not app.exception
    assert app.session_state.pending is None
    assert not (files / "old_draft.txt").exists()
    assert app.session_state.turns[-1]["reply"] == "Deleted **old_draft.txt**."
