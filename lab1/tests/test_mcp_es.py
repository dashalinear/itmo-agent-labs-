"""Integration: the real MCP server over a real Elasticsearch (skipped if ES is down).

Covers: upsert idempotency (no duplicates), replace semantics, path update
after a move, lexical search, and an unavailable Elasticsearch.
"""
import asyncio
import json
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from mcp_es.store import ExcerptStore
from organizer.agent import build_agent
from organizer.cli import Session
from organizer.history import validate_history
from organizer.mcp_client import MCPConnection
from organizer.results import run_tool
from organizer.testing import ScriptedChatModel, call
from organizer.tools_catalog import CatalogTools
from organizer.tracing import Tracer

from .conftest import es_available

pytestmark = pytest.mark.skipif(not es_available(), reason="Elasticsearch is not running on localhost:9200")


def _text(msg_content) -> dict:
    if isinstance(msg_content, list):
        msg_content = "".join(b.get("text", "") for b in msg_content if isinstance(b, dict))
    return json.loads(msg_content)


@pytest.fixture
def clean_index(settings):
    st = ExcerptStore(settings.es_url, settings.es_index)
    st.drop_index()
    yield st
    st.drop_index()


async def _with_tools(settings, fn):
    conn = MCPConnection(settings)
    tools = await conn.__aenter__()
    try:
        assert conn.available, conn.status
        return await fn({t.name: t for t in tools})
    finally:
        await conn.__aexit__(None, None, None)


def test_mcp_upsert_idempotent_search_and_path_update(settings, fs, ws, catalog, clean_index):
    d = json.loads(run_tool("move", fs.move, "input/ir_lecture_inverted_index.md", "data/courses/"))["data"]
    recs = CatalogTools(ws, catalog).build_excerpts(
        d["document_id"], [{"start_line": 5, "end_line": 8}, {"start_line": 17, "end_line": 20}])["excerpts"]

    async def scenario(t):
        r1 = _text(await t["upsert_excerpts"].ainvoke({"excerpts": recs}))
        r2 = _text(await t["upsert_excerpts"].ainvoke({"excerpts": recs}))
        stats = _text(await t["index_stats"].ainvoke({}))
        found = _text(await t["search_excerpts"].ainvoke({"query": "инвертированные индексы", "size": 5}))
        upd = _text(await t["update_document_path"].ainvoke(
            {"document_id": d["document_id"], "new_path": "data/search/ir_lecture_inverted_index.md"}))
        after = _text(await t["get_document_excerpts"].ainvoke({"document_id": d["document_id"]}))
        # replace mode drops ranges no longer selected
        r3 = _text(await t["upsert_excerpts"].ainvoke({"excerpts": recs[:1]}))
        return r1, r2, stats, found, upd, after, r3

    r1, r2, stats, found, upd, after, r3 = asyncio.run(_with_tools(settings, scenario))
    assert r1["ok"] and r1["created"] == 2
    assert r2["created"] == 0 and r2["updated"] == 2           # no duplicates on repeat
    assert stats["total_excerpts"] == 2
    hit = found["hits"][0]                                      # russian stemming: индексы -> индекс
    assert hit["document_id"] == d["document_id"] and hit["start_line"] == 5 and "Инвертированный" in hit["text"]
    assert upd["updated"] == 2
    assert {e["current_path"] for e in after["excerpts"]} == {"data/search/ir_lecture_inverted_index.md"}
    assert r3["deleted_stale"] == 1


def test_update_path_without_category_follows_folder(settings, fs, ws, catalog, clean_index):
    """Regression (audit finding 1): update_document_path called WITHOUT category after a move
    used to leave the old category in ES (catalog: projects, index: courses)."""
    d = json.loads(run_tool("move", fs.move, "input/search_quality_thoughts.md", "data/courses/"))["data"]
    recs = CatalogTools(ws, catalog).build_excerpts(d["document_id"], [{"start_line": 1, "end_line": 3}])["excerpts"]
    assert recs[0]["category"] == "courses"
    moved = json.loads(run_tool("move", fs.move, "data/courses/search_quality_thoughts.md", "data/projects/"))["data"]

    async def scenario(t):
        await t["upsert_excerpts"].ainvoke({"excerpts": recs})
        upd = _text(await t["update_document_path"].ainvoke(
            {"document_id": d["document_id"], "new_path": moved["new_path"]}))          # no category
        after = _text(await t["get_document_excerpts"].ainvoke({"document_id": d["document_id"]}))
        wrong = _text(await t["update_document_path"].ainvoke(
            {"document_id": d["document_id"], "new_path": moved["new_path"], "category": "courses"}))
        after2 = _text(await t["get_document_excerpts"].ainvoke({"document_id": d["document_id"]}))
        return upd, after, wrong, after2

    upd, after, wrong, after2 = asyncio.run(_with_tools(settings, scenario))
    assert upd["ok"] and upd["category"] == "projects"
    assert {(e["current_path"], e["category"]) for e in after["excerpts"]} == {
        ("data/projects/search_quality_thoughts.md", "projects")}
    assert wrong["category"] == "projects" and "category_note" in wrong          # folder wins over a wrong argument
    assert {e["category"] for e in after2["excerpts"]} == {"projects"}
    assert catalog.get(d["document_id"])["category"] == "projects"


def test_mcp_reports_unavailable_elasticsearch(settings, clean_index):
    bad = replace(settings, es_url="http://127.0.0.1:9", es_timeout_s=1.0)

    async def scenario(t):
        return _text(await t["search_excerpts"].ainvoke({"query": "x"}))
    r = asyncio.run(_with_tools(bad, scenario))
    assert r["ok"] is False and r["error"]["type"] in ("es_unavailable", "es_error")


def test_end_to_end_scripted_agent_with_real_mcp(settings, clean_index):
    """Scripted (non-LLM) run through the real graph + MCP: organize, index, re-index, move, search."""
    def build_step(doc_path, ranges, cid):
        def step(messages):
            doc = next(d for d in bundle.catalog.load()["documents"] if d["current_path"] == doc_path)
            return AIMessage(content="", tool_calls=[call("build_excerpts", {"document_id": doc["document_id"], "ranges": ranges}, cid)])
        return step

    def upsert_from_last(cid):
        def step(messages):
            last = [m for m in messages if getattr(m, "name", None) == "build_excerpts"][-1]
            recs = json.loads(last.content)["data"]["excerpts"]
            return AIMessage(content="", tool_calls=[call("upsert_excerpts", {"excerpts": recs}, cid)])
        return step

    def sync_step(messages):
        moved = [m for m in messages if getattr(m, "name", None) == "move"][-1]
        d = json.loads(moved.content)["data"]
        return AIMessage(content="", tool_calls=[call("update_document_path",
                                                      {"document_id": d["document_id"], "new_path": d["new_path"]}, "sync")])

    steps = [
        AIMessage(content="", tool_calls=[
            call("move", {"source": "input/ir_lecture_inverted_index.md", "destination": "data/courses/"}, "m1"),
            call("move", {"source": "input/project_docsearch.md", "destination": "data/projects/"}, "m2"),
            call("move", {"source": "input/howto_elasticsearch_docker.md", "destination": "data/howto/"}, "m3")]),
        AIMessage(content="Разложил."),
        build_step("data/courses/ir_lecture_inverted_index.md", [{"start_line": 5, "end_line": 8}], "b1"),
        upsert_from_last("u1"),
        build_step("data/projects/project_docsearch.md", [{"start_line": 6, "end_line": 10}], "b2"),
        upsert_from_last("u2"),
        build_step("data/howto/howto_elasticsearch_docker.md", [{"start_line": 3, "end_line": 6}], "b3"),
        upsert_from_last("u3"),
        AIMessage(content="Проиндексировал 3 документа."),
        build_step("data/courses/ir_lecture_inverted_index.md", None, "b4"),   # re-index, reuse ranges
        upsert_from_last("u4"),
        AIMessage(content="Повтор без дублей."),
        AIMessage(content="", tool_calls=[call("move", {"source": "data/courses/ir_lecture_inverted_index.md",
                                                        "destination": "data/search/"}, "m4")]),
        sync_step,
        AIMessage(content="Перенёс и синхронизировал."),
        AIMessage(content="", tool_calls=[call("search_excerpts", {"query": "индексы"}, "s1")]),
        AIMessage(content="Нашёл."),
    ]

    async def run():
        nonlocal bundle
        conn = MCPConnection(settings)
        tools = await conn.__aenter__()
        try:
            bundle = build_agent(settings, ScriptedChatModel(steps=steps), tools)
            sess = Session(settings, bundle, Tracer(settings.traces_dir, "e2e"), verbose=False)
            for q in ["разложи", "проиндексируй три", "повтори индексацию", "перенеси", "найди про индексы"]:
                await sess.run_turn(q)
            return await sess.messages()
        finally:
            await conn.__aexit__(None, None, None)

    bundle = None
    msgs = asyncio.run(run())
    assert validate_history(msgs) == []
    docs = {d["current_path"]: d for d in bundle.catalog.load()["documents"]}
    assert docs["data/search/ir_lecture_inverted_index.md"]["indexing_status"] == "indexed"
    assert sum(d["indexing_status"] == "indexed" for d in docs.values()) == 3
    st = ExcerptStore(settings.es_url, settings.es_index)
    assert st.count() == 3                                         # re-index created no duplicates
    search = [m for m in msgs if getattr(m, "name", None) == "search_excerpts"][-1]
    hits = _text(search.content)["hits"]
    assert hits[0]["current_path"] == "data/search/ir_lecture_inverted_index.md"
    # the scripted sync call passes no category: the index must still follow the new folder
    assert hits[0]["category"] == "search"
    assert {e["category"] for e in st.get_document(hits[0]["document_id"])["excerpts"]} == {"search"}
