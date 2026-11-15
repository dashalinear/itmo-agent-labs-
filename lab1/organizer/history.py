"""Conversation-history checks and repair.

History lives in the LangGraph checkpointer (thread_id = CLI session id):
each user turn sends ONLY the new HumanMessage; the ``messages`` reducer
(add_messages) appends model and tool messages. These helpers verify the
invariants required by the assignment.
"""
from __future__ import annotations

from collections import Counter

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage


def validate_history(messages: list[BaseMessage]) -> list[str]:
    """Return a list of problems; empty list means the history is consistent."""
    problems: list[str] = []
    ids = [m.id for m in messages if m.id]
    for mid, n in Counter(ids).items():
        if n > 1:
            problems.append(f"duplicate message id {mid} x{n}")
    pending: dict[str, int] = {}
    seen_calls: Counter = Counter()
    for i, m in enumerate(messages):
        if isinstance(m, AIMessage):
            if pending:
                problems.append(f"AI message #{i} starts before tool results for {sorted(pending)}")
                pending.clear()
            for tc in m.tool_calls:
                seen_calls[tc["id"]] += 1
                pending[tc["id"]] = i
        elif isinstance(m, ToolMessage):
            if m.tool_call_id not in pending:
                problems.append(f"ToolMessage #{i} has unknown/duplicate tool_call_id {m.tool_call_id}")
            pending.pop(m.tool_call_id, None)
        elif isinstance(m, HumanMessage):
            if pending:
                problems.append(f"user message #{i} while tool calls {sorted(pending)} have no results")
                pending.clear()
    for cid, n in seen_calls.items():
        if n > 1:
            problems.append(f"tool_call_id {cid} used {n} times")
    if pending:
        problems.append(f"dangling tool calls without results: {sorted(pending)}")
    return problems


def dangling_tool_calls(messages: list[BaseMessage]) -> list[dict]:
    """Tool calls of the last AI message that have no ToolMessage yet."""
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            return [tc for tc in m.tool_calls if tc["id"] not in answered]
    return []


def history_summary(messages: list[BaseMessage]) -> dict:
    kinds = Counter(type(m).__name__ for m in messages)
    calls = sum(len(m.tool_calls) for m in messages if isinstance(m, AIMessage))
    return {"messages": len(messages), "by_type": dict(kinds), "tool_calls": calls}
