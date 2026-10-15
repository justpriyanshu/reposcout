"""Chat-model clients: local Ollama, or any OpenAI-compatible API (Groq, Gemini, LM Studio...)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from .config import Settings
from .errors import ProviderError, RepoScoutError

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass
class LLMUsage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "seconds": round(self.seconds, 2),
        }


class LLM(Protocol):
    name: str
    usage: LLMUsage

    def complete(
        self, system: str, user: str, *, json_mode: bool = False, temperature: float = 0.1
    ) -> str: ...


def _post_with_retries(
    client: httpx.Client, url: str, payload: dict, headers: dict, provider: str, attempts: int = 3
) -> httpx.Response:
    delay = 1.5
    for attempt in range(1, attempts + 1):
        try:
            resp = client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            if attempt == attempts:
                raise ProviderError(f"{provider} timed out after {attempts} attempts") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach {provider} at {url}: {exc}") from exc
        else:
            if resp.status_code not in RETRY_STATUSES or attempt == attempts:
                return resp
            retry_after = resp.headers.get("retry-after")
            if retry_after and retry_after.isdigit():
                delay = max(delay, float(retry_after))
        log.warning(
            "%s request failed (attempt %d/%d), retrying in %.1fs", provider, attempt, attempts, delay
        )
        time.sleep(delay)
        delay *= 2
    raise ProviderError(f"{provider} request failed")  # pragma: no cover


class OllamaLLM:
    def __init__(self, model: str, base_url: str, timeout: float = 180, client: httpx.Client | None = None):
        self.model = model
        self.name = f"ollama:{model}"
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout)
        self.usage = LLMUsage()

    def complete(self, system: str, user: str, *, json_mode: bool = False, temperature: float = 0.1) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": 16384},
        }
        if json_mode:
            payload["format"] = "json"
        started = time.perf_counter()
        try:
            resp = _post_with_retries(self.client, f"{self.base_url}/api/chat", payload, {}, "Ollama")
        except ProviderError as exc:
            raise ProviderError(
                f"{exc}. Is Ollama running? Start it with `ollama serve` and pull the model "
                f"with `ollama pull {self.model}`."
            ) from exc
        if resp.status_code == 404:
            raise ProviderError(f"Ollama has no model '{self.model}'. Run `ollama pull {self.model}`.")
        if resp.status_code >= 400:
            raise ProviderError(f"Ollama error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        self.usage.calls += 1
        self.usage.prompt_tokens += int(data.get("prompt_eval_count") or 0)
        self.usage.completion_tokens += int(data.get("eval_count") or 0)
        self.usage.seconds += time.perf_counter() - started
        return data.get("message", {}).get("content", "")


class OpenAICompatibleLLM:
    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        timeout: float = 180,
        client: httpx.Client | None = None,
    ):
        self.model = model
        self.name = f"openai:{model}"
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = client or httpx.Client(timeout=timeout)
        self.usage = LLMUsage()
        self._json_supported = True

    def complete(self, system: str, user: str, *, json_mode: bool = False, temperature: float = 0.1) -> str:
        payload: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
        }
        if json_mode and self._json_supported:
            payload["response_format"] = {"type": "json_object"}
        url = f"{self.base_url}/chat/completions"
        started = time.perf_counter()
        resp = _post_with_retries(self.client, url, payload, self.headers, "LLM API")
        if resp.status_code == 400 and "response_format" in payload:
            # Some OpenAI-compatible servers don't support JSON mode; the prompt still asks for JSON.
            self._json_supported = False
            payload.pop("response_format")
            resp = _post_with_retries(self.client, url, payload, self.headers, "LLM API")
        if resp.status_code in (401, 403):
            raise ProviderError("The LLM API rejected the key. Check REPOSCOUT_OPENAI_API_KEY.")
        if resp.status_code >= 400:
            raise ProviderError(f"LLM API error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        usage = data.get("usage") or {}
        self.usage.calls += 1
        self.usage.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.usage.completion_tokens += int(usage.get("completion_tokens") or 0)
        self.usage.seconds += time.perf_counter() - started
        return data["choices"][0]["message"].get("content") or ""


def make_llm(settings: Settings) -> LLM:
    provider = settings.llm_provider.lower()
    if provider == "ollama":
        return OllamaLLM(settings.llm_model, settings.ollama_url, settings.request_timeout)
    if provider == "openai":
        return OpenAICompatibleLLM(
            settings.llm_model,
            settings.openai_base_url,
            settings.openai_api_key,
            settings.request_timeout,
        )
    raise RepoScoutError(f"Unknown LLM provider '{settings.llm_provider}' (use ollama or openai)")
