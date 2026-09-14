"""Fetch a source document and store its extracted text. The stored row is untrusted, always.

The content pipeline cites these rows by id. The stage 2 verifier compares claims to the stored
text. Nothing here interprets the content; it is bytes in, text out.
"""
from __future__ import annotations

import hashlib
from typing import Any

from collectors import ratelimit
from collectors.base import CollectorContext
from collectors.html import text_only
from collectors.http import client

MAX_CHARS = 60_000


def collect(ctx: CollectorContext, params: dict[str, Any]) -> None:
    urls: list[str] = params.get("urls") or []
    if not urls:
        ctx.gap("no urls requested", "all")
        return
    with client(transport=params.get("_transport"), follow_redirects=True) as http:
        for u in urls:
            ratelimit.acquire("http_fetch")
            try:
                r = http.get(u)
            except Exception as e:
                ctx.gap(f"fetch failed: {type(e).__name__}: {e}", u)
                ctx.write("raw_fetched_documents", {"url": u, "http_status": None, "content_text": None, "content_hash": None})
                continue
            ctype = r.headers.get("content-type", "")
            text = text_only(r.text) if "html" in ctype else r.text
            text = text[:MAX_CHARS]
            ctx.write("raw_fetched_documents", {
                "url": u, "http_status": r.status_code,
                "content_text": text if r.status_code == 200 else None,
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest() if r.status_code == 200 else None,
                "is_untrusted": True,
            })
            if r.status_code != 200:
                ctx.gap(f"source returned {r.status_code}", u)
