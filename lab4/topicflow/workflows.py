"""Temporal workflows of lab 4 (deterministic coordination only; activities are referenced by name).

IngestNewFiles(batch)   stage_batch -> run_ingest_agent -> verify_batch -> record_batch
TopicReport(params)     freeze_corpus -> run_topic_agent -> publish_report

Retry policy: every activity has a start-to-close timeout and a FINITE number of attempts; agent activities also
have a heartbeat timeout (dead worker -> retry on a live one, which resumes the same harness task). Errors that a
retry cannot fix are non-retryable. When the agent activity finally fails, the workflow still verifies / publishes
what exists and marks the result incomplete / partial instead of claiming success.
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

FAST = RetryPolicy(initial_interval=timedelta(seconds=1), backoff_coefficient=2.0, maximum_interval=timedelta(seconds=20),
                   maximum_attempts=5, non_retryable_error_types=["UnknownBatch"])
AGENT = RetryPolicy(initial_interval=timedelta(seconds=5), backoff_coefficient=2.0, maximum_interval=timedelta(minutes=1),
                    maximum_attempts=3)
AGENT_TIMEOUT = timedelta(hours=2)


def _cause(e: BaseException) -> str:
    c = e
    while getattr(c, "cause", None) is not None:
        c = c.cause
    return f"{type(c).__name__}: {c}"[:500]


@workflow.defn(name="IngestNewFiles")
class IngestNewFiles:
    @workflow.run
    async def run(self, req: dict) -> dict:
        info = workflow.info()
        batch_id = req["batch_id"]
        hb = timedelta(seconds=int(req.get("heartbeat_s", 90)))
        await workflow.execute_activity("record_batch", {"batch_id": batch_id, "workflow_id": info.workflow_id,
                                                         "run_id": info.run_id, "status": "started"},
                                        start_to_close_timeout=timedelta(seconds=30), retry_policy=FAST)
        try:
            staged = await workflow.execute_activity("stage_batch", {"batch_id": batch_id},
                                                     start_to_close_timeout=timedelta(minutes=2), retry_policy=FAST)
        except ActivityError as e:
            # retries exhausted: nothing was indexed, the files are still in input/ (or input/_batches/<id>)
            await workflow.execute_activity("record_batch", {"batch_id": batch_id, "workflow_id": info.workflow_id,
                                                             "run_id": info.run_id, "status": "failed"},
                                            start_to_close_timeout=timedelta(seconds=30), retry_policy=FAST)
            raise ApplicationError(f"batch {batch_id}: staging failed after retries: {_cause(e)}",
                                   type="StageFailed", non_retryable=True) from e
        agent, error = None, None
        if staged["staged"]:
            try:
                agent = await workflow.execute_activity(
                    "run_ingest_agent", {"batch_id": batch_id, "folder": staged["folder"], "overrides": req.get("overrides")},
                    start_to_close_timeout=AGENT_TIMEOUT, heartbeat_timeout=hb, retry_policy=AGENT)
            except ActivityError as e:
                error = _cause(e)
                agent = {"status": "failed", "error": error}
        verify = await workflow.execute_activity("verify_batch", {"batch_id": batch_id, "agent": agent},
                                                 start_to_close_timeout=timedelta(minutes=5), retry_policy=FAST)
        result = {"batch_id": batch_id, "workflow_id": info.workflow_id, "run_id": info.run_id, "staged": staged,
                  "agent": agent, "verify": {k: verify[k] for k in ("status", "documents", "problems", "duplicates",
                                                                    "left_in_staging", "l0_documents")}}
        if verify["status"] != "completed" and staged["staged"]:
            # files stay in input/_batches/<batch_id> (never lost); the watcher restarts the batch after the cause is fixed
            raise ApplicationError(f"batch {batch_id} is not fully indexed: {verify['problems'][:3]} {error or ''}",
                                   result, type="IngestIncomplete", non_retryable=True)
        return result


@workflow.defn(name="TopicReport")
class TopicReport:
    @workflow.run
    async def run(self, params: dict | None = None) -> dict:
        params = params or {}
        info = workflow.info()
        run_key = info.workflow_id            # one report per Schedule period / manual start; stable across retries
        hb = timedelta(seconds=int(params.get("heartbeat_s", 90)))
        base = {"run_key": run_key, "workflow_id": info.workflow_id, "run_id": info.run_id}
        frozen = await workflow.execute_activity("freeze_corpus", base, start_to_close_timeout=timedelta(minutes=2),
                                                 retry_policy=FAST)
        agent, error = None, None
        if frozen["documents"]:
            try:
                agent = await workflow.execute_activity(
                    "run_topic_agent", {**base, "overrides": params.get("overrides")},
                    start_to_close_timeout=AGENT_TIMEOUT, heartbeat_timeout=hb, retry_policy=AGENT)
            except ActivityError as e:
                error = _cause(e)
        report = await workflow.execute_activity(
            "publish_report", {**base, "source": params.get("source", "schedule"), "agent": agent, "agent_error": error},
            start_to_close_timeout=timedelta(minutes=5), retry_policy=FAST)
        return {"run_key": run_key, "run_id": info.run_id, "frozen": frozen, "agent": agent, "agent_error": error,
                "report": {k: report[k] for k in ("path", "document_id", "status", "topics", "documents_frozen",
                                                   "documents_examined", "report_count", "already_published")}}
