import json

import pytest
from pydantic import BaseModel

from config.settings import get_settings
from llm.client import LLMValidationError, OpenAIClient, _JsonClient, build_client


class Out(BaseModel):
    answer: int


class Scripted(_JsonClient):
    def __init__(self, replies):
        self.replies = list(replies)
        self.default_model = "m"
        self._settings = get_settings()
        self.calls = []

    def _generate(self, *, system, messages, model, max_tokens):
        self.calls.append(messages)
        return self.replies.pop(0), 10, 5


def test_validation_retry_then_success():
    c = Scripted(['{"answer": "nope"}', '```json\n{"answer": 3}\n```'])
    r = c.complete_json(agent="t", system="s", user="u", schema=Out)
    assert r.parsed.answer == 3 and r.retries == 1 and r.tokens_in == 20
    assert "did not validate" in c.calls[1][-1]["content"]


def test_validation_hard_fails_after_retry():
    c = Scripted(["not json", "still not json"])
    with pytest.raises(LLMValidationError):
        c.complete_json(agent="t", system="s", user="u", schema=Out)


def test_provider_selection(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()
    s = get_settings()
    assert s.llm_provider == "openai" and s.analyst_model == "gpt-4o-mini"
    assert isinstance(build_client(), OpenAIClient)
    monkeypatch.setenv("SEO_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("OPENAI_API_KEY")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_client()
    get_settings.cache_clear()


def test_openai_backend_is_tool_less():
    import inspect

    src = inspect.getsource(OpenAIClient._generate)
    assert "tools" not in src and "json_object" in src and json is not None
