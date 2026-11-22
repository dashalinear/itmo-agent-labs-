"""Lab 4 tests. Elasticsearch on localhost:9200 is required; the Temporal tests additionally need the Temporal
service on localhost:7233 (skipped otherwise). The LLM is replaced by deterministic policies (tests/fake_llm.py)."""
import asyncio
import json
import time
import uuid
from datetime import timedelta

import pytest

from conftest import LAB4, put, temporal_available

from topicflow import activities as A
from topicflow import reports as rep
from topicflow.registry import batch_id_for
from topicflow.watcher import Watcher, is_temporary


# ------------------------------------------------------------------------------------------ detection
def test_watcher_ignores_partial_files_and_waits_for_stability(lab4env):
    from topicflow.config import settings
    st = settings()
    w = Watcher(client=None, cfg=lab4env.cfg, st=st, registry=lab4env.registry, log=lambda *a: None)
    inp = lab4env.ws.root / "input"
    (inp / "a.md.part").write_text("в процессе записи", encoding="utf-8")
    (inp / "b.md").write_text("готовый файл", encoding="utf-8")
    assert is_temporary("a.md.part") and is_temporary("~lock.md") and not is_temporary("b.md")
    assert w.scan(now=0.0) == []                                   # first sighting: not stable yet
    assert w.scan(now=st.stable_s / 2) == []                       # unchanged, but not long enough
    ready = w.scan(now=st.stable_s + 0.1)
    assert [f["input_path"] for f in ready] == ["input/b.md"]
    (inp / "b.md").write_text("готовый файл, дописан", encoding="utf-8")   # changed again -> wait again
    assert w.scan(now=st.stable_s + 0.2) == []
    ready = w.scan(now=2 * st.stable_s + 0.5)
    bid = batch_id_for(ready)
    assert lab4env.registry.add_batch(bid, ready) and not lab4env.registry.add_batch(bid, ready)
    assert w.scan(now=3 * st.stable_s + 1) == []                   # registered: not detected twice
    assert batch_id_for(ready) == bid                              # stable batch id for the same event


class StubTemporal:
    """Minimal stand-in for temporalio.client.Client used by the watcher (start + describe)."""

    def __init__(self):
        self.down = False
        self.started = []

    async def start_workflow(self, name, arg, id, **kw):
        if self.down:
            raise ConnectionError("Temporal is unavailable")
        self.started.append(id)
        return type("H", (), {"result_run_id": f"run-{len(self.started)}"})()

    def get_workflow_handle(self, wid):
        from temporalio.client import WorkflowExecutionStatus
        from temporalio.service import RPCError, RPCStatusCode
        started = wid in self.started

        class H:
            async def describe(self):
                if not started:
                    raise RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b"")
                return type("D", (), {"status": WorkflowExecutionStatus.RUNNING})()
        return H()


async def test_watcher_survives_temporal_outage_and_rescans_after_restart(lab4env):
    from dataclasses import replace
    from topicflow.config import settings
    st = replace(settings(), stable_s=0.0, watch_interval_s=0.0)
    inp = lab4env.ws.root / "input"
    client = StubTemporal()
    w = Watcher(client=client, cfg=lab4env.cfg, st=st, registry=lab4env.registry, log=lambda *a: None)
    (inp / "a.md").write_text("файл a", encoding="utf-8")
    client.down = True                                            # Temporal unavailable at detection time
    w.scan()
    await w.tick()                                                # registered, not started, watcher still alive
    b1 = lab4env.registry.batches()[0]
    assert b1["status"] == "detected" and client.started == []
    client.down = False
    await w.recover(unstarted_only=True)                          # periodic retry of never-started batches
    assert client.started == [f"ingest-{b1['batch_id']}"] and lab4env.registry.batch(b1["batch_id"])["status"] == "started"
    # the watcher is stopped; meanwhile the user adds a file; a NEW watcher process picks it up on its first scan
    (inp / "b.md").write_text("файл b", encoding="utf-8")
    w2 = Watcher(client=client, cfg=lab4env.cfg, st=st, registry=lab4env.registry, log=lambda *a: None)
    await w2.recover()                                            # b1 is running: not started again
    w2.scan()
    await w2.tick()
    assert len(client.started) == 2 and client.started[0] == f"ingest-{b1['batch_id']}"
    b2 = [b for b in lab4env.registry.batches() if b["batch_id"] != b1["batch_id"]][0]
    assert [f["input_path"] for f in b2["files"]] == ["input/b.md"]   # a.md is not detected twice


# ------------------------------------------------------------------------------------------ ingestion
async def _ingest(env, files):
    bid = batch_id_for(files)
    env.registry.add_batch(bid, files)
    staged = await A.stage_batch({"batch_id": bid})
    agent = await A.run_ingest_agent({"batch_id": bid, "folder": staged["folder"]}) if staged["staged"] else None
    verify = await A.verify_batch({"batch_id": bid, "agent": agent})
    return bid, staged, agent, verify


async def test_failed_batch_is_restarted_a_bounded_number_of_times_over_watcher_starts(lab4env):
    """A batch that keeps failing gets one new run per watcher start, but at most MAX_BATCH_RESTARTS in total."""
    from dataclasses import replace
    from topicflow.config import settings
    from topicflow.watcher import MAX_BATCH_RESTARTS
    st = replace(settings(), stable_s=0.0, watch_interval_s=0.0)
    (lab4env.ws.root / "input" / "f.md").write_text("файл f", encoding="utf-8")
    client = StubTemporal()
    client.down = True
    w = Watcher(client=client, cfg=lab4env.cfg, st=st, registry=lab4env.registry, log=lambda *a: None)
    w.scan()
    await w.tick()
    bid = lab4env.registry.batches()[0]["batch_id"]
    client.down = False
    for _ in range(MAX_BATCH_RESTARTS + 2):                       # the watcher is started again and again
        lab4env.registry.update_batch(bid, status="failed")
        client.started.clear()                                    # the stub reports the workflow as not found
        await Watcher(client=client, cfg=lab4env.cfg, st=st, registry=lab4env.registry, log=lambda *a: None).recover()
    assert lab4env.registry.restarts(bid) == MAX_BATCH_RESTARTS


async def test_ingestion_batch_is_idempotent_and_duplicates_are_not_indexed(lab4env):
    env = lab4env
    files = put(env, "initial", {"checkpoint_sqlite.md", "bm25_basics.md", "oncall.md"})
    bid, staged, agent, verify = await _ingest(env, files)
    assert staged["staged"] == 3 and agent["status"] == "completed" and verify["status"] == "completed", verify
    assert {d["current_path"] for d in verify["documents"]} == {"data/state/checkpoint_sqlite.md", "data/search/bm25_basics.md",
                                                               "data/ops/oncall.md"}
    assert all(d["es"]["L1"] == 1 and d["es"]["L2"] == d["l2_fragments"] > 0 for d in verify["documents"])
    n_l2 = env.index.count("L2")
    # an Activity retry of every step: nothing moves twice, the agent task is not restarted, no new records
    again = await A.stage_batch({"batch_id": bid})
    assert again["staged"] == 3 and not (env.ws.root / "input" / "oncall.md").exists()
    agent2 = await A.run_ingest_agent({"batch_id": bid, "folder": staged["folder"]})
    assert agent2["status"] == "completed" and agent2["resumed"] and agent2["runs"] == agent["runs"]
    v2 = await A.verify_batch({"batch_id": bid})
    env.index.refresh()
    assert v2["status"] == "completed" and env.index.count("L2") == n_l2
    # the same content arrives again under another name: kept in rejected/, no second document
    dup = put(env, "initial", {"oncall.md"}, rename=lambda n: "oncall_copy.md")
    bid2, st2, agent3, v3 = await _ingest(env, dup)
    assert st2["staged"] == 0 and st2["duplicates"] == ["input/oncall_copy.md"] and agent3 is None
    assert (env.ws.root / "rejected" / bid2 / "oncall_copy.md").exists()
    f = env.registry.batch_files(bid2)[0]
    assert f["status"] == "duplicate" and f["current_path"] == "data/ops/oncall.md"
    cat = json.loads((env.ws.service / "catalog.json").read_text(encoding="utf-8")) if (env.ws.service / "catalog.json").exists() \
        else json.loads((env.ws.root / "catalog.json").read_text(encoding="utf-8"))
    assert sum(1 for d in cat["documents"] if d["current_path"].endswith("oncall.md")) == 1


async def test_restarted_incomplete_batch_runs_the_agent_again_instead_of_claiming_success(lab4env):
    """The agent moved a file into data/ but did not index it; the batch is restarted later (new workflow run)."""
    from harness.workspace import HarnessFileTools
    from organizer.catalog import Catalog
    env = lab4env
    files = put(env, "initial", {"redis_cache.md"})
    bid = batch_id_for(files)
    env.registry.add_batch(bid, files)
    st = await A.stage_batch({"batch_id": bid})
    HarnessFileTools(env.ws, Catalog(env.ws), 10 ** 6).move(f"{st['folder']}/redis_cache.md", "data/state/redis_cache.md")
    v1 = await A.verify_batch({"batch_id": bid})                  # moved, but no L1/L2: incomplete, file 'failed'
    assert v1["status"] == "incomplete" and env.registry.batch_files(bid)[0]["status"] == "failed"
    st2 = await A.stage_batch({"batch_id": bid})                  # the restarted run: the file is still part of the batch
    assert st2["staged"] == 1 and st2["missing"] == []
    agent = await A.run_ingest_agent({"batch_id": bid, "folder": st2["folder"]})
    v2 = await A.verify_batch({"batch_id": bid, "agent": agent})
    assert agent["status"] == "completed" and v2["status"] == "completed" and v2["documents"][0]["l2_fragments"] > 0


async def test_same_content_in_two_batches_staged_before_indexing_is_one_document(lab4env):
    env = lab4env
    f1 = put(env, "initial", {"vector_search.md"})
    b1 = batch_id_for(f1)
    env.registry.add_batch(b1, f1)
    await A.stage_batch({"batch_id": b1})                         # batch 1 taken, not indexed yet
    f2 = put(env, "initial", {"vector_search.md"}, rename=lambda n: "vector_again.md")
    b2 = batch_id_for(f2)
    env.registry.add_batch(b2, f2)
    s2 = await A.stage_batch({"batch_id": b2})
    assert s2["staged"] == 0 and s2["duplicates"] == ["input/vector_again.md"]
    assert (env.ws.root / "rejected" / b2 / "vector_again.md").exists()


# ------------------------------------------------------------------------------------------ topic reports
def l0_latest_complete(env):
    l0 = json.loads((env.ws.service_path("corpus") / "l0.json").read_text(encoding="utf-8"))
    return (l0["reports"].get("latest_complete") or {}).get("run_key")


async def _report(env, run_key, overrides=None, fault=False):
    base = {"run_key": run_key, "workflow_id": run_key, "run_id": "r-" + uuid.uuid4().hex[:6]}
    frozen = await A.freeze_corpus(base)
    agent = await A.run_topic_agent({**base, "overrides": overrides})
    return frozen, agent, await A.publish_report({**base, "source": "test", "agent": agent})


async def test_two_reports_on_l0_and_the_second_covers_the_new_topic(lab4env):
    env = lab4env
    await _ingest(env, put(env, "initial"))
    k1 = "lab4-topics-run-2026-11-17T10:00:00Z"
    frozen, agent, r1 = await _report(env, k1)
    assert frozen["documents"] == 10 and agent["status"] == "completed" and r1["status"] == "complete", (agent, r1)
    text1 = (env.ws.root / r1["path"]).read_text(encoding="utf-8")
    assert "**Статус: ПОЛНЫЙ**" in text1 and "просмотрено L1: **10 из 10**" in text1 and "строки" in text1
    l0 = json.loads((env.ws.service_path("corpus") / "l0.json").read_text(encoding="utf-8"))
    assert l0["reports"]["latest"]["run_key"] == k1 and l0["reports"]["latest"]["document_id"] == r1["document_id"]
    rec = next(d for d in l0["documents"] if d["document_id"] == r1["document_id"])
    assert rec["service"] == "topic_report" and rec["l1"] and rec["l2_fragments"] > 0      # openable from L0 -> L1 -> L2
    # a retried publication of the same run: same bytes, same document, no second history entry
    again = await A.publish_report({"run_key": k1, "workflow_id": k1, "run_id": r1["run_id"], "source": "test", "agent": agent})
    assert again["already_published"] and again["document_id"] == r1["document_id"] and again["report_count"] == 1
    # corpus growth: a file on a new topic is ingested, the next run sees it; the report itself is not analysed
    await _ingest(env, put(env, "new_topic"))
    k2 = "lab4-topics-run-2026-11-17T10:10:00Z"
    frozen2, agent2, r2 = await _report(env, k2)
    m2 = json.loads((rep.run_dir(env.ws, k2) / "manifest.json").read_text(encoding="utf-8"))
    assert frozen2["documents"] == 11 and frozen2["excluded_service"] == 1
    assert not any(d["path"].startswith("data/_reports/") for d in m2["documents"])
    text2 = (env.ws.root / r2["path"]).read_text(encoding="utf-8")
    assert "data/energy/inference_energy.md" in text2 and "новая тема" in text2 and r2["report_count"] == 2
    assert "### Новые документы и их темы" in text2
    assert [d["path"] for d in m2["documents"] if d["new_since_previous_report"]] == ["data/energy/inference_energy.md"]
    assert l0_latest_complete(env) == k2
    assert (env.ws.root / r1["path"]).exists() and r2["path"] != r1["path"]
    l0 = json.loads((env.ws.service_path("corpus") / "l0.json").read_text(encoding="utf-8"))
    assert [h["run_key"] for h in l0["reports"]["history"]] == [k1, k2] and l0["reports"]["latest"]["run_key"] == k2


async def test_budget_exhaustion_gives_a_partial_report_and_retry_does_not_duplicate(lab4env):
    env = lab4env
    await _ingest(env, put(env, "initial", {"checkpoint_sqlite.md", "redis_cache.md", "bm25_basics.md"}))
    k = "lab4-topics-manual-2026-11-17T11:00:00Z"
    frozen, agent, r = await _report(env, k, overrides={"max_model_calls": 1})
    assert agent["status"] == "budget_exhausted" and r["status"] == "partial"
    text = (env.ws.root / r["path"]).read_text(encoding="utf-8")
    assert "**Статус: ЧАСТИЧНЫЙ**" in text and "Почему отчёт частичный" in text and "не просмотрен" in text
    # fault between writing the file and registering it at L0: the retry finishes the same report
    k2 = "lab4-topics-manual-2026-11-17T11:30:00Z"
    env.faults.set("publish_report", "after_write", 1)
    base = {"run_key": k2, "workflow_id": k2, "run_id": "r2"}
    await A.freeze_corpus(base)
    ag = await A.run_topic_agent(base)
    with pytest.raises(Exception, match="injected fault"):
        await A.publish_report({**base, "agent": ag})
    ok = await A.publish_report({**base, "agent": ag})
    assert ok["status"] == "complete" and ok["already_published"]
    l0 = json.loads((env.ws.service_path("corpus") / "l0.json").read_text(encoding="utf-8"))
    assert [h["run_key"] for h in l0["reports"]["history"]].count(k2) == 1
    assert len(list((env.ws.root / "data" / "_reports").glob("*.md"))) == 2


async def test_agent_task_is_resumed_not_restarted(lab4env):
    """A retried agent activity continues the same harness task with its accumulated usage."""
    env = lab4env
    await _ingest(env, put(env, "initial", {"checkpoint_sqlite.md", "bm25_basics.md"}))
    k = "lab4-topics-manual-2026-11-17T12:00:00Z"
    base = {"run_key": k, "workflow_id": k, "run_id": "r"}
    await A.freeze_corpus(base)
    first = await A.run_topic_agent({**base, "overrides": {"max_model_calls": 2}})
    assert first["status"] == "budget_exhausted" and not first["resumed"]
    used = first["usage"]["model_calls"]
    second = await A.run_topic_agent({**base, "overrides": {"max_model_calls": 60}})   # user raises the limit, same task
    assert second["status"] == "completed" and second["resumed"] and second["runs"] == first["runs"] + 1
    assert second["usage"]["model_calls"] > used                   # counters continue, not reset
    from harness.store import TaskStore
    s = TaskStore(env.cfg.state_dir / "state.db")
    att = s.events(second["task_id"], "temporal_attempt")
    s.close()
    assert len(att) == 2 and any(a["data"]["resumed"] for a in att)


# ------------------------------------------------------------------------------------------ real Temporal
@pytest.mark.skipif(not temporal_available(), reason="Temporal service is not running on localhost:7233")
async def test_workflows_on_real_temporal_service(lab4env):
    from temporalio.client import Client
    from temporalio.worker import Worker
    from topicflow.workflows import IngestNewFiles, TopicReport
    env = lab4env
    client = await Client.connect("localhost:7233")
    q = "lab4-test-" + uuid.uuid4().hex[:6]
    files = put(env, "initial", {"checkpoint_postgres.md", "vector_search.md"})
    bid = batch_id_for(files)
    env.registry.add_batch(bid, files)
    # duplicate detection event WHILE the workflow is running (no worker yet): the same execution is returned
    from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
    iwid = f"test-ingest-{bid}-{q}"
    starts = [await client.start_workflow("IngestNewFiles", {"batch_id": bid}, id=iwid, task_queue=q,
                                          id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                                          id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
                                          execution_timeout=timedelta(minutes=5)) for _ in range(2)]
    assert starts[0].result_run_id == starts[1].result_run_id
    async with Worker(client, task_queue=q, workflows=[IngestNewFiles, TopicReport], activities=A.INGEST_ACTIVITIES + A.TOPIC_ACTIVITIES):
        res = await client.get_workflow_handle(iwid).result()
        runs = [w async for w in client.list_workflows(f"WorkflowId='{iwid}'")]
        assert len(runs) == 1                                         # one execution for two detection events
        assert res["verify"]["status"] == "completed" and len(res["verify"]["documents"]) == 2
        assert env.registry.batch(bid)["status"] == "completed"
        env.faults.set("publish_report", "after_write", 1)            # first publish attempt fails after writing
        wid = f"test-topics-{q}-2026-11-17T13:00:00Z"
        out = await client.execute_workflow("TopicReport", {"source": "test"}, id=wid, task_queue=q,
                                            execution_timeout=timedelta(minutes=5))
        assert out["report"]["status"] == "complete" and out["report"]["report_count"] == 1
        hist = await client.get_workflow_handle(wid).fetch_history()
        attempts = [e.activity_task_started_event_attributes.attempt for e in hist.events
                    if e.HasField("activity_task_started_event_attributes")]
        assert max(attempts) == 2                                     # publish_report retried once by Temporal


def test_report_tells_an_interval_tick_from_the_immediate_run(lab4env):
    """The report row "вид запуска" uses the Schedule metadata written by `schedule create`."""
    ws = lab4env.ws
    meta = ws.service_path("lab4") / "schedule_meta.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({"schedule_id": "lab4-topics", "created_at": "2026-11-21T10:00:03+00:00",
                                "every_s": 300, "trigger_immediately": True}), encoding="utf-8")
    assert rep._schedule_kind(ws, "lab4-topics-run-2026-11-21T10:00:03Z")[0] == "немедленный запуск при создании Schedule"
    assert rep._schedule_kind(ws, "lab4-topics-run-2026-11-21T10:05:00Z")[0] == "тик интервала Schedule (каждые 5 мин)"
    assert rep._schedule_kind(ws, "lab4-topics-manual-2026-11-21T10:06:00Z")[0] == "ручной запуск topics-now"
