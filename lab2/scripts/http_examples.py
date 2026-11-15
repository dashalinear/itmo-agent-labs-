"""Separate HTTP examples of the web harness (no LLM): duplicate, redirect, 404, retries, timeout, large response,
cache hit, HTTP budget. Uses the same ResearchTools / HttpFetcher as the agent, against scripts/fixture_web.py.

    python scripts/http_examples.py            # starts its own fixture server on a free port
Writes results/http_examples.json and results/http_examples.md.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

LAB2 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB2))
sys.path.insert(0, str(LAB2 / "scripts"))

import harness  # noqa: E402,F401
import fixture_web  # noqa: E402
from harness.artifacts import ArtifactStore  # noqa: E402
from harness.budget import Budget  # noqa: E402
from harness.config import Limits  # noqa: E402
from harness.store import TaskStore  # noqa: E402
from harness.tools_research import ResearchTools  # noqa: E402
from harness.web import FixtureProvider, HttpFetcher, canonical_url, url_key  # noqa: E402
from harness.workspace import HarnessWorkspace  # noqa: E402


async def main() -> None:
    fixture_web.SLOW_SECONDS = 6.0
    srv, base = fixture_web.start(0)
    tmp = Path(tempfile.mkdtemp())
    ws = HarnessWorkspace(tmp / "ws")
    store = TaskStore(ws.service / "state.db")
    rows = []

    def tools(task: str, **lim):
        limits = Limits().updated({"http_timeout_s": 2.0, "http_retries": 2, "backoff_base_s": 0.2, "max_redirects": 5,
                                   "max_fetch_bytes": 300_000, **lim})
        store.create_task(task, "web", "http examples", {"root": str(ws.root)}, limits.to_dict())
        b = Budget(store, task, limits)
        return ResearchTools(store, task, b, limits, HttpFetcher(b, limits),
                             FixtureProvider(LAB2 / "fixtures" / "web" / "serp.json", base),
                             ArtifactStore(ws, store, task)), limits

    async def case(name: str, expected: str, url: str, task: str, rt: ResearchTools, note: str = ""):
        before = dict(store.usage(task))
        res = json.loads(await rt.web_fetch(url))
        after = store.usage(task)
        d = res.get("data") or res.get("error") or {}
        rows.append({"case": name, "url": url.replace(base, "{BASE}"), "expected": expected, "ok": res["ok"],
                     "result": "ok" if res["ok"] else d.get("type"),
                     "final_url": (d.get("final_url") or "").replace(base, "{BASE}"),
                     "redirects": len(d.get("redirects") or []), "attempts": d.get("attempts"),
                     "http_requests_counted": after["http_requests"] - before["http_requests"],
                     "cache_hit": bool(d.get("from_cache")), "truncated": d.get("truncated_download"),
                     "queue_status": (store.get_url(task, url_key(canonical_url(url))) or {}).get("status"), "note": note})

    rt, _ = tools("H1")
    await case("page", "ok, 1 request", f"{base}/p/checkpoint-1", "H1", rt)
    await case("duplicate (utm)", "same canonical URL -> cache hit, 0 requests",
               f"{base}/p/checkpoint-1?utm_source=newsletter&utm_medium=email", "H1", rt,
               f"canonical: {canonical_url(base + '/p/checkpoint-1?utm_source=newsletter').replace(base, '{BASE}')}")
    await case("duplicate (trailing slash)", "cache hit, 0 requests", f"{base}/p/checkpoint-1/", "H1", rt)
    await case("redirect chain 301 -> 302 -> 200", "ok, 3 requests (each hop counted)", f"{base}/go2/durable-2", "H1", rt)
    await case("redirect loop", "too_many_redirects after max_redirects+1 hops", f"{base}/loop", "H1", rt)
    await case("404", "http_error, no retry, 1 request", f"{base}/missing", "H1", rt)
    await case("flaky 503 x2", "ok after 2 retries with backoff, 3 requests", f"{base}/flaky/2/idempotency-3", "H1", rt)
    await case("always 503", "http_error_retries_exhausted, 3 requests", f"{base}/down", "H1", rt)
    await case("slow (6 s > timeout 2 s)", "timeout_retries_exhausted, 3 requests", f"{base}/slow", "H1", rt)
    await case("large 3 MB", "ok, download capped at max_fetch_bytes; downloaded text in an artifact, model gets a bounded preview", f"{base}/large", "H1", rt)
    await case("binary", "unsupported_content_type", f"{base}/binary", "H1", rt)
    rt2, _ = tools("H2", max_http_requests=3)
    await case("budget: 1st page", "ok", f"{base}/p/memory-1", "H2", rt2)
    await case("budget: redirect chain", "2 of 3 hops fit, then http_budget_exhausted; URL stays pending",
               f"{base}/go2/memory-2", "H2", rt2)
    await case("budget: cache hit after exhaustion", "cache hit works without network budget", f"{base}/p/memory-1", "H2", rt2)
    srv.shutdown()

    out = LAB2 / "results"
    out.mkdir(exist_ok=True)
    (out / "http_examples.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# HTTP-примеры web harness (без LLM, scripts/http_examples.py)", "",
          "| случай | ожидание | результат | HTTP-запросов учтено | попыток | редиректов | cache hit | статус в очереди |",
          "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['case']} | {r['expected']} | {r['result']}{' (усечено)' if r['truncated'] else ''} | "
                  f"{r['http_requests_counted']} | {r['attempts']} | {r['redirects']} | {'да' if r['cache_hit'] else 'нет'} | "
                  f"{r['queue_status']} |")
    u1, u2 = store.usage("H1"), store.usage("H2")
    md += ["", f"Итого H1: HTTP-запросов {u1['http_requests']}, cache hits {u1['cache_hits']}, байт {u1['bytes_downloaded']}.",
           f"Итого H2 (max_http_requests=3): HTTP-запросов {u2['http_requests']}, cache hits {u2['cache_hits']}."]
    (out / "http_examples.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    store.close()
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
