"""Lab 4 configuration: the lab 3 harness configuration with lab 4 paths/indices + Temporal settings.

Everything comes from environment variables (lab4/.env, falling back to lab3/.env for the LLM token, which
stays only in git-ignored files).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path

from ._paths import LAB3_ROOT, LAB4_ROOT

TOPICS_LIMITS = {"max_total_tokens": 3_000_000, "max_model_calls": 160, "max_tool_calls": 900, "task_deadline_s": 5400,
                 "max_model_calls_per_run": 160, "working_context_tokens": 28000, "compaction_trigger": 0.75,
                 "compaction_target": 0.45}
INGEST_LIMITS = {"max_total_tokens": 4_000_000, "max_model_calls": 250, "max_tool_calls": 1500, "task_deadline_s": 5400,
                 "working_context_tokens": 28000, "compaction_trigger": 0.75, "compaction_target": 0.45}


def _duration(text: str) -> timedelta:
    m = re.fullmatch(r"\s*(\d+)\s*([smh])\s*", text or "")
    if not m:
        raise ValueError(f"bad duration {text!r}; use e.g. 90s, 10m, 1h")
    n, unit = int(m.group(1)), m.group(2)
    return timedelta(seconds=n * {"s": 1, "m": 60, "h": 3600}[unit])


@dataclass(frozen=True)
class Settings:
    temporal_address: str
    namespace: str
    ingest_queue: str
    topics_queue: str
    schedule_id: str
    topics_every: timedelta
    watch_interval_s: float
    stable_s: float
    agent_heartbeat_timeout: timedelta


def env_file() -> Path:
    own = LAB4_ROOT / ".env"
    return own if own.exists() else LAB3_ROOT / ".env"


def settings() -> Settings:
    return Settings(
        temporal_address=os.getenv("TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
        ingest_queue=os.getenv("LAB4_INGEST_QUEUE", "lab4-ingest"),
        topics_queue=os.getenv("LAB4_TOPICS_QUEUE", "lab4-topics"),
        schedule_id=os.getenv("LAB4_SCHEDULE_ID", "lab4-topics"),
        topics_every=_duration(os.getenv("LAB4_TOPICS_EVERY", "10m")),
        watch_interval_s=float(os.getenv("LAB4_WATCH_INTERVAL_S", "2")),
        stable_s=float(os.getenv("LAB4_STABLE_S", "3")),
        agent_heartbeat_timeout=_duration(os.getenv("LAB4_HEARTBEAT_TIMEOUT", "90s")),
    )


def load(env: str | os.PathLike | None = None):
    """HarnessConfig for the lab 4 task root (lab4/workspace) and the lab 4 Elasticsearch indices."""
    os.environ["ES_PREFIX"] = os.getenv("LAB4_ES_PREFIX", "itmo_lab4")
    os.environ["ES_INDEX"] = os.getenv("LAB4_ES_INDEX", "itmo_lab4_excerpts")
    os.environ["LAB3_TASK_ROOT"] = os.getenv("LAB4_TASK_ROOT", str(LAB4_ROOT / "workspace"))
    import navsearch  # noqa: F401  (registers labs 1-2 on sys.path)
    from navsearch.config import load as lab3_load
    cfg = lab3_load(env or env_file())
    cache = Path(os.getenv("EMBED_CACHE_DIR", ".cache/fastembed"))
    if not cache.is_absolute():                     # the lab 3 .env value is relative: share lab 3's downloaded model
        os.environ["EMBED_CACHE_DIR"] = str(LAB3_ROOT / cache)
    return replace(cfg, traces_dir=Path(os.getenv("LAB4_TRACES_DIR", str(LAB4_ROOT / "traces"))),
                   results_dir=Path(os.getenv("LAB4_RESULTS_DIR", str(LAB4_ROOT / "results"))),
                   skills_dirs=cfg.skills_dirs + (LAB4_ROOT / "skills",))
