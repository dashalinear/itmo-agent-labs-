"""Excerpt data contract shared by the MCP server and the agent side.

Excerpt record (one Elasticsearch document, _id = excerpt_id):
  excerpt_id    stable key: uuid5(document_id | content_hash | start_line | end_line)
  document_id   -> catalog.json documents[].document_id
  current_path  workspace-relative path of the source file (updated after moves)
  title, category, summary (optional, does not replace text)
  text          exact source text of lines start_line..end_line (inclusive, 1-based)
  start_line, end_line
  content_hash  sha256 of the whole source file version
  indexed_at    ISO-8601 UTC
"""
from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field

EXCERPT_NAMESPACE = uuid.UUID("6f1c2a9e-1d6b-4c43-9a43-3f3f1b7a1c01")


def excerpt_key(document_id: str, content_hash: str, start_line: int, end_line: int) -> str:
    return str(uuid.uuid5(EXCERPT_NAMESPACE, f"{document_id}|{content_hash}|{start_line}|{end_line}"))


class Excerpt(BaseModel):
    excerpt_id: Optional[str] = Field(None, description="Stable key; recomputed by the server if omitted or inconsistent")
    document_id: str
    current_path: str
    title: str
    category: Optional[str] = None
    text: str = Field(description="Exact source text of the line range")
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    content_hash: str
    summary: Optional[str] = Field(None, description="Optional short description; does not replace text")
    indexed_at: Optional[str] = None


INDEX_MAPPING = {
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "excerpt_id": {"type": "keyword"},
            "document_id": {"type": "keyword"},
            "current_path": {"type": "keyword"},
            "title": {"type": "text", "analyzer": "russian", "fields": {"std": {"type": "text", "analyzer": "standard"}, "raw": {"type": "keyword"}}},
            "category": {"type": "keyword"},
            "text": {"type": "text", "analyzer": "russian", "fields": {"std": {"type": "text", "analyzer": "standard"}}},
            "summary": {"type": "text", "analyzer": "russian"},
            "start_line": {"type": "integer"},
            "end_line": {"type": "integer"},
            "content_hash": {"type": "keyword"},
            "indexed_at": {"type": "date"},
        },
    },
}
