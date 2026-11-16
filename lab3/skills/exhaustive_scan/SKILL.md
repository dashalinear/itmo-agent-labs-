---
name: exhaustive_scan
description: Complete enumeration in a finite area of the corpus without top-k - fix the area by L0, read every L2 fragment, record every mention, report coverage; partial result and resume on budget limits. Use for "перечисли все", "просмотри все документы папки".
---
# exhaustive_scan — procedure

1. `area_define(folder)` once; the area (documents + all fragments) is then fixed for the task.
2. Loop `area_next(cursor)` until `done=true`; for every batch record each matching mention with `record_mention`.
   Mentions may be phrased without the keyword: read the meaning, not only the word.
3. Literal cross-check: `area_grep` / `area_fulltext` - read every page until `done=true`.
4. `area_coverage`: all documents `done`, fragments_read == fragments_total. Then `submit_result` with the full list.
5. Budget: if limits stop you, `stop_partial(reason, unverified=[remaining ranges])`. A new run continues from
   `area_coverage().resume_cursor`; completeness is claimed only after the whole area is read.
