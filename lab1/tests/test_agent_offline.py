"""Prebuilt ReAct graph, CLI history handling and execution limits with a scripted model.

No LLM is called: ScriptedChatModel replays tool calls so that the library
graph (model -> tools -> model -> END), history and limits can be checked offline.
"""
import asyncio
import json
from dataclasses import replace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from organizer.agent import build_agent
from organizer.cli import Session
from organizer.history import validate_history
from organizer.mcp_client import unavailable_tools
from organizer.testing import ScriptedChatModel, call
from organizer.tracing import Tracer


def make_session(settings, steps, loop_last=False, **overrides):
    s = replace(settings, **overrides)
    model = ScriptedChatModel(steps=steps, loop_last=loop_last)
    bundle = build_agent(s, model, unavailable_tools("offline test"))
    return Session(s, bundle, Tracer(s.traces_dir, "test_session"), verbose=False), bundle


def test_graph_topology(settings):
    _, b = make_session(settings, [])
    g = b.agent.get_graph()
    nodes = set(g.nodes)
    assert {"model", "tools", "__start__", "__end__"} <= nodes
    edges = {(e.source, e.target) for e in g.edges}
    assert ("tools", "model") in edges or any(e.source == "tools" for e in g.edges)


def test_turns_history_and_transitions(settings):
    steps = [
        AIMessage(content="", tool_calls=[call("load_skill", {"name": "organize_files"}, "a"),
                                          call("ls", {"path": "input"}, "b")]),
        AIMessage(content="", tool_calls=[call("move", {"source": "input/project_recsys.md",
                                                        "destination": "data/projects/"}, "c")]),
        AIMessage(content="Перенёс project_recsys.md в data/projects."),
        # second user turn: contextual follow-up
        AIMessage(content="", tool_calls=[call("ls", {"path": "data/projects"}, "d")]),
        AIMessage(content="В папке projects один документ."),
    ]
    sess, b = make_session(settings, steps)
    a1 = asyncio.run(sess.run_turn("Разложи файлы"))
    a2 = asyncio.run(sess.run_turn("Что теперь в папке проектов?"))
    assert "Перенёс" in a1 and "один документ" in a2
    msgs = asyncio.run(sess.messages())
    assert validate_history(msgs) == []
    assert [type(m).__name__ for m in msgs].count("HumanMessage") == 2  # no duplicated history
    assert sum(isinstance(m, ToolMessage) for m in msgs) == 4
    assert (settings.workspace / "data" / "projects" / "project_recsys.md").exists()
    events = [json.loads(l) for l in open(sess.tracer.jsonl, encoding="utf-8")]
    ends = [e for e in events if e["event"] == "turn_end"]
    assert ends[0]["transitions"][0] == "__start__" and ends[0]["transitions"][-1] == "__end__"
    seq = ends[0]["transitions"]
    assert "model" in seq and "tools" in seq and seq.index("tools") > seq.index("model")
    assert all(e["run_id"] for e in ends) and ends[0]["run_id"] != ends[1]["run_id"]


def test_tool_error_is_observation(settings):
    steps = [AIMessage(content="", tool_calls=[call("head", {"path": "input/nope.md", "n": -1}, "x")]),
             AIMessage(content="Файл не найден.")]
    sess, _ = make_session(settings, steps)
    asyncio.run(sess.run_turn("покажи начало файла nope"))
    msgs = asyncio.run(sess.messages())
    tm = [m for m in msgs if isinstance(m, ToolMessage)][0]
    assert json.loads(tm.content)["ok"] is False


def test_looping_agent_stopped_by_model_call_limit(settings):
    steps = [AIMessage(content="", tool_calls=[call("ls", {"path": "input"}, "loop")])]
    sess, _ = make_session(settings, steps, loop_last=True, model_calls_per_turn=5)
    asyncio.run(sess.run_turn("зациклись"))
    msgs = asyncio.run(sess.messages())
    ai = [m for m in msgs if isinstance(m, AIMessage)]
    assert len(ai) <= 6  # 5 model calls + limit message
    assert validate_history(msgs) == []


def test_looping_agent_stopped_by_recursion_limit_and_repaired(settings):
    steps = [AIMessage(content="", tool_calls=[call("ls", {"path": "input"}, "loop")])]
    sess, _ = make_session(settings, steps, loop_last=True, model_calls_per_turn=1000, recursion_limit=7)
    ans = asyncio.run(sess.run_turn("зациклись"))
    assert "recursion_limit" in ans
    msgs = asyncio.run(sess.messages())
    assert validate_history(msgs) == []  # dangling tool calls were closed
    # the same thread accepts the next user turn and history stays valid
    asyncio.run(sess.run_turn("ещё раз"))
    msgs = asyncio.run(sess.messages())
    assert validate_history(msgs) == []
    assert sum(isinstance(m, HumanMessage) for m in msgs) == 2


def test_history_validator_detects_problems():
    from langchain_core.messages import HumanMessage as H
    bad = [H(content="hi", id="1"), AIMessage(content="", tool_calls=[call("ls", {}, "z")], id="2"), H(content="x", id="3")]
    assert validate_history(bad)
    dup = [H(content="hi", id="1"), H(content="hi", id="1")]
    assert any("duplicate" in p for p in validate_history(dup))
