"""StudyPilot - Streamlit interface for the coursework agent.

Run locally:  streamlit run app.py --server.address localhost

This file is presentation only. Colors and type come from .streamlit/config.toml,
extra styling from assets/style.css, and everything the agent does lives in agent/.
"""
import json
import os
import uuid
from datetime import date
from html import escape
from pathlib import Path

import streamlit as st

from agent import config
from agent.core import Agent, AgentError
from agent.llm import LLMClient
from agent.prompts import SYSTEM_PROMPT
from agent.safety import describe
from agent.schema import TOOLS
from agent.workspace import Workspace, purge_stale_sessions

st.set_page_config(page_title="StudyPilot", page_icon=":material/explore:", layout="wide")

# status -> (icon, badge color, words shown to the user)
STATUS = {
    "ok": (":material/check_circle:", "green", "Done"),
    "error": (":material/error:", "orange", "Failed"),
    "blocked": (":material/block:", "red", "Blocked by safety check"),
    "cancelled": (":material/cancel:", "gray", "Cancelled by you"),
    "pending": (":material/hourglass_top:", "orange", "Waiting for your confirmation"),
}
DOMAIN = {
    "files": (":material/description:", "Files"),
    "database": (":material/checklist:", "Tasks"),
    "api": (":material/public:", "Web"),
    "system": (":material/terminal:", "System"),
}
RISK_COLOR = {"read": "gray", "write": "blue", "destructive": "red"}

EXAMPLES = [
    (":material/event_note:", "Plan my week",
     "What's due this week? Check the weather in Pomona too and tell me which day is best to study outside."),
    (":material/menu_book:", "Make a study guide",
     "Read my biology notes and write a study guide with 5 practice questions to a new file."),
    (":material/add_task:", "Add assignments",
     "Add two tasks: CS4680 demo video due next Friday (high priority) and email the TA about office hours by Wednesday."),
    (":material/delete_sweep:", "Clean up",
     "Delete old_draft.txt and remove the reading response task."),
]

PIPELINE = [
    ("You ask", "Type a request in your own words. No commands to learn."),
    ("The model plans", "It returns one JSON object: a short thought, a list of actions, and a reply."),
    ("The interpreter validates", "Unknown tools, wrong argument types, bad dates and oversize content are "
                                  "rejected and sent back to the model to fix."),
    ("The safety layer checks", "File paths stay inside your sandbox. Anything that deletes or overwrites "
                                "waits for your approval."),
    ("The executor runs", "Each action runs, and its result goes back to the model, which takes another "
                          "step or writes the final answer."),
    ("Everything is logged", "Requests, model calls, confirmations and results are recorded in the audit log."),
]

# Compass mark; the drawing itself is a CSS background in assets/style.css.
LOGO = '<span class="sp-mark" aria-hidden="true"></span>'


# ---------- session setup ----------

def load_api_key() -> bool:
    """The key comes from .env locally or from Streamlit secrets when deployed."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return True
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:  # no secrets file at all
        key = None
    if key:
        os.environ["ANTHROPIC_API_KEY"] = key
    return bool(key)


def init_session():
    ss = st.session_state
    if "sid" not in ss:
        purge_stale_sessions()
        ss.sid = uuid.uuid4().hex[:12]
        ss.turns = []       # [{"user": str, "steps": [Step], "reply": str, "error": str}]
        ss.pending = None   # Step waiting for the user's yes/no
        ss.job = None       # ("request", text) | ("confirm", bool)
        ss.agent = None
    ss.ws = Workspace(ss.sid)
    if ss.agent is None and HAS_KEY:
        ss.agent = Agent(LLMClient(), ss.ws)


def queue_request(text: str):
    st.session_state.job = ("request", text)


def queue_confirmation(approved: bool):
    st.session_state.job = ("confirm", approved)


def reset_workspace():
    ss = st.session_state
    ss.ws.reset()
    ss.turns, ss.pending, ss.job, ss.agent = [], None, None, None


# ---------- the agent loop, driven from the UI ----------

def run_job():
    """Advance the agent until it gives a final reply or needs a confirmation."""
    ss = st.session_state
    kind, payload = ss.job
    ss.job = None
    agent = ss.agent

    if kind == "request":
        ss.turns.append({"user": payload, "steps": [], "reply": "", "error": ""})
        with st.chat_message("user", avatar=":material/person:"):
            st.markdown(payload)
    turn = ss.turns[-1]

    with st.chat_message("assistant", avatar=":material/explore:"):
        with st.status("Thinking…", expanded=True) as status:
            agent.llm.on_retry = lambda reason, delay, attempt: status.write(
                f":material/schedule: The model is busy ({reason}). Retry {attempt} in {delay:.0f}s…")
            try:
                if kind == "request":
                    agent.begin(payload)
                else:
                    step, ss.pending = ss.pending, None
                    status.write("Running the approved actions…" if payload else "Cancelling…")
                    agent.execute(step, approved=payload)
                while True:
                    status.update(label="Deciding what to do next…")
                    step = agent.next_step()
                    if not step.actions:
                        turn["reply"] = step.reply
                        break
                    turn["steps"].append(step)
                    status.write(step.thought)
                    if step.needs_confirmation:
                        ss.pending = step
                        break
                    status.update(label="Running " + ", ".join(a.tool for a in step.actions) + "…")
                    agent.execute(step, approved=True)
                    for a in step.actions:
                        icon, color, words = STATUS[a.status]
                        status.write(f":{color}[{icon}] `{a.tool}` {words.lower()}")
            except AgentError as e:
                turn["error"] = str(e)
    st.rerun()


# ---------- rendering ----------

def html(markup: str):
    st.html(markup)


def render_header():
    state = ('<span class="sp-pill"><span class="sp-dot"></span>Model connected</span>' if HAS_KEY
             else '<span class="sp-pill"><span class="sp-dot off"></span>No API key</span>')
    html(f"""
    <header class="sp-hero">
      {LOGO}
      <div>
        <h1 class="sp-title">StudyPilot</h1>
        <p class="sp-tagline">Ask in plain language. It works across your notes, your assignments and the web,
        and checks with you before it deletes anything.</p>
      </div>
      <div class="sp-pills">{state}<span class="sp-pill">Sandboxed workspace</span></div>
    </header>""")


def render_action(action):
    icon, color, words = STATUS[action.status]
    if action.status == "pending" and action.risk != "destructive":
        words = "Queued behind the confirmation"
    domain_icon, domain_name = DOMAIN[action.domain]
    label = (f"**{action.tool}** &nbsp; :gray-badge[{domain_icon} {domain_name}] "
             f":{RISK_COLOR[action.risk]}-badge[{action.risk}] :{color}-badge[{words}]")
    with st.expander(label, icon=icon):
        left, right = st.columns(2)
        with left:
            st.caption("Arguments (validated)")
            st.json(action.args, expanded=True)
        with right:
            st.caption("Result")
            if action.result is None:
                st.write("Not run yet.")
            elif isinstance(action.result, (dict, list)):
                st.json(action.result, expanded=2)
            else:
                st.write(action.result)


def render_turn(index, turn):
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(turn["user"])
    if not (turn["steps"] or turn["reply"] or turn["error"]):
        return
    with st.chat_message("assistant", avatar=":material/explore:"):
        for n, step in enumerate(turn["steps"], 1):
            with st.container(border=True, key=f"step-{index}-{n}"):
                html(f'<div class="sp-step"><span class="sp-step-num" aria-hidden="true">{n}</span>'
                     f'<div class="sp-step-text"><span class="sp-step-label">Step {n}</span>'
                     f'{escape(step.thought)}</div></div>')
                for action in step.actions:
                    render_action(action)
        if turn["reply"]:
            st.markdown(turn["reply"])
        if turn["error"]:
            st.error(turn["error"], icon=":material/error:")


def render_confirmation(step):
    risky = [a for a in step.actions if a.risk == "destructive" and a.status == "pending"]
    with st.container(border=True, key="confirm"):
        st.markdown("#### :orange[:material/warning:] Confirm before I continue")
        st.write("Nothing has run yet. These actions can't be undone:")
        for action in risky:
            line = describe(action)
            if action.tool == "delete_task":
                match = st.session_state.ws.query("SELECT title FROM tasks WHERE id = ?",
                                                  (action.args["id"],))
                line += f" (*{match[0]['title']}*)" if match else " (no such task)"
            st.markdown(f"- {line}")
        approve, cancel, _ = st.columns([1.4, 1, 3])
        approve.button("Yes, go ahead", type="primary", icon=":material/check:",
                       on_click=queue_confirmation, args=(True,), width="stretch")
        cancel.button("Cancel", on_click=queue_confirmation, args=(False,), width="stretch")


def render_examples():
    st.markdown("#### What would you like to get done?")
    st.caption("Start with an example, or type your own request at the bottom of the page.")
    rows = [EXAMPLES[:2], EXAMPLES[2:]]
    for r, row in enumerate(rows):
        for c, (col, (icon, title, prompt)) in enumerate(zip(st.columns(2), row)):
            with col, st.container(border=True, key=f"example-{r}{c}"):
                st.markdown(f"**:primary[{icon}] &nbsp;{title}**")
                st.caption(prompt)
                st.button("Try it", key=f"try-{r}{c}", icon=":material/arrow_forward:",
                          on_click=queue_request, args=(prompt,), disabled=not HAS_KEY)


def format_due(iso):
    if not iso:
        return ""
    try:
        return date.fromisoformat(iso).strftime("%a, %b %d").replace(" 0", " ")
    except ValueError:
        return iso


def render_sidebar():
    ss = st.session_state
    with st.sidebar:
        html(f'<div class="sp-side-brand">{LOGO}<strong>StudyPilot</strong></div>')
        st.caption("This is your sandbox. The agent can only read and change what is listed here.")

        tasks = ss.ws.query("SELECT id, title, course, due_date, priority, status FROM tasks "
                            "ORDER BY status DESC, due_date IS NULL, due_date")
        open_count = sum(t["status"] == "open" for t in tasks)
        items = ""
        for t in tasks:
            done = t["status"] == "done"
            meta = " · ".join(v for v in (t["course"], format_due(t["due_date"]),
                                          "done" if done else f"{t['priority']} priority") if v)
            items += (f'<li class="sp-task{" done" if done else ""}">'
                      f'<span class="sp-task-bar {"" if done else escape(t["priority"])}"></span><div>'
                      f'<div class="sp-task-title">{escape(t["title"])}</div>'
                      f'<div class="sp-task-meta">#{t["id"]} · {escape(meta)}</div></div></li>')
        html(f'<div class="sp-section"><span>Tasks</span><span>{open_count} open</span></div>'
             f'<ul class="sp-tasks">{items}</ul>')
        if not tasks:
            st.caption("No tasks yet. Ask the agent to add one.")

        files = ss.ws.list_files()
        html(f'<div class="sp-section"><span>Files</span><span>{len(files)}</span></div>')
        for path in files:
            with st.expander(path.relative_to(ss.ws.files).as_posix(), icon=":material/description:"):
                st.code(path.read_text(encoding="utf-8", errors="replace"), language=None, wrap_lines=True)
        if not files:
            st.caption("No files yet.")

        st.divider()
        used = ss.agent.llm_calls if ss.agent else 0
        st.progress(min(used / config.MAX_LLM_CALLS_PER_SESSION, 1.0),
                    text=f"Model calls used: {used} of {config.MAX_LLM_CALLS_PER_SESSION}")
        st.button("Reset workspace", icon=":material/restart_alt:", on_click=reset_workspace,
                  width="stretch", help="Restore the sample files and tasks and clear the conversation.")
        st.caption(f"Model `{config.MODEL}` · session `{ss.sid}`")


def event_label(event: str) -> str:
    return event.replace("llm", "model").replace("_", " ")


def render_audit_tab(entries):
    st.caption("Every request, model call, validation failure, confirmation and action is "
               "recorded here as it happens.")
    if not entries:
        st.info("Nothing logged yet. Ask the agent to do something and it will show up here.",
                icon=":material/history:")
        return

    actions = [e for e in entries if e["event"] == "action_executed"]
    stopped = sum(e.get("status") in ("blocked", "error") for e in actions)
    stopped += sum(e["event"] == "confirmation" and e.get("decision") == "denied" for e in entries)
    stats = [
        ("Requests", sum(e["event"] == "user_request" for e in entries)),
        ("Model calls", sum(e["event"] == "llm_call" for e in entries)),
        ("Actions run", sum(e.get("status") == "ok" for e in actions)),
        ("Blocked, failed or cancelled", stopped),
    ]
    for col, (label, value) in zip(st.columns(4), stats):
        col.metric(label, value, border=True)

    kinds = sorted({e["event"] for e in entries})
    chosen = st.pills("Show", kinds, selection_mode="multi", label_visibility="collapsed",
                      format_func=event_label)
    shown = [e for e in entries if not chosen or e["event"] in chosen]
    rows = [{
        "Time (UTC)": e["time"][11:19],
        "Event": event_label(e["event"]),
        "Details": json.dumps({k: v for k, v in e.items() if k not in ("time", "event")},
                              ensure_ascii=False),
    } for e in reversed(shown)]
    st.dataframe(rows, hide_index=True, width="stretch", height=420,
                 column_config={"Details": st.column_config.TextColumn(width="large")})
    st.download_button("Download full log (JSONL)", icon=":material/download:",
                       data="\n".join(json.dumps(e, ensure_ascii=False) for e in entries),
                       file_name=f"studypilot_audit_{st.session_state.sid}.jsonl")


def render_about_tab():
    st.markdown("#### From a sentence to a safe action")
    cards = "".join(f"<li><i>Step {n}</i><b>{escape(title)}</b><span>{escape(text)}</span></li>"
                    for n, (title, text) in enumerate(PIPELINE, 1))
    html(f'<ol class="sp-pipeline">{cards}</ol>')

    st.markdown("#### What the agent can do")
    st.dataframe([{"Tool": t.name, "Area": DOMAIN[t.domain][1], "Risk": t.risk,
                   "What it does": t.description} for t in TOOLS.values()],
                 hide_index=True, width="stretch")
    with st.expander("System prompt (the full text sent to the model)", icon=":material/article:"):
        st.code(SYSTEM_PROMPT, language=None, wrap_lines=True)


# ---------- page ----------

html(f"<style>{(Path(__file__).parent / 'assets' / 'style.css').read_text(encoding='utf-8')}</style>")

HAS_KEY = load_api_key()
init_session()
ss = st.session_state
audit_entries = ss.agent.audit.read() if ss.agent else []
render_sidebar()
render_header()

if not HAS_KEY:
    st.error("**No API key found.** Add `ANTHROPIC_API_KEY` to a `.env` file (local) or to the "
             "app's Secrets (Streamlit Cloud), then reload.", icon=":material/key:")

chat_tab, audit_tab, about_tab = st.tabs([
    ":material/chat: Agent",
    f":material/history: Audit log ({len(audit_entries)})",
    ":material/info: How it works",
])

with chat_tab:
    if not ss.turns and not ss.job:
        render_examples()
    for index, turn in enumerate(ss.turns):
        render_turn(index, turn)
    if ss.pending and not ss.job:
        render_confirmation(ss.pending)
    if ss.job and ss.agent:
        run_job()

with audit_tab:
    render_audit_tab(audit_entries)
with about_tab:
    render_about_tab()

placeholder = ("Approve or cancel the pending action first" if ss.pending
               else "Ask StudyPilot to do something…")
if text := st.chat_input(placeholder, disabled=bool(ss.pending) or not HAS_KEY):
    queue_request(text.strip())
    st.rerun()
