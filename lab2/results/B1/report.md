# Частичный результат (задача не завершена): B1

- Статус: **paused** — budget exhausted before the call (model_calls: needed 1, remaining 0 of 70)
- Вид задачи: web

## Запрос

Разберись в подходах к хранению состояния LLM-агентов. Сам найди источники, изучи их, сопоставь ограничения и подготовь обзор с подтверждениями. Работай в пределах бюджета и явно укажи, что осталось непроверенным.

## Ограничения пользователя

- [W1] Прочитать полностью (web_fetch) не меньше 5 реально найденных страниц.
- [W2] Каждое утверждение обзора подтверждено цитатой со страницы (evidence с URL); snippet поисковика подтверждением не считается.
- [W3] Обзор не длиннее 900 слов.
- [W4] Отдельный раздел «Непроверенное»: что не удалось подтвердить и какие источники не прочитаны.
- [W5] Сравнить подходы по их ограничениям.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 0


## Приложение B. Поисковые запросы: 0


## Приложение C. Источники

Найдено: 9, отобрано: 9, прочитано: 6, ошибок: 3, отклонено: 0, не разобрано (found): 0, в очереди (pending): 0.

| статус | URL | обнаружен | причина / ошибка |
|---|---|---|---|
| error | https://langchain-ai.github.io/langgraph/concepts/persistence/ | agent | timeout_retries_exhausted: ConnectTimeout:  |
| error | https://en.wikipedia.org/wiki/Agent-based_software_development | agent | http_error: HTTP 403 |
| done | https://www.langchain.com/langgraph | agent | fetched directly |
| error | https://docs.langchain.com/oss/python/langgraph/persistence | agent | timeout_retries_exhausted: ReadTimeout:  |
| done | https://www.langchain.com/deep-agents | agent | fetched directly |
| done | https://www.anthropic.com/research/building-effective-agents | agent | fetched directly |
| done | https://www.anthropic.com/research/effective-harnesses-for-long-running-agents | agent | fetched directly |
| done | https://www.anthropic.com/research/building-effective-agents | agent | fetched directly |
| done | https://www.anthropic.com/research/effective-context-engineering-for-ai-agents | agent | fetched directly |

## Расход

```json
{
  "tokens": {
    "spent": 925108,
    "reserved": 0,
    "limit": 1200000,
    "of_which": {
      "input_tokens": 873353,
      "output_tokens": 4730,
      "compaction_input_tokens": 36225,
      "compaction_output_tokens": 10800,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 70,
    "limit": 70
  },
  "compaction_calls": 9,
  "failed_calls": 0,
  "tool_calls": {
    "used": 72,
    "limit": 300
  },
  "search_calls": {
    "used": 6,
    "limit": 8
  },
  "http_requests": {
    "used": 18,
    "limit": 40
  },
  "cache_hits": 5,
  "bytes_downloaded": 1143357,
  "active_seconds": {
    "used": 336.4,
    "limit": 5400
  },
  "peak_input_tokens": 17054
}
```
