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


def line_friendly(text: str) -> str:
    """Offloaded tool results are stored so that read_artifact (line ranges) reaches every part of them.
    A tool envelope is one JSON line; a long line could only be searched, not read in ranges. Stored form:
      * envelope with data.text (read_pages, read, read_lines...): the envelope without the text on line 1,
        then the text itself with its real line breaks;
      * other JSON envelopes (search_text, MCP search...): indented JSON, one field / hit item per line;
      * anything else: unchanged.
    Nothing is dropped: the full result is in the artifact, only the layout differs."""
    env = parse(text)
    if not isinstance(env, dict):
        return text
    data = env.get("data")
    if isinstance(data, dict) and isinstance(data.get("text"), str) and data["text"]:
        head = {**env, "data": {k: v for k, v in data.items() if k != "text"}}
        return json.dumps(head, ensure_ascii=False, default=str) + "\n--- data.text ---\n" + data["text"]
    return json.dumps(env, ensure_ascii=False, indent=1, default=str)


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
        self._seen: dict[str, tuple[str, int]] = {}
        self._reads_since_evidence = 0
        self._last_budget_left = None
        self._step_started: dict[str, float] = {}

    READ_TOOLS = {"read_lines", "read_pages", "read_artifact", "search_text", "search_artifact"}
    PROGRESS_TOOLS = {"save_evidence", "write_report_section"}

    def _budget_hint(self, res: ToolMessage) -> ToolMessage:
        """B4 on 20.11 spent all 120 model calls on reading and never wrote the report: when only a few calls are
        left, every tool result says so (once per result, the run itself is not stopped)."""
        K = self.ctx.limits.budget_hint_calls
        if not K:
            return res
        mc = self.ctx.budget.snapshot()["model_calls"]
        left = (mc.get("limit") or 0) - (mc.get("used") or 0)
        if not mc.get("limit") or left > K:
            return res
        note = (f"Only {max(left, 0)} model calls left. Stop reading: write the missing report sections with "
                "write_report_section from the evidence you have, then complete_task (or stop_partial).")
        if self._last_budget_left == left:
            return res
        self._last_budget_left = left
        self.ctx.emit("guardrail", guard="budget_hint", model_calls_left=left)
        try:
            env = json.loads(_text(res.content))
        except ValueError:
            env = None
        if isinstance(env, dict):
            content = json.dumps({**env, "harness_budget": note}, ensure_ascii=False)
        else:
            content = _text(res.content) + "\n[harness_budget] " + note
        return ToolMessage(content=content, tool_call_id=res.tool_call_id, name=res.name, status=res.status)

    def _evidence_guard(self, request: ToolCallRequest, res):
        """Reading without progress: in one A1 run the model read the 2000-line handbook in 8-line steps
        (212 read_lines) and never saved a single quote. After N reads in a row with no saved evidence the
        harness attaches a hint to the result (a bounded change of strategy, the run itself goes on)."""
        N = self.ctx.limits.reads_without_evidence_hint
        name = request.tool_call["name"]
        if not isinstance(res, ToolMessage):
            return res
        res = self._budget_hint(res)
        if not N:
            return res
        if name in self.PROGRESS_TOOLS and '"ok": true' in _text(res.content)[:200]:
            self._reads_since_evidence = 0
            return res
        if name not in self.READ_TOOLS:
            return res
        self._reads_since_evidence += 1
        if self._reads_since_evidence % N:
            return res
        self.ctx.emit("guardrail", guard="reads_without_evidence", tool=name, reads=self._reads_since_evidence)
        hint = (f"{self._reads_since_evidence} reading calls in a row and no new evidence. Stop scanning the document "
                "line by line: save_evidence for the quotes you have already found, write the report sections you "
                "can write now, and reach the remaining places with search_text instead of sequential reading.")
        try:
            env = json.loads(_text(res.content))
        except ValueError:
            env = None
        if isinstance(env, dict):
            env["harness_hint"] = hint
            content = json.dumps(env, ensure_ascii=False)
        else:
            content = _text(res.content) + "\n[harness_hint] " + hint
        return ToolMessage(content=content, tool_call_id=res.tool_call_id, name=res.name, status=res.status)

    def _repeat_guard(self, request: ToolCallRequest, res):
        """No-progress detector: the same call (tool + arguments) returning the SAME result again inside one run.
        Status tools that return a changed state (corpus_status, ls after moves...) are not affected."""
        L = self.ctx.limits
        if not isinstance(res, ToolMessage) or not (L.repeat_call_warn or L.repeat_call_stop):
            return res
        key = request.tool_call["name"] + json.dumps(request.tool_call["args"], ensure_ascii=False, sort_keys=True)
        digest = str(hash(_text(res.content)))
        prev, n = self._seen.get(key, ("", 0))
        n = n + 1 if prev == digest else 1
        self._seen[key] = (digest, n)
        if L.repeat_call_stop and n >= L.repeat_call_stop:
            self.ctx.emit("guardrail", guard="no_progress", tool=request.tool_call["name"], repeats=n)
            self.ctx.request_stop("paused", f"no progress: {request.tool_call['name']} returned the same result {n} times")
            return _tool_error(request, "no_progress", f"The same call returned the same result {n} times; the run is stopped")
        if L.repeat_call_warn and n >= L.repeat_call_warn:
            self.ctx.emit("guardrail", guard="repeated_call", tool=request.tool_call["name"], repeats=n)
            hint = (" The page is already stored: use its 'links' from the earlier result, search_artifact on its "
                    "artifact_id, or fetch another source." if request.tool_call["name"] == "web_fetch" else "")
            return _tool_error(request, "repeated_call",
                               f"This exact call already returned the same result {n - 1} times in this turn. Do not repeat it: "
                               "use the earlier result, change the approach, or answer with what you have "
                               "(say explicitly what the corpus does not confirm)." + hint)
        return res

    def _step_time(self, request: ToolCallRequest) -> ToolMessage | None:
        """One time budget for all parallel tool calls of one model step (max_step_seconds, 0 = off): a call that
        starts after the step's budget is used up is not run."""
        limit = self.ctx.limits.max_step_seconds
        if not limit:
            return None
        key, _n = OutputGuardMiddleware._step_info(request)
        t0 = self._step_started.setdefault(key, time.monotonic())
        used = time.monotonic() - t0
        if used > limit:
            self.ctx.emit("guardrail", guard="step_time", tool=request.tool_call["name"], used_s=round(used, 1), limit_s=limit)
            return _tool_error(request, "step_time_exhausted", f"this model step already ran {used:.0f} s of its {limit} s; "
                               "the call was not executed - use the results you have or call it in the next step")
        return None

    def _pre(self, request: ToolCallRequest) -> ToolMessage | None:
        ctx = self.ctx
        ctx.crash_check()
        blocked = self._step_time(request)
        if blocked:
            return blocked
        ctrl = ctx.store.get_control(ctx.task_id)
        if ctrl or ctx.stop:
            action = ctrl["action"] if ctrl else ctx.stop["status"]
            return _tool_error(request, "task_stopping", f"{action} requested: the tool was not executed")
        try:
            ctx.budget.check_deadline()
        except BudgetExhausted as e:
            ctx.emit("guardrail", guard="deadline", tool=request.tool_call["name"], **e.as_dict())
            # the reason keeps the "budget exhausted" prefix of earlier versions (lab 4 treats it as a final stop)
            ctx.request_stop("paused", f"budget exhausted: task deadline reached ({e})")
            return _tool_error(request, "budget_exhausted", f"task deadline reached: {e}", **e.as_dict())
        try:
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
            self.ctx.crash_check("after", tool=request.tool_call["name"])
            return self._evidence_guard(request, self._repeat_guard(request, res))
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
            return self._evidence_guard(request, self._repeat_guard(request, handler(request)))
        except Exception as e:  # noqa: BLE001
            return _tool_error(request, "tool_exception", f"{type(e).__name__}: {e}"[:800])


STUB_RESERVE_CHARS = 280      # room reserved per result still to come in the step (a reference stub is about 200-250 chars)


class OutputGuardMiddleware(AgentMiddleware):
    """Bounds every tool result before it reaches the model; the full text goes to an artifact."""

    def __init__(self, ctx: HarnessContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._used: dict[str, int] = {}
        self._seen_results: dict[str, int] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _step_key(request: ToolCallRequest) -> str:
        return OutputGuardMiddleware._step_info(request)[0]

    @staticmethod
    def _step_info(request: ToolCallRequest) -> tuple[str, int]:
        """(step key, number of tool calls in that step's AIMessage)."""
        cid = request.tool_call["id"]
        state = request.state if isinstance(request.state, dict) else {}
        for m in reversed(state.get("messages", [])):
            if isinstance(m, AIMessage) and any(tc["id"] == cid for tc in m.tool_calls):
                return (m.id or cid), len(m.tool_calls)
        return cid, 1

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
        if isinstance(env, dict) and isinstance(env.get("data"), dict):
            # small fields (next_cursor, total, page, ...) stay visible: paging works without the artifact
            meta = {k: v for k, v in env["data"].items() if len(json.dumps(v, ensure_ascii=False, default=str)) <= 300}
            return text[:n], {"ok": env.get("ok"), "data_fields": meta}
        if isinstance(env, dict):
            return text[:n], {"ok": env.get("ok")}
        return text[:n], {}

    def _guard(self, request: ToolCallRequest, msg: Any) -> Any:
        if not isinstance(msg, ToolMessage):
            return msg
        L = self.ctx.limits
        text = _text(msg.content)
        key, n_calls = self._step_info(request)
        with self._lock:
            used = self._used.get(key, 0)
            remaining = L.max_step_result_chars - used
            seen = self._seen_results.get(key, 0)
            self._seen_results[key] = seen + 1
            # every result of this step that is still to come gets at least a reference stub, so room for them is
            # reserved: the sum of all results of the step (stubs included) stays within max_step_result_chars
            still_to_come = max(n_calls - seen - 1, 0)
            room = remaining - still_to_come * STUB_RESERVE_CHARS
            if len(text) <= L.max_tool_result_chars and len(text) <= room:
                self._used[key] = used + len(text)
                return msg
            reason = (f"single result {len(text)} chars > limit {L.max_tool_result_chars}"
                      if len(text) > L.max_tool_result_chars else
                      f"step total would exceed {L.max_step_result_chars} chars (already {used})")
            name = request.tool_call["name"]
            args = json.dumps(request.tool_call["args"], ensure_ascii=False)[:300]
            art = self.ctx.artifacts.save(line_friendly(text), "tool_output", f"{name}({args})",
                                          {"tool": name, "args": request.tool_call["args"], "tool_call_id": request.tool_call["id"]})
            aid = art["artifact_id"]
            how = (f"read_artifact(artifact_id='{aid}', start_line=1, max_lines=60) "
                   f"or search_artifact(artifact_id='{aid}', query='...')")
            n = max(0, min(L.preview_chars, room - 900))
            preview, meta = self._preview(text, n)
            body = {"ok": meta.get("ok", True), "tool": name, "offloaded": True, "reason": reason,
                    "artifact_id": aid, "total_chars": len(text), "total_lines": art["lines"],
                    "preview_chars": len(preview), "preview": preview,
                    **({"data_fields": meta["data_fields"]} if meta.get("data_fields") else {}), "how_to_continue": how}
            content = json.dumps(body, ensure_ascii=False)
            if len(content) > room and "data_fields" in body:
                body.pop("data_fields")
                content = json.dumps(body, ensure_ascii=False)
            if len(content) > room:
                # the step budget is (almost) used up: only a minimal reference, still enough to read everything
                stub = {"ok": meta.get("ok", True), "tool": name, "offloaded": True, "reason": "step limit: reference only",
                        "artifact_id": aid, "total_chars": len(text), "how_to_continue": f"read_artifact(artifact_id='{aid}')"}
                content = json.dumps(stub, ensure_ascii=False)
            self._used[key] = used + len(content)
            step_total = self._used[key]
            if len(self._used) > 200:
                for k in list(self._used)[:100]:
                    self._used.pop(k, None)
                    self._seen_results.pop(k, None)
        self.ctx.emit("tool_output_offloaded", tool=name, artifact_id=aid, total_chars=len(text),
                      passed_chars=len(content), reason=reason, step=key, step_total_after=step_total,
                      step_limit=L.max_step_result_chars)
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

    SUMMARY_SECTIONS = ("Цель", "Ограничения пользователя", "Решения и договорённости",
                        "Подтверждённые факты (с источниками)", "Прогресс", "Открытые вопросы и следующий шаг",
                        "Артефакты")

    def _state_progress(self) -> str:
        try:
            st, tid = self.ctx.store, self.ctx.task_id
            docs = [f"{d['path']}: {d['pages_read']}/{d['pages']} стр." for d in st.documents_progress(tid)]
            q = st.url_counts(tid)
            secs = [s["section_id"] for s in st.sections(tid)]
            return ("по состоянию harness: " + "; ".join(docs + [f"evidence {len(st.evidence(tid))}",
                    f"очередь URL {q}" if any(q.values()) else "", f"разделы отчёта: {', '.join(secs) or 'нет'}"]))
        except Exception:  # noqa: BLE001 - a summary must never fail because of the digest
            return "по состоянию harness: см. PROGRESS DIGEST"

    def _complete_sections(self, text: str, msgs: list[BaseMessage], cut: bool) -> tuple[str, list[str]]:
        """The summary must keep all 7 sections. If the model ran into max_tokens, the unfinished last line is
        dropped; every missing section is then added by rules from the compacted messages (marked as such)."""
        if cut and "\n" in text:
            text = text[: text.rstrip().rfind("\n")].rstrip()
        # a heading with nothing under it counts as missing (A1 summary #3 had an empty "Прогресс"): drop it, re-add
        parts = re.split(r"^(##\s*.+?)\s*$", text, flags=re.M)
        kept, have = [parts[0]], set()
        for k in range(1, len(parts) - 1, 2):
            head, body = parts[k], parts[k + 1]
            name = re.sub(r"^##\s*", "", head).strip()
            if body.strip():
                kept += [head, body]
                have.add(name)
        text = "".join(kept).rstrip()
        missing = [h for h in self.SUMMARY_SECTIONS if h not in have]
        if not missing:
            return text, []
        arts = sorted({a for m in msgs for a in re.findall(r"\bart_[0-9a-f]{12,20}\b", _text(m.content))})
        evs = sorted({a for m in msgs for a in re.findall(r"\bev_[0-9a-f]{12,20}\b", _text(m.content))})
        calls = [tc["name"] for m in msgs if isinstance(m, AIMessage) for tc in m.tool_calls]
        plan = (self.ctx.store.get_task(self.ctx.task_id) or {}).get("plan") or []
        todo = [st.get("title", "") for st in plan if isinstance(st, dict) and st.get("status") not in ("done", "completed")]
        fill = {
            "Цель": "см. контракт задачи",
            "Ограничения пользователя": "см. контракт задачи (ограничения [C*] передаются в каждом вызове)",
            "Решения и договорённости": "—",
            "Подтверждённые факты (с источниками)": ("evidence в этом фрагменте: " + ", ".join(evs)) if evs else "—",
            # progress from the harness state (pages, evidence, queue, sections), not from the model's plan
            "Прогресс": self._state_progress() + ((f"; в сжатом фрагменте {len(calls)} вызовов tools: "
                         + ", ".join(f"{n}×{calls.count(n)}" for n in sorted(set(calls)))) if calls else ""),
            "Открытые вопросы и следующий шаг": ("невыполненные шаги плана: " + "; ".join(todo)) if todo else "по плану задачи",
            "Артефакты": ", ".join(arts) if arts else "—",
        }
        text += "".join(f"\n\n## {h}\n(дополнено правилами: модель не дописала раздел) {fill[h]}" for h in missing)
        return text, missing

    async def _summarize(self, prev: str, msgs: list[BaseMessage]) -> dict:
        ctx, L = self.ctx, self.ctx.limits
        cap_chars = int(L.summary_max_tokens * ctx.counter.cpt * 1.3)
        if ctx.summarizer is None:
            return {"summary": self._rules_summary(prev, msgs, cap_chars), "method": "rules(no summarizer)",
                    "chunks": 0, "cost_in": 0, "cost_out": 0}
        # Russian text costs about 2.5-3 tokens per word on qwen; with 0.6 words per token almost every summary
        # hit max_tokens and lost its last sections (audit of 20.11: 38 of 39 summaries ended mid-sentence)
        sys_text = SUMMARIZER_PROMPT.format(words=int(L.summary_max_tokens * 0.3))
        limit_in = min(ctx.working_limit, ctx.window) - L.summary_max_tokens
        rendered = [_render_for_summary(m, L.summary_tool_clip_chars) for m in msgs]
        contract = ("TASK CONTRACT (copy every user constraint VERBATIM with its [tag] into "
                    "'Ограничения пользователя'):\n" + ctx.contract_block())
        summary, method, chunks, cost_in, cost_out = prev, "llm", 0, 0, 0
        i = 0
        while i < len(rendered):
            if chunks >= L.max_compaction_chunks:
                summary = self._rules_summary(summary, msgs[i:], cap_chars)
                method = "llm+rules(chunk limit)"
                break
            fixed = (ctx.counter.text_tokens(sys_text) + ctx.counter.text_tokens(summary)
                     + ctx.counter.text_tokens(contract) + 200)
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
            user = (f"{contract}\n\nPREVIOUS SUMMARY:\n{summary or '(none)'}\n\nNEW HISTORY FRAGMENT ({j - i} messages):\n"
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
            except asyncio.CancelledError:
                # cancel while summarizing: release the reservation now (charged as interrupted), then propagate
                ctx.budget.interrupt(res_id, est_in)
                ctx.store.update_model_call(call_id, status="cancelled", duration_s=round(time.perf_counter() - t0, 2))
                raise
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
                cut = ((getattr(ai, "response_metadata", None) or {}).get("finish_reason") == "length"
                       or a_out >= L.summary_max_tokens - 8)      # qwen does not always report finish_reason
                summary, added = self._complete_sections(text[:cap_chars], msgs[i:j], cut)
                if added:
                    method = "llm+rules(sections " + ", ".join(added) + ")"
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
                 cost_output=result["cost_out"], note=note, summary=new_summary,
                 # the kept tail as it goes to the model: every tool result has its tool call (pairs are visible here)
                 kept_tail=[{"type": type(m).__name__,
                             **({"tool_calls": [tc["id"] for tc in m.tool_calls]} if isinstance(m, AIMessage) and m.tool_calls else {}),
                             **({"tool_call_id": m.tool_call_id} if isinstance(m, ToolMessage) else {})} for m in keep])
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
        self._run_calls = getattr(self, "_run_calls", 0) + 1
        if L.max_model_calls_per_run and self._run_calls > L.max_model_calls_per_run:
            ctx.emit("guardrail", guard="model_calls_per_run", limit=L.max_model_calls_per_run)
            return AIMessage(content=f"[harness] Лимит {L.max_model_calls_per_run} вызовов модели на один ход исчерпан. "
                                     "Отвечаю тем, что уже найдено; продолжить можно следующей репликой.")
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
        attempt, tries, unreachable_since = 0, 0, None
        while True:
            tries += 1
            try:
                res_id = ctx.budget.reserve("agent", total, est.tokens)
            except BudgetExhausted as e:
                ctx.store.update_model_call(call_id, status="blocked", note=f"budget: {e}")
                ctx.emit("guardrail", guard="token_budget", **e.as_dict())
                return self._stop("paused", f"budget exhausted before the call ({e})")
            t0 = time.perf_counter()
            try:
                resp = await asyncio.wait_for(handler(request), timeout=L.llm_timeout_s)
            except asyncio.CancelledError:
                # cancel from the user (runner cancels the graph task): the in-flight call is abandoned; its
                # reservation is released at once and the input estimate is charged as an interrupted call
                ctx.budget.interrupt(res_id, est.tokens)
                ctx.store.update_model_call(call_id, status="cancelled", note="interrupted by cancel",
                                            duration_s=round(time.perf_counter() - t0, 2))
                ctx.emit("model_call_cancelled", reservation=res_id, charged_input_estimate=est.tokens)
                raise
            except RETRYABLE as e:
                last_error = f"{type(e).__name__}: {e}"[:300]
                # "Connection error" = the request never reached the server (the ITMO endpoint drops out for a few
                # minutes now and then). Such a call costs nothing and is retried until llm_unreachable_wait_s runs
                # out; the ordinary retry counter is kept for errors the server actually answered with.
                unreachable = isinstance(e, openai.APIConnectionError) and not isinstance(e, openai.APITimeoutError)
                ctx.budget.fail(res_id, 0 if unreachable else est.tokens)
                if unreachable:
                    unreachable_since = unreachable_since or time.monotonic()
                    waited = time.monotonic() - unreachable_since
                    retry = waited < L.llm_unreachable_wait_s
                    try:
                        ctx.budget.check_deadline()
                    except BudgetExhausted:
                        retry = False                       # the task deadline is not extended by waiting
                else:
                    attempt += 1
                    retry = attempt <= L.llm_retries
                ctx.emit("model_call_error", attempt=tries, error=last_error, retry=retry,
                         **({"unreachable_for_s": round(waited, 1)} if unreachable else {}))
                if retry:
                    if unreachable:
                        pause = min(30.0, max(2.0, waited / 2))
                    else:
                        pause = min(30.0, L.backoff_base_s * 4 * (2 ** (attempt - 1)))
                    await asyncio.sleep(pause)
                    continue
                ctx.store.update_model_call(call_id, status="error", note=last_error,
                                            duration_s=round(time.perf_counter() - t0, 2))
                if unreachable:
                    return self._stop("paused", f"LLM unavailable for {round(waited)} s "
                                                f"(llm_unreachable_wait_s={L.llm_unreachable_wait_s}): {last_error}")
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
                # durable: a resumed process starts with the calibrated chars-per-token, not the default
                ctx.store.set_meta(f"token_calibration:{ctx.task_id}",
                                   {"cpt": round(ctx.counter.cpt, 4), "calibrations": ctx.counter.calibrations})
            ctx.store.update_model_call(call_id, status="ok", actual_input=a_in, actual_output=a_out,
                                        duration_s=round(time.perf_counter() - t0, 2))
            ctx.emit("model_call_done", estimated_input=est.tokens, actual_input=a_in, actual_output=a_out,
                     duration_s=round(time.perf_counter() - t0, 2), calibrated_cpt=round(ctx.counter.cpt, 3))
            return resp


def render_tokens(m: BaseMessage) -> int:  # small helper used by tests
    return len(render_message(m))
