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
