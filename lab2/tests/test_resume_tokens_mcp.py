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


def _run_helper(cfg, base, crash_at, create=False, *flags):
    args = [sys.executable, str(LAB2 / "tests" / "crash_task.py"), str(cfg.root), str(cfg.results_dir), str(cfg.traces_dir),
            base, crash_at] + (["create"] if create else []) + list(flags)
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=180,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def _trace_rows(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]


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


def test_crash_windows_download_fetch_evidence_in_four_processes(cfg, web):
    """Three real crash windows in three processes, then a clean resume (the I1 scenario of run_final_lab2.ps1):
    after_download (page downloaded, not stored) -> URL re-queued and fetched again (counted);
    after_tool:web_fetch (page stored, step checkpoint missing) -> re-executed as a cache hit, HTTP unchanged;
    after_tool:save_evidence (evidence stored) -> re-executed as already_saved; no duplicates in the end."""
    base, handler = web
    hits = lambda: {s: sum(1 for h in handler.hits if h.startswith(f"/p/{s}")) for s in ("checkpoint-1", "idempotency-1")}  # noqa: E731
    h0 = hits()
    p1 = _run_helper(cfg, base, "after_download:2", True, "web")
    assert p1.returncode == 137, p1.stdout + p1.stderr
    s = TaskStore(cfg.root / ".harness" / "state.db")
    running = s.urls("R", "running")
    assert len(running) == 1 and s.usage("R")["http_requests"] == 2      # downloaded twice, one page not stored
    s.close()
    p2 = _run_helper(cfg, base, "after_tool:web_fetch:1", False, "web", "resume")
    assert p2.returncode == 137, p2.stdout + p2.stderr
    p3 = _run_helper(cfg, base, "after_tool:save_evidence:1", False, "web", "resume")
    assert p3.returncode == 137, p3.stdout + p3.stderr
    p4 = _run_helper(cfg, base, "", False, "web")
    assert p4.returncode == 0 and "STATUS completed" in p4.stdout, p4.stdout + p4.stderr

    traces = sorted(cfg.traces_dir.glob("R_*.jsonl"), key=lambda p: _trace_rows(p)[0]["ts"])
    assert len(traces) == 4
    t2, t3, t4 = (_trace_rows(t) for t in traces[1:])
    assert t2[0]["recovered"]["urls_requeued"] == 1 and t2[0]["resume_kind"] == "continue_interrupted_step"
    assert any(r["event"] == "harness:simulated_crash" for r in t2)
    hit3 = next(r for r in t3 if r["event"] == "harness:fetch_cache_hit")      # the page stored by process 2
    got = hits()
    s = TaskStore(cfg.root / ".harness" / "state.db")
    try:
        assert len(s.evidence("R")) == 2 and s.get_task("R")["status"] == "completed"
        # only downloads that were killed before the page was stored are repeated (3 or 4 depending on which parallel
        # call finished first); every real request is counted in the HTTP budget, a cache hit never is
        # a request is counted BEFORE it is sent, so a request killed with the process is counted but may not arrive
        assert sum(got.values()) - sum(h0.values()) <= s.usage("R")["http_requests"] <= 5
        cached = next(u for u in s.urls("R") if u["url_key"] == hit3["url_key"])
        assert cached["attempts"] == hit3["url_http_attempts"]                # the cached page was not downloaded again
        assert len({a["artifact_id"] for a in s.artifacts("R") if a["kind"] == "web_page"}) == 2
        assert len({u["canonical"] for u in s.urls("R")}) == len(s.urls("R"))
    finally:
        s.close()
    # the evidence report built from these traces (scripts/idempotency_report.py) sees the same
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "TASK_ROOT": str(cfg.root), "RESULTS_DIR": str(cfg.results_dir),
           "TRACES_DIR": str(cfg.traces_dir), "FIXTURE_BASE_URL": base}
    rep = subprocess.run([sys.executable, str(LAB2 / "scripts" / "idempotency_report.py"), "--task-id", "R"],
                         capture_output=True, text=True, encoding="utf-8", timeout=120, env=env)
    assert rep.returncode == 0, rep.stdout + rep.stderr
    checks = json.loads((cfg.results_dir / "R" / "idempotency.json").read_text(encoding="utf-8"))["checks"]
    assert checks["crashes"] == 3 and checks["urls_requeued_after_crash"] >= 1
    assert checks["cache_hit_in_re_executed_step"] and checks["already_saved_in_re_executed_step"]
    assert checks["cached_pages_not_downloaded_again"]
    assert checks["replay_all_already_saved"] and checks["replay_all_cache_hits_no_http"]
    assert checks["no_duplicate_evidence"] and checks["no_duplicate_queue_entries"]


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


@pytest.mark.parametrize("mode,expected_src", [("tokenize", "POST /tokenize"), ("error", "rejection message")])
def test_window_detected_without_models_field(mode, expected_src):
    """/models without max_model_len (like the ITMO server): /tokenize or the rejection message reveals the window."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send(200, {"data": [{"id": "m", "object": "model"}]})

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/tokenize":
                return self._send(200, {"count": 1, "max_model_len": 65536}) if mode == "tokenize" else self._send(404, {})
            self._send(400, {"error": {"message": "This model's maximum context length is 40960 tokens. However, you "
                                                  "requested 10000009 tokens (9 in the messages, 10000000 in the completion)."}})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        w, src = detect_window(f"http://127.0.0.1:{srv.server_address[1]}/v1", "k", "m")
        assert w == (65536 if mode == "tokenize" else 40960) and src.startswith(expected_src)
    finally:
        srv.shutdown()


@pytest.mark.parametrize("output_cap", [None, 8192])
def test_window_measured_from_server_behaviour(output_cap, tmp_path):
    """Server reveals nothing (no field, no /tokenize, opaque 400): the window is measured from accept/reject."""
    W = 40960

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send(200, {"data": [{"id": "m"}]})

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path != "/v1/chat/completions":
                return self._send(404, {})
            prompt = len(req["messages"][0]["content"].split()) + 12
            mt = req["max_tokens"]
            if prompt + mt > W or (output_cap and mt > output_cap):
                return self._send(400, {"error": {"message": "Bad request", "code": 400}})
            self._send(200, {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": prompt}})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cache = tmp_path / "window.json"
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
        w, src = detect_window(base, "k", "m", cache=cache)
        if output_cap is None:
            assert w == W and src.startswith("measured: max accepted max_tokens")
        else:
            assert W - 64 <= w <= W and "measured by prompt length" in src and "output cap" in src
        w2, src2 = detect_window(base, "k", "m", cache=cache)          # second call: from the cache, no probes
        assert w2 == w and "[cached" in src2
    finally:
        srv.shutdown()
