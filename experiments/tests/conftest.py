"""Resolve the curated package and its numbered experiment entry points."""

import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
for folder in (
    PACKAGE_ROOT / "algorithm",
    PACKAGE_ROOT / "experiments" / "common",
    PACKAGE_ROOT / "experiments" / "source",
):
    sys.path.insert(0, str(folder))
