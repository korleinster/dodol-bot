"""Fail-closed policy and execution core for Telegram service control.

The Telegram adapter is deliberately thin. This module accepts only immutable
service numbers and invokes a root-owned allow-list helper with exact argv.
No Telegram text, service label, path, or callback value becomes shell input.
"""
from __future__ import annotations

import asyncio
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Literal, Mapping


CONFIRM_TTL_SECONDS = 30
SERVICE_COOLDOWN_SECONDS = 5 * 60
GLOBAL_RESTART_WINDOW_SECONDS = 10 * 60
GLOBAL_RESTART_LIMIT = 2
HEALTH_TIMEOUT_SECONDS = 90
HEALTH_POLL_SECONDS = 5
AUDIT_RETENTION_SECONDS = 90 * 24 * 60 * 60
AUDIT_MAX_EVENTS = 5_000
AUDIT_MAX_REQUESTS = 2_000


@dataclass(frozen=True)
class ControlService:
    id: int
    key: str
    display_name: str


CONTROL_SERVICES: Mapping[int, ControlService] = {
    1: ControlService(1, "dodol-bot-001", "보탐봇 001"),
    2: ControlService(2, "dodol-bot-002", "보탐봇 002"),
    3: ControlService(3, "dodol-bot-003", "보탐봇 003"),
    4: ControlService(4, "dodol-bot-004", "보탐봇 004"),
    5: ControlService(5, "leinster-center", "LeinyGames"),
}

Action = Literal["services", "status", "restart", "cancel"]


@dataclass(frozen=True)
class ParsedCommand:
    action: Action
    service_id: int | None = None


_SLASH_COMMAND = re.compile(
    r"^/(?P<action>services|status|restart|cancel)(?:@[A-Za-z0-9_]+)?(?:\s+(?P<id>[1-5]))?\s*$",
    re.IGNORECASE,
)
_KOREAN_SLASH_RESTART = re.compile(r"^/재시작\s+(?P<id>[1-5])\s*$")
_KOREAN_NATURAL_RESTART = re.compile(
    r"^(?P<id>[1-5])번(?:\s+컨테이너)?\s+재(?:시작|실행)(?:해)?\s*$",
)


def parse_control_command(text: str) -> ParsedCommand | None:
    """Parse only the documented command grammar.

    Free-form service names, extra arguments, shell punctuation, and numbers
    outside the fixed 1-5 range are rejected rather than guessed.
    """
    candidate = text.strip()
    match = _SLASH_COMMAND.fullmatch(candidate)
    if match:
        action = match.group("action").lower()
        raw_id = match.group("id")
        service_id = int(raw_id) if raw_id else None
        if action in {"status", "restart"} and service_id is None:
            return None
        if action in {"services", "cancel"} and service_id is not None:
            return None
        return ParsedCommand(action=action, service_id=service_id)  # type: ignore[arg-type]
    for pattern in (_KOREAN_SLASH_RESTART, _KOREAN_NATURAL_RESTART):
        match = pattern.fullmatch(candidate)
        if match:
            return ParsedCommand(action="restart", service_id=int(match.group("id")))
    return None


@dataclass(frozen=True)
class AccessPolicy:
    chat_id: int
    owner_user_id: int

    def allows(self, *, chat_id: int | None, user_id: int | None, chat_type: str | None) -> bool:
        return (
            chat_type == "private"
            and chat_id == self.chat_id
            and user_id == self.owner_user_id
        )


@dataclass(frozen=True)
class PendingRestart:
    request_id: str
    service_id: int
    expires_at: int


@dataclass(frozen=True)
class ConfirmationDecision:
    accepted: bool
    service_id: int | None
    error_code: str | None


class AuditStore:
    """SQLite request state plus append-only transition events."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS control_request (
              request_id TEXT PRIMARY KEY,
              service_id INTEGER NOT NULL CHECK(service_id BETWEEN 1 AND 5),
              chat_id INTEGER NOT NULL,
              user_id INTEGER NOT NULL,
              status TEXT NOT NULL CHECK(status IN
                ('pending','confirmed','running','succeeded','failed','canceled','expired','rejected')),
              created_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL,
              confirmed_at INTEGER,
              completed_at INTEGER,
              result_code TEXT
            );
            CREATE TABLE IF NOT EXISTS control_event (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              request_id TEXT,
              service_id INTEGER,
              action TEXT NOT NULL,
              state TEXT NOT NULL,
              error_code TEXT,
              created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_control_request_confirmed
              ON control_request(confirmed_at, service_id);
            """
        )
        self.invalidate_unfinished()
        self.prune()

    def close(self) -> None:
        self.db.close()

    def _event(
        self,
        *,
        request_id: str | None,
        service_id: int | None,
        action: str,
        state: str,
        error_code: str | None,
        now: int,
    ) -> None:
        self.db.execute(
            """INSERT INTO control_event
               (request_id, service_id, action, state, error_code, created_at)
               VALUES (?,?,?,?,?,?)""",
            (request_id, service_id, action, state, error_code, now),
        )
        self._prune_events(now)

    def _prune_events(self, now: int) -> None:
        cutoff = now - AUDIT_RETENTION_SECONDS
        self.db.execute("DELETE FROM control_event WHERE created_at<?", (cutoff,))
        self.db.execute(
            """DELETE FROM control_event WHERE id IN (
                 SELECT id FROM control_event ORDER BY id DESC LIMIT -1 OFFSET ?
               )""",
            (AUDIT_MAX_EVENTS,),
        )

    def record_read(self, action: str, service_id: int | None, *, now: int | None = None) -> None:
        observed = int(time.time()) if now is None else now
        self._event(
            request_id=None,
            service_id=service_id,
            action=action,
            state="observed",
            error_code=None,
            now=observed,
        )

    def invalidate_unfinished(self, *, now: int | None = None) -> None:
        """Fail closed after a controller restart; old buttons never replay."""
        observed = int(time.time()) if now is None else now
        rows = self.db.execute(
            """SELECT request_id, service_id, status FROM control_request
               WHERE status IN ('pending','confirmed','running')""",
        ).fetchall()
        for row in rows:
            code = "CONTROLLER_RESTARTED"
            status = "expired" if row["status"] == "pending" else "failed"
            self.db.execute(
                """UPDATE control_request
                   SET status=?, completed_at=?, result_code=? WHERE request_id=?""",
                (status, observed, code, row["request_id"]),
            )
            self._event(
                request_id=row["request_id"],
                service_id=int(row["service_id"]),
                action="restart",
                state=status,
                error_code=code,
                now=observed,
            )

    def prune(self, *, now: int | None = None) -> None:
        """Bound retained audit history without deleting active requests."""
        observed = int(time.time()) if now is None else now
        cutoff = observed - AUDIT_RETENTION_SECONDS
        self._prune_events(observed)
        self.db.execute(
            """DELETE FROM control_request
               WHERE completed_at IS NOT NULL AND completed_at<?""",
            (cutoff,),
        )
        self.db.execute(
            """DELETE FROM control_request WHERE request_id IN (
                 SELECT request_id FROM control_request
                 WHERE completed_at IS NOT NULL
                 ORDER BY completed_at DESC LIMIT -1 OFFSET ?
               )""",
            (AUDIT_MAX_REQUESTS,),
        )

    def create_pending(
        self,
        service_id: int,
        *,
        chat_id: int,
        user_id: int,
        now: int | None = None,
    ) -> PendingRestart:
        if service_id not in CONTROL_SERVICES:
            raise ValueError("unknown service id")
        observed = int(time.time()) if now is None else now
        self.prune(now=observed)
        request_id = secrets.token_urlsafe(12)
        expires_at = observed + CONFIRM_TTL_SECONDS
        self.db.execute("BEGIN IMMEDIATE")
        try:
            older = self.db.execute(
                """SELECT request_id, service_id, expires_at FROM control_request
                   WHERE chat_id=? AND user_id=? AND status='pending'""",
                (chat_id, user_id),
            ).fetchall()
            for row in older:
                stale = int(row["expires_at"]) <= observed
                old_status = "expired" if stale else "canceled"
                code = "CONFIRMATION_EXPIRED" if stale else "CONFIRMATION_SUPERSEDED"
                self.db.execute(
                    """UPDATE control_request
                       SET status=?, completed_at=?, result_code=? WHERE request_id=?""",
                    (old_status, observed, code, row["request_id"]),
                )
                self._event(
                    request_id=row["request_id"],
                    service_id=int(row["service_id"]),
                    action="restart",
                    state=old_status,
                    error_code=code,
                    now=observed,
                )
            self.db.execute(
                """INSERT INTO control_request
                   (request_id, service_id, chat_id, user_id, status, created_at, expires_at)
                   VALUES (?,?,?,?, 'pending', ?,?)""",
                (request_id, service_id, chat_id, user_id, observed, expires_at),
            )
            self._event(
                request_id=request_id,
                service_id=service_id,
                action="restart",
                state="pending",
                error_code=None,
                now=observed,
            )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        self.prune(now=observed)
        return PendingRestart(request_id=request_id, service_id=service_id, expires_at=expires_at)

    def cancel_latest(self, *, chat_id: int, user_id: int, now: int | None = None) -> bool:
        observed = int(time.time()) if now is None else now
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                """SELECT request_id, service_id FROM control_request
                   WHERE chat_id=? AND user_id=? AND status='pending'
                   ORDER BY created_at DESC LIMIT 1""",
                (chat_id, user_id),
            ).fetchone()
            if not row:
                self.db.execute("COMMIT")
                return False
            self.db.execute(
                "UPDATE control_request SET status='canceled', completed_at=? WHERE request_id=?",
                (observed, row["request_id"]),
            )
            self._event(
                request_id=row["request_id"],
                service_id=row["service_id"],
                action="restart",
                state="canceled",
                error_code=None,
                now=observed,
            )
            self.db.execute("COMMIT")
            return True
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def cancel(
        self,
        request_id: str,
        *,
        chat_id: int,
        user_id: int,
        now: int | None = None,
    ) -> bool:
        """Cancel only the pending request named by an inline button."""
        observed = int(time.time()) if now is None else now
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                """SELECT service_id FROM control_request
                   WHERE request_id=? AND chat_id=? AND user_id=? AND status='pending'""",
                (request_id, chat_id, user_id),
            ).fetchone()
            if not row:
                self.db.execute("COMMIT")
                return False
            updated = self.db.execute(
                """UPDATE control_request SET status='canceled', completed_at=?
                   WHERE request_id=? AND status='pending'""",
                (observed, request_id),
            )
            if updated.rowcount != 1:
                self.db.execute("ROLLBACK")
                return False
            service_id = int(row["service_id"])
            self._event(
                request_id=request_id,
                service_id=service_id,
                action="restart",
                state="canceled",
                error_code=None,
                now=observed,
            )
            self.db.execute("COMMIT")
            return True
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def confirm(
        self,
        request_id: str,
        *,
        chat_id: int,
        user_id: int,
        now: int | None = None,
    ) -> ConfirmationDecision:
        observed = int(time.time()) if now is None else now
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT * FROM control_request WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if not row or row["chat_id"] != chat_id or row["user_id"] != user_id:
                self.db.execute("COMMIT")
                return ConfirmationDecision(False, None, "CONFIRMATION_INVALID")
            service_id = int(row["service_id"])
            if row["status"] != "pending":
                self.db.execute("COMMIT")
                return ConfirmationDecision(False, service_id, "CONFIRMATION_USED")
            if int(row["expires_at"]) <= observed:
                self.db.execute(
                    "UPDATE control_request SET status='expired', completed_at=?, result_code=? WHERE request_id=?",
                    (observed, "CONFIRMATION_EXPIRED", request_id),
                )
                self._event(
                    request_id=request_id,
                    service_id=service_id,
                    action="restart",
                    state="expired",
                    error_code="CONFIRMATION_EXPIRED",
                    now=observed,
                )
                self.db.execute("COMMIT")
                return ConfirmationDecision(False, service_id, "CONFIRMATION_EXPIRED")
            cooldown = self.db.execute(
                """SELECT 1 FROM control_request
                   WHERE service_id=? AND confirmed_at IS NOT NULL AND confirmed_at>?
                   LIMIT 1""",
                (service_id, observed - SERVICE_COOLDOWN_SECONDS),
            ).fetchone()
            if cooldown:
                self._reject(request_id, service_id, "SERVICE_COOLDOWN", observed)
                self.db.execute("COMMIT")
                return ConfirmationDecision(False, service_id, "SERVICE_COOLDOWN")
            global_count = int(self.db.execute(
                "SELECT COUNT(*) FROM control_request WHERE confirmed_at IS NOT NULL AND confirmed_at>?",
                (observed - GLOBAL_RESTART_WINDOW_SECONDS,),
            ).fetchone()[0])
            if global_count >= GLOBAL_RESTART_LIMIT:
                self._reject(request_id, service_id, "GLOBAL_RESTART_LOCK", observed)
                self.db.execute("COMMIT")
                return ConfirmationDecision(False, service_id, "GLOBAL_RESTART_LOCK")
            updated = self.db.execute(
                """UPDATE control_request SET status='confirmed', confirmed_at=?
                   WHERE request_id=? AND status='pending'""",
                (observed, request_id),
            )
            if updated.rowcount != 1:
                self.db.execute("ROLLBACK")
                return ConfirmationDecision(False, service_id, "CONFIRMATION_USED")
            self._event(
                request_id=request_id,
                service_id=service_id,
                action="restart",
                state="confirmed",
                error_code=None,
                now=observed,
            )
            self.db.execute("COMMIT")
            return ConfirmationDecision(True, service_id, None)
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def _reject(self, request_id: str, service_id: int, code: str, now: int) -> None:
        self.db.execute(
            """UPDATE control_request
               SET status='rejected', completed_at=?, result_code=? WHERE request_id=?""",
            (now, code, request_id),
        )
        self._event(
            request_id=request_id,
            service_id=service_id,
            action="restart",
            state="rejected",
            error_code=code,
            now=now,
        )

    def mark_running(self, request_id: str, service_id: int, *, now: int | None = None) -> None:
        observed = int(time.time()) if now is None else now
        self.db.execute(
            "UPDATE control_request SET status='running' WHERE request_id=? AND status='confirmed'",
            (request_id,),
        )
        self._event(
            request_id=request_id,
            service_id=service_id,
            action="restart",
            state="running",
            error_code=None,
            now=observed,
        )

    def mark_result(
        self,
        request_id: str,
        service_id: int,
        *,
        succeeded: bool,
        error_code: str | None,
        now: int | None = None,
    ) -> None:
        observed = int(time.time()) if now is None else now
        status = "succeeded" if succeeded else "failed"
        self.db.execute(
            """UPDATE control_request
               SET status=?, completed_at=?, result_code=? WHERE request_id=? AND status='running'""",
            (status, observed, error_code, request_id),
        )
        self._event(
            request_id=request_id,
            service_id=service_id,
            action="restart",
            state=status,
            error_code=error_code,
            now=observed,
        )


@dataclass(frozen=True)
class HelperResult:
    ok: bool
    state: str
    error_code: str | None = None


Runner = Callable[[str, int], Awaitable[tuple[int, str]]]
Sleeper = Callable[[float], Awaitable[None]]


def build_helper_argv(helper_path: str, action: str, service_id: int) -> tuple[str, ...]:
    """Return the only privileged argv shape accepted by the controller."""
    if not helper_path.startswith("/"):
        raise ValueError("helper path must be absolute")
    if action not in {"status", "restart"} or service_id not in CONTROL_SERVICES:
        raise ValueError("helper action rejected")
    return ("/usr/bin/sudo", "-n", "--", helper_path, action, str(service_id))


class HelperClient:
    """Invoke the fixed privileged helper without a shell."""

    def __init__(
        self,
        helper_path: str,
        *,
        runner: Runner | None = None,
        sleeper: Sleeper = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if not helper_path.startswith("/"):
            raise ValueError("helper path must be absolute")
        self.helper_path = helper_path
        self._runner = runner or self._run_subprocess
        self._sleep = sleeper
        self._monotonic = monotonic
        self._operation_lock = asyncio.Lock()

    async def _run_subprocess(self, action: str, service_id: int) -> tuple[int, str]:
        argv = build_helper_argv(self.helper_path, action, service_id)
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return 124, "timeout"
        return process.returncode or 0, stdout.decode("utf-8", "replace").strip()[:80]

    async def status(self, service_id: int) -> HelperResult:
        if service_id not in CONTROL_SERVICES:
            return HelperResult(False, "rejected", "SERVICE_UNKNOWN")
        try:
            code, output = await self._runner("status", service_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            return HelperResult(False, "unknown", "STATUS_CHECK_FAILED")
        state = output.split(maxsplit=1)[0] if output else "unknown"
        if code == 0 and state == "ready":
            return HelperResult(True, "ready")
        if state in {"starting", "stopped", "unhealthy"}:
            return HelperResult(False, state, f"SERVICE_{state.upper()}")
        return HelperResult(False, "unknown", "STATUS_CHECK_FAILED")

    async def restart_and_verify(self, service_id: int) -> HelperResult:
        if service_id not in CONTROL_SERVICES:
            return HelperResult(False, "rejected", "SERVICE_UNKNOWN")
        async with self._operation_lock:
            code, output = await self._runner("restart", service_id)
            if code != 0 or output.split(maxsplit=1)[0] != "accepted":
                return HelperResult(False, "failed", "RESTART_COMMAND_FAILED")
            deadline = self._monotonic() + HEALTH_TIMEOUT_SECONDS
            while self._monotonic() < deadline:
                status = await self.status(service_id)
                if status.ok:
                    return status
                await self._sleep(HEALTH_POLL_SECONDS)
            return HelperResult(False, "unhealthy", "HEALTH_TIMEOUT")
