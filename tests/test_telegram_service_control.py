import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ops.telegram_service_control.core import (
    AccessPolicy,
    AuditStore,
    GLOBAL_RESTART_LIMIT,
    HEALTH_POLL_SECONDS,
    HEALTH_TIMEOUT_SECONDS,
    HelperClient,
    build_helper_argv,
    parse_control_command,
)


class TelegramControlPolicyTest(unittest.TestCase):
    def test_parser_accepts_only_documented_commands(self):
        accepted = {
            "/services": ("services", None),
            "/status 3": ("status", 3),
            "/restart 4": ("restart", 4),
            "/cancel": ("cancel", None),
            "/재시작 2": ("restart", 2),
            "1번 컨테이너 재실행해": ("restart", 1),
        }
        for text, expected in accepted.items():
            with self.subTest(text=text):
                parsed = parse_control_command(text)
                self.assertIsNotNone(parsed)
                self.assertEqual((parsed.action, parsed.service_id), expected)

        for text in (
            "/status",
            "/restart 0",
            "/restart 6",
            "/restart 3 now",
            "/services 1",
            "dodol-bot-003 재시작",
            "3; reboot",
            "전체 재시작",
        ):
            with self.subTest(text=text):
                self.assertIsNone(parse_control_command(text))

    def test_access_requires_exact_private_chat_and_owner(self):
        policy = AccessPolicy(chat_id=-1001, owner_user_id=77)
        self.assertTrue(policy.allows(chat_id=-1001, user_id=77, chat_type="private"))
        self.assertFalse(policy.allows(chat_id=-1001, user_id=78, chat_type="private"))
        self.assertFalse(policy.allows(chat_id=-1002, user_id=77, chat_type="private"))
        self.assertFalse(policy.allows(chat_id=-1001, user_id=77, chat_type="group"))

    def test_privileged_argv_is_fixed_and_shell_free(self):
        self.assertEqual(
            build_helper_argv("/usr/local/sbin/leinygames-service-control", "restart", 3),
            (
                "/usr/bin/sudo",
                "-n",
                "--",
                "/usr/local/sbin/leinygames-service-control",
                "restart",
                "3",
            ),
        )
        for action, service_id in (("restart; reboot", 3), ("restart", 0), ("status", 6)):
            with self.subTest(action=action, service_id=service_id):
                with self.assertRaises(ValueError):
                    build_helper_argv(
                        "/usr/local/sbin/leinygames-service-control",
                        action,
                        service_id,
                    )

    def test_privileged_helper_does_not_parse_compose_or_accept_dynamic_targets(self):
        helper = (
            Path(__file__).parents[1]
            / "ops"
            / "telegram_service_control"
            / "leinygames-service-control"
        ).read_text(encoding="utf-8")
        self.assertNotIn("docker compose", helper.lower())
        self.assertNotIn("eval", helper)
        self.assertIn("COMPOSE_PROJECT=dodol-bot", helper)
        self.assertIn('label=com.docker.compose.project=$COMPOSE_PROJECT', helper)
        self.assertIn('label=com.docker.compose.service=$service_name', helper)
        self.assertIn('$DOCKER restart "$container_id"', helper)
        self.assertIn("/run/leinygames-service-control.lock", helper)
        self.assertIn("$FLOCK -n 9", helper)
        self.assertIn("unset DOCKER_HOST DOCKER_CONTEXT", helper)
        for service_id in range(1, 6):
            self.assertIn(f'{service_id}) service_name="', helper)


class TelegramControlAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "audit.sqlite"
        self.store = AuditStore(self.path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _status(self, request_id):
        return self.store.db.execute(
            "SELECT status, result_code FROM control_request WHERE request_id=?",
            (request_id,),
        ).fetchone()

    def test_confirmation_is_one_use_and_expires_at_boundary(self):
        pending = self.store.create_pending(1, chat_id=10, user_id=20, now=100)
        accepted = self.store.confirm(pending.request_id, chat_id=10, user_id=20, now=101)
        self.assertTrue(accepted.accepted)
        repeated = self.store.confirm(pending.request_id, chat_id=10, user_id=20, now=102)
        self.assertEqual(repeated.error_code, "CONFIRMATION_USED")

        expiring = self.store.create_pending(2, chat_id=10, user_id=20, now=200)
        expired = self.store.confirm(expiring.request_id, chat_id=10, user_id=20, now=230)
        self.assertEqual(expired.error_code, "CONFIRMATION_EXPIRED")
        self.assertEqual(self._status(expiring.request_id)[0], "expired")

    def test_forged_chat_or_user_cannot_confirm_or_cancel(self):
        pending = self.store.create_pending(3, chat_id=10, user_id=20, now=100)
        self.assertEqual(
            self.store.confirm(pending.request_id, chat_id=11, user_id=20, now=101).error_code,
            "CONFIRMATION_INVALID",
        )
        self.assertFalse(self.store.cancel(pending.request_id, chat_id=10, user_id=21, now=102))
        self.assertEqual(self._status(pending.request_id)[0], "pending")

    def test_old_inline_cancel_cannot_cancel_newer_request(self):
        first = self.store.create_pending(1, chat_id=10, user_id=20, now=100)
        second = self.store.create_pending(2, chat_id=10, user_id=20, now=101)
        self.assertFalse(self.store.cancel(first.request_id, chat_id=10, user_id=20, now=102))
        self.assertEqual(self._status(first.request_id)[0], "canceled")
        self.assertEqual(self._status(second.request_id)[0], "pending")

    def test_inline_cancel_consumes_current_request(self):
        pending = self.store.create_pending(1, chat_id=10, user_id=20, now=100)
        self.assertTrue(self.store.cancel(pending.request_id, chat_id=10, user_id=20, now=101))
        self.assertEqual(self._status(pending.request_id)[0], "canceled")
        self.assertFalse(self.store.cancel(pending.request_id, chat_id=10, user_id=20, now=102))

    def test_new_restart_request_supersedes_prior_pending_and_bounds_active_rows(self):
        first = self.store.create_pending(1, chat_id=10, user_id=20, now=100)
        second = self.store.create_pending(2, chat_id=10, user_id=20, now=101)
        third = self.store.create_pending(3, chat_id=10, user_id=20, now=140)

        self.assertEqual(tuple(self._status(first.request_id)), ("canceled", "CONFIRMATION_SUPERSEDED"))
        self.assertEqual(tuple(self._status(second.request_id)), ("expired", "CONFIRMATION_EXPIRED"))
        self.assertEqual(self._status(third.request_id)[0], "pending")
        active_count = self.store.db.execute(
            "SELECT COUNT(*) FROM control_request WHERE status='pending'",
        ).fetchone()[0]
        self.assertEqual(active_count, 1)

    def test_reopening_store_invalidates_unfinished_requests(self):
        pending = self.store.create_pending(2, chat_id=10, user_id=20)
        self.store.close()
        self.store = AuditStore(self.path)
        status, code = self._status(pending.request_id)
        self.assertEqual(status, "expired")
        self.assertEqual(code, "CONTROLLER_RESTARTED")
        decision = self.store.confirm(pending.request_id, chat_id=10, user_id=20)
        self.assertEqual(decision.error_code, "CONFIRMATION_USED")

    def test_service_cooldown_and_global_restart_lock(self):
        first = self.store.create_pending(1, chat_id=10, user_id=20, now=100)
        self.assertTrue(self.store.confirm(first.request_id, chat_id=10, user_id=20, now=101).accepted)
        same = self.store.create_pending(1, chat_id=10, user_id=20, now=102)
        self.assertEqual(
            self.store.confirm(same.request_id, chat_id=10, user_id=20, now=103).error_code,
            "SERVICE_COOLDOWN",
        )

        for service_id in range(2, GLOBAL_RESTART_LIMIT + 1):
            pending = self.store.create_pending(service_id, chat_id=10, user_id=20, now=110 + service_id)
            self.assertTrue(
                self.store.confirm(
                    pending.request_id,
                    chat_id=10,
                    user_id=20,
                    now=120 + service_id,
                ).accepted,
            )
        blocked = self.store.create_pending(4, chat_id=10, user_id=20, now=130)
        self.assertEqual(
            self.store.confirm(blocked.request_id, chat_id=10, user_id=20, now=131).error_code,
            "GLOBAL_RESTART_LOCK",
        )

    def test_audit_schema_does_not_store_message_or_secret_material(self):
        columns = set()
        for table in ("control_request", "control_event"):
            columns.update(row[1] for row in self.store.db.execute(f"PRAGMA table_info({table})"))
        for forbidden in ("text", "message", "token", "secret", "environment", "callback"):
            self.assertNotIn(forbidden, columns)

        pending = self.store.create_pending(5, chat_id=10, user_id=20, now=100)
        self.store.confirm(pending.request_id, chat_id=10, user_id=20, now=101)
        persisted = self.path.read_bytes()
        self.assertNotIn(b"TELEGRAM_CONTROL_BOT_TOKEN", persisted)


class TelegramControlHelperTest(unittest.IsolatedAsyncioTestCase):
    async def test_status_failure_returns_a_safe_code(self):
        async def runner(_action, _service_id):
            raise OSError("sensitive host detail")

        helper = HelperClient("/safe/helper", runner=runner)
        result = await helper.status(1)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "STATUS_CHECK_FAILED")

    async def test_restart_operations_are_serialized(self):
        active = 0
        max_active = 0

        async def runner(action, service_id):
            nonlocal active, max_active
            if action == "restart":
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0)
                active -= 1
                return 0, "accepted RESTART_REQUESTED"
            return 0, "ready HEALTHY"

        helper = HelperClient("/safe/helper", runner=runner)
        results = await asyncio.gather(
            helper.restart_and_verify(1),
            helper.restart_and_verify(2),
        )
        self.assertTrue(all(result.ok for result in results))
        self.assertEqual(max_active, 1)

    async def test_restart_fails_closed_after_bounded_health_timeout(self):
        now = 0.0
        calls = []

        async def runner(action, service_id):
            calls.append((action, service_id))
            if action == "restart":
                return 0, "accepted RESTART_REQUESTED"
            return 3, "unhealthy HEALTHCHECK_FAILED"

        async def sleeper(delay):
            nonlocal now
            self.assertEqual(delay, HEALTH_POLL_SECONDS)
            now += delay

        helper = HelperClient(
            "/safe/helper",
            runner=runner,
            sleeper=sleeper,
            monotonic=lambda: now,
        )
        result = await helper.restart_and_verify(3)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "HEALTH_TIMEOUT")
        self.assertLessEqual(len(calls), 2 + HEALTH_TIMEOUT_SECONDS // HEALTH_POLL_SECONDS)


if __name__ == "__main__":
    unittest.main()
