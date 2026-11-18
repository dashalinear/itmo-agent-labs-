"""Total budget: LLM tokens, model/tool calls, search calls, HTTP requests, active time.

All counters live in the state database, so a new process continues from the
spent values (resume never resets them). LLM calls reserve
``estimated_input + response_reserve`` BEFORE the call; the reservation is then
replaced by the real usage (input+output). Failed calls are charged their input
estimate; a call interrupted by a cancel releases its reservation at once and is
charged its input estimate ("interrupted_call_tokens"); a reservation left
"inflight" by a killed process is charged the same way on the next start.
"""
from __future__ import annotations

import threading
import time

from .config import Limits
from .store import TaskStore


class BudgetExhausted(Exception):
    def __init__(self, resource: str, needed: float, remaining: float, limit: float) -> None:
        super().__init__(f"{resource}: needed {needed}, remaining {remaining} of {limit}")
        self.resource = resource
        self.needed = needed
        self.remaining = remaining
        self.limit = limit

    def as_dict(self) -> dict:
        return {"resource": self.resource, "needed": self.needed, "remaining": self.remaining, "limit": self.limit}


TOKEN_FIELDS = ("input_tokens", "output_tokens", "compaction_input_tokens", "compaction_output_tokens",
                "failed_call_tokens", "interrupted_call_tokens")


class Budget:
    def __init__(self, store: TaskStore, task_id: str, limits: Limits) -> None:
        self.store = store
        self.task_id = task_id
        self.limits = limits
        self._lock = threading.RLock()
        self._mark: float | None = None

    # -- tokens ---------------------------------------------------------------
    def spent_tokens(self, usage: dict | None = None) -> int:
        u = usage or self.store.usage(self.task_id)
        return int(sum(u.get(f, 0) for f in TOKEN_FIELDS))

    def remaining_tokens(self) -> int:
        return self.limits.max_total_tokens - self.spent_tokens() - self.store.reserved_tokens(self.task_id)

    def reserve(self, kind: str, tokens: int, input_est: int) -> int:
        """kind: agent | compaction. Raises BudgetExhausted; returns reservation id."""
        with self._lock:
            u = self.store.usage(self.task_id)
            if kind == "agent" and u.get("model_calls", 0) >= self.limits.max_model_calls:
                raise BudgetExhausted("model_calls", 1, 0, self.limits.max_model_calls)
            remaining = self.limits.max_total_tokens - self.spent_tokens(u) - self.store.reserved_tokens(self.task_id)
            if tokens > remaining:
                raise BudgetExhausted("tokens", tokens, max(remaining, 0), self.limits.max_total_tokens)
            return self.store.add_reservation(self.task_id, kind, tokens, input_est)

    def commit(self, res_id: int, kind: str, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            if kind == "compaction":
                self.store.add_usage(self.task_id, compaction_input_tokens=input_tokens,
                                     compaction_output_tokens=output_tokens, compaction_calls=1)
            else:
                self.store.add_usage(self.task_id, input_tokens=input_tokens, output_tokens=output_tokens, model_calls=1)
                self.store.set_peak(self.task_id, input_tokens)
            self.store.finish_reservation(res_id, "done")

    def fail(self, res_id: int, input_est: int) -> None:
        """A failed call (timeout, HTTP error) is charged its input estimate."""
        with self._lock:
            self.store.add_usage(self.task_id, failed_call_tokens=input_est, failed_calls=1)
            self.store.finish_reservation(res_id, "failed")

    def interrupt(self, res_id: int, input_est: int) -> None:
        """A call interrupted in THIS process (cancel, tool timeout): the reservation is released and the input
        estimate is charged as interrupted_call_tokens (the server may have processed the prompt already)."""
        with self._lock:
            self.store.add_usage(self.task_id, interrupted_call_tokens=input_est)
            self.store.finish_reservation(res_id, "interrupted")

    def settle_own_inflight(self, reason: str = "run ended") -> list[dict]:
        """Safety net at the end of a cancelled/failed run: reservations of THIS process that are still in flight
        are released and charged like interrupted calls, so 'reserved' never stays occupied after the run."""
        import os
        left = [r for r in self.store.inflight(self.task_id) if r.get("pid") == os.getpid()]
        for r in left:
            self.store.add_usage(self.task_id, interrupted_call_tokens=r["input_est"])
            self.store.finish_reservation(r["id"], "interrupted")
            self.store.event(self.task_id, "budget_reservation_released", {"reservation": r["id"], "charged": r["input_est"],
                                                                            "reason": reason})
        return left

    def recover_inflight(self) -> list[dict]:
        """Charge reservations of a previous process that died during an LLM call."""
        lost = self.store.inflight(self.task_id)
        for r in lost:
            self.store.add_usage(self.task_id, interrupted_call_tokens=r["input_est"])
            self.store.finish_reservation(r["id"], "interrupted")
            self.store.event(self.task_id, "budget_interrupted_call", {"reservation": r["id"], "charged": r["input_est"],
                                                                        "pid": r["pid"]})
        return lost

    # -- discrete resources ------------------------------------------------------
    _COUNTERS = {"tool": ("tool_calls", "max_tool_calls"), "search": ("search_calls", "max_search_calls"),
                 "http": ("http_requests", "max_http_requests")}

    def check(self, resource: str, n: int = 1) -> None:
        used_f, limit_f = self._COUNTERS[resource]
        used = self.store.usage(self.task_id).get(used_f, 0)
        limit = getattr(self.limits, limit_f)
        if used + n > limit:
            raise BudgetExhausted(used_f, n, max(limit - used, 0), limit)

    def take(self, resource: str, n: int = 1, **extra: int) -> None:
        """Atomic check + count (parallel tool calls share the same counters)."""
        with self._lock:
            self.check(resource, n)
            self.store.add_usage(self.task_id, **{self._COUNTERS[resource][0]: n}, **extra)

    def add(self, **deltas: int) -> None:
        self.store.add_usage(self.task_id, **deltas)

    # -- active time / deadline -----------------------------------------------------
    def start_clock(self) -> None:
        self._mark = time.monotonic()

    def tick(self) -> None:
        with self._lock:
            if self._mark is None:
                return
            now = time.monotonic()
            self.store.add_usage(self.task_id, active_seconds=now - self._mark)
            self._mark = now

    def stop_clock(self) -> None:
        self.tick()
        self._mark = None

    def check_deadline(self) -> None:
        self.tick()
        used = self.store.usage(self.task_id).get("active_seconds", 0.0)
        if used > self.limits.task_deadline_s:
            raise BudgetExhausted("task_deadline_s", round(used, 1), 0, self.limits.task_deadline_s)

    # -- reporting -------------------------------------------------------------------
    def snapshot(self) -> dict:
        u = self.store.usage(self.task_id)
        L = self.limits
        return {
            "tokens": {"spent": self.spent_tokens(u), "reserved": self.store.reserved_tokens(self.task_id),
                       "limit": L.max_total_tokens,
                       "of_which": {f: u.get(f, 0) for f in TOKEN_FIELDS}},
            "model_calls": {"used": u.get("model_calls", 0), "limit": L.max_model_calls},
            "compaction_calls": u.get("compaction_calls", 0),
            "failed_calls": u.get("failed_calls", 0),
            "tool_calls": {"used": u.get("tool_calls", 0), "limit": L.max_tool_calls},
            "search_calls": {"used": u.get("search_calls", 0), "limit": L.max_search_calls},
            "http_requests": {"used": u.get("http_requests", 0), "limit": L.max_http_requests},
            "cache_hits": u.get("cache_hits", 0),
            "bytes_downloaded": u.get("bytes_downloaded", 0),
            "active_seconds": {"used": round(u.get("active_seconds", 0.0), 1), "limit": L.task_deadline_s},
            "peak_input_tokens": u.get("peak_input_tokens", 0),
        }
