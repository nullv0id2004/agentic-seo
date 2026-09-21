"""Header probe: the collector that mechanically proves the confidentiality guarantee.

For every must_noindex / must_not_appear_in_sitemap critical rule, probe the project's configured
critical paths (plus any urls passed in) and record exactly what a crawler would see: status code,
X-Robots-Tag, robots meta. It then evaluates the rules deterministically. A violation is returned in
result.detail["violations"] and the orchestrator treats it as a page-the-owner event.

No LLM is involved at any point. The rule is a regex and the verdict is a boolean.
"""
from __future__ import annotations

from typing import Any

from collectors import ratelimit
from collectors.base import CollectorContext
from collectors.html import extract, normalise_url, sitemap_urls
from collectors.http import client
from rules.indexability import evaluate_critical_rules, rule_matches

PROBE_ASSERTIONS = {"must_noindex", "must_index", "must_not_appear_in_sitemap"}


def collect(ctx: CollectorContext, params: dict[str, Any]) -> None:
    project = ctx.project
    base = f"https://{project.primary_domain}"
    rules = ctx.read("select rule_key, url_pattern, assertion, active from critical_rules where project_id = %(project_id)s and active")
    paths: list[str] = list(params.get("paths") or [])
    for u in params.get("urls") or []:
        paths.append(u)
    probe_urls = [normalise_url(p if p.startswith("http") else base + p) for p in paths]
    pages: list[dict[str, Any]] = []
    smap: set[str] = set()
    with client(transport=params.get("_transport")) as http:
        # sitemap membership is part of the guarantee
        for sm in [f"{base}/sitemap.xml", *params.get("sitemaps", [])]:
            ratelimit.acquire("header_probe")
            try:
                r = http.get(sm)
                if r.status_code == 200:
                    urls, nested = sitemap_urls(r.text)
                    smap.update(normalise_url(u) for u in urls)
                    for n in nested:
                        rn = http.get(n)
                        if rn.status_code == 200:
                            smap.update(normalise_url(u) for u in sitemap_urls(rn.text)[0])
                else:
                    ctx.gap(f"sitemap returned {r.status_code}", sm)
            except Exception as e:
                ctx.gap(f"sitemap fetch failed: {type(e).__name__}", sm)
        for url in probe_urls:
            if not any(rule_matches(r["url_pattern"], url) for r in rules if r["assertion"] in PROBE_ASSERTIONS):
                continue
            ratelimit.acquire("header_probe")
            try:
                # A crawler follows redirects, so the probe must judge the final response. Same-site hops
                # only: a redirect off the property (to a login provider, say) ends the chain as not indexable.
                r, hops = _follow(http, url, project.domains)
                robots_meta = None
                if r.status_code == 200 and "html" in r.headers.get("content-type", ""):
                    robots_meta = extract(r.text, str(r.url), project.domains).robots_meta
            except Exception as e:
                # Cannot prove the guarantee for this url: that is a gap AND a violation to surface, never a pass.
                ctx.gap(f"probe failed: {type(e).__name__}: {e}", url)
                pages.append({"url": url, "status_code": None, "robots_meta": None, "x_robots_tag": None, "in_sitemap": url in smap})
                continue
            row = {"url": url, "status_code": r.status_code, "x_robots_tag": r.headers.get("x-robots-tag"),
                   "robots_meta": robots_meta, "in_sitemap": url in smap,
                   "canonical": (str(r.url) if hops else None) or (r.headers.get("location") if 300 <= r.status_code < 400 else None)}
            ctx.write("raw_crawl_pages", row)
            pages.append(row)
    violations = evaluate_critical_rules(rules, pages, smap)
    ctx.result.detail["violations"] = [v.__dict__ for v in violations]
    ctx.result.detail["probed"] = len(pages)
    if violations:
        ctx.scope.audit(f"collector:{ctx.name}", "critical_rule_violation",
                        {"violations": [v.__dict__ for v in violations]}, run_id=ctx.run_id)


MAX_HOPS = 5


def _follow(http, url: str, domains: list[str]):
    """GET with same-site redirects followed by hand. Returns (final response, hops taken).

    A redirect to another host is returned as-is (3xx): not indexable on this property, and never fetched.
    """
    from collectors.html import same_site

    hops = 0
    current = url
    while True:
        r = http.get(current)
        if not (300 <= r.status_code < 400) or hops >= MAX_HOPS:
            return r, hops
        location = r.headers.get("location")
        if not location:
            return r, hops
        from urllib.parse import urljoin
        target = normalise_url(urljoin(current, location))
        if not same_site(target, domains):
            return r, hops
        current = target
        hops += 1
