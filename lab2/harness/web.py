"""HTTP fetching under the web budget, URL canonicalization and search providers.

HttpFetcher follows redirects MANUALLY, so every hop is a separate counted
request; every retry is counted too. Before each request the HTTP budget is
checked (Budget.take("http")). Retries: finite, exponential backoff, only for
timeouts / connection errors / 429 / 5xx. Body size is capped while streaming.

Search providers (selected by WEB_SEARCH_PROVIDER):
  ddgs     real metasearch through the ``ddgs`` library (backend, e.g. bing);
           one provider call = one search call in the budget (its internal
           HTTP traffic is not visible to the harness and is not counted as HTTP).
  fixture  the fixed search index fixtures/web/serp.json (>= 50 URLs) ranked by
           word overlap with the agent's query, served by scripts/fixture_web.py.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx

from organizer.tools_web import html_to_text

from .budget import Budget, BudgetExhausted
from .config import Limits

TRACKING = re.compile(r"^(utm_.*|gclid|fbclid|yclid|mc_cid|mc_eid|ref|ref_src|_hsenc|_hsmi)$", re.I)
RETRY_STATUS = {429, 500, 502, 503, 504}
USER_AGENT = "itmo-lab2-research-harness/2.0 (+educational)"


def canonical_url(url: str) -> str:
    p = urlparse(url.strip())
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower()
    port = p.port
    netloc = host if port in (None, 80 if scheme == "http" else 443) else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", p.path or "/")
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)))
    return urlunparse((scheme, netloc, path, "", query, ""))


def url_key(canonical: str) -> str:
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join("".join(self._text).split())[:120]))
            self._href = None


def extract_links(html: str, base: str, limit: int = 25) -> list[dict]:
    p = _LinkParser()
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001 - malformed HTML
        pass
    out, seen = [], set()
    for href, text in p.links:
        absu = urljoin(base, href)
        if urlparse(absu).scheme not in ("http", "https"):
            continue
        c = canonical_url(absu)
        if c in seen or c == canonical_url(base):
            continue
        seen.add(c)
        out.append({"url": absu, "anchor": text})
        if len(out) >= limit:
            break
    return out


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int | None = None
    content_type: str = ""
    body: bytes = b""
    truncated: bool = False
    redirects: list[str] = field(default_factory=list)
    attempts: int = 0
    error_type: str | None = None
    error: str | None = None
    log: list[dict] = field(default_factory=list)


class HttpFetcher:
    def __init__(self, budget: Budget, limits: Limits, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.budget = budget
        self.limits = limits
        self.transport = transport

    async def _one(self, client: httpx.AsyncClient, url: str) -> tuple[int, dict, bytes, bool]:
        async with client.stream("GET", url) as resp:
            chunks, size, truncated = [], 0, False
            if resp.status_code < 300 or resp.status_code >= 400:
                async for chunk in resp.aiter_bytes():
                    if size + len(chunk) > self.limits.max_fetch_bytes:
                        chunks.append(chunk[: self.limits.max_fetch_bytes - size])
                        size = self.limits.max_fetch_bytes
                        truncated = True
                        break
                    chunks.append(chunk)
                    size += len(chunk)
            return resp.status_code, dict(resp.headers), b"".join(chunks), truncated

    async def fetch(self, url: str) -> FetchResult:
        res = FetchResult(url=url, final_url=url)
        current = url
        async with httpx.AsyncClient(follow_redirects=False, transport=self.transport,
                                     timeout=httpx.Timeout(self.limits.http_timeout_s),
                                     headers={"User-Agent": USER_AGENT}) as client:
            for hop in range(self.limits.max_redirects + 1):
                status = headers = body = None
                for attempt in range(self.limits.http_retries + 1):
                    try:
                        self.budget.take("http")          # check BEFORE the request; counts retries and hops
                    except BudgetExhausted as e:
                        res.error_type, res.error = "http_budget_exhausted", str(e)
                        return res
                    res.attempts += 1
                    try:
                        status, headers, body, truncated = await self._one(client, current)
                        self.budget.add(bytes_downloaded=len(body))
                        res.log.append({"url": current, "attempt": attempt + 1, "status": status})
                    except (httpx.TimeoutException, httpx.TransportError) as e:
                        kind = "timeout" if isinstance(e, httpx.TimeoutException) else "network_error"
                        res.log.append({"url": current, "attempt": attempt + 1, "error": kind})
                        status = None
                        res.error_type, res.error = kind, f"{type(e).__name__}: {e}"[:300]
                    if status is not None and status not in RETRY_STATUS:
                        break
                    if attempt < self.limits.http_retries:
                        await asyncio.sleep(self.limits.backoff_base_s * (2 ** attempt))
                if status is None:
                    if res.attempts and res.error_type in ("timeout", "network_error"):
                        res.error_type = f"{res.error_type}_retries_exhausted"
                    return res
                res.status = status
                if 300 <= status < 400 and headers.get("location"):
                    nxt = urljoin(current, headers["location"])
                    res.redirects.append(nxt)
                    if hop == self.limits.max_redirects:
                        res.error_type, res.error = "too_many_redirects", f"more than {self.limits.max_redirects} redirects"
                        return res
                    current = nxt
                    continue
                res.final_url = current
                res.content_type = headers.get("content-type", "")
                res.body = body
                res.truncated = truncated
                res.error_type = res.error = None
                if status in RETRY_STATUS:
                    res.error_type, res.error = "http_error_retries_exhausted", f"HTTP {status} after {self.limits.http_retries + 1} attempts"
                elif status >= 400:
                    res.error_type, res.error = "http_error", f"HTTP {status}"
                return res
        return res


def decode_body(res: FetchResult) -> tuple[str, str, str]:
    """Returns (title, text, raw_html_or_text)."""
    enc = "utf-8"
    m = re.search(r"charset=([\w-]+)", res.content_type or "", re.I)
    if m:
        enc = m.group(1)
    raw = res.body.decode(enc, errors="replace")
    if "html" in (res.content_type or "") or raw.lstrip().lower().startswith(("<!doctype html", "<html")):
        title, text = html_to_text(raw)
        return title, text, raw
    return "", raw, raw


def is_text_type(ctype: str) -> bool:
    c = (ctype or "").lower()
    return not c or c.startswith("text/") or any(x in c for x in ("html", "json", "xml"))


# ----------------------------------------------------------------- search providers
class SearchProvider:
    name = "base"

    async def search(self, query: str, max_results: int) -> list[dict]:
        raise NotImplementedError


class DdgsProvider(SearchProvider):
    name = "ddgs"

    def __init__(self, backend: str = "bing", timeout: float = 15.0) -> None:
        self.backend = backend
        self.timeout = timeout
        self.name = f"ddgs:{backend}"

    async def search(self, query: str, max_results: int) -> list[dict]:
        def _run():
            from ddgs import DDGS
            return DDGS(timeout=int(self.timeout)).text(query, max_results=max_results, backend=self.backend)
        rows = await asyncio.wait_for(asyncio.to_thread(_run), timeout=self.timeout + 5)
        return [{"url": r.get("href") or r.get("url"), "title": r.get("title", ""), "snippet": r.get("body", "")}
                for r in rows if r.get("href") or r.get("url")]


class FixtureProvider(SearchProvider):
    name = "fixture"

    def __init__(self, serp_path: Path, base_url: str) -> None:
        self.entries = json.loads(Path(serp_path).read_text(encoding="utf-8"))["entries"]
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def _stems(text: str) -> set[str]:
        return {w[:5] for w in re.findall(r"[\w-]+", text.lower()) if len(w) > 2}

    async def search(self, query: str, max_results: int) -> list[dict]:
        q = self._stems(query)
        scored = []
        for i, e in enumerate(self.entries):
            words = self._stems(" ".join([e["title"], e["snippet"], " ".join(e.get("keywords", []))]))
            score = len(q & words)
            if score:
                scored.append((-score, i, e))
        scored.sort()
        return [{"url": e["url"].replace("{BASE}", self.base_url), "title": e["title"], "snippet": e["snippet"]}
                for _s, _i, e in scored[:max_results]]
