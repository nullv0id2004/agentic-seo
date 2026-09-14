"""A stateful fake of the GitHub REST endpoints the executor uses. Records everything; refuses merges."""
from __future__ import annotations

import base64
import json
import re

import httpx


class FakeGitHub:
    def __init__(self, repo: str = "worldhire/korum-web", default_branch: str = "main"):
        self.repo = repo
        self.default_branch = default_branch
        self.branches: dict[str, str] = {default_branch: "abc123"}
        self.files: dict[tuple[str, str], str] = {(default_branch, "public/robots.txt"): "User-agent: *\nAllow: /\n"}
        self.pulls: dict[int, dict] = {}
        self.pushes_to_default = 0
        self.merge_attempts = 0
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        path, method = req.url.path, req.method
        body = json.loads(req.content) if req.content else {}
        if "/merge" in path:
            self.merge_attempts += 1
            return httpx.Response(405, json={"message": "merge attempted"})
        if path == f"/repos/{self.repo}" and method == "GET":
            return httpx.Response(200, json={"default_branch": self.default_branch})
        m = re.fullmatch(rf"/repos/{self.repo}/git/ref/heads/(.+)", path)
        if m and method == "GET":
            b = m.group(1)
            return httpx.Response(200, json={"object": {"sha": self.branches[b]}}) if b in self.branches else httpx.Response(404)
        if path == f"/repos/{self.repo}/git/refs" and method == "POST":
            name = body["ref"].removeprefix("refs/heads/")
            self.branches[name] = body["sha"]
            for (b, p), c in list(self.files.items()):
                if b == self.default_branch:
                    self.files[(name, p)] = c
            return httpx.Response(201, json={"ref": body["ref"]})
        m = re.fullmatch(rf"/repos/{self.repo}/git/refs/heads/(.+)", path)
        if m and method == "DELETE":
            self.branches.pop(m.group(1), None)
            return httpx.Response(204)
        m = re.fullmatch(rf"/repos/{self.repo}/contents/(.+)", path)
        if m:
            p = m.group(1)
            if method == "GET":
                b = req.url.params.get("ref", self.default_branch)
                if (b, p) in self.files:
                    return httpx.Response(200, json={"sha": f"sha-{b}-{p}", "content": base64.b64encode(self.files[(b, p)].encode()).decode()})
                return httpx.Response(404, json={"message": "Not Found"})
            if method == "PUT":
                b = body["branch"]
                if b == self.default_branch:
                    self.pushes_to_default += 1
                    return httpx.Response(409, json={"message": "protected"})
                self.files[(b, p)] = base64.b64decode(body["content"]).decode()
                return httpx.Response(201, json={"content": {"path": p}})
        if path == f"/repos/{self.repo}/pulls" and method == "POST":
            n = len(self.pulls) + 1
            self.pulls[n] = {**body, "state": "open", "number": n, "html_url": f"https://github.com/{self.repo}/pull/{n}"}
            return httpx.Response(201, json=self.pulls[n])
        m = re.fullmatch(rf"/repos/{self.repo}/pulls/(\d+)", path)
        if m and method == "PATCH":
            self.pulls[int(m.group(1))].update(body)
            return httpx.Response(200, json=self.pulls[int(m.group(1))])
        return httpx.Response(404, json={"message": f"unhandled {method} {path}"})
