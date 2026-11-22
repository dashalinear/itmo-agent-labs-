"""Detection of finished files in workspace/input (Temporal itself does not watch the filesystem).

Periodic scan every LAB4_WATCH_INTERVAL_S seconds:
  * ignored while being written: names ending .part/.tmp/.crdownload/.partial, starting with '.' or '~';
    the demo writes '<name>.part' and renames it when done (atomic on the same disk);
  * stability check for everything else: size and mtime unchanged for LAB4_STABLE_S seconds;
  * stable files that are not yet registered form one batch per scan: the batch and its files are written to the
    registry first, then the workflow 'ingest-<batch_id>' is started (id conflict policy USE_EXISTING, so a
    repeated start for the same batch never creates a second execution).

On (re)start the watcher first restarts registered batches whose workflow is not running and not completed
(the files are still in input/ or input/_batches/<batch_id>/), then rescans input/ for unprocessed files.
Folders of input/ whose name starts with '_' are reserved (input/_batches: files already taken by a workflow).
"""
from __future__ import annotations

import asyncio
import time
from datetime import timedelta

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

from .registry import BATCH_OPEN, Registry, batch_id_for

IGNORED_SUFFIXES = (".part", ".tmp", ".crdownload", ".partial", ".swp")
MAX_BATCH_RESTARTS = 3          # restarts of a failed / incomplete batch in total, over all watcher starts


def is_temporary(name: str) -> bool:
    return name.startswith((".", "~")) or name.lower().endswith(IGNORED_SUFFIXES)


class Watcher:
    def __init__(self, client: Client, cfg, st, registry: Registry, log=print) -> None:
        self.client, self.cfg, self.st, self.reg = client, cfg, st, registry
        self.input = cfg.root / "input"
        self.input.mkdir(parents=True, exist_ok=True)
        self.seen: dict[str, tuple[int, int, float]] = {}       # path -> (size, mtime_ns, unchanged since)
        self.log = log

    def scan(self, now: float | None = None) -> list[dict]:
        """Return the stable, unregistered files (does not start anything)."""
        now = time.monotonic() if now is None else now
        ready, present = [], set()
        for p in sorted(self.input.rglob("*")):
            if not p.is_file() or is_temporary(p.name):
                continue
            sub = p.relative_to(self.input)
            if sub.parts[0].startswith("_"):                   # input/_batches/...: already taken by a workflow
                continue
            rel = "input/" + sub.as_posix()
            present.add(rel)
            try:
                s = p.stat()
            except OSError:
                continue
            key = (s.st_size, s.st_mtime_ns)
            prev = self.seen.get(rel)
            if not prev or prev[:2] != key:
                self.seen[rel] = (*key, now)
                continue
            if now - prev[2] < self.st.stable_s:
                continue
            if self.reg.known(rel, *key):
                continue
            ready.append({"input_path": rel, "size": key[0], "mtime_ns": key[1]})
        for rel in list(self.seen):
            if rel not in present:
                del self.seen[rel]
        return ready

    async def start_batch(self, batch_id: str, reason: str) -> str:
        wid = f"ingest-{batch_id}"
        try:
            h = await self.client.start_workflow(
                "IngestNewFiles", {"batch_id": batch_id, "heartbeat_s": int(self.st.agent_heartbeat_timeout.total_seconds())},
                id=wid, task_queue=self.st.ingest_queue,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
                execution_timeout=timedelta(hours=4))
            before = self.reg.batch(batch_id) or {}
            if before.get("run_id") == h.result_run_id and before.get("status") in ("started", "running"):
                # USE_EXISTING: the event repeated while the workflow is running -> the same execution is returned
                self.log(f"[watcher] {reason}: duplicate event, workflow {wid} is already running - the same execution "
                         f"run {h.result_run_id} was returned, nothing new started")
            else:
                self.reg.update_batch(batch_id, workflow_id=wid, run_id=h.result_run_id, status="started")
                self.log(f"[watcher] {reason}: started workflow {wid} run {h.result_run_id}")
        except WorkflowAlreadyStartedError:   # same id already completed: a duplicate event, nothing new to do
            self.log(f"[watcher] {reason}: duplicate event rejected by Temporal - workflow {wid} already completed, "
                     "not started again")
        except RPCError as e:
            self.log(f"[watcher] {reason}: workflow {wid} not started ({e.status.name}: {e.message[:120]})")
        except Exception as e:  # noqa: BLE001 - connection problems: the batch stays 'detected', recover() retries it
            self.log(f"[watcher] {reason}: workflow {wid} not started ({type(e).__name__}: {e})")
        return wid

    async def recover(self, unstarted_only: bool = False) -> None:
        """On start: restart every batch whose workflow is neither running nor completed (failed/incomplete ones get
        one new run per watcher start and at most MAX_BATCH_RESTARTS in total, so retries stay bounded).
        Periodically (unstarted_only): only batches that were registered but whose workflow could not be started
        (e.g. Temporal was unavailable at that moment)."""
        statuses = ("detected",) if unstarted_only else BATCH_OPEN + ("incomplete", "failed")
        for b in self.reg.batches(statuses):
            wid = f"ingest-{b['batch_id']}"
            try:
                desc = await self.client.get_workflow_handle(wid).describe()
                if desc.status == WorkflowExecutionStatus.RUNNING:
                    continue
                if desc.status == WorkflowExecutionStatus.COMPLETED:
                    continue
            except RPCError:
                pass                                            # never started (watcher stopped in between)
            if b["status"] in ("incomplete", "failed"):
                # a batch that keeps failing is restarted at most MAX_BATCH_RESTARTS times in total, also across
                # watcher restarts (counted in the registry); after that it waits for the user
                if self.reg.restarts(b["batch_id"]) >= MAX_BATCH_RESTARTS:
                    self.log(f"[watcher] batch {b['batch_id']} ({b['status']}) was restarted {MAX_BATCH_RESTARTS} times "
                             "already: not restarted again, see `status` and the workflow history")
                    continue
                self.reg.count_restart(b["batch_id"])
            await self.start_batch(b["batch_id"], f"restart of unfinished batch {b['batch_id']} ({b['status']})")

    async def tick(self) -> str | None:
        ready = self.scan()
        if not ready:
            return None
        bid = batch_id_for(ready)
        if self.reg.add_batch(bid, ready):
            self.log(f"[watcher] new batch {bid}: {[f['input_path'] for f in ready]}")
        return await self.start_batch(bid, f"{len(ready)} new file(s)")

    async def run(self, once: bool = False, recover_every: int = 15) -> None:
        await self.recover()
        self.log(f"[watcher] watching {self.input} every {self.st.watch_interval_s}s (stable after {self.st.stable_s}s)")
        n = 0
        while True:
            try:
                await self.tick()
                n += 1
                if n % recover_every == 0:
                    await self.recover(unstarted_only=True)
            except Exception as e:  # noqa: BLE001 - Temporal unreachable etc.: the batch stays registered, retried later
                self.log(f"[watcher] {type(e).__name__}: {e}")
            if once and not self.seen:
                return
            await asyncio.sleep(self.st.watch_interval_s)
