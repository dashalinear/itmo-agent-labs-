"""CLI of lab 4 (run from lab4/, Temporal service on localhost:7233).

    python -m topicflow.cli prepare [--reset]            # workspace, Elasticsearch indices, registry
    python -m topicflow.cli worker                       # Temporal worker (both workflows and all activities)
    python -m topicflow.cli watch                        # file detection in workspace/input -> IngestNewFiles
    python -m topicflow.cli drop --set initial|arrivals|new_topic [--repeat]   # user adds files (atomic .part -> rename)
    python -m topicflow.cli schedule create [--every 10m] [--no-trigger] | describe | pause | unpause | trigger | delete
    python -m topicflow.cli topics-now [--set k=v ...]   # one extra TopicReport run outside the schedule (failure demos)
    python -m topicflow.cli replay --batch <batch_id>    # repeat a detection event for an existing batch
    python -m topicflow.cli status                       # registry (files, batches, reports) + Temporal executions
    python -m topicflow.cli l0                           # L0: corpus totals and the reports block (latest + history)
    python -m topicflow.cli open-report [--latest | --run-key K]   # open a report from L0 (L0 -> L1 -> text)
    python -m topicflow.cli fault --activity NAME [--point before|after_write] [--times N|always] | --clear
    python -m topicflow.cli wait [--batches] [--workflow-id W] [--schedule-runs N] [--timeout 1800]
    python -m topicflow.cli trace [--workflow-id W]      # Temporal histories -> traces/temporal (+ summary.md)
    python -m topicflow.cli export                       # reports, L0, registry, catalog -> results/
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ._paths import LAB4_ROOT
from . import config as lab4_config

CORPUS = LAB4_ROOT / "corpus"


def _kv(items: list[str]) -> dict:
    from harness.cli import _kv as kv
    return kv(items)


async def _client(st):
    from temporalio.client import Client
    return await Client.connect(st.temporal_address, namespace=st.namespace)


# ------------------------------------------------------------------------------------------ local commands
def prepare(cfg, reset: bool) -> None:
    from navsearch.index import CorpusIndex
    from harness.workspace import HarnessWorkspace
    root = cfg.root
    if reset and root.exists():
        assert root.name == "workspace", root
        shutil.rmtree(root)
    ws = HarnessWorkspace(root)
    (ws.root / "rejected").mkdir(parents=True, exist_ok=True)
    idx = CorpusIndex()
    if reset:
        idx.drop()
    idx.ensure()
    print(f"task root: {root}\nElasticsearch indices: {idx.l1}, {idx.l2}\ninput is empty: {not any(ws.input.iterdir())}")


def drop(cfg, which: str, repeat: bool, slow: bool, only: str | None = None) -> list[str]:
    """User action: put files into input. Written as <name>.part and renamed when complete (finished-write signal)."""
    src = CORPUS / which
    out = []
    for f in sorted(src.glob("*")):
        if only and f.name != only:
            continue
        name = f.name if not repeat else f"{f.stem}_copy{f.suffix}"
        dst = cfg.root / "input" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        part = dst.with_name(dst.name + ".part")
        data = f.read_bytes()
        with open(part, "wb") as fh:
            if slow:
                for i in range(0, len(data), 64):
                    fh.write(data[i:i + 64])
                    fh.flush()
                    time.sleep(0.05)
            else:
                fh.write(data)
        part.replace(dst)
        out.append(f"input/{name}")
    print("added:", out)
    return out


def l0_view(cfg) -> dict:
    from harness.workspace import HarnessWorkspace
    from navsearch.levels import Levels
    from organizer.catalog import Catalog
    ws = HarnessWorkspace(cfg.root)
    l0 = Levels(ws, Catalog(ws)).rebuild_l0()
    view = {"totals": l0["totals"], "folders": l0["folders"], "reports": l0.get("reports")}
    print(json.dumps(view, ensure_ascii=False, indent=2))
    return view


def open_report(cfg, run_key: str | None) -> int:
    from harness.workspace import HarnessWorkspace
    from navsearch.levels import Levels
    from organizer.catalog import Catalog
    ws = HarnessWorkspace(cfg.root)
    lv = Levels(ws, Catalog(ws))
    l0 = lv.rebuild_l0()
    reports = l0.get("reports")
    if not reports:
        print("в L0 ещё нет отчётов")
        return 1
    if run_key == "complete":
        item = reports.get("latest_complete")
    else:
        item = reports["latest"] if not run_key else next((h for h in reports["history"] if h["run_key"] == run_key), None)
    if not item:
        print(f"в L0 нет отчёта {run_key}")
        return 1
    l1 = lv.get_l1(item["document_id"])
    where = "latest" if not run_key else ("latest_complete" if run_key == "complete" else "history")
    print(f"L0 reports.{where} -> document_id {item['document_id']} ({item['status']})")
    print(f"L1: {l1['title']} | {l1['description']}\n    sections: " + ", ".join(s["title"] for s in l1["sections"]))
    print(f"L2/source: {item['path']}\n" + "-" * 80)
    print((ws.root / item["path"]).read_text(encoding="utf-8"))
    return 0


def status(cfg) -> None:
    from .registry import Registry
    reg = Registry(cfg.state_dir / "lab4.db")
    print("== batches (IngestNewFiles)")
    for b in reg.batches():
        r = b["result"] or {}
        print(f"  {b['batch_id']}  {b['status']:<10} wf={b['workflow_id']} run={b['run_id']} task={b['task_id']} "
              f"files={len(b['files'])} docs={len(r.get('documents', []))} dups={len(r.get('duplicates', []))}")
    print("== files")
    for f in reg.files():
        print(f"  {f['input_path']:<36} {f['status']:<9} batch={f['batch_id']} -> {f['current_path'] or f['staged_path'] or ''} "
              f"{f['document_id'] or ''} {('(' + f['note'] + ')') if f['note'] else ''}"[:220])
    print("== reports (TopicReport)")
    for r in reg.reports():
        print(f"  {r['run_key']:<48} {r['status']:<9} task={r['task_id']} frozen={r['frozen']} -> {r['path'] or ''}")
    reg.close()


# ------------------------------------------------------------------------------------------ Temporal commands
async def schedule_cmd(st, action: str, every: str | None, trigger: bool) -> None:
    from temporalio.client import (Schedule, ScheduleActionStartWorkflow, ScheduleIntervalSpec, ScheduleOverlapPolicy,
                                   SchedulePolicy, ScheduleSpec, ScheduleState)
    client = await _client(st)
    h = client.get_schedule_handle(st.schedule_id)
    if action == "create":
        interval = lab4_config._duration(every) if every else st.topics_every
        await client.create_schedule(
            st.schedule_id,
            Schedule(action=ScheduleActionStartWorkflow("TopicReport", {"source": "schedule",
                                                                       "heartbeat_s": int(st.agent_heartbeat_timeout.total_seconds())},
                                                        id="lab4-topics-run", task_queue=st.topics_queue,
                                                        execution_timeout=timedelta(hours=4)),
                     spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=interval)]),
                     policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP, catchup_window=timedelta(minutes=1)),
                     state=ScheduleState(note="lab 4: periodic whole-corpus topic report")),
            trigger_immediately=trigger)
        print(f"schedule {st.schedule_id}: every {interval}, overlap=SKIP, trigger_immediately={trigger}")
    elif action == "describe":
        d = await h.describe()
        info = d.info
        print(json.dumps({"schedule_id": st.schedule_id, "paused": d.schedule.state.paused,
                          "every": [str(i.every) for i in d.schedule.spec.intervals],
                          "overlap": d.schedule.policy.overlap.name, "num_actions": info.num_actions,
                          "skipped_overlap": info.num_actions_skipped_overlap,
                          "running": [a.workflow_id for a in info.running_actions],
                          "recent": [{"scheduled": a.scheduled_at.isoformat(), "started": a.started_at.isoformat(),
                                      "workflow_id": a.action.workflow_id, "run_id": a.action.first_execution_run_id}
                                     for a in info.recent_actions],
                          "next": [t.isoformat() for t in info.next_action_times[:3]]}, ensure_ascii=False, indent=2))
    elif action == "pause":
        await h.pause(note="paused from lab4 CLI")
        print("paused")
    elif action == "unpause":
        await h.unpause(note="unpaused from lab4 CLI")
        print("unpaused")
    elif action == "trigger":
        await h.trigger()
        print("triggered (overlap policy applies)")
    elif action == "delete":
        await h.delete()
        print("deleted")


async def topics_now(st, overrides: dict) -> str:
    client = await _client(st)
    now = datetime.now(timezone.utc)
    wid = "lab4-topics-manual-" + now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"   # unique per start
    h = await client.start_workflow("TopicReport", {"source": "manual (topics-now)", "overrides": overrides,
                                                    "heartbeat_s": int(st.agent_heartbeat_timeout.total_seconds())},
                                    id=wid, task_queue=st.topics_queue, execution_timeout=timedelta(hours=4))
    print(f"started {wid} run {h.result_run_id}")
    return wid


async def replay(cfg, st, batch_id: str) -> None:
    from .registry import Registry
    from .watcher import Watcher
    reg = Registry(cfg.state_dir / "lab4.db")
    if not reg.batch(batch_id):
        print(f"нет батча {batch_id}")
        return
    w = Watcher(await _client(st), cfg, st, reg)
    await w.start_batch(batch_id, f"replayed detection event for batch {batch_id}")
    reg.close()


async def wait_cmd(cfg, st, batches: bool, workflow_id: str | None, timeout: float, schedule_runs: int = 0,
                   after: str | None = None) -> int:
    from temporalio.client import WorkflowExecutionStatus
    from .registry import BATCH_OPEN, Registry
    client = await _client(st)
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if schedule_runs:
            info = (await client.get_schedule_handle(st.schedule_id).describe()).info
            done = 0
            since = datetime.fromisoformat(after.replace("Z", "+00:00")) if after else None
            for a in info.recent_actions:
                if since and a.scheduled_at <= since:
                    continue
                d = await client.get_workflow_handle(a.action.workflow_id, run_id=a.action.first_execution_run_id).describe()
                done += d.status != WorkflowExecutionStatus.RUNNING
            if done >= schedule_runs and not info.running_actions:
                print(f"schedule {st.schedule_id}: {done} finished run(s)" + (f" scheduled after {after}" if after else ""))
                return 0
        if workflow_id:
            d = await client.get_workflow_handle(workflow_id).describe()
            if d.status != WorkflowExecutionStatus.RUNNING:
                print(f"{workflow_id}: {d.status.name}")
                return 0 if d.status == WorkflowExecutionStatus.COMPLETED else 3
        if batches:
            from .watcher import is_temporary
            reg = Registry(cfg.state_dir / "lab4.db")
            running = 0
            for b in reg.batches(BATCH_OPEN):
                try:
                    d = await client.get_workflow_handle(f"ingest-{b['batch_id']}").describe()
                    running += d.status == WorkflowExecutionStatus.RUNNING
                except Exception:  # noqa: BLE001 - registered, workflow not started yet
                    running += 1
            inp = cfg.root / "input"
            undetected = [p for p in inp.rglob("*") if p.is_file() and not is_temporary(p.name)
                          and not p.relative_to(inp).parts[0].startswith("_")
                          and not reg.known("input/" + p.relative_to(inp).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)]
            reg.close()
            if not running and not undetected:
                print("no running ingestion workflows, no undetected files")
                return 0
        if not (workflow_id or batches or schedule_runs):
            return 0
        await asyncio.sleep(3)
    print("timeout")
    return 4


def export(cfg) -> None:
    """Copy the demonstration outputs into lab4/results (reports, L0 with its reports block, registry, catalog)."""
    from harness.workspace import HarnessWorkspace
    from .registry import Registry
    ws = HarnessWorkspace(cfg.root)
    out = cfg.results_dir
    (out / "reports").mkdir(parents=True, exist_ok=True)
    for p in sorted((ws.root / "data" / "_reports").glob("*.md")):
        shutil.copy2(p, out / "reports" / p.name)
    for name in ("l0.json", "reports.json"):
        p = ws.service_path("corpus") / name
        if p.exists():
            shutil.copy2(p, out / name)
    cat = ws.root / "catalog.json"
    if cat.exists():
        shutil.copy2(cat, out / "catalog.json")
    runs = ws.service_path("lab4") / "runs"            # frozen document list + topic map of every report run
    for d in sorted(runs.iterdir()) if runs.exists() else []:
        for name in ("manifest.json", "topics.json"):
            if (d / name).exists():
                (out / "runs" / d.name).mkdir(parents=True, exist_ok=True)
                shutil.copy2(d / name, out / "runs" / d.name / name)
    reg = Registry(cfg.state_dir / "lab4.db")
    (out / "registry.json").write_text(json.dumps({"batches": reg.batches(), "files": reg.files(), "reports": reg.reports()},
                                                  ensure_ascii=False, indent=1), encoding="utf-8")
    reg.close()
    tree = sorted(ws.rel(p) for p in (ws.root / "data").rglob("*") if p.is_file())
    (out / "data_tree.txt").write_text("\n".join(tree) + "\n", encoding="utf-8")
    print(f"exported -> {out}")


def _event_name(e) -> str:
    from temporalio.api.enums.v1 import EventType
    return EventType.Name(e.event_type).replace("EVENT_TYPE_", "")


def summarize_histories(out: Path) -> int:
    """summary.md from ALL saved histories in traces/temporal (not only what the server still keeps: the dev
    server drops closed executions after its retention period, and on 20.11 the summary shrank to 2 of 14)."""
    execs = []
    for f in sorted(out.glob("*__*.json")):
        ev = json.loads(f.read_text(encoding="utf-8-sig")).get("events", [])
        if not ev:
            continue
        start = ev[0].get("workflowExecutionStartedEventAttributes", {})
        acts, order = {}, []
        for e in ev:
            t = e.get("eventType", "").replace("EVENT_TYPE_", "")
            if t == "ACTIVITY_TASK_SCHEDULED":
                acts[e["eventId"]] = {"name": e["activityTaskScheduledEventAttributes"]["activityType"]["name"],
                                      "attempts": 0, "outcome": "scheduled", "failures": []}
                order.append(e["eventId"])
            elif t == "ACTIVITY_TASK_STARTED":
                a = e["activityTaskStartedEventAttributes"]
                acts[a["scheduledEventId"]]["attempts"] = a.get("attempt", 1)
                if (a.get("lastFailure") or {}).get("message"):
                    acts[a["scheduledEventId"]]["failures"].append(a["lastFailure"]["message"][:160])
            elif t in ("ACTIVITY_TASK_COMPLETED", "ACTIVITY_TASK_FAILED", "ACTIVITY_TASK_TIMED_OUT", "ACTIVITY_TASK_CANCELED"):
                key = {"ACTIVITY_TASK_COMPLETED": "activityTaskCompletedEventAttributes",
                       "ACTIVITY_TASK_FAILED": "activityTaskFailedEventAttributes",
                       "ACTIVITY_TASK_TIMED_OUT": "activityTaskTimedOutEventAttributes",
                       "ACTIVITY_TASK_CANCELED": "activityTaskCanceledEventAttributes"}[t]
                a = e.get(key, {})
                acts[a["scheduledEventId"]]["outcome"] = t.replace("ACTIVITY_TASK_", "").lower()
                if t != "ACTIVITY_TASK_COMPLETED" and (a.get("failure") or {}).get("message"):
                    acts[a["scheduledEventId"]]["failures"].append(a["failure"]["message"][:160])
        last = ev[-1].get("eventType", "")
        status = last.replace("EVENT_TYPE_WORKFLOW_EXECUTION_", "") if last.startswith("EVENT_TYPE_WORKFLOW_EXECUTION_") \
            else "RUNNING"
        wid, rid = f.stem.split("__", 1)
        execs.append((ev[0].get("eventTime", ""), start.get("workflowType", {}).get("name", "?"), wid, rid, status,
                      ev[-1].get("eventTime", ""), f.name, len(ev), [acts[k] for k in order]))
    lines = ["# Temporal executions of lab 4", "",
             "Generated by `python -m topicflow.cli trace` from all histories saved in this folder "
             "(`<workflow_id>__<run_id>.json`, ':' in ids replaced by '-').", ""]
    for t0, wtype, wid, rid, status, t1, fname, n, acts in sorted(execs):
        lines += [f"## {wtype} `{wid}`", "", f"- run ID: `{rid}`; status: **{status}**; start {t0}, last event {t1}",
                  f"- history: `{fname}` ({n} events)", "",
                  "| activity | attempts | outcome | failures before success / final failure |", "|---|---|---|---|"]
        lines += [f"| {a['name']} | {a['attempts']} | {a['outcome']} | {'; '.join(a['failures']) or '—'} |" for a in acts]
        lines.append("")
    (out / "summary.md").write_text(chr(10).join(lines), encoding="utf-8")
    return len(execs)


async def trace_cmd(cfg, st, workflow_id: str | None) -> None:
    client = await _client(st)
    out = cfg.traces_dir / "temporal"
    out.mkdir(parents=True, exist_ok=True)
    execs = []
    if workflow_id:
        d = await client.get_workflow_handle(workflow_id).describe()
        execs.append(d)
    else:
        async for wf in client.list_workflows("WorkflowType='IngestNewFiles' OR WorkflowType='TopicReport'"):
            if not wf.id.startswith("test-"):          # executions of the pytest suite are not part of the demonstration
                execs.append(wf)
    for wf in execs:
        hist = await client.get_workflow_handle(wf.id, run_id=wf.run_id).fetch_history()
        (out / f"{wf.id.replace(':', '-')}__{wf.run_id}.json").write_text(hist.to_json(), encoding="utf-8")
    summarize_histories(out)
    print(f"{len(execs)} executions fetched, summary over all saved histories -> {out}")


# ------------------------------------------------------------------------------------------ main
async def amain(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="topicflow")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--reset", action="store_true")
    sub.add_parser("worker")
    p = sub.add_parser("watch"); p.add_argument("--once", action="store_true")
    p = sub.add_parser("drop"); p.add_argument("--set", required=True, choices=["initial", "arrivals", "new_topic", "late", "new_topic2"])
    p.add_argument("--repeat", action="store_true", help="the same content under another name (duplicate)")
    p.add_argument("--slow", action="store_true", help="write slowly without .part (stability check)")
    p.add_argument("--only", help="only this file name of the set")
    p = sub.add_parser("schedule"); p.add_argument("action", choices=["create", "describe", "pause", "unpause", "trigger", "delete"])
    p.add_argument("--every"); p.add_argument("--no-trigger", action="store_true")
    p = sub.add_parser("topics-now"); p.add_argument("--set", nargs="*", default=[])
    p = sub.add_parser("replay"); p.add_argument("--batch", required=True)
    sub.add_parser("status")
    sub.add_parser("l0")
    p = sub.add_parser("open-report"); p.add_argument("--latest", action="store_true"); p.add_argument("--run-key")
    p.add_argument("--latest-complete", action="store_true", help="the latest COMPLETE report (reports.latest_complete)")
    p = sub.add_parser("fault"); p.add_argument("--activity"); p.add_argument("--point", default="before")
    p.add_argument("--times", default="1"); p.add_argument("--clear", action="store_true")
    p = sub.add_parser("wait"); p.add_argument("--batches", action="store_true"); p.add_argument("--workflow-id")
    p.add_argument("--timeout", type=float, default=1800); p.add_argument("--schedule-runs", type=int, default=0)
    p.add_argument("--after", help="count only schedule runs scheduled after this UTC time (ISO)")
    p = sub.add_parser("trace"); p.add_argument("--workflow-id")
    sub.add_parser("export")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = lab4_config.load()
    st = lab4_config.settings()

    if args.cmd == "prepare":
        prepare(cfg, args.reset)
    elif args.cmd == "worker":
        from .worker import run_worker
        await run_worker(st)
    elif args.cmd == "watch":
        from .registry import Registry
        from .watcher import Watcher
        await Watcher(await _client(st), cfg, st, Registry(cfg.state_dir / "lab4.db")).run(once=args.once)
    elif args.cmd == "drop":
        drop(cfg, args.set, args.repeat, args.slow, args.only)
    elif args.cmd == "schedule":
        from temporalio.service import RPCError, RPCStatusCode
        try:
            await schedule_cmd(st, args.action, args.every, not args.no_trigger)
        except RPCError as e:
            if e.status == RPCStatusCode.NOT_FOUND:
                print(f"schedule {st.schedule_id}: не существует ({e.message})")
                return 1 if args.action != "delete" else 0
            if e.status == RPCStatusCode.ALREADY_EXISTS:
                print(f"schedule {st.schedule_id}: уже существует")
                return 1
            raise
    elif args.cmd == "topics-now":
        await topics_now(st, _kv(args.set))
    elif args.cmd == "replay":
        await replay(cfg, st, args.batch)
    elif args.cmd == "status":
        status(cfg)
    elif args.cmd == "l0":
        l0_view(cfg)
    elif args.cmd == "open-report":
        return open_report(cfg, "complete" if args.latest_complete else args.run_key)
    elif args.cmd == "fault":
        from harness.workspace import HarnessWorkspace
        from .faults import Faults
        f = Faults(HarnessWorkspace(cfg.root).service_path("lab4") / "faults.json")
        if args.clear:
            f.clear()
            print("faults cleared")
        else:
            print(json.dumps(f.set(args.activity, args.point, -1 if args.times == "always" else int(args.times)), indent=1))
    elif args.cmd == "wait":
        return await wait_cmd(cfg, st, args.batches, args.workflow_id, args.timeout, args.schedule_runs, args.after)
    elif args.cmd == "trace":
        await trace_cmd(cfg, st, args.workflow_id)
    elif args.cmd == "export":
        export(cfg)
    return 0


def main() -> None:
    try:
        code = asyncio.run(amain())
    except KeyboardInterrupt:
        code = 130
    # On Windows a short-lived command that used the Temporal client occasionally did not terminate after printing
    # its result (observed once for topics-now under a PowerShell pipe). All work is done and committed to the
    # server at this point, so flush and leave the process explicitly.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code or 0)


if __name__ == "__main__":
    main()
