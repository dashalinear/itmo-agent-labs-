"""Process crash + resume in a new process, idempotency, token counting/window, chunked compaction, MCP wrapper."""
import json
import os
import subprocess
import sys
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import HANDBOOK, LAB2, es_available
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from policy import PolicyModel, SummaryModel, tc

from harness.agent import build_baseline_agent, build_harness_agent
from harness.middleware import CompactionMiddleware
from harness.store import TaskStore
from harness.tokens import TokenCounter, detect_window


def _run_helper(cfg, base, crash_at, create=False):
    args = [sys.executable, str(LAB2 / "tests" / "crash_task.py"), str(cfg.root), str(cfg.results_dir), str(cfg.traces_dir),
            base, crash_at] + (["create"] if create else [])
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=180,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def test_crash_after_effects_then_resume_in_new_process(cfg, web):
    base, handler = web
    page_hits = lambda: sum(1 for h in handler.hits if h.startswith("/p/checkpoint-1"))  # noqa: E731
    before_hits = page_hits()
    p1 = _run_helper(cfg, base, "after_tool:4", create=True)
    assert p1.returncode == 137, p1.stdout + p1.stderr            # killed right after the 4th tool finished
    s = TaskStore(cfg.root / ".harness" / "state.db")
    assert s.get_task("R")["status"] == "running"                 # died mid-run: not completed, not paused
    assert len(s.evidence("R")) == 1 and page_hits() == before_hits + 1
    usage1 = s.usage("R")
    s.close()

    p2 = _run_helper(cfg, base, "")                               # new process, same task_id, no context re-entry
    assert p2.returncode == 0 and "STATUS completed" in p2.stdout, p2.stdout + p2.stderr
    s = TaskStore(cfg.root / ".harness" / "state.db")
    try:
        # the interrupted tools step was re-executed: idempotency keys prevented duplicates
        assert len(s.evidence("R")) == 1
        assert page_hits() == before_hits + 1                      # the stored page was not downloaded again
        u = s.usage("R")
        assert u["cache_hits"] >= 1 and u["http_requests"] == usage1["http_requests"]
        assert u["model_calls"] > usage1["model_calls"] and u["tool_calls"] > usage1["tool_calls"]
        assert s.get_task("R")["runs"] == 2
        start = [e for e in s.events("R") if e["kind"] == "status" and e["data"]["status"] == "running"]
        assert len(start) == 2
        traces = sorted(cfg.traces_dir.glob("R_resume*.jsonl"))
        info = json.loads(traces[-1].read_text(encoding="utf-8").splitlines()[0])
        assert info["resume_kind"] == "continue_interrupted_step"
    finally:
        s.close()


def test_token_counter_calibration_and_server_window():
    c = TokenCounter(chars_per_token=2.0, margin=0.0, mode="heuristic")
    e1 = c.estimate("system " * 100, [HumanMessage(content="привет " * 200)])
    c.observe(e1.chars, e1.chars // 4)          # server says text is denser in chars per token
    e2 = c.estimate("system " * 100, [HumanMessage(content="привет " * 200)])
    assert e2.tokens < e1.tokens and c.calibrations == 1

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._json({"data": [{"id": "qwen-test", "max_model_len": 40960}]})

        def do_POST(self):
            n = int(self.headers["Content-Length"])
            payload = json.loads(self.rfile.read(n))
            assert payload["tools"] and payload["messages"][0]["role"] == "system"
            self._json({"count": 1234, "max_model_len": 40960})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    try:
        assert detect_window(base, "k", "qwen-test") == (40960, "GET /models -> max_model_len")
        sc = TokenCounter(mode="auto", base_url=base, api_key="k", model="qwen-test")
        from langchain_core.tools import tool

        @tool
        def ping(x: str) -> str:
            """ping"""
            return x
        est = sc.estimate("sys", [HumanMessage(content="hi")], [ping])
        assert est.method == "server_tokenize" and est.tokens == int(1234 * 1.02 + 0.999) and sc.server_window == 40960
    finally:
        srv.shutdown()


async def test_chunked_compaction_respects_window_and_budget(cfg, store):
    from harness.config import Limits
    store.update_task("T", limits=Limits().updated({"working_context_tokens": 12000, "summary_max_tokens": 600, "summary_tool_clip_chars": 6000,
                                                    "max_compaction_chunks": 3}).to_dict())
    summ = SummaryModel()
    b = build_harness_agent(cfg, store, "T", PolicyModel(plan=[]), summ, [], InMemorySaver(), 32768, "test")
    mw = CompactionMiddleware(b.ctx)
    msgs = []
    for i in range(12):
        msgs.append(AIMessage(content="", tool_calls=[{"name": "read_pages", "args": {"p": i}, "id": f"id{i}", "type": "tool_call"}]))
        msgs.append(ToolMessage(content="текст страницы " * 400, tool_call_id=f"id{i}", name="read_pages"))
    res = await mw._summarize("", msgs)
    assert res["chunks"] >= 2 and res["method"].startswith("llm")
    for call in store.model_calls("T"):
        assert call["estimated_input"] + call["reserve"] <= call["working_limit"]
    assert store.usage("T")["compaction_calls"] == res["chunks"]
    # no budget left -> deterministic rule-based summary, no LLM call
    store.update_task("T", limits=Limits().updated({"max_total_tokens": 10}).to_dict())
    b2 = build_harness_agent(cfg, store, "T", PolicyModel(plan=[]), summ, [], InMemorySaver(), 32768, "test")
    calls_before = store.usage("T")["compaction_calls"]
    r2 = await CompactionMiddleware(b2.ctx)._summarize("prev", msgs)
    assert r2["method"] == "rules(budget)" and store.usage("T")["compaction_calls"] == calls_before
    assert "вызов read_pages" in r2["summary"]


async def test_baseline_agent_is_lab1_agent_with_meter(cfg):
    model = PolicyModel(plan=[[tc("read", {"path": HANDBOOK})], "ok"])
    agent, meter, tools = build_baseline_agent(cfg, model, [], InMemorySaver(), cfg.root, 5,
                                               TokenCounter(mode="heuristic"))
    out = await agent.ainvoke({"messages": [HumanMessage(content="x")]}, {"configurable": {"thread_id": "b"}})
    tm = next(m for m in out["messages"] if isinstance(m, ToolMessage))
    assert "truncated by harness" in str(tm.content)              # lab 1 behaviour: plain truncation, no artifact
    assert len(meter.calls) == 2 and {t.name for t in tools} >= {"read", "grep", "web_fetch"}


@pytest.mark.skipif(not es_available(), reason="Elasticsearch is not running on localhost:9200")
async def test_mcp_outputs_are_bounded_and_offloaded(cfg, web):
    from mcp_es.schema import Excerpt, excerpt_key
    from mcp_es.store import ExcerptStore
    from organizer.mcp_client import MCPConnection
    from harness.runner import TaskRunner
    index = "itmo_lab2_test_mcp"
    st = ExcerptStore(cfg.base.es_url, index)
    st.drop_index()
    st.ensure_index()
    recs = []
    for i in range(1, 41):
        text = f"Фрагмент {i}: поиск, индекс и ранжирование BM25. " * 6
        recs.append(Excerpt(excerpt_id=excerpt_key("doc", "sha256:x", i, i), document_id="doc", current_path="data/x.md",
                            title="Тест", category="search", text=text, start_line=i, end_line=i, content_hash="sha256:x"))
    st.upsert(recs, mode="merge")
    st.es.indices.refresh(index=index)
    c = replace(cfg, base=replace(cfg.base, es_index=index))
    model = PolicyModel(plan=[[tc("search_excerpts", {"query": "поиск индекс", "size": 40})], "ok"])
    try:
        async with TaskRunner(c, verbose=False, use_mcp=True, model=model, summarizer=None) as r:
            assert "connected" in r.mcp_status
            r.create({"task_id": "M", "kind": "local", "request": "x", "constraints": []})
            await r.run("M")
            off = [e for e in r.store.events("M") if e["kind"] == "tool_output_offloaded"]
            assert off and off[0]["data"]["tool"] == "search_excerpts"
            assert off[0]["data"]["passed_chars"] < off[0]["data"]["total_chars"]
    finally:
        st.drop_index()
