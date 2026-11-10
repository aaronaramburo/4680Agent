"""The agent loop: ask the model for a step, validate it, execute it, feed back results.

The loop is driven from outside (the UI) one step at a time so that it can
stop and wait for a human whenever a step contains a destructive action:

    agent.begin("delete my old draft")
    step = agent.next_step()          # model proposes delete_file
    if step.needs_confirmation: ...   # UI asks the user
    agent.execute(step, approved)     # runs or cancels, records observations
    step = agent.next_step()          # model writes the final reply
"""
import json
import threading
from datetime import date

from . import config, prompts
from .audit import AuditLog, redact
from .executor import execute
from .interpreter import PlanError, Step, parse_step
from .llm import LLMError
from .safety import effective_risk


class AgentError(Exception):
    """The turn cannot continue. str(e) is safe to show the user."""


# Shared by every session in this server process. A visitor can get a fresh
# per-session budget by reloading the page, so the app's key needs a cap
# that reloading does not reset.
_daily = {"day": None, "calls": 0}
_daily_lock = threading.Lock()


def _spend_daily_call() -> bool:
    with _daily_lock:
        if _daily["day"] != date.today():
            _daily.update(day=date.today(), calls=0)
        if _daily["calls"] >= config.MAX_LLM_CALLS_PER_DAY:
            return False
        _daily["calls"] += 1
        return True


class Agent:
    def __init__(self, llm, workspace, today=None):
        self.llm = llm
        self.ws = workspace
        self.audit = AuditLog(workspace.audit_path)
        self.history = []          # messages exactly as sent to the API, append-only
        self.llm_calls = 0
        self.steps_this_turn = 0
        self._today = today

    def begin(self, user_text: str):
        today = self._today or date.today().strftime("%Y-%m-%d (%A)")
        self.history.append({"role": "user", "content": prompts.user_message(user_text, today)})
        self.steps_this_turn = 0
        self.audit.record("user_request", text=user_text)

    def next_step(self) -> Step:
        """One model call (plus repair re-asks) -> one validated Step."""
        for attempt in range(config.MAX_REPAIR_ATTEMPTS + 1):
            result = self._call_model()
            self.history.append({"role": "assistant", "content": result.content})
            if result.refused:
                self.audit.record("model_refused")
                return Step("", [], "I can't help with that request.")
            try:
                step = parse_step(result.text)
            except PlanError as e:
                self.audit.record("validation_failed", errors=e.errors, attempt=attempt + 1,
                                  raw_output=result.text)
                self.history.append({"role": "user", "content": prompts.repair_message(e.errors)})
                continue

            for action in step.actions:
                action.risk = effective_risk(action, self.ws.files)
            if step.actions and self.steps_this_turn >= config.MAX_STEPS:
                # The model was told to wrap up and didn't; refuse to run more.
                self.audit.record("step_limit_enforced", dropped=[a.tool for a in step.actions])
                return Step(step.thought, [],
                            step.reply or "I hit the step limit for one request before finishing. "
                                          "Ask me to continue and I'll pick up from here.")
            self.audit.record("step_planned", thought=step.thought,
                              actions=[{"tool": a.tool, "args": a.args, "risk": a.risk}
                                       for a in step.actions],
                              reply=step.reply)
            return step
        raise AgentError("The model kept producing output that failed validation, so nothing "
                         "was executed. Try rephrasing the request.")

    def execute(self, step: Step, approved: bool):
        """Run a step's actions. Destructive ones run only if the user approved."""
        self.steps_this_turn += 1
        for action in step.actions:
            if action.risk == "destructive":
                self.audit.record("confirmation", tool=action.tool, args=action.args,
                                  decision="approved" if approved else "denied")
                if not approved:
                    action.status, action.result = "cancelled", "The student declined this action."
                    continue
            execute(action, self.ws)
            self.audit.record("action_executed", tool=action.tool, domain=action.domain,
                              args=action.args, risk=action.risk, status=action.status,
                              result=action.result)

        observations = [{"tool": a.tool, "status": a.status, "result": a.result} for a in step.actions]
        final = self.steps_this_turn >= config.MAX_STEPS
        self.history.append({"role": "user", "content": prompts.observations_message(
            json.dumps(observations, ensure_ascii=False, default=str), final)})

    def _call_model(self):
        if self.llm_calls >= config.MAX_LLM_CALLS_PER_SESSION:
            self.audit.record("session_limit_reached")
            raise AgentError("This session has used its model-call budget "
                             f"({config.MAX_LLM_CALLS_PER_SESSION}). Reset the workspace to start over.")
        if not _spend_daily_call():
            self.audit.record("daily_limit_reached")
            raise AgentError("This demo has used its model budget for today. Please try again tomorrow.")
        self.llm_calls += 1
        try:
            result = self.llm.complete(prompts.SYSTEM_PROMPT, self.history)
        except LLMError as e:
            self.audit.record("llm_error", error=str(e))
            raise AgentError(redact(str(e))) from None
        self.audit.record("llm_call", model=result.model, attempts=result.attempts, **result.usage)
        return result
