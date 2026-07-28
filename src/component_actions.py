"""Shared, fail-closed execution policy for Discord message components.

The registry is intentionally small: a Discord component is only executable
when it has an explicit registration with an ``allow_non_admin`` boolean.
Everything else, including components written before this subsystem existed,
is display-only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Pattern

from src.db import (
    claim_component_action,
    complete_component_action_claim,
    get_component_action_claim_status,
    get_db,
)


LEGACY_COMPONENT_PREFIXES = ("boss_cut:", "boss_miss:")


@dataclass(frozen=True)
class RegisteredComponentAction:
    """A custom ID resolved against one centrally registered action."""

    key: str
    custom_id: str
    allow_non_admin: bool
    handler_cog: str
    handler_name: str
    params: Mapping[str, str]
    idempotency_key: str


@dataclass(frozen=True)
class ComponentActionDefinition:
    """Immutable policy required before a Discord button can execute."""

    key: str
    custom_id_pattern: Pattern[str]
    allow_non_admin: bool
    handler_cog: str
    handler_name: str
    idempotency_key: Callable[[int, int, Mapping[str, str]], str]

    def __post_init__(self) -> None:
        # ``bool`` is deliberately checked exactly: truthy values such as 1
        # must not accidentally make a new action available to guests.
        if type(self.allow_non_admin) is not bool:
            raise TypeError("allow_non_admin must be an explicit boolean")
        if not self.key or not self.handler_cog or not self.handler_name:
            raise ValueError("component action registration is incomplete")

    def resolve(self, custom_id: str, guild_id: int, message_id: int) -> RegisteredComponentAction | None:
        match = self.custom_id_pattern.fullmatch(custom_id)
        if match is None:
            return None
        params = match.groupdict()
        return RegisteredComponentAction(
            key=self.key,
            custom_id=custom_id,
            allow_non_admin=self.allow_non_admin,
            handler_cog=self.handler_cog,
            handler_name=self.handler_name,
            params=params,
            idempotency_key=self.idempotency_key(guild_id, message_id, params),
        )


class ComponentActionRegistry:
    def __init__(self) -> None:
        self._definitions: list[ComponentActionDefinition] = []

    def register(self, definition: ComponentActionDefinition) -> None:
        if any(item.key == definition.key for item in self._definitions):
            raise ValueError(f"component action already registered: {definition.key}")
        self._definitions.append(definition)

    def resolve(self, custom_id: str, guild_id: int, message_id: int) -> RegisteredComponentAction | None:
        if not isinstance(custom_id, str):
            return None
        for definition in self._definitions:
            resolved = definition.resolve(custom_id, guild_id, message_id)
            if resolved is not None:
                return resolved
        return None

    def metadata(
        self,
        *,
        custom_id: str | None,
        label: str | None,
        component_type: int,
        style: int | None,
        disabled: bool,
        guild_id: int,
        message_id: int,
    ) -> dict[str, Any]:
        """Serialize an action row without granting an unregistered action."""
        action = (
            self.resolve(custom_id, guild_id, message_id)
            if isinstance(custom_id, str) else None
        )
        return {
            "label": label,
            "customId": custom_id,
            "type": component_type,
            "style": style,
            "disabled": bool(disabled),
            "actionable": bool(action is not None and not disabled),
            "allowNonAdmin": action.allow_non_admin if action is not None else False,
        }


def boss_component_custom_id(action: str, guild_id: int, boss_name: str) -> str:
    """Build a versioned ID so pre-M42 buttons remain safely read-only."""
    if action not in {"cut", "miss"}:
        raise ValueError("unsupported boss component action")
    if guild_id <= 0 or not boss_name or ":" in boss_name:
        raise ValueError("invalid boss component target")
    custom_id = f"component:v1:boss:{action}:{guild_id}:{boss_name}"
    if len(custom_id) > 100:
        raise ValueError("boss component ID exceeds Discord limit")
    return custom_id


def _boss_idempotency_key(guild_id: int, message_id: int, _params: Mapping[str, str]) -> str:
    # The two buttons describe mutually exclusive outcomes for one alert.
    return f"boss-message:{guild_id}:{message_id}"


COMPONENT_ACTIONS = ComponentActionRegistry()
COMPONENT_ACTIONS.register(ComponentActionDefinition(
    key="boss_cut",
    custom_id_pattern=re.compile(
        r"component:v1:boss:cut:(?P<guild_id>[1-9][0-9]{0,19}):(?P<boss_name>[^:]{1,50})",
    ),
    allow_non_admin=True,
    handler_cog="Boss",
    handler_name="execute_registered_component",
    idempotency_key=_boss_idempotency_key,
))
COMPONENT_ACTIONS.register(ComponentActionDefinition(
    key="boss_miss",
    custom_id_pattern=re.compile(
        r"component:v1:boss:miss:(?P<guild_id>[1-9][0-9]{0,19}):(?P<boss_name>[^:]{1,50})",
    ),
    allow_non_admin=True,
    handler_cog="Boss",
    handler_name="execute_registered_component",
    idempotency_key=_boss_idempotency_key,
))


@dataclass
class ComponentActionResult:
    status: str
    error_code: str | None = None
    error_message: str | None = None
    recovery_instructions: str | None = None
    components: list[dict[str, Any]] | None = None
    output: Any = None

    @property
    def succeeded(self) -> bool:
        return self.status in {"succeeded", "already_processed"}

    def payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "errorCode": self.error_code,
            "errorMessage": self.error_message,
            "recoveryInstructions": self.recovery_instructions,
            "components": self.components or [],
        }


class ComponentActionDispatcher:
    """Resolve, authorize, claim, and dispatch one registered component action."""

    def __init__(self, bot: Any, registry: ComponentActionRegistry = COMPONENT_ACTIONS):
        self.bot = bot
        self.registry = registry

    @staticmethod
    def _failure(
        code: str,
        message: str,
        recovery: str,
    ) -> ComponentActionResult:
        return ComponentActionResult(
            status="failed",
            error_code=code,
            error_message=message,
            recovery_instructions=recovery,
        )

    async def fetch_original_message(
        self, *, guild_id: int, message_id: int,
    ) -> tuple[Any | None, ComponentActionResult | None]:
        """Fetch only the configured 003 channel's original bot message."""
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return None, self._failure(
                "GUILD_UNAVAILABLE", "연결된 Discord 서버를 찾을 수 없습니다.",
                "003번 봇 연결 상태를 확인한 뒤 다시 시도하세요.",
            )
        async with get_db() as db:
            async with db.execute(
                "SELECT text_channel_id FROM guild_config WHERE guild_id=? AND bot_number=?",
                (guild_id, self.bot.bot_number),
            ) as cur:
                row = await cur.fetchone()
        if row is None or not row["text_channel_id"]:
            return None, self._failure(
                "CHANNEL_UNAVAILABLE", "연결된 Discord 채널을 찾을 수 없습니다.",
                "003번 봇 채널 설정을 확인한 뒤 다시 시도하세요.",
            )
        channel = self.bot.get_channel(int(row["text_channel_id"]))
        fetch_message = getattr(channel, "fetch_message", None)
        if channel is None or fetch_message is None:
            return None, self._failure(
                "CHANNEL_UNAVAILABLE", "연결된 Discord 채널을 사용할 수 없습니다.",
                "003번 봇 연결 상태를 확인한 뒤 다시 시도하세요.",
            )
        try:
            return await fetch_message(message_id), None
        except Exception as exc:
            # Do not expose Discord resource identifiers or exception detail.
            print(f"[component-actions] original message fetch failed ({type(exc).__name__})")
            return None, self._failure(
                "MESSAGE_UNAVAILABLE", "원본 Discord 메시지를 찾을 수 없습니다.",
                "새 알림인지 확인한 뒤 다시 시도하세요.",
            )

    @staticmethod
    def _find_component(message: Any, custom_id: str) -> Any | None:
        for row in getattr(message, "components", ()) or ():
            children = getattr(row, "children", None)
            for component in children if children is not None else (row,):
                if getattr(component, "custom_id", None) == custom_id:
                    return component
        return None

    def _component_metadata(
        self,
        message: Any,
        *,
        disable_idempotency_key: str | None = None,
    ) -> list[dict[str, Any]]:
        guild_id = int(message.guild.id)
        message_id = int(message.id)
        items: list[dict[str, Any]] = []
        for row in getattr(message, "components", ()) or ():
            children = getattr(row, "children", None)
            for component in children if children is not None else (row,):
                custom_id = getattr(component, "custom_id", None)
                disabled = bool(getattr(component, "disabled", False))
                action = self.registry.resolve(custom_id, guild_id, message_id) if isinstance(custom_id, str) else None
                if action is not None and action.idempotency_key == disable_idempotency_key:
                    disabled = True
                style_value = getattr(component, "style", None)
                try:
                    style = int(getattr(style_value, "value", style_value)) if style_value is not None else None
                except (TypeError, ValueError):
                    style = None
                type_value = getattr(component, "type", 0)
                try:
                    component_type = int(getattr(type_value, "value", type_value))
                except (TypeError, ValueError):
                    component_type = 0
                items.append(self.registry.metadata(
                    custom_id=custom_id,
                    label=getattr(component, "label", None),
                    component_type=component_type,
                    style=style,
                    disabled=disabled,
                    guild_id=guild_id,
                    message_id=message_id,
                ))
        return items

    async def dispatch(
        self,
        *,
        request_id: str,
        guild_id: int,
        message_id: int,
        custom_id: str,
        actor: Any,
        actor_type: str,
        actor_ref: str,
        is_owner: bool,
        message: Any | None = None,
    ) -> ComponentActionResult:
        """Execute a registered action once, from Discord or the web bridge."""
        action = self.registry.resolve(custom_id, guild_id, message_id)
        if action is None:
            if isinstance(custom_id, str) and custom_id.startswith(LEGACY_COMPONENT_PREFIXES):
                return self._failure(
                    "READ_ONLY_LEGACY", "이전 알림 버튼은 더 이상 실행할 수 없습니다.",
                    "새로 생성된 알림에서 다시 시도하세요.",
                )
            return self._failure(
                "UNREGISTERED_COMPONENT", "등록되지 않은 버튼입니다.",
                "새로 고침한 뒤 지원되는 버튼에서 다시 시도하세요.",
            )
        target_guild = action.params.get("guild_id")
        if target_guild is not None:
            try:
                target_matches = int(target_guild) == guild_id
            except ValueError:
                target_matches = False
            if not target_matches:
                return self._failure(
                    "MESSAGE_TARGET_MISMATCH", "다른 서버의 메시지는 실행할 수 없습니다.",
                    "현재 보탐 서버의 알림에서 다시 시도하세요.",
                )
        if not action.allow_non_admin and not is_owner:
            return self._failure(
                "OWNER_ACTION_REQUIRED", "이 버튼은 관리자만 실행할 수 있습니다.",
                "관리자 계정으로 다시 시도하세요.",
            )

        if message is None:
            message, failure = await self.fetch_original_message(
                guild_id=guild_id, message_id=message_id,
            )
            if failure is not None:
                return failure

        validation = await self._validate_original_message(
            message=message,
            guild_id=guild_id,
            message_id=message_id,
            custom_id=custom_id,
        )
        if validation is not None:
            return validation
        component = self._find_component(message, custom_id)
        if bool(getattr(component, "disabled", False)):
            prior_status = await get_component_action_claim_status(action.idempotency_key)
            if prior_status == "succeeded":
                canonical_message = await self._disable_after_success(message, action)
                return ComponentActionResult(
                    status="already_processed",
                    components=self._component_metadata(
                        canonical_message, disable_idempotency_key=action.idempotency_key,
                    ),
                )
            return self._failure(
                "ACTION_ALREADY_DISABLED", "이미 처리된 버튼입니다.",
                "최신 보스 알림을 확인하세요.",
            )

        claim_state = await claim_component_action(
            idempotency_key=action.idempotency_key,
            request_id=request_id,
            guild_id=guild_id,
            channel_id=int(message.channel.id),
            message_id=message_id,
            custom_id=custom_id,
            action_key=action.key,
            actor_type=actor_type,
            actor_ref=actor_ref,
        )
        if claim_state == "succeeded":
            canonical_message = await self._disable_after_success(message, action)
            return ComponentActionResult(
                status="already_processed",
                components=self._component_metadata(
                    canonical_message, disable_idempotency_key=action.idempotency_key,
                ),
            )
        if claim_state == "in_progress":
            return self._failure(
                "ACTION_IN_PROGRESS", "다른 요청에서 버튼을 처리하고 있습니다.",
                "잠시 뒤 최신 알림 상태를 확인하세요.",
            )

        cog = self.bot.get_cog(action.handler_cog)
        handler = getattr(cog, action.handler_name, None) if cog is not None else None
        if handler is None:
            await complete_component_action_claim(
                action.idempotency_key, status="failed", error_code="ACTION_HANDLER_UNAVAILABLE",
            )
            return self._failure(
                "ACTION_HANDLER_UNAVAILABLE", "버튼 처리 기능을 사용할 수 없습니다.",
                "003번 봇 상태를 확인한 뒤 다시 시도하세요.",
            )
        try:
            output = await handler(action=action, message=message, actor=actor)
        except Exception as exc:
            print(f"[component-actions] action handler failed ({type(exc).__name__})")
            await complete_component_action_claim(
                action.idempotency_key, status="failed", error_code="ACTION_FAILED",
            )
            return self._failure(
                "ACTION_FAILED", "버튼 처리에 실패했습니다.",
                "잠시 뒤 같은 버튼을 다시 시도하세요.",
            )
        if isinstance(output, str):
            # Business validation failures are retryable. The message remains
            # actionable and the audit row records a safe reason only.
            await complete_component_action_claim(
                action.idempotency_key, status="failed", error_code="ACTION_REJECTED",
            )
            return self._failure(
                "ACTION_REJECTED", output,
                "알림 대상과 보스 상태를 확인한 뒤 다시 시도하세요.",
            )

        await complete_component_action_claim(action.idempotency_key, status="succeeded")
        # Discord and web requests share one public result delivery. The
        # business mutation is already terminal in the durable claim, so a
        # delivery outage can never reopen it or permit a duplicate cut/miss.
        try:
            await message.channel.send(embed=output)
        except Exception as exc:
            print(f"[component-actions] action result broadcast failed ({type(exc).__name__})")
        canonical_message = await self._disable_after_success(message, action)
        return ComponentActionResult(
            status="succeeded",
            components=self._component_metadata(
                canonical_message, disable_idempotency_key=action.idempotency_key,
            ),
            output=output,
        )

    async def _validate_original_message(
        self,
        *,
        message: Any,
        guild_id: int,
        message_id: int,
        custom_id: str,
    ) -> ComponentActionResult | None:
        if message is None or int(getattr(message, "id", 0)) != message_id:
            return self._failure(
                "MESSAGE_UNAVAILABLE", "원본 Discord 메시지를 찾을 수 없습니다.",
                "새 알림인지 확인한 뒤 다시 시도하세요.",
            )
        guild = getattr(message, "guild", None)
        channel = getattr(message, "channel", None)
        bot_user = getattr(self.bot, "user", None)
        author = getattr(message, "author", None)
        if guild is None or int(getattr(guild, "id", 0)) != guild_id:
            return self._failure(
                "MESSAGE_TARGET_MISMATCH", "다른 서버의 메시지는 실행할 수 없습니다.",
                "현재 보탐 서버의 알림에서 다시 시도하세요.",
            )
        if channel is None or author is None or bot_user is None:
            return self._failure(
                "MESSAGE_INVALID", "실행 가능한 Discord 메시지가 아닙니다.",
                "새로 생성된 알림에서 다시 시도하세요.",
            )
        async with get_db() as db:
            async with db.execute(
                "SELECT text_channel_id FROM guild_config WHERE guild_id=? AND bot_number=?",
                (guild_id, self.bot.bot_number),
            ) as cur:
                configured = await cur.fetchone()
        if configured is None or int(configured["text_channel_id"] or 0) != int(getattr(channel, "id", 0)):
            return self._failure(
                "MESSAGE_CHANNEL_MISMATCH", "설정된 보탐 채널의 메시지만 실행할 수 있습니다.",
                "현재 보탐 채널의 새 알림에서 다시 시도하세요.",
            )
        if int(getattr(author, "id", 0)) != int(getattr(bot_user, "id", -1)):
            return self._failure(
                "MESSAGE_NOT_BOT_AUTHORED", "봇이 작성한 메시지만 실행할 수 있습니다.",
                "003번 봇 알림에서 다시 시도하세요.",
            )
        component = self._find_component(message, custom_id)
        if component is None:
            return self._failure(
                "COMPONENT_NOT_PRESENT", "원본 메시지에 해당 버튼이 없습니다.",
                "최신 알림을 확인한 뒤 다시 시도하세요.",
            )
        return None

    async def _disable_after_success(
        self, message: Any, action: RegisteredComponentAction,
    ) -> Any:
        cog = self.bot.get_cog(action.handler_cog)
        disable = getattr(cog, "disable_registered_component", None) if cog is not None else None
        if disable is None:
            return message
        try:
            updated = await disable(message=message, action=action)
            return updated if updated is not None else message
        except Exception as exc:
            # The business action already completed under its persistent claim.
            # A later duplicate request will retry only this view reconciliation.
            print(f"[component-actions] component disable failed ({type(exc).__name__})")
            return message
