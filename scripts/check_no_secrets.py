#!/usr/bin/env python3
"""Reject tracked credential-shaped literals and runtime environment files."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


PATTERNS = (
    re.compile(rb"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    re.compile(rb"\bgh[oprsu]_[A-Za-z0-9]{36,255}\b"),
)


def main() -> int:
    failures = []
    for raw_path in subprocess.check_output(["git", "ls-files", "-z"]).split(b"\0"):
        if not raw_path:
            continue
        path = Path(raw_path.decode())
        if path.name.startswith(".env") and path.name != ".env.example":
            failures.append(f"tracked environment file: {path}")
            continue
        try:
            content = path.read_bytes()
        except OSError:
            continue
        if any(pattern.search(content) for pattern in PATTERNS):
            failures.append(f"credential-shaped literal: {path}")
    if failures:
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
