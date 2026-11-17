"""L0/L1/L2 structure, validation, navigation and the six search combinations (no LLM)."""
import json
import re

import pytest
from conftest import LAB3

from navsearch.levels import fragment_key
from navsearch.tools_corpus import CorpusTools
from organizer.results import ToolError

MAN = json.loads((LAB3 / "corpus" / "manifest.json").read_text(encoding="utf-8"))


def doc_by_name(env, name):
    return next(d for d in env.levels.l0()["documents"] if d["path"].endswith("/" + name))


def test_l0_totals_and_folders(env):
    l0 = env.levels.l0()
    assert l0["totals"]["documents"] == 53 and l0["totals"]["fragments"] >= 200
    assert {f["folder"] for f in l0["folders"]} >= {"storage", "search", "agents", "ops", "projects", "lectures"}
    ct = CorpusTools(env)
    page1 = ct.corpus_overview()
    page2 = ct.corpus_overview(page1["next_cursor"])
    assert len(page1["documents_page"]) == 25 and page2["page"][0] == 26
    assert ct.list_documents("storage")["documents_total"] == 10


def test_fragments_match_source_and_ids_are_stable(env):
    d = doc_by_name(env, "st_snapshot_policy.md")
    l2 = env.levels.get_l2(d["document_id"])
    _e, p, lines = env.levels.source(d["document_id"])
    for f in l2["fragments"]:
        assert f["fragment_id"] == fragment_key(d["document_id"], f["content_hash"], f["start_line"], f["end_line"])
        assert env.levels.with_text(f)["text"] == "\n".join(lines[f["start_line"] - 1: f["end_line"]])
    assert [f["fragment_id"] for f in env.levels.build_fragments(d["document_id"])] == [f["fragment_id"] for f in l2["fragments"]]
    assert env.levels.validate(env.index.counts_by_document())["ok"]


def test_write_l1_rejects_bad_structure(env):
    d = doc_by_name(env, "st_retention_policy.txt")
    with pytest.raises(ToolError) as e:
        env.levels.write_l1(d["document_id"], "t", "d", "s", [{"title": "a", "start_line": 1, "end_line": 5},
                                                              {"title": "b", "start_line": 4, "end_line": 8}])
    assert e.value.type == "invalid_structure"
    assert any("overlaps" in p for p in e.value.extra["problems"]) and any("not covered" in p for p in e.value.extra["problems"])


def test_navigation_l0_l1_l2(env):
    ct = CorpusTools(env)
    d = doc_by_name(env, "meeting_m12.md")
    l1 = ct.get_l1(d["document_id"])
    sec = next(s for s in l1["sections"] if s["title"] == "Решения")
    part = ct.read_section(d["document_id"], sec["section_id"])
    assert "РС-7" in part["fragments"][0]["text"] and part["back"]
    fid = part["fragments"][0]["fragment_id"]
    fr = ct.read_fragment(fid)
    assert fr["lines"][0] >= sec["start_line"] and fr["lines"][1] <= sec["end_line"]
    nxt = ct.neighbor_fragment(fid, "next")
    assert nxt["fragment_id"] == fr["next_id"]
    src = ct.open_source(d["document_id"], fr["lines"][0], fr["lines"][1])
    assert fid in src["fragments"] and src["text"].split("\n")[0].startswith(f"{fr['lines'][0]}: ")


@pytest.mark.parametrize("cid", ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"])
def test_six_search_combinations(env, cid):
    c = next(x for x in MAN["control"] if x["id"] == cid)
    ct = CorpusTools(env)
    level = c["kind"].split()[0]
    if "fulltext" in c["kind"]:
        r = ct.search_fulltext(c["query"], level, size=5)
    elif "grep" in c["kind"]:
        r = ct.search_grep(c["pattern"], level, regex=c.get("regex", False))
    else:
        r = ct.search_vector(c["query"], level, k=5)
    names = [h["path"].split("/")[-1] for h in r["hits"]]
    for exp in (c.get("expect_files") or [c["expect_file"]]):
        assert exp in names, (cid, names)
    h = r["hits"][0]
    assert ("fragment_id" in h) == (level == "L2") and h["document_id"]
    if level == "L2" and "grep" in c["kind"]:
        line = h["line"]
        _d, _p, lines = env.levels.source(h["document_id"])
        assert re.search(c["pattern"], lines[line - 1])            # exact line of the match


def test_grep_pages_reach_every_hit(env):
    ct = CorpusTools(env)
    total, seen, cur = None, 0, None
    while True:
        r = ct.search_grep("Ограничение", "L2", folder="storage", cursor=cur)
        total = r["total"]
        seen += len(r["hits"])
        cur = r["next_cursor"]
        if not cur:
            break
    expected = sum(1 for m in MAN["exhaustive_area"]["mentions"] if m["has_word"])
    assert total == seen == expected > 20                           # more than one page


def test_grep_page_fits_single_result_limit_and_invented_cursor_is_rejected(env):
    import base64
    ct = CorpusTools(env)
    r = ct.search_grep("Ограничение", "L2", folder="storage")
    envelope = json.dumps({"ok": True, "tool": "search_grep", "data": r}, ensure_ascii=False)
    assert len(envelope) < 6000 and r["next_cursor"]                # not offloaded: next_cursor visible to the model
    unpadded = r["next_cursor"].rstrip("=")
    assert ct.search_grep("Ограничение", "L2", folder="storage", cursor=unpadded)["page"] ==         ct.search_grep("Ограничение", "L2", folder="storage", cursor=r["next_cursor"])["page"]
    invented = base64.urlsafe_b64encode(b'{"offset":16}').decode()  # what the model made up in run M3
    with pytest.raises(ToolError) as e:
        ct.search_grep("Ограничение", "L2", folder="storage", cursor=invented)
    assert e.value.type == "invalid_cursor"


def test_deep_coaching_marks_repeated_reads_and_asks_to_cite(env):
    ct = CorpusTools(env, coach=True)
    d = doc_by_name(env, "runbook_rb09.md")
    secs = ct.get_l1(d["document_id"])["sections"]
    first = ct.read_section(d["document_id"], secs[0]["section_id"])
    assert "note_repeat" not in first
    again = ct.read_section(d["document_id"], secs[0]["section_id"])
    assert "note_repeat" in again
    for _ in range(4):
        r = ct.read_fragment(secs[0]["first_fragment_id"])
    if not env.store.evidence(env.task_id):
        assert "harness_hint" in r
    assert "note_repeat" not in CorpusTools(env).read_section(d["document_id"], secs[0]["section_id"])   # main: no coaching


def test_cite_fragment_verifies_quote(env):
    ct = CorpusTools(env)
    d = doc_by_name(env, "runbook_rb09.md")
    hit = ct.search_grep("15 минут", "L2", document_id=d["document_id"])["hits"][0]
    ev = ct.cite_fragment(hit["fragment_id"], "допустимое время переключения — 15 минут", "RB-09 шаг 3: 15 минут")
    assert ev["status"] == "saved" and ev["lines"][0] == hit["line"]
    assert ct.cite_fragment(hit["fragment_id"], "допустимое время переключения — 15 минут", "x")["status"] == "already_saved"
    with pytest.raises(ToolError):
        ct.cite_fragment(hit["fragment_id"], "этого текста во фрагменте нет", "x")
    assert env.store.evidence(env.task_id)[0]["fragment_id"] == hit["fragment_id"]
