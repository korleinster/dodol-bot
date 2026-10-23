"""Small, local-only runtime-health contract for container supervision.

The health file is deliberately not an HTTP endpoint and contains only
operational state.  It lets Docker test the actual application state without
giving a bot container access to the Docker daemon.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any


HEALTH_FILE_ENV = "BOT_HEALTH_PATH"
DEFAULT_HEALTH_FILE = "/tmp/dodol-bot-health.json"
RUNTIME_HEALTH_DIR_ENV = "BOT_RUNTIME_HEALTH_DIR"
RUNTIME_HEALTH_GID_ENV = "BOT_RUNTIME_HEALTH_GID"
VOICE_STATES = frozenset({"unconfigured", "connecting", "connected", "recovering", "unavailable"})
SCHEDULER_READY = "ready"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_timestamp(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def initialize_runtime_health(bot: Any) -> None:
    """Attach a non-secret health state to a bot before it connects."""
    bot.runtime_health = {
        "discord": "starting",
        "voice": {
            "state": "unconfigured",
            "configuredTargets": 0,
            "connectedTargets": 0,
            "lastErrorCode": None,
            "nextRetryAt": None,
            "updatedAt": _now_ms(),
            "targets": {},
        },
    }


def _state(bot: Any) -> dict[str, Any]:
    raw = getattr(bot, "runtime_health", None)
    if not isinstance(raw, dict):
        initialize_runtime_health(bot)
        raw = bot.runtime_health
    return raw


def mark_discord_health(bot: Any, ready: bool) -> None:
    _state(bot)["discord"] = "ready" if ready else "stopped"


def update_voice_health(
    bot: Any,
    guild_id: int,
    *,
    configured: bool,
    state: str,
    error_code: str | None = None,
    next_retry_at: int | None = None,
) -> None:
    """Record observed voice state, never a desired channel as a proxy.

    Only state derived from an actual VoiceClient check is accepted as
    ``connected``.  Guild IDs are kept in-process for aggregation but omitted
    from the public bridge payload.
    """
    if state not in VOICE_STATES:
        state = "unavailable"
        error_code = "VOICE_STATUS_UNKNOWN"
    if not configured:
        state = "unconfigured"
        error_code = None
        next_retry_at = None

    voice = _state(bot).setdefault("voice", {})
    targets = voice.setdefault("targets", {})
    targets[str(guild_id)] = {
        "configured": bool(configured),
        "state": state,
        "lastErrorCode": error_code if isinstance(error_code, str) else None,
        "nextRetryAt": _safe_timestamp(next_retry_at),
        "updatedAt": _now_ms(),
    }

    configured_targets = [item for item in targets.values() if item.get("configured")]
    connected_targets = [item for item in configured_targets if item.get("state") == "connected"]
    if not configured_targets:
        aggregate_state = "unconfigured"
        aggregate_error = None
    elif len(connected_targets) == len(configured_targets):
        aggregate_state = "connected"
        aggregate_error = None
    elif any(item.get("state") == "recovering" for item in configured_targets):
        aggregate_state = "recovering"
        aggregate_error = next(
            (item.get("lastErrorCode") for item in configured_targets if item.get("lastErrorCode")),
            None,
        )
    elif any(item.get("state") == "connecting" for item in configured_targets):
        aggregate_state = "connecting"
        aggregate_error = next(
            (item.get("lastErrorCode") for item in configured_targets if item.get("lastErrorCode")),
            None,
        )
    else:
        aggregate_state = "unavailable"
        aggregate_error = next(
            (item.get("lastErrorCode") for item in configured_targets if item.get("lastErrorCode")),
            "VOICE_CONNECTION_UNHEALTHY",
        )
    next_retry = min(
        (
            value for item in configured_targets
            if (value := _safe_timestamp(item.get("nextRetryAt"))) is not None
        ),
        default=None,
    )

    voice.update({
        "state": aggregate_state,
        "configuredTargets": len(configured_targets),
        "connectedTargets": len(connected_targets),
        "lastErrorCode": aggregate_error,
        "nextRetryAt": next_retry,
        "updatedAt": _now_ms(),
    })


def runtime_health_payload(bot: Any) -> dict[str, Any]:
    """Return a conservative, detail-free payload for trusted observability."""
    state = _state(bot)
    voice = state.get("voice") if isinstance(state.get("voice"), dict) else {}
    configured = bool(voice.get("configuredTargets", 0))
    status = voice.get("state")
    if not configured:
        # Keep the shared contract coherent even if an in-process caller left
        # malformed state behind: no target can only be ``unconfigured``.
        status = "unconfigured"
        error_code = None
        next_retry_at = None
    else:
        if status not in VOICE_STATES or status == "unconfigured":
            status = "unavailable"
        error_code = voice.get("lastErrorCode")
        if not isinstance(error_code, str) or not error_code.startswith("VOICE_"):
            error_code = None
        next_retry_at = _safe_timestamp(voice.get("nextRetryAt"))
    return {
        "discord": "ready" if state.get("discord") == "ready" else "starting",
        "voice": {
            "configured": configured,
            "connected": status == "connected",
            "state": status,
            "lastErrorCode": error_code,
            "nextRetryAt": next_retry_at,
        },
    }


def voice_target_health_payload(
    bot: Any,
    guild_id: int,
    *,
    configured: bool,
) -> dict[str, Any]:
    """Return one guild's safe voice state without leaking its identifier.

    The aggregate runtime state is useful for container supervision, but it
    must never be copied to every bridge target: one connected guild cannot
    make another unconfigured guild look connected.
    """
    voice = _state(bot).get("voice")
    targets = voice.get("targets") if isinstance(voice, dict) else None
    target = targets.get(str(guild_id)) if isinstance(targets, dict) else None
    if not configured:
        return {
            "configured": False,
            "connected": False,
            "state": "unconfigured",
            "lastErrorCode": None,
            "nextRetryAt": None,
        }
    if not isinstance(target, dict):
        return {
            "configured": True,
            "connected": False,
            "state": "connecting",
            "lastErrorCode": None,
            "nextRetryAt": None,
        }
    state = target.get("state")
    if state not in {"connecting", "connected", "recovering", "unavailable"}:
        state = "unavailable"
    error_code = target.get("lastErrorCode")
    if not isinstance(error_code, str) or not error_code.startswith("VOICE_"):
        error_code = None
    return {
        "configured": True,
        "connected": state == "connected",
        "state": state,
        "lastErrorCode": error_code,
        "nextRetryAt": _safe_timestamp(target.get("nextRetryAt")),
    }


def application_health_payload(bot: Any) -> dict[str, Any]:
    """Build the file contract consumed by ``python -m src.health_probe``."""
    runtime = runtime_health_payload(bot)
    scheduler = getattr(bot, "scheduler_health", {})
    scheduler_status = scheduler.get("status") if isinstance(scheduler, dict) else None
    healthy = (
        runtime["discord"] == "ready"
        and scheduler_status == SCHEDULER_READY
        and runtime["voice"]["state"] in {"connected", "unconfigured"}
    )
    return {
        "version": 1,
        "status": "ready" if healthy else "starting",
        "updatedAt": _now_ms(),
        "runtime": runtime,
        "schedulerStatus": scheduler_status if isinstance(scheduler_status, str) else "starting",
    }


def health_file_path() -> Path:
    return Path(os.getenv(HEALTH_FILE_ENV, DEFAULT_HEALTH_FILE))


def host_visible_health_path(bot: Any) -> Path | None:
    """Select only the bot's own fixed file in the explicitly mounted data dir."""
    directory = os.getenv(RUNTIME_HEALTH_DIR_ENV, "")
    number = getattr(bot, "bot_number", None)
    if not directory or type(number) is not int or number not in {1, 2, 3, 4}:
        return None
    return Path(directory) / f"runtime-health-{number:03d}.json"


def host_visible_health_payload(bot: Any) -> dict[str, Any]:
    """Minimal, non-secret observer input; freshness is based on observedAt."""
    scheduler = getattr(bot, "scheduler_health", None)
    scheduler = scheduler if isinstance(scheduler, dict) else {}
    status = scheduler.get("status")
    if status not in {"ready", "starting", "failed"}:
        status = "starting"
    runtime = runtime_health_payload(bot)
    return {
        "version": 1,
        "botNumber": bot.bot_number,
        "observedAt": _now_ms(),
        "discord": runtime["discord"],
        "schedulerStatus": status,
        "schedulerLastTickAt": _safe_timestamp(scheduler.get("lastTickAt")),
        "status": application_health_payload(bot)["status"],
    }


def host_visible_health_gid() -> int | None:
    """Require an explicit, canonical positive group ID for the host mirror."""
    raw = os.getenv(RUNTIME_HEALTH_GID_ENV, "")
    if not re.fullmatch(r"[1-9][0-9]{0,9}", raw):
        return None
    value = int(raw)
    return value if value < 2**32 - 1 else None


def _atomic_write(path: Path, payload: dict[str, Any], *, mirror_gid: int | None = None) -> None:
    """Publish a new inode; never open a pre-existing temporary path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = f".{path.name}.{os.getpid()}.{secrets.token_hex(16)}.tmp"
    descriptor = None
    own_inode = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        own_stat = os.fstat(descriptor)
        own_inode = (own_stat.st_dev, own_stat.st_ino)
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        while data:
            count = os.write(descriptor, data)
            if count <= 0:
                raise OSError("health file write failed")
            data = data[count:]
        if mirror_gid is not None:
            os.fchown(descriptor, 0, mirror_gid)
            os.fchmod(descriptor, 0o640)
        else:
            os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        completed_descriptor = descriptor
        descriptor = None
        os.close(completed_descriptor)
        # The shared data directory is outside this file's integrity contract,
        # but do not knowingly publish a swapped temporary inode.
        current = os.stat(temporary, dir_fd=directory_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != own_inode:
            raise OSError("health temporary file changed")
        os.replace(temporary, path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
    finally:
        try:
            if descriptor is not None:
                os.close(descriptor)
        finally:
            try:
                if own_inode is not None:
                    try:
                        current = os.stat(temporary, dir_fd=directory_fd, follow_symlinks=False)
                        if (current.st_dev, current.st_ino) == own_inode:
                            os.unlink(temporary, dir_fd=directory_fd)
                    except FileNotFoundError:
                        pass
            finally:
                os.close(directory_fd)


def write_health_file(bot: Any) -> None:
    """Atomically publish a tiny health snapshot; errors leave the probe unhealthy."""
    path = health_file_path()
    payload = application_health_payload(bot)
    try:
        _atomic_write(path, payload)
    except OSError:
        # Health publication failure must not take down bot commands. Docker
        # sees the missing/stale file and marks the process unhealthy instead.
        return
    mirror = host_visible_health_path(bot)
    if mirror is not None:
        gid = host_visible_health_gid()
        if gid is None:
            return
        try:
            _atomic_write(mirror, host_visible_health_payload(bot), mirror_gid=gid)
        except (OSError, OverflowError):
            # Local Docker probe and bot execution must not depend on the
            # separate host observer's mount permissions.
            pass
