"""Interactive CLI of the file organizer.

    python -m organizer.cli                      # interactive dialog
    python -m organizer.cli --script demo/dialog_ru.txt   # replay user turns

History: one LangGraph thread per CLI session (InMemorySaver checkpointer).
Each turn passes ONLY the new HumanMessage; the graph's add_messages reducer
appends AI/tool messages with their tool_call_id, so nothing is lost or
duplicated. Commands: /help /history /check /catalog /graph /exit.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import uuid
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

EXIT_WORDS = {"/exit", "/quit", "exit", "quit", "выход"}
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
        transitions, error, step = ["__start__"], None, 0
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
        except TimeoutError:
            error = f"turn timeout {self.settings.turn_timeout_s}s"
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
        usage = {"input_tokens": 0, "output_tokens": 0}
        for m in produced:
            u = getattr(m, "usage_metadata", None) or {}
            usage["input_tokens"] += u.get("input_tokens", 0)
            usage["output_tokens"] += u.get("output_tokens", 0)
        problems = validate_history(msgs)
        self.tracer.turn_end(run_id, answer, transitions, problems, error, usage)
        return answer

    def _echo(self, node: str, msgs) -> None:
        for m in msgs:
            if isinstance(m, AIMessage):
                for tc in m.tool_calls:
                    print(f"  ↳ [{node}] {tc['name']}({_short(tc['args'])})", flush=True)
            elif isinstance(m, ToolMessage):
                ok = "✗" if m.status == "error" or '"ok": false' in str(m.content)[:40] else "✓"
                print(f"    {ok} {m.name}", flush=True)

    async def command(self, cmd: str) -> bool:
        """Handle /commands. Returns False to exit."""
        c = cmd.strip().lower()
        if c in EXIT_WORDS:
            return False
        msgs = await self.messages()
        if c == "/help":
            print(HELP)
        elif c == "/history":
            print(json.dumps(history_summary(msgs), ensure_ascii=False, indent=2))
        elif c == "/check":
            problems = validate_history(msgs)
            print("История корректна ✓" if not problems else "\n".join(problems))
        elif c == "/catalog":
            cat = self.bundle.catalog.load()
            for d in cat["documents"]:
                print(f"{d['indexing_status']:<12} {d['current_path']}  [{d['document_id'][:8]}] {d['title']}")
        elif c == "/graph":
            print(self.bundle.agent.get_graph().draw_mermaid())
        else:
            print("Неизвестная команда. " + HELP)
        return True


def add_late_files(settings: Settings, tracer: Tracer) -> list[str]:
    """Simulates the user dropping the two prepared files into input/."""
    src = PROJECT_ROOT / "fixtures" / "late"
    copied = []
    for f in sorted(src.glob("*")):
        dst = settings.workspace / "input" / f.name
        if not dst.exists():
            shutil.copy2(f, dst)
            copied.append(f"input/{f.name}")
    tracer.user_action("add_late_files", files=copied)
    return copied


async def amain(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="organizer", description="Conversational file organizer (lab 1)")
    ap.add_argument("--script", type=Path, help="replay user turns from a file (one per line; '@add_late_files' = manual action)")
    ap.add_argument("--env", type=Path, help="alternative .env file")
    ap.add_argument("--no-mcp", action="store_true", help="start without the Elasticsearch MCP server")
    ap.add_argument("--quiet", action="store_true", help="do not echo tool calls")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")

    settings = load_settings(args.env)
    session_id = datetime.now().strftime("session_%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    tracer = Tracer(settings.traces_dir, session_id)
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
        while True:
            if lines is not None:
                if not lines:
                    break
                text = lines.pop(0)
                print(f"\n> {text}")
            else:
                try:
                    text = await asyncio.to_thread(input, "\n> ")
                except (EOFError, KeyboardInterrupt):
                    break
            text = text.strip()
            if not text:
                continue
            if text == "@add_late_files":
                print("  [ручное действие пользователя] добавлены:", add_late_files(settings, tracer))
                continue
            if text.startswith("/") or text.lower() in EXIT_WORDS:
                if not await sess.command(text):
                    break
                continue
            answer = await sess.run_turn(text)
            print(f"\n{answer}")
        tracer.event("session_end", history=history_summary(await sess.messages()))
        print(f"\nСессия завершена. Трасса: {tracer.md}")
        return 0
    finally:
        await mcp.__aexit__(None, None, None)


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
