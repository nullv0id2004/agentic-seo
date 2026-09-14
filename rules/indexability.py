from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

NOINDEX_RE = re.compile(r"\bnoindex\b", re.IGNORECASE)


def path_of(url: str) -> str:
    """Path plus query of an absolute or relative URL. Rules match on path, never on host."""
    if url.startswith(("http://", "https://")):
        parts = urlsplit(url)
        return parts.path + (f"?{parts.query}" if parts.query else "")
    return url if url.startswith("/") else "/" + url


def rule_matches(url_pattern: str, url: str) -> bool:
    return re.search(url_pattern, path_of(url)) is not None


def has_noindex(robots_meta: str | None, x_robots_tag: str | None) -> bool:
    return bool((robots_meta and NOINDEX_RE.search(robots_meta)) or (x_robots_tag and NOINDEX_RE.search(x_robots_tag)))


def looks_indexable(status_code: int | None, robots_meta: str | None, x_robots_tag: str | None) -> bool:
    """True when a fetch of this URL returned a page a crawler could index.

    A 200 with no noindex directive is indexable. Anything else (redirect to login, 401, 403, 404,
    noindex header or meta) is not. Unknown status (None: fetch failed) is NOT treated as safe.
    """
    if status_code is None:
        return True  # unknown is unsafe; callers that cannot fetch must record a gap, not a pass
    if status_code != 200:
        return False
    return not has_noindex(robots_meta, x_robots_tag)


@dataclass(frozen=True)
class CriticalViolation:
    rule_key: str
    assertion: str
    url: str
    detail: str


def evaluate_critical_rules(
    rules: list[dict],
    pages: list[dict],
    sitemap_urls: set[str] | None = None,
) -> list[CriticalViolation]:
    """Evaluate every active critical rule against crawled/probed page rows.

    `rules` rows: rule_key, url_pattern, assertion. `pages` rows: url, status_code, robots_meta,
    x_robots_tag, canonical, schema_types, raw_jsonld, visible_price, in_sitemap.
    """
    out: list[CriticalViolation] = []
    sitemap_urls = sitemap_urls or set()
    for rule in rules:
        if not rule.get("active", True):
            continue
        assertion = rule["assertion"]
        base, _, arg = assertion.partition(":")
        for page in pages:
            url = page["url"]
            if not rule_matches(rule["url_pattern"], url):
                continue
            if base == "must_noindex":
                if looks_indexable(page.get("status_code"), page.get("robots_meta"), page.get("x_robots_tag")):
                    out.append(CriticalViolation(rule["rule_key"], assertion, url,
                                                 f"status={page.get('status_code')} robots_meta={page.get('robots_meta')!r} x_robots_tag={page.get('x_robots_tag')!r}"))
            elif base == "must_index":
                if not looks_indexable(page.get("status_code"), page.get("robots_meta"), page.get("x_robots_tag")):
                    out.append(CriticalViolation(rule["rule_key"], assertion, url, "page is not indexable"))
            elif base == "must_not_appear_in_sitemap":
                if page.get("in_sitemap") or url in sitemap_urls or path_of(url) in {path_of(u) for u in sitemap_urls}:
                    out.append(CriticalViolation(rule["rule_key"], assertion, url, "url listed in sitemap"))
            elif base == "schema_type_forbidden":
                if arg in (page.get("schema_types") or []):
                    out.append(CriticalViolation(rule["rule_key"], assertion, url, f"JSON-LD type {arg} present"))
            elif base == "must_canonical_to_parent":
                parent = url.split("?", 1)[0]
                canon = page.get("canonical")
                if not canon or canon.split("?", 1)[0] != parent or "?" in canon:
                    out.append(CriticalViolation(rule["rule_key"], assertion, url, f"canonical={canon!r} expected {parent!r}"))
            elif base == "schema_matches_visible":
                for v in schema_price_mismatches(page):
                    out.append(CriticalViolation(rule["rule_key"], assertion, url, v))
    # sitemap-only violations: a url that is in the sitemap but was not crawled
    for rule in rules:
        if rule.get("active", True) and rule["assertion"] == "must_not_appear_in_sitemap":
            crawled = {p["url"] for p in pages}
            for u in sitemap_urls - crawled:
                if rule_matches(rule["url_pattern"], u):
                    out.append(CriticalViolation(rule["rule_key"], rule["assertion"], u, "url listed in sitemap"))
    return out


def schema_price_mismatches(page: dict) -> list[str]:
    """Compare Offer.price in JSON-LD to the price visible on the page. Returns human-readable mismatches."""
    visible = page.get("visible_price")
    jsonld = page.get("raw_jsonld") or []
    if visible is None or not jsonld:
        return []
    visible_num = _num(visible)
    if visible_num is None:
        return []
    found: list[str] = []
    for node in _walk(jsonld):
        if isinstance(node, dict) and node.get("@type") in ("Offer", "AggregateOffer"):
            price = node.get("price") or node.get("lowPrice")
            n = _num(str(price)) if price is not None else None
            if n is not None and abs(n - visible_num) > 0.005:
                found.append(f"schema price {price} differs from visible price {visible}")
    return found


def _num(s: str) -> float | None:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s or "")
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)
