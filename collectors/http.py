"""Shared HTTP client for collectors. One place for timeouts, headers and the user agent."""
from __future__ import annotations

import httpx

USER_AGENT = "seo-agents-collector/0.1 (+https://worldhire.com)"
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


def client(transport: httpx.BaseTransport | None = None, **kw) -> httpx.Client:
    headers = {"User-Agent": USER_AGENT, **kw.pop("headers", {})}
    kw.setdefault("follow_redirects", False)
    return httpx.Client(timeout=DEFAULT_TIMEOUT, headers=headers, transport=transport, **kw)
