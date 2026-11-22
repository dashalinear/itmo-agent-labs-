"""Contracts of read/head/tail/grep/ls/write/move on fixed files + path guard."""
import json
import os

import pytest

from organizer.results import run_tool
from organizer.textutil import file_hash

from .conftest import can_symlink


def call(fs, name, **kw):
    return json.loads(run_tool(name, getattr(fs, name), **kw))


# ---------------------------------------------------------------- read
def test_read_returns_path_text_and_flags(fs):
    r = call(fs, "read", path="input/project_recsys.md")
    assert r["ok"] and r["data"]["path"] == "input/project_recsys.md"
    assert r["data"]["text"].startswith("# Проект: рекомендательная")
    assert r["data"]["truncated"] is False and r["data"]["content_hash"].startswith("sha256:")


def test_read_truncates_large_output(fs, ws):
    (ws.data / "big.txt").write_text("x" * 5000, encoding="utf-8")
    r = call(fs, "read", path="data/big.txt")
    assert r["data"]["truncated"] is True and len(r["data"]["text"]) == 2000 and r["data"]["total_chars"] == 5000


def test_read_numbered(fs):
    r = call(fs, "read", path="input/howto_git_branches.txt", numbered=True)
    assert r["data"]["text"].splitlines()[0].startswith("1: Инструкция")


def test_read_missing_file_is_observation(fs):
    r = call(fs, "read", path="input/nope.md")
    assert r["ok"] is False and r["error"]["type"] == "not_found"


def test_read_directory_error(fs):
    assert call(fs, "read", path="input")["error"]["type"] == "is_directory"


# ---------------------------------------------------------------- head/tail
def test_head_and_tail(fs):
    h = call(fs, "head", path="input/howto_git_branches.txt", n=2)["data"]
    assert h["returned_lines"] == 2 and h["first_line"] == 1 and h["text"].startswith("Инструкция")
    t = call(fs, "tail", path="input/howto_git_branches.txt", n=2)["data"]
    assert t["last_line"] == t["total_lines"] and t["text"].endswith(".gitignore.")
    assert t["text"].splitlines()[0] == ""  # original order kept (blank line before last)


def test_head_short_file_returns_all(fs):
    d = call(fs, "head", path="input/howto_git_branches.txt", n=1000)["data"]
    assert d["returned_lines"] == d["total_lines"]


def test_tail_empty_file(fs):
    d = call(fs, "tail", path="input/empty_todo.txt", n=5)["data"]
    assert d["text"] == "" and d["returned_lines"] == 0


@pytest.mark.parametrize("bad", [0, -3, True, "5"])
def test_invalid_n(fs, bad):
    r = call(fs, "head", path="input/howto_git_branches.txt", n=bad)
    assert r["ok"] is False and r["error"]["type"] == "invalid_argument"


# ---------------------------------------------------------------- grep
def test_grep_literal_recursive_with_limit(fs):
    r = call(fs, "grep", pattern="Elasticsearch", path="input", recursive=True, max_matches=2)["data"]
    assert r["returned"] == 2 and r["has_more"] and r["remaining"] == r["total_matches"] - 2
    m = r["matches"][0]
    assert set(m) == {"path", "line", "text"} and m["line"] >= 1


def test_grep_non_recursive_only_top_level(fs):
    r = call(fs, "grep", pattern="шард", path="input", recursive=False)["data"]
    assert r["total_matches"] == 0
    r = call(fs, "grep", pattern="шард", path="input", recursive=True)["data"]
    assert r["matches"][0]["path"] == "input/courses/notes.md"


def test_grep_literal_not_regex(fs):
    assert call(fs, "grep", pattern="k1 = 1.2", path="input", recursive=True)["data"]["total_matches"] == 1
    assert call(fs, "grep", pattern="k1.=", path="input", recursive=True)["data"]["total_matches"] == 0
    assert call(fs, "grep", pattern=r"k1\s=", path="input", recursive=True, regex=True)["data"]["total_matches"] == 1


# ---------------------------------------------------------------- ls
def test_ls_stable_and_fresh(fs, ws):
    a = call(fs, "ls", path="input")["data"]
    names = [e["name"] for e in a["entries"]]
    assert names[:2] == ["courses", "meetings"]  # dirs first, then files by name
    assert call(fs, "ls", path="input")["data"] == a  # stable
    (ws.input / "new.md").write_text("new", encoding="utf-8")
    b = call(fs, "ls", path="input")["data"]
    assert "new.md" in [e["name"] for e in b["entries"]]
    f = next(e for e in b["entries"] if e["name"] == "new.md")
    assert f["type"] == "file" and f["size"] == 3


# ---------------------------------------------------------------- write
def test_write_creates_parents_and_appends(fs, ws):
    r = call(fs, "write", path="data/courses/_about.md", content="Курсы\n")["data"]
    assert r["created"] and (ws.data / "courses" / "_about.md").exists()
    call(fs, "write", path="data/courses/_about.md", content="ещё\n", mode="append")
    assert (ws.data / "courses" / "_about.md").read_text(encoding="utf-8") == "Курсы\nещё\n"


def test_write_protects_catalog_input_and_documents(fs, ws):
    assert call(fs, "write", path="catalog.json", content="{}")["error"]["type"] == "protected"
    assert call(fs, "write", path="input/x.md", content="x")["error"]["type"] == "protected"
    call(fs, "move", source="input/project_recsys.md", destination="data/projects/")
    assert call(fs, "write", path="data/projects/project_recsys.md", content="x")["error"]["type"] == "protected"


# ---------------------------------------------------------------- move
def test_move_input_to_data_and_between_folders(fs, ws, catalog):
    h = file_hash(ws.input / "search_quality_thoughts.md")
    r = call(fs, "move", source="input/search_quality_thoughts.md", destination="data/projects/search_quality_thoughts.md")["data"]
    assert r["verified"] and not (ws.input / "search_quality_thoughts.md").exists()
    doc_id = r["document_id"]
    r2 = call(fs, "move", source="data/projects/search_quality_thoughts.md", destination="data/courses/")["data"]
    assert r2["document_id"] == doc_id and r2["new_path"] == "data/courses/search_quality_thoughts.md"
    assert not (ws.data / "projects" / "search_quality_thoughts.md").exists()
    assert file_hash(ws.data / "courses" / "search_quality_thoughts.md") == h
    d = catalog.get(doc_id)
    assert d["original_path"] == "input/search_quality_thoughts.md" and d["category"] == "courses"
    assert d["content_hash"] == h and len(d["moves"]) == 2


def test_move_conflict_never_overwrites(fs, ws):
    call(fs, "move", source="input/courses/notes.md", destination="data/notes/notes.md")
    before = (ws.data / "notes" / "notes.md").read_bytes()
    r = call(fs, "move", source="input/meetings/notes.md", destination="data/notes/notes.md")
    assert r["ok"] is False and r["error"]["type"] == "name_conflict"
    assert r["error"]["suggested_name"] == "notes_2.md"
    assert (ws.data / "notes" / "notes.md").read_bytes() == before
    assert (ws.input / "meetings" / "notes.md").exists()  # nothing lost
    ok = call(fs, "move", source="input/meetings/notes.md", destination="data/notes/notes_2.md")
    assert ok["ok"]


def test_move_rejects_bad_targets(fs):
    assert call(fs, "move", source="input/project_recsys.md", destination="input/x.md")["error"]["type"] == "invalid_destination"
    assert call(fs, "move", source="input/nope.md", destination="data/x/")["error"]["type"] == "not_found"
    assert call(fs, "move", source="input/courses", destination="data/x/")["error"]["type"] == "is_directory"
    assert call(fs, "move", source="input/project_recsys.md", destination="data/x.md")["error"]["type"] == "invalid_destination"
    # folder given without trailing slash keeps the file name
    r = call(fs, "move", source="input/project_recsys.md", destination="data/projects")["data"]
    assert r["new_path"] == "data/projects/project_recsys.md"


def test_move_reports_duplicate_content(fs, ws):
    (ws.input / "copy.md").write_bytes((ws.input / "project_recsys.md").read_bytes())
    call(fs, "move", source="input/project_recsys.md", destination="data/projects/")
    r = call(fs, "move", source="input/copy.md", destination="data/projects/")["data"]
    assert r["duplicate_of"][0]["current_path"] == "data/projects/project_recsys.md"


def test_empty_file_kept_in_catalog(fs, catalog):
    r = call(fs, "move", source="input/empty_todo.txt", destination="data/_unsorted/")["data"]
    d = catalog.get(r["document_id"])
    assert d["is_empty"] and d["note"]


# ---------------------------------------------------------------- path guard
@pytest.mark.parametrize("tool,kw", [
    ("read", {"path": "../secret.txt"}),
    ("read", {"path": "input/../../secret.txt"}),
    ("ls", {"path": ".."}),
    ("write", {"path": "../evil.txt", "content": "x"}),
    ("move", {"source": "input/project_recsys.md", "destination": "../stolen.md"}),
    ("grep", {"pattern": "x", "path": "..", "recursive": True}),
])
def test_dotdot_escape_blocked(fs, ws, tool, kw):
    (ws.root.parent / "secret.txt").write_text("top secret", encoding="utf-8")
    r = call(fs, tool, **kw)
    assert r["ok"] is False and r["error"]["type"] == "outside_workspace"
    assert not (ws.root.parent / "evil.txt").exists() and not (ws.root.parent / "stolen.md").exists()


def test_absolute_path_outside_blocked(fs, ws, tmp_path):
    outside = tmp_path / "abs.txt"
    outside.write_text("x", encoding="utf-8")
    assert call(fs, "read", path=str(outside))["error"]["type"] == "outside_workspace"
    inside = call(fs, "read", path=str(ws.input / "project_recsys.md"))
    assert inside["ok"]  # absolute path inside workspace is fine


def test_symlink_escape_blocked(fs, ws, tmp_path):
    """Real symlinks (file and directory). Runs on Linux/macOS and on Windows with Developer Mode
    (or SeCreateSymbolicLinkPrivilege); otherwise skipped - the junction test below covers the
    Windows reparse-point escape with the same assertions."""
    if not can_symlink(tmp_path):
        pytest.skip("symlinks not permitted for this OS account (Windows: enable Developer Mode or run "
                    "as administrator); the escape is covered by test_junction_escape_blocked_windows")
    secret = tmp_path / "outside_secret.txt"
    secret.write_text("secret", encoding="utf-8")
    os.symlink(secret, ws.input / "link.txt")
    os.symlink(tmp_path, ws.input / "linkdir", target_is_directory=True)
    assert call(fs, "read", path="input/link.txt")["error"]["type"] == "outside_workspace"
    assert call(fs, "read", path="input/linkdir/outside_secret.txt")["error"]["type"] == "outside_workspace"
    assert call(fs, "write", path="input/linkdir/new.txt", content="x")["error"]["type"] == "outside_workspace"
    assert call(fs, "move", source="input/link.txt", destination="data/x/")["error"]["type"] == "symlink_not_allowed"
    ls = call(fs, "ls", path="input")["data"]["entries"]
    assert next(e for e in ls if e["name"] == "link.txt")["target"].startswith("outside")
    g = call(fs, "grep", pattern="secret", path="input", recursive=True)["data"]
    assert g["total_matches"] == 0


def test_junction_escape_blocked_windows(fs, ws, tmp_path):
    """Windows directory junctions need no admin rights: same guarantees as symlinks."""
    try:
        import _winapi
    except ImportError:
        pytest.skip("Windows-only test")
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    _winapi.CreateJunction(str(outside), str(ws.input / "junction"))
    assert call(fs, "read", path="input/junction/secret.txt")["error"]["type"] == "outside_workspace"
    assert call(fs, "write", path="input/junction/new.txt", content="x")["error"]["type"] == "outside_workspace"
    assert not (outside / "new.txt").exists()
    assert call(fs, "move", source="input/junction/secret.txt", destination="data/x/")["error"]["type"] == "outside_workspace"
    assert call(fs, "move", source="input/project_recsys.md", destination="input/junction/x.md")["error"]["type"] == "outside_workspace"
    assert call(fs, "ls", path="input/junction")["error"]["type"] == "outside_workspace"
    entry = next(e for e in call(fs, "ls", path="input")["data"]["entries"] if e["name"] == "junction")
    assert entry["type"] == "symlink" and entry["path"] is None
    g = call(fs, "grep", pattern="secret", path="input", recursive=True)["data"]
    assert g["total_matches"] == 0
    # the junction itself is reported, the names of files behind it are not
    assert {"path": "input/junction", "reason": "link_outside_workspace"} in g["skipped"]
    assert not any("secret.txt" in str(x["path"]) for x in g["skipped"])
    # the same escape written as an absolute path through the junction, as an absolute path to the
    # target itself, and combined with ../
    assert call(fs, "read", path=str(ws.input / "junction" / "secret.txt"))["error"]["type"] == "outside_workspace"
    assert call(fs, "read", path=str(outside / "secret.txt"))["error"]["type"] == "outside_workspace"
    assert call(fs, "read", path="input/junction/../junction/secret.txt")["error"]["type"] == "outside_workspace"
    assert call(fs, "head", path="input/junction/secret.txt", n=1)["error"]["type"] == "outside_workspace"
    assert call(fs, "tail", path="input/junction/secret.txt", n=1)["error"]["type"] == "outside_workspace"
    assert call(fs, "grep", pattern="secret", path="input/junction", recursive=True)["error"]["type"] == "outside_workspace"
    assert (outside / "secret.txt").exists() and not (ws.data / "x").exists()


def test_parallel_moves_from_one_folder_all_catalogued(fs, ws, catalog):
    """Parallel tool calls move several files out of one input subfolder: every move is catalogued."""
    from concurrent.futures import ThreadPoolExecutor
    sub = ws.input / "batch"
    sub.mkdir()
    for i in range(6):
        (sub / f"doc{i}.md").write_text(f"# Документ {i}\nтекст {i}\n", encoding="utf-8")
    with ThreadPoolExecutor(6) as pool:
        res = list(pool.map(lambda i: fs.move(f"input/batch/doc{i}.md", "data/batch/"), range(6)))
    assert all(r["verified"] for r in res)
    paths = {d["current_path"] for d in catalog.load()["documents"]}
    assert {f"data/batch/doc{i}.md" for i in range(6)} <= paths
    assert not sub.exists()


# ---------------------------------------------------------------- parallel moves into a new folder (Windows)
def test_extended_length_prefix_is_not_treated_as_outside(ws):
    from pathlib import Path
    from organizer.pathguard import _plain
    inside = ws.root / "data" / "topic" / "a.md"
    prefix = "\\" * 2 + "?" + "\\"                    # the Windows extended-length prefix
    assert _plain(Path(prefix + str(inside))) == inside
    assert ws.contains(Path(prefix + str(inside)))


def test_parallel_moves_into_a_new_folder_all_succeed(fs, ws):
    from concurrent.futures import ThreadPoolExecutor
    for i in range(8):
        (ws.input / f"p{i}.md").write_text(f"# Файл {i}\nтекст {i}\n", encoding="utf-8")
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda i: call(fs, "move", source=f"input/p{i}.md", destination=f"data/newtopic/p{i}.md"), range(8)))
    assert all(r["ok"] for r in res), [r.get("error") for r in res if not r["ok"]]
    assert sorted(p.name for p in (ws.data / "newtopic").iterdir()) == [f"p{i}.md" for i in range(8)]
