"""CLI script mode: files added to input/ from ANOTHER process during an open session,
execution-limit marking in the trace, and the default limit relationship.

No LLM: the CLI runs with --dry-run-model / ScriptedChatModel.
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from langchain_core.messages import AIMessage

from organizer.agent import build_agent
from organizer.cli import Session, wait_for_files
from organizer.config import PROJECT_ROOT, Settings
from organizer.history import validate_history
from organizer.mcp_client import unavailable_tools
from organizer.testing import ScriptedChatModel, call
from organizer.tracing import Tracer

LATE = sorted((PROJECT_ROOT / "fixtures" / "late").glob("*"))


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_wait_for_files_sees_files_added_by_another_process(settings, tmp_path):
    marker = tmp_path / "waiting.json"
    tracer = Tracer(settings.traces_dir, "wait_test")
    names = [f"input/{p.name}" for p in LATE]
    # a separate OS process copies the files as soon as the CLI says it is waiting
    copier = subprocess.Popen([sys.executable, "-c", (
        "import shutil, sys, time, pathlib\n"
        "marker, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])\n"
        "while not marker.exists(): time.sleep(0.1)\n"
        "for src in sys.argv[3:]: shutil.copy2(src, dst)\n"),
        str(marker), str(settings.workspace / "input"), *map(str, LATE)])
    try:
        appeared = asyncio.run(wait_for_files(settings, tracer, names, timeout_s=30, marker=marker, poll_s=0.2))
    finally:
        copier.wait(timeout=30)
    assert appeared == sorted(names)
    assert not marker.exists()
    ev = [e for e in _events(tracer.jsonl) if e["event"] == "user_action"][-1]
    assert ev["action"] == "external_files_added" and ev["files"] == sorted(names) and not ev["timed_out"]


def test_wait_for_files_copies_nothing_itself(settings):
    tracer = Tracer(settings.traces_dir, "wait_timeout")
    appeared = asyncio.run(wait_for_files(settings, tracer, ["input/never.md"], timeout_s=0.5, poll_s=0.1))
    assert appeared == [] and not (settings.workspace / "input" / "never.md").exists()
    ev = [e for e in _events(tracer.jsonl) if e["event"] == "user_action"][-1]
    assert ev["timed_out"] is True


def test_cli_script_with_external_late_files_and_loop_limit(tmp_path):
    """Whole CLI process in --script mode: pause, files added from outside, real ls afterwards,
    a looping turn stopped by the model-call limit, /check and /exit."""
    ws = tmp_path / "workspace"
    (ws / "data").mkdir(parents=True)
    shutil.copytree(PROJECT_ROOT / "fixtures" / "input", ws / "input")
    traces = tmp_path / "traces"
    marker = tmp_path / "waiting.json"
    script = tmp_path / "dialog.txt"
    script.write_text("\n".join([
        "# comment line",
        "Посмотри input.",
        "@wait_for_files " + " ".join(f"input/{p.name}" for p in LATE),
        "Я добавил файлы, разбери новые.",
        "Проверяй ls input снова и снова, пока он не появится.",
        "/check",
        "/exit",
    ]), encoding="utf-8")
    env = {**os.environ, "WORKSPACE_DIR": str(ws), "TRACES_DIR": str(traces),
           "AGENT_MODEL_CALLS_PER_TURN": "6", "AGENT_RECURSION_LIMIT": "110", "PYTHONIOENCODING": "utf-8"}
    cli = subprocess.Popen([sys.executable, "-m", "organizer.cli", "--script", str(script), "--no-mcp",
                            "--dry-run-model", "--quiet", "--wait-marker", str(marker), "--wait-timeout", "60"],
                           cwd=PROJECT_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 60
    while not marker.exists() and cli.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert marker.exists(), "CLI never reached @wait_for_files"
    for p in LATE:                       # the "user": this test process, not the CLI
        shutil.copy2(p, ws / "input" / p.name)
    out, _ = cli.communicate(timeout=120)
    assert cli.returncode == 0, out.decode("utf-8", "replace")
    trace = next(traces.glob("session_*.jsonl"))
    ev = _events(trace)
    actions = [e for e in ev if e["event"] == "user_action"]
    assert actions[0]["action"] == "external_files_added"
    assert sorted(actions[0]["files"]) == sorted(f"input/{p.name}" for p in LATE)
    turns = [e for e in ev if e["event"] == "turn_end"]
    assert len(turns) == 3 and all(not t["history_problems"] for t in turns)
    # the turn after the pause listed input again and saw the new files
    ls_after = [e for e in ev if e["event"] == "graph_step" and e["run_id"] == turns[1]["run_id"]
                and any(m.get("name") == "ls" for m in e["messages"])]
    assert any(p.name in m["content"] for e in ls_after for m in e["messages"] for p in LATE)
    assert turns[2]["stopped_by"] == "model_call_limit" and "лимитом выполнения" in turns[2]["answer"]
    assert ev[0]["model"].startswith("dry-run-model")
    # CLI commands and script directives are recorded in the trace, and so is how the session ended
    cmds = [e for e in ev if e["event"] == "cli_command"]
    assert [c["command"].split()[0] for c in cmds] == ["@wait_for_files", "/check", "/exit"]
    assert cmds[0]["ok"] and len(cmds[0]["result"]["appeared"]) == 2
    assert cmds[1]["result"]["history_ok"] is True and cmds[1]["result"]["messages"] > 0
    assert cmds[2]["result"] == "exit"
    end = [e for e in ev if e["event"] == "session_end"][-1]
    assert end["ended_by"] == "exit_command (/exit)" and end["user_turns"] == 3
    md = trace.with_suffix(".md").read_text(encoding="utf-8")
    assert "**CLI command** `/check`" in md


def _run_cli(tmp_path, lines, extra_env=None, extra_args=()):
    ws = tmp_path / "workspace"
    if not ws.exists():
        (ws / "data").mkdir(parents=True)
        shutil.copytree(PROJECT_ROOT / "fixtures" / "input", ws / "input")
    traces = tmp_path / "traces"
    script = tmp_path / "dialog.txt"
    script.write_text("\n".join(lines), encoding="utf-8")
    env = {**os.environ, "WORKSPACE_DIR": str(ws), "TRACES_DIR": str(traces), "PYTHONIOENCODING": "utf-8",
           **(extra_env or {})}
    r = subprocess.run([sys.executable, "-m", "organizer.cli", "--script", str(script), "--dry-run-model", "--quiet",
                        *extra_args], cwd=PROJECT_ROOT, env=env, capture_output=True, timeout=180)
    assert r.returncode == 0, r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
    return _events(next(traces.glob("session_*.jsonl")))


def test_cli_commands_traced_and_end_of_script(tmp_path):
    ev = _run_cli(tmp_path, ["/history", "/catalog", "/graph", "/help", "/nonsense", "Посмотри input."],
                  extra_args=["--no-mcp"])
    cmds = {e["command"]: e for e in ev if e["event"] == "cli_command"}
    assert set(cmds) == {"/history", "/catalog", "/graph", "/help", "/nonsense"}
    assert "messages" in json.dumps(cmds["/history"]["result"])
    assert cmds["/catalog"]["result"]["documents"] == 0
    assert cmds["/graph"]["result"]["mermaid_chars"] > 0
    assert cmds["/nonsense"]["ok"] is False
    end = [e for e in ev if e["event"] == "session_end"][-1]
    assert end["ended_by"] == "end_of_script" and end["user_turns"] == 1


def test_mcp_server_unavailable_session(tmp_path):
    """MCP server cannot start (bad MCP_COMMAND, as in the demo session mcp_down): index tools answer
    mcp_unavailable, the catalog records `error`, a move still works."""
    from organizer.config import load_settings
    from organizer.mcp_client import MCPConnection
    ws = tmp_path / "workspace"
    (ws / "data").mkdir(parents=True)
    shutil.copytree(PROJECT_ROOT / "fixtures" / "input", ws / "input")
    s = replace(load_settings(), workspace=ws, traces_dir=tmp_path / "traces",
                mcp_command="lab1-mcp-server-does-not-exist.exe")

    def build(messages):
        doc = next(d for d in bundle.catalog.load()["documents"] if d["current_path"].endswith("howto_git_branches.txt"))
        return AIMessage(content="", tool_calls=[call("build_excerpts", {"document_id": doc["document_id"],
                                                                         "ranges": [{"start_line": 1, "end_line": 3}]}, "b")])

    def upsert(messages):
        last = [m for m in messages if getattr(m, "name", None) == "build_excerpts"][-1]
        return AIMessage(content="", tool_calls=[call("upsert_excerpts",
                                                      {"excerpts": json.loads(last.content)["data"]["excerpts"]}, "u")])
    steps = [AIMessage(content="", tool_calls=[call("move", {"source": "input/howto_git_branches.txt",
                                                             "destination": "data/howto/"}, "m")]),
             AIMessage(content="Разложил."), build, upsert, AIMessage(content="MCP недоступен, индексация не выполнена."),
             AIMessage(content="", tool_calls=[call("move", {"source": "data/howto/howto_git_branches.txt",
                                                             "destination": "data/guides/"}, "m2")]),
             AIMessage(content="Перенёс.")]
    bundle = None

    async def run():
        nonlocal bundle
        conn = MCPConnection(s)
        tools = await conn.__aenter__()
        try:
            assert not conn.available and conn.status.startswith("MCP unavailable")
            bundle = build_agent(s, ScriptedChatModel(steps=steps), tools)
            sess = Session(s, bundle, Tracer(s.traces_dir, "mcp_down"), verbose=False)
            for q in ["разложи", "положи в Elastic", "перенеси в guides"]:
                await sess.run_turn(q)
            return await sess.messages()
        finally:
            await conn.__aexit__(None, None, None)
    msgs = asyncio.run(run())
    up = [m for m in msgs if getattr(m, "name", None) == "upsert_excerpts"][-1]
    assert json.loads(up.content)["error"]["type"] == "mcp_unavailable"
    doc = next(d for d in bundle.catalog.load()["documents"] if d["original_path"] == "input/howto_git_branches.txt")
    assert doc["indexing_status"] == "error" and "upsert failed" in doc["index_error"]
    assert doc["current_path"] == "data/guides/howto_git_branches.txt" and (ws / doc["current_path"]).is_file()
    assert validate_history(msgs) == []


def test_http_fixture_server_for_web_fetch_demo():
    """scripts/http_fixture_server.py (started by run_all_demos.ps1 for the guard session):
    200 HTML page, 404 -> http_error, slow answer -> timeout."""
    import socket
    from organizer.results import run_tool
    from organizer.tools_web import WebTools
    with socket.socket() as sck:
        sck.bind(("127.0.0.1", 0))
        port = sck.getsockname()[1]
    srv = subprocess.Popen([sys.executable, str(PROJECT_ROOT / "scripts" / "http_fixture_server.py"),
                            "--port", str(port), "--slow", "3"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        assert b"fixture server" in srv.stdout.readline()
        w = WebTools(timeout_s=1.0, max_bytes=100_000, max_chars=5000)
        base = f"http://127.0.0.1:{port}"
        page = json.loads(run_tool("web_fetch", w.web_fetch, f"{base}/page.html"))
        assert page["ok"] and page["data"]["status"] == 200 and "DocSearch" in page["data"]["title"]
        assert "console.log" not in page["data"]["text"]
        missing = json.loads(run_tool("web_fetch", w.web_fetch, f"{base}/missing"))
        assert missing["error"]["type"] == "http_error" and missing["error"]["status"] == 404
        slow = json.loads(run_tool("web_fetch", w.web_fetch, f"{base}/slow"))
        assert slow["error"]["type"] == "timeout"
    finally:
        srv.kill()
        srv.wait(timeout=10)


def test_model_call_limit_marked_in_trace(settings):
    steps = [AIMessage(content="", tool_calls=[call("ls", {"path": "input"}, "loop")])]
    s = replace(settings, model_calls_per_turn=4, recursion_limit=110)
    bundle = build_agent(s, ScriptedChatModel(steps=steps, loop_last=True), unavailable_tools("offline"))
    sess = Session(s, bundle, Tracer(s.traces_dir, "limit_mark"), verbose=False)
    ans = asyncio.run(sess.run_turn("зациклись"))
    end = [e for e in _events(sess.tracer.jsonl) if e["event"] == "turn_end"][-1]
    assert end["stopped_by"] == "model_call_limit" and end["error"] is None
    assert "model_calls_per_turn=4" in ans
    assert validate_history(asyncio.run(sess.messages())) == []


def test_default_limits_let_model_call_limit_fire_first(settings):
    """One model call = 4 super-steps; the default recursion_limit is a backstop above 4 * calls,
    so a real loop ends gracefully via ModelCallLimitMiddleware, not GraphRecursionError."""
    d = Settings()
    assert d.recursion_limit > 4 * d.model_calls_per_turn
    s = replace(settings, model_calls_per_turn=d.model_calls_per_turn, recursion_limit=d.recursion_limit)
    model = ScriptedChatModel(steps=[AIMessage(content="", tool_calls=[call("ls", {"path": "input"}, "l")])],
                              loop_last=True)
    sess = Session(s, build_agent(s, model, unavailable_tools("offline")), Tracer(s.traces_dir, "defaults"),
                   verbose=False)
    asyncio.run(sess.run_turn("зациклись"))
    end = [e for e in _events(sess.tracer.jsonl) if e["event"] == "turn_end"][-1]
    assert end["stopped_by"] == "model_call_limit" and model.calls == d.model_calls_per_turn
