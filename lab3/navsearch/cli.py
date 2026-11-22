"""CLI of lab 3.

    python -m navsearch.cli prepare [--reset]                  # task root from corpus/input, ES indices
    python -m navsearch.cli ingest --folder input              # ingestion AGENT (lab 1 organizer in the lab 2 harness)
    python -m navsearch.cli add-new-files                      # user action: copies corpus/new_batch -> workspace/input/new_files
    python -m navsearch.cli ingest --folder input/new_files    # index the new files (same agent, new folder)
    python -m navsearch.cli chat --task-id M1 [--script demo/dialog_main_ru.txt]   # main agent (dialog CLI)
    python -m navsearch.cli subagent --kind exhaustive --scope storage --request "..." [--task-id X] [--set k=v]
    python -m navsearch.cli resume --task-id <id> [--set k=v]  # any task: ingestion, main, deep, exhaustive
    python -m navsearch.cli status|pause|cancel|limits|report --task-id <id>
    python -m navsearch.cli coverage --task-id <exhaustive id>
    python -m navsearch.cli validate | probe | export | list
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

from . import LAB3_ROOT
from .agents import Lab3
from .config import DEFAULT_LIMITS, load
from .embed import Embedder
from .index import CorpusIndex
from .levels import Levels

from harness.cli import _kv, print_status
from harness.runner import TaskRunner
from harness.store import TaskStore
from harness.workspace import HarnessWorkspace
from organizer.catalog import Catalog

CORPUS = LAB3_ROOT / "corpus"


def prepare(cfg, reset: bool) -> None:
    root = cfg.root
    if reset and root.exists():
        assert root.name == "workspace" and root.parent == LAB3_ROOT, root
        shutil.rmtree(root)
    ws = HarnessWorkspace(root)
    if not any(ws.input.iterdir()) and not any(ws.data.iterdir()):
        shutil.copytree(CORPUS / "input", ws.input, dirs_exist_ok=True)
    idx = CorpusIndex()
    if reset:
        idx.drop()
    idx.ensure()
    n = sum(1 for p in ws.input.rglob("*") if p.is_file())
    print(f"task root: {root}\ninput files: {n}\nElasticsearch indices: {idx.l1}, {idx.l2}")


def add_new_files(cfg, batch: str = "new_batch", to: str = "input/new_files") -> list[str]:
    dst = cfg.root / to
    dst.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted((CORPUS / batch).glob("*")):
        if not (dst / f.name).exists():
            shutil.copy2(f, dst / f.name)
            out.append(f"{to}/{f.name}")
    print("added:", out)
    return out


def ensure_task(runner: TaskRunner, task_id: str, kind: str, request: str, extra_spec: dict, overrides: dict) -> dict:
    t = runner.store.get_task(task_id)
    if t:
        return t
    spec = {"task_id": task_id, "kind": kind, "request": request, "constraints": [], "scope": [], "criteria": {},
            "limits": DEFAULT_LIMITS.get(kind, DEFAULT_LIMITS["main"]), **extra_spec}
    return runner.create(spec, task_id, overrides)


def skip_known_content(cfg, folder: str) -> list[dict]:
    """Repeated ingestion of unchanged files: a file whose content hash already belongs to a document in data/ is
    moved to rejected/<folder>/ (kept, not lost) and NOT handed to the agent, so no second document appears."""
    from organizer.textutil import file_hash
    ws = HarnessWorkspace(cfg.root)
    cat = Catalog(ws)
    out = []
    root = ws.resolve(folder, must_exist=True)
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        same = [d for d in cat.same_content(file_hash(p)) if d["current_path"].startswith("data/")]
        if not same:
            continue
        dst = ws.root / "rejected" / ws.rel(p)
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(p, dst)
        out.append({"file": ws.rel(p), "existing": same[0]["current_path"], "document_id": same[0]["document_id"],
                    "kept_in": ws.rel(dst)})
        print(f"  [harness] {ws.rel(p)}: same content as {same[0]['current_path']} -> {ws.rel(dst)} (not indexed again)")
    return out


INGEST_REQUEST = ("Организуй все файлы из папки {folder} в data по темам (по содержимому, тематические подпапки входа "
                  "сохраняй, если они подходят) и добавь документы в корпус: каталог L0, структура и summary L1, "
                  "тексты L2 в Elasticsearch. Неизменённые документы, уже имеющие актуальные L1/L2, не обрабатывай повторно. "
                  "В конце проверь корпус validate_corpus и заверши задачу.")


PATH_RX = re.compile(r"data/[\w.\-/]+\.(?:md|txt)")
EV_RX = re.compile(r"\bev_[0-9a-f]+\b")
SHORT_FRAG_RX = re.compile(r"(?:fragment_id|fragment|frag)[\s:=`'\"]{0,4}([0-9a-f]{8})(?![0-9a-f-])", re.I)


def answer_issue(app: Lab3, answer: str) -> str | None:
    """Deterministic check of a main-agent answer before the next user turn (see the main prompt):
    an empty/unreadable answer, or document paths that do not exist in L0, are returned to the agent once."""
    text = (answer or "").strip()
    letters = sum(ch.isalnum() for ch in text)
    if not text or letters < 0.3 * len(text) or letters < 5:
        return ("Предыдущий ответ пустой или нечитаемый. Ответь на последний вопрос пользователя заново, по-русски, "
                "опираясь на результаты инструментов; если нужных данных нет, вызови инструменты.")
    evs = set(EV_RX.findall(text))
    if evs and getattr(app, "runner", None) is not None:
        st = app.runner.store
        known_ev = {e["evidence_id"] for t in st.list_tasks() for e in st.evidence(t["task_id"])}
        bad = sorted(evs - known_ev)
        if bad:
            return ("В ответе есть evidence_id, которых нет ни в одной задаче: " + ", ".join(bad[:8]) +
                    ". Называй только evidence_id, которые вернули cite_fragment / record_mention или subagent_result.")
    short = sorted(set(SHORT_FRAG_RX.findall(text)))
    if short:
        return ("В ответе fragment_id сокращены до 8 символов (" + ", ".join(short[:6]) + "). Указывай fragment_id "
                "целиком, как его вернул инструмент (36 символов), иначе по ссылке нельзя открыть фрагмент L2.")
    paths = set(PATH_RX.findall(text))
    if paths:
        ws = HarnessWorkspace(app.cfg.root)
        known = {d["path"] for d in Levels(ws, Catalog(ws)).l0()["documents"]}
        unknown = sorted(p for p in paths if p not in known)
        if unknown:
            return ("В ответе названы документы, которых нет в корпусе (L0): " + ", ".join(unknown[:8]) +
                    ". Проверь состав через list_documents / corpus_overview и исправь ответ: называй только то, "
                    "что вернули инструменты.")
    return None


async def chat(app: Lab3, task_id: str, script: Path | None) -> None:
    runner = app.runner
    lines = None
    if script:
        lines = [l for l in script.read_text(encoding="utf-8").splitlines() if l.strip() and not l.lstrip().startswith("#")]
    print("Основной агент корпуса. Команды: /status, /subtasks, @wait N, @wait-subtasks, /exit")
    factory = app.factory("main")
    while True:
        if lines is not None:
            if not lines:
                break
            text = lines.pop(0).strip()
            print(f"\n> {text}")
        else:
            try:
                text = (await asyncio.to_thread(input, "\n> ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
        if not text:
            continue
        if text in ("/exit", "выход"):
            break
        if text == "/status":
            print_status(runner.store, task_id)
            continue
        if text == "/subtasks":
            for t in runner.store.list_tasks():
                if t["task_id"].startswith(task_id + "-"):
                    print(f"  {t['task_id']:<28} {t['kind']:<10} {t['status']:<10} {t['status_reason'] or ''}"[:160])
            continue
        if text.startswith("@wait-subtasks"):
            mgr = app.managers.get(task_id)
            pending = [t for t in (mgr.tasks.values() if mgr else []) if not t.done()]
            print(f"  [ожидание завершения фоновых субагентов: {len(pending)}]")
            if pending:
                await asyncio.wait(pending, timeout=3600)
            continue
        if text.startswith("@wait"):
            sec = int(text.split()[1]) if len(text.split()) > 1 else 10
            print(f"  [пользователь ждёт {sec} с; субагенты продолжают работу в фоне]")
            await asyncio.sleep(sec)
            continue
        res = await runner.run(task_id, user_text=text, label="chat", agent_factory=factory)
        issue = answer_issue(app, (res or {}).get("answer") or "")
        if issue and (res or {}).get("status") not in ("cancelled",):
            print(f"  [harness check] {issue[:160]}")
            await runner.run(task_id, user_text=f"[проверка harness] {issue}", label="chat-recheck", agent_factory=factory)
    mgr = app.managers.get(task_id)
    pending = [t for t in (mgr.tasks.values() if mgr else []) if not t.done()]
    if pending:
        print(f"\nЖду завершения фоновых субагентов перед выходом: {len(pending)}")
        await asyncio.wait(pending, timeout=3600)


def probe(app: Lab3, cfg) -> dict:
    """Six search combinations on the control queries (deterministic tool calls, no LLM)."""
    from .tools_corpus import CorpusEnv, CorpusTools
    store = TaskStore(cfg.state_dir / "state.db")
    if not store.get_task("PROBE"):
        store.create_task("PROBE", "probe", "search probe", {"root": str(cfg.root)}, {})
    ws = HarnessWorkspace(cfg.root)
    cat = Catalog(ws)
    env = CorpusEnv(ws, cat, Levels(ws, cat), app.index, app.embedder, store, "PROBE")
    ct = CorpusTools(env)
    man = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    rows = []
    for c in man["control"]:
        if c["kind"] in ("multi-hop", "new batch"):
            continue
        lvl = c["kind"].split()[0]
        if "fulltext" in c["kind"]:
            r = ct.search_fulltext(c["query"], lvl, size=5)
        elif "grep" in c["kind"]:
            r = ct.search_grep(c["pattern"], lvl, regex=c.get("regex", False))
        else:
            r = ct.search_vector(c["query"], lvl, k=5)
        paths = [h["path"].split("/")[-1] for h in r["hits"]]
        expect = c.get("expect_files") or [c["expect_file"]]
        rows.append({"id": c["id"], "kind": c["kind"], "input": c.get("query") or c.get("pattern"), "expected": expect,
                     "top_paths": paths[:5], "found": all(e in paths for e in expect), "total": r.get("total", len(r["hits"])),
                     "first_hit": r["hits"][0] if r["hits"] else None})
    out = {"rows": rows, "all_found": all(r["found"] for r in rows)}
    (cfg.results_dir).mkdir(parents=True, exist_ok=True)
    (cfg.results_dir / "search_probe.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in rows:
        print(f"{r['id']} {r['kind']:<12} {'OK  ' if r['found'] else 'MISS'} expected {r['expected']} -> {r['top_paths']}")
    store.close()
    return out


def export_corpus(cfg) -> None:
    ws = HarnessWorkspace(cfg.root)
    src = ws.service / "corpus"
    dst = cfg.results_dir / "corpus"
    if dst.exists():
        shutil.rmtree(dst)
    if not src.exists():
        print("корпус ещё не построен: нечего выгружать")
        return
    shutil.copytree(src, dst)
    if ws.catalog_path.exists():
        shutil.copy2(ws.catalog_path, dst / "catalog.json")
    # the organised source texts as well, so that L2 fragments (lines) can be opened from the repository alone
    shutil.copytree(ws.data, dst / "data")
    tree = sorted(p.relative_to(ws.root).as_posix() for p in ws.data.rglob("*") if p.is_file())
    (dst / "data_tree.txt").write_text("\n".join(tree) + "\n", encoding="utf-8")
    # frozen demonstration manifest: document_id, path, title, content hash (+ L1/L2 state) of every corpus document
    l0 = json.loads((src / "l0.json").read_text(encoding="utf-8"))
    frozen = {"corpus_id": l0["corpus_id"], "frozen_at": l0["generated_at"], "totals": l0["totals"],
              "documents": [{k: d.get(k) for k in ("document_id", "path", "title", "content_hash", "l1", "l2_fragments")}
                            for d in l0["documents"]]}
    (CORPUS / "manifest_frozen.json").write_text(json.dumps(frozen, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"L0/L1/L2 + catalog -> {dst}; frozen manifest -> {CORPUS / 'manifest_frozen.json'}")


async def amain(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="navsearch")
    ap.add_argument("--env", type=Path)
    ap.add_argument("--no-mcp", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("prepare", "ingest", "add-new-files", "chat", "subagent", "resume", "status", "pause", "cancel",
                 "limits", "report", "coverage", "validate", "probe", "export", "list"):
        p = sub.add_parser(name)
        if name in ("chat", "resume", "status", "pause", "cancel", "limits", "report", "coverage"):
            p.add_argument("--task-id", required=name != "chat", default="M1")
        if name in ("ingest", "resume", "limits", "subagent", "chat"):
            p.add_argument("--set", nargs="*", default=[])
        if name == "prepare":
            p.add_argument("--reset", action="store_true")
        if name == "add-new-files":
            p.add_argument("--batch", default="new_batch", help="corpus/<batch> to copy (new_batch, new_batch2)")
            p.add_argument("--to", default="input/new_files", help="target folder inside the task root")
        if name == "ingest":
            p.add_argument("--folder", required=True, help="folder with new files inside the task root, e.g. input or input/new_files")
            p.add_argument("--task-id")
        if name == "chat":
            p.add_argument("--script", type=Path)
        if name == "subagent":
            p.add_argument("--kind", choices=["deep", "exhaustive"], required=True)
            p.add_argument("--request", help="natural-language task for the sub-agent")
            p.add_argument("--request-file", type=Path, help="UTF-8 file with the request (for shells without UTF-8)")
            p.add_argument("--scope")
            p.add_argument("--task-id")
            p.add_argument("--parent", default="CLI")
        if name == "status":
            p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    cfg = load(args.env)

    if args.cmd == "prepare":
        prepare(cfg, args.reset)
        return 0
    if args.cmd == "add-new-files":
        add_new_files(cfg, args.batch, args.to)
        return 0
    if args.cmd in ("status", "pause", "cancel", "list", "report", "coverage"):
        store = TaskStore(cfg.state_dir / "state.db")
        try:
            if args.cmd == "list":
                for t in store.list_tasks():
                    print(f"{t['task_id']:<28} {t['kind']:<10} {t['status']:<10} runs={t['runs']} {t['status_reason'] or ''}"[:200])
                return 0
            if not store.get_task(args.task_id):
                print(f"нет задачи {args.task_id}")
                return 1
            if args.cmd == "status":
                return print_status(store, args.task_id, args.json)
            if args.cmd == "report":
                p = cfg.state_dir / "tasks" / args.task_id / "report.md"
                print(p.read_text(encoding="utf-8") if p.exists() else "отчёта ещё нет")
                return 0
            if args.cmd == "coverage":
                app = Lab3(cfg, CorpusIndex(), Embedder())
                app.runner = type("R", (), {"store": store})()
                try:
                    print(json.dumps(app.coverage_of(args.task_id), ensure_ascii=False, indent=2))
                except Exception as e:  # noqa: BLE001 - area not defined yet
                    print(f"покрытия пока нет: {e}")
                return 0
            store.request_control(args.task_id, args.cmd, "")
            if not store.alive(args.task_id):
                store.clear_control(args.task_id)
                store.set_status(args.task_id, "cancelled" if args.cmd == "cancel" else "paused", f"{args.cmd} by the user")
                print(f"{args.cmd}: процесс не запущен, статус изменён сразу")
            else:
                print(f"{args.cmd}: запрос передан работающему процессу")
            return 0
        finally:
            store.close()

    app = Lab3(cfg, CorpusIndex(), Embedder())
    if args.cmd == "validate":
        ws = HarnessWorkspace(cfg.root)
        app.index.refresh()
        res = Levels(ws, Catalog(ws)).validate(app.index.counts_by_document())
        res["es_counts"] = {"L1": app.index.count("L1"), "L2": app.index.count("L2")}
        cfg.results_dir.mkdir(parents=True, exist_ok=True)
        (cfg.results_dir / "validation.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["ok"] else 4
    if args.cmd == "probe":
        return 0 if probe(app, cfg)["all_found"] else 5
    if args.cmd == "export":
        export_corpus(cfg)
        return 0

    async with TaskRunner(cfg, verbose=not args.quiet, use_mcp=not args.no_mcp) as runner:
        app.runner = runner
        if args.cmd == "ingest":
            folder = args.folder.strip().strip("/").replace("\\", "/")
            HarnessWorkspace(cfg.root).resolve(folder, must_exist=True)          # must be inside the task root
            tid = args.task_id or f"ING-{folder.replace('/', '_')}-{datetime.now().strftime('%m%d%H%M')}"
            dups = skip_known_content(cfg, folder) if not runner.store.get_task(tid) else []
            request = INGEST_REQUEST.format(folder=folder)
            if dups:
                request += (" Уже были в корпусе с тем же содержимым и убраны harness из входной папки (не индексируй их): "
                            + "; ".join(f"{d['file']} = {d['existing']}" for d in dups) + ".")
            ensure_task(runner, tid, "ingest", request, {"ingest_folder": folder, "skipped_duplicates": dups}, _kv(args.set))
            if dups:
                runner.store.event(tid, "duplicates_skipped", {"files": dups})
            res = await runner.run(tid, label="ingest", agent_factory=app.factory("ingest"))
            return 0 if res.get("status") in ("completed", "paused") else 2
        if args.cmd == "resume":
            t = runner.store.get_task(args.task_id)
            if not t:
                print(f"нет задачи {args.task_id}")
                return 1
            text = None
            if t["kind"] == "exhaustive":
                try:
                    cov = app.coverage_of(args.task_id)
                except Exception:  # noqa: BLE001 - area not defined yet
                    cov = None
                if cov and not cov.get("complete") and cov.get("resume_cursor"):
                    # the stored boundary of the registry, not whatever the restored history happens to contain
                    text = ("[harness: resume] Процесс перезапущен. Реестр покрытия: прочитано "
                            f"{cov['fragments_read']} из {cov['fragments_total']} фрагментов. Продолжи обход с границы "
                            f"реестра: area_next(cursor=\"{cov['resume_cursor']}\").")
            res = await runner.run(args.task_id, user_text=text, overrides=_kv(args.set), label="resume",
                                   agent_factory=app.factory(t["kind"]))
            return 0 if res.get("status") in ("completed", "paused", "cancelled") else 2
        if args.cmd == "limits":
            if args.set:
                runner.set_limits(args.task_id, _kv(args.set))
            print(json.dumps(runner.store.get_task(args.task_id)["limits"], ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "chat":
            ensure_task(runner, args.task_id, "main", "Диалог пользователя с основным агентом по корпусу.", {}, _kv(args.set))
            await chat(app, args.task_id, args.script)
            return 0
        if args.cmd == "subagent":
            from .subagents import DEFAULT_SUB_LIMITS
            if args.request_file:
                args.request = args.request_file.read_text(encoding="utf-8").strip()
            if not args.request:
                print("нужен --request или --request-file")
                return 1
            from harness.config import Limits
            ensure_task(runner, args.parent, "main", "Родительская задача для вызова субагентов из CLI.", {}, {})
            tid = args.task_id or f"{args.parent}-{args.kind}-{uuid.uuid4().hex[:4]}"
            if not runner.store.get_task(tid):
                parent = runner.store.get_task(args.parent)
                limits = Limits.from_dict(parent["limits"]).updated(DEFAULT_SUB_LIMITS[args.kind]).updated(_kv(args.set))
                runner.store.create_task(tid, args.kind, args.request,
                                         {"task_id": tid, "kind": args.kind, "request": args.request, "parent_task_id": args.parent,
                                          "scope_folder": args.scope, "scope": [], "root": parent["spec"]["root"],
                                          "constraints": [], "criteria": {}}, limits.to_dict())
                runner.store.event(args.parent, "subtask_started", {"task_id": tid, "kind": args.kind, "request": args.request,
                                                                     "mode": "blocking (CLI)"})
            res = await runner.run(tid, label=args.kind, agent_factory=app.factory(args.kind))
            print(f"[{tid}] {res.get('status')}")
            return 0
    return 0


def main() -> None:
    try:
        sys.exit(asyncio.run(amain()))
    except KeyboardInterrupt:
        print("\nпрервано (Ctrl+C); продолжить: python -m navsearch.cli resume --task-id <id>")
        sys.exit(130)


if __name__ == "__main__":
    main()
