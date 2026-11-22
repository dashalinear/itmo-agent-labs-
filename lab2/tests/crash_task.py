"""Subprocess helper for test_resume: runs task R with a PolicyModel and HARNESS_CRASH_AT.

    python tests/crash_task.py <task_root> <results_dir> <traces_dir> <fixture_base_url> <crash_at> [create] [resume] [web]

create: create task R first; resume: label the run "resume" even with a crash point;
web: the web plan (two parallel fetches, then two parallel evidence) for the crash-window test.
"""
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import harness  # noqa: E402,F401
from harness.config import load_config  # noqa: E402
from harness.runner import TaskRunner  # noqa: E402
from harness.web import FixtureProvider  # noqa: E402
from policy import PolicyModel, SummaryModel, tc  # noqa: E402

HANDBOOK = "data/search/search_engineering_handbook.md"
QUOTE = "эталонный кластер «Северный» хранит поисковый индекс в 12 первичных шардах"


def plan(base: str) -> list:
    return [
        [tc("doc_info", {"path": HANDBOOK})],
        [tc("search_text", {"query": "АС-01", "path": "data/search"}),
         tc("web_search", {"query": "чекпоинты идемпотентность"})],     # web_fetch reads only discovered URLs
        # step 2: two side effects in one step; the crash happens after both finished,
        # before the checkpoint of this tools step is written
        [tc("save_evidence", {"claim": "АС-01: 12 шардов", "kind": "decision", "source": HANDBOOK, "page": 4, "quote": QUOTE}),
         tc("web_fetch", {"url": f"{base}/p/checkpoint-1"})],
        [tc("write_report_section", {"section_id": "summary", "title": "Итог", "body": "АС-01, стр. 4"})],
        [tc("complete_task", {"note": "готово"})],
        "Готово.",
    ]


def web_plan(base: str) -> list:
    u1, u2 = f"{base}/p/checkpoint-1", f"{base}/p/idempotency-1"
    q1 = "Чекпоинт содержит значения каналов состояния, версию и список ожидающих записей узлов."
    q2 = WEB_QUOTE
    return [
        [tc("web_search", {"query": "чекпоинты идемпотентность"})],
        [tc("web_fetch", {"url": u1}), tc("web_fetch", {"url": u2})],
        [tc("save_evidence", {"claim": "чекпоинт", "kind": "fact", "source": u1, "quote": q1}),
         tc("save_evidence", {"claim": "идемпотентность", "kind": "fact", "source": u2, "quote": q2})],
        [tc("write_report_section", {"section_id": "summary", "title": "Итог", "body": "две страницы"})],
        [tc("complete_task", {"note": "готово"})],
        "Готово.",
    ]


def _first_p(slug: str) -> str:
    import re
    html = (HERE.parent / "fixtures" / "web" / "pages" / f"{slug}.html").read_text(encoding="utf-8")
    return re.findall(r"<p>(.*?)</p>", html)[0]


WEB_QUOTE = _first_p("idempotency-1")


async def main() -> int:
    root, results, traces, base, crash_at = sys.argv[1:6]
    flags = set(sys.argv[6:])
    cfg = replace(load_config(root=Path(root)), results_dir=Path(results), traces_dir=Path(traces), context_window=32768,
                  token_count_mode="heuristic", crash_at=crash_at)
    provider = FixtureProvider(HERE.parent / "fixtures" / "web" / "serp.json", base)
    steps = web_plan(base) if "web" in flags else plan(base)
    async with TaskRunner(cfg, verbose=False, use_mcp=False, model=PolicyModel(plan=steps), summarizer=SummaryModel(),
                          provider=provider) as r:
        if "create" in flags:
            r.create({"task_id": "R", "kind": "mixed", "request": "тест перезапуска", "scope": ["data/search"],
                      "constraints": ["[C1] x"],
                      "criteria": {"min_evidence": 2 if "web" in flags else 1, "required_sections": ["summary"]}})
        res = await r.run("R", label="resume" if ("resume" in flags or not crash_at) else "run")
        print("STATUS", res["status"])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
