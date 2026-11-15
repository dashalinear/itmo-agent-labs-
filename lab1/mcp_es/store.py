"""Elasticsearch operations used by the MCP server (and by admin scripts).

The agent never calls this module directly: it reaches Elasticsearch only
through the MCP tools defined in mcp_es/server.py.
"""
from __future__ import annotations

import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

from elasticsearch import Elasticsearch, NotFoundError
from elasticsearch.helpers import scan

from .schema import INDEX_MAPPING, Excerpt, excerpt_key


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ExcerptStore:
    def __init__(self, url: str | None = None, index: str | None = None, timeout_s: float | None = None) -> None:
        self.url = url or os.getenv("ES_URL", "http://localhost:9200")
        self.index = index or os.getenv("ES_INDEX", "itmo_lab1_excerpts")
        timeout = timeout_s or float(os.getenv("ES_TIMEOUT_S", "10"))
        kwargs: dict[str, Any] = {"request_timeout": timeout, "max_retries": 1, "retry_on_timeout": True}
        if os.getenv("ES_API_KEY"):
            kwargs["api_key"] = os.getenv("ES_API_KEY")
        self.es = Elasticsearch(self.url, **kwargs)

    # -- index management ---------------------------------------------------
    def ensure_index(self) -> bool:
        if self.es.indices.exists(index=self.index):
            return False
        self.es.indices.create(index=self.index, **INDEX_MAPPING)
        return True

    def drop_index(self) -> None:
        self.es.indices.delete(index=self.index, ignore_unavailable=True)

    # -- writes ---------------------------------------------------------------
    def upsert(self, excerpts: Iterable[Excerpt], mode: str = "replace") -> dict:
        self.ensure_index()
        created = updated = 0
        per_doc: dict[str, list[str]] = {}
        for ex in excerpts:
            if ex.end_line < ex.start_line:
                raise ValueError(f"end_line < start_line for {ex.document_id}")
            if not ex.text.strip():
                raise ValueError(f"Empty excerpt text for {ex.document_id}:{ex.start_line}-{ex.end_line}")
            key = excerpt_key(ex.document_id, ex.content_hash, ex.start_line, ex.end_line)
            body = ex.model_dump()
            body["excerpt_id"] = key
            body["indexed_at"] = ex.indexed_at or _now()
            res = self.es.index(index=self.index, id=key, document=body)
            if res["result"] == "created":
                created += 1
            else:
                updated += 1
            per_doc.setdefault(ex.document_id, []).append(key)
        deleted = 0
        if mode == "replace":
            for doc_id, keep in per_doc.items():
                r = self.es.delete_by_query(
                    index=self.index, refresh=True, conflicts="proceed",
                    query={"bool": {"filter": [{"term": {"document_id": doc_id}}],
                                    "must_not": [{"ids": {"values": keep}}]}})
                deleted += r.get("deleted", 0)
        self.es.indices.refresh(index=self.index)
        return {"created": created, "updated": updated, "deleted_stale": deleted,
                "documents": {d: {"excerpt_ids": ids, "count": self.count(d)} for d, ids in per_doc.items()}}

    def update_path(self, document_id: str, new_path: str, category: str | None = None) -> dict:
        self.ensure_index()
        script = "ctx._source.current_path = params.p;"
        params: dict[str, Any] = {"p": new_path}
        if category is not None:
            script += " ctx._source.category = params.c;"
            params["c"] = category
        r = self.es.update_by_query(index=self.index, refresh=True, conflicts="proceed",
                                    query={"term": {"document_id": document_id}},
                                    script={"source": script, "params": params, "lang": "painless"})
        return {"document_id": document_id, "new_path": new_path, "updated": r.get("updated", 0),
                "total": r.get("total", 0)}

    def delete_document(self, document_id: str) -> dict:
        self.ensure_index()
        r = self.es.delete_by_query(index=self.index, refresh=True,
                                    query={"term": {"document_id": document_id}})
        return {"document_id": document_id, "deleted": r.get("deleted", 0)}

    # -- reads ----------------------------------------------------------------
    def count(self, document_id: str | None = None) -> int:
        q = {"term": {"document_id": document_id}} if document_id else {"match_all": {}}
        try:
            return self.es.count(index=self.index, query=q)["count"]
        except NotFoundError:
            return 0

    @staticmethod
    def _hit(h: dict) -> dict:
        s = h["_source"]
        out = {k: s.get(k) for k in ("excerpt_id", "document_id", "current_path", "title", "category",
                                     "text", "start_line", "end_line", "content_hash", "summary", "indexed_at")}
        if "_score" in h and h["_score"] is not None:
            out["score"] = round(h["_score"], 3)
        if h.get("highlight"):
            out["highlight"] = [frag for frags in h["highlight"].values() for frag in frags][:3]
        return out

    def search(self, query: str, size: int = 10, category: str | None = None,
               document_id: str | None = None, path_prefix: str | None = None) -> dict:
        self.ensure_index()
        filters = []
        if category:
            filters.append({"term": {"category": category}})
        if document_id:
            filters.append({"term": {"document_id": document_id}})
        if path_prefix:
            filters.append({"prefix": {"current_path": path_prefix}})
        size = max(1, min(int(size), 50))
        r = self.es.search(
            index=self.index, size=size,
            query={"bool": {"must": [{"multi_match": {
                "query": query, "type": "best_fields",
                "fields": ["text^3", "text.std^2", "title^2", "title.std", "summary"]}}],
                "filter": filters}},
            highlight={"fields": {"text": {"fragment_size": 160, "number_of_fragments": 2}}},
            track_total_hits=True)
        hits = [self._hit(h) for h in r["hits"]["hits"]]
        return {"query": query, "total": r["hits"]["total"]["value"], "returned": len(hits), "hits": hits}

    def get_document(self, document_id: str) -> dict:
        self.ensure_index()
        r = self.es.search(index=self.index, size=200, query={"term": {"document_id": document_id}},
                           sort=[{"start_line": "asc"}])
        hits = [self._hit(h) for h in r["hits"]["hits"]]
        return {"document_id": document_id, "count": len(hits), "excerpts": hits}

    def stats(self) -> dict:
        self.ensure_index()
        per_doc = Counter()
        paths: dict[str, set] = {}
        for h in scan(self.es, index=self.index, query={"query": {"match_all": {}}},
                      _source=["document_id", "current_path"]):
            s = h["_source"]
            per_doc[s["document_id"]] += 1
            paths.setdefault(s["document_id"], set()).add(s["current_path"])
        return {"index": self.index, "total_excerpts": sum(per_doc.values()),
                "documents": [{"document_id": d, "excerpts": c, "current_paths": sorted(paths[d])}
                              for d, c in sorted(per_doc.items())]}

    def export_all(self) -> list[dict]:
        self.ensure_index()
        rows = [h["_source"] for h in scan(self.es, index=self.index, query={"query": {"match_all": {}}})]
        return sorted(rows, key=lambda s: (s["current_path"], s["start_line"]))
