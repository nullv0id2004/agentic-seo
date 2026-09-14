"""A deterministic stand-in for tests and evals. Responses are functions of the request, never of the network."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from llm.client import LLMResult, LLMValidationError

Responder = Callable[[str, str, type[BaseModel]], dict[str, Any] | BaseModel]


class FakeLLM:
    def __init__(self, responders: dict[str, Responder] | None = None, default: Responder | None = None,
                 tokens: tuple[int, int] = (1200, 400), model: str = "fake-model", cost_per_call: float = 0.01):
        self.responders = responders or {}
        self.default = default
        self.tokens = tokens
        self.model = model
        self.cost_per_call = cost_per_call
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, *, agent: str, system: str, user: str, schema: type[BaseModel],
                      model: str | None = None, max_tokens: int = 4096) -> LLMResult:
        self.calls.append({"agent": agent, "system": system, "user": user, "schema": schema.__name__})
        responder = self.responders.get(agent) or self.default
        if responder is None:
            raise LLMValidationError(f"FakeLLM has no responder for {agent}")
        out = responder(system, user, schema)
        parsed = out if isinstance(out, BaseModel) else schema.model_validate(out)
        return LLMResult(parsed=parsed, raw=parsed.model_dump(mode="json"), model=model or self.model,
                         tokens_in=self.tokens[0], tokens_out=self.tokens[1], cost_usd=self.cost_per_call)
