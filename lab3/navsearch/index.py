"""Elasticsearch storage of L1 and L2 (two indices, python client; ES 9.5.5 from lab 1 docker compose).

itmo_lab3_l1   one document per corpus document: title, description, summary, section titles/summaries
               (`structure`), folder, path, hash, embedding of title+description+summary+structure
itmo_lab3_l2   one document per L2 fragment (_id = fragment_id): text, section, lines, pages, prev/next,
               embedding of section title + text

Both: Russian analyzer + standard subfield, keyword fields for exact filters, dense_vector(384, cosine).
Full-text search = multi_match (BM25). Vector search = kNN. Exhaustive scans use a query sorted by
(path, ordinal) with search_after — no score, no relevance cut-off — until the end of the area.
"""
from __future__ import annotations

import os

from elasticsearch import Elasticsearch, NotFoundError, helpers

from .embed import DIMS

TEXT = {"type": "text", "analyzer": "russian", "fields": {"std": {"type": "text", "analyzer": "standard"}}}
VEC = {"type": "dense_vector", "dims": DIMS, "index": True, "similarity": "cosine"}
L1_MAPPING = {"settings": {"number_of_shards": 1, "number_of_replicas": 0},
              "mappings": {"dynamic": "strict", "properties": {
                  "document_id": {"type": "keyword"}, "path": {"type": "keyword"}, "folder": {"type": "keyword"},
                  "title": TEXT, "description": TEXT, "summary": TEXT, "structure": TEXT,
                  "content_hash": {"type": "keyword"}, "sections": {"type": "integer"}, "embedding": VEC}}}
L2_MAPPING = {"settings": {"number_of_shards": 1, "number_of_replicas": 0},
              "mappings": {"dynamic": "strict", "properties": {
                  "fragment_id": {"type": "keyword"}, "document_id": {"type": "keyword"}, "path": {"type": "keyword"},
                  "folder": {"type": "keyword"}, "doc_title": TEXT, "section_id": {"type": "keyword"},
                  "section_title": TEXT, "text": TEXT, "start_line": {"type": "integer"}, "end_line": {"type": "integer"},
                  "page_start": {"type": "integer"}, "page_end": {"type": "integer"}, "ordinal": {"type": "integer"},
                  "prev_id": {"type": "keyword"}, "next_id": {"type": "keyword"}, "content_hash": {"type": "keyword"},
                  "embedding": VEC}}}


class CorpusIndex:
    def __init__(self, url: str | None = None, prefix: str | None = None) -> None:
        self.es = Elasticsearch(url or os.getenv("ES_URL", "http://localhost:9200"), request_timeout=30, max_retries=1)
        prefix = prefix or os.getenv("ES_PREFIX", "itmo_lab3")
        self.l1, self.l2 = f"{prefix}_l1", f"{prefix}_l2"

    def ensure(self) -> None:
        for name, mapping in ((self.l1, L1_MAPPING), (self.l2, L2_MAPPING)):
            if not self.es.indices.exists(index=name):
                self.es.indices.create(index=name, **mapping)

    def drop(self) -> None:
        for name in (self.l1, self.l2):
            self.es.indices.delete(index=name, ignore_unavailable=True)

    def refresh(self) -> None:
        self.es.indices.refresh(index=f"{self.l1},{self.l2}")

    # ------------------------------------------------------------------ writes (idempotent by _id)
    def upsert_l1(self, rec: dict, vector: list[float]) -> None:
        structure = "\n".join(f"{s['title']}: {s['summary']}" for s in rec["sections"])
        self.es.index(index=self.l1, id=rec["document_id"], document={
            "document_id": rec["document_id"], "path": rec["path"], "folder": rec["folder"], "title": rec["title"],
            "description": rec["description"], "summary": rec["summary"], "structure": structure,
            "content_hash": rec["content_hash"], "sections": len(rec["sections"]), "embedding": vector})

    def replace_l2(self, document_id: str, frags: list[dict], vectors: list[list[float]]) -> dict:
        keep = {f["fragment_id"] for f in frags}
        existing = set(self.fragment_ids(document_id))
        actions = [{"_op_type": "index", "_index": self.l2, "_id": f["fragment_id"],
                    "_source": {**{k: f[k] for k in L2_MAPPING["mappings"]["properties"] if k in f and k != "embedding"},
                                "embedding": v}} for f, v in zip(frags, vectors)]
        helpers.bulk(self.es, actions, refresh=False)
        stale = sorted(existing - keep)
        if stale:
            helpers.bulk(self.es, [{"_op_type": "delete", "_index": self.l2, "_id": s} for s in stale], raise_on_error=False)
        return {"created": len(keep - existing), "updated": len(keep & existing), "deleted_stale": len(stale)}

    # ------------------------------------------------------------------ reads
    def fragment_ids(self, document_id: str) -> list[str]:
        try:
            res = self.es.search(index=self.l2, query={"term": {"document_id": document_id}}, size=10000, _source=False)
        except NotFoundError:
            return []
        return [h["_id"] for h in res["hits"]["hits"]]

    def counts_by_document(self) -> dict[str, int]:
        res = self.es.search(index=self.l2, size=0, aggs={"d": {"terms": {"field": "document_id", "size": 10000}}})
        return {b["key"]: b["doc_count"] for b in res["aggregations"]["d"]["buckets"]}

    def count(self, level: str) -> int:
        return int(self.es.count(index=self.l1 if level == "L1" else self.l2)["count"])

    @staticmethod
    def _filters(folder: str | None, document_id: str | None, document_ids: list[str] | None = None) -> list[dict]:
        f = []
        if folder:
            f.append({"term": {"folder": folder}})
        if document_id:
            f.append({"term": {"document_id": document_id}})
        if document_ids:
            f.append({"terms": {"document_id": document_ids}})
        return f

    def fulltext(self, level: str, query: str, size: int, offset: int, folder=None, document_id=None) -> dict:
        idx = self.l1 if level == "L1" else self.l2
        fields = (["title^3", "title.std^2", "description^2", "summary", "summary.std", "structure^2", "structure.std"]
                  if level == "L1" else ["text", "text.std", "section_title^2", "doc_title"])
        hl = ({"summary": {}, "structure": {}, "description": {}} if level == "L1" else {"text": {}})
        res = self.es.search(index=idx, from_=offset, size=size, track_total_hits=True,
                             query={"bool": {"must": [{"multi_match": {"query": query, "fields": fields}}],
                                             "filter": self._filters(folder, document_id)}},
                             highlight={"fields": hl, "fragment_size": 160, "number_of_fragments": 2},
                             source_excludes=["embedding"])
        return {"total": res["hits"]["total"]["value"], "hits": res["hits"]["hits"]}

    def knn(self, level: str, vector: list[float], k: int, folder=None, document_id=None) -> list[dict]:
        idx = self.l1 if level == "L1" else self.l2
        knn = {"field": "embedding", "query_vector": vector, "k": k, "num_candidates": max(50, 5 * k)}
        flt = self._filters(folder, document_id)
        if flt:
            knn["filter"] = flt
        res = self.es.search(index=idx, knn=knn, size=k, source_excludes=["embedding"])
        return res["hits"]["hits"]

    def scan_matches(self, query: str, document_ids: list[str], after: list | None, page: int) -> dict:
        """Exhaustive full-text pass: ALL matching fragments of the area, ordered by (path, ordinal), no score."""
        body = {"index": self.l2, "size": page, "track_total_hits": True,
                "query": {"bool": {"filter": [{"terms": {"document_id": document_ids}},
                                              {"multi_match": {"query": query, "fields": ["text", "text.std"]}}]}},
                "sort": [{"path": "asc"}, {"ordinal": "asc"}], "source_excludes": ["embedding"],
                "highlight": {"fields": {"text": {}}, "fragment_size": 160, "number_of_fragments": 3}}
        if after:
            body["search_after"] = after
        res = self.es.search(**body)
        return {"total": res["hits"]["total"]["value"], "hits": res["hits"]["hits"]}
