"""Temporal activities: every side effect of lab 4 (files, Elasticsearch/MCP, LLM agents) runs here.

Only ids and small metadata go back to the workflows; texts, reports and agent histories stay in the workspace,
the harness store (state.db, checkpoints.db) and artifacts.

Agent activities reuse the lab 2 TaskRunner with the lab 3 agent factories:
  * the harness task id is derived from the batch / run key, so a retried attempt (after a worker crash or an
    error) RESUMES the same task from its checkpoint with its accumulated budget instead of starting from zero;
  * a heartbeat every 10 s lets Temporal detect a dead worker (heartbeat timeout) and retry on a live one;
  * budget exhaustion and the agent's own stop_partial are final (no retry, the result is marked partial);
    other pauses/failures (LLM server unreachable, ...) raise a retryable error, bounded by the retry policy.
"""
from __future__ import annotations

from . import _paths  # noqa: F401  (labs 1-3 on sys.path)

import asyncio
import os
import shutil
from dataclasses import replace
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationError

from organizer.catalog import Catalog
from organizer.textutil import file_hash

from harness.budget import TOKEN_FIELDS
from harness.runner import TaskRunner
from harness.workspace import HarnessWorkspace

from navsearch.cli import INGEST_REQUEST, ensure_task
from navsearch.embed import Embedder
from navsearch.index import CorpusIndex
from navsearch.levels import Levels
from navsearch.tools_corpus import CorpusEnv, CorpusTools

from . import config as lab4_config
from . import reports as rep
from .faults import Faults
from .registry import Registry
from .topics import Lab4

INGEST_TASK_PREFIX = "ING-"


class Env:
    """Process-wide objects of the worker (embedding model, ES client, registry) - created once."""
    _inst: "Env | None" = None

    def __init__(self, cfg=None, model=None, summarizer=None, use_mcp: bool = True, limit_overrides: dict | None = None) -> None:
        self.cfg = cfg or lab4_config.load()
        self.ws = HarnessWorkspace(self.cfg.root)
        self.index = CorpusIndex()
        self.index.ensure()
        self.embedder = Embedder()
        self.registry = Registry(self.cfg.state_dir / "lab4.db")
        self.faults = Faults(self.ws.service_path("lab4") / "faults.json")
        self.model, self.summarizer = model, summarizer       # test stand-ins; None = the real LLM from .env
        self.use_mcp = use_mcp
        # test stand-ins cannot summarise, so tests/dry runs raise the working limit instead of relying on compaction
        self.limit_overrides = limit_overrides or {}

    @classmethod
    def get(cls) -> "Env":
        if cls._inst is None:
            cls._inst = Env()
        return cls._inst

    def runner(self, use_mcp: bool) -> TaskRunner:
        return TaskRunner(self.cfg, verbose=False, use_mcp=use_mcp and self.use_mcp, model=self.model,
                          summarizer=self.summarizer)

    def corpus_tools(self, store, task_id: str) -> CorpusTools:
        cat = Catalog(self.ws)
        return CorpusTools(CorpusEnv(self.ws, cat, Levels(self.ws, cat), self.index, self.embedder, store, task_id))


def _attempt() -> int:
    try:
        return activity.info().attempt
    except RuntimeError:          # called outside an activity (tests)
        return 1


def _info() -> dict:
    try:
        i = activity.info()
        return {"workflow_id": i.workflow_id, "workflow_run_id": i.workflow_run_id, "activity_id": i.activity_id,
                "activity_type": i.activity_type, "attempt": i.attempt}
    except RuntimeError:
        return {"attempt": 1}


def _heartbeat(details: dict) -> None:
    try:
        activity.heartbeat(details)
    except RuntimeError:
        pass


def usage_of(store, task_id: str) -> dict:
    u = store.usage(task_id)
    return {"model_calls": u.get("model_calls", 0), "tool_calls": u.get("tool_calls", 0),
            "tokens": sum(u.get(f, 0) for f in TOKEN_FIELDS), "compactions": u.get("compaction_calls", 0)}


async def _run_agent(env: Env, task_id: str, kind: str, create, label: str, overrides: dict | None = None) -> dict:
    """Create (first attempt) or resume (later attempts) the harness task and run the agent to its stop."""
    att = _info()
    async with env.runner(use_mcp=kind == "ingest") as runner:
        app = Lab4(env.cfg, env.index, env.embedder)
        app.runner = runner
        before = runner.store.get_task(task_id)
        if before is None:
            create(runner)
        elif overrides:
            runner.set_limits(task_id, overrides)
        t0 = runner.store.get_task(task_id)
        runner.store.event(task_id, "temporal_attempt", {**att, "resumed": before is not None,
                                                         "status_before": before["status"] if before else None,
                                                         "runs_before": before["runs"] if before else 0})
        stop = asyncio.Event()

        async def beat():
            while not stop.is_set():
                _heartbeat({"task_id": task_id, "usage": usage_of(runner.store, task_id)})
                try:
                    await asyncio.wait_for(stop.wait(), timeout=10)
                except asyncio.TimeoutError:
                    pass
        hb = asyncio.create_task(beat())
        try:
            if t0["status"] in ("completed", "cancelled"):
                res = {"status": t0["status"]}
            else:
                res = await runner.run(task_id, label=f"{label}-a{att['attempt']}", agent_factory=app.factory(kind))
        finally:
            stop.set()
            await hb
        t = runner.store.get_task(task_id)
        out = {"task_id": task_id, "status": t["status"], "reason": t["status_reason"], "runs": t["runs"],
               "resumed": before is not None, "attempt": att["attempt"], "usage": usage_of(runner.store, task_id),
               "run_result": res.get("status")}
    reason = (t["status_reason"] or "")
    if t["status"] == "completed":
        return out
    if t["status"] == "paused" and (reason.startswith("partial:") or "budget exhausted" in reason):
        out["status"] = "partial" if reason.startswith("partial:") else "budget_exhausted"
        return out                                            # final: retrying cannot add budget
    if t["status"] == "cancelled":
        out["status"] = "cancelled"
        return out
    raise ApplicationError(f"agent task {task_id} stopped: {t['status']} - {reason[:300]}", out, type="AgentNotFinished")


# ============================================================================================ task 1: ingestion
def _stage_batch(env: Env, batch_id: str) -> dict:
    reg, ws = env.registry, env.ws
    b = reg.batch(batch_id)
    if not b:
        raise ApplicationError(f"unknown batch {batch_id}", type="UnknownBatch", non_retryable=True)
    env.faults.check("stage_batch", "before", _attempt())
    folder = f"input/_batches/{batch_id}"
    cat = Catalog(ws)
    staged, dups, missing, seen = [], [], [], {}
    for f in reg.batch_files(batch_id):
        rel_in = f["input_path"]                              # input/... (workspace-relative)
        sub = rel_in.split("/", 1)[1]
        src = ws.root / rel_in
        dst = ws.root / folder / sub
        if f["status"] == "duplicate":
            dups.append(rel_in)
            continue
        if f["status"] in ("staged", "indexed", "failed") and f["content_hash"]:
            # taken by an earlier attempt / earlier run of this batch: it is in the batch folder or already moved into
            # data/ by the agent. It stays part of the batch, so a restarted batch runs the agent again (resumed task)
            # and verify_batch checks it, instead of the file being treated as missing.
            staged.append(rel_in)
            continue
        if not src.exists():
            if dst.exists():                                  # moved by an attempt that crashed before recording it
                reg.update_file(rel_in, batch_id, status="staged", staged_path=f"{folder}/{sub}", content_hash=file_hash(dst))
                staged.append(rel_in)
            else:
                reg.update_file(rel_in, batch_id, status="missing", note="file disappeared from input before staging")
                missing.append(rel_in)
            continue
        h = file_hash(src)
        same = [d for d in cat.same_content(h) if d["current_path"].startswith("data/")]
        other = reg.taken_elsewhere(h, batch_id)          # same content staged by another batch, not indexed yet
        if same or h in seen or other:
            existing = same[0]["current_path"] if same else (seen.get(h) or other["staged_path"])
            rej = ws.root / "rejected" / batch_id / sub
            rej.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, rej)
            reg.update_file(rel_in, batch_id, status="duplicate", content_hash=h, staged_path=ws.rel(rej),
                            document_id=same[0]["document_id"] if same else None, current_path=existing,
                            note=f"same content as {existing}: not indexed again, source kept in {ws.rel(rej)}")
            dups.append(rel_in)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)                                  # atomic rename inside the task root
        reg.update_file(rel_in, batch_id, status="staged", content_hash=h, staged_path=f"{folder}/{sub}")
        seen[h] = f"{folder}/{sub}"
        staged.append(rel_in)
    reg.update_batch(batch_id, status="running")
    return {"batch_id": batch_id, "folder": folder, "staged": len(staged), "duplicates": dups, "missing": missing}


@activity.defn(name="stage_batch")
async def stage_batch(req: dict) -> dict:
    """Move the detected files of the batch to input/_batches/<batch_id>/ (atomic rename); duplicates by content
    hash go to rejected/<batch_id>/ and are not indexed again. Idempotent: a retry sees what is already moved."""
    return await asyncio.to_thread(_stage_batch, Env.get(), req["batch_id"])


@activity.defn(name="run_ingest_agent")
async def run_ingest_agent(req: dict) -> dict:
    """The lab 1/3 ingestion agent on input/_batches/<batch_id> inside the lab 2 harness (task ING-<batch_id>)."""
    env = Env.get()
    env.faults.check("run_ingest_agent", "before", _attempt())
    folder, tid = req["folder"], INGEST_TASK_PREFIX + req["batch_id"]
    env.registry.update_batch(req["batch_id"], task_id=tid)

    def create(runner):
        from .config import INGEST_LIMITS
        ensure_task(runner, tid, "ingest", INGEST_REQUEST.format(folder=folder), {"ingest_folder": folder},
                    {**INGEST_LIMITS, **env.limit_overrides, **(req.get("overrides") or {})})
    return await _run_agent(env, tid, "ingest", create, "ingest", req.get("overrides"))


def _verify_batch(env: Env, batch_id: str, agent: dict | None) -> dict:
    reg, ws = env.registry, env.ws
    cat = Catalog(ws)
    l0 = Levels(ws, cat).rebuild_l0()
    env.index.refresh()
    counts = env.index.counts_by_document()
    by_id = {d["document_id"]: d for d in l0["documents"]}
    docs, problems = [], []
    for f in reg.batch_files(batch_id):
        if f["status"] in ("duplicate", "missing"):
            continue
        same = [d for d in cat.same_content(f["content_hash"]) if d["current_path"].startswith("data/")] if f["content_hash"] else []
        if not same:
            reg.update_file(f["input_path"], batch_id, status="failed", note="not in data/ after the ingestion agent")
            problems.append(f"{f['input_path']}: not organized into data/")
            continue
        d = same[0]
        x = by_id.get(d["document_id"], {})
        es = {"L1": int(bool(env.index.es.exists(index=env.index.l1, id=d["document_id"]))),
              "L2": counts.get(d["document_id"], 0)}
        ok = d.get("is_empty") or (x.get("l1") and x.get("l2_current") and x.get("l2_fragments", 0) > 0
                                   and es["L1"] == 1 and es["L2"] == x.get("l2_fragments"))
        reg.update_file(f["input_path"], batch_id, status="indexed" if ok else "failed", document_id=d["document_id"],
                        current_path=d["current_path"],
                        note=None if ok else f"L1={x.get('l1')} L2={x.get('l2_fragments')} ES={es}")
        docs.append({"input_path": f["input_path"], "document_id": d["document_id"], "current_path": d["current_path"],
                     "l2_fragments": x.get("l2_fragments", 0), "es": es, "ok": bool(ok)})
        if not ok:
            problems.append(f"{f['input_path']}: L1/L2/ES incomplete")
    bdir = ws.root / "input" / "_batches" / batch_id
    left = [ws.rel(p) for p in bdir.rglob("*") if p.is_file()] if bdir.exists() else []
    status = "completed" if not problems and not left else "incomplete"
    result = {"batch_id": batch_id, "status": status, "documents": docs, "problems": problems, "left_in_staging": left,
              "duplicates": [f["input_path"] for f in reg.batch_files(batch_id) if f["status"] == "duplicate"],
              "l0_documents": l0["totals"]["documents"], "agent": agent}
    reg.update_batch(batch_id, status=status, result=result)
    if not left and bdir.exists():
        shutil.rmtree(bdir, ignore_errors=True)
    return result


@activity.defn(name="verify_batch")
async def verify_batch(req: dict) -> dict:
    """Deterministic check: every staged file is in data/, has L1 + current L2 and the same counts in Elasticsearch."""
    return await asyncio.to_thread(_verify_batch, Env.get(), req["batch_id"], req.get("agent"))


@activity.defn(name="record_batch")
async def record_batch(req: dict) -> None:
    env = Env.get()
    env.registry.update_batch(req["batch_id"], workflow_id=req.get("workflow_id"), run_id=req.get("run_id"),
                              status=req["status"])


# ============================================================================================ task 2: topic report
@activity.defn(name="freeze_corpus")
async def freeze_corpus(req: dict) -> dict:
    """Freeze the L0 document list of this run (once per run key); reports and empty files are excluded."""
    env = Env.get()
    env.faults.check("freeze_corpus", "before", _attempt())
    res = await asyncio.to_thread(rep.freeze_corpus, env.ws, req["run_key"], req["workflow_id"], req["run_id"])
    env.registry.upsert_report(req["run_key"], workflow_id=req["workflow_id"], run_id=req["run_id"],
                               task_id=rep.topic_task_id(req["run_key"]), status="running", frozen=res["documents"])
    return res


@activity.defn(name="run_topic_agent")
async def run_topic_agent(req: dict) -> dict:
    """The topic-analysis agent (topics.py) in the harness, task TOP-<run slug>; resumed on retry."""
    env = Env.get()
    env.faults.check("run_topic_agent", "before", _attempt())
    run_key = req["run_key"]
    tid = rep.topic_task_id(run_key)
    rdir = rep.run_dir(env.ws, run_key)

    def create(runner):
        from .config import TOPICS_LIMITS
        ensure_task(runner, tid, "topics",
                    "Выдели основные темы всего зафиксированного корпуса, объедини близкие темы и подтверди каждую "
                    "цитатами из L2; затем submit_report.",
                    {"run_key": run_key, "run_dir": str(rdir), "workflow_id": req.get("workflow_id")},
                    {**TOPICS_LIMITS, **env.limit_overrides, **(req.get("overrides") or {})})
    return await _run_agent(env, tid, "topics", create, "topics", req.get("overrides"))


def _publish(env: Env, req: dict) -> dict:
    from harness.store import TaskStore
    run_key = req["run_key"]
    tid = rep.topic_task_id(run_key)
    store = TaskStore(env.cfg.state_dir / "state.db")
    try:
        task = store.get_task(tid)
        evidence = store.evidence(tid) if task else []
        agent = req.get("agent") or {}
        meta = {"workflow_id": req.get("workflow_id"), "run_id": req.get("run_id"), "source": req.get("source", "schedule"),
                "usage": agent.get("usage"), "agent_error": req.get("agent_error")}
        att = _attempt()
        res = rep.publish(env.ws, env.corpus_tools(store, tid), run_key, meta, task, evidence,
                          fault=lambda point: env.faults.check("publish_report", point, att))
    finally:
        store.close()
    env.registry.upsert_report(run_key, status=res["status"], path=res["path"], document_id=res["document_id"], result=res)
    return res


@activity.defn(name="publish_report")
async def publish_report(req: dict) -> dict:
    """Render the report from saved state, write data/_reports/topics_<run>.md atomically, register it at L0
    (catalog, L1, L2, reports history + latest pointer). Idempotent per run key."""
    env = Env.get()
    env.faults.check("publish_report", "before", _attempt())
    return await asyncio.to_thread(_publish, env, req)


INGEST_ACTIVITIES = [stage_batch, run_ingest_agent, verify_batch, record_batch]
TOPIC_ACTIVITIES = [freeze_corpus, run_topic_agent, publish_report]


def test_env(cfg, model, summarizer=None) -> Env:
    """Env for tests (policy model instead of the LLM, no MCP)."""
    Env._inst = Env(replace(cfg), model=model, summarizer=summarizer, use_mcp=False)
    return Env._inst
