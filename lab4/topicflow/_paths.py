"""Paths of lab 4 and registration of labs 1-3 on sys.path.

Kept out of the package __init__ on purpose: the Temporal workflow sandbox imports topicflow.workflows (and so
the package __init__) in isolation, and it must not run Path.resolve() or import the lab 3 stack there.
Every non-workflow module imports this first.
"""
from __future__ import annotations

import sys
from pathlib import Path

LAB4_ROOT = Path(__file__).resolve().parent.parent
LAB3_ROOT = LAB4_ROOT.parent / "lab3"
if str(LAB3_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB3_ROOT))

import navsearch  # noqa: E402,F401  (lab 3; registers lab 2 harness and lab 1 organizer on sys.path)
