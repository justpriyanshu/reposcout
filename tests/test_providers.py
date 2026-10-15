import json

import httpx
import pytest

from reposcout.embeddings import OllamaEmbedder, OpenAICompatibleEmbedder
from reposcout.errors import ProviderError
from reposcout.llm import OllamaLLM, OpenAICompatibleLLM


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ollama_chat_request_and_usage():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"message": {"content": "{}"}, "prompt_eval_count": 12, "eval_count": 3}
        )

    llm = OllamaLLM("qwen2.5-coder:7b", "http://ollama:11434", client=mock_client(handler))
    assert llm.complete("sys", "user", json_mode=True) == "{}"
    assert seen["url"] == "http://ollama:11434/api/chat"
    assert seen["body"]["format"] == "json" and seen["body"]["stream"] is False
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert llm.usage.prompt_tokens == 12 and llm.usage.completion_tokens == 3


def test_ollama_missing_model_message():
    llm = OllamaLLM("nope", "http://x", client=mock_client(lambda r: httpx.Response(404, text="not found")))
    with pytest.raises(ProviderError, match="ollama pull nope"):
        llm.complete("s", "u")


def test_ollama_unreachable_message():
    def handler(request):
        raise httpx.ConnectError("refused")

    llm = OllamaLLM("m", "http://x", client=mock_client(handler))
    with pytest.raises(ProviderError, match="ollama serve"):
        llm.complete("s", "u")


def test_openai_compatible_retries_on_rate_limit(monkeypatch):
    monkeypatch.setattr("reposcout.llm.time.sleep", lambda s: None)
    responses = iter(
        [
            httpx.Response(429, headers={"retry-after": "1"}, json={}),
            httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "hi"}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 1},
                },
            ),
        ]
    )
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer key"
        return next(responses)

    llm = OpenAICompatibleLLM("m", "https://api.example.com/v1", "key", client=mock_client(handler))
    assert llm.complete("s", "u") == "hi"
    assert len(calls) == 2 and str(calls[0].url).endswith("/chat/completions")


def test_openai_compatible_falls_back_when_json_mode_unsupported():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": "response_format not supported"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    llm = OpenAICompatibleLLM("m", "https://x/v1", "", client=mock_client(handler))
    assert llm.complete("s", "u", json_mode=True) == "{}"
    assert "response_format" in bodies[0] and "response_format" not in bodies[1]
    llm.complete("s", "u", json_mode=True)
    assert "response_format" not in bodies[2]  # remembered for later calls


def test_openai_compatible_auth_error():
    llm = OpenAICompatibleLLM("m", "https://x/v1", "bad", client=mock_client(lambda r: httpx.Response(401)))
    with pytest.raises(ProviderError, match="rejected the key"):
        llm.complete("s", "u")


def test_ollama_embedder_batches_and_normalises():
    batches = []

    def handler(request):
        body = json.loads(request.content)
        batches.append(len(body["input"]))
        return httpx.Response(200, json={"embeddings": [[3.0, 4.0] for _ in body["input"]]})

    emb = OllamaEmbedder("nomic-embed-text", "http://x", batch_size=2, client=mock_client(handler))
    vectors = emb.embed(["a", "b", "c"])
    assert batches == [2, 1]
    assert vectors.shape == (3, 2) and abs(vectors[0][0] - 0.6) < 1e-6


def test_openai_embedder_orders_by_index():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )

    emb = OpenAICompatibleEmbedder("m", "https://x/v1", "k", client=mock_client(handler))
    vectors = emb.embed(["first", "second"])
    assert vectors[0][0] == 1.0 and vectors[1][1] == 1.0
