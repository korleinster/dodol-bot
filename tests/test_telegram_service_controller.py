import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ops.telegram_service_control.controller import (
    TelegramServiceController,
    _RedactingFormatter,
    _set_commands,
    build_application,
)
from ops.telegram_service_control.core import AccessPolicy, AuditStore, HelperResult


def _callback_update(*, data, chat_id=10, user_id=20, chat_type="private"):
    query = SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        effective_user=SimpleNamespace(id=user_id),
        effective_message=None,
    )
    return update, query


class TelegramServiceControllerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AuditStore(Path(self.tmp.name) / "audit.sqlite")
        self.helper = SimpleNamespace(
            status=AsyncMock(return_value=HelperResult(True, "ready")),
            restart_and_verify=AsyncMock(return_value=HelperResult(True, "ready")),
        )
        self.controller = TelegramServiceController(
            AccessPolicy(chat_id=10, owner_user_id=20),
            self.store,
            self.helper,
        )

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _status(self, request_id):
        return self.store.db.execute(
            "SELECT status FROM control_request WHERE request_id=?",
            (request_id,),
        ).fetchone()[0]

    async def test_old_cancel_button_does_not_cancel_new_request(self):
        first = self.store.create_pending(1, chat_id=10, user_id=20)
        second = self.store.create_pending(2, chat_id=10, user_id=20)
        update, query = _callback_update(data=f"cancel:{first.request_id}")

        await self.controller.callback(update, SimpleNamespace(application=None))

        self.assertEqual(self._status(first.request_id), "canceled")
        self.assertEqual(self._status(second.request_id), "pending")
        query.edit_message_text.assert_not_awaited()

    async def test_current_cancel_button_cancels_its_exact_request(self):
        pending = self.store.create_pending(2, chat_id=10, user_id=20)
        update, query = _callback_update(data=f"cancel:{pending.request_id}")

        await self.controller.callback(update, SimpleNamespace(application=None))

        self.assertEqual(self._status(pending.request_id), "canceled")
        query.edit_message_text.assert_awaited_once()

    async def test_confirmed_restart_runs_even_if_progress_message_fails(self):
        pending = self.store.create_pending(3, chat_id=10, user_id=20)
        update, query = _callback_update(data=f"confirm:{pending.request_id}")
        query.answer.side_effect = RuntimeError("telegram unavailable")
        tasks = []

        def create_task(coroutine, **_kwargs):
            tasks.append(coroutine)

        context = SimpleNamespace(application=SimpleNamespace(create_task=create_task))
        await self.controller.callback(update, context)
        self.assertEqual(len(tasks), 1)
        await tasks[0]

        self.helper.restart_and_verify.assert_awaited_once_with(3)
        self.assertEqual(self._status(pending.request_id), "succeeded")
        query.edit_message_text.assert_awaited_once()

    async def test_forged_callback_is_denied_before_state_change(self):
        pending = self.store.create_pending(4, chat_id=10, user_id=20)
        update, query = _callback_update(
            data=f"confirm:{pending.request_id}",
            user_id=21,
        )

        await self.controller.callback(update, SimpleNamespace(application=None))

        self.assertEqual(self._status(pending.request_id), "pending")
        self.helper.restart_and_verify.assert_not_awaited()
        query.answer.assert_awaited_once_with("사용할 수 없는 요청입니다.", show_alert=True)

    def test_journal_formatter_redacts_bot_token(self):
        import logging

        token = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        record = logging.LogRecord(
            "httpx",
            logging.ERROR,
            __file__,
            1,
            "request failed at https://api.telegram.org/bot%s/getUpdates",
            (token,),
            None,
        )
        rendered = _RedactingFormatter(token).format(record)
        self.assertNotIn(token, rendered)
        self.assertIn("[REDACTED]", rendered)

    def test_application_registers_post_init_and_fails_closed_without_config(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                build_application()

        audit_path = str(Path(self.tmp.name) / "application.sqlite")
        environment = {
            "TELEGRAM_CONTROL_BOT_TOKEN": "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "TELEGRAM_CONTROL_CHAT_ID": "10",
            "TELEGRAM_CONTROL_OWNER_USER_ID": "20",
            "TELEGRAM_CONTROL_AUDIT_DB": audit_path,
            "TELEGRAM_CONTROL_HELPER": "/usr/local/sbin/leinygames-service-control",
        }
        with patch.dict("os.environ", environment, clear=True):
            application, store = build_application()
        try:
            self.assertIs(application.post_init, _set_commands)
            self.assertGreaterEqual(sum(len(group) for group in application.handlers.values()), 3)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
