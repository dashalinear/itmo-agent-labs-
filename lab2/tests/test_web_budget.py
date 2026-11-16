"""Web research tools, HTTP limits, retries, cache, budget (local fixture server, offline)."""
import json

import pytest

from harness.budget import Budget, BudgetExhausted
from harness.config import Limits
from harness.store import TaskStore
from harness.tools_research import ResearchTools
from harness.web import FixtureProvider, HttpFetcher, canonical_url
from conftest import LAB2


def mk(store, artifacts, base, **lim):
    limits = Limits().updated({"http_timeout_s": 2.0, "backoff_base_s": 0.05, **lim})
    budget = Budget(store, "T", limits)
    return ResearchTools(store, "T", budget, limits, HttpFetcher(budget, limits),
                         FixtureProvider(LAB2 / "fixtures" / "web" / "serp.json", base), artifacts), budget


def j(s):
    return json.loads(s)


def test_canonical_url_dedup():
    a = canonical_url("HTTP://Example.com:80/p/x/?utm_source=a&b=2&a=1#frag")
    assert a == "http://example.com/p/x?a=1&b=2"
    assert canonical_url("http://h/p/checkpoint-1?utm_medium=email") == canonical_url("http://h/p/checkpoint-1/")


async def test_fixture_search_gives_50_plus_urls_and_queue(store, artifacts, web):
    base, _ = web
    rt, budget = mk(store, artifacts, base, max_search_calls=20)
    queries = ["чекпоинты графа langgraph thread_id", "бэкенды sqlite postgres redis хранилище", "долговременная память агента",
               "суммаризация компактификация контекста", "durable execution temporal workflow", "идемпотентность ключ повтор",
               "схема состояния миграция версия", "безопасность секреты шифрование", "состояние агента обзор практика"]
    for q in queries:
        assert j(await rt.web_search(q, 10))["ok"]
    counts = store.url_counts("T")
    assert counts["found_total"] >= 50
    assert store.usage("T")["search_calls"] == len(queries)
    # repeated query -> cache hit, no search budget
    again = j(await rt.web_search(queries[0], 10))
    assert again["data"]["from_cache"] is True and store.usage("T")["search_calls"] == len(queries)
    # duplicates collapse to one queue entry
    assert store.url_counts("T")["found_total"] == len({u["canonical"] for u in store.urls("T")})


async def test_select_fetch_cache_and_links(store, artifacts, web):
    base, handler = web
    rt, budget = mk(store, artifacts, base)
    await rt.web_search("чекпоинты графа", 5)
    url = f"{base}/p/checkpoint-2"
    sel = j(rt.select_sources([{"url": url, "decision": "queue", "reason": "про чекпоинты"},
                               {"url": f"{base}/binary", "decision": "reject", "reason": "бинарный файл"}]))
    assert [d["status"] for d in sel["data"]["decisions"]] == ["pending", "skipped"]
    r = j(await rt.web_fetch(url))
    assert r["ok"] and r["data"]["artifact_id"] and r["data"]["links"]
    http_after_first = store.usage("T")["http_requests"]
    r2 = j(await rt.web_fetch(url + "?utm_source=x"))       # duplicate URL form -> same queue entry, cache hit
    assert r2["data"]["from_cache"] is True
    assert store.usage("T")["http_requests"] == http_after_first and store.usage("T")["cache_hits"] >= 1
    row = store.get_url("T", r["data"]["url_key"])
    assert row["status"] == "done" and row["final_url"] == url
    from harness.web import url_key
    fresh = [l["url"] for l in r["data"]["links"] if store.get_url("T", url_key(canonical_url(l["url"]))) is None]
    assert fresh, "the page links to sources not found by the search"
    j(rt.select_sources([{"url": fresh[0], "decision": "queue", "reason": "ссылка со страницы", "found_on": url}]))
    row = store.get_url("T", url_key(canonical_url(fresh[0])))
    assert row["discovered_by"] == f"page:{url}" and row["status"] == "pending"


@pytest.mark.parametrize("path,error,requests", [
    ("/missing", "http_error", 1),                       # 404: no retry
    ("/down", "http_error_retries_exhausted", 3),        # 503: 1 + 2 retries with backoff
    ("/loop", "too_many_redirects", 6),                  # max_redirects=5 -> 6 hops, each counted
    ("/binary", "unsupported_content_type", 1),
])
async def test_http_errors_are_counted(store, artifacts, web, path, error, requests):
    base, _ = web
    rt, budget = mk(store, artifacts, base)
    r = j(await rt.web_fetch(base + path))
    assert r["ok"] is False and r["error"]["type"] == error
    assert store.usage("T")["http_requests"] == requests
    if error != "unsupported_content_type":
        assert store.urls("T")[0]["status"] == "error"


async def test_redirect_chain_and_flaky_retry(store, artifacts, web):
    base, _ = web
    rt, budget = mk(store, artifacts, base)
    r = j(await rt.web_fetch(f"{base}/go2/durable-2"))
    assert r["ok"] and r["data"]["final_url"].endswith("/p/durable-2") and len(r["data"]["redirects"]) == 2
    assert store.usage("T")["http_requests"] == 3
    f = j(await rt.web_fetch(f"{base}/flaky/2/idempotency-3"))
    assert f["ok"] and f["data"]["attempts"] == 3
    assert store.usage("T")["http_requests"] == 6


async def test_timeout_retries_and_large_response(store, artifacts, web):
    base, _ = web
    rt, budget = mk(store, artifacts, base, http_timeout_s=0.5, http_retries=1, max_fetch_bytes=200_000)
    t = j(await rt.web_fetch(f"{base}/slow"))
    assert t["error"]["type"] == "timeout_retries_exhausted" and store.usage("T")["http_requests"] == 2
    big = j(await rt.web_fetch(f"{base}/large"))
    assert big["ok"] and big["data"]["truncated_download"] is True
    assert store.usage("T")["bytes_downloaded"] <= 200_000 + 10_000


async def test_http_budget_stops_and_keeps_queue(store, artifacts, web):
    base, _ = web
    rt, budget = mk(store, artifacts, base, max_http_requests=2)
    await rt.web_search("идемпотентность ключ повтор", 6)
    urls = [u["url"] for u in store.urls("T")][:4]
    j(rt.select_sources([{"url": u, "decision": "queue", "reason": "тема"} for u in urls]))
    res = [j(await rt.web_fetch(u)) for u in urls[:3]]
    assert res[0]["ok"] and res[1]["ok"]
    assert res[2]["error"]["type"] == "http_budget_exhausted"
    assert store.usage("T")["http_requests"] == 2
    assert store.url_counts("T")["pending"] >= 2          # unprocessed URLs stay in the durable queue


async def test_search_budget_exhausted(store, artifacts, web):
    base, _ = web
    rt, budget = mk(store, artifacts, base, max_search_calls=1)
    assert j(await rt.web_search("чекпоинты", 5))["ok"]
    r = j(await rt.web_search("память агента", 5))
    assert r["error"]["type"] == "search_budget_exhausted"


def test_token_budget_reserve_commit_and_restart(store, ws):
    limits = Limits().updated({"max_total_tokens": 10_000, "max_model_calls": 2})
    b = Budget(store, "T", limits)
    rid = b.reserve("agent", 6000, 4000)
    with pytest.raises(BudgetExhausted):
        b.reserve("agent", 5000, 3000)                 # reserved tokens count before the call
    b.commit(rid, "agent", 3900, 300)
    assert b.spent_tokens() == 4200
    rid2 = b.reserve("compaction", 1000, 800)
    b.fail(rid2, 800)
    assert b.spent_tokens() == 5000
    # a new process: inflight reservations of a dead process are charged, counters persist
    rid3 = b.reserve("agent", 2000, 1500)
    store.close()
    s2 = TaskStore(ws.service / "state.db")
    b2 = Budget(s2, "T", limits)
    lost = b2.recover_inflight()
    assert [r["id"] for r in lost] == [rid3] and b2.spent_tokens() == 6500
    b2.commit(b2.reserve("agent", 100, 50), "agent", 50, 10)
    with pytest.raises(BudgetExhausted) as e:
        b2.reserve("agent", 10, 5)                       # max_model_calls=2 reached
    assert e.value.resource == "model_calls"
    s2.close()


async def test_multi_engine_search_falls_back_and_skips_dead_engines(store, artifacts, web):
    from harness.web import MultiProvider, SearchProvider

    class Dead(SearchProvider):
        name = "dead"
        calls = 0

        async def search(self, query, max_results):
            Dead.calls += 1
            raise TimeoutError("engine unreachable")

    class Api(SearchProvider):
        name = "api"

        async def search(self, query, max_results):
            return [{"url": "https://www.bing.com/aclick?ld=ad", "title": "ad", "snippet": ""},
                    {"url": "https://example.org/a", "title": "A", "snippet": "a"},
                    {"url": "https://example.org/a?utm_source=x", "title": "A dup", "snippet": "a"},
                    {"url": "https://example.org/b", "title": "B", "snippet": "b"}]

    mp = MultiProvider([Dead(), Api()])
    limits = Limits()
    budget = Budget(store, "T", limits)
    rt = ResearchTools(store, "T", budget, limits, HttpFetcher(budget, limits), mp, artifacts)
    for q in ("q one", "q two", "q three"):
        r = j(await rt.web_search(q, 8))
        assert r["ok"] and [x["url"] for x in r["data"]["results"]] == ["https://example.org/a", "https://example.org/b"]
    assert Dead.calls == 2                                 # circuit breaker: skipped after two failures
    assert r["data"]["engines"]["skipped"] == ["dead"] and r["data"]["engines"]["ok"] == {"api": 2}
    assert store.usage("T")["search_calls"] == 3           # one web_search = one search call
    assert any(e["kind"] == "search_engines" for e in store.events("T"))

    class AllDead(SearchProvider):
        name = "all"

        async def search(self, query, max_results):
            raise ConnectionError("no network")
    rt2 = ResearchTools(store, "T", budget, limits, HttpFetcher(budget, limits), MultiProvider([AllDead()]), artifacts)
    r = j(await rt2.web_search("q four", 8))
    assert r["error"]["type"] == "search_failed" and "all search engines failed" in r["error"]["message"]


async def test_fetch_interrupted_by_tool_timeout_is_not_left_running(store, artifacts, web):
    import asyncio
    base, _ = web
    rt, budget = mk(store, artifacts, base, http_timeout_s=30.0)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(rt.web_fetch(f"{base}/slow"), timeout=0.5)
    row = store.urls("T")[0]
    assert row["status"] == "error" and "interrupted" in row["error"]


async def test_web_fetch_answer_fits_single_limit_and_shows_page_artifact(store, artifacts, web):
    base, _ = web
    rt, budget = mk(store, artifacts, base, max_fetch_bytes=3_000_000)
    for path in ("/large", "/p/checkpoint-3"):
        raw = await rt.web_fetch(base + path)
        r = j(raw)
        assert r["ok"] and len(raw) <= Limits().max_tool_result_chars       # never offloaded as a whole
        assert r["data"]["artifact_id"].startswith("art_") and r["data"]["artifact_id"] in r["data"]["note"]
        assert artifacts.text(r["data"]["artifact_id"]).startswith("Источник:")   # id of the PAGE copy
