"""Autonomous web research tools: search, source selection, fetching, queue.

The agent decides which queries to run and which sources to read; the harness
only keeps the durable queue and enforces the limits:

  web_search(query)        -> results recorded as status "found" (discovered_by = query);
                              a repeated query is answered from the stored results
                              (cache hit, no search budget)
  select_sources(items)    -> "queue" (status pending, with reason) or "reject"
                              (status skipped, with reason); links found on pages can
                              be queued with found_on = page URL
  web_fetch(url)           -> reads the full page (not the snippet): running -> done | error;
                              material copy saved as an artifact (original/final URL, hash);
                              an already fetched unchanged page is a cache hit (no HTTP)
  queue_status()           -> counters found / selected / read / error / skipped and items
"""
from __future__ import annotations

import json
import re
from typing import Callable, Literal

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from organizer.results import ToolError, err, ok
from organizer.textutil import sha256_bytes

from .artifacts import ArtifactStore
from .budget import Budget, BudgetExhausted
from .config import Limits
from .store import TaskStore
from .web import (HttpFetcher, SearchProvider, canonical_url, decode_body, extract_links, is_text_type, rank_links,
                  url_key)


def norm_query(q: str) -> str:
    return " ".join(re.findall(r"[\w-]+", q.lower()))


class WebSearchArgs(BaseModel):
    query: str = Field(description="Search query you formulated for the research topic")
    max_results: int = Field(8, description="Results to return (1..10)")


class SourceDecision(BaseModel):
    url: str = Field(description="URL from search results or from links of a fetched page")
    decision: Literal["queue", "reject"] = Field(description="queue = will read it; reject = not relevant / duplicate")
    reason: str = Field(description="Why the source was selected or rejected (relevance to the topic)")
    found_on: str | None = Field(None, description="Page URL where the link was found (for links from pages)")


class SelectSourcesArgs(BaseModel):
    items: list[SourceDecision] = Field(description="Decisions for one or more URLs")


class WebFetchArgs(BaseModel):
    url: str = Field(description="URL to read (normally a queued source)")
    force: bool = Field(False, description="Re-download even if the page is already stored (needs a reason)")


class QueueStatusArgs(BaseModel):
    status: str | None = Field(None, description="Filter: found|pending|running|done|error|skipped")
    limit: int = Field(15, description="Items to list (1..50)")


class ResearchTools:
    def __init__(self, store: TaskStore, task_id: str, budget: Budget, limits: Limits, fetcher: HttpFetcher,
                 provider: SearchProvider, artifacts: ArtifactStore) -> None:
        self.store = store
        self.task_id = task_id
        self.budget = budget
        self.limits = limits
        self.fetcher = fetcher
        self.provider = provider
        self.artifacts = artifacts
        # optional hooks set by the harness agent (build_harness_agent): trace events and the crash test hook
        self.emit: Callable[..., None] | None = None
        self.crash_hook: Callable[[str], None] | None = None

    def _emit(self, kind: str, **data) -> None:
        if self.emit:
            self.emit(kind, **data)
        else:
            self.store.event(self.task_id, kind, data)

    # -- web_search ------------------------------------------------------------
    async def web_search(self, query: str, max_results: int = 8) -> str:
        name = "web_search"
        if not query or not query.strip():
            return err(name, "invalid_argument", "query must be non-empty")
        max_results = max(1, min(int(max_results), 10))
        qn = norm_query(query)
        prev = self.store.find_search(self.task_id, qn)
        if prev:
            self.budget.add(cache_hits=1)
            return ok(name, query=query, from_cache=True, note="Same query was already run; stored results returned "
                      "(no search budget used). Formulate a different query for new sources.",
                      results=self._annotate(prev["results"]))
        try:
            self.budget.take("search")
        except BudgetExhausted as e:
            self._emit("guardrail", guard="search_budget", query=query, **e.as_dict(),
                       queue=self.store.url_counts(self.task_id))
            return err(name, "search_budget_exhausted", str(e), **e.as_dict(),
                       hint="Stop searching; read the queued sources or finish with what you have.")
        pages_read_before = self.store.url_counts(self.task_id)["done"]
        try:
            rows = await self.provider.search(query, max_results)
        except Exception as e:  # noqa: BLE001
            self.store.event(self.task_id, "search_error", {"query": query, "error": f"{type(e).__name__}: {e}"[:300]})
            return err(name, "search_failed", f"{type(e).__name__}: {e}"[:300])
        results, new = [], 0
        for rank, r in enumerate(rows, 1):
            if not r.get("url") or not r["url"].startswith(("http://", "https://")):
                continue
            c = canonical_url(r["url"])
            k = url_key(c)
            if self.store.add_url(self.task_id, k, r["url"], c, "found", f"search:{query}", r.get("title", "")[:300],
                                  r.get("snippet", "")[:500], rank):
                new += 1
            results.append({"url": r["url"], "url_key": k, "title": r.get("title", "")[:200],
                            "snippet": r.get("snippet", "")[:300], **({"engine": r["engine"]} if r.get("engine") else {})})
        engines = getattr(self.provider, "last_report", None) or {}
        self.store.add_search(self.task_id, query, qn, self.provider.name, results, new)
        if engines:
            self.store.event(self.task_id, "search_engines", {"query": query, **engines})
        # research rounds: a query issued after pages were read is a refinement round (criterion min_queries_after_reading)
        self.store.event(self.task_id, "search_round", {"query": query, "pages_read_before": pages_read_before,
                                                        "new_urls": new, "round": 2 if pages_read_before else 1})
        return ok(name, query=query, from_cache=False, provider=self.provider.name, engines=engines, new_urls=new,
                  results=self._annotate(results), queue=self.store.url_counts(self.task_id))

    def _annotate(self, results: list[dict]) -> list[dict]:
        out = []
        for r in results:
            row = self.store.get_url(self.task_id, r["url_key"]) or {}
            out.append({**r, "status": row.get("status", "found")})
        return out

    # -- select_sources ------------------------------------------------------------
    def select_sources(self, items: list) -> str:
        name = "select_sources"
        done = []
        for it in items:
            it = it if isinstance(it, dict) else it.model_dump()
            url, decision, reason = it.get("url", ""), it.get("decision"), (it.get("reason") or "").strip()
            if not url.startswith(("http://", "https://")):
                done.append({"url": url, "error": "invalid_url"})
                continue
            if not reason:
                done.append({"url": url, "error": "reason_required"})
                continue
            c = canonical_url(url)
            k = url_key(c)
            row = self.store.get_url(self.task_id, k)
            if row is None:
                # every source must come from a search result or from a page the agent has read: a URL from the
                # model's memory (B2 had one, discovered_by=agent) is refused
                page = self.store.get_url(self.task_id, url_key(canonical_url(it["found_on"]))) if it.get("found_on") else None
                if decision != "queue":
                    # rejecting an unknown URL is harmless: it is recorded as skipped and never read
                    self.store.add_url(self.task_id, k, url, c, "found", "agent")
                    row = self.store.get_url(self.task_id, k)
                elif not page or page["status"] != "done":
                    done.append({"url": url, "error": "not_discovered",
                                 "message": "this URL was not returned by web_search and found_on is not a fetched page; "
                                            "find it with web_search or take it from the links of a fetched page"})
                    continue
                else:
                    self.store.add_url(self.task_id, k, url, c, "found", f"page:{it['found_on']}")
                    row = self.store.get_url(self.task_id, k)
            if row["status"] in ("running", "done", "error"):
                done.append({"url": url, "url_key": k, "status": row["status"], "note": "already processed; unchanged"})
                continue
            status = "pending" if decision == "queue" else "skipped"
            self.store.set_url(self.task_id, k, status=status, reason=reason[:500])
            if it.get("found_on") and decision == "queue":
                # a link followed from a fetched page (also when the same URL was found by a search earlier)
                self.store.event(self.task_id, "link_followed", {"url": url, "url_key": k, "found_on": it["found_on"]})
            done.append({"url": url, "url_key": k, "status": status})
        return ok(name, decisions=done, queue=self.store.url_counts(self.task_id))

    # -- web_fetch ---------------------------------------------------------------------
    async def web_fetch(self, url: str, force: bool = False) -> str:
        name = "web_fetch"
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return err(name, "invalid_url", f"Only absolute http(s) URLs are supported: {url!r}")
        c = canonical_url(url)
        k = url_key(c)
        row = self.store.get_url(self.task_id, k)
        if row is None:
            self.store.add_url(self.task_id, k, url, c, "pending", "agent", reason="fetched directly")
            row = self.store.get_url(self.task_id, k)
        if row["status"] == "done" and row.get("artifact_id") and not force:
            try:
                text = self.artifacts.text(row["artifact_id"])
            except ToolError:
                text = None
            if text is not None:
                self.budget.add(cache_hits=1)
                self._emit("fetch_cache_hit", url=url, url_key=k, artifact_id=row["artifact_id"],
                           http_requests=self.store.usage(self.task_id).get("http_requests", 0),
                           url_http_attempts=row.get("attempts") or 0)
                meta = (self.store.artifact(row["artifact_id"]) or {}).get("meta") or {}
                return self._answer(
                    dict(url=url, url_key=k, final_url=row["final_url"], from_cache=True, artifact_id=row["artifact_id"],
                         title=row["title"], chars=len(text)),
                    meta.get("links") or [], text[:3000],
                    "Already fetched and stored; no network request made (fetching it again gives the same). The page "
                    "links are listed in 'links'. Find exact quotes with search_artifact(artifact_id, term).")
        if row["status"] == "error" and not force and not (row.get("error") or "").startswith("interrupted"):
            # a URL that already failed in this task (retries exhausted, 4xx, redirect loop...) is not fetched again
            # automatically: the saved error is returned at once, no HTTP request, no budget
            self._emit("fetch_error_cached", url=url, url_key=k, error=row.get("error"),
                       http_requests=self.store.usage(self.task_id).get("http_requests", 0))
            return err(name, "already_failed", f"This URL already failed in this task: {row.get('error')}", url=url,
                       url_key=k, attempts=row.get("attempts") or 0, from_cache=True,
                       hint="Not fetched again (no HTTP request). Choose another source; force=true only with a new reason.")
        self.store.set_url(self.task_id, k, status="running")
        try:
            res = await self.fetcher.fetch(url)
        except BaseException:      # tool timeout / cancel: never leave the URL "running"
            self.store.set_url(self.task_id, k, status="error", error="interrupted: tool timeout or cancel")
            raise
        if self.crash_hook and not res.error_type:
            self.crash_hook("download")       # test hook: HTTP done and counted, page not yet stored
        self.store.set_url(self.task_id, k, attempts=(row.get("attempts") or 0) + res.attempts, http_status=res.status,
                           final_url=res.final_url)
        if res.error_type:
            status = "pending" if res.error_type == "http_budget_exhausted" else "error"
            self.store.set_url(self.task_id, k, status=status, error=f"{res.error_type}: {res.error}"[:500])
            self.store.event(self.task_id, "fetch_error", {"url": url, "type": res.error_type, "log": res.log})
            if status == "pending":
                pend = self.store.urls(self.task_id, "pending")
                self._emit("guardrail", guard="http_budget", url=url, http_requests=self.budget.store.usage(
                    self.task_id).get("http_requests", 0), limit=self.limits.max_http_requests,
                    pending_saved=len(pend), pending_urls=[u["url"] for u in pend][:20])
            extra = {"hint": "HTTP budget exhausted: the URL stays pending in the queue."} if status == "pending" else {}
            return err(name, res.error_type, res.error or "", url=url, url_key=k, final_url=res.final_url,
                       redirects=res.redirects, attempts=res.attempts, http_log=res.log, **extra)
        if not is_text_type(res.content_type):
            self.store.set_url(self.task_id, k, status="error", error=f"unsupported_content_type: {res.content_type}")
            return err(name, "unsupported_content_type", f"Not a text/HTML response: {res.content_type}",
                       url=url, final_url=res.final_url, attempts=res.attempts)
        title, text, raw = decode_body(res)
        final_c = canonical_url(res.final_url)
        dup = None
        if final_c != c:
            other = self.store.get_url(self.task_id, url_key(final_c))
            if other and other["status"] == "done":
                dup = other["url"]
        header = (f"Источник: {url}\nКонечный URL: {res.final_url}\nHTTP: {res.status}\nЗаголовок: {title}\n"
                  f"Получено: {('усечено до ' + str(self.limits.max_fetch_bytes) + ' байт') if res.truncated else 'полностью'}\n\n")
        links = rank_links(extract_links(raw, res.final_url, limit=80), res.final_url, limit=12) if "<a" in raw else []
        links = [{"url": l["url"][:200], "anchor": l["anchor"][:60]} for l in links]
        art = self.artifacts.save(header + text, "web_page", res.final_url,
                                  {"url": url, "final_url": res.final_url, "status": res.status, "truncated": res.truncated,
                                   "content_type": res.content_type, "redirects": res.redirects, "links": links})
        self.store.set_url(self.task_id, k, status="done", artifact_id=art["artifact_id"], title=title[:300] or row["title"],
                           content_hash=sha256_bytes(res.body), duplicate_of=dup, error=None)
        return self._answer(
            dict(url=url, url_key=k, final_url=res.final_url, status=res.status, redirects=res.redirects,
                 attempts=res.attempts, title=title[:200], artifact_id=art["artifact_id"], chars=len(text),
                 truncated_download=res.truncated, duplicate_of=dup),
            links, text[:2500],
            f"Page text: first {{n}} of {len(text)} chars below; the FULL page is artifact_id {art['artifact_id']}. Find "
            "exact quotes with search_artifact(artifact_id, term); cite with save_evidence(source=<url>, quote=<exact text>). "
            "Useful links of the page are in 'links' (queue them with found_on=<this url>).")

    def _answer(self, fields: dict, links: list[dict], head: str, note: str) -> str:
        """The answer must fit the single-result limit, so the model sees the PAGE artifact_id directly
        (otherwise the whole answer would be offloaded and the page id hidden inside another artifact)."""
        budget_chars = self.limits.max_tool_result_chars - 300
        while True:
            body = ok("web_fetch", **fields, links=links, note=note.replace("{n}", str(len(head))), text=head)
            if len(body) <= budget_chars or (not links and len(head) < 300):
                return body
            if links:
                links = links[:-3] if len(links) > 3 else []
            else:
                head = head[: int(len(head) * 0.7)]

    # -- queue_status ---------------------------------------------------------------------
    def queue_status(self, status: str | None = None, limit: int = 15) -> str:
        limit = max(1, min(int(limit), 50))
        rows = self.store.urls(self.task_id, status)
        items = [{"url": r["url"], "status": r["status"], "title": (r["title"] or "")[:100], "reason": r["reason"],
                  "discovered_by": r["discovered_by"], "error": r["error"]} for r in rows[:limit]]
        return ok("queue_status", counts=self.store.url_counts(self.task_id),
                  searches=[{"query": s["query"], "results": s["results"], "new_urls": s["new_urls"]}
                            for s in self.store.searches(self.task_id)],
                  items=items, total=len(rows))

    def as_tools(self) -> list[BaseTool]:
        return [
            StructuredTool.from_function(coroutine=self.web_search, name="web_search", args_schema=WebSearchArgs,
                                         description="Search the web: returns URL, title and snippet. You formulate the queries; "
                                                     "results are added to the research queue as 'found'. Snippets are not evidence."),
            StructuredTool.from_function(func=self.select_sources, name="select_sources", args_schema=SelectSourcesArgs,
                                         description="Decide for found URLs: queue (will read; status pending) or reject "
                                                     "(skipped) - always with a reason. Also queues useful links from pages."),
            StructuredTool.from_function(coroutine=self.web_fetch, name="web_fetch", args_schema=WebFetchArgs,
                                         description="Read the full page of a URL (redirects, retries and size limits are enforced; "
                                                     "every HTTP request counts). Stores a copy as an artifact; returns text and links."),
            StructuredTool.from_function(func=self.queue_status, name="queue_status", args_schema=QueueStatusArgs,
                                         description="Research queue counters (found/selected/read/error/skipped), queries and items."),
        ]


def _dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)
