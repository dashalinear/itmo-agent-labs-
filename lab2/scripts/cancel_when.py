"""Cancel a running task from ANOTHER process as soon as it has saved N evidence (scenario E1).

    python scripts/cancel_when.py --task-id E1 --min-evidence 2 [--timeout 900]

Polls the state DB every 0.3 s (one process, no CLI start-up per poll) and then sends the same request as
`python -m harness.cli cancel`: a control row "cancel" that the running TaskRunner picks up; the in-flight
LLM/HTTP wait is interrupted and its token reservation is released. Exit code 0 = cancel sent, 3 = the task
ended or the timeout passed before the threshold (nothing sent).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

LAB2 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB2))

import harness  # noqa: E402,F401
from harness.config import load_config  # noqa: E402
from harness.store import TaskStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--min-evidence", type=int, default=2)
    ap.add_argument("--timeout", type=float, default=900)
    args = ap.parse_args()
    cfg = load_config()
    store = TaskStore(cfg.state_dir / "state.db")
    t0 = time.monotonic()
    seen_running = False
    try:
        while time.monotonic() - t0 < args.timeout:
            task = store.get_task(args.task_id)
            if task and task["status"] == "running":
                seen_running = True
            n = len(store.evidence(args.task_id))
            if n >= args.min_evidence and task and task["status"] == "running":
                store.request_control(args.task_id, "cancel", f"demo: cancel from another process after {n} evidence")
                print(f"cancel sent to {args.task_id} after {n} evidence ({time.monotonic() - t0:.1f} s)", flush=True)
                return 0
            if seen_running and task and task["status"] != "running":
                print(f"{args.task_id} ended ({task['status']}) before {args.min_evidence} evidence", flush=True)
                return 3
            time.sleep(0.3)
        print("timeout: cancel not sent", flush=True)
        return 3
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
