# HTTP-примеры web harness (без LLM, scripts/http_examples.py)

| случай | ожидание | результат | HTTP-запросов учтено | попыток | редиректов | cache hit | статус в очереди |
|---|---|---|---|---|---|---|---|
| page | ok, 1 request | ok | 1 | 1 | 0 | нет | done |
| duplicate (utm) | same canonical URL -> cache hit, 0 requests | ok | 0 | None | 0 | да | done |
| duplicate (trailing slash) | cache hit, 0 requests | ok | 0 | None | 0 | да | done |
| redirect chain 301 -> 302 -> 200 | ok, 3 requests (each hop counted) | ok | 3 | 3 | 2 | нет | done |
| redirect loop | too_many_redirects after max_redirects+1 hops | too_many_redirects | 6 | 6 | 6 | нет | error |
| 404 | http_error, no retry, 1 request | http_error | 1 | 1 | 0 | нет | error |
| flaky 503 x2 | ok after 2 retries with backoff, 3 requests | ok | 3 | 3 | 0 | нет | done |
| always 503 | http_error_retries_exhausted, 3 requests | http_error_retries_exhausted | 3 | 3 | 0 | нет | error |
| slow (6 s > timeout 2 s) | timeout_retries_exhausted, 3 requests | timeout_retries_exhausted | 3 | 3 | 0 | нет | error |
| large 3 MB | ok, download capped at max_fetch_bytes; downloaded text in an artifact, model gets a bounded preview | ok (усечено) | 1 | 1 | 0 | нет | done |
| binary | unsupported_content_type | unsupported_content_type | 1 | 1 | 0 | нет | error |
| budget: 1st page | ok | ok | 1 | 1 | 0 | нет | done |
| budget: redirect chain | 2 of 3 hops fit, then http_budget_exhausted; URL stays pending | http_budget_exhausted | 2 | 2 | 2 | нет | pending |
| budget: cache hit after exhaustion | cache hit works without network budget | ok | 0 | None | 0 | да | done |

Итого H1: HTTP-запросов 22, cache hits 2, байт 315048.
Итого H2 (max_http_requests=3): HTTP-запросов 3, cache hits 1.
