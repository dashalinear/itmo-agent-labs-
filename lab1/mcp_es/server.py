"""MCP server (stdio) exposing excerpt write/update/search over Elasticsearch.

Run standalone:   python -m mcp_es.server
The agent starts it as a subprocess through langchain-mcp-adapters.

Every tool returns {"ok": true, ...} or {"ok": false, "error": {...}} so that
an unavailable Elasticsearch becomes an observation for the agent.
"""
from __future__ import annotations

import logging
import sys
from typing import Literal, Optional

from mcp.server.fastmcp import FastMCP

from .schema import Excerpt
from .store import ExcerptStore

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)  # stdout is the MCP channel
# connection failures are reported to the agent as ok=false; keep the CLI terminal clean
logging.getLogger("elastic_transport").setLevel(logging.CRITICAL)
mcp = FastMCP("elasticsearch-excerpts")
_store: ExcerptStore | None = None


def store() -> ExcerptStore:
    global _store
    if _store is None:
        _store = ExcerptStore()
    return _store


def _fail(e: Exception) -> dict:
    name = type(e).__name__
    kind = "es_unavailable" if "Connection" in name or "Timeout" in name else "es_error"
    return {"ok": False, "error": {"type": kind, "message": f"{name}: {e}"[:800]}}


@mcp.tool()
def upsert_excerpts(excerpts: list[Excerpt], mode: Literal["replace", "merge"] = "replace") -> dict:
    """Write or update excerpts in Elasticsearch, keyed by a stable excerpt_id.

    Each excerpt is an exact fragment of a source file with 1-based inclusive
    line range, document_id, current_path, title, category, content_hash and
    optional summary. Re-sending the same document version and ranges updates
    the same records (no duplicates). mode="replace" (default) also removes
    older excerpts of the same document that are not in this batch;
    mode="merge" only adds/updates.
    """
    try:
        return {"ok": True, **store().upsert(excerpts, mode=mode)}
    except ValueError as e:
        return {"ok": False, "error": {"type": "invalid_excerpt", "message": str(e)}}
    except Exception as e:  # noqa: BLE001 - surfaced to the agent as an observation
        return _fail(e)


@mcp.tool()
def search_excerpts(query: str, size: int = 10, category: Optional[str] = None,
                    document_id: Optional[str] = None, path_prefix: Optional[str] = None) -> dict:
    """Full-text (lexical) search over stored excerpts.

    Returns hits with text, document_id, current_path, title, category,
    start_line, end_line, score and highlights. Optional filters: category,
    document_id, path_prefix (e.g. "data/courses/"). size is capped at 50.
    """
    try:
        return {"ok": True, **store().search(query, size=size, category=category,
                                             document_id=document_id, path_prefix=path_prefix)}
    except Exception as e:  # noqa: BLE001
        return _fail(e)


@mcp.tool()
def update_document_path(document_id: str, new_path: str, category: Optional[str] = None) -> dict:
    """Set current_path (and optionally category) of ALL excerpts of a document.

    Call after the source file was moved, so search results point to the new path.
    """
    try:
        return {"ok": True, **store().update_path(document_id, new_path, category)}
    except Exception as e:  # noqa: BLE001
        return _fail(e)


@mcp.tool()
def get_document_excerpts(document_id: str) -> dict:
    """List all stored excerpts of one document ordered by start_line."""
    try:
        return {"ok": True, **store().get_document(document_id)}
    except Exception as e:  # noqa: BLE001
        return _fail(e)


@mcp.tool()
def delete_document_excerpts(document_id: str) -> dict:
    """Delete all excerpts of one document (use only when the user asks)."""
    try:
        return {"ok": True, **store().delete_document(document_id)}
    except Exception as e:  # noqa: BLE001
        return _fail(e)


@mcp.tool()
def index_stats() -> dict:
    """Index name, total excerpt count and per-document counts/paths."""
    try:
        return {"ok": True, **store().stats()}
    except Exception as e:  # noqa: BLE001
        return _fail(e)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
