---
name: organize_files
description: Organize files from input/ into topic folders in data/ - inspect contents, choose or apply a folder structure, resolve ambiguous documents and name conflicts, move files and keep the catalog up to date. Use for "разложи/разбери файлы", "перенеси файл", "что лежит в папке", new files in input.
---

# organize_files — procedure

## 1. Inspect the current state (always fresh)
1. `ls input` and `ls` every subfolder of input recursively. Earlier listings are stale: the user may have added files during the session.
2. `ls data` (and its folders) to see the existing structure; `catalog_view` to see catalogued documents.
3. Only files still in input/ need organizing. Never copy or re-create files that are already in data/.

## 2. Read contents
- For each file in input/: `read` it (or `head` with n=40 for long files). Classify by CONTENT, not by name.
- Size 0 bytes -> empty file: do not read further; plan `data/_unsorted/`.

## 3. Choose the structure
- If the user named folders, use them exactly.
- Otherwise derive 3+ topics from contents; short lowercase English folder names, e.g. `courses`, `projects`, `meetings`, `howto`. Reuse existing data/ folders when they fit.
- Ambiguous document (fits 2+ topics): either ask the user ONE clarifying question before moving it, or choose the dominant topic and state the reason. Record the rationale: `catalog_update(document_id, note="ambiguous: ... chose X because ...")`.

## 4. Move
- `move(source="input/...", destination="data/<topic>/<file>")`, one call per file.
- `name_conflict` -> never overwrite. Use `suggested_name` (e.g. `notes_2.md`) or a descriptive name derived from the content (e.g. `notes_team_sync.md`), then retry.
- `duplicate_of` warning -> report it to the user; do not delete anything.
- If the move result contains `action_required` (document already indexed) -> call MCP `update_document_path(document_id, new_path)` right away (see skill index_excerpts §5).
- Moving between data folders works the same way: `move("data/a/x.md", "data/b/x.md")`. document_id and content stay the same.

## 5. Describe
- For each moved document: `catalog_update(document_id, title=..., description="1 sentence about the content")`.
- Optionally `write("data/<topic>/_about.md", ...)` with a short folder description (new artifact, not a document copy).

## 6. Report
- Table/list: old path -> new path, topic, one-line reason. Mention empty files, ambiguity decisions, conflicts and duplicates.
- To answer "what is in folder X": `ls data/X` (shows titles/descriptions from the catalog) and summarize.
