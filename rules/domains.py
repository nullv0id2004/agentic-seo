"""Domain helpers shared by collectors and analysts. Pure functions, no network."""
from __future__ import annotations

from urllib.parse import urlsplit

# Second-level labels under a two-letter country code that are part of the registrable suffix:
# glassdoor.co.in, amazon.co.uk, example.com.au.
_SECOND_LEVEL = frozenset({"co", "com", "org", "net", "gov", "ac", "edu", "ltd", "plc", "nic", "res"})


def host_of(value: str | None) -> str:
    """Lower-case host of a URL or bare domain, without www. or a port."""
    v = (value or "").strip().lower()
    host = urlsplit(v).netloc if "//" in v else v.split("/", 1)[0]
    host = host.split(":", 1)[0]
    return host[4:] if host.startswith("www.") else host


def registrable(value: str | None) -> str:
    """The registrable domain: tr.linkedin.com -> linkedin.com, jobs.glassdoor.co.in -> glassdoor.co.in."""
    labels = [x for x in host_of(value).split(".") if x]
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def brand_label(value: str | None) -> str:
    """The name part of a registrable domain: naukri.com -> naukri, glassdoor.co.in -> glassdoor."""
    return registrable(value).split(".", 1)[0]
