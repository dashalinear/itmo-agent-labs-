"""Subprocess helper for test_resume: runs task R with a PolicyModel and HARNESS_CRASH_AT.

    python tests/crash_task.py <task_root> <results_dir> <traces_dir> <fixture_base_url> <crash_at> [create]
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
        [tc("search_text", {"query": "АС-01", "path": "data/search"})],
        # step 2: two side effects in one step; the crash happens after both finished,
        # before the checkpoint of this tools step is written
        [tc("save_evidence", {"claim": "АС-01: 12 шардов", "kind": "decision", "source": HANDBOOK, "page": 4, "quote": QUOTE}),
         tc("web_fetch", {"url": f"{base}/p/checkpoint-1"})],
        [tc("write_report_section", {"section_id": "summary", "title": "Итог", "body": "АС-01, стр. 4"})],
        [tc("complete_task", {"note": "готово"})],
        "Готово.",
    ]


async def main() -> int:
    root, results, traces, base, crash_at = sys.argv[1:6]
    cfg = replace(load_config(root=Path(root)), results_dir=Path(results), traces_dir=Path(traces), context_window=32768,
                  token_count_mode="heuristic", crash_at=crash_at)
    provider = FixtureProvider(HERE.parent / "fixtures" / "web" / "serp.json", base)
    async with TaskRunner(cfg, verbose=False, use_mcp=False, model=PolicyModel(plan=plan(base)), summarizer=SummaryModel(),
                          provider=provider) as r:
        if len(sys.argv) > 6 and sys.argv[6] == "create":
            r.create({"task_id": "R", "kind": "mixed", "request": "тест перезапуска", "scope": ["data/search"],
                      "constraints": ["[C1] x"], "criteria": {"min_evidence": 1, "required_sections": ["summary"]}})
        res = await r.run("R", label="run" if crash_at else "resume")
        print("STATUS", res["status"])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
