import pytest

from reposcout.assistant import NO_RESULTS_ANSWER, RepoAssistant
from reposcout.embeddings import HashingEmbedder
from reposcout.errors import IndexMismatchError
from reposcout.store import IndexStore

from .conftest import FakeLLM


def test_answer_uses_snippets_and_tracks_citations(make_assistant):
    llm = FakeLLM("Tokens are checked in `verify_token` using HMAC [1]. See also [9].")
    answer = make_assistant(llm).ask("How is the session token verified?")
    assert answer.used_llm
    assert answer.sources[0].path == "auth/session.py"
    assert answer.cited == [1]  # [9] does not exist and is ignored
    system, prompt, _ = llm.calls[0]
    assert "Use ONLY the numbered code snippets" in system
    assert "[1] auth/session.py:" in prompt and "def verify_token" in prompt


def test_without_llm_lists_relevant_code(make_assistant):
    answer = make_assistant(None).ask("calculate total with tax")
    assert not answer.used_llm
    assert "billing/invoice.py" in answer.answer


def test_empty_index_returns_no_results_without_calling_llm(tmp_path):
    llm = FakeLLM("should not be called")
    assistant = RepoAssistant(IndexStore(tmp_path / "i.db"), HashingEmbedder(), llm)
    answer = assistant.ask("anything?")
    assert answer.answer == NO_RESULTS_ANSWER and llm.calls == []


def test_context_budget_limits_snippets(indexed):
    store, embedder = indexed
    llm = FakeLLM("answer")
    assistant = RepoAssistant(store, embedder, llm, top_k=8, max_context_chars=300)
    answer = assistant.ask("invoice total renderer cart session")
    assert 1 <= len(answer.sources) <= 2


def test_search_rejects_mismatched_embedder(indexed):
    store, _ = indexed
    with pytest.raises(IndexMismatchError):
        RepoAssistant(store, HashingEmbedder(dim=64), None).search("total")


def test_follow_up_questions_use_the_conversation(indexed):
    store, embedder = indexed
    llm = FakeLLM("It is called from the login view [1].")
    assistant = RepoAssistant(store, embedder, llm, repo_name="octo/shop")
    history = [{"question": "How is the session token verified?", "answer": "By verify_token [1]."}]
    answer = assistant.ask("Where is it called?", history=history)
    _, prompt, _ = llm.calls[0]
    assert "Repository: octo/shop" in prompt
    assert "Earlier in this conversation" in prompt and "By verify_token" in prompt
    assert prompt.rstrip().endswith("Question: Where is it called?")
    # Retrieval used the previous question, so the token code is found for a vague follow-up.
    assert answer.sources[0].path == "auth/session.py"
