---
name: navigate_corpus
description: Answer a question from the L0/L1/L2 corpus - choose full-text, grep or vector search on L1 or L2, navigate L0 -> L1 -> L2 and cite exact fragments. Use for questions about the corpus content.
---
# navigate_corpus — procedure

1. Pick the search by the question:
   - exact code, number, name -> `search_grep` (L2 for the place, L1 for documents whose structure/summary mention it);
   - words of the question -> `search_fulltext` (L1 to find documents, L2 to find passages);
   - paraphrase / meaning -> `search_vector` (L1 or L2).
2. From a document hit: `get_l1(document_id)` -> choose the section -> `read_section` / `read_fragment`.
   From a fragment hit: `read_fragment`, `neighbor_fragment` for context, `get_l1` to return to the structure.
3. Multi-hop: if a fragment refers to another document (protocol M-12, runbook RB-09 ...), search that reference and read it.
4. Cite with `cite_fragment(fragment_id, exact quote, claim)`; answer with path, fragment_id, lines.
5. L1 summaries are not evidence. No confirmation in L2 -> say so.
