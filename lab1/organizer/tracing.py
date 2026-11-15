"""Session traces: JSONL (machine-readable) + Markdown (human-readable).

Each user turn gets a run_id. Every graph super-step streamed with
``stream_mode="updates"`` is logged with its node name, so the trace shows
transitions such as  __start__ -> model -> tools -> model -> __end__ ,
tool calls (name, args, id), tool results (tool_call_id, status) and errors.
Secrets are redacted.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,})")
MD_RESULT_CHARS = 1500


def redact(text: str) -> str:
    return _SECRET.sub("[REDACTED]", text)


def _content(m: BaseMessage) -> str:
    c = m.content
    if isinstance(c, list):
        c = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in c)
    return redact(str(c))


def msg_record(m: BaseMessage) -> dict:
    rec: dict[str, Any] = {"type": type(m).__name__, "id": m.id, "content": _content(m)}
    if isinstance(m, AIMessage):
        rec["tool_calls"] = [{"id": tc["id"], "name": tc["name"],
                              "args": json.loads(redact(json.dumps(tc["args"], ensure_ascii=False)))}
                             for tc in m.tool_calls]
        usage = getattr(m, "usage_metadata", None)
        if usage:
            rec["usage"] = dict(usage)
    if isinstance(m, ToolMessage):
        rec.update(tool_call_id=m.tool_call_id, name=m.name, status=m.status)
    return rec


class Tracer:
    def __init__(self, traces_dir: Path, session_id: str) -> None:
        traces_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        self.jsonl = traces_dir / f"{session_id}.jsonl"
        self.md = traces_dir / f"{session_id}.md"
        self._t0: dict[str, float] = {}
        self._md(f"# Trace {session_id}\n")

    # low level -------------------------------------------------------------
    def event(self, kind: str, **data: Any) -> None:
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
               "session_id": self.session_id, "event": kind, **data}
        with open(self.jsonl, "a", encoding="utf-8") as f:
            f.write(redact(json.dumps(rec, ensure_ascii=False, default=str)) + "\n")

    def _md(self, text: str) -> None:
        with open(self.md, "a", encoding="utf-8") as f:
            f.write(redact(text) + "\n")

    # high level --------------------------------------------------------------
    def session_start(self, **info: Any) -> None:
        self.event("session_start", **info)
        self._md("## Session\n```json\n" + json.dumps(info, ensure_ascii=False, indent=2, default=str) + "\n```\n")

    def user_turn(self, run_id: str, text: str) -> None:
        self._t0[run_id] = time.perf_counter()
        self.event("user_turn", run_id=run_id, text=text)
        self._md(f"---\n## 👤 User  `run_id={run_id}`\n\n{text}\n")

    def user_action(self, action: str, **data: Any) -> None:
        self.event("user_action", action=action, **data)
        self._md(f"> **Manual user action:** `{action}` {json.dumps(data, ensure_ascii=False)}\n")

    def step(self, run_id: str, idx: int, node: str, messages: list[BaseMessage]) -> None:
        recs = [msg_record(m) for m in messages]
        self.event("graph_step", run_id=run_id, step=idx, node=node, messages=recs)
        lines = [f"**step {idx} · node `{node}`**"]
        for r in recs:
            if r["type"] == "AIMessage":
                if r["content"].strip():
                    lines.append(f"- AI text: {r['content'][:800]}")
                for tc in r.get("tool_calls", []):
                    lines.append(f"- tool_call `{tc['name']}` id=`{tc['id']}` args=`{json.dumps(tc['args'], ensure_ascii=False)[:600]}`")
            elif r["type"] == "ToolMessage":
                lines.append(f"- tool_result `{r['name']}` for `{r['tool_call_id']}` status={r['status']}: "
                             f"`{r['content'][:MD_RESULT_CHARS]}`")
        self._md("\n".join(lines) + "\n")

    def turn_end(self, run_id: str, answer: str, transitions: list[str], history_problems: list[str],
                 error: str | None = None, usage: dict | None = None) -> None:
        dur = round(time.perf_counter() - self._t0.pop(run_id, time.perf_counter()), 2)
        self.event("turn_end", run_id=run_id, answer=answer, transitions=transitions,
                   history_problems=history_problems, error=error, duration_s=dur, usage=usage)
        self._md(f"**transitions:** `{' -> '.join(transitions)}`  \n"
                 f"**history check:** {'OK' if not history_problems else history_problems}  \n"
                 f"**duration:** {dur}s" + (f"  \n**error:** `{error}`" if error else "") +
                 (f"  \n**usage:** `{usage}`" if usage else "") +
                 f"\n\n### 🤖 Assistant\n\n{answer}\n")

    def note(self, text: str) -> None:
        self.event("note", text=text)
        self._md(f"> {text}\n")
