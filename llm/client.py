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


class AnthropicClient:
    """Tool-less JSON completions against the Anthropic Messages API.

    Retries transient failures (429, 5xx, connection errors) three times with exponential backoff.
    Never retries other 4xx. A schema validation failure is retried once with the validation error
    echoed back, then raised as LLMValidationError: there is no free-text fallback.
    """

    def __init__(self, api_key: str | None = None, default_model: str | None = None):
        import anthropic

        s = get_settings()
        key = api_key or s.anthropic_api_key
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set; failing closed")
        self._client = anthropic.Anthropic(api_key=key, max_retries=0)
        self.default_model = default_model or s.analyst_model
        self._settings = s

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
            resp = self._call(model=model, system=system_full, messages=messages, max_tokens=max_tokens)
            tokens_in += resp.usage.input_tokens
            tokens_out += resp.usage.output_tokens
            text = "".join(getattr(b, "text", "") for b in resp.content)
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

    def _call(self, **kw):
        import anthropic

        delay = 2.0
        for attempt in range(self._settings.llm_max_retries + 1):
            try:
                return self._client.messages.create(**kw)
            except anthropic.APIStatusError as e:
                if e.status_code in TRANSIENT_STATUS and attempt < self._settings.llm_max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise
            except anthropic.APIConnectionError:
                if attempt < self._settings.llm_max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise
        raise RuntimeError("unreachable")


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
    return AnthropicClient()
