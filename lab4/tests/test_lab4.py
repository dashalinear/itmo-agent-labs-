"""Lab 4 tests. Elasticsearch on localhost:9200 is required; the Temporal tests additionally need the Temporal
service on localhost:7233 (skipped otherwise). The LLM is replaced by deterministic policies (conftest.py)."""
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


# ------------------------------------------------------------------------------------------ ingestion
async def _ingest(env, files):
    bid = batch_id_for(files)
    env.registry.add_batch(bid, files)
    staged = await A.stage_batch({"batch_id": bid})
    agent = await A.run_ingest_agent({"batch_id": bid, "folder": staged["folder"]}) if staged["staged"] else None
    verify = await A.verify_batch({"batch_id": bid, "agent": agent})
    return bid, staged, agent, verify


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


# ------------------------------------------------------------------------------------------ topic reports
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
    async with Worker(client, task_queue=q, workflows=[IngestNewFiles, TopicReport], activities=A.INGEST_ACTIVITIES + A.TOPIC_ACTIVITIES):
        res = await client.execute_workflow("IngestNewFiles", {"batch_id": bid}, id=f"test-ingest-{bid}-{q}", task_queue=q,
                                            execution_timeout=timedelta(minutes=5))
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
