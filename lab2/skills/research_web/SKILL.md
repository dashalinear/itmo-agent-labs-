---
name: research_web
description: Autonomous internet research on a user topic without a given URL list - own search queries in two rounds, relevance selection with reasons, durable URL queue, full-page reading, following links found on pages, verified web evidence and a review with unverified items. Use for "разберись в теме", "сам найди источники", "исследуй в интернете".
---
# research_web — procedure

## 1. Round 1: broad queries
- Split the topic into 3-6 aspects; formulate one specific query per aspect (Russian and English variants help). Call `web_search(query)`.
- Prefer different source TYPES: official documentation, engineering blogs, Wikipedia / encyclopedic pages, code repositories, papers. Do not fill the queue from one site: aim for at most about half of the read pages from one domain (e.g. not only arxiv.org abstracts).

## 2. Selection
- For each result decide with `select_sources(items=[{url, decision: queue|reject, reason}])`.
- Reject duplicates (same page with tracking parameters, mirrors), off-topic pages, binaries, obviously broken links.

## 3. Reading
- `web_fetch(url)` reads the FULL page (the snippet is not evidence). Find exact quotes with `search_artifact(artifact_id, term)` on the page artifact.
- Errors (404, timeout, 503 after retries, too many redirects) are recorded in the queue; continue with other sources.
- Save evidence right after reading a page: `save_evidence(claim, kind, source=<url>, quote=<exact text from the page>)`.

## 4. Round 2: refine from what you read (required)
- After the first 2-3 pages: write down new terms, names, systems and open questions from them, then run at least one NEW, more specific `web_search` (a repeated query is a cache hit and gives nothing new).
- Follow at least one useful link from the `links` of a fetched page: `select_sources([{url, decision: "queue", reason, found_on: <page URL>}])`, then `web_fetch` it. `links` are ranked by the harness (links to other sites and descriptive links first, site navigation dropped) and are returned again on a cache hit, so never re-fetch a stored page to get them.
- Diversity: if more than about half of the read pages come from one domain, the next sources must come from other domains (sites that time out from your network are recorded as errors; choose others).
- The harness checks this in `complete_task` when the task criteria contain `min_queries_after_reading`, `min_page_links_read`, `max_domain_share`.

## 5. Review
- Sections: approaches, limitations, comparison, unverified (and summary). Compare approaches by their limits; cite [ev_...].
- The unverified section lists claims seen only in snippets or single sources and the unread queue.

## 6. Stop and budget
- Watch the HTTP and search budget in the PROGRESS DIGEST; when `*_budget_exhausted` appears, stop that activity; the remaining queue stays saved.
- Stop when the criteria in the TASK CONTRACT are met, when no useful new sources appear, or at a limit: `complete_task` or `stop_partial`. Coverage refers to the recorded queue, not the whole internet.
