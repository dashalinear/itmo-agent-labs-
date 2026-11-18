"""Runtime configuration loaded from environment variables (.env is supported).

Secrets (LLM_API_KEY) are read only from the environment and never written
to traces or logs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # LLM (OpenAI-compatible endpoint, e.g. the ITMO vLLM server)
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "qwen3.8-27b"
    llm_temperature: float = 0.2
    llm_timeout_s: float = 120.0
    llm_max_retries: int = 2
    llm_max_tokens: int = 4096
    llm_enable_thinking: str = "auto"  # auto | true | false

    # Workspace
    workspace: Path = field(default_factory=lambda: PROJECT_ROOT / "workspace")
    skills_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "skills")
    traces_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "traces")

    # Agent limits
    recursion_limit: int = 110         # LangGraph super-steps per user turn; backstop, > 4 * model_calls_per_turn
    model_calls_per_turn: int = 25     # ModelCallLimitMiddleware run_limit
    tool_timeout_s: float = 30.0       # per tool call
    turn_timeout_s: float = 600.0      # whole user turn
    max_tool_output_chars: int = 12000 # per tool result handed to the model

    # web_fetch
    web_timeout_s: float = 10.0
    web_max_bytes: int = 500_000
    web_max_redirects: int = 5

    # Elasticsearch / MCP
    es_url: str = "http://localhost:9200"
    es_index: str = "itmo_lab1_excerpts"
    es_timeout_s: float = 10.0
    mcp_command: str = ""  # empty -> current python interpreter


def load_settings(env_file: str | os.PathLike | None = None) -> Settings:
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)
    ws = Path(os.getenv("WORKSPACE_DIR", str(PROJECT_ROOT / "workspace")))
    if not ws.is_absolute():
        ws = PROJECT_ROOT / ws
    return Settings(
        llm_base_url=os.getenv("LLM_BASE_URL", ""),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_model=os.getenv("LLM_MODEL", "qwen3.8-27b"),
        llm_temperature=_env_float("LLM_TEMPERATURE", 0.2),
        llm_timeout_s=_env_float("LLM_TIMEOUT_S", 120.0),
        llm_max_retries=_env_int("LLM_MAX_RETRIES", 2),
        llm_max_tokens=_env_int("LLM_MAX_TOKENS", 4096),
        llm_enable_thinking=os.getenv("LLM_ENABLE_THINKING", "auto").lower(),
        workspace=ws,
        skills_dir=Path(os.getenv("SKILLS_DIR", str(PROJECT_ROOT / "skills"))),
        traces_dir=Path(os.getenv("TRACES_DIR", str(PROJECT_ROOT / "traces"))),
        recursion_limit=_env_int("AGENT_RECURSION_LIMIT", 110),
        model_calls_per_turn=_env_int("AGENT_MODEL_CALLS_PER_TURN", 25),
        tool_timeout_s=_env_float("TOOL_TIMEOUT_S", 30.0),
        turn_timeout_s=_env_float("TURN_TIMEOUT_S", 600.0),
        max_tool_output_chars=_env_int("MAX_TOOL_OUTPUT_CHARS", 12000),
        web_timeout_s=_env_float("WEB_TIMEOUT_S", 10.0),
        web_max_bytes=_env_int("WEB_MAX_BYTES", 500_000),
        web_max_redirects=_env_int("WEB_MAX_REDIRECTS", 5),
        es_url=os.getenv("ES_URL", "http://localhost:9200"),
        es_index=os.getenv("ES_INDEX", "itmo_lab1_excerpts"),
        es_timeout_s=_env_float("ES_TIMEOUT_S", 10.0),
        mcp_command=os.getenv("MCP_COMMAND", ""),
    )
