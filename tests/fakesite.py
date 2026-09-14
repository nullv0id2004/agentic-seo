"""An httpx MockTransport that serves a small fake site and can be told to 'lose the network' after N requests."""
from __future__ import annotations

import httpx

HOME = """<html><head><title>KORUM: hiring platform</title>
<meta name="description" content="Hire faster with KORUM."><link rel="canonical" href="https://korum.worldhire.com/">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization","name":"KORUM"}</script>
</head><body><h1>Hiring, done right</h1><p>Some words on the page about hiring and job seekers.</p>
<a href="/about">About</a> <a href="/blog/one">Blog one</a> <a href="/blog/two">Blog two</a>
<a href="/recruiter/inbox">Inbox</a> <a href="https://other.example/x">ext</a></body></html>"""

ABOUT = """<html><head><title>About KORUM</title><meta name="robots" content="index,follow"></head>
<body><h1>About</h1><p>Words words words.</p><a href="/">home</a></body></html>"""

BLOG = """<html><head><title>Blog {n}</title>
<script type="application/ld+json">{{"@type":"Article","headline":"Blog {n}"}}</script></head>
<body><h1>Blog {n}</h1><p>{words}</p></body></html>"""

SITEMAP = """<?xml version="1.0"?><urlset><url><loc>https://korum.worldhire.com/</loc></url>
<url><loc>https://korum.worldhire.com/about</loc></url><url><loc>https://korum.worldhire.com/blog/one</loc></url></urlset>"""

PRODUCT = """<html><head><title>Retinol Serum</title>
<script type="application/ld+json">{"@type":"Product","name":"Retinol Serum","offers":{"@type":"Offer","price":"49.00","priceCurrency":"USD"}}</script>
</head><body><h1>Retinol Serum</h1><p>Only $59.00 today.</p></body></html>"""


class FakeSite:
    def __init__(self, leaked_inbox: bool = False, fail_after: int | None = None, sitemap: str = SITEMAP):
        self.leaked_inbox = leaked_inbox
        self.fail_after = fail_after
        self.requests = 0
        self.sitemap = sitemap
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.fail_after is not None and self.requests > self.fail_after:
            raise httpx.ConnectError("network killed", request=request)
        path = request.url.path
        html = {"content-type": "text/html; charset=utf-8"}
        if path == "/sitemap.xml":
            return httpx.Response(200, text=self.sitemap, headers={"content-type": "application/xml"})
        if path == "/":
            return httpx.Response(200, text=HOME, headers=html)
        if path == "/about":
            return httpx.Response(200, text=ABOUT, headers=html)
        if path.startswith("/blog/"):
            n = path.rsplit("/", 1)[-1]
            return httpx.Response(200, text=BLOG.format(n=n, words="lorem " * 120), headers=html)
        if path.startswith("/recruiter/") or path.startswith("/dashboard/") or path.startswith("/admin/"):
            if self.leaked_inbox:
                return httpx.Response(200, text="<html><head><title>Inbox</title></head><body>candidate data</body></html>", headers=html)
            return httpx.Response(200, text="<html><head><title>Inbox</title><meta name='robots' content='noindex'></head><body></body></html>",
                                  headers={**html, "x-robots-tag": "noindex, nofollow"})
        if path.startswith("/api/"):
            return httpx.Response(401, text="unauthorised", headers={"x-robots-tag": "noindex"})
        if path.startswith("/products/"):
            return httpx.Response(200, text=PRODUCT, headers=html)
        if path.startswith("/cart") or path.startswith("/checkout") or path.startswith("/account"):
            return httpx.Response(302, headers={"location": "https://rejuveluxe.com/login"})
        return httpx.Response(404, text="nope", headers=html)
