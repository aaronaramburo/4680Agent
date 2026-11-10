"""LLM integration: one place that talks to the Anthropic API.

Handles retry with exponential backoff (honoring the server's retry-after),
maps every SDK exception to a message a user can act on, and checks the stop
reason before anything downstream reads the text.
"""
import random
import time
from dataclasses import dataclass, field

import anthropic

from . import config

# Models that accept the server-side refusal fallback (routes a declined
# request to another model inside the same call).
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(Exception):
    """A failure that retrying will not fix. str(e) is safe to show the user."""


@dataclass
class LLMResult:
    text: str
    content: list            # raw content blocks, replayed unchanged in history
    model: str
    refused: bool = False
    usage: dict = field(default_factory=dict)
    attempts: int = 1


class LLMClient:
    def __init__(self, client=None, model=config.MODEL, effort=config.EFFORT,
                 max_retries=config.LLM_MAX_RETRIES, on_retry=None, sleep=time.sleep):
        # The SDK's own retries are off so that ours are visible in the UI and the audit log.
        self.client = client or anthropic.Anthropic(max_retries=0, timeout=config.LLM_TIMEOUT)
        self.model = model
        self.effort = effort
        self.max_retries = max_retries
        self.on_retry = on_retry or (lambda reason, delay, attempt: None)
        self._sleep = sleep

    def complete(self, system: str, messages: list) -> LLMResult:
        request = dict(
            model=self.model,
            max_tokens=config.MAX_TOKENS,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            output_config={"effort": self.effort},
        )
        if self.model in _FALLBACK_MODELS:
            request.update(betas=[_FALLBACK_BETA], fallbacks="default")

        attempt = 0
        while True:
            attempt += 1
            try:
                response = self.client.beta.messages.create(**request)
                return self._to_result(response, attempt)
            except anthropic.AuthenticationError:
                raise LLMError("The API key was rejected. Check ANTHROPIC_API_KEY.") from None
            except anthropic.PermissionDeniedError as e:
                raise LLMError(f"This API key is not allowed to do that: {e.message}") from None
            except anthropic.NotFoundError:
                raise LLMError(f"Model '{self.model}' is not available to this API key.") from None
            except anthropic.RateLimitError as e:
                reason, wait = "rate limited", _retry_after(e)
            except anthropic.APIStatusError as e:
                if e.status_code < 500:
                    raise LLMError(f"The API rejected the request ({e.status_code}): {e.message}") from None
                reason, wait = f"server error {e.status_code}", None
            except anthropic.APITimeoutError:
                reason, wait = "request timed out", None
            except anthropic.APIConnectionError:
                reason, wait = "network error", None

            if attempt > self.max_retries:
                raise LLMError(f"The model is unavailable ({reason}) after {attempt} attempts. "
                               "Try again in a minute.")
            backoff = config.LLM_BASE_DELAY * 2 ** (attempt - 1) + random.uniform(0, 0.5)
            delay = min(wait if wait is not None else backoff, config.LLM_MAX_DELAY)
            self.on_retry(reason, delay, attempt)
            self._sleep(delay)

    def _to_result(self, response, attempts: int) -> LLMResult:
        text = "".join(b.text for b in response.content if b.type == "text")
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        }
        if response.stop_reason == "max_tokens":
            raise LLMError("The model's response was cut off before it finished. Try a smaller request.")
        return LLMResult(
            text=text,
            content=response.content,
            model=response.model,
            refused=response.stop_reason == "refusal",
            usage=usage,
            attempts=attempts,
        )


def _retry_after(error):
    try:
        return float(error.response.headers.get("retry-after"))
    except (TypeError, ValueError, AttributeError):
        return None
