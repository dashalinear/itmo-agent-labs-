"""Shared runtime of one task run: what every hook needs to know.

``HarnessContext`` builds the request representation that the model actually
sees on every call:

    system = harness prompt (+ skills metadata)
           + TASK CONTRACT (goal, user constraints, scope, criteria) - pinned, never compacted
           + PROGRESS DIGEST (from the state DB: coverage, evidence, queue, budget)
           + CONTEXT SUMMARY (result of the last compaction; restored from the checkpoint)
    messages = the current (possibly compacted) message list of the graph state
    tools    = schemas of all tools (they count against the window too)
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage

from organizer.tracing import msg_record, redact

from .artifacts import ArtifactStore
from .budget import Budget
from .config import Limits
from .store import TaskStore
from .tokens import TokenCounter, TokenEstimate
from .workspace import HarnessWorkspace


@dataclass
class HarnessContext:
    store: TaskStore
    task_id: str
    limits: Limits
    budget: Budget
    counter: TokenCounter
    window: int
    window_source: str
    base_prompt: str
    ws: HarnessWorkspace
    artifacts: ArtifactStore
    summarizer: BaseChatModel | None = None
    tools: Sequence[Any] = ()
    on_event: Callable[[str, dict], None] | None = None
    crash_at: str = ""
    stop: dict | None = None                    # set when a hook stopped the task in this run
    _tool_calls_this_process: int = field(default=0)
    _tools_done_this_process: int = field(default=0)

    # -- limits ------------------------------------------------------------
    @property
    def working_limit(self) -> int:
        return min(self.limits.working_context_tokens, self.window)

    @property
    def reserve(self) -> int:
        return self.limits.response_reserve_tokens

    # -- events ------------------------------------------------------------
    def emit(self, event_kind: str, /, **data: Any) -> None:
        self.store.event(self.task_id, event_kind, data)
        if self.on_event:
            self.on_event(event_kind, data)

    def request_stop(self, status: str, reason: str) -> None:
        """Stop the task at this boundary: status paused | cancelled | failed (never completed)."""
        if self.stop is None:
            self.stop = {"status": status, "reason": reason}
            self.store.set_status(self.task_id, status, reason[:300])
            self.emit("task_stopped", status=status, reason=reason)

    # -- request representation -----------------------------------------------
    def contract_block(self) -> str:
        task = self.store.get_task(self.task_id)
        spec = task["spec"]
        lines = ["## TASK CONTRACT (pinned by the harness, never compacted)",
                 f"task_id: {self.task_id} · kind: {task['kind']}",
                 "Request:", task["request"].strip()]
        if spec.get("constraints"):
            lines.append("User constraints (must hold for the whole task):")
            lines += [f"- {c}" for c in spec["constraints"]]
        if spec.get("scope"):
            lines.append("Local scope: " + ", ".join(spec["scope"]))
        if spec.get("criteria"):
            lines.append("Completion criteria (checked by complete_task): " + json.dumps(spec["criteria"], ensure_ascii=False))
        return "\n".join(lines)

    def digest(self) -> str:
        b = self.budget.snapshot()
        docs = []
        for d in self.store.documents(self.task_id):
            pages = set()
            for r in self.store.ranges(self.task_id, d["path"], d["content_hash"]):
                if r["unit"] == "page":
                    pages.update(range(r["start_no"], r["end_no"] + 1))
            docs.append(f"{d['path']}: read {len(pages)}/{d['pages']} pages{', searched' if d['searched'] else ''}")
        q = self.store.url_counts(self.task_id)
        ev = self.store.evidence(self.task_id)
        sections = [s["section_id"] for s in self.store.sections(self.task_id)]
        lines = ["## PROGRESS DIGEST (harness state, updated before every call)",
                 f"budget: tokens {b['tokens']['spent']}/{b['tokens']['limit']}, model calls {b['model_calls']['used']}/"
                 f"{b['model_calls']['limit']}, tools {b['tool_calls']['used']}/{b['tool_calls']['limit']}, "
                 f"search {b['search_calls']['used']}/{b['search_calls']['limit']}, http {b['http_requests']['used']}/"
                 f"{b['http_requests']['limit']}, time {b['active_seconds']['used']}/{b['active_seconds']['limit']}s",
                 f"evidence: {len(ev)}; report sections: {', '.join(sections) or 'none'}"]
        if docs:
            lines.append("documents: " + "; ".join(docs))
        if q["found_total"]:
            lines.append(f"web queue: found {q['found_total']}, selected {q['selected_total']}, read {q['done']}, "
                         f"pending {q['pending']}, errors {q['error']}, skipped {q['skipped']}")
        return "\n".join(lines)

    def build_system(self, summary: str) -> str:
        parts = [self.base_prompt, self.contract_block(), self.digest()]
        if summary:
            parts.append("## CONTEXT SUMMARY (earlier conversation, compacted by the harness; full history is archived)\n"
                         + summary)
        return "\n\n".join(parts)

    def estimate(self, summary: str, messages: Sequence[BaseMessage]) -> TokenEstimate:
        return self.counter.estimate(self.build_system(summary), messages, self.tools)

    # -- full history archive -------------------------------------------------------
    def history_path(self) -> Path:
        return self.ws.service_path("tasks", self.task_id, "history.jsonl")

    def archive(self, messages: Sequence[BaseMessage]) -> int:
        ids = [m.id for m in messages if m.id]
        fresh = set(self.store.archive_new(self.task_id, ids))
        if not fresh:
            return 0
        path = self.history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for m in messages:
                if m.id in fresh:
                    f.write(redact(json.dumps(msg_record(m), ensure_ascii=False, default=str)) + "\n")
        return len(fresh)

    # -- test hook: simulated crash -------------------------------------------------
    def crash_check(self, phase: str = "before") -> None:
        """HARNESS_CRASH_AT=tool:N       -> os._exit before the N-th tool call of this process
                         after_tool:N -> os._exit right AFTER the N-th tool finished (effect done,
                                         checkpoint of the tools step not yet written: the real failure window)"""
        if not self.crash_at or ":" not in self.crash_at:
            return
        kind, n = self.crash_at.split(":", 1)
        if (kind == "tool" and phase == "before") or (kind == "after_tool" and phase == "after"):
            if phase == "before":
                self._tool_calls_this_process += 1
                count = self._tool_calls_this_process
            else:
                self._tools_done_this_process += 1
                count = self._tools_done_this_process
            if count >= int(n):
                self.emit("simulated_crash", at=self.crash_at, phase=phase)
                sys.stdout.flush()
                os._exit(137)
