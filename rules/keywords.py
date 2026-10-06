"""Keyword relevance helpers shared by collectors, analysts and the orchestrator. Pure, no network."""
from __future__ import annotations

import re
from collections import Counter


def _edit_distance(a: str, b: str) -> int:
    """Optimal string alignment distance: an adjacent swap ("kourm" for "korum") counts as one edit."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def brand_tokens(brand: str, domains: list[str], context_terms: list[str]) -> set[str]:
    """Words that make a keyword navigational: the brand, the labels of the project's domains and the
    first context term (the parent brand). Generic labels such as www and com are left out."""
    toks = {brand.lower()} if brand else set()
    for d in domains:
        toks |= {x for x in d.lower().removeprefix("www.").split(".")[:-1] if len(x) > 2}
    if context_terms:
        toks.add(context_terms[0].lower())
    return {t for t in toks if t}


def is_branded(keyword: str, tokens: set[str]) -> bool:
    """True for a keyword that names the brand, including a one-edit misspelling with the same first
    letter ("kourm" for "korum", not "forum") and a split name ("world hire"). Asking an assistant for
    "the best options for kourm" measures nothing."""
    words = keyword.lower().split()
    candidates = words + [x + y for x, y in zip(words, words[1:], strict=False)]
    for word in candidates:
        for t in tokens:
            if word == t or (len(t) >= 4 and word[:1] == t[:1] and _edit_distance(word, t) <= 1):
                return True
    return False


# Words too broad to define a topic on their own.
GENERIC_WORDS = frozenset({"best", "top", "free", "online", "india", "near", "with", "from", "what", "how", "the", "for", "and",
                           "search", "platform", "platforms", "software", "website", "websites", "site", "sites", "services",
                           "service", "app", "apps", "list", "2024", "2025", "2026",
                           "while", "when", "your", "into", "about", "after", "during", "without", "who", "why", "where"})


# Words every site in a vertical uses. Run b3245cad filtered competitor keywords by "job" and "hiring" and got
# every job-board search ("free job alert", "sarkari job"); a topic is what sets the project apart.
VERTICAL_GENERIC: dict[str, frozenset[str]] = {
    "recruitment": frozenset({"job", "jobs", "hiring", "hire", "hired", "career", "careers", "work", "vacancy", "vacancies",
                              "recruitment", "recruiting", "recruiter", "recruiters", "employment", "employer", "employers",
                              "firm", "firms", "company", "opening", "openings", "apply", "salary", "role", "roles", "portal",
                              # run 4c217c16: "professional resume templates", "talent acquisition", "national talent
                              # search examination" came through on these two
                              "talent", "talents", "professional", "professionals"}),
}


def topic_words(keywords: list[str], brand: set[str], vertical: str | None = None, limit: int = 8) -> list[str]:
    """Most frequent distinctive words across the seeds: not generic, not generic for the vertical, and not
    the brand or a misspelling of it ("kourm" for "korum", run be9ad355)."""
    generic = GENERIC_WORDS | VERTICAL_GENERIC.get(vertical or "", frozenset())
    counts: Counter[str] = Counter()
    for k in keywords:
        for w in re.findall(r"[a-z0-9]+", k.lower()):
            if len(w) >= 3 and w not in generic and not is_branded(w, brand):
                counts[w] += 1
    return [w for w, _ in counts.most_common(limit)]





# Words that place a search in the vertical's own world. A topic word alone is ambiguous: "executive" also
# means "executive meaning in hindi" and "chief executive officer of google" (run 3fc48787), so an
# opportunity needs a topic word AND a different word from this list.
VERTICAL_ANCHORS: dict[str, frozenset[str]] = {
    "recruitment": frozenset({"job", "jobs", "search", "hiring", "hire", "recruiter", "recruiters", "recruitment", "recruiting",
                              "headhunter", "headhunters", "headhunting", "career", "careers", "role", "roles", "position",
                              "positions", "opportunity", "opportunities", "employer", "employers", "employed",
                              "vacancy", "vacancies", "opening", "openings"}),
}


def _tokens(keyword: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", keyword.lower())


def _matches(token: str, word: str) -> bool:
    return token == word or (token.startswith(word) and len(token) - len(word) <= 2)


def on_topic(keyword: str, words: list[str], vertical: str | None = None) -> bool:
    """True when the keyword contains a topic word ("executive" in "executives") and, for a vertical with
    anchors, also a different word that places it in that vertical ("executive job search", not
    "executive meaning")."""
    toks = _tokens(keyword)
    topic = [t for t in toks if any(_matches(t, w) for w in words)]
    if not topic:
        return False
    anchors = VERTICAL_ANCHORS.get(vertical or "")
    if not anchors:
        return True
    return any(a != t for a in toks if a in anchors for t in topic)


def anchor_pattern(vertical: str | None) -> str | None:
    """Regex alternation of the vertical's anchor words, for API-side filtering; None when it has none."""
    anchors = VERTICAL_ANCHORS.get(vertical or "")
    return "|".join(sorted(anchors)) if anchors else None
