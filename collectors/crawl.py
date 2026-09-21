"""Site crawl. Plain HTTP + HTML parsing. Writes raw_crawl_pages and raw_sitemap_urls.

Full mode: BFS from the homepage plus every sitemap url, same-site only, up to max_pages.
Delta mode (post-deploy): params["urls"] only, no discovery.
The crawler never renders JavaScript; Playwright rendering is a separate collector if ever needed.
"""
from __future__ import annotations

from collections import deque
from typing import Any

from collectors import ratelimit
from collectors.base import CollectorContext
from collectors.html import extract, normalise_url, same_site, sitemap_urls
from collectors.http import client
from db.connection import jsonb

DEFAULT_MAX_PAGES = 300


def collect(ctx: CollectorContext, params: dict[str, Any]) -> None:
    project = ctx.project
    base = f"https://{project.primary_domain}"
    max_pages = int(params.get("max_pages", DEFAULT_MAX_PAGES))
    with client(transport=params.get("_transport")) as http:
        in_sitemap = _load_sitemaps(ctx, http, base, params)
        if params.get("urls"):
            queue = deque(normalise_url(u if u.startswith("http") else base + u) for u in params["urls"])
            discover = False
        else:
            queue = deque([normalise_url(base + "/"), *sorted(in_sitemap)])
            discover = True
        seen: set[str] = set()
        while queue and len(seen) < max_pages:
            url = queue.popleft()
            if url in seen or not same_site(url, project.domains):
                continue
            seen.add(url)
            ratelimit.acquire("site_crawl")
            try:
                r = http.get(url)
            except Exception as e:  # network failure on one page is a gap for that page; the crawl goes on
                ctx.gap(f"{type(e).__name__}: {e}", url)
                continue
            row: dict[str, Any] = {"url": url, "status_code": r.status_code, "x_robots_tag": r.headers.get("x-robots-tag"),
                                   "in_sitemap": url in in_sitemap}
            ctype = r.headers.get("content-type", "")
            if r.status_code == 200 and "html" in ctype:
                facts = extract(r.text, url, project.domains)
                row.update({
                    "title": facts.title, "meta_description": facts.meta_description, "h1": facts.h1,
                    "canonical": facts.canonical, "robots_meta": facts.robots_meta, "word_count": facts.word_count,
                    "schema_types": facts.schema_types, "raw_jsonld": jsonb(facts.raw_jsonld),
                    "internal_links_out": facts.internal_links_out, "internal_links": facts.internal_links,
                    "visible_price": facts.visible_price,
                })
                if discover:
                    queue.extend(link for link in facts.internal_links if link not in seen)
            elif 300 <= r.status_code < 400:
                row["canonical"] = r.headers.get("location")
                if discover and row["canonical"]:
                    from urllib.parse import urljoin
                    target = normalise_url(urljoin(url, row["canonical"]))
                    if same_site(target, project.domains) and target not in seen:
                        queue.append(target)
            ctx.write("raw_crawl_pages", row)
        if queue and len(seen) >= max_pages:
            ctx.gap(f"max_pages={max_pages} reached with {len(queue)} urls still queued", "discovery truncated")
    ctx.result.detail["pages"] = len(seen)


def _load_sitemaps(ctx: CollectorContext, http, base: str, params: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    todo = [f"{base}/sitemap.xml", *params.get("sitemaps", [])]
    visited: set[str] = set()
    while todo:
        sm = todo.pop()
        if sm in visited:
            continue
        visited.add(sm)
        ratelimit.acquire("site_crawl")
        try:
            r = http.get(sm)
        except Exception as e:
            ctx.gap(f"sitemap fetch failed: {type(e).__name__}", sm)
            continue
        if r.status_code != 200:
            ctx.gap(f"sitemap returned {r.status_code}", sm)
            continue
        pages, nested = sitemap_urls(r.text)
        todo.extend(nested)
        for u in pages:
            nu = normalise_url(u)
            found.add(nu)
            ctx.write("raw_sitemap_urls", {"sitemap_url": sm, "url": nu})
    return found
