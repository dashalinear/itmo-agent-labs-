"""Cancel (or pause) a running task from ANOTHER process as soon as it has saved N evidence (scenarios E1, P1).

    python scripts/cancel_when.py --task-id E1 --min-evidence 2 [--timeout 900]
    python scripts/cancel_when.py --task-id P1 --min-evidence 1 --action wait   # then: python -m harness.cli pause

Polls the state DB every 0.3 s (one process, no CLI start-up per poll) and then sends the same request as
`python -m harness.cli cancel` / `pause`: a control row that the running TaskRunner picks up. cancel interrupts
the in-flight LLM/HTTP wait and releases its token reservation; pause takes effect at the next step boundary
(status paused, resumable). Exit code 0 = request sent, 3 = the task ended or the timeout passed first.
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
    ap.add_argument("--action", choices=["cancel", "pause", "wait"], default="cancel",
                    help="wait = only wait for the threshold (the caller then uses the CLI: harness.cli pause/cancel)")
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
                if args.action != "wait":
                    store.request_control(args.task_id, args.action, f"demo: {args.action} from another process after {n} evidence")
                print(f"{args.task_id}: {n} evidence saved ({time.monotonic() - t0:.1f} s)"
                      + ("" if args.action == "wait" else f"; {args.action} sent"), flush=True)
                return 0
            if seen_running and task and task["status"] != "running":
                print(f"{args.task_id} ended ({task['status']}) before {args.min_evidence} evidence", flush=True)
                return 3
            time.sleep(0.3)
        print(f"timeout: {args.action} not sent", flush=True)
        return 3
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
