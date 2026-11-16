---
name: build_corpus_levels
description: Add organized documents to the L0/L1/L2 corpus - outline, read, write the L1 structure and summary, index L2 fragments and embeddings in Elasticsearch, validate. Use for "добавь в корпус", "проиндексируй папку", "построй L0/L1/L2", ingestion of new files.
---
# build_corpus_levels — procedure

## 1. Organize first
- Every file of the input folder goes to `data/<topic>/` with `move` (lab 1 rules: by content, no overwrite, empty -> `data/_unsorted/`).
- Only documents in `data/` with a document_id can get L1/L2.

## 2. What is missing
- `corpus_status` lists documents without L1 or with stale L2. Do not redo documents that are current (idempotent re-run).

## 3. L1 per document (your decisions)
- `doc_outline(path)`: lines, headings, suggested_sections. A file WITHOUT headings needs your own sections by line ranges.
- Read the text: `read_lines` for small files; `read_pages` / `search_text` for large ones (never the whole file in one call).
- `write_l1(document_id, title, description, summary, sections)`:
  - summary: 3-6 sentences in Russian from the content: purpose, key facts, codes (ERR_..., INC-..., РС-...), numbers, links to other documents;
  - sections: ordered, non-overlapping, together they cover every non-blank line; 1-2 sentence summary each;
  - if the harness answers `invalid_structure`, fix the ranges as reported.

## 4. L2
- `index_document(document_id)` cuts deterministic fragments inside your sections, embeds them and upserts into Elasticsearch.
  Re-running on an unchanged document returns `unchanged` - no duplicates.

## 5. Finish
- `validate_corpus` must return ok=true; then `complete_task(note)` with counts (documents, fragments).
