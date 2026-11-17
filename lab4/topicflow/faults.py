"""Fault injection for the failure scenarios (workspace/.harness/lab4/faults.json).

  python -m topicflow.cli fault --activity publish_report --point after_write --times 1
  python -m topicflow.cli fault --activity stage_batch --times always
  python -m topicflow.cli fault --clear

The file is re-read on every check, so faults are switched on/off without restarting the worker. The counter is
decremented and saved BEFORE raising, so a retried attempt sees the fault already consumed.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_lock = threading.RLock()


class InjectedFault(RuntimeError):
    pass


class Faults:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def set(self, activity: str, point: str = "before", times: int = 1) -> dict:
        with _lock:
            data = self.load()
            data[activity] = {"point": point, "remaining": times, "fired": 0}
            self._save(data)
            return data

    def clear(self) -> None:
        with _lock:
            if self.path.exists():
                self.path.unlink()

    def check(self, activity: str, point: str, attempt: int) -> None:
        with _lock:
            data = self.load()
            f = data.get(activity)
            if not f or f["point"] != point or f["remaining"] == 0:
                return
            if f["remaining"] > 0:
                f["remaining"] -= 1
            f["fired"] += 1
            self._save(data)
        raise InjectedFault(f"injected fault in {activity} at '{point}' (attempt {attempt}, fired {f['fired']} time(s))")
