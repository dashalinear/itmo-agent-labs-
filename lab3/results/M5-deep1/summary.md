## Цель
Исследовать в корпусе все подходы к хранению состояния (state/memory) агентов. Задачи:
1. Найти ВСЕ документы с описаниями подходов (state, memory, persistence, storage, checkpoint, compaction, context window, etc.) в папках agents, projects, meetings, search, storage, courses. Использовать search_fulltext (L1/L2), search_vector (L1/L2), search_grep.
2. Для каждого подхода: название, document_id, path, краткое описание, ограничения, цитаты из L2 (fragment_id, lines).
3. Определить выбранный подход для проекта DocSearch (поиск в meetings, projects, agents; явные решения/decision; подтверждение цитатами L2).
4. Сравнить подходы по ограничениям (таблица/список).
5. Выписать открытые вопросы (неподтверждённые факты, противоречия).
Формат: структурированный отчёт, каждый факт с document_id, fragment_id, lines. Отдельный раздел "Открытые вопросы".

## Ограничения пользователя
[task_id: M5-deep1 · kind: deep]
Request:
Исследуй в корпусе все подходы к хранению состояния (state/memory) агентов. Задача:

1. Найди ВСЕ документы, где описаны подходы к хранению состояния/памяти агентов (state, memory, persistence, storage, checkpoint, compaction, context window, etc.). Ищи в папках agents, projects, meetings, search, storage, courses — везде, где есть упоминания. Используй search_fulltext (L1 и L2), search_vector (L1 и L2), search_grep для точных терминов (state, memory, persistence, checkpoint, compaction, context, storage, состояние, память, хранение).

2. Для каждого найденного подхода:
   - Как он называется и в каком документе описан (document_id, path)
   - Краткое описание механизма
   - Ограничения (лимиты, потери данных, стоимость, сложность)
   - Цитаты из L2-текста (fragment_id, lines) как подтверждение

3. Определи, какой подход выбран для проекта DocSearch:
   - Ищи в meetings (kickoff, weekly, retro, M-12, M-13), projects, agents
   - Найди явные решения/решения о выборе (decision)
   - Подтверди цитатами из L2

4. Сравни подходы по ограничениям (таблица или список)

5. Выпиши открытые вопросы (open questions) — что в корпусе НЕ подтверждено, что осталось неясным, противоречия между документами.

Формат ответа: структурированный отчёт с разделами, каждый факт — с document_id, fragment_id, lines. Отдельный раздел "Открытые вопросы".

## Решения и договорённости
- План работы: 1) Найти все документы (L1/L2, grep, vector) — done; 2) Прочитать L2-тексты, собрать цитаты — doing (прочитаны основные документы из storage); 3) Найти решение для DocSearch — done (найдено в M-12); 4) Составить сравнение и открытые вопросы, submit_result — todo.
- Корпус: itmo-lab3, 59 документов, 58 с L1, 332 фрагмента.
- Папки: _unsorted (1), agents (11), courses (5), howto (2), meetings (6), ops (7), projects (6), search (11), storage (10).

## Подтверждённые факты (с источниками)
- Корпус itmo-lab3: 59 документов, 58 с L1, 332 фрагмента. (corpus_overview)
- Папка storage содержит 10 документов, 47 фрагментов. (corpus_overview)
- **Redis State** (doc: 72c0b078-4913-4a81-bdeb-794740e421f9, path: data/storage/st_redis_state.md):
  - Механизм: быстрый кэш состояния между шагами агента. (frag: 95c85e6c-d413-51f0-871f-1501376c150d, lines 1-9)
  - Ограничения: Без AOF/RDB состояние теряется при перезапуске; appendfsync everysec допускает потерю последней секунды [L-ST-08]. (frag: cd5c7e49-b02e-50c2-8c6e-e67a2d4685d0, lines 13-21)
  - Ограничения: Весь набор данных должен помещаться в RAM [L-ST-09]; для чекпоинтов используется noeviction [L-ST-10]. (frag: 856ded03-45ee-5db6-82cd-c5abf5597616, lines 23-31)
- **Event Sourcing** (doc: d461