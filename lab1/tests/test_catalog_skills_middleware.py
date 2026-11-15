"""Catalog/excerpt building, skills registry and CatalogSync/ToolGuard middleware."""
import asyncio
import json

import pytest
from langchain_core.messages import ToolMessage

from organizer.config import PROJECT_ROOT
from organizer.middleware import CatalogSyncMiddleware, ToolGuardMiddleware
from organizer.results import run_tool
from organizer.skills import SkillRegistry
from organizer.tools_catalog import CatalogTools


def mv(fs, src, dst):
    return json.loads(run_tool("move", fs.move, src, dst))["data"]


def test_build_excerpts_exact_and_stable(fs, ws, catalog):
    doc = mv(fs, "input/ir_lecture_inverted_index.md", "data/courses/")
    ct = CatalogTools(ws, catalog)
    a = ct.build_excerpts(doc["document_id"], [{"start_line": 5, "end_line": 7, "summary": "def"}])
    ex = a["excerpts"][0]
    lines = (ws.data / "courses" / "ir_lecture_inverted_index.md").read_text(encoding="utf-8").splitlines()
    assert ex["text"] == "\n".join(lines[4:7]) and ex["current_path"] == "data/courses/ir_lecture_inverted_index.md"
    b = ct.build_excerpts(doc["document_id"], [{"start_line": 5, "end_line": 7}])
    assert b["excerpts"][0]["excerpt_id"] == ex["excerpt_id"]


def test_build_excerpts_errors(fs, ws, catalog):
    ct = CatalogTools(ws, catalog)
    e = mv(fs, "input/empty_todo.txt", "data/_unsorted/")
    r = json.loads(run_tool("build_excerpts", ct.build_excerpts, e["document_id"], [{"start_line": 1, "end_line": 1}]))
    assert r["error"]["type"] == "empty_document"
    d = mv(fs, "input/project_recsys.md", "data/projects/")
    r = json.loads(run_tool("build_excerpts", ct.build_excerpts, d["document_id"], [{"start_line": 3, "end_line": 999}]))
    assert r["error"]["type"] == "invalid_range"
    r = json.loads(run_tool("build_excerpts", ct.build_excerpts, d["document_id"], None))
    assert r["error"]["type"] == "no_ranges"


def test_skills_metadata_and_on_demand_load():
    reg = SkillRegistry(PROJECT_ROOT / "skills")
    assert set(reg.skills) == {"organize_files", "index_excerpts"}
    block = reg.metadata_block()
    assert "organize_files:" in block and "# organize_files — procedure" not in block  # body not in prompt
    body = reg.load("index_excerpts")["instructions"]
    assert "build_excerpts" in body and reg.loaded == ["index_excerpts"]
    with pytest.raises(Exception):
        reg.load("unknown")


class Req:
    def __init__(self, name, args, cid="t1"):
        self.tool_call = {"name": name, "args": args, "id": cid}
        self.tool = None
        self.state = {}
        self.runtime = None


def ok_msg(req, payload):
    return ToolMessage(content=json.dumps(payload), tool_call_id=req.tool_call["id"], name=req.tool_call["name"])


def test_catalog_sync_validates_and_records(fs, ws, catalog):
    d = mv(fs, "input/project_docsearch.md", "data/projects/")
    recs = CatalogTools(ws, catalog).build_excerpts(d["document_id"], [{"start_line": 1, "end_line": 4}])["excerpts"]
    mw = CatalogSyncMiddleware(ws, catalog)
    called = []

    async def handler(req):
        called.append(req)
        return ok_msg(req, {"ok": True, "created": 1})

    # tampered text is rejected before reaching MCP
    bad = [dict(recs[0], text="выдуманный текст")]
    res = asyncio.run(mw.awrap_tool_call(Req("upsert_excerpts", {"excerpts": bad}), handler))
    assert res.status == "error" and "excerpt_validation_failed" in res.content and not called
    # valid -> indexed
    asyncio.run(mw.awrap_tool_call(Req("upsert_excerpts", {"excerpts": recs}), handler))
    assert catalog.get(d["document_id"])["indexing_status"] == "indexed"
    # move indexed document -> pending_sync; failed sync keeps pending; successful sync -> indexed
    mv(fs, "data/projects/project_docsearch.md", "data/search/")
    assert catalog.get(d["document_id"])["indexing_status"] == "pending_sync"

    async def failing(req):
        return ok_msg(req, {"ok": False, "error": {"type": "es_unavailable", "message": "down"}})
    asyncio.run(mw.awrap_tool_call(Req("update_document_path", {"document_id": d["document_id"],
                                                                "new_path": "data/search/project_docsearch.md"}), failing))
    assert catalog.get(d["document_id"])["indexing_status"] == "pending_sync"
    asyncio.run(mw.awrap_tool_call(Req("update_document_path", {"document_id": d["document_id"],
                                                                "new_path": "data/search/project_docsearch.md"}), handler))
    assert catalog.get(d["document_id"])["indexing_status"] == "indexed"


def test_catalog_sync_upsert_failure_marks_error(fs, ws, catalog):
    d = mv(fs, "input/project_recsys.md", "data/projects/")
    recs = CatalogTools(ws, catalog).build_excerpts(d["document_id"], [{"start_line": 1, "end_line": 2}])["excerpts"]

    async def boom(req):
        raise ConnectionError("MCP server died")
    res = asyncio.run(CatalogSyncMiddleware(ws, catalog).awrap_tool_call(Req("upsert_excerpts", {"excerpts": recs}), boom))
    assert res.status == "error"
    assert catalog.get(d["document_id"])["indexing_status"] == "error"


def test_tool_guard_timeout_exception_and_cap():
    g = ToolGuardMiddleware(timeout_s=0.2, max_chars=50)

    async def slow(req):
        await asyncio.sleep(1)

    async def crash(req):
        raise RuntimeError("bad")

    async def big(req):
        return ok_msg(req, {"x": "y" * 500})
    assert json.loads(asyncio.run(g.awrap_tool_call(Req("t", {}), slow)).content)["error"]["type"] == "timeout"
    assert json.loads(asyncio.run(g.awrap_tool_call(Req("t", {}), crash)).content)["error"]["type"] == "tool_exception"
    assert "truncated by harness" in asyncio.run(g.awrap_tool_call(Req("t", {}), big)).content
