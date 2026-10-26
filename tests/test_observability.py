from contextlib import contextmanager

import pytest

from reposcout.errors import ProviderError
from reposcout.observability import TracedLLM, init_sentry, scrub_event, traced

from .conftest import FakeLLM


class FakeLangfuse:
    def __init__(self):
        self.observations = []

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        record = {"start": kwargs, "updates": []}
        self.observations.append(record)

        class Generation:
            def update(self, **fields):
                record["updates"].append(fields)

        yield Generation()


def test_traced_llm_records_prompt_answer_and_tokens():
    class Counting(FakeLLM):
        def complete(self, system, user, **kwargs):
            self.usage.prompt_tokens += 12
            self.usage.completion_tokens += 3
            return super().complete(system, user, **kwargs)

    client = FakeLangfuse()
    llm = traced(Counting("answer"), client)
    assert isinstance(llm, TracedLLM)
    assert llm.complete("sys", "question", json_mode=True) == "answer"
    (obs,) = client.observations
    assert obs["start"]["as_type"] == "generation"
    assert obs["start"]["input"][1] == {"role": "user", "content": "question"}
    assert obs["start"]["model_parameters"]["json_mode"] is True
    assert obs["updates"][0]["output"] == "answer"
    assert obs["updates"][0]["usage_details"] == {"input": 12, "output": 3}
    assert llm.usage.calls == 1 and llm.responder == "answer"  # attributes pass through


def test_traced_llm_marks_failures():
    class Down(FakeLLM):
        def complete(self, *args, **kwargs):
            raise ProviderError("rate limited")

    client = FakeLangfuse()
    with pytest.raises(ProviderError):
        traced(Down(), client).complete("s", "u")
    assert client.observations[0]["updates"][0]["level"] == "ERROR"


def test_tracing_is_off_without_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    llm = FakeLLM()
    assert traced(llm) is llm and traced(None) is None


def test_sentry_events_never_carry_keys_or_bodies():
    event = {
        "request": {
            "headers": {"X-LLM-Key": "gsk_secret", "Authorization": "Bearer t", "Accept": "json"},
            "data": {"question": "private code"},
        }
    }
    scrubbed = scrub_event(event)
    assert scrubbed["request"]["headers"] == {
        "X-LLM-Key": "[Filtered]",
        "Authorization": "[Filtered]",
        "Accept": "json",
    }
    assert "data" not in scrubbed["request"]


def test_sentry_is_off_without_a_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert init_sentry("test") is False
