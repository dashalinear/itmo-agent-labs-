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
# descriptive bot UA with a policy URL (Wikimedia and others reject anonymous clients); no personal data
USER_AGENT = "Mozilla/5.0 (compatible; itmo-lab2-research-harness/2.0; +https://www.mediawiki.org/wiki/User-Agent_policy)"


_ARXIV = re.compile(r"^/(?:abs|html|pdf)/((?:\d{4}\.\d{4,5})|(?:[a-z\-]+(?:\.[A-Z]{2})?/\d{7}))(?:v\d+)?(?:\.pdf)?/?$")


def canonical_url(url: str) -> str:
    p = urlparse(url.strip())
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower()
    if host in ("arxiv.org", "www.arxiv.org", "export.arxiv.org"):
        m = _ARXIV.match(p.path or "")
        if m:       # abs / html / pdf and every vN of one paper are ONE source
            return f"https://arxiv.org/abs/{m.group(1)}"
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


_NAV_ANCHOR = re.compile(r"^(search|submit|log ?in|log ?out|sign ?(in|up)|register|help|about|contact|donate|privacy|terms|"
                         r"cookies?|home|subscribe|rss|feed|skip|menu|news|blog|careers|jobs|login|account|"
                         r"\u043f\u043e\u0438\u0441\u043a|\u0432\u043e\u0439\u0442\u0438)\b", re.I)
_NAV_PATH = re.compile(r"/(login|logout|signup|register|search|submit|help|about|privacy|terms|donate|account|"
                       r"ignoreme|auth|static|assets)(/|$)", re.I)
_BINARY = re.compile(r"\.(pdf|png|jpe?g|gif|svg|zip|gz|tar|mp4|mp3|ps)$", re.I)


def rank_links(links: list[dict], base: str, limit: int = 12) -> list[dict]:
    """Content links first: site navigation (search, login, help, the site root...), empty anchors and binaries
    go to the end or are dropped, links to other sites and links with a descriptive anchor go first. Stable
    for equal scores, so a page whose links are all content links keeps its order."""
    host = (urlparse(base).hostname or "").lower()
    scored = []
    for i, l in enumerate(links):
        u = urlparse(l["url"])
        a = (l.get("anchor") or "").strip()
        path = u.path.strip("/")
        if not path and not u.query:
            continue                                       # a site root is never a source
        score = 0.0
        if (u.hostname or "").lower() != host:
            score += 2
        score += min(len(a), 60) / 20
        if len(path.split("/")) >= 2:
            score += 1
        if not a:
            score -= 2
        if _NAV_ANCHOR.match(a) or _NAV_PATH.search(u.path):
            score -= 5
        if _BINARY.search(u.path):
            score -= 1.5
        if score > -2:
            scored.append((-score, i, l))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [l for _s, _i, l in scored[:limit]]


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

    def __init__(self, backend: str = "bing", timeout: float = 8.0) -> None:
        self.backend = backend
        self.timeout = timeout
        self.name = f"ddgs:{backend}"

    async def search(self, query: str, max_results: int) -> list[dict]:
        def _run():
            from ddgs import DDGS
            return DDGS(timeout=int(self.timeout)).text(query, max_results=max_results, backend=self.backend)
        rows = await asyncio.wait_for(asyncio.to_thread(_run), timeout=self.timeout + 4)
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


# ----------------------------------------------------------------- public search APIs (no key needed)
class _ApiProvider(SearchProvider):
    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout

    async def _get(self, url: str, params: dict) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as c:
            r = await c.get(url, params=params)
            r.raise_for_status()
            return r


class WikipediaProvider(_ApiProvider):
    name = "wikipedia"

    async def search(self, query: str, max_results: int) -> list[dict]:
        lang = "ru" if re.search(r"[а-яё]", query.lower()) else "en"
        r = await self._get(f"https://{lang}.wikipedia.org/w/api.php",
                            {"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": max_results})
        out = []
        for it in r.json().get("query", {}).get("search", []):
            title = it["title"]
            out.append({"url": f"https://{lang}.wikipedia.org/wiki/" + title.replace(" ", "_"), "title": title,
                        "snippet": re.sub(r"<[^>]+>", "", it.get("snippet", ""))})
        return out


class ArxivProvider(_ApiProvider):
    name = "arxiv"

    async def search(self, query: str, max_results: int) -> list[dict]:
        import xml.etree.ElementTree as ET
        words = [w for w in re.findall(r"[A-Za-z0-9-]+", query) if len(w) > 2][:6]
        if not words:
            return []
        r = await self._get("http://export.arxiv.org/api/query",
                            {"search_query": " AND ".join(f"all:{w}" for w in words), "max_results": max_results})
        ns = {"a": "http://www.w3.org/2005/Atom"}
        out = []
        for e in ET.fromstring(r.text).findall("a:entry", ns):
            url = (e.findtext("a:id", "", ns) or "").replace("http://", "https://")
            out.append({"url": url, "title": " ".join((e.findtext("a:title", "", ns) or "").split()),
                        "snippet": " ".join((e.findtext("a:summary", "", ns) or "").split())[:300]})
        return out


class HackerNewsProvider(_ApiProvider):
    name = "hn_algolia"

    async def search(self, query: str, max_results: int) -> list[dict]:
        r = await self._get("https://hn.algolia.com/api/v1/search", {"query": query, "tags": "story", "hitsPerPage": max_results})
        return [{"url": h["url"], "title": h.get("title", ""), "snippet": f"HN story, {h.get('points', 0)} points"}
                for h in r.json().get("hits", []) if h.get("url")]


class StackOverflowProvider(_ApiProvider):
    name = "stackoverflow"

    async def search(self, query: str, max_results: int) -> list[dict]:
        r = await self._get("https://api.stackexchange.com/2.3/search/advanced",
                            {"q": query, "site": "stackoverflow", "pagesize": max_results, "order": "desc", "sort": "relevance"})
        import html as _h
        return [{"url": it["link"], "title": _h.unescape(it.get("title", "")), "snippet": ", ".join(it.get("tags", []))}
                for it in r.json().get("items", [])]


class GithubProvider(_ApiProvider):
    name = "github"

    async def search(self, query: str, max_results: int) -> list[dict]:
        r = await self._get("https://api.github.com/search/repositories", {"q": query, "per_page": max_results})
        return [{"url": it["html_url"], "title": it["full_name"], "snippet": (it.get("description") or "")[:300]}
                for it in r.json().get("items", [])]


AD_URL = re.compile(r"bing\.com/(aclick|ck/a)|duckduckgo\.com/y\.js|yandex\.\w+/clck|googleadservices\.com", re.I)


class MultiProvider(SearchProvider):
    """One web_search = one search call in the budget; inside, engines are tried in order.

    General web engines (ddgs backends) come first; the public search APIs (Wikipedia, arXiv,
    Hacker News, Stack Overflow, GitHub) complement them and keep the research going when the
    general engines are unreachable from the current network. An engine that failed twice
    is skipped for the rest of the process (circuit breaker). The report of the last call
    (engines ok / failed / skipped) is returned to the agent and stored with the search.
    """
    name = "multi"

    def __init__(self, engines: list[SearchProvider], per_engine_cap: int = 5) -> None:
        self.engines = engines
        self.per_engine_cap = per_engine_cap
        self.failures: dict[str, int] = {}
        self.last_report: dict = {}
        self.name = "multi:" + ",".join(e.name for e in engines)

    async def search(self, query: str, max_results: int) -> list[dict]:
        report = {"ok": {}, "failed": {}, "skipped": []}
        out, seen = [], set()
        for eng in self.engines:
            if len(out) >= max_results:
                break
            if self.failures.get(eng.name, 0) >= 2:
                report["skipped"].append(eng.name)
                continue
            try:
                rows = await eng.search(query, min(self.per_engine_cap, max_results))
                self.failures[eng.name] = 0
            except Exception as e:  # noqa: BLE001
                self.failures[eng.name] = self.failures.get(eng.name, 0) + 1
                report["failed"][eng.name] = f"{type(e).__name__}: {e}"[:120]
                continue
            n = 0
            for row in rows:
                if not row.get("url") or not row["url"].startswith(("http://", "https://")) or AD_URL.search(row["url"]):
                    continue
                c = canonical_url(row["url"])
                if c in seen:
                    continue
                seen.add(c)
                out.append({**row, "engine": eng.name})
                n += 1
                if len(out) >= max_results:
                    break
            report["ok"][eng.name] = n
        self.last_report = report
        if not out and report["failed"] and not report["ok"]:
            raise RuntimeError("all search engines failed: " + "; ".join(f"{k}: {v}" for k, v in report["failed"].items()))
        return out


def default_engines(ddgs_backends: str) -> list[SearchProvider]:
    engines: list[SearchProvider] = [DdgsProvider(b.strip()) for b in ddgs_backends.split(",") if b.strip()]
    return engines + [WikipediaProvider(), ArxivProvider(), HackerNewsProvider(), StackOverflowProvider(), GithubProvider()]
