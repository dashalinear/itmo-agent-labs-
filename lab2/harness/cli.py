"""Command-line interface of the harness (dialog CLI of lab 1 is kept as `chat`).

    python -m harness.cli new      --spec demo/task_a_local.json [--task-id A1] [--set key=value ...]
    python -m harness.cli run      --task-id A1 [--set key=value ...]     # start or continue in this process
    python -m harness.cli resume   --task-id A1 [--set key=value ...]     # same, labelled "resume" in traces
    python -m harness.cli status   --task-id A1 [--json]
    python -m harness.cli pause    --task-id A1                           # from another terminal
    python -m harness.cli cancel   --task-id A1
    python -m harness.cli limits   --task-id A1 [--set key=value ...]     # show / change limits (explicit user action)
    python -m harness.cli report   --task-id A1
    python -m harness.cli artifact --task-id A1 --id art_... [--start 1 --lines 40]
    python -m harness.cli chat     --task-id G1 [--script demo/dialog_guard_ru.txt]
    python -m harness.cli baseline --spec demo/task_a_local.json --task-id A_base [--max-model-calls 40]
    python -m harness.cli list | export --task-id A1

Global: --env FILE, --root DIR (task root), --no-mcp, --quiet, --provider ddgs|fixture.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from organizer.history import validate_history
from organizer.llm import strip_thinking
from organizer.tracing import Tracer

from .budget import Budget
from .config import Limits, load_config
from .runner import TaskRunner
from .store import TaskStore
from .tokens import TokenCounter


def _kv(items: list[str] | None) -> dict:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"--set expects key=value, got {it!r}")
        k, v = it.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _store(cfg) -> TaskStore:
    return TaskStore(cfg.state_dir / "state.db")


def print_status(store: TaskStore, task_id: str, as_json: bool = False) -> int:
    task = store.get_task(task_id)
    if not task:
        print(f"нет задачи {task_id}")
        return 1
    b = Budget(store, task_id, Limits.from_dict(task["limits"])).snapshot()
    docs = []
    for d in store.documents(task_id):
        pages = set()
        for r in store.ranges(task_id, d["path"], d["content_hash"]):
            if r["unit"] in ("page", "page_part"):        # read_pages or read_lines
                pages.update(range(r["start_no"], r["end_no"] + 1))
        docs.append({"path": d["path"], "pages_read": len(pages), "pages": d["pages"], "searched": bool(d["searched"])})
    info = {"task_id": task_id, "kind": task["kind"], "status": task["status"], "reason": task["status_reason"],
            "runs": task["runs"], "alive": store.alive(task_id), "plan": task["plan"],
            "documents": docs, "evidence": len(store.evidence(task_id)),
            "sections": [s["section_id"] for s in store.sections(task_id)], "queue": store.url_counts(task_id),
            "searches": [s["query"] for s in store.searches(task_id)], "compactions": len(store.compactions(task_id)),
            "summary_version": task.get("summary_version"), "budget": b,
            "last_errors": [e["data"] for e in store.events(task_id, "fetch_error")][-3:]}
    if as_json:
        print(json.dumps(info, ensure_ascii=False, indent=2, default=str))
        return 0
    print(f"Задача {task_id} [{task['kind']}] — статус: {task['status']} ({task['status_reason']}); запусков: {task['runs']}; "
          f"процесс активен: {info['alive']}")
    for d in docs:
        print(f"  документ {d['path']}: прочитано {d['pages_read']}/{d['pages']} стр., поиск: {'да' if d['searched'] else 'нет'}")
    q = info["queue"]
    if q["found_total"]:
        print(f"  очередь URL: найдено {q['found_total']}, отобрано {q['selected_total']}, прочитано {q['done']}, "
              f"ошибок {q['error']}, отклонено {q['skipped']}, ожидают {q['pending']}; запросов: {len(info['searches'])}")
    print(f"  evidence: {info['evidence']}; разделы отчёта: {', '.join(info['sections']) or '—'}; "
          f"компактификаций: {info['compactions']}")
    print(f"  бюджет: токены {b['tokens']['spent']}/{b['tokens']['limit']} (из них компактификация "
          f"{b['tokens']['of_which']['compaction_input_tokens'] + b['tokens']['of_which']['compaction_output_tokens']}), "
          f"вызовы модели {b['model_calls']['used']}/{b['model_calls']['limit']}, tools {b['tool_calls']['used']}/"
          f"{b['tool_calls']['limit']}, поиск {b['search_calls']['used']}/{b['search_calls']['limit']}, HTTP "
          f"{b['http_requests']['used']}/{b['http_requests']['limit']}, cache hits {b['cache_hits']}, время "
          f"{b['active_seconds']['used']}/{b['active_seconds']['limit']} с, пик входа {b['peak_input_tokens']}")
    return 0


async def chat(runner: TaskRunner, task_id: str, script: Path | None) -> int:
    lines = None
    if script:
        lines = [l for l in script.read_text(encoding="utf-8").splitlines() if l.strip() and not l.lstrip().startswith("#")]
    print("Диалог с агентом в harness. Команды: /status, /limits key=value, /report, /exit (проверка истории — в конце каждого хода)")
    while True:
        if lines is not None:
            if not lines:
                break
            text = lines.pop(0).strip()
            print(f"\n> {text}")
        else:
            try:
                text = (await asyncio.to_thread(input, "\n> ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
        if not text:
            continue
        if text in ("/exit", "/quit", "выход"):
            break
        if text == "/status":
            print_status(runner.store, task_id)
            continue
        if text.startswith("/limits"):
            kv = _kv(text.split()[1:])
            if kv:
                print("лимиты изменены пользователем:", kv)
                runner.set_limits(task_id, kv)
            print(json.dumps(runner.store.get_task(task_id)["limits"], ensure_ascii=False))
            continue
        if text == "/report":
            p = runner.cfg.state_dir / "tasks" / task_id / "report.md"
            print(p.read_text(encoding="utf-8")[:3000] if p.exists() else "отчёта ещё нет")
            continue
        await runner.run(task_id, user_text=text, label="chat")
    return 0


async def baseline(cfg, spec: dict, task_id: str, max_calls: int, no_mcp: bool) -> int:
    """Lab 1 agent on the same task: no harness. Passive metering only."""
    from organizer.mcp_client import MCPConnection, unavailable_tools
    from .agent import build_baseline_agent
    from .llm import build_models
    limits = cfg.limits.updated(spec.get("limits", {}))
    model = build_models(cfg, limits)[0]
    mcp = MCPConnection(cfg.base)
    tools = unavailable_tools("--no-mcp") if no_mcp else await mcp.__aenter__()
    counter = TokenCounter(cfg.chars_per_token, 0.0, "heuristic")
    try:
        agent, meter, all_tools = build_baseline_agent(cfg, model, tools, InMemorySaver(), cfg.root, max_calls, counter)
        session = f"{task_id}_baseline_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        tracer = Tracer(cfg.traces_dir, session)
        tracer.session_start(task_id=task_id, mode="baseline (lab 1 agent, no harness)", model=cfg.base.llm_model,
                             max_model_calls=max_calls, tools=[t.name for t in all_tools])
        request = spec["request"] + ("\n\nОграничения:\n" + "\n".join(f"- {c}" for c in spec.get("constraints", []))
                                     if spec.get("constraints") else "")
        run_id = str(uuid.uuid4())
        tracer.user_turn(run_id, request)
        t0, steps, error, transitions = time.perf_counter(), 0, None, ["__start__"]
        config = {"configurable": {"thread_id": session}, "recursion_limit": 4 * max_calls + 10}
        try:
            async for chunk in agent.astream({"messages": [HumanMessage(content=request)]}, config, stream_mode="updates"):
                for node, update in chunk.items():
                    steps += 1
                    transitions.append(node)
                    msgs = (update or {}).get("messages", []) if isinstance(update, dict) else []
                    tracer.step(run_id, steps, node, [m for m in msgs if hasattr(m, "content")])
                    for m in msgs:
                        if isinstance(m, AIMessage):
                            for tc in m.tool_calls:
                                print(f"  -> {tc['name']}({json.dumps(tc['args'], ensure_ascii=False)[:120]})", flush=True)
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"[:500]
        state = await agent.aget_state(config)
        msgs = list(state.values.get("messages", []))
        answer = next((strip_thinking(str(m.content)) for m in reversed(msgs)
                       if isinstance(m, AIMessage) and not m.tool_calls and str(m.content).strip()), "")
        tracer.turn_end(run_id, answer, transitions, validate_history(msgs), error)
        calls = meter.calls
        res = {"task_id": task_id, "mode": "baseline", "error": error, "duration_s": round(time.perf_counter() - t0, 1),
               "model_calls": len(calls), "failed_calls": sum(1 for c in calls if c["status"] != "ok"),
               "tool_calls": sum(len(m.tool_calls) for m in msgs if isinstance(m, AIMessage)),
               "input_tokens": sum(c.get("actual_input") or 0 for c in calls),
               "output_tokens": sum(c.get("actual_output") or 0 for c in calls),
               "peak_input_tokens": max([c.get("actual_input") or c["estimated_input"] for c in calls] or [0]),
               "peak_estimated_input": max([c["estimated_input"] for c in calls] or [0]),
               "answer": answer, "calls": calls, "trace": str(tracer.md)}
        out = cfg.results_dir / task_id
        out.mkdir(parents=True, exist_ok=True)
        (out / "baseline.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        tracer.event("session_end", **{k: v for k, v in res.items() if k != "calls"})
        print(f"\n{answer}\n\nbaseline: {json.dumps({k: v for k, v in res.items() if k not in ('calls', 'answer')}, ensure_ascii=False)}")
        return 0
    finally:
        if not no_mcp:
            await mcp.__aexit__(None, None, None)


async def amain(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="harness")
    ap.add_argument("--env", type=Path)
    ap.add_argument("--root", type=Path, help="task root (default TASK_ROOT or lab2/workspace)")
    ap.add_argument("--no-mcp", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--provider", choices=["ddgs", "fixture"])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("new", "run", "resume", "status", "pause", "cancel", "limits", "report", "artifact", "chat",
                 "baseline", "list", "export", "window"):
        p = sub.add_parser(name)
        if name not in ("list", "window"):
            p.add_argument("--task-id", required=name not in ("new", "baseline"))
        if name in ("new", "baseline"):
            p.add_argument("--spec", type=Path, required=True)
        if name in ("new", "run", "resume", "limits"):
            p.add_argument("--set", nargs="*", default=[])
        if name == "status":
            p.add_argument("--json", action="store_true")
        if name in ("pause", "cancel"):
            p.add_argument("--reason", default="")
        if name == "artifact":
            p.add_argument("--id", required=True)
            p.add_argument("--start", type=int, default=1)
            p.add_argument("--lines", type=int, default=40)
        if name == "chat":
            p.add_argument("--script", type=Path)
        if name == "window":
            p.add_argument("--refresh", action="store_true", help="measure again, ignore the cache")
        if name == "baseline":
            p.add_argument("--max-model-calls", type=int, default=40)
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    cfg = load_config(args.env, args.root)
    if args.provider:
        cfg = replace(cfg, search_provider=args.provider)

    if args.cmd == "window":
        # real context window of the configured model, as one JSON line (used by run_all_demos.ps1)
        from .tokens import detect_window
        w, src = detect_window(cfg.base.llm_base_url, cfg.base.llm_api_key, cfg.base.llm_model,
                               cache=cfg.window_cache, refresh=args.refresh)
        if not w and cfg.context_window:
            w, src = cfg.context_window, f"LLM_CONTEXT_WINDOW (.env); server: {src}"
        print(json.dumps({"model": cfg.base.llm_model, "window": w, "source": src}, ensure_ascii=False))
        return 0 if w else 3

    if args.cmd in ("status", "pause", "cancel", "list", "report", "artifact"):
        store = _store(cfg)
        try:
            if args.cmd == "list":
                for t in store.list_tasks():
                    print(f"{t['task_id']:<20} {t['kind']:<6} {t['status']:<10} runs={t['runs']} {t['status_reason'] or ''}")
                return 0
            task = store.get_task(args.task_id)
            if not task:
                print(f"нет задачи {args.task_id}")
                return 1
            if args.cmd == "status":
                return print_status(store, args.task_id, args.json)
            if args.cmd in ("pause", "cancel"):
                if task["status"] in ("completed", "cancelled"):
                    print(f"задача уже {task['status']}")
                    return 1
                store.request_control(args.task_id, args.cmd, args.reason)
                if store.alive(args.task_id):
                    print(f"{args.cmd}: запрос передан работающему процессу (сработает на ближайшей границе шага"
                          + ("; текущая операция LLM/HTTP будет прервана" if args.cmd == "cancel" else "") + ")")
                else:
                    store.clear_control(args.task_id)
                    store.set_status(args.task_id, "cancelled" if args.cmd == "cancel" else "paused",
                                     f"{args.cmd} by the user (no running process)")
                    print(f"{args.cmd}: процесс не запущен, статус изменён сразу")
                return 0
            if args.cmd == "report":
                p = cfg.state_dir / "tasks" / args.task_id / "report.md"
                print(p.read_text(encoding="utf-8") if p.exists() else "отчёта ещё нет")
                return 0
            if args.cmd == "artifact":
                from .artifacts import ArtifactStore
                from .workspace import HarnessWorkspace
                arts = ArtifactStore(HarnessWorkspace(cfg.root), store, args.task_id)
                print(json.dumps(arts.read(args.id, args.start, args.lines), ensure_ascii=False, indent=2))
                return 0
        finally:
            store.close()

    if args.cmd == "baseline":
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        return await baseline(cfg, spec, args.task_id, args.max_model_calls, args.no_mcp)

    async with TaskRunner(cfg, verbose=not args.quiet, use_mcp=not args.no_mcp) as runner:
        if args.cmd == "new":
            spec = json.loads(args.spec.read_text(encoding="utf-8"))
            task = runner.create(spec, args.task_id, _kv(args.set))
            print(f"создана задача {task['task_id']} ({task['kind']}), статус {task['status']}; лимиты: "
                  f"{json.dumps(task['limits'], ensure_ascii=False)}")
            return 0
        if args.cmd == "limits":
            kv = _kv(args.set)
            if kv:
                runner.set_limits(args.task_id, kv)
                print("лимиты изменены пользователем:", kv)
            print(json.dumps(runner.store.get_task(args.task_id)["limits"], ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "export":
            print(runner.export(args.task_id))
            return 0
        if args.cmd in ("run", "resume"):
            res = await runner.run(args.task_id, overrides=_kv(args.set), label=args.cmd)
            if res.get("message") or res.get("reason"):
                print(f"[{res['status']}] {res.get('message') or res.get('reason')}")
            return 0 if res.get("status") in ("completed", "paused", "cancelled") else 2
        if args.cmd == "chat":
            return await chat(runner, args.task_id, getattr(args, "script", None))
    return 0


def main() -> None:
    try:
        sys.exit(asyncio.run(amain()))
    except KeyboardInterrupt:
        # Ctrl+C: the next run charges in-flight reservations and continues from the last checkpoint
        print("\nпрервано пользователем (Ctrl+C); продолжить: python -m harness.cli resume --task-id <id>")
        sys.exit(130)


if __name__ == "__main__":
    main()
