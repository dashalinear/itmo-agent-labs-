"""Durable harness state in SQLite (<task root>/.harness/state.db).

The LangGraph checkpointer (graph state, messages, context summary) lives in a
separate SQLite file (checkpoints.db). This database holds everything the
harness itself must not lose on restart:

  tasks          contract (goal, constraints, criteria), status, limits, plan, summary copy
  usage          spent budget: tokens, calls, tools, search, HTTP, cache hits, active time
  reservations   in-flight LLM budget reservations (charged after a crash)
  control        pause / cancel requests from another process
  documents      local documents in scope (id, hash, pages) for progress
  ranges         processed page / line ranges per document version
  evidence       verified quotes with source and position (idempotent key)
  searches       search queries and their results
  urls           research queue: found / pending / running / done / error / skipped
  operations     idempotency registry of side-effecting operations
  sections       report sections (upsert by section_id)
  artifacts      offloaded tool outputs and web material copies
  compactions    compaction log (size before/after, method, cost)
  model_calls    window accounting of every model call (estimate, reserve, limits, actual)
  events         audit log (guardrail blocks, budget stops, status changes)

Every write is one short transaction (WAL mode), so the database is always at a
consistent boundary; see README "Окна сбоя".
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

STATUSES = ("queued", "running", "paused", "completed", "failed", "cancelled")
URL_STATUSES = ("found", "pending", "running", "done", "error", "skipped")
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY, kind TEXT, request TEXT, spec TEXT, status TEXT, status_reason TEXT,
  limits TEXT, plan TEXT, summary TEXT, summary_version INTEGER DEFAULT 0, completion TEXT,
  runs INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS usage (
  task_id TEXT PRIMARY KEY, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
  compaction_input_tokens INTEGER DEFAULT 0, compaction_output_tokens INTEGER DEFAULT 0,
  failed_call_tokens INTEGER DEFAULT 0, interrupted_call_tokens INTEGER DEFAULT 0,
  model_calls INTEGER DEFAULT 0, compaction_calls INTEGER DEFAULT 0, failed_calls INTEGER DEFAULT 0,
  tool_calls INTEGER DEFAULT 0, search_calls INTEGER DEFAULT 0, http_requests INTEGER DEFAULT 0,
  cache_hits INTEGER DEFAULT 0, bytes_downloaded INTEGER DEFAULT 0, active_seconds REAL DEFAULT 0,
  peak_input_tokens INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reservations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, kind TEXT, tokens INTEGER, input_est INTEGER,
  pid INTEGER, status TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS control (task_id TEXT PRIMARY KEY, action TEXT, reason TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS documents (
  task_id TEXT, path TEXT, document_id TEXT, content_hash TEXT, pages INTEGER, lines INTEGER, chars INTEGER,
  searched INTEGER DEFAULT 0, status TEXT DEFAULT 'pending', updated_at TEXT, PRIMARY KEY (task_id, path));
CREATE TABLE IF NOT EXISTS ranges (
  task_id TEXT, path TEXT, content_hash TEXT, unit TEXT, start_no INTEGER, end_no INTEGER, ts TEXT,
  PRIMARY KEY (task_id, path, content_hash, unit, start_no, end_no));
CREATE TABLE IF NOT EXISTS evidence (
  evidence_id TEXT PRIMARY KEY, task_id TEXT, claim TEXT, kind TEXT, source_type TEXT, source TEXT,
  document_id TEXT, content_hash TEXT, page_start INTEGER, page_end INTEGER, line_start INTEGER,
  line_end INTEGER, quote TEXT, created_at TEXT, fragment_id TEXT);
CREATE TABLE IF NOT EXISTS searches (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, query TEXT, query_norm TEXT, provider TEXT,
  results TEXT, new_urls INTEGER, ts TEXT);
CREATE TABLE IF NOT EXISTS urls (
  task_id TEXT, url_key TEXT, url TEXT, canonical TEXT, final_url TEXT, title TEXT, snippet TEXT,
  status TEXT, discovered_by TEXT, reason TEXT, attempts INTEGER DEFAULT 0, http_status INTEGER,
  artifact_id TEXT, content_hash TEXT, error TEXT, duplicate_of TEXT, rank INTEGER, updated_at TEXT,
  PRIMARY KEY (task_id, url_key));
CREATE TABLE IF NOT EXISTS operations (
  op_key TEXT PRIMARY KEY, task_id TEXT, kind TEXT, status TEXT, result TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS sections (
  task_id TEXT, section_id TEXT, title TEXT, body TEXT, ordinal INTEGER, version INTEGER DEFAULT 1,
  updated_at TEXT, PRIMARY KEY (task_id, section_id));
CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id TEXT, task_id TEXT, kind TEXT, source TEXT, chars INTEGER, lines INTEGER, path TEXT,
  meta TEXT, created_at TEXT, PRIMARY KEY (artifact_id, task_id));
CREATE TABLE IF NOT EXISTS compactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, ts TEXT, before_tokens INTEGER, after_tokens INTEGER,
  removed_messages INTEGER, kept_messages INTEGER, summary_tokens INTEGER, method TEXT, chunks INTEGER,
  cost_input INTEGER, cost_output INTEGER, summary TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS model_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, ts TEXT, kind TEXT, estimated_input INTEGER,
  reserve INTEGER, working_limit INTEGER, window_tokens INTEGER, method TEXT, actual_input INTEGER,
  actual_output INTEGER, duration_s REAL, status TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS archive (task_id TEXT, msg_id TEXT, PRIMARY KEY (task_id, msg_id));
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, ts TEXT, kind TEXT, data TEXT);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


class TaskStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(evidence)")}
        if "fragment_id" not in cols:                       # migration of a lab 2 database
            self.db.execute("ALTER TABLE evidence ADD COLUMN fragment_id TEXT")

    # -- small durable key/value (e.g. token calibration per task) -------------------
    def set_meta(self, key: str, value: Any) -> None:
        self._exec("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, _j(value)))

    def get_meta(self, key: str, default: Any = None) -> Any:
        r = self._one("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(r["value"]) if r and r["value"] is not None else default

    def close(self) -> None:
        with self._lock:
            self.db.close()

    # -- helpers ----------------------------------------------------------
    def _exec(self, sql: str, params: Iterable = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.db.execute(sql, tuple(params))

    def _tx(self, statements: list[tuple[str, Iterable]]) -> None:
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                for sql, params in statements:
                    self.db.execute(sql, tuple(params))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def _one(self, sql: str, params: Iterable = ()) -> dict | None:
        # fetch under the same lock: with the connection shared between threads, a fetch outside the lock could read
        # the cursor of a parallel query (lab 3, M10: subagent_status of an existing task answered unknown_task)
        with self._lock:
            row = self.db.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, params: Iterable = ()) -> list[dict]:
        with self._lock:
            rows = self.db.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    # -- tasks --------------------------------------------------------------
    def create_task(self, task_id: str, kind: str, request: str, spec: dict, limits: dict) -> dict:
        if self.get_task(task_id):
            raise ValueError(f"task {task_id} already exists")
        ts = now_iso()
        self._tx([
            ("INSERT INTO tasks (task_id, kind, request, spec, status, limits, plan, created_at, updated_at) "
             "VALUES (?,?,?,?,?,?,?,?,?)", (task_id, kind, request, _j(spec), "queued", _j(limits), "[]", ts, ts)),
            ("INSERT INTO usage (task_id) VALUES (?)", (task_id,)),
        ])
        self.event(task_id, "status", {"status": "queued"})
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict | None:
        t = self._one("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        if not t:
            return None
        for k in ("spec", "limits", "plan", "completion"):
            t[k] = json.loads(t[k]) if t.get(k) else ({} if k != "plan" else [])
        return t

    def list_tasks(self) -> list[dict]:
        return self._all("SELECT task_id, kind, status, status_reason, runs, updated_at FROM tasks ORDER BY created_at")

    def set_status(self, task_id: str, status: str, reason: str | None = None) -> None:
        assert status in STATUSES, status
        self._exec("UPDATE tasks SET status=?, status_reason=?, updated_at=? WHERE task_id=?",
                   (status, reason, now_iso(), task_id))
        self.event(task_id, "status", {"status": status, "reason": reason})

    def update_task(self, task_id: str, **fields: Any) -> None:
        allowed = {"plan", "summary", "summary_version", "limits", "completion", "runs", "spec"}
        sets, params = [], []
        for k, v in fields.items():
            assert k in allowed, k
            sets.append(f"{k}=?")
            params.append(_j(v) if k in ("plan", "limits", "completion", "spec") else v)
        sets.append("updated_at=?")
        params += [now_iso(), task_id]
        self._exec(f"UPDATE tasks SET {', '.join(sets)} WHERE task_id=?", params)

    # -- control (pause / cancel requests, possibly from another process) ----
    def request_control(self, task_id: str, action: str, reason: str = "") -> None:
        assert action in ("pause", "cancel")
        self._exec("INSERT OR REPLACE INTO control VALUES (?,?,?,?)", (task_id, action, reason, now_iso()))
        self.event(task_id, "control_requested", {"action": action, "reason": reason})

    def get_control(self, task_id: str) -> dict | None:
        return self._one("SELECT * FROM control WHERE task_id=?", (task_id,))

    def clear_control(self, task_id: str) -> None:
        self._exec("DELETE FROM control WHERE task_id=?", (task_id,))

    def heartbeat(self, task_id: str) -> None:
        self._exec("INSERT OR REPLACE INTO meta VALUES (?, ?)", (f"hb:{task_id}", _j({"pid": os.getpid(), "t": time.time()})))

    def alive(self, task_id: str, within_s: float = 5.0) -> bool:
        r = self._one("SELECT value FROM meta WHERE key=?", (f"hb:{task_id}",))
        return bool(r) and time.time() - json.loads(r["value"])["t"] < within_s

    # -- usage / reservations ------------------------------------------------
    def usage(self, task_id: str) -> dict:
        return self._one("SELECT * FROM usage WHERE task_id=?", (task_id,)) or {}

    def add_usage(self, task_id: str, **deltas: float) -> None:
        if not deltas:
            return
        sets = ", ".join(f"{k}={k}+?" for k in deltas)
        self._exec(f"UPDATE usage SET {sets} WHERE task_id=?", (*deltas.values(), task_id))

    def set_peak(self, task_id: str, tokens: int) -> None:
        self._exec("UPDATE usage SET peak_input_tokens=MAX(peak_input_tokens, ?) WHERE task_id=?", (tokens, task_id))

    def add_reservation(self, task_id: str, kind: str, tokens: int, input_est: int) -> int:
        cur = self._exec("INSERT INTO reservations (task_id, kind, tokens, input_est, pid, status, ts) VALUES (?,?,?,?,?,?,?)",
                         (task_id, kind, tokens, input_est, os.getpid(), "inflight", now_iso()))
        return int(cur.lastrowid)

    def finish_reservation(self, res_id: int, status: str = "done") -> None:
        self._exec("UPDATE reservations SET status=? WHERE id=?", (status, res_id))

    def inflight(self, task_id: str) -> list[dict]:
        return self._all("SELECT * FROM reservations WHERE task_id=? AND status='inflight'", (task_id,))

    def reserved_tokens(self, task_id: str) -> int:
        row = self._one("SELECT COALESCE(SUM(tokens),0) AS s FROM reservations WHERE task_id=? AND status='inflight'", (task_id,))
        return int(row["s"]) if row else 0

    # -- events ---------------------------------------------------------------
    def event(self, task_id: str, kind: str, data: Any = None) -> None:
        self._exec("INSERT INTO events (task_id, ts, kind, data) VALUES (?,?,?,?)", (task_id, now_iso(), kind, _j(data)))

    def events(self, task_id: str, kind: str | None = None, limit: int = 1000) -> list[dict]:
        if kind:
            rows = self._all("SELECT * FROM events WHERE task_id=? AND kind=? ORDER BY id DESC LIMIT ?", (task_id, kind, limit))
        else:
            rows = self._all("SELECT * FROM events WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, limit))
        for r in rows:
            r["data"] = json.loads(r["data"]) if r["data"] else None
        return rows[::-1]

    # -- documents / ranges ------------------------------------------------------
    def upsert_document(self, task_id: str, path: str, document_id: str | None, content_hash: str,
                        pages: int, lines: int, chars: int) -> str:
        """Returns 'new', 'same' or 'changed' (content hash differs from the recorded version)."""
        old = self._one("SELECT * FROM documents WHERE task_id=? AND path=?", (task_id, path))
        if old and old["content_hash"] == content_hash:
            return "same"
        status = "changed" if old else "pending"
        self._exec("INSERT OR REPLACE INTO documents (task_id, path, document_id, content_hash, pages, lines, chars, "
                   "searched, status, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (task_id, path, document_id, content_hash, pages, lines, chars, 0, status, now_iso()))
        return "changed" if old else "new"

    def documents_progress(self, task_id: str) -> list[dict]:
        """Documents with the pages read (by read_pages or partly by read_lines) and a status from that."""
        out = []
        for d in self.documents(task_id):
            read = set()
            for r in self.ranges(task_id, d["path"], d["content_hash"]):
                if r["unit"] in ("page", "page_part"):
                    read.update(range(r["start_no"], r["end_no"] + 1))
            out.append({**d, "pages_read": len(read),
                        "status": "read" if read else ("searched" if d["searched"] else d["status"])})
        return out

    def documents(self, task_id: str) -> list[dict]:
        return self._all("SELECT * FROM documents WHERE task_id=? ORDER BY path", (task_id,))

    def mark_searched(self, task_id: str, path: str) -> None:
        self._exec("UPDATE documents SET searched=1, updated_at=? WHERE task_id=? AND path=?", (now_iso(), task_id, path))

    def add_range(self, task_id: str, path: str, content_hash: str, unit: str, start: int, end: int) -> bool:
        cur = self._exec("INSERT OR IGNORE INTO ranges VALUES (?,?,?,?,?,?,?)",
                         (task_id, path, content_hash, unit, start, end, now_iso()))
        return cur.rowcount == 1

    def ranges(self, task_id: str, path: str | None = None, content_hash: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM ranges WHERE task_id=?", [task_id]
        if path:
            sql += " AND path=?"
            params.append(path)
        if content_hash:
            sql += " AND content_hash=?"
            params.append(content_hash)
        return self._all(sql + " ORDER BY path, unit, start_no", params)

    # -- evidence -------------------------------------------------------------
    def add_evidence(self, rec: dict) -> bool:
        cols = ("evidence_id", "task_id", "claim", "kind", "source_type", "source", "document_id", "content_hash",
                "page_start", "page_end", "line_start", "line_end", "quote", "created_at", "fragment_id")
        cur = self._exec(f"INSERT OR IGNORE INTO evidence ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                         [rec.get(c) if c != "created_at" else now_iso() for c in cols])
        return cur.rowcount == 1

    def extend_evidence(self, evidence_id: str, **fields) -> None:
        """A longer quote of the same place replaces the text of an existing evidence (its id stays)."""
        allowed = {k: v for k, v in fields.items() if k in ("quote", "claim", "line_start", "line_end", "page_start", "page_end")
                   and v is not None}
        if allowed:
            sets = ", ".join(f"{k}=?" for k in allowed)
            self._exec(f"UPDATE evidence SET {sets} WHERE evidence_id=?", [*allowed.values(), evidence_id])

    def evidence(self, task_id: str) -> list[dict]:
        return self._all("SELECT * FROM evidence WHERE task_id=? ORDER BY created_at, evidence_id", (task_id,))

    # -- searches / urls --------------------------------------------------------
    def find_search(self, task_id: str, query_norm: str) -> dict | None:
        r = self._one("SELECT * FROM searches WHERE task_id=? AND query_norm=? ORDER BY id LIMIT 1", (task_id, query_norm))
        if r:
            r["results"] = json.loads(r["results"])
        return r

    def add_search(self, task_id: str, query: str, query_norm: str, provider: str, results: list, new_urls: int) -> None:
        self._exec("INSERT INTO searches (task_id, query, query_norm, provider, results, new_urls, ts) VALUES (?,?,?,?,?,?,?)",
                   (task_id, query, query_norm, provider, _j(results), new_urls, now_iso()))

    def searches(self, task_id: str) -> list[dict]:
        rows = self._all("SELECT id, query, provider, new_urls, ts, results FROM searches WHERE task_id=? ORDER BY id", (task_id,))
        for r in rows:
            r["results"] = len(json.loads(r["results"]))
        return rows

    def get_url(self, task_id: str, url_key: str) -> dict | None:
        return self._one("SELECT * FROM urls WHERE task_id=? AND url_key=?", (task_id, url_key))

    def add_url(self, task_id: str, url_key: str, url: str, canonical: str, status: str, discovered_by: str,
                title: str = "", snippet: str = "", rank: int | None = None, reason: str = "") -> bool:
        assert status in URL_STATUSES
        cur = self._exec("INSERT OR IGNORE INTO urls (task_id, url_key, url, canonical, title, snippet, status, "
                         "discovered_by, reason, rank, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (task_id, url_key, url, canonical, title, snippet, status, discovered_by, reason, rank, now_iso()))
        return cur.rowcount == 1

    def set_url(self, task_id: str, url_key: str, **fields: Any) -> None:
        if "status" in fields:
            assert fields["status"] in URL_STATUSES
        sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=?"
        self._exec(f"UPDATE urls SET {sets} WHERE task_id=? AND url_key=?", (*fields.values(), now_iso(), task_id, url_key))

    def urls(self, task_id: str, status: str | None = None) -> list[dict]:
        if status:
            return self._all("SELECT * FROM urls WHERE task_id=? AND status=? ORDER BY updated_at", (task_id, status))
        return self._all("SELECT * FROM urls WHERE task_id=? ORDER BY rowid", (task_id,))

    def url_counts(self, task_id: str) -> dict:
        rows = self._all("SELECT status, COUNT(*) AS n FROM urls WHERE task_id=? GROUP BY status", (task_id,))
        counts = {s: 0 for s in URL_STATUSES}
        counts.update({r["status"]: r["n"] for r in rows})
        counts["found_total"] = sum(counts[s] for s in URL_STATUSES)
        counts["selected_total"] = counts["pending"] + counts["running"] + counts["done"] + counts["error"]
        counts["read_total"] = counts["done"]
        return counts

    # -- operations (idempotency registry) ----------------------------------------
    def op_get(self, op_key: str) -> dict | None:
        r = self._one("SELECT * FROM operations WHERE op_key=?", (op_key,))
        if r and r.get("result"):
            r["result"] = json.loads(r["result"])
        return r

    def op_start(self, op_key: str, task_id: str, kind: str) -> None:
        self._exec("INSERT OR IGNORE INTO operations VALUES (?,?,?,?,?,?)", (op_key, task_id, kind, "started", None, now_iso()))

    def op_done(self, op_key: str, result: Any) -> None:
        self._exec("UPDATE operations SET status='done', result=?, ts=? WHERE op_key=?", (_j(result), now_iso(), op_key))

    # -- report sections ---------------------------------------------------------
    def upsert_section(self, task_id: str, section_id: str, title: str, body: str) -> str:
        old = self._one("SELECT * FROM sections WHERE task_id=? AND section_id=?", (task_id, section_id))
        if old and old["title"] == title and old["body"] == body:
            return "unchanged"
        if old:
            self._exec("UPDATE sections SET title=?, body=?, version=version+1, updated_at=? WHERE task_id=? AND section_id=?",
                       (title, body, now_iso(), task_id, section_id))
            return "updated"
        n = self._one("SELECT COUNT(*) AS n FROM sections WHERE task_id=?", (task_id,))["n"]
        self._exec("INSERT INTO sections VALUES (?,?,?,?,?,?,?)", (task_id, section_id, title, body, n + 1, 1, now_iso()))
        return "created"

    def sections(self, task_id: str) -> list[dict]:
        return self._all("SELECT * FROM sections WHERE task_id=? ORDER BY ordinal", (task_id,))

    # -- artifacts -----------------------------------------------------------------
    def add_artifact(self, artifact_id: str, task_id: str, kind: str, source: str, chars: int, lines: int,
                     path: str, meta: dict) -> bool:
        cur = self._exec("INSERT OR IGNORE INTO artifacts VALUES (?,?,?,?,?,?,?,?,?)",
                         (artifact_id, task_id, kind, source, chars, lines, path, _j(meta), now_iso()))
        return cur.rowcount == 1

    def artifact(self, artifact_id: str) -> dict | None:
        r = self._one("SELECT * FROM artifacts WHERE artifact_id=? LIMIT 1", (artifact_id,))
        if r:
            r["meta"] = json.loads(r["meta"]) if r["meta"] else {}
        return r

    def artifacts(self, task_id: str) -> list[dict]:
        return self._all("SELECT artifact_id, kind, source, chars, lines, created_at FROM artifacts WHERE task_id=? ORDER BY created_at",
                         (task_id,))

    # -- compactions / model calls / archive ----------------------------------------
    def add_compaction(self, task_id: str, **rec: Any) -> int:
        cols = ["task_id", "ts", *rec.keys()]
        cur = self._exec(f"INSERT INTO compactions ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                         (task_id, now_iso(), *rec.values()))
        return int(cur.lastrowid)

    def compactions(self, task_id: str) -> list[dict]:
        return self._all("SELECT * FROM compactions WHERE task_id=? ORDER BY id", (task_id,))

    def add_model_call(self, task_id: str, **rec: Any) -> int:
        cols = ["task_id", "ts", *rec.keys()]
        cur = self._exec(f"INSERT INTO model_calls ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                         (task_id, now_iso(), *rec.values()))
        return int(cur.lastrowid)

    def update_model_call(self, call_id: int, **rec: Any) -> None:
        sets = ", ".join(f"{k}=?" for k in rec)
        self._exec(f"UPDATE model_calls SET {sets} WHERE id=?", (*rec.values(), call_id))

    def model_calls(self, task_id: str) -> list[dict]:
        return self._all("SELECT * FROM model_calls WHERE task_id=? ORDER BY id", (task_id,))

    def archive_new(self, task_id: str, msg_ids: list[str]) -> list[str]:
        """Register message ids as archived; returns the ids that were not archived before."""
        fresh = []
        with self._lock:
            for mid in msg_ids:
                cur = self.db.execute("INSERT OR IGNORE INTO archive VALUES (?,?)", (task_id, mid))
                if cur.rowcount == 1:
                    fresh.append(mid)
        return fresh


def wait_until(predicate, timeout: float = 5.0, step: float = 0.05) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step)
    return predicate()
