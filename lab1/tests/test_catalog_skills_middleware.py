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


def test_catalog_sync_checks_category_after_path_update(fs, ws, catalog):
    """Regression (audit finding 1): a successful update_document_path must not mark the document
    `indexed` when the index category differs from the catalog category."""
    d = mv(fs, "input/search_quality_thoughts.md", "data/courses/")
    recs = CatalogTools(ws, catalog).build_excerpts(d["document_id"], [{"start_line": 1, "end_line": 3}])["excerpts"]
    mw = CatalogSyncMiddleware(ws, catalog)

    async def ok_handler(req):
        return ok_msg(req, {"ok": True, "created": 1})
    asyncio.run(mw.awrap_tool_call(Req("upsert_excerpts", {"excerpts": recs}), ok_handler))
    mv(fs, "data/courses/search_quality_thoughts.md", "data/projects/")
    new_path = "data/projects/search_quality_thoughts.md"

    async def stale_category(req):   # e.g. an old server that kept the previous category
        return ok_msg(req, {"ok": True, "updated": 1, "new_path": new_path, "category": "courses"})
    asyncio.run(mw.awrap_tool_call(Req("update_document_path", {"document_id": d["document_id"],
                                                                "new_path": new_path}), stale_category))
    doc = catalog.get(d["document_id"])
    assert doc["indexing_status"] == "pending_sync" and "category" in doc["index_error"]

    async def fixed(req):
        return ok_msg(req, {"ok": True, "updated": 1, "new_path": new_path, "category": "projects"})
    asyncio.run(mw.awrap_tool_call(Req("update_document_path", {"document_id": d["document_id"],
                                                                "new_path": new_path}), fixed))
    assert catalog.get(d["document_id"])["indexing_status"] == "indexed"


def test_store_update_path_derives_category_offline():
    """Regression (audit finding 1), no Elasticsearch needed: the update script sent by the MCP
    server sets category from data/<cat>/ even when the caller omits it."""
    from mcp_es.schema import category_from_path
    from mcp_es.store import ExcerptStore

    class FakeES:
        class indices:  # noqa: N801
            @staticmethod
            def exists(index):
                return True

        def update_by_query(self, **kw):
            self.kw = kw
            return {"updated": 2, "total": 2}

    st = ExcerptStore.__new__(ExcerptStore)
    st.index, st.es = "itmo_lab1_fake", FakeES()
    r = st.update_path("doc", "data/projects/search_quality_thoughts.md")
    assert r["category"] == "projects" and st.es.kw["script"]["params"]["c"] == "projects"
    assert "ctx._source.category" in st.es.kw["script"]["source"]
    r = st.update_path("doc", "data/projects/x.md", category="courses")
    assert r["category"] == "projects" and "category_note" in r
    r = st.update_path("doc", "elsewhere/x.md", category="misc")      # outside data/: explicit value kept
    assert r["category"] == "misc"
    assert category_from_path("data/courses/a/b.md") == "courses" and category_from_path("input/a.md") is None


def test_catalog_sync_rejects_excerpt_with_wrong_category(fs, ws, catalog):
    d = mv(fs, "input/project_docsearch.md", "data/projects/")
    recs = CatalogTools(ws, catalog).build_excerpts(d["document_id"], [{"start_line": 1, "end_line": 4}])["excerpts"]
    called = []

    async def handler(req):
        called.append(req)
        return ok_msg(req, {"ok": True})
    res = asyncio.run(CatalogSyncMiddleware(ws, catalog).awrap_tool_call(
        Req("upsert_excerpts", {"excerpts": [dict(recs[0], category="courses")]}), handler))
    assert res.status == "error" and "category" in res.content and not called


def test_verify_flags_category_drift(fs, ws, catalog):
    """scripts/verify.py: an `indexed` document whose excerpts carry another category is an error."""
    import importlib
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    verify = importlib.import_module("verify")
    d = mv(fs, "input/search_quality_thoughts.md", "data/projects/")
    recs = CatalogTools(ws, catalog).build_excerpts(d["document_id"], [{"start_line": 1, "end_line": 3}])["excerpts"]
    catalog.set_index_state(d["document_id"], "indexed", excerpts=[{"start_line": 1, "end_line": 3}])
    doc = catalog.get(d["document_id"])
    good, problems = verify.check_document_excerpts(doc, recs, ws.root)
    assert good and not problems
    stale = [dict(recs[0], category="courses")]
    bad, problems = verify.check_document_excerpts(doc, stale, ws.root)
    assert not bad and "category courses" in problems[0] and "NOT recorded" in problems[0]
    catalog.set_index_state(d["document_id"], "pending_sync")
    tolerated, _ = verify.check_document_excerpts(catalog.get(d["document_id"]), stale, ws.root)
    assert tolerated   # drift is acceptable only while pending_sync is recorded


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
