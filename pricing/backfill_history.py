#!/usr/bin/env python3
"""One-off: rebuilds data/history/ from every past commit of data/prices.json."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import history  # noqa: E402

ROOT = HERE.parent


def main() -> int:
    commits = subprocess.run(
        ["git", "log", "--reverse", "--format=%H", "--", "data/prices.json"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    for sha in commits:
        raw = subprocess.run(["git", "show", f"{sha}:data/prices.json"], cwd=ROOT, capture_output=True, text=True)
        if raw.returncode != 0:
            continue
        try:
            items = json.loads(raw.stdout).get("items", {})
        except json.JSONDecodeError:
            continue
        changed = history.update(ROOT / "data" / "history", items)
        print(f"{sha[:8]}: {changed} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
