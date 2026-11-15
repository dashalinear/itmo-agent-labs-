"""Harness configuration: user limits (policy) and runtime settings.

The limit policy comes ONLY from the user: .env defaults, the task spec and
explicit CLI ``--set key=value``. It is stored per task in the state database;
document text and web pages have no code path that can change it.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from . import LAB1_ROOT, LAB2_ROOT
from organizer.config import Settings, load_settings  # noqa: E402  (lab 1)


@dataclass
class Limits:
    # --- total budget (persists across restarts, never reset by resume)
    max_total_tokens: int = 600_000      # LLM input+output incl. compaction and failed calls
    max_model_calls: int = 150           # agent model calls (compaction calls counted separately)
    max_tool_calls: int = 500
    task_deadline_s: float = 5400.0      # cumulative active time of the task
    # --- web budget
    max_search_calls: int = 12           # calls of the external search service
    max_http_requests: int = 80          # real outgoing HTTP requests of web_fetch incl. retries and redirects
    max_fetch_bytes: int = 1_000_000     # per response
    max_redirects: int = 5
    http_timeout_s: float = 10.0
    http_retries: int = 2                # extra attempts after the first one
    backoff_base_s: float = 0.5          # exponential backoff: base * 2^attempt
    # --- context window
    working_context_tokens: int = 16_000 # working limit <= real model window
    response_reserve_tokens: int = 2048  # = max_tokens of every agent call
    estimate_margin: float = 0.10        # safety margin on the token estimate
    compaction_trigger: float = 0.70     # compact when input+reserve > trigger * working limit
    compaction_target: float = 0.45      # keep the recent tail within target * working limit
    compaction_min_gain: float = 0.05    # compact only if >= this share of the working limit can be removed
    summary_max_tokens: int = 1200
    summary_tool_clip_chars: int = 1200  # how much of each tool result the summarizer sees
    max_compaction_chunks: int = 4       # LLM chunks per compaction, then rule-based fallback
    # --- tool outputs
    max_tool_result_chars: int = 6000    # single result handed to the model
    max_step_result_chars: int = 12000   # all results of one model step together
    preview_chars: int = 1200            # preview of an offloaded result
    max_pages_per_read: int = 5
    max_lines_per_read: int = 300
    # --- time / retries
    llm_timeout_s: float = 180.0
    llm_retries: int = 2
    tool_timeout_s: float = 60.0

    @classmethod
    def from_env(cls) -> "Limits":
        values = {}
        for f in fields(cls):
            raw = os.getenv("LIMIT_" + f.name.upper())
            if raw not in (None, ""):
                values[f.name] = _coerce(f.type, raw)
        return cls(**values)

    def updated(self, overrides: dict[str, Any]) -> "Limits":
        known = {f.name: f.type for f in fields(self)}
        clean = {}
        for k, v in overrides.items():
            if k not in known:
                raise KeyError(f"unknown limit '{k}'. Known: {', '.join(known)}")
            clean[k] = _coerce(known[k], v) if isinstance(v, str) else v
        return replace(self, **clean)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Limits":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


def _coerce(type_: Any, raw: str):
    t = type_ if isinstance(type_, str) else getattr(type_, "__name__", str(type_))
    if "int" in t:
        return int(float(raw))
    if "float" in t:
        return float(raw)
    return raw


@dataclass(frozen=True)
class HarnessConfig:
    base: Settings                         # lab 1 settings: LLM endpoint, ES, skills
    root: Path                             # task root (current working directory of tasks)
    traces_dir: Path
    results_dir: Path
    skills_dirs: tuple[Path, ...]
    context_window: int | None             # fallback if the server does not report max_model_len
    token_count_mode: str = "auto"         # auto | server | heuristic
    chars_per_token: float = 2.6           # initial heuristic before calibration by usage
    search_provider: str = "ddgs"          # ddgs | fixture
    search_backend: str = "bing,yahoo,duckduckgo"  # ddgs backends tried in order
    fixture_base_url: str = "http://127.0.0.1:8765"
    serp_path: Path = field(default_factory=lambda: LAB2_ROOT / "fixtures" / "web" / "serp.json")
    crash_at: str = ""                     # test hook, e.g. "tool:12" -> os._exit before the 12th tool call
    limits: Limits = field(default_factory=Limits)

    @property
    def state_dir(self) -> Path:
        return self.root / ".harness"

    @property
    def window_cache(self) -> Path:
        """Measured context window (per base_url + model); outside the task root, git-ignored."""
        return LAB2_ROOT / ".cache" / "window.json"


def load_config(env_file: str | os.PathLike | None = None, root: Path | None = None) -> HarnessConfig:
    env = Path(env_file) if env_file else LAB2_ROOT / ".env"
    load_dotenv(env, override=False)
    base = load_settings(env)
    ws = root or Path(os.getenv("TASK_ROOT", str(LAB2_ROOT / "workspace")))
    if not ws.is_absolute():
        ws = LAB2_ROOT / ws
    base = replace(base, workspace=ws, skills_dir=LAB1_ROOT / "skills",
                   traces_dir=LAB2_ROOT / "traces",
                   es_index=os.getenv("ES_INDEX", "itmo_lab2_excerpts"))
    cw = os.getenv("LLM_CONTEXT_WINDOW")
    return HarnessConfig(
        base=base, root=ws,
        traces_dir=Path(os.getenv("TRACES_DIR", str(LAB2_ROOT / "traces"))),
        results_dir=Path(os.getenv("RESULTS_DIR", str(LAB2_ROOT / "results"))),
        skills_dirs=(LAB1_ROOT / "skills", LAB2_ROOT / "skills"),
        context_window=int(cw) if cw else None,
        token_count_mode=os.getenv("TOKEN_COUNT_MODE", "auto").lower(),
        chars_per_token=float(os.getenv("CHARS_PER_TOKEN", "2.6")),
        search_provider=os.getenv("WEB_SEARCH_PROVIDER", "ddgs").lower(),
        search_backend=os.getenv("WEB_SEARCH_BACKEND", "bing,yahoo,duckduckgo"),
        fixture_base_url=os.getenv("FIXTURE_BASE_URL", "http://127.0.0.1:8765"),
        crash_at=os.getenv("HARNESS_CRASH_AT", ""),
        limits=Limits.from_env(),
    )
