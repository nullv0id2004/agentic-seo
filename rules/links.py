"""Backlink-gap candidates, shared by the backlink_gap collector (which fetches the linking page of each) and the
link-building analyst (which plans from them). Pure functions, no network.

A candidate links to two or more competitors and not to the project. Left out: the project's own and the
competitors' domains, sites that link to everything, namesakes, anything already on the checklist, and links a
competitor is better off without: a spam score above 10 or a domain rank below 20 (DataForSEO, 0 to 100). Run
6c1d72dd kept 40 sites at the old limits (spam up to 30, any rank) and most were job-listing scrapers and spam.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from rules.domains import GENERIC_DOMAINS, brand_label, host_of, registrable
from rules.keywords import is_branded

MIN_COMPETITORS = 2
SPAM_MAX = 10
MIN_RANK = 20


@dataclass
class Candidate:
    domain: str                                       # registrable
    targets: set[str] = field(default_factory=set)    # competitors it links to, registrable
    hosts: set[str] = field(default_factory=set)      # the hosts the links come from (www. stripped)
    rank: int | None = None
    spam: int | None = None
    ref: Any = None                                   # id of the strongest raw_referring_domains row, when known
    ref_rank: int = -1


def gap_candidates(rows: Iterable[dict[str, Any]], *, excluded: set[str], brand: set[str],
                   min_competitors: int = MIN_COMPETITORS, spam_max: int = SPAM_MAX, min_rank: int = MIN_RANK) -> list[Candidate]:
    """Strongest first: more competitors linked, then higher rank. `excluded` holds registrable domains (own sites,
    competitors, checklist platforms, sites already linking to the project); generic sites are always excluded."""
    excluded = excluded | GENERIC_DOMAINS
    by: dict[str, Candidate] = {}
    for r in rows:
        if r.get("role") != "competitor" or r.get("lost_date"):
            continue
        host = host_of(r.get("domain"))
        key = registrable(host)
        if not key or key in excluded or is_branded(brand_label(key), brand):
            continue
        c = by.setdefault(key, Candidate(domain=key))
        c.targets.add(registrable(r.get("target")))
        c.hosts.add(host)
        rank, spam = r.get("rank"), r.get("spam_score")
        if rank is not None and (c.rank is None or rank > c.rank):
            c.rank = rank
        if spam is not None and (c.spam is None or spam > c.spam):
            c.spam = spam
        if (rank or 0) > c.ref_rank:
            c.ref, c.ref_rank = r.get("id"), rank or 0
    keep = [c for c in by.values() if len(c.targets) >= min_competitors and (c.spam or 0) <= spam_max and (c.rank or 0) >= min_rank]
    keep.sort(key=lambda c: (-len(c.targets), -(c.rank or 0), c.domain))
    return keep


def lookup_plan(candidates: list[Candidate], max_calls: int) -> list[tuple[str, list[Candidate]]]:
    """Which competitor to ask for which candidates' linking pages, in as few calls as possible: each call takes the
    competitor that the most remaining candidates link to (greedy set cover). One linking page per candidate is
    enough to see what kind of site it is."""
    remaining = list(candidates)
    plan: list[tuple[str, list[Candidate]]] = []
    while remaining and len(plan) < max_calls:
        counts: dict[str, int] = {}
        for c in remaining:
            for t in c.targets:
                counts[t] = counts.get(t, 0) + 1
        best = min(counts, key=lambda t: (-counts[t], t))
        group = [c for c in remaining if best in c.targets]
        plan.append((best, group))
        remaining = [c for c in remaining if best not in c.targets]
    return plan
