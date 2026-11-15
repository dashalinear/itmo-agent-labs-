"""Harness hooks on the prebuilt LangGraph ReAct agent (langchain.agents.create_agent).

No node, edge or loop is rewritten; the hooks are LangChain v1 middleware:

  CompactionMiddleware   before_model   (node "CompactionMiddleware.before_model", runs before EVERY model call,
                                         also between tool steps inside one user turn): estimates the full request
                                         (system + contract + digest + summary + messages + tool schemas + response
                                         reserve) against the working limit; if over the trigger it summarizes the
                                         oldest COMPLETE messages (never splitting tool_call/tool_result pairs) into
                                         ``context_summary`` and removes them from the state; re-checks the size,
                                         reduces by rules or pauses with a reason.
  ContextMiddleware      wrap_model_call  puts contract + digest + summary into the system message.
  ModelGuardMiddleware   wrap_model_call  last check of the FINAL request: pause/cancel flags, deadline, window,
                                         budget reservation, timeout, finite retries with backoff, usage accounting.
  ToolRuntimeMiddleware  wrap_tool_call   flags, deadline, tool budget, per-tool timeout, exceptions -> observations.
  OutputGuardMiddleware  wrap_tool_call   per-result and per-step output limits for ALL tools (local, web, MCP):
                                         oversized output -> artifact on disk + preview + artifact_id.
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from typing import Any, NotRequired

import openai
from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRequest, ToolCallRequest, hook_config
from langchain_core.messages import (AIMessage, BaseMessage, HumanMessage, RemoveMessage, SystemMessage,
                                     ToolMessage)
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from organizer.llm import strip_thinking
from organizer.results import parse

from .budget import BudgetExhausted
from .context import HarnessContext
from .prompts import SUMMARIZER_PROMPT
from .tokens import render_message


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


def _tool_error(request: ToolCallRequest, type_: str, message: str, **extra) -> ToolMessage:
    body = json.dumps({"ok": False, "tool": request.tool_call["name"], "error": {"type": type_, "message": message, **extra}},
                      ensure_ascii=False)
    return ToolMessage(content=body, tool_call_id=request.tool_call["id"], name=request.tool_call["name"], status="error")


# =========================================================== tools
class ToolRuntimeMiddleware(AgentMiddleware):
    """Outermost tool wrapper: control flags, deadline, tool budget, timeout, exceptions."""

    def __init__(self, ctx: HarnessContext) -> None:
        super().__init__()
        self.ctx = ctx

    def _pre(self, request: ToolCallRequest) -> ToolMessage | None:
        ctx = self.ctx
        ctx.crash_check()
        ctrl = ctx.store.get_control(ctx.task_id)
        if ctrl or ctx.stop:
            action = ctrl["action"] if ctrl else ctx.stop["status"]
            return _tool_error(request, "task_stopping", f"{action} requested: the tool was not executed")
        try:
            ctx.budget.check_deadline()
            ctx.budget.take("tool")
        except BudgetExhausted as e:
            ctx.emit("guardrail", guard="tool_budget", tool=request.tool_call["name"], **e.as_dict())
            ctx.request_stop("paused", f"budget exhausted: {e}")
            return _tool_error(request, "budget_exhausted", str(e), **e.as_dict())
        return None

    async def awrap_tool_call(self, request: ToolCallRequest, handler):
        blocked = self._pre(request)
        if blocked:
            return blocked
        try:
            res = await asyncio.wait_for(handler(request), timeout=self.ctx.limits.tool_timeout_s)
            self.ctx.crash_check("after")
            return res
        except asyncio.TimeoutError:
            self.ctx.emit("guardrail", guard="tool_timeout", tool=request.tool_call["name"],
                          timeout_s=self.ctx.limits.tool_timeout_s)
            return _tool_error(request, "timeout", f"Tool exceeded {self.ctx.limits.tool_timeout_s}s; "
                                                   "async I/O was cancelled, a sync tool thread may finish in the background")
        except Exception as e:  # noqa: BLE001
            return _tool_error(request, "tool_exception", f"{type(e).__name__}: {e}"[:800])

    def wrap_tool_call(self, request: ToolCallRequest, handler):
        blocked = self._pre(request)
        if blocked:
            return blocked
        try:
            return handler(request)
        except Exception as e:  # noqa: BLE001
            return _tool_error(request, "tool_exception", f"{type(e).__name__}: {e}"[:800])


class OutputGuardMiddleware(AgentMiddleware):
    """Bounds every tool result before it reaches the model; the full text goes to an artifact."""

    def __init__(self, ctx: HarnessContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._used: dict[str, int] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _step_key(request: ToolCallRequest) -> str:
        cid = request.tool_call["id"]
        state = request.state if isinstance(request.state, dict) else {}
        for m in reversed(state.get("messages", [])):
            if isinstance(m, AIMessage) and any(tc["id"] == cid for tc in m.tool_calls):
                return m.id or cid
        return cid

    def _preview(self, text: str, n: int) -> tuple[str, dict]:
        env = parse(text)
        if isinstance(env, dict) and isinstance(env.get("data"), dict) and isinstance(env["data"].get("text"), str):
            meta = {}
            for k, v in env["data"].items():
                if k == "text":
                    continue
                s = json.dumps(v, ensure_ascii=False, default=str)
                meta[k] = v if len(s) <= 300 else s[:300] + "…"
            return env["data"]["text"][:n], {"ok": env.get("ok"), "data_fields": meta}
        if isinstance(env, dict):
            return text[:n], {"ok": env.get("ok")}
        return text[:n], {}

    def _guard(self, request: ToolCallRequest, msg: Any) -> Any:
        if not isinstance(msg, ToolMessage):
            return msg
        L = self.ctx.limits
        text = _text(msg.content)
        key = self._step_key(request)
        with self._lock:
            used = self._used.get(key, 0)
            remaining = L.max_step_result_chars - used
            if len(text) <= L.max_tool_result_chars and len(text) <= remaining:
                self._used[key] = used + len(text)
                return msg
            reason = (f"single result {len(text)} chars > limit {L.max_tool_result_chars}"
                      if len(text) > L.max_tool_result_chars else
                      f"step total would exceed {L.max_step_result_chars} chars (already {used})")
            n = max(0, min(L.preview_chars, remaining - 700))
            name = request.tool_call["name"]
            args = json.dumps(request.tool_call["args"], ensure_ascii=False)[:300]
            art = self.ctx.artifacts.save(text, "tool_output", f"{name}({args})",
                                          {"tool": name, "args": request.tool_call["args"], "tool_call_id": request.tool_call["id"]})
            preview, meta = self._preview(text, n)
            body = {"ok": meta.get("ok", True), "tool": name, "offloaded": True, "reason": reason,
                    "artifact_id": art["artifact_id"], "total_chars": len(text), "total_lines": art["lines"],
                    "preview_chars": len(preview), "preview": preview, **({"data_fields": meta["data_fields"]} if meta.get("data_fields") else {}),
                    "how_to_continue": f"read_artifact(artifact_id='{art['artifact_id']}', start_line=1, max_lines=60) "
                                       f"or search_artifact(artifact_id='{art['artifact_id']}', query='...')"}
            content = json.dumps(body, ensure_ascii=False)
            self._used[key] = used + len(content)
            if len(self._used) > 200:
                for k in list(self._used)[:100]:
                    self._used.pop(k, None)
        self.ctx.emit("tool_output_offloaded", tool=name, artifact_id=art["artifact_id"], total_chars=len(text),
                      passed_chars=len(content), reason=reason, step=key)
        return msg.model_copy(update={"content": content})

    async def awrap_tool_call(self, request: ToolCallRequest, handler):
        return self._guard(request, await handler(request))

    def wrap_tool_call(self, request: ToolCallRequest, handler):
        return self._guard(request, handler(request))


# =========================================================== compaction (before_model hook)
class CompactionState(AgentState):
    context_summary: NotRequired[str]
    compactions: NotRequired[int]


def _render_for_summary(m: BaseMessage, clip: int) -> str:
    if isinstance(m, HumanMessage):
        return "USER: " + _text(m.content)[:3000]
    if isinstance(m, AIMessage):
        parts = []
        t = strip_thinking(_text(m.content))
        if t:
            parts.append("ASSISTANT: " + t[:2000])
        for tc in m.tool_calls:
            parts.append(f"ASSISTANT CALLS {tc['name']}({json.dumps(tc['args'], ensure_ascii=False)[:400]})")
        return "\n".join(parts) or "ASSISTANT: (empty)"
    if isinstance(m, ToolMessage):
        t = _text(m.content)
        if len(t) > clip:
            t = t[: int(clip * 0.75)] + f" …[{len(t)} chars, clipped]… " + t[-int(clip * 0.25):]
        return f"TOOL RESULT {m.name}: {t}"
    return f"{type(m).__name__}: {_text(m.content)[:1000]}"


class CompactionMiddleware(AgentMiddleware):
    state_schema = CompactionState

    def __init__(self, ctx: HarnessContext) -> None:
        super().__init__()
        self.ctx = ctx

    # ---- cut selection: only complete fragments ---------------------------------
    def _cut(self, msgs: list[BaseMessage], target_tokens: int) -> int:
        """Index where the preserved tail starts. Never inside a tool_call/tool_result group."""
        boundaries = [i for i, m in enumerate(msgs) if i > 0 and not isinstance(m, ToolMessage)]
        if not boundaries:
            return 0
        tail_tokens, sizes = 0, [self.ctx.counter.message_tokens(m) for m in msgs]
        best = boundaries[-1]                     # minimum tail: the last round
        for i in reversed(boundaries):
            tail_tokens = sum(sizes[i:])
            if tail_tokens <= target_tokens:
                best = i
            else:
                break
        return best

    # ---- summarization (bounded, budgeted, chunked) ------------------------------------
    def _rules_summary(self, prev: str, msgs: list[BaseMessage], cap_chars: int) -> str:
        lines = [prev.strip()] if prev.strip() else []
        lines.append("## Сжато правилами (без LLM)")
        for m in msgs:
            if isinstance(m, HumanMessage):
                lines.append("- пользователь: " + _text(m.content)[:200].replace("\n", " "))
            elif isinstance(m, AIMessage):
                for tc in m.tool_calls:
                    lines.append(f"- вызов {tc['name']}({json.dumps(tc['args'], ensure_ascii=False)[:120]})")
            elif isinstance(m, ToolMessage):
                t = _text(m.content)
                ids = sorted(set(re.findall(r"\b(?:art|ev)_[0-9a-f]{12,20}\b", t)))
                ok = '"ok": false' not in t[:60]
                lines.append(f"  → {m.name}: {'ok' if ok else 'ошибка'} {' '.join(ids)} {t[:140].replace(chr(10), ' ')}")
        text = "\n".join(lines)
        return text if len(text) <= cap_chars else text[: cap_chars - 200] + "\n…(правила: хвост обрезан)…\n" + text[-180:]

    async def _summarize(self, prev: str, msgs: list[BaseMessage]) -> dict:
        ctx, L = self.ctx, self.ctx.limits
        cap_chars = int(L.summary_max_tokens * ctx.counter.cpt * 1.3)
        if ctx.summarizer is None:
            return {"summary": self._rules_summary(prev, msgs, cap_chars), "method": "rules(no summarizer)",
                    "chunks": 0, "cost_in": 0, "cost_out": 0}
        sys_text = SUMMARIZER_PROMPT.format(words=int(L.summary_max_tokens * 0.6))
        limit_in = min(ctx.working_limit, ctx.window) - L.summary_max_tokens
        rendered = [_render_for_summary(m, L.summary_tool_clip_chars) for m in msgs]
        summary, method, chunks, cost_in, cost_out = prev, "llm", 0, 0, 0
        i = 0
        while i < len(rendered):
            if chunks >= L.max_compaction_chunks:
                summary = self._rules_summary(summary, msgs[i:], cap_chars)
                method = "llm+rules(chunk limit)"
                break
            fixed = ctx.counter.text_tokens(sys_text) + ctx.counter.text_tokens(summary) + 200
            room = limit_in - fixed
            if room < 400:
                summary = self._rules_summary(summary, msgs[i:], cap_chars)
                method = "rules(no room in window)"
                break
            chunk, j = [], i
            while j < len(rendered):
                piece = rendered[j]
                if ctx.counter.text_tokens("\n\n".join(chunk + [piece])) > room:
                    if not chunk:      # a single oversized item: clip it to the room
                        chunk.append(piece[: int(room * ctx.counter.cpt * 0.9)])
                        j += 1
                    break
                chunk.append(piece)
                j += 1
            user = (f"PREVIOUS SUMMARY:\n{summary or '(none)'}\n\nNEW HISTORY FRAGMENT ({j - i} messages):\n"
                    + "\n\n".join(chunk) + "\n\nWrite the updated summary.")
            est_in = ctx.counter.estimate(sys_text, [HumanMessage(content=user)]).tokens
            if est_in + L.summary_max_tokens > min(ctx.working_limit, ctx.window):
                summary = self._rules_summary(summary, msgs[i:], cap_chars)
                method = "rules(window check)"
                break
            try:
                res_id = ctx.budget.reserve("compaction", est_in + L.summary_max_tokens, est_in)
            except BudgetExhausted as e:
                ctx.emit("guardrail", guard="compaction_budget", **e.as_dict())
                summary = self._rules_summary(summary, msgs[i:], cap_chars)
                method = "rules(budget)"
                break
            t0 = time.perf_counter()
            call_id = ctx.store.add_model_call(ctx.task_id, kind="compaction", estimated_input=est_in, reserve=L.summary_max_tokens,
                                               working_limit=ctx.working_limit, window_tokens=ctx.window,
                                               method=ctx.counter.method, status="started")
            try:
                ai = await asyncio.wait_for(
                    ctx.summarizer.ainvoke([SystemMessage(content=sys_text), HumanMessage(content=user)]),
                    timeout=L.llm_timeout_s)
            except Exception as e:  # noqa: BLE001
                ctx.budget.fail(res_id, est_in)
                ctx.store.update_model_call(call_id, status="error", note=f"{type(e).__name__}: {e}"[:300],
                                            duration_s=round(time.perf_counter() - t0, 2))
                summary = self._rules_summary(summary, msgs[i:], cap_chars)
                method = f"rules(summarizer error {type(e).__name__})"
                break
            u = getattr(ai, "usage_metadata", None) or {}
            a_in = int(u.get("input_tokens") or est_in)
            a_out = int(u.get("output_tokens") or ctx.counter.text_tokens(_text(ai.content)))
            ctx.budget.commit(res_id, "compaction", a_in, a_out)
            ctx.store.update_model_call(call_id, status="ok", actual_input=a_in, actual_output=a_out,
                                        duration_s=round(time.perf_counter() - t0, 2))
            cost_in, cost_out = cost_in + a_in, cost_out + a_out
            text = strip_thinking(_text(ai.content))
            if not text:
                summary = self._rules_summary(summary, msgs[i:j], cap_chars)
                method = "llm+rules(empty answer)"
            else:
                summary = text[:cap_chars]
            chunks += 1
            i = j
        return {"summary": summary, "method": method, "chunks": chunks, "cost_in": cost_in, "cost_out": cost_out}

    # ---- rule-based reduction of the preserved tail ---------------------------------------
    def _rule_reduce(self, msgs: list[BaseMessage]) -> tuple[list[BaseMessage], int]:
        out, n = [], 0
        for m in msgs:
            t = _text(m.content)
            if isinstance(m, ToolMessage) and len(t) > 1500:
                art = self.ctx.artifacts.save(t, "tool_output", f"compaction-rules:{m.name}", {"tool_call_id": m.tool_call_id})
                body = json.dumps({"offloaded": True, "by": "compaction rules", "artifact_id": art["artifact_id"],
                                   "total_chars": len(t), "preview": t[:500],
                                   "how_to_continue": f"read_artifact(artifact_id='{art['artifact_id']}')"}, ensure_ascii=False)
                out.append(m.model_copy(update={"content": body}))
                n += 1
            elif isinstance(m, AIMessage) and len(t) > 3000:
                out.append(m.model_copy(update={"content": t[:1500] + " …[shortened by compaction rules]"}))
                n += 1
            else:
                out.append(m)
        return out, n

    # ---- the hook ------------------------------------------------------------------------------
    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: CompactionState, runtime) -> dict | None:
        ctx, L = self.ctx, self.ctx.limits
        msgs = list(state["messages"])
        for m in msgs:
            if m.id is None:
                import uuid
                m.id = str(uuid.uuid4())
        ctx.archive(msgs)
        summary = state.get("context_summary", "") or ""
        est = ctx.estimate(summary, msgs)
        trigger = int(L.compaction_trigger * ctx.working_limit)
        ctx.emit("context_check", estimated_input=est.tokens, reserve=ctx.reserve, total=est.tokens + ctx.reserve,
                 trigger=trigger, working_limit=ctx.working_limit, window=ctx.window, method=est.method,
                 messages=len(msgs))
        if est.tokens + ctx.reserve <= trigger:
            return None

        fixed = ctx.estimate(summary, []).tokens
        target = max(int(L.compaction_target * ctx.working_limit) - fixed - L.summary_max_tokens, 500)
        cut = self._cut(msgs, target)
        to_sum, keep = msgs[:cut], msgs[cut:]
        removable = sum(ctx.counter.message_tokens(m) for m in to_sum if not isinstance(m, HumanMessage))
        hard = est.tokens + ctx.reserve > ctx.working_limit
        if not hard and removable < L.compaction_min_gain * ctx.working_limit:
            # hysteresis: over the trigger but too little complete history to remove -> no pointless compaction
            ctx.emit("compaction_skipped", estimated_input=est.tokens, removable_tokens=removable,
                     min_gain=int(L.compaction_min_gain * ctx.working_limit))
            return None
        pinned = None
        for m in reversed(to_sum):
            if isinstance(m, HumanMessage):
                pinned = m
                break
        if pinned is not None:
            to_sum = [m for m in to_sum if m is not pinned]
            keep = [pinned] + keep
        result = {"summary": summary, "method": "none", "chunks": 0, "cost_in": 0, "cost_out": 0}
        if to_sum:
            result = await self._summarize(summary, to_sum)
        new_summary = result["summary"]
        est2 = ctx.estimate(new_summary, keep)
        note = ""
        if est2.tokens + ctx.reserve > ctx.working_limit:
            keep, n = self._rule_reduce(keep)
            est2 = ctx.estimate(new_summary, keep)
            note = f"rule-reduced {n} message(s)"
        n_comp = int(state.get("compactions", 0) or 0) + 1
        ctx.store.add_compaction(ctx.task_id, before_tokens=est.tokens, after_tokens=est2.tokens,
                                 removed_messages=len(to_sum), kept_messages=len(keep),
                                 summary_tokens=ctx.counter.text_tokens(new_summary), method=result["method"],
                                 chunks=result["chunks"], cost_input=result["cost_in"], cost_output=result["cost_out"],
                                 summary=new_summary, note=note)
        task = ctx.store.get_task(ctx.task_id)
        ctx.store.update_task(ctx.task_id, summary=new_summary, summary_version=(task.get("summary_version") or 0) + 1)
        ctx.emit("compaction", n=n_comp, before_tokens=est.tokens, after_tokens=est2.tokens, reserve=ctx.reserve,
                 working_limit=ctx.working_limit, removed_messages=len(to_sum), kept_messages=len(keep),
                 method=result["method"], chunks=result["chunks"], cost_input=result["cost_in"],
                 cost_output=result["cost_out"], note=note, summary=new_summary)
        update: dict[str, Any] = {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *keep],
                                  "context_summary": new_summary, "compactions": n_comp}
        if est2.tokens + ctx.reserve > ctx.working_limit:
            reason = (f"context: request {est2.tokens}+{ctx.reserve} tokens still exceeds the working limit "
                      f"{ctx.working_limit} after compaction and rule reduction")
            ctx.request_stop("paused", reason)
            update["messages"].append(AIMessage(content=f"[harness] Задача приостановлена: {reason}."))
            update["jump_to"] = "end"
        return update


# =========================================================== model call hooks
class ContextMiddleware(AgentMiddleware):
    """Selected context representation: contract + digest + compacted summary in the system message."""

    def __init__(self, ctx: HarnessContext) -> None:
        super().__init__()
        self.ctx = ctx

    async def awrap_model_call(self, request: ModelRequest, handler):
        summary = (request.state or {}).get("context_summary", "") if isinstance(request.state, dict) else ""
        return await handler(request.override(system_message=SystemMessage(content=self.ctx.build_system(summary or ""))))


RETRYABLE = (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError,
             asyncio.TimeoutError)


class ModelGuardMiddleware(AgentMiddleware):
    """Final gate before the LLM: flags, deadline, window, budget, timeout, retries, usage."""

    def __init__(self, ctx: HarnessContext) -> None:
        super().__init__()
        self.ctx = ctx

    def _stop(self, status: str, reason: str) -> AIMessage:
        self.ctx.request_stop(status, reason)
        if status in ("paused", "cancelled"):
            self.ctx.store.clear_control(self.ctx.task_id)
        word = {"paused": "приостановлена", "cancelled": "отменена", "failed": "остановлена с ошибкой"}[status]
        return AIMessage(content=f"[harness] Задача {word}: {reason}. Прогресс, evidence и отчёт сохранены; "
                                 f"расход не сбрасывается.")

    async def awrap_model_call(self, request: ModelRequest, handler):
        ctx, L = self.ctx, self.ctx.limits
        if ctx.stop:
            return self._stop(ctx.stop["status"], ctx.stop["reason"])
        ctrl = ctx.store.get_control(ctx.task_id)
        if ctrl:
            status = "cancelled" if ctrl["action"] == "cancel" else "paused"
            return self._stop(status, f"{ctrl['action']} requested by the user" + (f" ({ctrl['reason']})" if ctrl["reason"] else ""))
        try:
            ctx.budget.check_deadline()
        except BudgetExhausted as e:
            ctx.emit("guardrail", guard="deadline", **e.as_dict())
            return self._stop("paused", f"task deadline reached: {e}")
        system = _text(request.system_message.content) if request.system_message else ""
        est = ctx.counter.estimate(system, request.messages, request.tools)
        total = est.tokens + ctx.reserve
        call_id = ctx.store.add_model_call(ctx.task_id, kind="agent", estimated_input=est.tokens, reserve=ctx.reserve,
                                           working_limit=ctx.working_limit, window_tokens=ctx.window,
                                           method=est.method, status="planned")
        ctx.emit("model_call_plan", estimated_input=est.tokens, reserve=ctx.reserve, total=total,
                 working_limit=ctx.working_limit, window=ctx.window, method=est.method)
        if total > ctx.window:
            ctx.store.update_model_call(call_id, status="blocked", note="exceeds real window")
            return self._stop("paused", f"request {total} tokens exceeds the model window {ctx.window}; call not made")
        if total > ctx.working_limit:
            ctx.store.update_model_call(call_id, status="blocked", note="exceeds working limit")
            return self._stop("paused", f"request {total} tokens exceeds the working limit {ctx.working_limit}; call not made")
        last_error = None
        for attempt in range(L.llm_retries + 1):
            try:
                res_id = ctx.budget.reserve("agent", total, est.tokens)
            except BudgetExhausted as e:
                ctx.store.update_model_call(call_id, status="blocked", note=f"budget: {e}")
                ctx.emit("guardrail", guard="token_budget", **e.as_dict())
                return self._stop("paused", f"budget exhausted before the call ({e})")
            t0 = time.perf_counter()
            try:
                resp = await asyncio.wait_for(handler(request), timeout=L.llm_timeout_s)
            except RETRYABLE as e:
                ctx.budget.fail(res_id, est.tokens)
                last_error = f"{type(e).__name__}: {e}"[:300]
                ctx.emit("model_call_error", attempt=attempt + 1, error=last_error, retry=attempt < L.llm_retries)
                if attempt < L.llm_retries:
                    await asyncio.sleep(L.backoff_base_s * 4 * (2 ** attempt))
                    continue
                ctx.store.update_model_call(call_id, status="error", note=last_error,
                                            duration_s=round(time.perf_counter() - t0, 2))
                return self._stop("paused", f"LLM unavailable after {L.llm_retries + 1} attempts: {last_error}")
            except openai.BadRequestError as e:
                ctx.budget.fail(res_id, est.tokens)
                msg = f"{type(e).__name__}: {e}"[:300]
                ctx.store.update_model_call(call_id, status="error", note=msg)
                return self._stop("paused", f"LLM rejected the request: {msg}")
            ai = None
            if isinstance(resp, AIMessage):
                ai = resp
            else:
                result = getattr(resp, "result", None) or getattr(getattr(resp, "model_response", None), "result", None) or []
                ai = next((m for m in result if isinstance(m, AIMessage)), None)
            u = (getattr(ai, "usage_metadata", None) or {}) if ai is not None else {}
            a_in = int(u.get("input_tokens") or est.tokens)
            a_out = int(u.get("output_tokens") or (ctx.counter.text_tokens(_text(ai.content)) if ai else 0))
            ctx.budget.commit(res_id, "agent", a_in, a_out)
            if u.get("input_tokens"):
                ctx.counter.observe(est.chars, int(u["input_tokens"]))
            ctx.store.update_model_call(call_id, status="ok", actual_input=a_in, actual_output=a_out,
                                        duration_s=round(time.perf_counter() - t0, 2))
            ctx.emit("model_call_done", estimated_input=est.tokens, actual_input=a_in, actual_output=a_out,
                     duration_s=round(time.perf_counter() - t0, 2), calibrated_cpt=round(ctx.counter.cpt, 3))
            return resp
        return self._stop("paused", f"LLM failed: {last_error}")


def render_tokens(m: BaseMessage) -> int:  # small helper used by tests
    return len(render_message(m))
