"""Optional monitoring, switched on by environment variables (free tiers are enough):

* **Sentry** (``SENTRY_DSN``): unhandled errors with stack traces, plus request timings
  when ``SENTRY_TRACES_SAMPLE_RATE`` is above 0.
* **Langfuse** (``LANGFUSE_PUBLIC_KEY`` + ``LANGFUSE_SECRET_KEY``, optional
  ``LANGFUSE_BASE_URL``): every LLM call as a trace with its prompt, answer, model, token
  counts and latency, so bad answers can be looked at afterwards. The SDK is built on
  OpenTelemetry. Traces contain prompts, which include code and questions; in the public
  app they are only recorded when ``REPOSCOUT_TRACE_PUBLIC=1``.

Both need the ``observability`` extra; without it (or without the variables) nothing is
sent anywhere.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from . import __version__
from .llm import LLM, LLMUsage

log = logging.getLogger(__name__)

# Never sent to Sentry: visitors' API keys, GitHub signatures, cookies.
SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-llm-key", "x-hub-signature-256", "x-api-key"}


def scrub_event(event: Any, hint: Any = None) -> Any:
    """Remove secrets and request bodies (questions, code) from a Sentry event."""
    request = event.get("request")
    if isinstance(request, dict):
        headers = request.get("headers")
        if isinstance(headers, dict):
            for name in list(headers):
                if name.lower() in SENSITIVE_HEADERS:
                    headers[name] = "[Filtered]"
        request.pop("data", None)
        request.pop("cookies", None)
    return event


def init_sentry(component: str) -> bool:
    """Start Sentry if ``SENTRY_DSN`` is set. Returns True when it is running."""
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        log.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed (pip install 'reposcout[observability]')"
        )
        return False
    try:
        rate = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE") or 0)
    except ValueError:
        rate = 0.0
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
        release=f"reposcout@{__version__}",
        traces_sample_rate=rate,
        send_default_pii=False,
        before_send=scrub_event,
        before_send_transaction=scrub_event,
    )
    sentry_sdk.set_tag("component", component)
    return True


class TracedLLM:
    """Wraps an LLM so each call is recorded as a Langfuse generation. Everything else
    (``usage``, ``model``, ``headers``...) is the wrapped client's."""

    def __init__(self, llm: LLM, client):
        self.llm = llm
        self.client = client
        self.name = llm.name

    @property
    def usage(self) -> LLMUsage:
        return self.llm.usage

    def __getattr__(self, name: str):
        return getattr(self.llm, name)

    def complete(self, system: str, user: str, *, json_mode: bool = False, temperature: float = 0.1) -> str:
        before = self.llm.usage
        prompt_before, completion_before = before.prompt_tokens, before.completion_tokens
        with self.client.start_as_current_observation(
            name="reposcout.llm",
            as_type="generation",
            model=getattr(self.llm, "model", self.llm.name),
            input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            model_parameters={"temperature": temperature, "json_mode": json_mode},
            metadata={"client": self.llm.name},
        ) as generation:
            try:
                text = self.llm.complete(system, user, json_mode=json_mode, temperature=temperature)
            except Exception as exc:
                generation.update(level="ERROR", status_message=str(exc)[:500])
                raise
            after = self.llm.usage
            generation.update(
                output=text,
                usage_details={
                    "input": after.prompt_tokens - prompt_before,
                    "output": after.completion_tokens - completion_before,
                },
                metadata={"served_by": getattr(self.llm, "last_used", None) or self.llm.name},
            )
            return text


_client = None
_client_lock = threading.Lock()


def langfuse_client():
    """A shared Langfuse client when its keys are set and the SDK is installed, else None."""
    global _client
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return None
    with _client_lock:
        if _client is None:
            try:
                from langfuse import Langfuse
            except ImportError:
                log.warning("LANGFUSE_* keys are set but langfuse is not installed")
                return None
            _client = Langfuse(release=__version__)
        return _client


def traced(llm: LLM | None, client=None) -> LLM | None:
    """``llm`` wrapped for tracing when Langfuse is configured; otherwise ``llm`` itself."""
    if llm is None:
        return None
    client = client or langfuse_client()
    return TracedLLM(llm, client) if client is not None else llm
