"""linkbuilding_analyst. Reads raw_backlink_summary, raw_referring_domains, raw_anchors, raw_crawl_pages, link_targets,
keywords, brand_rules. Emits a link plan: the site's authority phase, the three tactics that fit it, the profiles and
directories still to do, prospects from the competitor backlink gap, anchor text safety and link velocity.

The method is a link-building playbook (phase first, tactics that fit the phase, foundation before outreach), but
every judgement about the site is made in code from collected rows:
  - phase: the lower median of what the rows show (domain rank, age of the first link, indexable pages, profiles
    listed), so one strong signal does not skip the foundation work;
  - checklist: a link_targets row is done when the site's Organization schema lists it in sameAs, or, for a
    directory or review site, when that domain already links to the site;
  - prospects: domains linking to two or more competitors and not to the site, without spam, namesakes, sites that
    link to everything, or the competitors themselves;
  - anchors and velocity: shares and monthly counts computed from raw_anchors and raw_referring_domains.
The model is asked for two things only: what kind of site each prospect is (with one line on how to earn a link
there; no outreach emails) and the profile descriptions, from the facts given. Its text is checked in code: numbers
it was not given, an over-long description or a brand rule it breaks drop that text, not the plan.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from analysts.base import AnalystContext, AnalystInput
from contracts.artifacts import (
    AnchorShare,
    ChecklistItem,
    CompetitorAuthority,
    LinkFlag,
    LinkPlanReport,
    MonthCount,
    PhaseSignal,
    ProfileCopy,
    Prospect,
    TacticOut,
    Velocity,
)
from gate.stage1_rules import blocking_brand_hit
from rules.domains import GENERIC_DOMAINS, brand_label, host_of, registrable
from rules.indexability import indexable_html
from rules.keywords import brand_tokens, is_branded

NAME = "linkbuilding"

PHASES = ("foundation", "growth", "authority")
SPAM_MAX = 30              # DataForSEO backlinks_spam_score above this: not a link worth copying
MIN_COMPETITORS = 2        # a site linking to two or more competitors already links to sites like this one
PROSPECT_CAP = 40
ANCHOR_MIN_LINKS = 10      # below this an anchor distribution says nothing
SPIKE = 50                 # new referring domains in one month that look unnatural
MONTHS = 12

COPY_LIMITS = {"short": 50, "medium": 150, "long": 300}

# Anchor share ranges a natural profile stays within; exact match above its ceiling is a penalty risk.
SAFE_ANCHORS: dict[str, tuple[int, int]] = {"branded": (40, 50), "naked_url": (15, 20), "generic": (15, 20),
                                           "partial_match": (10, 15), "exact_match": (0, 5)}
GENERIC_ANCHORS = frozenset({"click here", "here", "read more", "learn more", "more", "website", "this website", "the website",
                             "visit", "visit website", "visit site", "link", "this link", "this", "source", "view", "see more",
                             "home", "homepage", "official website", "official site", "check it out", "this article", "go"})
_URLISH = re.compile(r"^(https?://)?(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?$")

# Velocity guideline by month of the link programme (counted from the first link found).
VELOCITY = ((1, "15 to 25 new referring domains, mostly profiles and directories"), (3, "5 to 10 quality links a month"),
            (6, "8 to 15 links a month"), (10_000, "10 to 30 links a month"))

CATEGORY_ORDER = {"directory": 0, "resource_page": 1, "forum": 2, "blog": 3, "news": 4, "other": 5, None: 6}


@dataclass(frozen=True)
class Tactic:
    name: str
    effort: str
    typical: str
    why: str
    first_action: str


# The playbook's tactics in its order of phase. Effort and typical results are playbook guidance, shown as such.
TACTICS: dict[str, Tactic] = {
    "entity_stacking": Tactic(
        "Brand profiles (entity stacking)", "6 to 10 hours spread over two or three weeks, then about an hour a month",
        "20 to 30 referring domains in the first month, mostly nofollow but from trusted platforms", "", ""),
    "citations": Tactic(
        "Directories and review sites", "3 to 5 hours once, then 30 minutes a quarter to keep listings current",
        "10 to 20 referring domains in the first two weeks", "", ""),
    "competitor_gap": Tactic(
        "Competitor backlink gap", "2 to 3 hours to sort the list, then 3 to 5 hours a month",
        "5 to 15 referring domains in the first month, listings and directories first", "", ""),
    "guest_posting": Tactic(
        "Guest articles", "4 to 6 hours per published article", "one contextual link per article, worth far more than a profile link",
        "Links inside articles on relevant sites with real traffic carry far more weight than profile or footer links.",
        "Pick three sites from the gap list that publish articles in your field and pitch each one a specific topic you can write with authority."),
    "resource_pages": Tactic(
        "Resource pages", "an hour or two per page found", "a permanent link from a page that exists to link out",
        "Pages that list useful sites for your audience link out by design; a page that fills a gap on such a list is an easy yes.",
        ""),
    "skyscraper": Tactic(
        "Better versions of the most linked content", "a week per piece, including the outreach", "links from every site that linked to the original and accepts the better one",
        "The pages your competitors earn the most links with show what your field links to; a clearly better version gives each of those sites a reason to link to you.",
        "Find the most linked page among your competitors on your main topic, publish a more complete and current version, and tell the sites that linked to the original."),
    "partnerships": Tactic(
        "Partnerships and integration pages", "a few hours per partner", "permanent links from sites that share your audience",
        "Integration pages, joint research and co-written guides give lasting links from sites your audience already trusts.",
        "List the products your users already use alongside yours and propose a joint guide or an integration page to one of them."),
    "podcasts": Tactic(
        "Podcast guesting", "2 to 3 hours per episode", "a link from each episode's show notes",
        "Niche shows with a modest, active audience book guests readily, and their show notes usually link to the guest's site.",
        "Shortlist niche podcasts in your field and offer each host one specific story only you can tell."),
}

ORDER = {
    "foundation": ("entity_stacking", "citations", "competitor_gap", "guest_posting"),
    "growth": ("competitor_gap", "guest_posting", "resource_pages", "skyscraper"),
    "authority": ("partnerships", "podcasts", "skyscraper", "competitor_gap"),
}

SYSTEM = """You help one web property plan how to earn links.

Part 1: each domain listed already links to two or more of the property's competitors but not to the property.
From the domain and the competitors it links to, say what kind of site it most likely is: directory (a listing or
review site anyone in the field can be listed on), resource_page (a page of useful links for an audience), blog,
news, forum or other. Then write one plain sentence, under 30 words, on how the property could earn a link there.
No email drafts, no numbers, no paid links, no promises.

Part 2: write three descriptions of the property for its profiles and directory listings: short (at most 50 words),
medium (at most 150 words) and long (at most 300 words). Use the brand name exactly as given, only the facts given,
and no number that is not in the facts. Plain sentences, no hype, no exclamation marks."""


class _Kind(BaseModel):
    model_config = ConfigDict(extra="ignore")
    domain: str
    category: str | None = None
    approach: str | None = None


class _Copy(BaseModel):
    model_config = ConfigDict(extra="ignore")
    short: str | None = None
    medium: str | None = None
    long: str | None = None


class _Reply(BaseModel):
    model_config = ConfigDict(extra="ignore")
    prospects: list[_Kind] = Field(default_factory=list)
    profile_copy: _Copy | None = None


# ---------- phase ----------

def _vote(value: float | None, cuts: tuple[float, float]) -> str | None:
    if value is None:
        return None
    return PHASES[0] if value <= cuts[0] else PHASES[1] if value <= cuts[1] else PHASES[2]


def phase_of(signals: list[PhaseSignal]) -> str | None:
    """The lower median of the signals that vote: a high rank alone does not move a site with no profiles and no
    content past the foundation work."""
    votes = sorted(PHASES.index(s.points_to) for s in signals if s.points_to)
    return PHASES[votes[(len(votes) - 1) // 2]] if votes else None


def phase_signals(summary: dict | None, pages: list[dict], listed: int, total_targets: int, page_ref: Any, today: date) -> list[PhaseSignal]:
    out: list[PhaseSignal] = []
    if summary:
        rank = summary.get("rank")
        out.append(PhaseSignal(evidence_ref=summary["id"], name="domain_rank", value=rank, points_to=_vote(rank, (15, 40)),
                               detail=f"domain rank {rank} of 100 (foundation up to 15, growth up to 40)" if rank is not None else "domain rank not reported"))
        seen = summary.get("first_seen")
        if isinstance(seen, datetime):
            years = round(max(0, (today - seen.date()).days) / 365.25, 1)
            out.append(PhaseSignal(evidence_ref=summary["id"], name="site_age_years", value=years, points_to=_vote(years, (0.99, 2.99)),
                                   detail=f"first link to the site found on {seen.date().isoformat()}"))
        rds = summary.get("referring_domains")
        if rds is not None:
            out.append(PhaseSignal(evidence_ref=summary["id"], name="referring_domains", value=rds, points_to=None,
                                   detail=f"{rds} referring domains"))
    if page_ref:
        n = len({p["url"] for p in pages if indexable_html(p)})
        out.append(PhaseSignal(evidence_ref=page_ref, name="indexable_pages", value=n, points_to="foundation" if n < 20 else "growth",
                               detail=f"{n} indexable pages in the latest crawl (thin under 20)"))
        if total_targets:
            out.append(PhaseSignal(evidence_ref=page_ref, name="profiles_listed", value=listed, points_to="foundation" if listed < 5 else "growth",
                                   detail=f"{listed} of {total_targets} brand profiles and directories done"))
    return out


# ---------- checklist ----------

def same_as_hosts(pages: list[dict]) -> dict[str, Any]:
    """registrable domain -> id of the crawl row whose JSON-LD lists it in sameAs."""
    found: dict[str, Any] = {}

    def walk(node: Any, pid: Any) -> None:
        if isinstance(node, dict):
            same = node.get("sameAs")
            for u in ([same] if isinstance(same, str) else same if isinstance(same, list) else []):
                if isinstance(u, str) and u.strip():
                    found.setdefault(registrable(u), pid)
            for v in node.values():
                walk(v, pid)
        elif isinstance(node, list):
            for v in node:
                walk(v, pid)

    for p in pages:
        if p.get("status_code") == 200:
            walk(p.get("raw_jsonld"), p["id"])
    return found


def checklist(targets: list[dict], same_as: dict[str, Any], linking: dict[str, Any], fallback_ref: Any, brand: str, site: str) -> list[ChecklistItem]:
    out = []
    for t in sorted((t for t in targets if t.get("active", True)), key=lambda t: (t.get("priority") or 100, t["platform"].lower())):
        key = registrable(t["domain"])
        in_same_as, links = key in same_as, key in linking
        done = in_same_as if t["kind"] == "profile" else (in_same_as or links)
        if done and t["kind"] == "profile":
            action = "Done: keep it current and identical to the other profiles."
        elif done:
            action = "Listed: keep the listing current and identical to the profiles."
        elif t["kind"] == "profile" and links:
            action = (f"{t['platform']} already links to the site. If that is your profile, check it uses the name {brand}, "
                      "then add its address to the Organization schema's sameAs.")
        elif t["kind"] == "profile":
            action = (f"Create the {t['platform']} profile under the exact name {brand}, fill in every field, link it to {site}, "
                      "post once, then add its address to the Organization schema's sameAs.")
        else:
            action = f"List {brand} on {t['platform']} with the same name, logo and description as the profiles, linking to {site}."
        if t.get("note") and not done:
            action = f"{action} {t['note']}"
        ref = linking.get(key) or same_as.get(key) or fallback_ref
        out.append(ChecklistItem(evidence_ref=ref, kind=t["kind"], platform=t["platform"], url=f"https://{t['domain']}/",
                                 status="done" if done else "todo", in_same_as=in_same_as, links_to_you=links,
                                 priority=t.get("priority") or 100, action=action))
    return out


# ---------- prospects ----------

def prospects(rds: list[dict], own_hosts: set[str], competitors: set[str], target_domains: set[str], linking: set[str], brand: set[str]) -> list[Prospect]:
    excluded = own_hosts | competitors | GENERIC_DOMAINS | target_domains | linking
    by: dict[str, dict[str, Any]] = {}
    for r in rds:
        if r.get("role") != "competitor" or r.get("lost_date"):
            continue
        key = registrable(r.get("domain"))
        if not key or key in excluded or is_branded(brand_label(key), brand):
            continue
        g = by.setdefault(key, {"targets": set(), "rank": None, "spam": None, "ref": None, "ref_rank": -1})
        g["targets"].add(registrable(r["target"]))
        rank, spam = r.get("rank"), r.get("spam_score")
        if rank is not None and (g["rank"] is None or rank > g["rank"]):
            g["rank"] = rank
        if spam is not None and (g["spam"] is None or spam > g["spam"]):
            g["spam"] = spam
        if (rank or 0) > g["ref_rank"]:
            g["ref"], g["ref_rank"] = r["id"], rank or 0
    keep = [(k, g) for k, g in by.items() if len(g["targets"]) >= MIN_COMPETITORS and (g["spam"] or 0) <= SPAM_MAX]
    keep.sort(key=lambda kg: (-len(kg[1]["targets"]), -(kg[1]["rank"] or 0), kg[0]))
    return [Prospect(evidence_ref=g["ref"], url=f"https://{k}/", competitors=len(g["targets"]),
                     competitor_urls=[f"https://{t}/" for t in sorted(g["targets"])], domain_rank=g["rank"], spam_score=g["spam"])
            for k, g in keep[:PROSPECT_CAP]]


# ---------- anchors ----------

def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3}


def anchor_bucket(anchor: str, brand: set[str], keywords: set[str]) -> str:
    a = " ".join(anchor.lower().split())
    if not a:
        return "other"
    if _URLISH.match(a):
        return "naked_url"
    if a in GENERIC_ANCHORS:
        return "generic"
    if is_branded(a, brand):
        return "branded"
    if a in keywords:
        return "exact_match"
    aw = _words(a)
    if any(k in a or len(aw & _words(k)) >= 2 for k in keywords):
        return "partial_match"
    return "other"


def anchor_shares(anchors: list[dict], brand: set[str], keywords: set[str]) -> tuple[list[AnchorShare], int]:
    weight: dict[str, int] = defaultdict(int)
    ref: dict[str, Any] = {}
    for a in anchors:
        if a.get("lost_date"):
            continue
        b = anchor_bucket(a.get("anchor") or "", brand, keywords)
        weight[b] += max(1, a.get("backlinks") or 0)
        ref.setdefault(b, a["id"])
    total = sum(weight.values())
    out = []
    for b in ("branded", "naked_url", "generic", "partial_match", "exact_match", "other"):
        if not weight.get(b):
            continue
        share = round(100 * weight[b] / total, 1)
        rng = SAFE_ANCHORS.get(b)
        within = None
        if rng and total >= ANCHOR_MIN_LINKS:
            within = share <= rng[1] if b == "exact_match" else rng[0] <= share <= rng[1]
        out.append(AnchorShare(evidence_ref=ref[b], bucket=b, backlinks=weight[b], share_pct=share,
                               safe_range=f"{rng[0]} to {rng[1]}%" if rng else None, within=within))
    return out, total


# ---------- velocity ----------

def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def velocity(own_rds: list[dict], ref: Any, today: date) -> tuple[Velocity | None, list[LinkFlag]]:
    if not ref:
        return None, []
    months = []
    y, m = today.year, today.month
    for _ in range(MONTHS):
        months.append(f"{y:04d}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    months.reverse()
    counts: dict[str, int] = defaultdict(int)
    first: dict[str, Any] = {}
    seen = [r for r in own_rds if isinstance(r.get("first_seen"), datetime)]
    for r in seen:
        k = _month(r["first_seen"].date())
        counts[k] += 1
        first.setdefault(k, r["id"])
    earliest = min((r["first_seen"].date() for r in seen), default=None)
    program = 1 if earliest is None else (today.year - earliest.year) * 12 + today.month - earliest.month + 1
    guideline = next(text for upto, text in VELOCITY if program <= upto)
    flags = [LinkFlag(evidence_ref=first[k], kind="velocity_spike",
                      detail=f"{counts[k]} new referring domains first seen in {k}; {SPIKE} or more in one month looks unnatural.")
             for k in months if counts[k] >= SPIKE]
    return Velocity(evidence_ref=ref, months=[MonthCount(month=k, new_domains=counts[k]) for k in months], program_month=program,
                    guideline=guideline, last_month_new=counts[months[-2]]), flags


# ---------- tactics ----------

def choose_tactics(phase: str | None, items: list[ChecklistItem], found: list[Prospect]) -> list[str]:
    profiles = [i for i in items if i.kind == "profile"]
    todo_profiles = [i for i in profiles if i.status == "todo"]
    todo_dirs = [i for i in items if i.kind != "profile" and i.status == "todo"]
    order = list(ORDER[phase or "foundation"])
    # foundation work comes back to the front, in any phase, while half or more of the profiles are missing
    if phase != "foundation" and profiles and len(todo_profiles) * 2 >= len(profiles):
        order.insert(0, "entity_stacking")
    available = {"entity_stacking": bool(todo_profiles), "citations": bool(todo_dirs), "competitor_gap": bool(found)}
    out: list[str] = []
    for slug in order:
        if available.get(slug, True) and slug not in out:
            out.append(slug)
    return out[:3]


def tactic_out(slug: str, ref: Any, items: list[ChecklistItem], found: list[Prospect], brand: str, topic: str) -> TacticOut:
    t = TACTICS[slug]
    why, first, eref = t.why, t.first_action, ref
    if slug == "entity_stacking":
        profiles = [i for i in items if i.kind == "profile"]
        todo = [i for i in profiles if i.status == "todo"]
        why = (f"The site's sameAs lists {len(profiles) - len(todo)} of {len(profiles)} brand profiles. Consistent profiles under one "
               f"name are how search engines and AI assistants learn which {brand} this site is, and they make later links look natural.")
        first = f"Create the {todo[0].platform} profile under the exact name {brand}, link it to the site and add its address to sameAs."
        eref = todo[0].evidence_ref
    elif slug == "citations":
        dirs = [i for i in items if i.kind != "profile"]
        todo = [i for i in dirs if i.status == "todo"]
        why = (f"Directories and review sites that list or link to the site: {len(dirs) - len(todo)} of {len(dirs)}. Listings in your own field "
               "are quick, trusted links; skip any directory that accepts every site.")
        first = f"List {brand} on {todo[0].platform} with the same name and description as your profiles."
        eref = todo[0].evidence_ref
    elif slug == "competitor_gap":
        top = found[0]
        why = (f"Sites that link to {MIN_COMPETITORS} or more of your competitors but not to you: {len(found)}. Sites that already link to "
               "sites like yours are the likeliest to link to you.")
        first = (f"Start with {top.url}, which links to {top.competitors} of your competitors; take listings and directories first "
                 "and articles last.")
        eref = top.evidence_ref
    elif slug == "resource_pages":
        first = f"Search intitle:resources {topic} and note the pages where a {brand} page would fill a gap, then offer it to each."
    return TacticOut(evidence_ref=eref, slug=slug, name=t.name, why=why, first_action=first, effort=t.effort, typical=t.typical)


# ---------- model text checks ----------

def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)*", text))


def clean_copy(text: str | None, limit: int, facts: str, brand_name: str, brand_rules: list[dict]) -> str | None:
    """A description survives only if it is within its length, names the brand, uses no number it was not given
    and breaks no blocking brand rule."""
    if not text or not text.strip():
        return None
    text = " ".join(text.split())
    if len(text.split()) > round(limit * 1.1) or brand_name.lower() not in text.lower():
        return None
    if _numbers(text) - _numbers(facts) or blocking_brand_hit(text, brand_rules):
        return None
    return text


def clean_approach(text: str | None, allowed: list[str], brand_rules: list[dict]) -> str | None:
    if not text or not text.strip():
        return None
    text = " ".join(text.split())
    bare = text.lower()
    for a in allowed:
        bare = bare.replace(a.lower(), " ")
    if re.search(r"\d", bare) or len(text.split()) > 40 or blocking_brand_hit(text, brand_rules):
        return None
    return text


def _homepage(pages: list[dict], host: str) -> dict | None:
    home = [p for p in pages if host_of(p.get("url")) == host and (urlsplit(p["url"]).path or "/") == "/" and p.get("status_code") == 200]
    home.sort(key=lambda p: (p.get("word_count") is None, p.get("raw_jsonld") is None))
    return home[0] if home else None


def run(ctx: AnalystContext, inp: AnalystInput) -> LinkPlanReport:
    project = inp.project
    today = inp.params.get("today") or date.today()
    if isinstance(today, str):
        today = date.fromisoformat(today)
    own_host = host_of(project.primary_domain)
    own_hosts = {registrable(d) for d in project.domains} | {host_of(d) for d in project.domains}
    competitors = {registrable(c) for c in project.competitors}
    brand_name = project.display_name or project.slug
    brand = brand_tokens(brand_name, list(project.domains), project.brand_context_terms)
    rules = inp.table("brand_rules")

    pages = inp.table("raw_crawl_pages")
    home = _homepage(pages, own_host)
    page_ref = home["id"] if home else next((p["id"] for p in pages if p.get("status_code") == 200), None)
    summaries = inp.table("raw_backlink_summary")
    own_summary = next((s for s in summaries if s.get("role") == "own"), None)
    rds = inp.table("raw_referring_domains")
    own_rds = [r for r in rds if r.get("role") == "own" and not r.get("lost_date")]
    linking: dict[str, Any] = {}
    for r in own_rds:
        linking.setdefault(registrable(r.get("domain")), r["id"])
    fallback_ref = page_ref or (own_summary or {}).get("id")

    targets = inp.table("link_targets")
    items = checklist(targets, same_as_hosts(pages), linking, fallback_ref, brand_name, own_host)
    listed = sum(1 for i in items if i.status == "done")
    signals = phase_signals(own_summary, pages, listed, len(items), page_ref, today)
    phase = phase_of(signals)

    found = prospects(rds, own_hosts, competitors, {registrable(t["domain"]) for t in targets}, set(linking), brand)
    keywords = {str(k["keyword"]).lower() for k in inp.table("keywords") if k.get("keyword")}
    anchors, anchor_total = anchor_shares(inp.table("raw_anchors"), brand, keywords)
    vel, flags = velocity(own_rds, (own_summary or {}).get("id") or (own_rds[0]["id"] if own_rds else None), today)
    exact = next((a for a in anchors if a.bucket == "exact_match"), None)
    if exact and anchor_total >= ANCHOR_MIN_LINKS and exact.share_pct > SAFE_ANCHORS["exact_match"][1]:
        flags.append(LinkFlag(evidence_ref=exact.evidence_ref, kind="exact_match_anchors",
                              detail=f"{exact.share_pct}% of anchors exactly match a tracked keyword; keep the next 10 to 15 links branded or plain."))
    spammy = [r for r in own_rds if (r.get("spam_score") or 0) > SPAM_MAX]
    if spammy:
        flags.append(LinkFlag(evidence_ref=spammy[0]["id"], kind="spammy_links",
                              detail=f"{len(spammy)} of the domains linking to the site have a spam score above {SPAM_MAX}. Check them, and build no more like them."))

    own_rank = (own_summary or {}).get("rank")
    comps = [CompetitorAuthority(evidence_ref=s["id"], url=f"https://{s['target']}/", domain_rank=s.get("rank"), referring_domains=s.get("referring_domains"),
                                 tier=None if s.get("rank") is None or own_rank is None else "realistic" if s["rank"] <= own_rank + 15 else "aspirational")
             for s in summaries if s.get("role") == "competitor"]
    comps.sort(key=lambda c: -(c.domain_rank or -1))


    facts = " ".join(x for x in [project.description or "", (home or {}).get("title") or "", (home or {}).get("meta_description") or ""] if x)
    copy: ProfileCopy | None = None
    if found or (home and facts):
        listing = [{"domain": p.url.removeprefix("https://").rstrip("/"), "links_to": [u.removeprefix("https://").rstrip("/") for u in p.competitor_urls]}
                   for p in found]
        user = {"brand_name": brand_name, "site": own_host, "today": today.isoformat(), "prospects": listing,
                "facts": facts if home else None}
        reply = ctx.complete(agent=NAME, system=SYSTEM, user=json.dumps(user, indent=1), schema=_Reply, max_tokens=8192)
        kinds = {k.domain.strip().lower().removeprefix("www."): k for k in reply.prospects}
        for p in found:
            d = p.url.removeprefix("https://").rstrip("/")
            k = kinds.get(d)
            if not k:
                continue
            p.category = k.category if k.category in CATEGORY_ORDER and k.category else None
            p.approach = clean_approach(k.approach, [d, *(u.removeprefix("https://").rstrip("/") for u in p.competitor_urls)], rules)
        if home and reply.profile_copy:
            texts = {f: clean_copy(getattr(reply.profile_copy, f), n, facts, brand_name, rules) for f, n in COPY_LIMITS.items()}
            if any(texts.values()):
                copy = ProfileCopy(evidence_ref=home["id"], name=brand_name, **texts)
    found.sort(key=lambda p: (CATEGORY_ORDER.get(p.category, 6), -p.competitors, -(p.domain_rank or 0), p.url))

    topic = (project.keyword_seeds or ["your topic"])[0]
    # no phase means nothing about the site was measured; tactics would be guesses
    tactics = [tactic_out(slug, fallback_ref, items, found, brand_name, topic) for slug in choose_tactics(phase, items, found)] if phase else []

    # Text built in code from config (platform names, notes) still meets the brand rules item by item.
    items = [i for i in items if not blocking_brand_hit(f"{i.platform}\n{i.action}", rules)]
    tactics = [t for t in tactics if not blocking_brand_hit(f"{t.why}\n{t.first_action}", rules)]
    return LinkPlanReport(phase=phase, signals=signals, tactics=tactics, checklist=items, prospects=found, anchors=anchors,
                          velocity=vel, flags=flags, competitors=comps, profile_copy=copy)
