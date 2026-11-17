"""Durable processing registry of lab 4 (SQLite in the harness service area, workspace/.harness/lab4.db).

  files    one row per detected input file version: path, size, content hash, batch, status, document_id, current_path
  batches  one row per detection batch = one IngestNewFiles workflow (workflow_id, run_id, harness task_id, status)
  reports  one row per topic-report run key (= Temporal workflow id): harness task, report path, status

Temporal keeps the execution history; this registry keeps the business state that must survive Temporal, the
worker and the watcher (what was detected, what was indexed where, which report belongs to which run).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  input_path TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
  batch_id TEXT NOT NULL, status TEXT NOT NULL, content_hash TEXT, staged_path TEXT,
  document_id TEXT, current_path TEXT, note TEXT, updated_at TEXT NOT NULL,
  PRIMARY KEY (input_path, size, mtime_ns));
CREATE TABLE IF NOT EXISTS batches (
  batch_id TEXT PRIMARY KEY, workflow_id TEXT, run_id TEXT, task_id TEXT, status TEXT NOT NULL,
  files TEXT NOT NULL, result TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reports (
  run_key TEXT PRIMARY KEY, workflow_id TEXT, run_id TEXT, task_id TEXT, status TEXT NOT NULL,
  path TEXT, document_id TEXT, frozen INTEGER, result TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
"""

FILE_STATUSES = ("detected", "staged", "duplicate", "indexed", "failed", "missing")
BATCH_OPEN = ("detected", "started", "running")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def batch_id_for(files: list[dict]) -> str:
    """Stable id of a detection batch: the same files (path, size, mtime) always give the same id."""
    import hashlib
    key = "\n".join(f"{f['input_path']}|{f['size']}|{f['mtime_ns']}" for f in sorted(files, key=lambda x: x["input_path"]))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


class Registry:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def _x(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.db.execute(sql, params)
            self.db.commit()
            return cur

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    # ------------------------------------------------------------------ files / batches
    def known(self, input_path: str, size: int, mtime_ns: int) -> dict | None:
        rows = self._all("SELECT * FROM files WHERE input_path=? AND size=? AND mtime_ns=?", (input_path, size, mtime_ns))
        return rows[0] if rows else None

    def add_batch(self, batch_id: str, files: list[dict]) -> bool:
        """Register a detection batch and its files atomically; False if this batch is already registered."""
        ts = now_iso()
        with self._lock:
            if self.db.execute("SELECT 1 FROM batches WHERE batch_id=?", (batch_id,)).fetchone():
                return False
            self.db.execute("INSERT INTO batches (batch_id, status, files, created_at, updated_at) VALUES (?,?,?,?,?)",
                            (batch_id, "detected", json.dumps(files, ensure_ascii=False), ts, ts))
            for f in files:
                self.db.execute("INSERT OR IGNORE INTO files (input_path, size, mtime_ns, batch_id, status, updated_at) "
                                "VALUES (?,?,?,?,?,?)", (f["input_path"], f["size"], f["mtime_ns"], batch_id, "detected", ts))
            self.db.commit()
            return True

    def batch(self, batch_id: str) -> dict | None:
        rows = self._all("SELECT * FROM batches WHERE batch_id=?", (batch_id,))
        if not rows:
            return None
        b = rows[0]
        b["files"] = json.loads(b["files"])
        b["result"] = json.loads(b["result"]) if b["result"] else None
        return b

    def batches(self, statuses: tuple[str, ...] | None = None) -> list[dict]:
        rows = self._all("SELECT batch_id FROM batches ORDER BY created_at, batch_id")
        out = [self.batch(r["batch_id"]) for r in rows]
        return [b for b in out if statuses is None or b["status"] in statuses]

    def update_batch(self, batch_id: str, **fields: Any) -> None:
        if "result" in fields:
            fields["result"] = json.dumps(fields["result"], ensure_ascii=False)
        sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=?"
        self._x(f"UPDATE batches SET {sets} WHERE batch_id=?", (*fields.values(), now_iso(), batch_id))

    def batch_files(self, batch_id: str) -> list[dict]:
        return self._all("SELECT * FROM files WHERE batch_id=? ORDER BY input_path", (batch_id,))

    def update_file(self, input_path: str, batch_id: str, **fields: Any) -> None:
        sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=?"
        self._x(f"UPDATE files SET {sets} WHERE input_path=? AND batch_id=?", (*fields.values(), now_iso(), input_path, batch_id))

    def taken_elsewhere(self, content_hash: str, batch_id: str) -> dict | None:
        """A file with this content already taken by ANOTHER batch (staged, indexed or awaiting a retry)."""
        rows = self._all("SELECT * FROM files WHERE content_hash=? AND batch_id<>? AND status IN ('staged','indexed','failed') "
                         "ORDER BY updated_at LIMIT 1", (content_hash, batch_id))
        return rows[0] if rows else None

    def files(self) -> list[dict]:
        return self._all("SELECT * FROM files ORDER BY updated_at, input_path")

    # ------------------------------------------------------------------ reports
    def report(self, run_key: str) -> dict | None:
        rows = self._all("SELECT * FROM reports WHERE run_key=?", (run_key,))
        if not rows:
            return None
        r = rows[0]
        r["result"] = json.loads(r["result"]) if r["result"] else None
        return r

    def upsert_report(self, run_key: str, **fields: Any) -> None:
        if "result" in fields:
            fields["result"] = json.dumps(fields["result"], ensure_ascii=False)
        ts = now_iso()
        with self._lock:
            if not self.db.execute("SELECT 1 FROM reports WHERE run_key=?", (run_key,)).fetchone():
                self.db.execute("INSERT INTO reports (run_key, status, created_at, updated_at) VALUES (?,?,?,?)",
                                (run_key, fields.get("status", "running"), ts, ts))
            sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=?"
            self.db.execute(f"UPDATE reports SET {sets} WHERE run_key=?", (*fields.values(), ts, run_key))
            self.db.commit()

    def reports(self) -> list[dict]:
        return [self.report(r["run_key"]) for r in self._all("SELECT run_key FROM reports ORDER BY created_at, run_key")]
