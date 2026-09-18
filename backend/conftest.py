"""Pytest path setup.

Several test modules use bare imports (`from confidence import ...`,
`import learner as ml`) that only resolved when pytest happened to be invoked
from inside the package directory. `engine/` and `engine/ml/` both contain
`__init__.py`, so pytest inserts the *backend* root on sys.path instead of the
package dir and those imports fail at collection time.

Putting the package dirs on sys.path here makes `pytest` work from the repo
root, from `backend/`, or from any subdirectory.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

for sub in ("", "engine", "engine/ml", "delivery", "data", "execution", "backtest"):
    path = ROOT / sub if sub else ROOT
    if path.is_dir():
        p = str(path)
        if p not in sys.path:
            sys.path.insert(0, p)
