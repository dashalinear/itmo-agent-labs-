"""Worker for a dry run of the whole lab 4 scenario without the ITMO LLM (the model is not checked here).

The agents get the deterministic test policies from tests/fake_llm.py instead of qwen; everything else is real:
Temporal service, Schedule, workflows, activities, harness, Elasticsearch. Use another namespace, task root and
index prefix so that the dry run never mixes with the real traces:

    $env:TEMPORAL_NAMESPACE="dryrun"; $env:LAB4_TASK_ROOT="D:\\tmp_lab4\\workspace"; $env:LAB4_ES_PREFIX="itmo_lab4_dry"
    $env:LAB4_TRACES_DIR="D:\\tmp_lab4\\traces"; $env:LAB4_RESULTS_DIR="D:\\tmp_lab4\\results"
    ..\\lab3\\.venv\\Scripts\\python.exe scripts\\dry_run_worker.py
"""
import asyncio
import sys
from pathlib import Path

LAB4 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB4))
sys.path.insert(0, str(LAB4 / "tests"))

from fake_llm import router  # noqa: E402  (no environment side effects)

from topicflow import config as lab4_config  # noqa: E402
from topicflow.activities import Env  # noqa: E402
from topicflow.worker import run_worker  # noqa: E402


def main() -> None:
    from dataclasses import replace
    # the fake model has no server to measure the window: use the window measured for qwen in labs 2-3
    cfg = replace(lab4_config.load(), context_window=262144, token_count_mode="heuristic")
    env = Env(cfg, model=router(), use_mcp=False, limit_overrides={"working_context_tokens": 120000})
    env.embedder.embed(["прогрев"])
    Env._inst = env
    print(f"[dry-run worker] FAKE LLM (test policies); crash_at={cfg.crash_at!r}", flush=True)
    asyncio.run(run_worker(lab4_config.settings(), env))


if __name__ == "__main__":
    main()
