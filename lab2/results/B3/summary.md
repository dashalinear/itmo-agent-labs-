## Цель
Исследовать подходы к хранению состояния LLM-агентов, найти источники, сопоставить ограничения и подготовить обзор с подтверждениями (evidence).

## Ограничения пользователя
- [W1] Прочитать полностью (web_fetch) не меньше 5 реально найденных страниц.
- [W2] Каждое утверждение обзора подтверждено цитатой со страницы (evidence с URL); snippet поисковика подтверждением не считается.
- [W3] Обзор не длиннее 900 слов.
- [W4] Отдельный раздел «Непроверенное»: что не удалось подтвердить и какие источники не прочитаны.
- [W5] Сравнить подходы по их ограничениям.
- [W6] Минимум два раунда: после чтения первых страниц уточнить запросы и пройти хотя бы по одной ссылке, найденной на прочитанной странице; не больше 60 % прочитанных страниц с одного домена.

## Решения и договорённости
- Использовать навык `research_web` для автономного поиска.
- Выполнить минимум 3 поисковых запроса в первом раунде.
- После чтения первых страниц выполнить уточняющие запросы и перейти по ссылке с прочитанной страницы.
- Собирать минимум 6 подтверждений (evidence) с URL.
- Включить в отчет секции: approaches, limitations, comparison, unverified.
- Ограничить долю одного домена до 60% прочитанных страниц.
- Бюджет поисковых запросов исчерпан (10/10 использовано). Дальнейший поиск невозможен.
- Статус задачи: `complete_task` отклонен из-за несоответствия критериям. Необходимо дочитать 1 страницу (итого 5) и пройти по 1 ссылке, найденной на прочитанной странице.
- Выбраны для чтения: `https://github.com/vaibhav7087/agent-state-checkpoints` (pending), `https://vinithavn.medium.com/advancing-agentic-memory-an-overview-of-modern-memory-management-architectures-in-llm-agents-8df87b0da58f` (error: already processed).

## Подтверждённые факты (с источниками)
1. **Agent Harness**: "structure surrounding a large language model (LLM) that enables it to operate as an AI agent. It manages tool use, memory, state persistence, execution environments and feedback loops, as opposed to the model's internal reasoning."
   - Источник: https://en.wikipedia.org/wiki/Agent_harness (artifact_id: `art_07adcaf63baf6a6afe1b`, строка 149).
2. **Stateless Model**: "Because a \"base model\" LLM is a stateless probabilistic function that maps input token sequences to output token sequences, the harness is what makes an agent out of a model..."
   - Источник: https://en.wikipedia.org/wiki/Agent_harness (artifact_id: `art_07adcaf63baf6a6afe1b`, строка 151).
3. **Harness Components**: "Components commonly grouped under a harness include tool dispatch, memory and state persistence, an isolated sandbox or workspace, context management, and guardrails..."
   - Источник: https://en.wikipedia.org/wiki/Agent_harness (artifact_id: `art_07adcaf63baf6a6afe1b`, строка 159).
4. **Agent File Format**: "Agent File (.af) is an open standard file format for serializing stateful AI agents. Originally designed for the Letta framework, Agent File provides a portable way to share agents with persistent memory and behavior."
   - Источник: https://github.com/letta-ai/agent-file (artifact_id: `art_bfc1d6f64cd670bb0db9`, строка 294).
5. **Agent File Contents**: "Agent Files package all components of a stateful agent: system prompts, editable memory (personality and user information), tool configurations (code and schemas), and LLM se..."
   - Источник: https://github.com/letta-ai/agent-file (artifact_id: `art_bfc1d6f64cd670bb0db9`, строка 296).
6. **Portability Goal**: "Agent File addresses the need for a standard that enables: 🔄 Portability: Move agents between systems or deploy them to new environments"
   - Источник: https://github.com/letta-ai/agent-file (artifact_id: `art_bfc1d6f64cd670bb0db9`, строки 423-425).
7. **Agent File State Components**: "A .af file contains all the state required to re-create the exact same agent: Model configuration, Message history, System prompt, Memory blocks..."
   - Источник: https://github.com/letta-ai/agent-file (artifact_id: `art_bfc1d6f64cd670bb0db9`, строки 435-450).
8. **LangGraph Checkpoint SAP**: "A free, open-source LangGraph's checkpoint saver that uses SAP Agent Memory Service. It enables state persistence backed by SAP Agent Memory Service for durable, long-running workflows and agents."
   - Источник: https://github.com/S
## Сжато правилами (без LLM)
- вызов web_fetch({"url": "https://github.com/letta-ai/agent-file", "force": true})
  → web_fetch: ok art_bfc1d6f64cd670bb0db9 {"ok": true, "tool": "web_fetch", "data": {"url": "https://github.com/letta-ai/agent-file", "url_key": "aecc93f673ce3ad7", "final_url": "htt
- вызов search_artifact({"artifact_id": "art_bfc1d6f64cd670bb0db9", "query": "state", "max_hits": 30})
  → search_artifact: ok art_bfc1d6f64cd670bb0db9 {"ok": true, "tool": "search_artifact", "data": {"artifact_id": "art_bfc1d6f64cd670bb0db9", "query": "state", "hits": [{"line": 4, "snippet"
- вызов read_artifact({"artifact_id": "art_bfc1d6f64cd670bb0db9", "start_line": 290, "max_lines": 60})
  → read_artifact: ok art_bfc1d6f64cd670bb0db9 {"ok": true, "tool": "read_artifact", "data": {"artifact_id": "art_bfc1d6f64cd670bb0db9", "start_line": 290, "end_line": 349, "total_lines":
- вызов read_artifact({"artifact_id": "art_bfc1d6f64cd670bb0db9", "start_line": 420, "max_lines": 60})
  → read_artifact: ok art_bfc1d6f64cd670bb0db9 {"ok": true, "tool": "read_artifact", "data": {"artifact_id": "art_bfc1d6f64cd670bb0db9", "start_line": 420, "end_line": 479, "total_lines":
- вызов save_evidence({"claim":
…(правила: хвост обрезан)…
d_artifact: ok art_daa2bb99f1925e6d6296 {"ok": true, "tool": "read_artifact", "data": {"artifact_id": "art_daa2bb99f1925e6d6296", "start_line": 195, "end_line": 264, "total_lines":