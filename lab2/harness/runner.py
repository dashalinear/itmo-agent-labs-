"""Task runner: start / resume / chat a durable task in the current process.

Durability:
  * graph state (messages, context_summary) -> AsyncSqliteSaver  .harness/checkpoints.db, thread_id = task_id,
    ``durability="sync"``: each super-step is written before the next one starts;
  * harness state (status, budget, queue, evidence, artifacts...) -> TaskStore .harness/state.db.

Resume in a NEW process with the same task_id:
  * if the last checkpoint has pending nodes (the process died mid-step), the graph continues with input None;
  * otherwise a harness message asks the agent to continue from the saved progress (no user re-entry);
  * reservations left in flight are charged, URLs left "running" go back to "pending", dangling tool calls are closed.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError

from organizer.history import dangling_tool_calls, validate_history
from organizer.llm import strip_thinking
from organizer.mcp_client import MCPConnection, unavailable_tools
from organizer.tracing import Tracer

from .agent import HarnessBundle, build_harness_agent
from .config import HarnessConfig, Limits
from .store import TaskStore
from .tokens import detect_window
from .tools_docs import TEXT_SUFFIXES

RESUMABLE = ("queued", "running", "paused", "failed")
RECURSION_LIMIT = 2000


def _short(v: Any, n: int = 150) -> str:
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= n else s[:n] + "…"


class TaskRunner:
    def __init__(self, cfg: HarnessConfig, *, verbose: bool = True, use_mcp: bool = True,
                 model=None, summarizer=None, provider=None, http_transport=None) -> None:
        self.cfg = cfg
        self.verbose = verbose
        self.use_mcp = use_mcp
        self._model, self._summarizer = model, summarizer
        self.provider = provider
        self.http_transport = http_transport
        self.store: TaskStore | None = None
        self._stack = contextlib.AsyncExitStack()
        self.checkpointer = None
        self.mcp_tools: list = []
        self.mcp_status = "MCP not started"

    async def __aenter__(self) -> "TaskRunner":
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
        self.store = TaskStore(self.cfg.state_dir / "state.db")
        # Windows: right after the previous process closed the database, antivirus/indexer may still hold the
        # file for a moment ("disk I/O error"); open and set up the checkpointer with a few retries.
        import sqlite3
        for attempt in range(6):
            stack = contextlib.AsyncExitStack()
            try:
                cp = await stack.enter_async_context(
                    AsyncSqliteSaver.from_conn_string(str(self.cfg.state_dir / "checkpoints.db")))
                await cp.setup()
                break
            except sqlite3.OperationalError:
                await stack.aclose()
                if attempt == 5:
                    raise
                await asyncio.sleep(1.5 * (attempt + 1))
        self._stack.push_async_callback(stack.aclose)
        self.checkpointer = cp
        if self.use_mcp:
            mcp = MCPConnection(self.cfg.base)
            self.mcp_tools = await mcp.__aenter__()
            self.mcp_status = mcp.status
            self._stack.push_async_callback(mcp.__aexit__, None, None, None)
        else:
            self.mcp_tools, self.mcp_status = unavailable_tools("disabled with --no-mcp"), "MCP disabled (--no-mcp)"
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()
        if self.store:
            self.store.close()

    # ------------------------------------------------------------------ task contract
    def create(self, spec: dict, task_id: str | None = None, overrides: dict | None = None) -> dict:
        task_id = task_id or spec.get("task_id") or ("task_" + uuid.uuid4().hex[:8])
        limits = self.cfg.limits.updated(spec.get("limits", {})).updated(overrides or {})
        spec = {**spec, "root": str(self.cfg.root.resolve()), "task_id": task_id}
        return self.store.create_task(task_id, spec.get("kind", "local"), spec["request"], spec, limits.to_dict())

    def set_limits(self, task_id: str, overrides: dict) -> Limits:
        task = self.store.get_task(task_id)
        old = Limits.from_dict(task["limits"])
        new = old.updated(overrides)
        self.store.update_task(task_id, limits=new.to_dict())
        self.store.event(task_id, "limits_changed", {"by": "user", "changes": overrides})
        return new

    # ------------------------------------------------------------------ models / window
    def _models(self, limits: Limits):
        if self._model is not None:
            return self._model, self._summarizer
        from .llm import build_models
        return build_models(self.cfg, limits)

    def window(self, counter_window: int | None = None) -> tuple[int, str]:
        if self._model is None:
            w, src = detect_window(self.cfg.base.llm_base_url, self.cfg.base.llm_api_key, self.cfg.base.llm_model,
                                   cache=self.cfg.window_cache)
            if w:
                return w, src
        if counter_window:
            return counter_window, "POST /tokenize -> max_model_len"
        if self.cfg.context_window:
            return self.cfg.context_window, "LLM_CONTEXT_WINDOW (.env)"
        raise RuntimeError("Model window is unknown: the server did not report it; set LLM_CONTEXT_WINDOW in .env")

    # ------------------------------------------------------------------ run / resume / chat turn
    async def run(self, task_id: str, user_text: str | None = None, overrides: dict | None = None,
                  label: str = "run", agent_factory=None, verbose: bool | None = None) -> dict:
        """agent_factory(...) -> bundle with .agent .ctx .ws .docs .task_tools .tools (default: lab 2 agent)."""
        saved_verbose = self.verbose
        if verbose is not None:
            self.verbose = verbose
        try:
            return await self._run(task_id, user_text, overrides, label, agent_factory or build_harness_agent)
        finally:
            self.verbose = saved_verbose

    async def _run(self, task_id: str, user_text: str | None, overrides: dict | None, label: str, agent_factory) -> dict:
        store = self.store
        task = store.get_task(task_id)
        if not task:
            raise KeyError(f"unknown task {task_id}")
        if task["status"] not in RESUMABLE:
            return {"status": task["status"], "message": f"task is {task['status']}; nothing to run"}
        if overrides:
            self.set_limits(task_id, overrides)
            task = store.get_task(task_id)
        limits = Limits.from_dict(task["limits"])
        model, summarizer = self._models(limits)
        window, wsrc = self.window()
        if limits.working_context_tokens > window:
            raise RuntimeError(f"working_context_tokens={limits.working_context_tokens} exceeds the real model window {window}")

        session = f"{task_id}_{label}{task['runs'] + 1}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        tracer = Tracer(self.cfg.traces_dir, session)

        def on_event(kind: str, data: dict) -> None:
            tracer.event(f"harness:{kind}", **data)
            if kind in ("compaction", "tool_output_offloaded", "guardrail", "task_stopped", "model_call_error",
                        "budget_interrupted_call", "simulated_crash"):
                shown = {k: v for k, v in data.items() if k != "summary"}
                tracer._md(f"> **harness:{kind}** `{json.dumps(shown, ensure_ascii=False, default=str)[:900]}`\n")
                if kind == "compaction":
                    tracer._md("<details><summary>summary после компактификации</summary>\n\n"
                               + data.get("summary", "") + "\n\n</details>\n")
                if self.verbose:
                    print(f"  ⚙ harness:{kind} {_short(shown, 220)}", flush=True)

        bundle = agent_factory(self.cfg, store, task_id, model, summarizer, self.mcp_tools, self.checkpointer,
                                     window, wsrc, provider=self.provider, http_transport=self.http_transport,
                                     on_event=on_event)
        ctx = bundle.ctx
        config = {"configurable": {"thread_id": task_id}, "recursion_limit": RECURSION_LIMIT,
                  "run_name": label, "metadata": {"task_id": task_id}}

        # -- recovery of the previous process ------------------------------------------
        lost = ctx.budget.recover_inflight()
        stuck = store.urls(task_id, "running")
        for u in stuck:
            store.set_url(task_id, u["url_key"], status="pending", error="interrupted while running; re-queued")
        store.clear_control(task_id)
        ctx.stop = None

        # -- fixed part of the prompt must fit -----------------------------------------
        fixed = ctx.estimate("", [])
        if fixed.tokens + ctx.reserve > ctx.working_limit:
            reason = (f"fixed prompt part (system+contract+tools) {fixed.tokens} + reserve {ctx.reserve} "
                      f"> working limit {ctx.working_limit}")
            store.set_status(task_id, "paused", reason)
            return {"status": "paused", "reason": reason}

        # -- scope documents (progress denominator) ---------------------------------------
        for scope in task["spec"].get("scope", []):
            root = bundle.ws.resolve(scope, must_exist=True)
            files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.suffix.lower() in TEXT_SUFFIXES)
            for f in files:
                bundle.docs.open(bundle.ws.rel(f))

        state = await bundle.agent.aget_state({"configurable": {"thread_id": task_id}})
        msgs = list(state.values.get("messages", [])) if state and state.values else []
        resume_kind = "start" if not msgs else ("continue_interrupted_step" if state.next else "continue")
        if msgs and not state.next and dangling_tool_calls(msgs):
            fixes = [ToolMessage(content=json.dumps({"ok": False, "error": {"type": "interrupted",
                                                                             "message": "process stopped before the result"}}),
                                 tool_call_id=tc["id"], name=tc["name"], status="error") for tc in dangling_tool_calls(msgs)]
            await bundle.agent.aupdate_state({"configurable": {"thread_id": task_id}}, {"messages": fixes}, as_node="tools")
            tracer.note(f"closed {len(fixes)} dangling tool call(s) from the previous process")
        if user_text is not None:
            inp = {"messages": [HumanMessage(content=user_text)]}
        elif resume_kind == "start":
            inp = {"messages": [HumanMessage(content=task["request"])]}
        elif resume_kind == "continue_interrupted_step":
            inp = None
        else:
            inp = {"messages": [HumanMessage(content=(
                "[harness: resume] Процесс был перезапущен. Продолжи ту же задачу с сохранённого места: прогресс, "
                "evidence, очередь источников и summary восстановлены (см. PROGRESS DIGEST и CONTEXT SUMMARY). "
                "Не повторяй уже сделанное."))]}

        usage_before = ctx.budget.snapshot()
        tracer.session_start(task_id=task_id, label=label, resume_kind=resume_kind, model=self.cfg.base.llm_model,
                             llm_base_url=self.cfg.base.llm_base_url, window=window, window_source=wsrc,
                             working_limit=ctx.working_limit, response_reserve=ctx.reserve,
                             token_count_method=ctx.counter.method, fixed_prompt_tokens=fixed.tokens,
                             limits=limits.to_dict(), mcp=self.mcp_status, tools=[t.name for t in bundle.tools],
                             task_root=str(bundle.ws.root), usage_before=usage_before,
                             recovered={"inflight_reservations_charged": [r["input_est"] for r in lost],
                                        "urls_requeued": len(stuck)},
                             status_before=task["status"])
        if self.verbose:
            print(f"Задача {task_id} · {label} · {resume_kind} · модель {self.cfg.base.llm_model} · окно {window} ({wsrc}) · "
                  f"рабочий лимит {ctx.working_limit} · резерв ответа {ctx.reserve} · {ctx.counter.method}\n"
                  f"трасса: {tracer.md}", flush=True)
        store.set_status(task_id, "running", label)
        store.update_task(task_id, runs=task["runs"] + 1)
        run_id = str(uuid.uuid4())
        tracer.user_turn(run_id, user_text if user_text is not None else
                         (inp["messages"][0].content if inp else "[harness: continue interrupted step]"))
        ctx.budget.start_clock()

        transitions, steps, error = ["__start__"], 0, None
        cancelled = False

        async def consume():
            nonlocal steps
            async for chunk in bundle.agent.astream(inp, {**config, "run_id": run_id}, stream_mode="updates",
                                                    durability="sync"):
                for node, update in chunk.items():
                    steps += 1
                    transitions.append(node)
                    new_msgs = (update or {}).get("messages", []) if isinstance(update, dict) else []
                    if node.endswith("before_model"):
                        # compaction re-adds the kept tail; it is logged as a harness:compaction event, not as new messages
                        new_msgs = [m for m in new_msgs if isinstance(m, AIMessage) and str(m.content).startswith("[harness]")]
                    new_msgs = [m for m in new_msgs if hasattr(m, "content") and not isinstance(m, RemoveMessage)]
                    tracer.step(run_id, steps, node, new_msgs)
                    if self.verbose:
                        self._echo(node, new_msgs)

        main = asyncio.create_task(consume())
        try:
            while not main.done():
                store.heartbeat(task_id)
                await asyncio.wait({main}, timeout=0.5)
                ctrl = store.get_control(task_id)
                if ctrl and ctrl["action"] == "cancel" and not main.done():
                    cancelled = True
                    main.cancel()
            await main
        except asyncio.CancelledError:
            if not cancelled:
                raise
        except GraphRecursionError:
            error = f"recursion_limit={RECURSION_LIMIT} reached"
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"[:500]
        finally:
            ctx.budget.stop_clock()

        if cancelled:
            ctx.request_stop("cancelled", "cancel requested by the user; the in-flight operation was interrupted")
            store.clear_control(task_id)
            transitions.append("[cancelled]")
        elif error:
            transitions.append("[stopped]")
            if store.get_task(task_id)["status"] == "running":
                store.set_status(task_id, "failed" if "recursion" not in error else "paused", error[:300])
        else:
            transitions.append("__end__")

        # close dangling tool calls left by a cancellation / error so the history stays valid
        final = await bundle.agent.aget_state({"configurable": {"thread_id": task_id}})
        fmsgs = list(final.values.get("messages", [])) if final and final.values else []
        if (cancelled or error) and dangling_tool_calls(fmsgs):
            fixes = [ToolMessage(content=json.dumps({"ok": False, "error": {"type": "cancelled", "message": error or "cancelled"}}),
                                 tool_call_id=tc["id"], name=tc["name"], status="error") for tc in dangling_tool_calls(fmsgs)]
            await bundle.agent.aupdate_state({"configurable": {"thread_id": task_id}}, {"messages": fixes}, as_node="tools")
            final = await bundle.agent.aget_state({"configurable": {"thread_id": task_id}})
            fmsgs = list(final.values.get("messages", []))
        ctx.archive(fmsgs)
        status = store.get_task(task_id)["status"]
        if status == "running":
            status = "paused"
            store.set_status(task_id, "paused", "waiting for user input" if user_text is not None else
                             "agent ended the turn without complete_task")
        answer = ""
        for m in reversed(fmsgs):
            if isinstance(m, AIMessage) and not m.tool_calls and str(m.content).strip():
                answer = strip_thinking(str(m.content))
                break
        problems = validate_history(fmsgs)
        report = bundle.task_tools.render()
        usage_after = ctx.budget.snapshot()
        tracer.turn_end(run_id, answer, transitions, problems, error,
                        {"spent_tokens_this_run": usage_after["tokens"]["spent"] - usage_before["tokens"]["spent"]})
        snapshot = self.export(task_id, bundle)
        tracer.event("session_end", status=store.get_task(task_id)["status"], usage=usage_after,
                     compactions=len(store.compactions(task_id)), history_valid=not problems,
                     context_summary_chars=len((final.values or {}).get("context_summary", "") or ""))
        tracer._md(f"---\n## Итог запуска\n\n- статус: **{store.get_task(task_id)['status']}** "
                   f"({store.get_task(task_id)['status_reason']})\n- компактификаций всего: {len(store.compactions(task_id))}\n"
                   f"- история корректна: {not problems}\n- отчёт: `{report}`\n\n```json\n"
                   + json.dumps(usage_after, ensure_ascii=False, indent=2) + "\n```\n")
        if self.verbose:
            print(f"\n{answer}\n\n[статус: {store.get_task(task_id)['status']} — {store.get_task(task_id)['status_reason']}]"
                  f"\nтрасса: {tracer.md}", flush=True)
        return {"status": store.get_task(task_id)["status"], "answer": answer, "error": error, "history_problems": problems,
                "trace": str(tracer.md), "snapshot": snapshot}

    def _echo(self, node: str, msgs) -> None:
        for m in msgs:
            if isinstance(m, AIMessage):
                for tc in m.tool_calls:
                    print(f"  ↳ [{node}] {tc['name']}({_short(tc['args'])})", flush=True)
                if not m.tool_calls and str(m.content).startswith("[harness]"):
                    print(f"  ⚙ {m.content}", flush=True)
            elif isinstance(m, ToolMessage):
                head = str(m.content)[:160]
                mark = "✗" if m.status == "error" or '"ok": false' in head else ("⇢" if '"offloaded": true' in head else "✓")
                print(f"    {mark} {m.name}", flush=True)

    # ------------------------------------------------------------------ results for submission
    def export(self, task_id: str, bundle: HarnessBundle | None = None) -> dict:
        store = self.store
        out = self.cfg.results_dir / task_id
        out.mkdir(parents=True, exist_ok=True)
        task = store.get_task(task_id)
        from .budget import Budget
        budget = Budget(store, task_id, Limits.from_dict(task["limits"]))
        rep = self.cfg.state_dir / "tasks" / task_id / "report.md"
        if rep.exists():
            shutil.copy2(rep, out / "report.md")
        snap = {"task": {k: task[k] for k in ("task_id", "kind", "status", "status_reason", "runs", "request", "plan",
                                              "completion", "limits")},
                "constraints": task["spec"].get("constraints", []), "criteria": task["spec"].get("criteria", {}),
                "budget": budget.snapshot(), "queue": store.url_counts(task_id),
                "documents": store.documents(task_id), "summary_version": task.get("summary_version"),
                "compactions": [{k: c[k] for k in ("id", "ts", "before_tokens", "after_tokens", "removed_messages",
                                                   "kept_messages", "summary_tokens", "method", "chunks", "cost_input",
                                                   "cost_output", "note")} for c in store.compactions(task_id)],
                "model_calls": store.model_calls(task_id)}
        (out / "status.json").write_text(json.dumps(snap, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        (out / "evidence.json").write_text(json.dumps(store.evidence(task_id), ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "queue.json").write_text(json.dumps({"searches": store.searches(task_id), "urls": store.urls(task_id)},
                                                   ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "summary.md").write_text(task.get("summary") or "", encoding="utf-8")
        (out / "compactions.json").write_text(json.dumps(store.compactions(task_id), ensure_ascii=False, indent=2),
                                              encoding="utf-8")
        (out / "events.json").write_text(json.dumps(store.events(task_id), ensure_ascii=False, indent=2, default=str),
                                         encoding="utf-8")
        mat = out / "web_materials"
        for u in store.urls(task_id, "done"):
            if u.get("artifact_id"):
                src = self.cfg.state_dir / "artifacts" / f"{u['artifact_id']}.txt"
                if src.exists():
                    mat.mkdir(exist_ok=True)
                    shutil.copy2(src, mat / f"{u['url_key']}.txt")
        hist = self.cfg.state_dir / "tasks" / task_id / "history.jsonl"
        if hist.exists():
            shutil.copy2(hist, out / "history.jsonl")
        return {"dir": str(out), "status": task["status"]}
