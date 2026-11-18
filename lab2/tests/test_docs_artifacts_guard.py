"""Selective reading, artifacts, task-root guard (offline)."""
import json

import pytest
from conftest import HANDBOOK, can_symlink, make_junction

from harness.config import Limits
from harness.tools_docs import DocTools, load_doc
from harness.tools_task import TaskTools
from harness.workspace import HarnessFileTools
from organizer.catalog import Catalog
from organizer.results import ToolError


@pytest.fixture
def docs(ws, store):
    return DocTools(ws, Catalog(ws), store, "T", Limits())


def test_handbook_has_500_plus_pages_with_markers(ws):
    doc = load_doc(ws.root / HANDBOOK, HANDBOOK)
    assert doc.paged and len(doc.pages) == 520
    assert all(b >= a for _no, a, b in doc.pages)
    # every page has substantive text, not just a separator
    for no, a, b in doc.pages:
        body = "\n".join(doc.lines[a: b]).strip()
        assert len(body) > 300, no


def test_doc_info_read_pages_and_registry(docs, store):
    info = docs.doc_info(HANDBOOK)
    assert info["pages"] == 520 and info["document_id"] and info["content_hash"].startswith("sha256:")
    r = docs.read_pages(HANDBOOK, 4, 4)
    assert "АС-01" in r["text"] and r["pages"] == [4, 4] and r["already_read_before"] is False
    assert r["text"].startswith("[стр. 4 |")
    again = docs.read_pages(HANDBOOK, 4, 4)
    assert again["already_read_before"] is True
    assert docs.doc_info(HANDBOOK)["pages_read"] == 1
    with pytest.raises(ToolError) as e:
        docs.read_pages(HANDBOOK, 1, 9)
    assert e.value.type == "too_many_pages"
    with pytest.raises(ToolError):
        docs.read_pages(HANDBOOK, 600, 600)
    with pytest.raises(ToolError):
        docs.read_pages(HANDBOOK, 0, 1)


def test_search_text_finds_facts_at_beginning_middle_end(docs):
    res = docs.search_text("Решение архитектурного совета АС-", "data/search")
    pages = sorted(h["page"] for h in res["hits"] if h["path"] == HANDBOOK)
    assert pages == [4, 261, 517]
    assert all(h["document_id"] for h in res["hits"])
    inc = docs.search_text("INC-2026-017", "data/search")
    assert inc["hits"][0]["page"] == 17


def test_read_lines_maps_pages(docs):
    doc = load_doc(docs.ws.root / HANDBOOK, HANDBOOK)
    a, b = doc.page_bounds(261)
    r = docs.read_lines(HANDBOOK, a, b)
    assert r["pages"] == [261, 261] and "АС-02" in r["text"]


def test_changed_document_is_detected(docs, store, ws):
    docs.read_pages(HANDBOOK, 1, 1)
    p = ws.root / HANDBOOK
    p.write_text(p.read_text(encoding="utf-8") + "\nновая строка\n", encoding="utf-8")
    docs.doc_info(HANDBOOK)
    assert any(e["kind"] == "document_changed" for e in store.events("T"))
    assert docs.doc_info(HANDBOOK)["pages_read"] == 0   # ranges of the old version are not reused


def test_artifact_roundtrip_survives_new_instance(artifacts, ws, store):
    from harness.artifacts import ArtifactStore
    text = "\n".join(f"строка {i}" for i in range(1, 501)) + "\nКОНТРОЛЬ: факт за пределами превью\n"
    a = artifacts.save(text, "tool_output", "test")
    b = artifacts.save(text, "tool_output", "test")
    assert a["artifact_id"] == b["artifact_id"] and b["created"] is False
    fresh = ArtifactStore(ws, store, "T")              # "after restart"
    hit = fresh.search(a["artifact_id"], "КОНТРОЛЬ")
    assert hit["hits"][0]["line"] == 501
    part = fresh.read(a["artifact_id"], 495, 10)
    assert "КОНТРОЛЬ" in part["text"]
    with pytest.raises(ToolError):
        fresh.read("art_../../etc", 1, 1)


def test_task_root_guard_blocks_escape_and_service_area(ws, root, tmp_path):
    fs = HarnessFileTools(ws, Catalog(ws), max_chars=10_000)
    for bad in ("../outside_notes.txt", str(tmp_path / "outside_notes.txt"), "data/../../outside_notes.txt"):
        with pytest.raises(ToolError) as e:
            fs.read(bad)
        assert e.value.type == "outside_workspace"
    with pytest.raises(ToolError) as e:
        fs.write("../stolen.txt", "x")
    assert e.value.type == "outside_workspace"
    assert not (tmp_path / "stolen.txt").exists()
    with pytest.raises(ToolError):
        fs.move(HANDBOOK, "../moved.md")
    assert (root / HANDBOOK).exists()
    for svc in (".harness/state.db", ".harness/artifacts/x.txt"):
        with pytest.raises(ToolError) as e:
            fs.read(svc)
        assert e.value.type == "protected"
    with pytest.raises(ToolError):
        fs.write(".harness/evil.txt", "x")
    # allowed operations keep working
    assert fs.write("data/notes/n.md", "ok")["created"] is True
    assert fs.read("data/notes/n.md")["text"] == "ok"
    grep = fs.grep("OUTSIDE", ".", recursive=True)
    assert grep["total_matches"] == 0


def test_link_escape_blocked(ws, root, tmp_path):
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "data" / "link_out"
    if not make_junction(link, outside):
        pytest.skip("cannot create a junction/symlink here")
    fs = HarnessFileTools(ws, Catalog(ws), max_chars=10_000)
    with pytest.raises(ToolError) as e:
        fs.read("data/link_out/secret.txt")
    assert e.value.type == "outside_workspace"
    with pytest.raises(ToolError):
        fs.write("data/link_out/new.txt", "x")
    assert not (outside / "new.txt").exists()
    docs = DocTools(ws, Catalog(ws), __import__("harness.store", fromlist=["TaskStore"]).TaskStore(ws.service / "s2.db"),
                    "T", Limits())
    res = docs.search_text("secret", "data")
    assert res["total_hits"] == 0


def test_evidence_verified_verbatim_and_idempotent(ws, store, artifacts, budget):
    tt = TaskTools(ws, Catalog(ws), store, "T", artifacts, budget)
    quote = "эталонный кластер «Северный» хранит поисковый индекс в 12 первичных шардах с одной репликой"
    r1 = tt.save_evidence("АС-01: 12 шардов", "decision", HANDBOOK, quote, page=4)
    assert r1["status"] == "saved" and r1["page_start"] == 4
    r2 = tt.save_evidence("АС-01: 12 шардов", "decision", HANDBOOK, quote, page=4)
    assert r2["status"] == "already_saved" and r2["evidence_id"] == r1["evidence_id"]
    with pytest.raises(ToolError) as e:
        tt.save_evidence("x", "decision", HANDBOOK, quote, page=5)
    assert e.value.type == "quote_not_in_range" and e.value.extra["actual_page"] == 4
    with pytest.raises(ToolError) as e:
        tt.save_evidence("x", "fact", HANDBOOK, "этого текста в документе нет совсем")
    assert e.value.type == "quote_not_found"
    assert len(store.evidence("T")) == 1


def test_report_sections_upsert_and_completion_criteria(ws, store, artifacts, budget):
    store.update_task("T", spec={"root": str(ws.root), "scope": ["data/search"], "criteria": {
        "min_evidence": 1, "required_sections": ["summary"]}})
    tt = TaskTools(ws, Catalog(ws), store, "T", artifacts, budget)
    with pytest.raises(ToolError) as e:
        tt.complete_task("done")
    assert e.value.type == "criteria_not_met"
    assert store.get_task("T")["status"] == "queued"
    assert tt.write_report_section("summary", "Итог", "текст")["status"] == "created"
    assert tt.write_report_section("summary", "Итог", "текст")["status"] == "unchanged"
    tt.save_evidence("инцидент", "fact", "data/search/search_incident_reports.md",
                     "лимит кэша снижен до 1 ГБ, вытеснение — LRU", page=17)
    assert tt.complete_task("done")["status"] == "completed"
    report = (ws.service / "tasks" / "T" / "report.md").read_text(encoding="utf-8")
    assert "Итоговый отчёт" in report and "ev_" in report


def test_stop_partial_is_not_completed(ws, store, artifacts, budget):
    tt = TaskTools(ws, Catalog(ws), store, "T", artifacts, budget)
    tt.stop_partial("no progress", ["claim X"])
    t = store.get_task("T")
    assert t["status"] == "paused" and t["completion"]["partial"] is True
    assert "Частичный результат" in (ws.service / "tasks" / "T" / "report.md").read_text(encoding="utf-8")


def test_evidence_tolerates_line_prefixes_and_typography(ws, store, artifacts, budget):
    tt = TaskTools(ws, Catalog(ws), store, "T", artifacts, budget)
    # quote copied from numbered read_pages output across two lines, with straight quotes instead of «»
    from harness.tools_docs import load_doc
    doc = load_doc(ws.root / HANDBOOK, HANDBOOK)
    a, b = doc.page_bounds(4)
    line_no = next(i for i in range(a, b + 1) if "АС-01" in doc.lines[i - 1])
    text = doc.lines[line_no - 1]
    start = text.index("эталонный")
    quote = f'{line_no}: ' + text[start:start + 60].replace("«", '"').replace("»", '"')
    r = tt.save_evidence("АС-01", "decision", HANDBOOK, quote, page=4)
    assert r["status"] == "saved" and r["line_start"] == line_no


def test_quote_with_inner_number_colon_is_not_mangled(ws, store, artifacts, budget):
    """'Шаг 2: ...' inside a quote is source text, only a leading 'N: ' line number is stripped."""
    (ws.root / "data" / "notes").mkdir(parents=True, exist_ok=True)
    (ws.root / "data" / "notes" / "rb.md").write_text("# RB\n\nШаг 2: восстановить снимок на узле es-new-01 (код X-1).\n",
                                                     encoding="utf-8")
    tt = TaskTools(ws, Catalog(ws), store, "T", artifacts, budget)
    r = tt.save_evidence("шаг 2", "fact", "data/notes/rb.md", "Шаг 2: восстановить снимок на узле es-new-01 (код X-1).")
    assert r["line_start"] == 3
    r2 = tt.save_evidence("с номером строки", "fact", "data/notes/rb.md", "3: Шаг 2: восстановить снимок на узле es-new-01")
    assert r2["line_start"] == 3


def test_write_and_move_by_absolute_path_are_blocked(ws, root, tmp_path):
    """Guard matrix: absolute path for write and for move (source and destination), allowed ops keep working."""
    fs = HarnessFileTools(ws, Catalog(ws), max_chars=10_000)
    outside = tmp_path / "abs_outside"
    outside.mkdir()
    with pytest.raises(ToolError) as e:
        fs.write(str(outside / "stolen.txt"), "x")
    assert e.value.type == "outside_workspace" and not (outside / "stolen.txt").exists()
    with pytest.raises(ToolError) as e:
        fs.move(HANDBOOK, str(outside / "moved.md"))
    assert e.value.type == "outside_workspace" and (root / HANDBOOK).exists() and not (outside / "moved.md").exists()
    with pytest.raises(ToolError) as e:
        fs.move(str(tmp_path / "outside_notes.txt"), "data/notes/in.txt")
    assert e.value.type == "outside_workspace" and (tmp_path / "outside_notes.txt").exists()
    # an absolute path INSIDE the root is allowed
    assert fs.write(str(root / "data" / "notes" / "abs_ok.md"), "ok")["created"] is True
    assert fs.move("data/notes/abs_ok.md", str(root / "data" / "notes" / "abs_ok_moved.md"))
    assert (root / "data" / "notes" / "abs_ok_moved.md").exists()


def test_move_through_junction_is_blocked_both_ways(ws, root, tmp_path):
    outside = tmp_path / "junction_target"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "data" / "link_out"
    if not make_junction(link, outside):
        pytest.skip("cannot create a junction/symlink here")
    fs = HarnessFileTools(ws, Catalog(ws), max_chars=10_000)
    with pytest.raises(ToolError) as e:                       # destination resolves outside through the junction
        fs.move("data/search/search_incident_reports.md", "data/link_out/moved.md")
    assert e.value.type == "outside_workspace"
    assert (root / "data" / "search" / "search_incident_reports.md").exists() and not (outside / "moved.md").exists()
    with pytest.raises(ToolError) as e:                       # source resolves outside through the junction
        fs.move("data/link_out/secret.txt", "data/secret_copy.txt")
    assert e.value.type == "outside_workspace"
    assert (outside / "secret.txt").exists() and not (root / "data" / "secret_copy.txt").exists()
    with pytest.raises(ToolError) as e:                       # the link itself is never moved
        fs.move("data/link_out", "data/link_moved")
    assert e.value.type in ("symlink_not_allowed", "outside_workspace", "is_directory")


def test_verbatim_quotes_criterion_rejects_paraphrase(ws, store, artifacts, budget):
    """[C5] 'decisions verbatim with page' is checked by code in complete_task, not only claimed by the model."""
    store.update_task("T", spec={"root": str(ws.root), "scope": ["data/search"], "criteria": {
        "min_evidence": 1, "required_sections": ["decisions"],
        "verbatim_quotes": {"section": "decisions", "codes": ["АС-01"]}}})
    tt = TaskTools(ws, Catalog(ws), store, "T", artifacts, budget)
    quote = ("Решение архитектурного совета АС-01: эталонный кластер «Северный» хранит поисковый индекс в 12 первичных "
             "шардах с одной репликой; увеличение числа шардов допускается только через полную переиндексацию.")
    ev = tt.save_evidence("АС-01", "decision", HANDBOOK, quote, page=4)
    tt.write_report_section("decisions", "Решения", "**АС-01** (стр. 4): «Кластер „Северный“ хранит индекс в 12 шардах "
                                                    "с одной репликой.»")
    with pytest.raises(ToolError) as e:
        tt.complete_task("решения дословно")
    prob = e.value.extra["missing"]["verbatim_quotes"]["problems"]["АС-01"]
    assert prob["evidence_id"] == ev["evidence_id"] and prob["page"] == "стр. 4"
    assert "12 первичных шардах" in prob["paste_exactly"] and store.get_task("T")["status"] != "completed"
    # verbatim but without the page -> still rejected
    tt.write_report_section("decisions", "Решения", f"**АС-01**: «{quote}»")
    with pytest.raises(ToolError):
        tt.complete_task("x")
    # exact quote (typographic quotes may differ) + page -> accepted
    tt.write_report_section("decisions", "Решения", f"**АС-01** (справочник, стр. 4):\n\n> «{quote.replace('«', '\"').replace('»', '\"')}» [{ev['evidence_id']}]")
    res = tt.complete_task("решения дословно")
    assert res["status"] == "completed" and res["criteria"]["verbatim_quotes"]["ok"]


def test_offloaded_json_is_stored_line_by_line():
    """A one-line tool envelope is stored so that read_artifact reaches every part by line ranges."""
    from harness.middleware import line_friendly
    env = json.dumps({"ok": True, "tool": "read_pages", "data": {"pages": [2, 6], "text": "a\nb\nКОНТРОЛЬ"}}, ensure_ascii=False)
    out = line_friendly(env)
    assert out.splitlines()[-1] == "КОНТРОЛЬ" and json.loads(out.splitlines()[0])["data"]["pages"] == [2, 6]
    hits = json.dumps({"ok": True, "tool": "search_text", "data": {"hits": [{"page": i, "snippet": f"s{i}"} for i in range(50)]}})
    lf = line_friendly(hits)
    assert len(lf.splitlines()) > 50 and json.loads(lf) == json.loads(hits)
    assert line_friendly("plain text") == "plain text"
