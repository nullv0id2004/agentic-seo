"""Deterministic HTML extraction for the crawler and the document fetcher."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit

from selectolax.parser import HTMLParser

PRICE_RE = re.compile(r"(?:₹|\$|€|£|Rs\.?|INR|USD)\s?\d[\d,]*(?:\.\d{1,2})?")


@dataclass
class PageFacts:
    title: str | None = None
    meta_description: str | None = None
    h1: list[str] = field(default_factory=list)
    canonical: str | None = None
    robots_meta: str | None = None
    word_count: int = 0
    schema_types: list[str] = field(default_factory=list)
    raw_jsonld: list = field(default_factory=list)
    internal_links: list[str] = field(default_factory=list)
    internal_links_out: int = 0
    visible_price: str | None = None
    text: str = ""


def normalise_url(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def same_site(url: str, domains: list[str]) -> bool:
    host = urlsplit(url).netloc.lower().split(":")[0]
    return any(host == d.lower() or host == f"www.{d.lower()}" or f"www.{host}" == d.lower() for d in domains)


def extract(html: str, base_url: str, domains: list[str]) -> PageFacts:
    tree = HTMLParser(html)
    facts = PageFacts()
    t = tree.css_first("title")
    facts.title = t.text(strip=True) if t else None
    for m in tree.css("meta"):
        name = (m.attributes.get("name") or "").lower()
        if name == "description":
            facts.meta_description = m.attributes.get("content")
        elif name == "robots":
            facts.robots_meta = m.attributes.get("content")
    facts.h1 = [h.text(strip=True) for h in tree.css("h1")]
    c = tree.css_first('link[rel="canonical"]')
    if c and c.attributes.get("href"):
        facts.canonical = urljoin(base_url, c.attributes["href"])
    for s in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(s.text() or "")
        except json.JSONDecodeError:
            continue
        facts.raw_jsonld.append(data)
        facts.schema_types.extend(_types(data))
    facts.schema_types = sorted(set(facts.schema_types))
    seen: set[str] = set()
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = normalise_url(urljoin(base_url, href))
        if same_site(absolute, domains) and absolute not in seen:
            seen.add(absolute)
            facts.internal_links.append(absolute)
    facts.internal_links_out = len(facts.internal_links)
    for node in tree.css("script, style, noscript, template"):
        node.decompose()
    body = tree.body or tree.root
    facts.text = re.sub(r"\s+", " ", body.text(separator=" ") if body else "").strip()
    facts.word_count = len(facts.text.split()) if facts.text else 0
    price = PRICE_RE.search(facts.text)
    facts.visible_price = price.group(0) if price else None
    return facts


def _types(node) -> list[str]:
    out: list[str] = []
    if isinstance(node, dict):
        t = node.get("@type")
        if isinstance(t, str):
            out.append(t)
        elif isinstance(t, list):
            out.extend(x for x in t if isinstance(x, str))
        for v in node.values():
            out.extend(_types(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(_types(v))
    return out


def text_only(html: str) -> str:
    tree = HTMLParser(html)
    for node in tree.css("script, style, noscript, template, nav, footer, header"):
        node.decompose()
    body = tree.body or tree.root
    return re.sub(r"\s+", " ", body.text(separator=" ") if body else "").strip()


def sitemap_urls(xml: str) -> tuple[list[str], list[str]]:
    """Return (page urls, nested sitemap urls) from a sitemap or sitemap index. Regex on purpose: no XML entity expansion."""
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
    if "<sitemapindex" in xml:
        return [], locs
    return locs, []
