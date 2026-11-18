---
name: index_excerpts
description: Store useful excerpts of organized documents in Elasticsearch through MCP and search them - choose exact line ranges, build metadata, upsert without duplicates, verify with search, and sync current_path after a file move. Use for "положи/сохрани в Elastic", "проиндексируй", "найди в Elastic", "синхронизируй индекс".
---

# index_excerpts — procedure

## 1. Select documents
- Resolve the request to catalogued documents: `catalog_view(folder=...)` or `catalog_view()`; for "this file" use the document discussed earlier.
- Only documents in data/ with a document_id can be indexed. Empty documents (`is_empty=true`) are skipped with an explanation.

## 2. Choose excerpts by content
- `read(path, numbered=true)` to see line numbers.
- Pick 1-4 informative fragments per document (definitions, decisions, facts, steps). Each fragment is a contiguous 1-based inclusive line range, at most 60 lines, not blank. For a very short file one range may cover the whole text.
- Optionally write a short `summary` per range (it does NOT replace the text).

## 3. Build exact records
- `build_excerpts(document_id, ranges=[{start_line, end_line, summary}, ...])`.
- The tool copies the exact text, content_hash, title, category, current_path and computes the stable `excerpt_id`. Never edit the returned text.
- Re-indexing an unchanged document: `build_excerpts(document_id)` WITHOUT ranges reuses the ranges already stored -> same excerpt_ids -> no duplicates.

## 4. Write through MCP
- `upsert_excerpts(excerpts=<records from build_excerpts>)` (default mode="replace" keeps exactly this set for the document; use mode="merge" only to ADD excerpts to an already indexed document).
- Check the result: `ok=true`, `created`/`updated` counts, per-document `count`. A second run of the same version must show `created=0`.
- `ok=false` (es_unavailable, mcp_unavailable, excerpt_validation_failed): report the failure, do NOT say it was indexed. The catalog marks the document `error`. For validation failures rebuild with build_excerpts.

## 5. Keep paths consistent after moves
- When `move` returns `action_required`, or `catalog_view(status="pending_sync")` lists documents: call `update_document_path(document_id, new_path=<catalog current_path>)` for each (the server sets `category` from the `data/<category>/` folder of the new path). On success - path and category in the index equal the catalog - the catalog switches back to `indexed`; otherwise it stays `pending_sync` - tell the user and retry later.

## 6. Search and verify
- `search_excerpts(query, size=5..10, category=?, path_prefix=?)`. Formulate a lexical query with key terms (Russian morphology is handled by the index analyzer).
- Answer with: excerpt text (quote), `current_path`, lines `start_line-end_line`, `document_id`. If asked, open the source with `read(current_path)` and point to the lines.
- After writing, run one search for a key term of a written excerpt to verify it is findable.
- `get_document_excerpts(document_id)` / `index_stats()` show what is stored (useful to prove "no duplicates").
