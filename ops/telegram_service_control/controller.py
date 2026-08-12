"""Dedicated Telegram adapter for the host-side service controller.

Use a separate BotFather token. The existing notification/announcement bot may
already have a long-polling consumer, and Telegram permits only one consumer per
bot token.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ops.telegram_service_control.core import (
    CONTROL_SERVICES,
    AccessPolicy,
    AuditStore,
    ConfirmationDecision,
    HelperClient,
    ParsedCommand,
    parse_control_command,
)


CALLBACK_PATTERN = re.compile(r"^(confirm|cancel):([A-Za-z0-9_-]{16})$")
KOREAN_COMMAND_PATTERN = re.compile(
    r"^(?:/재시작\s+[1-5]|[1-5]번(?:\s+컨테이너)?\s+재(?:시작|실행)(?:해)?)\s*$",
)
logger = logging.getLogger(__name__)


class _RedactingFormatter(logging.Formatter):
    def __init__(self, token: str):
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")
        self._token = token

    def format(self, record: logging.LogRecord) -> str:
        return super().format(record).replace(self._token, "[REDACTED]")


def _configure_logging(token: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_RedactingFormatter(token))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.WARNING)


def _required_int(name: str) -> int:
    raw = os.getenv(name, "").strip()
    if not raw or not re.fullmatch(r"-?\d+", raw):
        raise RuntimeError(f"{name} must be an integer")
    return int(raw)


def _required_path(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    if not value.startswith("/"):
        raise RuntimeError(f"{name} must be an absolute path")
    return value


class TelegramServiceController:
    def __init__(self, policy: AccessPolicy, store: AuditStore, helper: HelperClient):
        self.policy = policy
        self.store = store
        self.helper = helper

    def _authorized(self, update: Update) -> bool:
        return self.policy.allows(
            chat_id=update.effective_chat.id if update.effective_chat else None,
            user_id=update.effective_user.id if update.effective_user else None,
            chat_type=update.effective_chat.type if update.effective_chat else None,
        )

    async def _reply_denied(self, update: Update) -> None:
        # Do not disclose the configured IDs or service inventory.
        if update.callback_query:
            await update.callback_query.answer("사용할 수 없는 요청입니다.", show_alert=True)

    async def dispatch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            await self._reply_denied(update)
            return
        message = update.effective_message
        parsed = parse_control_command(message.text if message else "")
        if not message or not parsed:
            return
        await self._handle_command(parsed, update, context)

    async def _handle_command(
        self,
        parsed: ParsedCommand,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        if not message or not update.effective_chat or not update.effective_user:
            return
        if parsed.action == "services":
            self.store.record_read("services", None)
            lines = ["서비스 상태"]
            service_ids = tuple(CONTROL_SERVICES)
            results = await asyncio.gather(*(self.helper.status(service_id) for service_id in service_ids))
            for service_id, result in zip(service_ids, results):
                service = CONTROL_SERVICES[service_id]
                label = "정상" if result.ok else {
                    "starting": "시작 중",
                    "stopped": "중지",
                    "unhealthy": "점검 필요",
                }.get(result.state, "확인 실패")
                lines.append(f"{service_id}. {service.display_name} — {label}")
            await message.reply_text("\n".join(lines))
            return
        if parsed.action == "status" and parsed.service_id:
            self.store.record_read("status", parsed.service_id)
            service = CONTROL_SERVICES[parsed.service_id]
            result = await self.helper.status(parsed.service_id)
            if result.ok:
                await message.reply_text(f"{service.display_name}: 정상")
            else:
                await message.reply_text(
                    f"{service.display_name}: 상태를 확인해 주세요 "
                    f"({result.error_code or 'STATUS_CHECK_FAILED'})",
                )
            return
        if parsed.action == "cancel":
            canceled = self.store.cancel_latest(
                chat_id=update.effective_chat.id,
                user_id=update.effective_user.id,
            )
            await message.reply_text("재시작 요청을 취소했습니다." if canceled else "취소할 요청이 없습니다.")
            return
        if parsed.action == "restart" and parsed.service_id:
            pending = self.store.create_pending(
                parsed.service_id,
                chat_id=update.effective_chat.id,
                user_id=update.effective_user.id,
            )
            service = CONTROL_SERVICES[parsed.service_id]
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("재시작 확인", callback_data=f"confirm:{pending.request_id}"),
                InlineKeyboardButton("취소", callback_data=f"cancel:{pending.request_id}"),
            ]])
            await message.reply_text(
                f"{service.display_name}을 재시작할까요?\n확인은 30초 동안 한 번만 유효합니다.",
                reply_markup=keyboard,
            )

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query:
            return
        if not self._authorized(update):
            await self._reply_denied(update)
            return
        match = CALLBACK_PATTERN.fullmatch(query.data or "")
        if not match or not update.effective_chat or not update.effective_user:
            await query.answer("유효하지 않은 요청입니다.", show_alert=True)
            return
        action, request_id = match.groups()
        if action == "cancel":
            canceled = self.store.cancel(
                request_id,
                chat_id=update.effective_chat.id,
                user_id=update.effective_user.id,
            )
            await query.answer("취소했습니다." if canceled else "이미 처리된 요청입니다.")
            if canceled:
                await query.edit_message_text("재시작 요청을 취소했습니다.")
            return

        decision = self.store.confirm(
            request_id,
            chat_id=update.effective_chat.id,
            user_id=update.effective_user.id,
        )
        if not decision.accepted or not decision.service_id:
            await self._answer_rejection(query, decision)
            return
        service = CONTROL_SERVICES[decision.service_id]
        context.application.create_task(
            self._execute_restart(request_id, decision.service_id, query),
            name=f"telegram-restart-{request_id}",
        )
        try:
            await query.answer("재시작을 시작합니다.")
            await query.edit_message_text(
                f"{service.display_name} 재시작을 시작했습니다. 상태를 확인 중입니다.",
            )
        except Exception as exc:
            logger.warning("telegram control progress update failed: %s", type(exc).__name__)

    async def _answer_rejection(self, query, decision: ConfirmationDecision) -> None:
        messages = {
            "CONFIRMATION_EXPIRED": "확인 시간이 만료됐습니다. 새로 요청해 주세요.",
            "CONFIRMATION_USED": "이미 처리된 요청입니다.",
            "SERVICE_COOLDOWN": "이 서비스는 최근 재시작됐습니다. 5분 뒤 다시 시도해 주세요.",
            "GLOBAL_RESTART_LOCK": "짧은 시간에 재시작이 반복되어 10분 보호 잠금이 적용됐습니다.",
        }
        await query.answer(messages.get(decision.error_code, "유효하지 않은 요청입니다."), show_alert=True)

    async def _execute_restart(self, request_id: str, service_id: int, query) -> None:
        service = CONTROL_SERVICES[service_id]
        self.store.mark_running(request_id, service_id)
        try:
            result = await self.helper.restart_and_verify(service_id)
        except asyncio.CancelledError:
            self.store.mark_result(
                request_id,
                service_id,
                succeeded=False,
                error_code="CONTROL_CANCELED",
            )
            raise
        except Exception:
            result = None
        if result and result.ok:
            self.store.mark_result(request_id, service_id, succeeded=True, error_code=None)
            try:
                await query.edit_message_text(f"{service.display_name} 재시작과 상태 확인을 완료했습니다.")
            except Exception as exc:
                logger.warning("telegram control result update failed: %s", type(exc).__name__)
            return
        error_code = result.error_code if result else "CONTROL_INTERNAL_ERROR"
        self.store.mark_result(
            request_id,
            service_id,
            succeeded=False,
            error_code=error_code,
        )
        try:
            await query.edit_message_text(
                f"{service.display_name} 재시작을 완료하지 못했습니다. "
                f"상태를 확인한 뒤 다시 시도해 주세요 ({error_code}).",
            )
        except Exception as exc:
            logger.warning("telegram control failure update failed: %s", type(exc).__name__)


def build_application() -> tuple[Application, AuditStore]:
    token = os.getenv("TELEGRAM_CONTROL_BOT_TOKEN", "").strip()
    if len(token) < 20:
        raise RuntimeError("TELEGRAM_CONTROL_BOT_TOKEN is required")
    _configure_logging(token)
    policy = AccessPolicy(
        chat_id=_required_int("TELEGRAM_CONTROL_CHAT_ID"),
        owner_user_id=_required_int("TELEGRAM_CONTROL_OWNER_USER_ID"),
    )
    audit_path = _required_path(
        "TELEGRAM_CONTROL_AUDIT_DB",
        "/var/lib/leinygames-telegram-control/audit.sqlite",
    )
    helper_path = _required_path(
        "TELEGRAM_CONTROL_HELPER",
        "/usr/local/sbin/leinygames-service-control",
    )
    store = AuditStore(audit_path)
    controller = TelegramServiceController(policy, store, HelperClient(helper_path))
    try:
        application = Application.builder().token(token).post_init(_set_commands).build()
    except Exception:
        store.close()
        raise
    application.add_handler(CommandHandler(["services", "status", "restart", "cancel"], controller.dispatch))
    application.add_handler(MessageHandler(filters.Regex(KOREAN_COMMAND_PATTERN), controller.dispatch))
    application.add_handler(CallbackQueryHandler(controller.callback, pattern=CALLBACK_PATTERN))
    return application, store


async def _set_commands(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("services", "서비스 번호와 상태"),
        BotCommand("status", "서비스 상태 확인: /status 3"),
        BotCommand("restart", "재시작 요청: /restart 3"),
        BotCommand("cancel", "최근 확인 대기 요청 취소"),
    ])


def main() -> None:
    application, store = build_application()
    try:
        application.run_polling(
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )
    finally:
        store.close()


if __name__ == "__main__":
    main()
