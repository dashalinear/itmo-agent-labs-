"""Text delegation to sub-agents (deep search, exhaustive search): non-blocking, harness-controlled.

Call contract
  input : request      - the task in natural language written by the main agent (mandatory content)
          scope        - optional technical metadata: folder (exhaustive area / deep search focus)
          metadata     - parent_task_id, corpus_id, budget (limits of the sub-task) are set by code
  output: task_id, kind, status (running|completed|paused|failed|cancelled), findings, open_questions,
          evidence [{evidence_id, document_id, fragment_id, path, lines, quote, claim}], usage, and for
          exhaustive also the coverage registry (documents/fragments read, remaining ranges).

Mode: NON-BLOCKING. start_subagent creates a sub-task (state.db, own thread_id = task_id, own checkpoints,
own system prompt, context and tools) and starts it as an asyncio task inside the CLI process; it returns
the task_id immediately. The main agent can answer the user meanwhile, ask subagent_status and later
subagent_result (optionally waiting). A sub-task can also be resumed in a new process:
`python -m navsearch.cli resume --task-id <id>`.

Budget: ChainedBudget charges every sub-agent LLM call to its own limits AND to the parent task, so the
spending of sub-agents counts in the total limit of the task.
"""
from __future__ import annotations

import asyncio
import json
from typing import Literal

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from organizer.results import ToolError, err, ok, run_tool

from harness.budget import Budget, BudgetExhausted
from harness.config import Limits
from harness.store import TaskStore

DEFAULT_SUB_LIMITS = {
    "deep": {"max_total_tokens": 450_000, "max_model_calls": 40, "max_tool_calls": 160},
    "exhaustive": {"max_total_tokens": 700_000, "max_model_calls": 70, "max_tool_calls": 300},
}


class ChainedBudget(Budget):
    """Own limits + the parent's total: a call is allowed only if both can afford it."""

    def __init__(self, store: TaskStore, task_id: str, limits: Limits, parent_id: str) -> None:
        super().__init__(store, task_id, limits)
        self.parent_id = parent_id
        parent = store.get_task(parent_id)
        self.parent = Budget(store, parent_id, Limits.from_dict(parent["limits"])) if parent else None
        self._links: dict[int, int] = {}

    def reserve(self, kind: str, tokens: int, input_est: int) -> int:
        if self.parent:
            rem = self.parent.remaining_tokens()
            if tokens > rem:
                raise BudgetExhausted("parent_tokens", tokens, max(rem, 0), self.parent.limits.max_total_tokens)
        rid = super().reserve(kind, tokens, input_est)
        if self.parent:
            self._links[rid] = self.store.add_reservation(self.parent_id, f"sub:{kind}", tokens, input_est)
        return rid

    def commit(self, res_id: int, kind: str, input_tokens: int, output_tokens: int) -> None:
        super().commit(res_id, kind, input_tokens, output_tokens)
        if self.parent and res_id in self._links:
            if kind == "compaction":
                self.store.add_usage(self.parent_id, compaction_input_tokens=input_tokens, compaction_output_tokens=output_tokens)
            else:
                self.store.add_usage(self.parent_id, input_tokens=input_tokens, output_tokens=output_tokens)
            self.store.finish_reservation(self._links.pop(res_id), "done")

    def fail(self, res_id: int, input_est: int) -> None:
        super().fail(res_id, input_est)
        if self.parent and res_id in self._links:
            self.store.add_usage(self.parent_id, failed_call_tokens=input_est)
            self.store.finish_reservation(self._links.pop(res_id), "failed")


def build_result(store: TaskStore, task_id: str, coverage: dict | None = None) -> dict:
    t = store.get_task(task_id)
    if not t:
        raise ToolError("unknown_task", f"no sub-task {task_id}")
    comp = t.get("completion") or {}
    ev = [{"evidence_id": e["evidence_id"], "document_id": e["document_id"], "fragment_id": e.get("fragment_id"),
           "path": e["source"], "lines": [e["line_start"], e["line_end"]], "quote": e["quote"], "claim": e["claim"]}
          for e in store.evidence(task_id)]
    b = Budget(store, task_id, Limits.from_dict(t["limits"])).snapshot()
    out = {"task_id": task_id, "kind": t["kind"], "parent_task_id": t["spec"].get("parent_task_id"),
           "request": t["request"], "status": t["status"], "reason": t["status_reason"],
           "findings": comp.get("findings") or comp.get("reason"), "open_questions": comp.get("open_questions", []),
           "evidence": ev, "usage": {"tokens": b["tokens"]["spent"], "model_calls": b["model_calls"]["used"],
                                     "tool_calls": b["tool_calls"]["used"], "compactions": b["compaction_calls"]}}
    if coverage is not None:
        out["coverage"] = coverage
    return out


class StartArgs(BaseModel):
    kind: Literal["deep", "exhaustive"] = Field(description="deep = multi-round research; exhaustive = complete pass over a finite area")
    request: str = Field(description="The task for the sub-agent in natural language (what to find, compare, list, with sources)")
    scope_folder: str | None = Field(None, description="Optional metadata: corpus folder (exhaustive area or deep focus)")


class TaskIdArg(BaseModel):
    task_id: str


class ResultArgs(BaseModel):
    task_id: str
    wait_seconds: int = Field(0, description="Wait up to N seconds (0..240) if the sub-task is still running")


class SubagentManager:
    def __init__(self, runner, factories: dict, parent_task_id: str, coverage_fn=None) -> None:
        self.runner = runner
        self.factories = factories
        self.parent_task_id = parent_task_id
        self.coverage_fn = coverage_fn       # task_id -> coverage dict (exhaustive)
        self.tasks: dict[str, asyncio.Task] = {}

    @property
    def store(self) -> TaskStore:
        return self.runner.store

    async def start(self, kind: str, request: str, scope_folder: str | None = None) -> str:
        if not request or len(request.strip()) < 15:
            return err("start_subagent", "invalid_argument", "request must be a natural-language task (>= 15 chars)")
        parent = self.store.get_task(self.parent_task_id)
        n = sum(1 for t in self.store.list_tasks() if t["task_id"].startswith(f"{self.parent_task_id}-{kind}")) + 1
        tid = f"{self.parent_task_id}-{kind}{n}"
        limits = Limits.from_dict(parent["limits"]).updated(DEFAULT_SUB_LIMITS[kind])
        spec = {"task_id": tid, "kind": kind, "request": request, "parent_task_id": self.parent_task_id,
                "corpus_id": "itmo-lab3", "scope_folder": scope_folder, "scope": [], "root": parent["spec"]["root"],
                "constraints": parent["spec"].get("constraints", []), "criteria": {}}
        self.store.create_task(tid, kind, request, spec, limits.to_dict())
        self.store.event(self.parent_task_id, "subtask_started", {"task_id": tid, "kind": kind, "request": request,
                                                                   "scope_folder": scope_folder})
        self.tasks[tid] = asyncio.create_task(self.runner.run(tid, label=kind, agent_factory=self.factories[kind], verbose=False))
        return ok("start_subagent", task_id=tid, kind=kind, status="running", mode="non-blocking",
                  note="The sub-agent works in the background with its own context. Answer the user meanwhile; check "
                       "subagent_status(task_id) and fetch subagent_result(task_id) later.")

    def status(self, task_id: str) -> dict:
        t = self.store.get_task(task_id)
        if not t or t["spec"].get("parent_task_id") != self.parent_task_id:
            raise ToolError("unknown_task", f"{task_id} is not a sub-task of {self.parent_task_id}")
        running = task_id in self.tasks and not self.tasks[task_id].done()
        b = Budget(self.store, task_id, Limits.from_dict(t["limits"])).snapshot()
        out = {"task_id": task_id, "kind": t["kind"], "status": t["status"], "reason": t["status_reason"],
               "running_in_background": running, "evidence": len(self.store.evidence(task_id)),
               "model_calls": b["model_calls"]["used"], "tokens": b["tokens"]["spent"]}
        if t["kind"] == "exhaustive" and self.coverage_fn:
            try:
                c = self.coverage_fn(task_id)
                out["coverage"] = {k: c[k] for k in ("fragments_read", "fragments_total", "documents_done", "documents_total")}
            except Exception:  # noqa: BLE001 - area not defined yet
                pass
        return out

    async def result(self, task_id: str, wait_seconds: int = 0) -> str:
        try:
            self.status(task_id)
        except ToolError as e:
            return err("subagent_result", e.type, e.message)
        t = self.tasks.get(task_id)
        if t and not t.done() and wait_seconds > 0:
            await asyncio.wait({t}, timeout=min(int(wait_seconds), 240))
        if t and not t.done():
            return ok("subagent_result", task_id=task_id, status="running",
                      note="Still running; answer the user and ask again later.", progress=self.status(task_id))
        cov = None
        if self.store.get_task(task_id)["kind"] == "exhaustive" and self.coverage_fn:
            try:
                cov = self.coverage_fn(task_id)
            except Exception:  # noqa: BLE001
                cov = None
        return ok("subagent_result", **build_result(self.store, task_id, cov))

    def as_tools(self) -> list[BaseTool]:
        return [
            StructuredTool.from_function(coroutine=lambda kind, request, scope_folder=None: self.start(kind, request, scope_folder),
                                         name="start_subagent", args_schema=StartArgs,
                                         description="Delegate a complex question to a sub-agent with a natural-language request. "
                                                     "Non-blocking: returns task_id at once. deep = research with several search "
                                                     "rounds; exhaustive = list EVERYTHING in a finite area (folder) without top-k."),
            StructuredTool.from_function(func=lambda task_id: run_tool("subagent_status", self.status, task_id),
                                         name="subagent_status", args_schema=TaskIdArg,
                                         description="Status/progress of a delegated sub-task (running, completed, paused...)."),
            StructuredTool.from_function(coroutine=lambda task_id, wait_seconds=0: self.result(task_id, wait_seconds),
                                         name="subagent_result", args_schema=ResultArgs,
                                         description="Result of a sub-task: findings, evidence with document_id/fragment_id/lines, "
                                                     "open questions, usage (and coverage for exhaustive). Can wait a bit."),
        ]


# ------------------------------------------------------------------ result submission inside sub-agents
class SubmitArgs(BaseModel):
    findings: str = Field("", description="Your conclusions with references to evidence_id (empty: the harness lists the evidence)")
    open_questions: list[str] = Field(default_factory=list, description="What remained unclear or unconfirmed")


class SubmitTools:
    def __init__(self, store: TaskStore, task_id: str, kind: str, coverage_fn=None, render_fn=None) -> None:
        self.store, self.task_id, self.kind = store, task_id, kind
        self.coverage_fn = coverage_fn
        self.render_fn = render_fn

    def submit_result(self, findings: str = "", open_questions: list[str] | None = None) -> dict:
        ev = self.store.evidence(self.task_id)
        if not (findings or "").strip():
            by_doc: dict[str, int] = {}
            for e in ev:
                by_doc[e["source"]] = by_doc.get(e["source"], 0) + 1
            findings = (f"Найдено подтверждений: {len(ev)} в {len(by_doc)} документах: "
                        + "; ".join(f"{p} — {n}" for p, n in sorted(by_doc.items())) + " (итог собран harness по evidence)")
        if self.kind == "exhaustive":
            cov = self.coverage_fn()
            if not cov["complete"]:
                raise ToolError("coverage_incomplete", f"{cov['fragments_total'] - cov['fragments_read']} fragments of the area are "
                                "not read yet; continue area_next, or stop_partial if the budget does not allow it",
                                remaining=[d for d in cov["documents"] if d["status"] != "done"][:10])
        else:
            searches = sum(1 for e in self.store.events(self.task_id, "corpus_search"))
            if len(ev) < 2 or searches < 2:
                raise ToolError("criteria_not_met", "Deep search needs at least 2 search calls and 2 cited fragments",
                                searches=searches, evidence=len(ev))
        self.store.update_task(self.task_id, completion={"completed": True, "findings": findings,
                                                         "open_questions": open_questions or []})
        self.store.set_status(self.task_id, "completed", findings[:200])
        if self.render_fn:
            self.render_fn()
        return {"status": "completed", "evidence": len(ev)}

    def as_tools(self) -> list[BaseTool]:
        return [StructuredTool.from_function(func=lambda **kw: run_tool("submit_result", self.submit_result, **kw),
                                             name="submit_result", args_schema=SubmitArgs,
                                             description="Finish the sub-task: findings with evidence_id references and open "
                                                         "questions. The harness checks the completion rule (exhaustive: full coverage).")]


def dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)
