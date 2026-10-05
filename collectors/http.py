"""Shared HTTP client for collectors. One place for timeouts, headers and the user agent."""
from __future__ import annotations

from collections.abc import Callable

import httpx

USER_AGENT = "seo-agents-collector/0.1 (+https://worldhire.com)"
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


def client(transport: httpx.BaseTransport | None = None, on_cost: Callable[[float], None] | None = None, **kw) -> httpx.Client:
    """on_cost receives the USD cost a paid API reports in its JSON body (DataForSEO's top-level "cost"),
    once per response, so a collector's spend reaches the run's cost and the monthly budget."""
    headers = {"User-Agent": USER_AGENT, **kw.pop("headers", {})}
    kw.setdefault("follow_redirects", False)
    if on_cost is not None:
        kw["event_hooks"] = {"response": [_cost_hook(on_cost)]}
    return httpx.Client(timeout=DEFAULT_TIMEOUT, headers=headers, transport=transport, **kw)


def _cost_hook(on_cost: Callable[[float], None]) -> Callable[[httpx.Response], None]:
    def hook(response: httpx.Response) -> None:
        response.read()
        try:
            cost = response.json().get("cost")
        except (ValueError, AttributeError):
            return
        if isinstance(cost, int | float) and cost > 0:
            on_cost(float(cost))
    return hook
