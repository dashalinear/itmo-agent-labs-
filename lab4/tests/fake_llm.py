"""Deterministic stand-ins for the LLM (tests and scripts/dry_run_worker.py). not a demonstration of the model.

The policies decide the next tool calls from the CURRENT message history only, like the real agent does, so they
continue correctly after a worker crash (the harness resumes the checkpointed history). They work in small steps
(at most 5 documents per step) because the harness bounds the total tool output of one step.
Importing this module has no side effects (no environment changes).
"""
from __future__ import annotations

import json
import re
import uuid

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

STEP = 5
DEST = {"checkpoint": "state", "redis": "state", "snapshot": "state", "bm25": "search", "vector": "search",
        "hybrid": "search", "query": "search", "oncall": "ops", "incident": "ops", "monitoring": "ops",
        "meeting": "meetings", "inference": "energy"}


def call(name, args):
    return {"name": name, "args": args, "id": "c_" + uuid.uuid4().hex[:8], "type": "tool_call"}


def ai(*calls):
    return AIMessage(content="", tool_calls=list(calls))


def results(messages, name, ok_only=True):
    out = []
    for m in messages:
        if isinstance(m, ToolMessage) and m.name == name:
            try:
                r = json.loads(m.content)
            except Exception:  # noqa: BLE001
                continue
            if not ok_only or (r.get("ok") and isinstance(r.get("data"), dict)):
                out.append(r)
    return out


def last_tool(messages):
    for m in reversed(messages):
        if m.type == "human":
            return {}
        if isinstance(m, ToolMessage):
            try:
                return json.loads(m.content)
            except Exception:  # noqa: BLE001
                return {"ok": False}
    return {}


# ------------------------------------------------------------------------------------------ ingestion agent
def ingest_policy(messages):
    t = last_tool(messages)
    tool, data = t.get("tool"), t.get("data") if isinstance(t.get("data"), dict) else None
    if not t:
        human = next(str(m.content) for m in messages if m.type == "human")
        return ai(call("ls", {"path": re.search(r"папки (\S+) в data", human).group(1)}))
    if tool == "complete_task" and t.get("ok"):
        return AIMessage(content="Загрузка завершена.")
    if data is None:                               # an error result (e.g. the batch folder is already empty after a crash)
        return ai(call("corpus_status", {})) if tool != "corpus_status" else AIMessage(content="Не удалось продолжить.")
    if tool == "ls":
        files = [e["name"] for e in data["entries"] if e["type"] == "file"]
        moves = [call("move", {"source": f"{data['path']}/{n}",
                               "destination": f"data/{next((v for k, v in DEST.items() if k in n), 'misc')}/{n}"}) for n in files]
        return ai(*moves) if moves else ai(call("corpus_status", {}))
    if tool in ("move", "index_document", "write_l1", "doc_outline"):
        if tool == "doc_outline":
            outs = [r["data"] for r in results(messages, "doc_outline")]
            done = {r["data"]["document_id"] for r in results(messages, "write_l1")}
            todo = [o for o in outs if o["document_id"] not in done and o.get("status") == "ready"]
            if todo:
                return ai(*[call("write_l1", {"document_id": o["document_id"], "title": o["headings"][0]["title"],
                                              "description": "Документ стенда.", "summary": "Агент прочитал документ: " + o["path"],
                                              "sections": o["suggested_sections"]}) for o in todo[:STEP]])
        if tool == "write_l1":
            done = {r["data"]["document_id"] for r in results(messages, "index_document")}
            todo = [r["data"]["document_id"] for r in results(messages, "write_l1") if r["data"]["document_id"] not in done]
            if todo:
                return ai(*[call("index_document", {"document_id": i}) for i in todo[:STEP]])
        return ai(call("corpus_status", {}))
    if tool == "corpus_status":
        if not data["need_l1"] and not data["need_l2"]:
            return ai(call("validate_corpus", {}))
        need = data["need_l1"] or data["need_l2"]
        return ai(*[call("doc_outline", {"path": p}) for p in need[:STEP]])
    if tool == "validate_corpus":
        return ai(call("complete_task", {"note": "файлы добавлены"}))
    return AIMessage(content="Загрузка завершена.")


# ------------------------------------------------------------------------------------------ topic agent
def _strip(text):
    lines = [re.sub(r"^\s*\d+\s*[:|]\s?", "", x) for x in text.split("\n")]
    return next((x.strip() for x in lines if len(x.strip()) >= 12 and not x.startswith("#")), lines[0].strip())


def topics_policy(messages):
    t = last_tool(messages)
    if not t:
        return ai(call("frozen_documents", {}))
    if (t.get("tool") == "submit_report" and t.get("ok")) or t.get("tool") == "stop_partial":
        return AIMessage(content="Отчёт готов." if t.get("tool") == "submit_report" else "Остановлено: частичный результат.")
    pages = results(messages, "frozen_documents")
    if not pages:
        return ai(call("frozen_documents", {}))
    if pages[-1]["data"]["next_cursor"] and t.get("tool") == "frozen_documents":
        return ai(call("frozen_documents", {"cursor": pages[-1]["data"]["next_cursor"]}))
    docs = {d["document_id"]: d for p in pages for d in p["data"]["documents"]}
    l1 = {r["data"]["document_id"]: r["data"] for r in results(messages, "examine_document") if "sections" in r["data"]}
    todo = [i for i in docs if i not in l1]
    if todo:
        return ai(*[call("examine_document", {"document_id": i}) for i in todo[:STEP]])
    frags = {r["data"]["document_id"]: r["data"] for r in results(messages, "read_fragment")}
    to_read = [i for i in l1 if i not in frags]
    if to_read:
        return ai(*[call("read_fragment", {"fragment_id": next(s["first_fragment_id"] for s in l1[i]["sections"]
                                                               if s["first_fragment_id"])}) for i in to_read[:STEP]])
    ev = {r["data"]["document_id"]: r["data"]["evidence_id"] for r in results(messages, "cite_fragment")}
    to_cite = [i for i in frags if i not in ev]
    if to_cite:
        return ai(*[call("cite_fragment", {"fragment_id": frags[i]["fragment_id"], "quote": _strip(frags[i]["text"]),
                                           "claim": "тема документа"}) for i in to_cite[:STEP]])
    if not results(messages, "topic_upsert") and t.get("tool") != "topics_status":
        groups = {}
        for i, d in l1.items():
            groups.setdefault(d["path"].split("/")[1], []).append(i)
        return ai(*[call("topic_upsert", {"title": f"Тема {f}", "description": f"Документы папки {f}.", "document_ids": ids,
                                          "evidence_ids": [ev[i] for i in ids if i in ev]}) for f, ids in sorted(groups.items())])
    if t.get("tool") != "topics_status":
        return ai(call("topics_status", {}))
    data = t.get("data") or {}
    if data.get("complete"):
        return ai(call("submit_report", {"summary": "Корпус про хранение состояния, поиск и эксплуатацию."}))
    return ai(call("stop_partial", {"reason": "gaps: " + json.dumps(data, ensure_ascii=False)[:200]}))


class Router(BaseChatModel):
    """Dispatches to a policy by the agent's system prompt."""
    policies: dict = Field(default_factory=dict)
    calls: dict = Field(default_factory=dict)

    @property
    def _llm_type(self):
        return "router"

    def bind_tools(self, tools, **kw):
        return self

    def _kind(self, messages):
        s = str(messages[0].content) if messages and isinstance(messages[0], SystemMessage) else ""
        for k, marker in (("topics", "topic-analysis agent"), ("ingest", "Corpus ingestion")):
            if marker in s:
                return k
        return "other"

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        k = self._kind(messages)
        self.calls[k] = self.calls.get(k, 0) + 1
        return ChatResult(generations=[ChatGeneration(message=self.policies[k](messages))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kw):
        return self._generate(messages)


def router() -> Router:
    return Router(policies={"ingest": ingest_policy, "topics": topics_policy})
