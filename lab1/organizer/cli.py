"""Interactive CLI of the file organizer.

    python -m organizer.cli                      # interactive dialog
    python -m organizer.cli --script demo/dialog_ru.txt   # replay user turns

In a script, '@wait_for_files input/a.md input/b.md' pauses the session until
those files are added to input/ by another process (second terminal, Explorer,
scripts/run_all_demos.ps1); the CLI only waits for them.

History: one LangGraph thread per CLI session (InMemorySaver checkpointer).
Each turn passes only the new HumanMessage; the graph's add_messages reducer
appends AI/tool messages with their tool_call_id, so the history keeps every
message exactly once. Commands: /help /history /check /catalog /graph /exit.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from .agent import AgentBundle, build_agent
from .config import PROJECT_ROOT, Settings, load_settings
from .history import dangling_tool_calls, history_summary, validate_history
from .llm import build_llm, strip_thinking
from .mcp_client import MCPConnection, unavailable_tools
from .tracing import Tracer


def empty_answer_note(produced: list) -> str:
    fails = []
    for m in produced:
        if not isinstance(m, ToolMessage):
            continue
        try:
            body = json.loads(str(m.content))
        except ValueError:
            continue
        if isinstance(body, dict) and body.get("ok") is False:
            fails.append(f"{body.get('tool')}: {(body.get('error') or {}).get('type')}")
    last = next((m for m in reversed(produced) if isinstance(m, AIMessage)), None)
    out = ((getattr(last, "usage_metadata", None) or {}).get("output_tokens", 0)) if last else 0
    return (f"[Модель вернула пустой ответ (output_tokens последнего вызова: {out}). "
            f"Ошибки инструментов в этом ходе: {', '.join(dict.fromkeys(fails)) or 'нет'}.]")

EXIT_WORDS = {"/exit", "/quit", "exit", "quit", "выход"}
MODEL_LIMIT_MARK = "Model call limits exceeded"   # text of ModelCallLimitMiddleware's final AIMessage
WAIT_DIRECTIVE = "@wait_for_files"
HELP = """Команды: /history — статистика истории, /check — проверка пар tool_call/tool_result,
/catalog — сводка каталога, /graph — mermaid-схема графа, /exit — выход.
Любой другой текст отправляется агенту."""


def _short(args: dict, limit: int = 140) -> str:
    s = json.dumps(args, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + "…"


class Session:
    def __init__(self, settings: Settings, bundle: AgentBundle, tracer: Tracer, verbose: bool = True) -> None:
        self.settings = settings
        self.bundle = bundle
        self.tracer = tracer
        self.thread_id = tracer.session_id
        self.verbose = verbose

    @property
    def config_base(self) -> dict:
        return {"configurable": {"thread_id": self.thread_id}}

    async def messages(self):
        state = await self.bundle.agent.aget_state(self.config_base)
        return list(state.values.get("messages", [])) if state and state.values else []

    async def _repair(self, reason: str) -> None:
        """Close dangling tool calls so the next turn has a valid history."""
        msgs = await self.messages()
        dangling = dangling_tool_calls(msgs)
        if dangling:
            fixes = [ToolMessage(content=json.dumps({"ok": False, "error": {"type": "cancelled", "message": reason}}),
                                 tool_call_id=tc["id"], name=tc["name"], status="error") for tc in dangling]
            await self.bundle.agent.aupdate_state(self.config_base, {"messages": fixes}, as_node="tools")
            self.tracer.note(f"repaired {len(fixes)} dangling tool call(s): {reason}")

    async def run_turn(self, text: str) -> str:
        run_id = str(uuid.uuid4())
        config = {**self.config_base, "recursion_limit": self.settings.recursion_limit,
                  "run_id": run_id, "run_name": "user_turn",
                  "metadata": {"session_id": self.thread_id}}
        self.tracer.user_turn(run_id, text)
        before = len(await self.messages())
        transitions, error, step, stopped_by = ["__start__"], None, 0, None
        try:
            async with asyncio.timeout(self.settings.turn_timeout_s):
                async for chunk in self.bundle.agent.astream(
                        {"messages": [HumanMessage(content=text)]}, config, stream_mode="updates"):
                    for node, update in chunk.items():
                        step += 1
                        transitions.append(node)
                        new_msgs = (update or {}).get("messages", []) if isinstance(update, dict) else []
                        self.tracer.step(run_id, step, node, new_msgs)
                        if self.verbose:
                            self._echo(node, new_msgs)
        except GraphRecursionError:
            error = f"execution limit: recursion_limit={self.settings.recursion_limit} reached"
            stopped_by = "recursion_limit"
        except TimeoutError:
            error = f"turn timeout {self.settings.turn_timeout_s}s"
            stopped_by = "turn_timeout"
        except Exception as e:  # noqa: BLE001 - LLM/network errors must not kill the CLI
            error = f"{type(e).__name__}: {e}"[:500]
        if error:
            transitions.append("[stopped]")
            await self._repair(error)
        else:
            transitions.append("__end__")
        msgs = await self.messages()
        produced = msgs[before:]
        answer = ""
        for m in reversed(produced):
            if isinstance(m, AIMessage) and not m.tool_calls and str(m.content).strip():
                answer = strip_thinking(str(m.content))
                break
        if error:
            answer = (answer + "\n\n" if answer else "") + f"[Ход остановлен: {error}]"
        elif any(isinstance(m, AIMessage) and str(m.content).startswith(MODEL_LIMIT_MARK) for m in produced):
            # ModelCallLimitMiddleware(exit_behavior="end") ended the turn with its own AIMessage
            stopped_by = "model_call_limit"
            answer += (f"\n\n[Ход остановлен лимитом выполнения: model_calls_per_turn="
                       f"{self.settings.model_calls_per_turn} (ModelCallLimitMiddleware)]")
        if not answer.strip():
            # qwen sometimes spends the whole output budget on hidden reasoning and returns empty text.
            # Then the user at least sees which tools failed in this turn instead of a blank line.
            answer = empty_answer_note(produced)
        usage = {"input_tokens": 0, "output_tokens": 0}
        for m in produced:
            u = getattr(m, "usage_metadata", None) or {}
            usage["input_tokens"] += u.get("input_tokens", 0)
            usage["output_tokens"] += u.get("output_tokens", 0)
        problems = validate_history(msgs)
        self.tracer.turn_end(run_id, answer, transitions, problems, error, usage, stopped_by=stopped_by)
        return answer

    def _echo(self, node: str, msgs) -> None:
        for m in msgs:
            if isinstance(m, AIMessage):
                for tc in m.tool_calls:
                    print(f"  ↳ [{node}] {tc['name']}({_short(tc['args'])})", flush=True)
            elif isinstance(m, ToolMessage):
                ok = "ERR" if m.status == "error" or '"ok": false' in str(m.content)[:40] else "ok"
                print(f"    {ok} {m.name}", flush=True)

    async def command(self, cmd: str) -> bool:
        """Handle /commands. Returns False to exit. Every command is recorded in the trace
        (event ``cli_command``) with a short machine-readable result."""
        c = cmd.strip().lower()
        if c in EXIT_WORDS:
            self.tracer.cli_command(cmd.strip(), result="exit")
            return False
        msgs = await self.messages()
        ok, result = True, None
        if c == "/help":
            print(HELP)
            result = "help shown"
        elif c == "/history":
            summary = history_summary(msgs)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            result = summary
        elif c == "/check":
            problems = validate_history(msgs)
            print("История корректна: OK" if not problems else "\n".join(problems))
            ok = not problems
            result = {"history_ok": ok, "messages": len(msgs), "problems": problems[:20]}
        elif c == "/catalog":
            cat = self.bundle.catalog.load()
            for d in cat["documents"]:
                print(f"{d['indexing_status']:<12} {d['current_path']}  [{d['document_id'][:8]}] {d['title']}")
            counts: dict[str, int] = {}
            for d in cat["documents"]:
                counts[d["indexing_status"]] = counts.get(d["indexing_status"], 0) + 1
            result = {"documents": len(cat["documents"]), "status_counts": counts}
        elif c == "/graph":
            mermaid = self.bundle.agent.get_graph().draw_mermaid()
            print(mermaid)
            result = {"mermaid_chars": len(mermaid)}
        else:
            print("Неизвестная команда. " + HELP)
            ok, result = False, "unknown command"
        self.tracer.cli_command(cmd.strip(), result=result, ok=ok)
        return True


def _input_files(settings: Settings) -> dict[str, int]:
    root = settings.workspace
    return {p.relative_to(root).as_posix(): p.stat().st_size
            for p in (root / "input").rglob("*") if p.is_file()}


async def wait_for_files(settings: Settings, tracer: Tracer, names: list[str], timeout_s: float = 600.0,
                         marker: Path | None = None, poll_s: float = 0.5) -> list[str]:
    """Script pause: wait until files appear in input/. They are added from outside the CLI (a second
    terminal, Explorer or the demo PowerShell script); this function checks that the files exist and
    their size is stable, then logs what appeared. The agent finds them with a new ``ls`` on the next
    user turn.

    ``names``: workspace-relative paths (``input/x.md``) or bare file names; empty = any new file.
    ``marker``: optional file written while waiting, so an external script knows when to add files.
    """
    targets = [n.replace("\\", "/").lstrip("/") for n in names]
    targets = [t if t.startswith("input/") else f"input/{t}" for t in targets]
    if any(".." in t.split("/") for t in targets):
        raise ValueError(f"{WAIT_DIRECTIVE}: paths must stay inside workspace/input: {targets}")
    before = _input_files(settings)
    tracer.event("harness_wait", waiting_for=targets or "any new file in input/", timeout_s=timeout_s,
                 marker=marker.name if marker else None, pid=os.getpid())
    print(f"  [пауза сценария] жду, пока пользователь добавит файлы в input извне "
          f"({', '.join(targets) or 'любой новый файл'}); этот процесс ничего не копирует", flush=True)
    if marker:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"waiting_for": targets, "pid": os.getpid(),
                                      "workspace_input": str(settings.workspace / "input")}), encoding="utf-8")
    t0, last, timed_out = time.monotonic(), None, False
    try:
        while True:
            now = _input_files(settings)
            if targets:
                ready = all(t in now for t in targets)
                snapshot = {t: now.get(t) for t in targets}
            else:
                ready = bool(set(now) - set(before))
                snapshot = {k: v for k, v in now.items() if k not in before}
            if ready and snapshot == last:      # present and size unchanged for one poll interval
                break
            last = snapshot if ready else None
            if time.monotonic() - t0 > timeout_s:
                timed_out = True
                break
            await asyncio.sleep(poll_s)
    finally:
        if marker:
            marker.unlink(missing_ok=True)
    appeared = sorted(set(_input_files(settings)) - set(before))
    tracer.user_action("external_files_added", files=appeared, waited_s=round(time.monotonic() - t0, 1),
                       timed_out=timed_out, added_by="another process (this CLI only waited, copied nothing)")
    if timed_out:
        print(f"  [пауза сценария] таймаут {timeout_s}s: файлы так и не появились", flush=True)
    else:
        print("  [пауза сценария] в input появились:", appeared, flush=True)
    return appeared


def add_late_files(settings: Settings, tracer: Tracer) -> list[str]:
    """Older shortcut ('@add_late_files'): copy fixtures/late/* into input/ from the CLI process.
    The demo scripts use '@wait_for_files' instead, where the files are added from outside the CLI."""
    src = PROJECT_ROOT / "fixtures" / "late"
    copied = []
    for f in sorted(src.glob("*")):
        dst = settings.workspace / "input" / f.name
        if not dst.exists():
            shutil.copy2(f, dst)
            copied.append(f"input/{f.name}")
    tracer.user_action("add_late_files", files=copied, added_by="this CLI process (legacy @add_late_files)")
    return copied


async def amain(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="organizer", description="Conversational file organizer (lab 1)")
    ap.add_argument("--script", type=Path, help="replay user turns from a file (one per line; "
                    "'@wait_for_files <paths>' = pause until the files are added to input/ from outside)")
    ap.add_argument("--env", type=Path, help="alternative .env file")
    ap.add_argument("--no-mcp", action="store_true", help="start without the Elasticsearch MCP server")
    ap.add_argument("--quiet", action="store_true", help="do not echo tool calls")
    ap.add_argument("--wait-marker", type=Path, help="file written while '@wait_for_files' waits "
                    "(lets an external script know when to add the files)")
    ap.add_argument("--wait-timeout", type=float, default=600.0, help="seconds '@wait_for_files' waits (default 600)")
    ap.add_argument("--dry-run-model", action="store_true",
                    help="use a deterministic stand-in model instead of the LLM (checks scripts/plumbing only; "
                         "not for demo traces)")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")

    settings = load_settings(args.env)
    session_id = datetime.now().strftime("session_%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    tracer = Tracer(settings.traces_dir, session_id)
    if args.dry_run_model:
        from .testing import DryRunChatModel
        settings = replace(settings, llm_model="dry-run-model (scripted stand-in, NOT an LLM)", llm_base_url="")
        model = DryRunChatModel()
    else:
        model = build_llm(settings)

    mcp = MCPConnection(settings)
    if args.no_mcp:
        mcp_tools, mcp_status = unavailable_tools("disabled with --no-mcp"), "MCP disabled (--no-mcp)"
    else:
        mcp_tools = await mcp.__aenter__()
        mcp_status = mcp.status
    try:
        bundle = build_agent(settings, model, mcp_tools)
        sess = Session(settings, bundle, tracer, verbose=not args.quiet)
        tracer.session_start(thread_id=sess.thread_id, model=settings.llm_model,
                             llm_base_url=settings.llm_base_url, workspace=str(settings.workspace),
                             es_index=settings.es_index, mcp=mcp_status,
                             tools=[t.name for t in bundle.local_tools + bundle.mcp_tools],
                             skills=list(bundle.skills.skills),
                             limits={"recursion_limit": settings.recursion_limit,
                                     "model_calls_per_turn": settings.model_calls_per_turn,
                                     "tool_timeout_s": settings.tool_timeout_s,
                                     "turn_timeout_s": settings.turn_timeout_s,
                                     "max_tool_output_chars": settings.max_tool_output_chars})
        print(f"Организатор файлов · модель {settings.llm_model} · {mcp_status}")
        print(f"workspace: {settings.workspace}\nтрасса: {tracer.md}\n{HELP}\n")

        lines = None
        if args.script:
            lines = [l.rstrip("\n") for l in args.script.read_text(encoding="utf-8").splitlines()]
            lines = [l for l in lines if l.strip() and not l.lstrip().startswith("#")]
        ended_by, turns = None, 0
        try:
            while True:
                if lines is not None:
                    if not lines:
                        ended_by = "end_of_script"
                        break
                    text = lines.pop(0)
                    print(f"\n> {text}")
                else:
                    try:
                        text = await asyncio.to_thread(input, "\n> ")
                    except EOFError:
                        ended_by = "eof (Ctrl+Z / Ctrl+D)"
                        break
                    except KeyboardInterrupt:
                        ended_by = "ctrl_c"
                        break
                text = text.strip()
                if not text:
                    continue
                if text == "@add_late_files":
                    copied = add_late_files(settings, tracer)
                    print("  [ручное действие пользователя] добавлены:", copied)
                    tracer.cli_command(text, result={"copied_by_this_cli": copied})
                    continue
                if text.split()[0] == WAIT_DIRECTIVE:
                    appeared = await wait_for_files(settings, tracer, text.split()[1:], timeout_s=args.wait_timeout,
                                                    marker=args.wait_marker)
                    tracer.cli_command(text, result={"appeared": appeared}, ok=bool(appeared))
                    continue
                if text.startswith("/") or text.lower() in EXIT_WORDS:
                    if not await sess.command(text):
                        ended_by = f"exit_command ({text})"
                        break
                    continue
                turns += 1
                answer = await sess.run_turn(text)
                print(f"\n{answer}")
        except (KeyboardInterrupt, asyncio.CancelledError):
            ended_by = "ctrl_c"
            raise
        except Exception as e:  # noqa: BLE001 - recorded, then re-raised
            ended_by = f"error: {type(e).__name__}: {e}"[:300]
            raise
        finally:
            try:
                hist = history_summary(await sess.messages())
            except BaseException:  # noqa: BLE001 - never lose the session_end record
                hist = None
            tracer.event("session_end", ended_by=ended_by or "unknown", user_turns=turns, history=hist)
        print(f"\nСессия завершена ({ended_by}). Трасса: {tracer.md}")
        return 0
    finally:
        await mcp.__aexit__(None, None, None)


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
