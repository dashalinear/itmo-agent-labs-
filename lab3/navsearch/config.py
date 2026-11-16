"""Lab 3 configuration = lab 2 harness configuration with lab 3 paths and indices."""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from harness.config import HarnessConfig, load_config

from . import LAB1_ROOT, LAB2_ROOT, LAB3_ROOT

DEFAULT_LIMITS = {
    "ingest": {"max_total_tokens": 8_000_000, "max_model_calls": 450, "max_tool_calls": 2000, "task_deadline_s": 10800,
               "working_context_tokens": 28000, "compaction_trigger": 0.75, "compaction_target": 0.45},
    "main": {"max_total_tokens": 4_000_000, "max_model_calls": 300, "max_tool_calls": 1200, "task_deadline_s": 10800,
             "max_model_calls_per_run": 30,
             "working_context_tokens": 28000, "compaction_trigger": 0.75, "compaction_target": 0.45},
}


def load(env_file: str | os.PathLike | None = None) -> HarnessConfig:
    env = Path(env_file) if env_file else Path(os.getenv("HARNESS_ENV_FILE") or LAB3_ROOT / ".env")
    cfg = load_config(env, root=Path(os.getenv("LAB3_TASK_ROOT", str(LAB3_ROOT / "workspace"))))   # loads the .env first
    os.environ.setdefault("ES_PREFIX", "itmo_lab3")
    return replace(cfg, traces_dir=LAB3_ROOT / "traces", results_dir=LAB3_ROOT / "results",
                   skills_dirs=(LAB1_ROOT / "skills", LAB2_ROOT / "skills", LAB3_ROOT / "skills"),
                   base=replace(cfg.base, es_index=os.getenv("ES_INDEX", "itmo_lab3_excerpts")))
