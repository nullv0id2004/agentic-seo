from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from config.settings import get_settings


@dataclass
class LLMResult:
    parsed: BaseModel
    raw: dict[str, Any]
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    retries: int = 0


class LLMValidationError(RuntimeError):
    """The model failed to produce output matching the contract after the allowed retries."""


class LLMClient(Protocol):
    def complete_json(self, *, agent: str, system: str, user: str, schema: type[BaseModel],
                      model: str | None = None, max_tokens: int = 4096) -> LLMResult: ...


TRANSIENT_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


class _JsonClient:
    """Shared validation loop. Subclasses implement _generate(system, messages, model, max_tokens)
    returning (text, tokens_in, tokens_out) with their own transient-error retry policy.

    Tool-less by construction: nothing here accepts or forwards a tools argument. A schema validation
    failure is retried once with the validation error echoed back, then raised as LLMValidationError:
    there is no free-text fallback.
    """

    default_model: str
    _settings: Any

    def _generate(self, *, system: str, messages: list[dict[str, str]], model: str, max_tokens: int) -> tuple[str, int, int]:
        raise NotImplementedError

    def complete_json(self, *, agent: str, system: str, user: str, schema: type[BaseModel],
                      model: str | None = None, max_tokens: int = 4096) -> LLMResult:
        model = model or self.default_model
        messages = [{"role": "user", "content": user}]
        system_full = (
            system
            + "\n\nRespond with a single JSON object and nothing else. It must validate against this JSON schema:\n"
            + json.dumps(schema.model_json_schema(), separators=(",", ":"))
        )
        tokens_in = tokens_out = 0
        retries = 0
        last_err: Exception | None = None
        for validation_attempt in range(2):
            text, t_in, t_out = self._generate(system=system_full, messages=messages, model=model, max_tokens=max_tokens)
            tokens_in += t_in
            tokens_out += t_out
            try:
                data = _extract_json(text)
                parsed = schema.model_validate(data)
                pin, pout = self._settings.price_for(model)
                cost = (tokens_in * pin + tokens_out * pout) / 1_000_000
                return LLMResult(parsed=parsed, raw=data, model=model, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost, retries=retries)
            except (ValueError, ValidationError) as e:
                last_err = e
                retries += 1
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": f"That did not validate: {str(e)[:1500]}. Return only the corrected JSON object."},
                ]
                if validation_attempt == 1:
                    break
        raise LLMValidationError(f"{agent}: output did not validate after retry: {last_err}")

    def _with_retries(self, fn, status_error, connection_error):
        """Transient failures (429, 5xx, connection errors) retry three times with exponential backoff. Other 4xx never."""
        delay = 2.0
        for attempt in range(self._settings.llm_max_retries + 1):
            try:
                return fn()
            except status_error as e:
                if getattr(e, "status_code", None) in TRANSIENT_STATUS and attempt < self._settings.llm_max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise
            except connection_error:
                if attempt < self._settings.llm_max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise
        raise RuntimeError("unreachable")


class AnthropicClient(_JsonClient):
    """Anthropic Messages API backend."""

    def __init__(self, api_key: str | None = None, default_model: str | None = None):
        import anthropic

        s = get_settings()
        key = api_key or s.anthropic_api_key
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set; failing closed")
        self._client = anthropic.Anthropic(api_key=key, max_retries=0)
        self.default_model = default_model or s.analyst_model
        self._settings = s

    def _generate(self, *, system, messages, model, max_tokens):
        import anthropic

        resp = self._with_retries(lambda: self._client.messages.create(model=model, system=system, messages=messages, max_tokens=max_tokens),
                                  anthropic.APIStatusError, anthropic.APIConnectionError)
        return "".join(getattr(b, "text", "") for b in resp.content), resp.usage.input_tokens, resp.usage.output_tokens


class OpenAIClient(_JsonClient):
    """OpenAI Chat Completions backend (also any OpenAI-compatible endpoint via OPENAI_BASE_URL)."""

    def __init__(self, api_key: str | None = None, default_model: str | None = None):
        import openai

        s = get_settings()
        key = api_key or s.openai_api_key
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set; failing closed")
        self._client = openai.OpenAI(api_key=key, base_url=s.openai_base_url, max_retries=0)
        self.default_model = default_model or s.analyst_model
        self._settings = s

    def _generate(self, *, system, messages, model, max_tokens):
        import openai

        chat = [{"role": "system", "content": system}, *messages]
        resp = self._with_retries(
            lambda: self._client.chat.completions.create(model=model, messages=chat, max_completion_tokens=max_tokens,
                                                         response_format={"type": "json_object"}),
            openai.APIStatusError, openai.APIConnectionError)
        usage = resp.usage
        return resp.choices[0].message.content or "", (usage.prompt_tokens if usage else 0), (usage.completion_tokens if usage else 0)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError("no JSON object in model output") from None
        return json.loads(m.group(0))


def build_client() -> LLMClient:
    provider = get_settings().llm_provider
    if provider == "openai":
        return OpenAIClient()
    if provider == "anthropic":
        return AnthropicClient()
    raise RuntimeError(f"unknown SEO_LLM_PROVIDER {provider!r}; failing closed")
