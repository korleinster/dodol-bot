"""Docker healthcheck command for one bot process.

No network listener and no container-runtime access are required.  ``main``
atomically refreshes a file from the live Discord, scheduler, and voice state.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from src.runtime_health import health_file_path


MAX_HEALTH_AGE_SECONDS = 20


def is_healthy(path: Path, *, now_ms: int | None = None) -> bool:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(raw, dict) or raw.get("status") != "ready":
        return False
    updated_at = raw.get("updatedAt")
    if type(updated_at) is not int:
        return False
    current = int(time.time() * 1000) if now_ms is None else now_ms
    return 0 <= current - updated_at <= MAX_HEALTH_AGE_SECONDS * 1000


def main() -> int:
    return 0 if is_healthy(health_file_path()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
