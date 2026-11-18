# Итоговый отчёт: ING1

- Статус: **completed** — Все файлы из input перенесены в data по темам (9 папок, 53 документа). L1 написан для 52 документов (пустой empty_todo.txt не индексируется), L2: 316 фрагментов в Elasticsearch. validate_corpus: ok, 0 проблем.
- Вид задачи: ingest

## Запрос

Организуй все файлы из папки input в data по темам (по содержимому, тематические подпапки входа сохраняй, если они подходят) и добавь документы в корпус: каталог L0, структура и summary L1, тексты L2 в Elasticsearch. Неизменённые документы, уже имеющие актуальные L1/L2, не обрабатывай повторно. В конце проверь корпус validate_corpus и заверши задачу.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 0


## Приложение D. Покрытие локальных документов

| документ | страниц | прочитано стр. | поиск |
|---|---|---|---|
| data/agents/agent_evaluation.md | 1 | 0 | нет |
| data/agents/agents_seminar_react.txt | 1 | 0 | нет |
| data/agents/compaction_hooks.md | 1 | 0 | нет |
| data/agents/guardrails.md | 1 | 0 | нет |
| data/agents/planning.md | 1 | 0 | нет |
| data/agents/prompt_injection.md | 1 | 0 | нет |
| data/agents/react_pattern.md | 1 | 0 | нет |
| data/agents/subagents.md | 1 | 0 | нет |
| data/agents/tool_design.md | 1 | 0 | нет |
| data/courses/es_seminar_analyzers_shards.md | 1 | 0 | нет |
| data/courses/ir_course_lectures.md | 90 | 0 | да |
| data/courses/ir_lecture_inverted_index.md | 1 | 0 | нет |
| data/courses/mlops_lecture_monitoring.md | 1 | 0 | нет |
| data/courses/vector_search_lecture.md | 1 | 0 | нет |
| data/howto/howto_elasticsearch_docker.md | 1 | 0 | нет |
| data/howto/howto_git_branches.txt | 1 | 0 | нет |
| data/meetings/call_transcript_2026-11-05.txt | 1 | 0 | нет |
| data/meetings/meeting_2026-10-02_kickoff.md | 1 | 0 | нет |
| data/meetings/meeting_2026-10-09_weekly.md | 1 | 0 | нет |
| data/meetings/meeting_2026-11-10_retro.md | 1 | 0 | нет |
| data/meetings/meeting_m12.md | 1 | 0 | нет |
| data/meetings/meeting_m13.md | 1 | 0 | нет |
| data/ops/incident_031.md | 1 | 0 | нет |
| data/ops/incident_034.md | 1 | 0 | нет |
| data/ops/monitoring.md | 1 | 0 | нет |
| data/ops/oncall_policy.md | 1 | 0 | нет |
| data/ops/runbook_rb09.md | 1 | 0 | нет |
| data/search/analyzers_ru.md | 1 | 0 | нет |
| data/search/bm25_ranking.md | 1 | 0 | нет |
| data/search/hybrid_search.md | 1 | 0 | нет |
| data/search/query_cache.md | 1 | 0 | нет |
| data/search/query_suggest.md | 1 | 0 | нет |
| data/search/search_quality.md | 1 | 0 | нет |
| data/search/search_quality_thoughts.md | 1 | 0 | нет |
| data/search/sharding.md | 1 | 0 | нет |
| data/search/vector_knn.md | 1 | 0 | нет |
| data/storage/st_snapshot_policy.md | 1 | 0 | нет |
| input/lectures/ir_course_lectures.md | 90 | 0 | нет |

## Критерии завершения

- OK input_folder_organized: 0 files left
- OK l1_l2_complete: need L1: 0, need L2: 0
- OK validation: 316 fragments checked, 0 problems

## Расход

```json
{
  "tokens": {
    "spent": 3970097,
    "reserved": 0,
    "limit": 8000000,
    "of_which": {
      "input_tokens": 3267192,
      "output_tokens": 82384,
      "compaction_input_tokens": 411068,
      "compaction_output_tokens": 64800,
      "failed_call_tokens": 144653,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 242,
    "limit": 450
  },
  "compaction_calls": 54,
  "failed_calls": 10,
  "tool_calls": {
    "used": 995,
    "limit": 2000
  },
  "search_calls": {
    "used": 0,
    "limit": 12
  },
  "http_requests": {
    "used": 0,
    "limit": 80
  },
  "cache_hits": 0,
  "bytes_downloaded": 0,
  "active_seconds": {
    "used": 1578.2,
    "limit": 10800
  },
  "peak_input_tokens": 17673
}
```
