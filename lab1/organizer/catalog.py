"""Service catalog (workspace/catalog.json): stable identity and navigation.

The catalog is maintained by deterministic code, not by free-form model
writes, so IDs, hashes and paths stay consistent:
* ``move`` registers a document on its first move out of input (UUID4 id,
  sha256 content hash) and updates ``current_path`` on every later move;
* the agent fills title/description through ``catalog_update``;
* MCP index results update ``indexing_status`` via CatalogSyncMiddleware.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .pathguard import Workspace
from .textutil import file_hash, read_text, split_lines

SCHEMA_VERSION = 1
STATUSES = ("not_indexed", "indexed", "error", "pending_sync")
_lock = threading.RLock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def guess_title(path: Path) -> str:
    try:
        text = read_text(path)
    except (UnicodeDecodeError, OSError):
        return path.stem
    for line in split_lines(text):
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^#{1,6}\s+(.*)$", s)
        return (m.group(1) if m else s)[:100].strip() or path.stem
    return path.stem


class Catalog:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    # -- persistence ------------------------------------------------------
    def load(self) -> dict[str, Any]:
        with _lock:
            if not self.ws.catalog_path.exists():
                return {"schema_version": SCHEMA_VERSION, "updated_at": now_iso(), "documents": []}
            return json.loads(self.ws.catalog_path.read_text(encoding="utf-8"))

    def save(self, cat: dict[str, Any]) -> None:
        with _lock:
            cat["updated_at"] = now_iso()
            cat["documents"].sort(key=lambda d: d["current_path"])
            tmp = self.ws.catalog_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(cat, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.ws.catalog_path)  # atomic on the same volume

    # -- queries ----------------------------------------------------------
    @staticmethod
    def _find(cat, *, document_id=None, path=None, content_hash=None):
        for d in cat["documents"]:
            if document_id and d["document_id"] == document_id:
                return d
            if path and d["current_path"] == path:
                return d
        if content_hash:
            return [d for d in cat["documents"] if d["content_hash"] == content_hash]
        return None

    def get(self, document_id: str) -> dict | None:
        return self._find(self.load(), document_id=document_id)

    def by_path(self, rel: str) -> dict | None:
        return self._find(self.load(), path=rel)

    def same_content(self, content_hash: str, exclude_id: str | None = None) -> list[dict]:
        docs = self._find(self.load(), content_hash=content_hash) or []
        return [d for d in docs if d["document_id"] != exclude_id]

    # -- mutations --------------------------------------------------------
    def _new_entry(self, abs_path: Path, original_rel: str) -> dict:
        size = abs_path.stat().st_size
        try:
            lines = len(split_lines(read_text(abs_path)))
        except UnicodeDecodeError:
            lines = None
        entry = {
            "document_id": str(uuid.uuid4()),
            "original_path": original_rel,
            "current_path": original_rel,
            "title": guess_title(abs_path),
            "category": None,
            "description": "",
            "content_hash": file_hash(abs_path),
            "size_bytes": size,
            "line_count": lines,
            "is_empty": size == 0,
            "note": "Empty file: kept in catalog, nothing to index." if size == 0 else "",
            "indexing_status": "not_indexed",
            "index_error": None,
            "excerpts": [],
            "indexed_at": None,
            "registered_at": now_iso(),
            "moves": [],
        }
        return entry

    def record_move(self, old_rel: str, new_abs: Path) -> dict:
        """Register (if needed) and relocate a document. Returns the entry."""
        new_rel = self.ws.rel(new_abs)
        with _lock:
            cat = self.load()
            entry = self._find(cat, path=old_rel)
            if entry is None:
                entry = self._new_entry(new_abs, old_rel)
                cat["documents"].append(entry)
            entry["current_path"] = new_rel
            entry["category"] = self.category_of(new_rel)
            entry["moves"].append({"from": old_rel, "to": new_rel, "at": now_iso()})
            if entry["indexing_status"] in ("indexed", "pending_sync"):
                entry["indexing_status"] = "pending_sync"
                entry["index_error"] = f"Index still points to an old path; sync current_path={new_rel}"
            self.save(cat)
            return dict(entry)

    @staticmethod
    def category_of(rel: str) -> str | None:
        parts = rel.split("/")
        if len(parts) >= 3 and parts[0] == "data":
            return parts[1]
        return None

    def update(self, document_id: str, **fields: Any) -> dict:
        allowed = {"title", "description", "note"}
        with _lock:
            cat = self.load()
            entry = self._find(cat, document_id=document_id)
            if entry is None:
                raise KeyError(document_id)
            for k, v in fields.items():
                if k in allowed and v is not None:
                    entry[k] = v
            self.save(cat)
            return dict(entry)

    def set_index_state(self, document_id: str, status: str, *, error: str | None = None,
                        excerpts: list[dict] | None = None) -> dict | None:
        assert status in STATUSES
        with _lock:
            cat = self.load()
            entry = self._find(cat, document_id=document_id)
            if entry is None:
                return None
            entry["indexing_status"] = status
            entry["index_error"] = error
            if excerpts is not None:
                entry["excerpts"] = excerpts
                entry["indexed_at"] = now_iso()
            self.save(cat)
            return dict(entry)

    # -- reconciliation ---------------------------------------------------
    def reconcile(self) -> dict[str, list[str]]:
        """Report catalog entries whose file is missing and data files that are
        not catalogued (e.g. copied there by hand)."""
        cat = self.load()
        missing = [d["current_path"] for d in cat["documents"]
                   if not (self.ws.root / d["current_path"]).is_file()]
        known = {d["current_path"] for d in cat["documents"]}
        unregistered = []
        for dirpath, _dirs, files in os.walk(self.ws.data, followlinks=False):
            for name in files:
                rel = self.ws.rel(Path(dirpath) / name)
                if rel not in known:
                    unregistered.append(rel)
        return {"missing_files": sorted(missing), "unregistered_data_files": sorted(unregistered)}

    def register_existing(self, rel: str) -> dict:
        """Register a file already lying in data (manual placement)."""
        abs_path = self.ws.resolve(rel, must_exist=True)
        with _lock:
            cat = self.load()
            entry = self._find(cat, path=rel)
            if entry is None:
                entry = self._new_entry(abs_path, rel)
                entry["category"] = self.category_of(rel)
                cat["documents"].append(entry)
                self.save(cat)
            return dict(entry)
