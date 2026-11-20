"""Agents end to end with deterministic policies instead of the LLM (offline; real ES and embeddings).

Covers: exhaustive full pass with coverage registry (all 36 mentions, incl. those without the keyword),
deterministic replay of a batch, partial by budget + resume from the same boundary, non-blocking deep
sub-agent (task_id first, main answers meanwhile, result with evidence later), budget chained to the
parent, ingestion agent (organize + L1 + L2 + validate) and an idempotent re-run.
"""
import asyncio
import json
import re
import shutil
import uuid
from dataclasses import replace

import pytest
from conftest import LAB3
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from navsearch.agents import Lab3
from navsearch.cli import ensure_task, INGEST_REQUEST
from harness.runner import TaskRunner

MAN = json.loads((LAB3 / "corpus" / "manifest.json").read_text(encoding="utf-8"))


def call(name, args):
    return {"name": name, "args": args, "id": "c_" + uuid.uuid4().hex[:8], "type": "tool_call"}


def last_tool(messages):
    """Last tool result AFTER the latest user message (earlier turns do not drive the policy)."""
    for m in reversed(messages):
        if m.type == "human":
            return {}
        if isinstance(m, ToolMessage):
            try:
                return json.loads(m.content)
            except Exception:  # noqa: BLE001
                return {}
    return {}


class Router(BaseChatModel):
    """Dispatches to a policy by the agent's system prompt; each policy: messages -> AIMessage."""
    policies: dict = Field(default_factory=dict)
    delay: float = 0.0
    calls: dict = Field(default_factory=dict)

    @property
    def _llm_type(self):
        return "router"

    def bind_tools(self, tools, **kw):
        return self

    def _kind(self, messages):
        s = str(messages[0].content) if messages and isinstance(messages[0], SystemMessage) else ""
        for k, marker in (("deep", "DEEP SEARCH sub-agent"), ("exhaustive", "EXHAUSTIVE SEARCH sub-agent"),
                          ("main", "MAIN ASSISTANT"), ("ingest", "Corpus ingestion")):
            if marker in s:
                return k
        return "other"

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        k = self._kind(messages)
        self.calls[k] = self.calls.get(k, 0) + 1
        return ChatResult(generations=[ChatGeneration(message=self.policies[k](messages))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kw):
        if self.delay:
            await asyncio.sleep(self.delay)
        return self._generate(messages)


def exhaustive_policy(messages):
    t = last_tool(messages)
    d = t.get("data", {})
    if not t:
        return AIMessage(content="", tool_calls=[call("area_define", {"folder": "storage"})])
    if t.get("tool") == "area_define":
        return AIMessage(content="", tool_calls=[call("area_next", {})])
    if t.get("tool") == "area_next":
        calls = []
        for f in d.get("fragments", []):
            for line in f["text"].split("\n"):
                txt = line.split(": ", 1)[1] if ": " in line else line
                for code in re.findall(r"\[L-ST-\d\d\]", txt):
                    calls.append(call("record_mention", {"fragment_id": f["fragment_id"], "quote": txt[-120:], "note": code}))
        if d.get("done"):
            return AIMessage(content="", tool_calls=[call("area_coverage", {})])
        if calls:            # first record the mentions of this batch, then ask for the next one
            return AIMessage(content="", tool_calls=calls)
        return AIMessage(content="", tool_calls=[call("area_next", {"cursor": d["next_cursor"]})])
    if t.get("tool") == "record_mention":
        # continue after the batch these mentions came from
        for m in reversed(messages):
            if isinstance(m, ToolMessage) and m.name == "area_next":
                return AIMessage(content="", tool_calls=[call("area_next", {"cursor": json.loads(m.content)["data"]["next_cursor"]})])
        return AIMessage(content="", tool_calls=[call("area_next", {})])
    if t.get("tool") == "area_coverage":
        if d.get("complete"):
            return AIMessage(content="", tool_calls=[call("submit_result", {"findings": f"Найдено упоминаний: {d['mentions_recorded']}",
                                                                             "open_questions": []})])
        return AIMessage(content="", tool_calls=[call("area_next", {"cursor": d["resume_cursor"]})])
    return AIMessage(content="Готово: полный обход завершён.")


async def run_exhaustive(cfg, tid, overrides=None, label="exhaustive"):
    app = Lab3(cfg, *_shared())
    async with TaskRunner(cfg, verbose=False, use_mcp=False, model=Router(policies={"exhaustive": exhaustive_policy}),
                          summarizer=None) as r:
        app.runner = r
        ensure_task(r, "PARENT", "main", "parent", {}, {})
        if not r.store.get_task(tid):
            from harness.config import Limits
            from navsearch.subagents import DEFAULT_SUB_LIMITS
            lim = Limits.from_dict(r.store.get_task("PARENT")["limits"]).updated(DEFAULT_SUB_LIMITS["exhaustive"]).updated(overrides or {})
            r.store.create_task(tid, "exhaustive", "Перечисли все ограничения в папке storage с источниками.",
                                {"task_id": tid, "kind": "exhaustive", "parent_task_id": "PARENT", "root": str(cfg.root),
                                 "scope": [], "constraints": [], "criteria": {}}, lim.to_dict())
        res = await r.run(tid, label=label, agent_factory=app.factory("exhaustive"),
                          overrides=overrides if label == "resume" else None)
        return res, app.coverage_of(tid), {e["evidence_id"]: e for e in r.store.evidence(tid)}, r.store.get_task(tid)


_SHARED = {}


def _shared():
    return _SHARED["index"], _SHARED["embedder"]


@pytest.fixture(autouse=True)
def shared(corpus, embedder):
    _SHARED.update(index=corpus["index"], embedder=embedder)


async def test_exhaustive_reads_whole_area_and_finds_all_mentions(cfg):
    res, cov, ev, task = await run_exhaustive(cfg, "EX-full")
    assert task["status"] == "completed", task["status_reason"]
    assert cov["complete"] and cov["fragments_read"] == cov["fragments_total"] and cov["documents_done"] == 10
    codes = {re.search(r"L-ST-\d\d", e["claim"]).group(0) for e in ev.values()}
    assert codes == {m["code"] for m in MAN["exhaustive_area"]["mentions"]}          # all 36, incl. 10 without the keyword


async def test_exhaustive_partial_then_resume_from_the_same_boundary(cfg):
    res, cov1, ev1, t1 = await run_exhaustive(cfg, "EX-part", overrides={"max_model_calls": 6})
    assert t1["status"] == "paused" and "budget" in t1["status_reason"]
    assert 0 < cov1["fragments_read"] < cov1["fragments_total"] and not cov1["complete"]
    res2, cov2, ev2, t2 = await run_exhaustive(cfg, "EX-part", overrides={"max_model_calls": 200}, label="resume")
    assert t2["status"] == "completed" and cov2["complete"]
    assert set(ev1) <= set(ev2) and len(ev2) == 36                                    # nothing lost, nothing duplicated


async def test_area_next_is_deterministic_per_cursor(cfg, env):
    from navsearch.tools_corpus import CorpusTools
    from navsearch.tools_exhaustive import ExhaustiveTools
    ex = ExhaustiveTools(env, CorpusTools(env))
    ex.area_define(folder="storage")
    b1 = ex.area_next()
    b2 = ex.area_next(b1["next_cursor"])
    replay = ex.area_next(b1["next_cursor"])                    # a tools step replayed after a crash
    assert [f["fragment_id"] for f in b2["fragments"]] == [f["fragment_id"] for f in replay["fragments"]]
    assert ex.area_coverage()["fragments_read"] == len(b1["fragments"])      # b2 not acknowledged yet
    again = ex.area_next()                                       # resume without cursor: b2 is delivered again
    assert [f["fragment_id"] for f in again["fragments"]] == [f["fragment_id"] for f in b2["fragments"]]
    cov = ex.area_coverage()                                     # the registry's resume_cursor is accepted by area_next
    via_registry = ex.area_next(cov["resume_cursor"])
    assert [f["fragment_id"] for f in via_registry["fragments"]] == [f["fragment_id"] for f in b2["fragments"]]
    assert ex.area_coverage()["fragments_read"] == len(b1["fragments"])      # nothing acknowledged by it
    assert all(len(d["unread_lines"]) <= d["fragments"] for d in cov["documents"])
    g = ex.area_grep("Ограничение")
    assert not g["done"] and g["next_cursor"]                   # more than one page; agent must read on
    ft = ex.area_fulltext("ограничение")
    assert ft["order"].startswith("path") and ft["total"] >= 15 and not ft["done"]   # fragments with the word, >1 page


def main_policy(messages):
    t = last_tool(messages)
    human = [m for m in messages if m.type == "human"][-1].content
    if "исследуй" in human.lower() and t.get("tool") != "start_subagent":
        return AIMessage(content="", tool_calls=[call("start_subagent", {
            "kind": "deep", "request": "Выясни, какой бэкенд чекпоинтов выбран для DocSearch, кто ответственный, и приведи подтверждения."})])
    if t.get("tool") == "start_subagent":
        return AIMessage(content=f"Запустила исследование, task_id {t['data']['task_id']}.")
    if "сколько" in human.lower() and t.get("tool") != "list_documents":
        return AIMessage(content="", tool_calls=[call("list_documents", {"folder": "storage"})])
    if t.get("tool") == "list_documents":
        return AIMessage(content=f"В папке storage {t['data']['documents_total']} документов.")
    if "результат" in human.lower() and t.get("tool") != "subagent_result":
        tid = re.search(r"(M-T-deep\d+)", " ".join(str(m.content) for m in messages)).group(1)
        return AIMessage(content="", tool_calls=[call("subagent_result", {"task_id": tid, "wait_seconds": 60})])
    if t.get("tool") == "subagent_result":
        return AIMessage(content="Итог: " + str(t["data"].get("findings")))
    return AIMessage(content="ok")


def deep_policy(messages):
    t = last_tool(messages)
    if not t:
        return AIMessage(content="", tool_calls=[call("search_grep", {"pattern": "РС-7", "level": "L2"})])
    if t.get("tool") == "search_grep":
        hits = t["data"]["hits"]
        fid = next(h["fragment_id"] for h in hits if "meeting_m12" in h["path"])
        return AIMessage(content="", tool_calls=[call("read_fragment", {"fragment_id": fid}),
                                                 call("search_fulltext", {"query": "бэкенд хранения чекпоинтов DocSearch", "level": "L1"})])
    if t.get("tool") in ("read_fragment", "search_fulltext"):
        frag = None
        for m in messages:
            if isinstance(m, ToolMessage) and m.name == "read_fragment":
                frag = json.loads(m.content)["data"]
        line = next(l.split(": ", 1)[1] for l in frag["text"].split("\n") if "РС-7" in l)
        return AIMessage(content="", tool_calls=[
            call("cite_fragment", {"fragment_id": frag["fragment_id"], "quote": line[:90], "claim": "РС-7: PostgreSQL 16"}),
            call("cite_fragment", {"fragment_id": frag["fragment_id"], "quote": "ответственный — Ирина Ковалёва", "claim": "ответственная"})])
    if t.get("tool") == "cite_fragment":
        return AIMessage(content="", tool_calls=[call("submit_result", {"findings": "PostgreSQL 16, ответственная Ирина Ковалёва",
                                                                         "open_questions": []})])
    if t.get("tool") == "submit_result" and not t.get("ok"):     # open questions are required: answer with one
        assert t["error"]["type"] == "open_questions_required"
        return AIMessage(content="", tool_calls=[call("submit_result", {"findings": "PostgreSQL 16, ответственная Ирина Ковалёва",
                                                                         "open_questions": ["срок перехода в корпусе не подтверждён вторым источником"]})])
    return AIMessage(content="готово")


async def test_non_blocking_deep_subagent_and_chained_budget(cfg):
    app = Lab3(cfg, *_shared())
    model = Router(policies={"main": main_policy, "deep": deep_policy}, delay=0.4)
    async with TaskRunner(cfg, verbose=False, use_mcp=False, model=model, summarizer=None) as r:
        app.runner = r
        ensure_task(r, "M-T", "main", "dialog", {}, {})
        f = app.factory("main")
        await r.run("M-T", user_text="Исследуй выбор бэкенда чекпоинтов", label="chat", agent_factory=f)
        mgr = app.managers["M-T"]
        child = next(iter(mgr.tasks))
        assert not mgr.tasks[child].done()                                   # returned before the sub-agent finished
        res2 = await r.run("M-T", user_text="Сколько документов в папке storage?", label="chat", agent_factory=f)
        assert "10" in res2["answer"]                                         # main answered while the sub-agent works
        res3 = await r.run("M-T", user_text="Покажи результат исследования", label="chat", agent_factory=f)
        sub = r.store.get_task(child)
        assert sub["status"] == "completed" and sub["spec"]["parent_task_id"] == "M-T"
        assert sub["limits"]["max_model_calls_per_run"] == 0          # the dialog per-turn cap is not inherited
        assert "PostgreSQL 16" in res3["answer"]
        assert len(r.store.evidence(child)) == 2 and all(e["fragment_id"] for e in r.store.evidence(child))
        assert r.store.usage("M-T")["input_tokens"] >= r.store.usage(child)["input_tokens"] > 0   # charged to the parent


async def test_ingestion_agent_organizes_and_indexes_then_rerun_without_duplicates(cfg, tmp_path, embedder):
    from navsearch.index import CorpusIndex
    root = tmp_path / "workspace"
    from harness.workspace import HarnessWorkspace
    ws = HarnessWorkspace(root)
    (ws.input / "new_files").mkdir()
    for f in sorted((LAB3 / "corpus" / "new_batch").glob("*")):
        shutil.copy2(f, ws.input / "new_files" / f.name)
    idx = CorpusIndex(prefix="itmo_lab3_ingtest")
    idx.drop()
    idx.ensure()
    c2 = replace(cfg, root=root)

    def ingest_policy(messages):
        t = last_tool(messages)
        tool = t.get("tool")
        if not t:
            return AIMessage(content="", tool_calls=[call("ls", {"path": "input/new_files"})])
        if tool == "ls":
            names = [e["name"] for e in t["data"]["entries"]]
            dest = {"agent": "agents", "runbook": "ops", "query": "search"}
            return AIMessage(content="", tool_calls=[call("move", {"source": f"input/new_files/{n}",
                                                                   "destination": f"data/{next(v for k, v in dest.items() if n.startswith(k))}/"})
                                                     for n in names])
        if tool == "move":
            return AIMessage(content="", tool_calls=[call("corpus_status", {})])
        if tool == "corpus_status":
            if not t["data"]["need_l1"] and not t["data"]["need_l2"]:
                return AIMessage(content="", tool_calls=[call("validate_corpus", {})])
            return AIMessage(content="", tool_calls=[call("doc_outline", {"path": p}) for p in t["data"]["need_l1"]])
        if tool == "doc_outline":
            outs = [json.loads(m.content)["data"] for m in messages if isinstance(m, ToolMessage) and m.name == "doc_outline"]
            return AIMessage(content="", tool_calls=[call("write_l1", {
                "document_id": o["document_id"], "title": o["headings"][0]["title"], "description": "Новый документ.",
                "summary": "Агент прочитал документ: " + o["path"], "sections": o["suggested_sections"]}) for o in outs])
        if tool == "write_l1":
            ids = [json.loads(m.content)["data"]["document_id"] for m in messages if isinstance(m, ToolMessage) and m.name == "write_l1"]
            return AIMessage(content="", tool_calls=[call("index_document", {"document_id": i}) for i in ids])
        if tool == "index_document":
            return AIMessage(content="", tool_calls=[call("corpus_status", {})])
        if tool == "validate_corpus":
            return AIMessage(content="", tool_calls=[call("complete_task", {"note": "3 документа добавлены"})])
        return AIMessage(content="Загрузка завершена.")

    try:
        app = Lab3(c2, idx, embedder)
        async with TaskRunner(c2, verbose=False, use_mcp=False, model=Router(policies={"ingest": ingest_policy}),
                              summarizer=None) as r:
            app.runner = r
            ensure_task(r, "ING1", "ingest", INGEST_REQUEST.format(folder="input/new_files"), {"ingest_folder": "input/new_files"}, {})
            res = await r.run("ING1", label="ingest", agent_factory=app.factory("ingest"))
            assert r.store.get_task("ING1")["status"] == "completed", r.store.get_task("ING1")["status_reason"]
            idx.refresh()
            n1 = idx.count("L2")
            from navsearch.tools_corpus import CorpusTools
            from navsearch.levels import Levels
            from organizer.catalog import Catalog
            from navsearch.tools_corpus import CorpusEnv
            env2 = CorpusEnv(ws, Catalog(ws), Levels(ws, Catalog(ws)), idx, embedder, r.store, "ING1")
            hits = CorpusTools(env2).search_grep("NEWDOC-2026-B", "L2")["hits"]
            assert hits and hits[0]["path"].startswith("data/ops/")
            # re-run on the unchanged corpus: nothing to do, no duplicates
            ensure_task(r, "ING2", "ingest", INGEST_REQUEST.format(folder="input/new_files"), {"ingest_folder": "input/new_files"}, {})
            await r.run("ING2", label="ingest", agent_factory=app.factory("ingest"))
            idx.refresh()
            assert idx.count("L2") == n1 and idx.count("L1") == 3
    finally:
        idx.drop()


def test_submit_result_without_findings_is_composed_from_evidence(env):
    from navsearch.subagents import SubmitTools
    from navsearch.tools_corpus import CorpusTools
    ct = CorpusTools(env)
    hit = ct.search_grep("РС-7", "L2")["hits"][0]
    ct.cite_fragment(hit["fragment_id"], hit["text"][:60], "РС-7")
    full = {"complete": True, "fragments_read": 1, "fragments_total": 1, "documents": []}
    st = SubmitTools(env.store, env.task_id, "exhaustive", coverage_fn=lambda: full)
    res = st.submit_result()                                   # the model called submit_result({})
    assert res["status"] == "completed"
    t = env.store.get_task(env.task_id)
    assert "Найдено подтверждений: 1" in t["completion"]["findings"]


def test_deep_stopped_by_budget_returns_an_explicit_partial_result(env):
    """A deep sub-agent paused by its budget before submit_result: the result is PARTIAL with findings from the
    cited evidence and open questions from the unfinished plan - never empty, never 'complete'."""
    from navsearch.subagents import build_result
    from navsearch.tools_corpus import CorpusTools
    ct = CorpusTools(env)
    hit = ct.search_grep("РС-7", "L2")["hits"][0]
    ct.cite_fragment(hit["fragment_id"], hit["text"][:60], "решение РС-7")
    env.store.update_task(env.task_id, plan=[{"title": "найти бэкенды", "status": "done"},
                                             {"title": "сравнить ограничения", "status": "doing"}])
    env.store.set_status(env.task_id, "paused", "budget exhausted before the call (model_calls: needed 1, remaining 0 of 60)")
    r = build_result(env.store, env.task_id)
    assert r["result"] == "partial" and r["status"] == "paused"
    assert "ЧАСТИЧНЫЙ" in r["findings"] and "ev_" in r["findings"] and len(r["evidence"]) == 1
    assert any("сравнить ограничения" in q for q in r["open_questions"])
    assert any("budget exhausted" in q for q in r["open_questions"])


def test_deep_tools_warn_when_few_model_calls_are_left(env):
    import json as _json
    from navsearch.tools_corpus import CorpusTools
    ct = CorpusTools(env, coach=True)
    t = env.store.get_task(env.task_id)
    env.store.update_task(env.task_id, limits={**t["limits"], "max_model_calls": 10})
    tool = next(x for x in ct.search_tools() if x.name == "search_grep")
    quiet = _json.loads(tool.invoke({"pattern": "РС-7", "level": "L2"}))["data"]
    assert "harness_budget" not in quiet
    env.store.add_usage(env.task_id, model_calls=6)
    warned = _json.loads(tool.invoke({"pattern": "РС-7", "level": "L2"}))["data"]
    assert "submit_result" in warned["harness_budget"]


def test_answer_check_flags_invented_paths_and_unreadable_answers(cfg, corpus):
    from dataclasses import replace
    from navsearch.agents import Lab3
    from navsearch.cli import answer_issue
    app = Lab3(replace(cfg, root=corpus["root"]), None, None)
    assert "нечитаемый" in answer_issue(app, "。\n\n。\n。、")
    assert "нечитаемый" in answer_issue(app, "")
    bad = answer_issue(app, "В папке ops: `data/ops/ops_runbook.md`, `data/ops/ops_alerts.md`.")
    assert bad and "data/ops/ops_runbook.md" in bad
    from harness.workspace import HarnessWorkspace
    from navsearch.levels import Levels
    from organizer.catalog import Catalog
    ws = HarnessWorkspace(corpus["root"])
    real = Levels(ws, Catalog(ws)).l0()["documents"][3]["path"]
    assert answer_issue(app, f"Документ найден: `{real}`, строка 12.") is None


def test_repeated_ingestion_of_known_content_is_skipped_before_the_agent(cfg, corpus):
    from dataclasses import replace
    from harness.workspace import HarnessWorkspace
    from navsearch.cli import skip_known_content
    from navsearch.levels import Levels
    from organizer.catalog import Catalog
    ws = HarnessWorkspace(corpus["root"])
    doc = next(d for d in Levels(ws, Catalog(ws)).l0()["documents"] if not d["is_empty"])
    rep = ws.root / "input" / "repeat_test"
    rep.mkdir(parents=True, exist_ok=True)
    (rep / "same.md").write_bytes((ws.root / doc["path"]).read_bytes())
    (rep / "fresh.md").write_text("# Совсем новый документ\nуникальный текст 9f3c\n", encoding="utf-8")
    n_docs = len(Catalog(ws).load()["documents"])
    out = skip_known_content(replace(cfg, root=corpus["root"]), "input/repeat_test")
    assert [d["existing"] for d in out] == [doc["path"]]
    assert not (rep / "same.md").exists() and (ws.root / "rejected" / "input" / "repeat_test" / "same.md").exists()
    assert (rep / "fresh.md").exists() and len(Catalog(ws).load()["documents"]) == n_docs
    (rep / "fresh.md").unlink()


def test_unknown_section_lists_real_sections_and_error_streak_is_pointed_out(env):
    import json as _json
    from navsearch.tools_corpus import CorpusTools
    ct = CorpusTools(env, coach=True)
    hit = ct.search_grep("РС-7", "L2")["hits"][0]
    tool = next(x for x in ct.navigation_tools() if x.name == "read_section")
    errs = [_json.loads(tool.invoke({"document_id": hit["document_id"], "section_id": f"s9{i}"})) for i in range(4)]
    assert all(not e["ok"] and e["error"]["type"] == "unknown_section" for e in errs)
    assert errs[0]["error"]["sections"] and errs[0]["error"]["sections"][0]["section_id"].startswith("s")
    assert "harness_hint" in errs[3]["error"] and "harness_hint" not in errs[0]["error"]


def test_deep_submit_with_cut_off_arguments_gets_a_clear_error(env):
    """M7-deep1: submit_result came as {} because the answer hit max_tokens=2048. The error now says so (instead of
    'open_questions_required'), and the schema puts open_questions first."""
    from navsearch.subagents import SubmitArgs, SubmitTools
    from navsearch.tools_corpus import ToolError
    st = SubmitTools(env.store, env.task_id, "deep")
    with pytest.raises(ToolError) as e:
        st.submit_result()
    assert e.value.type == "empty_arguments" and "max_tokens" in e.value.message
    assert list(SubmitArgs.model_fields)[0] == "open_questions"


async def test_sub_agent_that_stops_without_submit_gets_one_follow_up(env):
    """M7-exhaustive1 read 47/47 fragments and ended its turn without submit_result: the manager sends ONE
    follow-up message in the same task; a second silent stop is left as it is (bounded)."""
    from navsearch.subagents import FINISH_NUDGE, SubagentManager

    class FakeRunner:
        def __init__(self, store, finish_on):
            self.store, self.calls, self.finish_on = store, [], finish_on

        async def run(self, tid, user_text=None, label="run", agent_factory=None, verbose=None, overrides=None):
            self.calls.append((label, user_text))
            done = len(self.calls) == self.finish_on
            self.store.set_status(tid, "completed" if done else "paused",
                                  "ok" if done else "agent ended the turn without complete_task")
            return {}

    for finish_on, expected_calls, status in ((2, 2, "completed"), (99, 2, "paused")):
        r = FakeRunner(env.store, finish_on)
        mgr = SubagentManager(r, {"exhaustive": None}, "P")
        await mgr._run_sub(env.task_id, "exhaustive")
        assert len(r.calls) == expected_calls and r.calls[1] == ("exhaustive-finish", FINISH_NUDGE["exhaustive"])
        assert env.store.get_task(env.task_id)["status"] == status
