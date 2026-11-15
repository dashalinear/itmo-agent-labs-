---
name: research_web
description: Autonomous internet research on a user topic without a given URL list - own search queries, relevance selection with reasons, durable URL queue, full-page reading, verified web evidence and a review with unverified items. Use for "разберись в теме", "сам найди источники", "исследуй в интернете".
---
# research_web — procedure

## 1. Queries
- Split the topic into 3-6 aspects; formulate one specific query per aspect (Russian and English variants help). Call `web_search(query)`.
- After reading a few pages, refine: new terms, names, comparisons. A repeated query returns cached results and gives nothing new.

## 2. Selection
- For each result decide with `select_sources(items=[{url, decision: queue|reject, reason}])`.
- Reject duplicates (same page with tracking parameters, mirrors), off-topic pages, binaries, obviously broken links. Prefer primary docs, papers, engineering blogs.
- Queue useful links from fetched pages with `found_on` = the page URL.

## 3. Reading
- `web_fetch(url)` reads the FULL page (the snippet is not evidence). Long pages are offloaded: use `search_artifact` / `read_artifact`.
- Errors (404, timeout, 503 after retries, too many redirects) are recorded in the queue; continue with other sources.
- Watch the HTTP and search budget in the PROGRESS DIGEST; when `*_budget_exhausted` appears, stop fetching; the remaining queue stays saved.

## 4. Evidence and review
- `save_evidence(claim, kind, source=<url>, quote=<exact text from the page>)` for every confirmed statement.
- Sections: approaches, limitations, comparison, unverified (and summary). Compare approaches by their limits.
- The unverified section lists claims seen only in snippets or single sources and the unread queue.

## 5. Stop
- Stop when the criteria in the TASK CONTRACT are met, when no useful new sources appear, or at a limit. `complete_task` or `stop_partial`. Coverage refers to the recorded queue, not the whole internet.
