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


def test_parse_wait_understands_rate_limit_headers():
    from reposcout.llm import _parse_wait

    assert _parse_wait("2") == 2.0
    assert _parse_wait("2.5") == 2.5
    assert _parse_wait("7.66s") == pytest.approx(7.66)
    assert _parse_wait("1m2.5s") == pytest.approx(62.5)
    assert _parse_wait("250ms") == pytest.approx(0.25)
    assert _parse_wait("soon") is None
    assert _parse_wait(None) is None


def test_rate_limit_waits_for_reset_header(monkeypatch):
    waits = []
    monkeypatch.setattr("reposcout.llm.time.sleep", waits.append)
    responses = iter(
        [
            httpx.Response(429, headers={"x-ratelimit-reset-tokens": "7.5s"}, json={}),
            httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}),
        ]
    )
    llm = OpenAICompatibleLLM("m", "https://x/v1", "k", client=mock_client(lambda r: next(responses)))
    assert llm.complete("s", "u") == "ok"
    assert waits == [8.0]  # the reset time plus a small margin


def test_persistent_rate_limit_and_oversized_prompt_explain_the_fix(monkeypatch):
    monkeypatch.setattr("reposcout.llm.time.sleep", lambda s: None)
    always_429 = OpenAICompatibleLLM(
        "m", "https://x/v1", "k", client=mock_client(lambda r: httpx.Response(429))
    )
    with pytest.raises(ProviderError, match="REPOSCOUT_REVIEW_WORKERS"):
        always_429.complete("s", "u")
    too_big = OpenAICompatibleLLM("m", "https://x/v1", "k", client=mock_client(lambda r: httpx.Response(413)))
    with pytest.raises(ProviderError, match="MAX_CONTEXT_CHARS"):
        too_big.complete("s", "u")


def test_review_workers_setting_reaches_reviewer(settings):
    from reposcout.services import build_services

    services = build_services(settings.with_overrides(review_workers=1), use_llm=False)
    assert services.reviewer.workers == 1


def test_retry_after_wins_over_far_away_quota_resets():
    from reposcout.llm import _retry_delay

    groq_429 = httpx.Response(
        429,
        headers={
            "retry-after": "3",
            "x-ratelimit-reset-requests": "2m59.56s",  # daily request quota, not the problem
            "x-ratelimit-remaining-requests": "14370",
            "x-ratelimit-reset-tokens": "7.66s",
        },
    )
    assert _retry_delay(groq_429, 1.5) == 3.5

    tokens_used_up = httpx.Response(
        429,
        headers={
            "x-ratelimit-reset-requests": "2m59.56s",
            "x-ratelimit-remaining-requests": "14370",
            "x-ratelimit-reset-tokens": "7.66s",
            "x-ratelimit-remaining-tokens": "0",
        },
    )
    assert _retry_delay(tokens_used_up, 1.5) == pytest.approx(8.16)
    assert _retry_delay(httpx.Response(503), 1.5) == 1.5


def test_jina_embedder_uses_jina_api_and_name():
    from reposcout.embeddings import JinaEmbedder

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        body = json.loads(request.content)
        data = [{"index": i, "embedding": [1.0, float(i)]} for i in range(len(body["input"]))]
        return httpx.Response(200, json={"data": data})

    emb = JinaEmbedder("jina-embeddings-v2-base-code", "jina_key", client=mock_client(handler))
    vectors = emb.embed(["a", "b"])
    assert seen["url"] == "https://api.jina.ai/v1/embeddings" and seen["auth"] == "Bearer jina_key"
    assert emb.name == "jina:jina-embeddings-v2-base-code" and vectors.shape == (2, 2)


def test_embed_model_defaults_follow_the_provider(settings, monkeypatch):
    from reposcout.embeddings import make_embedder
    from reposcout.errors import RepoScoutError

    assert make_embedder(settings.with_overrides(embed_provider="ollama", embed_model="")).name == (
        "ollama:nomic-embed-text"
    )
    jina = settings.with_overrides(embed_provider="jina", embed_model="", jina_api_key="k")
    assert make_embedder(jina).name == "jina:jina-embeddings-v2-base-code"
    with pytest.raises(RepoScoutError, match="JINA_API_KEY"):
        make_embedder(settings.with_overrides(embed_provider="jina", jina_api_key=""))


def test_jina_reranker_reorders_and_failures_keep_fused_order(make_assistant):
    from reposcout.rerank import JinaReranker, apply_reranker

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["body"] = body
        # Score the last document highest.
        n = len(body["documents"])
        return httpx.Response(
            200, json={"results": [{"index": i, "relevance_score": i / n} for i in range(n)]}
        )

    reranker = JinaReranker("jina-reranker-v2-base-multilingual", "k", client=mock_client(handler))
    assistant = make_assistant()
    results = assistant.store.hybrid_search(
        "invoice total", assistant.embedder.embed(["invoice total"])[0], k=5
    )
    reranked = apply_reranker(reranker, "invoice total", results, 3)
    assert [r.chunk_id for r in reranked] == [r.chunk_id for r in reversed(results)][:3]
    assert seen["body"]["query"] == "invoice total" and seen["body"]["documents"][0].startswith("File: ")

    def broken(request):
        return httpx.Response(500, text="down")

    failing = JinaReranker("m", "k", client=mock_client(broken))
    assert apply_reranker(failing, "invoice total", results, 2) == results[:2]


def test_assistant_reranks_a_larger_candidate_pool(make_assistant):
    calls = []

    class Recorder:
        name = "recorder"

        def rerank(self, query, documents):
            calls.append(len(documents))
            return [0.0] * len(documents)

    assistant = make_assistant()
    assistant.reranker, assistant.rerank_candidates = Recorder(), 6
    assert len(assistant.search("calculate total", k=2)) == 2
    assert calls and calls[0] > 2


def test_fallback_llm_moves_to_the_next_model():
    from reposcout.llm import FallbackLLM

    from .conftest import FakeLLM

    class Down(FakeLLM):
        name = "down"

        def complete(self, *args, **kwargs):
            raise ProviderError("rate limited")

    backup = FakeLLM("from backup")
    chain = FallbackLLM([Down(), backup])
    assert chain.complete("s", "u", json_mode=True) == "from backup"
    assert chain.last_used == "fake" and chain.usage.calls == 1
    assert backup.calls[0][2] is True  # json_mode is passed through
    with pytest.raises(ProviderError, match="Every configured model failed"):
        FallbackLLM([Down(), Down()]).complete("s", "u")


def test_llm_fallbacks_setting_builds_a_chain(settings, monkeypatch):
    from reposcout.errors import RepoScoutError
    from reposcout.llm import FallbackLLM, make_llm

    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("REPOSCOUT_OPENROUTER_API_KEY", "o")
    chain = make_llm(
        settings.with_overrides(
            llm_provider="openai",
            llm_model="llama-3.3-70b-versatile",
            openai_api_key="k",
            llm_fallbacks="gemini:gemini-2.5-flash, openrouter:qwen/qwen3-coder:free",
        )
    )
    assert isinstance(chain, FallbackLLM)
    names = [llm.name for llm in chain.llms]
    assert names == [
        "openai:llama-3.3-70b-versatile",
        "gemini:gemini-2.5-flash",
        "openrouter:qwen/qwen3-coder:free",
    ]
    assert chain.llms[1].base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert chain.llms[0].retry_attempts == 2  # fail over quickly instead of waiting out a limit
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(RepoScoutError, match="GEMINI_API_KEY"):
        make_llm(settings.with_overrides(llm_fallbacks="gemini:gemini-2.5-flash"))
    with pytest.raises(RepoScoutError, match="provider:model"):
        make_llm(settings.with_overrides(llm_fallbacks="gemini"))
    assert not isinstance(make_llm(settings.with_overrides(llm_fallbacks="")), FallbackLLM)


def test_fallback_after_rate_limits_uses_the_next_provider(monkeypatch):
    from reposcout.llm import FallbackLLM

    monkeypatch.setattr("reposcout.llm.time.sleep", lambda s: None)

    def limited(request):
        return httpx.Response(429, json={"error": "slow down"})

    def ok(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}], "usage": {}})

    first = OpenAICompatibleLLM("a", "https://a", "k", client=mock_client(limited), retry_attempts=2)
    second = OpenAICompatibleLLM("b", "https://b", "k", client=mock_client(ok))
    assert FallbackLLM([first, second]).complete("s", "u") == "hi"
