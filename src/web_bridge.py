"""Authenticated Unix-socket bridge for the botam web pilot on bot 003."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import discord
from aiohttp import web

from src.component_actions import COMPONENT_ACTIONS, ComponentActionDispatcher
from src.db import (
    append_web_broadcast_event,
    get_db,
    has_web_broadcast_message,
    list_web_broadcast_events,
)


BRIDGE_VERSION = 1
MAX_BODY_BYTES = 8 * 1024
MAX_TTS_CHARS = 200
MAX_TTS_QUEUE = 3
NONCE_TTL_SECONDS = 60
JOB_TTL_SECONDS = 15 * 60
BLOCKED_COMMANDS = {"재시작", "정신차려"}
BLOCKED_HEADS = {"소환", "설정"}
SCHEDULER_STATUSES = {"starting", "ready", "failed"}
SCHEDULER_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _scheduler_health_payload(bot: Any) -> dict[str, Any]:
    """Return the fail-closed, detail-free scheduler health contract."""
    raw = getattr(bot, "scheduler_health", None)
    if not isinstance(raw, dict):
        raw = {}

    status = raw.get("status")
    if status not in SCHEDULER_STATUSES:
        status = "starting"

    def safe_timestamp(value: Any) -> int | None:
        return value if type(value) is int and value >= 0 else None

    bootstrap_completed_at = safe_timestamp(raw.get("bootstrapCompletedAt"))
    last_tick_at = safe_timestamp(raw.get("lastTickAt"))
    error_code = raw.get("errorCode")
    if not isinstance(error_code, str) or not SCHEDULER_ERROR_CODE_RE.fullmatch(error_code):
        error_code = None
    if status == "ready" and (
        bootstrap_completed_at is None or last_tick_at is None
    ):
        status = "failed"
        error_code = "SCHEDULER_STATUS_UNKNOWN"
    if status == "starting":
        last_tick_at = None
        error_code = None
    if status == "failed" and error_code is None:
        error_code = "SCHEDULER_STATUS_UNKNOWN"
    if status != "failed":
        error_code = None

    return {
        "status": status,
        "bootstrapCompletedAt": bootstrap_completed_at,
        "lastTickAt": last_tick_at,
        "errorCode": error_code,
    }


def _actor_id(actor_ref: str) -> int:
    value = int.from_bytes(hashlib.sha256(actor_ref.encode("utf-8")).digest()[:8], "big")
    value = value & ((1 << 63) - 1)
    return -(value or 1)


def _embed_json(embed: discord.Embed | None) -> dict[str, Any] | None:
    if embed is None:
        return None
    raw = embed.to_dict()
    return {
        "title": raw.get("title"),
        "description": raw.get("description"),
        "color": raw.get("color"),
        "fields": raw.get("fields", []),
        "footer": raw.get("footer"),
        "url": raw.get("url"),
        "imageUrl": (raw.get("image") or {}).get("url"),
        "thumbnailUrl": (raw.get("thumbnail") or {}).get("url"),
        "author": raw.get("author"),
    }


def _message_embeds(message: discord.Message) -> list[dict[str, Any]]:
    return [item for embed in message.embeds if (item := _embed_json(embed)) is not None]


def _message_attachments(message: discord.Message) -> list[dict[str, Any]]:
    return [
        {
            "filename": attachment.filename,
            "url": attachment.url,
            "contentType": attachment.content_type,
            "size": attachment.size,
        }
        for attachment in message.attachments
    ]


def _component_type(component: Any) -> int:
    value = getattr(component, "type", 0)
    try:
        return int(getattr(value, "value", value))
    except (TypeError, ValueError):
        return 0


def _message_components(message: discord.Message) -> list[dict[str, Any]]:
    """Flatten components with explicit policy metadata for new broadcasts."""
    result: list[dict[str, Any]] = []
    guild_id = int(getattr(getattr(message, "guild", None), "id", 0) or 0)
    message_id = int(getattr(message, "id", 0) or 0)
    for row in getattr(message, "components", ()) or ():
        children = getattr(row, "children", None)
        for component in children if children is not None else (row,):
            style_value = getattr(component, "style", None)
            try:
                style = int(getattr(style_value, "value", style_value)) if style_value is not None else None
            except (TypeError, ValueError):
                style = None
            result.append(COMPONENT_ACTIONS.metadata(
                label=getattr(component, "label", None),
                custom_id=getattr(component, "custom_id", None),
                component_type=_component_type(component),
                style=style,
                disabled=bool(getattr(component, "disabled", False)),
                guild_id=guild_id,
                message_id=message_id,
            ))
    return result


def _message_content(message: discord.Message) -> str:
    # clean_content resolves mentions without exposing raw Discord mention IDs.
    content = getattr(message, "clean_content", None)
    if content is None:
        content = getattr(message, "content", "")
    return str(content)


def _event_key(
    *, message_id: int, kind: str, content: str | None = None,
    embeds: list[dict[str, Any]] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    components: list[dict[str, Any]] | None = None,
    edited_at: Any = None,
) -> str:
    """Derive an idempotency key from Discord's stable message/revision data."""
    if kind in {"message", "message_delete"}:
        return f"{kind}:{message_id}"
    revision = getattr(edited_at, "isoformat", lambda: None)()
    if revision:
        return f"{kind}:{message_id}:{revision}"
    payload = json.dumps(
        [content, embeds or [], attachments or [], components or []],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{kind}:{message_id}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


@dataclass
class BridgeJob:
    id: str
    actor_ref: str
    command: str
    status: str = "queued"
    events: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    recovery_instructions: str | None = None
    created_at: int = field(default_factory=_now_ms)
    updated_at: int = field(default_factory=_now_ms)
    lottery_message_id: int | None = None

    def add_event(
        self,
        kind: str,
        *,
        content: str | None = None,
        embed: discord.Embed | None = None,
        message_id: int | None = None,
    ) -> None:
        self.events.append({
            "sequence": len(self.events) + 1,
            "kind": kind,
            "content": content,
            "embed": _embed_json(embed),
            "messageId": str(message_id) if message_id is not None else None,
            "createdAt": _now_ms(),
        })
        self.updated_at = _now_ms()

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "events": self.events,
            "errorCode": self.error_code,
            "errorMessage": self.error_message,
            "recoveryInstructions": self.recovery_instructions,
            "lotteryMessageId": str(self.lottery_message_id) if self.lottery_message_id else None,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


class WebActor:
    def __init__(self, actor_ref: str, nickname: str):
        self.id = _actor_id(actor_ref)
        self.actor_ref = actor_ref
        self.actor_type = "web_guest"
        self.name = f"웹 · {nickname}"
        self.display_name = self.name
        self.mention = self.name
        self.bot = False
        self.voice = None


class CaptureMessage:
    def __init__(self, message: discord.Message, channel: "CaptureChannel"):
        self._message = message
        self.channel = channel
        self.id = message.id

    async def edit(self, *, content=None, embed=None, **kwargs):
        result = await self._message.edit(content=content, embed=embed, **kwargs)
        self.channel.job.add_event(
            "message_update", content=content, embed=embed, message_id=self.id,
        )
        return result

    async def add_reaction(self, emoji):
        return await self._message.add_reaction(emoji)

    def __getattr__(self, name: str):
        return getattr(self._message, name)


class CaptureChannel:
    def __init__(self, channel: discord.abc.Messageable, job: BridgeJob):
        self._channel = channel
        self.job = job
        self.id = channel.id

    async def send(self, content=None, *, embed=None, **kwargs):
        message = await self._channel.send(content, embed=embed, **kwargs)
        self.job.add_event("message", content=content, embed=embed, message_id=message.id)
        if self.job.command.split(maxsplit=1)[0].startswith("뽑기"):
            self.job.lottery_message_id = message.id
        return CaptureMessage(message, self)

    def typing(self):
        return self._channel.typing()

    def __getattr__(self, name: str):
        return getattr(self._channel, name)


class WebMessage:
    def __init__(self, command: str, guild: discord.Guild, channel: CaptureChannel, actor: WebActor):
        self.content = command
        self.guild = guild
        self.channel = channel
        self.author = actor


class WebBridge:
    def __init__(self, bot, secret: str, socket_path: Path):
        self.bot = bot
        self.secret = secret.encode("utf-8")
        self.socket_path = socket_path
        self.jobs: dict[str, BridgeJob] = {}
        self.nonces: dict[str, float] = {}
        self.runner: web.AppRunner | None = None
        self.tts_lock = asyncio.Lock()
        self.tts_pending = 0
        self.component_dispatcher = ComponentActionDispatcher(bot)
        self._broadcast_listeners_registered = False
        # Keep the exact bound-method instances so discord.py can reliably
        # unregister them when 003 shuts down or reconnects during a deploy.
        self._broadcast_message_listener = self._on_broadcast_message
        self._broadcast_edit_listener = self._on_broadcast_message_edit
        self._broadcast_delete_listener = self._on_broadcast_message_delete
        self._broadcast_raw_delete_listener = self._on_raw_broadcast_message_delete

    async def _authenticate(self, request: web.Request) -> bytes:
        body = await request.read()
        if len(body) > MAX_BODY_BYTES:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY_BYTES, actual_size=len(body))
        timestamp = request.headers.get("x-lc-timestamp", "")
        nonce = request.headers.get("x-lc-nonce", "")
        signature = request.headers.get("x-lc-signature", "")
        try:
            ts = int(timestamp)
        except ValueError as exc:
            raise web.HTTPUnauthorized(text="invalid bridge timestamp") from exc
        now = int(time.time())
        if abs(now - ts) > 30:
            raise web.HTTPUnauthorized(text="expired bridge request")
        self._cleanup_nonces()
        if not nonce or nonce in self.nonces:
            raise web.HTTPConflict(text="bridge nonce already used")
        body_hash = hashlib.sha256(body).hexdigest()
        # The broadcast read endpoint is query-based. Including its query string
        # prevents a valid HMAC for one guild/cursor from being replayed against
        # a different slice. Existing endpoints have no query and stay unchanged.
        canonical = "\n".join((timestamp, nonce, request.method, request.path_qs, body_hash)).encode("utf-8")
        expected = hmac.new(self.secret, canonical, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise web.HTTPUnauthorized(text="invalid bridge signature")
        self.nonces[nonce] = time.time()
        return body

    def _cleanup_nonces(self) -> None:
        cutoff = time.time() - NONCE_TTL_SECONDS
        self.nonces = {key: created for key, created in self.nonces.items() if created >= cutoff}
        job_cutoff = _now_ms() - JOB_TTL_SECONDS * 1000
        self.jobs = {key: job for key, job in self.jobs.items() if job.updated_at >= job_cutoff}

    async def _json(self, request: web.Request) -> dict[str, Any]:
        body = await self._authenticate(request)
        try:
            value = json.loads(body or b"{}")
        except json.JSONDecodeError as exc:
            raise web.HTTPBadRequest(text="invalid JSON") from exc
        if not isinstance(value, dict):
            raise web.HTTPBadRequest(text="JSON object required")
        return value

    async def health(self, request: web.Request) -> web.Response:
        await self._authenticate(request)
        return web.json_response({
            "ok": self.bot.is_ready(),
            "botNumber": self.bot.bot_number,
            "version": BRIDGE_VERSION,
            "scheduler": _scheduler_health_payload(self.bot),
        })

    async def targets(self, request: web.Request) -> web.Response:
        await self._authenticate(request)
        async with get_db() as db:
            async with db.execute(
                "SELECT guild_id, text_channel_id, voice_channel_id FROM guild_config WHERE bot_number=?",
                (self.bot.bot_number,),
            ) as cur:
                rows = await cur.fetchall()
        targets = []
        for guild_id, text_id, voice_id in rows:
            guild = self.bot.get_guild(guild_id)
            targets.append({
                "guildId": str(guild_id),
                "guildName": guild.name if guild else f"Discord {guild_id}",
                "botNumber": self.bot.bot_number,
                "textChannelId": str(text_id),
                "voiceChannelId": str(voice_id) if voice_id else None,
                "scheduler": _scheduler_health_payload(self.bot),
            })
        return web.json_response({"targets": targets})

    async def broadcast_events(self, request: web.Request) -> web.Response:
        """Return the retained 003-only bot-message feed for one guild."""
        await self._authenticate(request)
        guild_id_raw = request.query.get("guildId", "")
        after_raw = request.query.get("after", "0")
        limit_raw = request.query.get("limit", "100")
        try:
            guild_id = int(guild_id_raw)
            after = int(after_raw)
            limit = int(limit_raw)
        except ValueError as exc:
            raise web.HTTPBadRequest(text="invalid broadcast event cursor") from exc
        if guild_id <= 0 or after < 0:
            raise web.HTTPBadRequest(text="invalid broadcast event cursor")
        channel_id = await self._configured_broadcast_channel(guild_id)
        if channel_id is None:
            return web.json_response({"events": [], "nextCursor": after})
        try:
            events, next_cursor = await list_web_broadcast_events(
                guild_id=guild_id, channel_id=channel_id, after=after, limit=limit,
            )
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc
        return web.json_response({"events": events, "nextCursor": next_cursor})

    async def _configured_broadcast_channel(self, guild_id: int) -> int | None:
        """Resolve the only Discord channel whose bot speech may be mirrored."""
        async with get_db() as db:
            async with db.execute(
                "SELECT text_channel_id FROM guild_config WHERE guild_id=? AND bot_number=?",
                (guild_id, 3),
            ) as cur:
                row = await cur.fetchone()
        return int(row["text_channel_id"]) if row and row["text_channel_id"] else None

    async def _is_broadcast_message(self, message: discord.Message) -> bool:
        if getattr(self.bot, "bot_number", None) != 3:
            return False
        guild = getattr(message, "guild", None)
        author = getattr(message, "author", None)
        bot_user = getattr(self.bot, "user", None)
        channel = getattr(message, "channel", None)
        if not guild or not author or not bot_user or not channel:
            return False
        if getattr(author, "id", None) != getattr(bot_user, "id", None):
            return False
        configured_channel = await self._configured_broadcast_channel(int(guild.id))
        return configured_channel is not None and configured_channel == int(channel.id)

    async def _store_broadcast_message(self, message: discord.Message, kind: str) -> None:
        if not await self._is_broadcast_message(message):
            return
        guild_id = int(message.guild.id)
        channel_id = int(message.channel.id)
        message_id = int(message.id)
        if kind == "message_delete":
            await append_web_broadcast_event(
                event_key=_event_key(message_id=message_id, kind=kind),
                guild_id=guild_id,
                channel_id=channel_id,
                message_id=message_id,
                kind=kind,
                content=None,
                embeds=[],
                attachments=[],
                components=[],
            )
            return

        content = _message_content(message)
        embeds = _message_embeds(message)
        attachments = _message_attachments(message)
        components = _message_components(message)
        await append_web_broadcast_event(
            event_key=_event_key(
                message_id=message_id,
                kind=kind,
                content=content,
                embeds=embeds,
                attachments=attachments,
                components=components,
                edited_at=getattr(message, "edited_at", None),
            ),
            guild_id=guild_id,
            channel_id=channel_id,
            message_id=message_id,
            kind=kind,
            content=content,
            embeds=embeds,
            attachments=attachments,
            components=components,
        )

    async def _capture_broadcast(self, message: discord.Message, kind: str) -> None:
        try:
            await self._store_broadcast_message(message, kind)
        except Exception as exc:
            # Message delivery must never be interrupted by a best-effort web
            # mirror. Avoid outputting event content, HMAC material, or DB paths.
            print(f"[web-bridge] broadcast capture failed ({type(exc).__name__})")

    async def _on_broadcast_message(self, message: discord.Message) -> None:
        await self._capture_broadcast(message, "message")

    async def _on_broadcast_message_edit(
        self, _before: discord.Message, after: discord.Message,
    ) -> None:
        await self._capture_broadcast(after, "message_update")

    async def _on_broadcast_message_delete(self, message: discord.Message) -> None:
        await self._capture_broadcast(message, "message_delete")

    async def _on_raw_broadcast_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        """Mirror deletes even after Discord evicts the message from its cache.

        Raw delete events do not include an author. We only emit a tombstone for
        a message that this 003 bridge had already mirrored from its configured
        channel, which keeps human and other-bot deletes out of the web feed.
        """
        try:
            if getattr(self.bot, "bot_number", None) != 3:
                return
            guild_id = getattr(payload, "guild_id", None)
            channel_id = getattr(payload, "channel_id", None)
            message_id = getattr(payload, "message_id", None)
            if guild_id is None or channel_id is None or message_id is None:
                return
            guild_id, channel_id, message_id = int(guild_id), int(channel_id), int(message_id)
            configured_channel = await self._configured_broadcast_channel(guild_id)
            if configured_channel != channel_id:
                return
            if not await has_web_broadcast_message(
                guild_id=guild_id, channel_id=channel_id, message_id=message_id,
            ):
                return
            await append_web_broadcast_event(
                event_key=_event_key(message_id=message_id, kind="message_delete"),
                guild_id=guild_id,
                channel_id=channel_id,
                message_id=message_id,
                kind="message_delete",
                content=None,
                embeds=[],
                attachments=[],
                components=[],
            )
        except Exception as exc:
            print(f"[web-bridge] raw broadcast delete capture failed ({type(exc).__name__})")

    def _register_broadcast_listeners(self) -> None:
        if self._broadcast_listeners_registered or getattr(self.bot, "bot_number", None) != 3:
            return
        self.bot.add_listener(self._broadcast_message_listener, "on_message")
        self.bot.add_listener(self._broadcast_edit_listener, "on_message_edit")
        self.bot.add_listener(self._broadcast_delete_listener, "on_message_delete")
        self.bot.add_listener(self._broadcast_raw_delete_listener, "on_raw_message_delete")
        self._broadcast_listeners_registered = True

    def _remove_broadcast_listeners(self) -> None:
        if not self._broadcast_listeners_registered:
            return
        self.bot.remove_listener(self._broadcast_message_listener, "on_message")
        self.bot.remove_listener(self._broadcast_edit_listener, "on_message_edit")
        self.bot.remove_listener(self._broadcast_delete_listener, "on_message_delete")
        self.bot.remove_listener(self._broadcast_raw_delete_listener, "on_raw_message_delete")
        self._broadcast_listeners_registered = False

    @staticmethod
    def _validate_command(command: str) -> None:
        normalized = command.strip()
        if not normalized or len(normalized.encode("utf-8")) > 4096:
            raise web.HTTPBadRequest(text="command must be 1-4096 bytes")
        lines = [line for line in normalized.splitlines() if line.strip()]
        if len(lines) > 100:
            raise web.HTTPBadRequest(text="command has too many lines")
        head = normalized.split(maxsplit=1)[0]
        if normalized.lower() in BLOCKED_COMMANDS or head.lower() in BLOCKED_HEADS:
            raise web.HTTPForbidden(text="system-impacting command is owner-only")
        if head.lower() in {"v", "ㅍ"}:
            text = normalized[len(head):].strip()
            if not text or len(text) > MAX_TTS_CHARS:
                raise web.HTTPBadRequest(text=f"TTS text must be 1-{MAX_TTS_CHARS} characters")

    async def create_command(self, request: web.Request) -> web.Response:
        data = await self._json(request)
        request_id = str(data.get("requestId", ""))
        command = str(data.get("command", "")).strip()
        actor_ref = str(data.get("actorRef", ""))
        nickname = str(data.get("nickname", "")).strip()
        guild_id_raw = str(data.get("guildId", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", request_id):
            raise web.HTTPBadRequest(text="invalid requestId")
        if not actor_ref or not (2 <= len(nickname) <= 20) or re.search(r"[@\x00-\x1f\x7f]", nickname):
            raise web.HTTPBadRequest(text="invalid web actor")
        try:
            guild_id = int(guild_id_raw)
        except ValueError as exc:
            raise web.HTTPBadRequest(text="invalid guildId") from exc
        self._validate_command(command)
        existing = self.jobs.get(request_id)
        if existing:
            if existing.actor_ref != actor_ref or existing.command != command:
                raise web.HTTPConflict(text="idempotency key conflict")
            return web.json_response(existing.payload())

        is_tts = command == "Z" or command.split(maxsplit=1)[0].lower() in {"v", "ㅍ"}
        if is_tts and self.tts_pending >= MAX_TTS_QUEUE:
            raise web.HTTPTooManyRequests(text="TTS queue is full")
        job = BridgeJob(id=request_id, actor_ref=actor_ref, command=command)
        self.jobs[request_id] = job
        if is_tts:
            self.tts_pending += 1
        asyncio.create_task(self._run_command(job, guild_id, nickname, is_tts))
        return web.json_response(job.payload(), status=202)

    async def get_command(self, request: web.Request) -> web.Response:
        await self._authenticate(request)
        job = self.jobs.get(request.match_info["job_id"])
        if not job:
            raise web.HTTPNotFound(text="command not found")
        return web.json_response(job.payload())

    async def component_actions(self, request: web.Request) -> web.Response:
        """Run one registered Discord component through the shared dispatcher.

        Action-level denials deliberately use a 200 response with ``status``
        ``failed``. The leinsterCenter bridge client otherwise turns a useful
        policy code into a generic bridge-unavailable failure before its route
        can map the denial to the appropriate public HTTP status.
        """
        data = await self._json(request)
        request_id = str(data.get("requestId", ""))
        actor_ref = str(data.get("actorRef", ""))
        nickname = str(data.get("nickname", "")).strip()
        actor_type = str(data.get("actorType", ""))
        custom_id = str(data.get("customId", ""))
        try:
            guild_id = int(str(data.get("guildId", "")))
            message_id = int(str(data.get("messageId", "")))
        except ValueError as exc:
            raise web.HTTPBadRequest(text="invalid component target") from exc
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", request_id):
            raise web.HTTPBadRequest(text="invalid requestId")
        if not actor_ref or len(actor_ref) > 200:
            raise web.HTTPBadRequest(text="invalid component actor")
        if actor_type not in {"web_guest", "owner"}:
            raise web.HTTPBadRequest(text="invalid component actor type")
        if actor_type == "web_guest" and (
            not (2 <= len(nickname) <= 20) or re.search(r"[@\x00-\x1f\x7f]", nickname)
        ):
            raise web.HTTPBadRequest(text="invalid web actor")
        if actor_type == "owner" and not nickname:
            nickname = "사장"
        if guild_id <= 0 or message_id <= 0 or not custom_id or len(custom_id) > 100:
            raise web.HTTPBadRequest(text="invalid component target")

        actor = WebActor(actor_ref, nickname)
        actor.actor_type = actor_type
        result = await self.component_dispatcher.dispatch(
            request_id=request_id,
            guild_id=guild_id,
            message_id=message_id,
            custom_id=custom_id,
            actor=actor,
            actor_type=actor_type,
            actor_ref=actor_ref,
            is_owner=actor_type == "owner",
        )
        return web.json_response(result.payload())

    async def _run_command(self, job: BridgeJob, guild_id: int, nickname: str, is_tts: bool) -> None:
        job.status = "running"
        job.updated_at = _now_ms()
        try:
            if not self.bot.is_ready():
                raise RuntimeError("bot is not ready")
            guild = self.bot.get_guild(guild_id)
            if not guild:
                raise RuntimeError("configured Discord server is unavailable")
            async with get_db() as db:
                async with db.execute(
                    "SELECT text_channel_id FROM guild_config WHERE guild_id=? AND bot_number=?",
                    (guild_id, self.bot.bot_number),
                ) as cur:
                    row = await cur.fetchone()
            if not row:
                raise RuntimeError("bot is not configured for this Discord server")
            channel = self.bot.get_channel(row[0])
            if channel is None:
                raise RuntimeError("configured Discord channel is unavailable")

            actor = WebActor(job.actor_ref, nickname)
            mirror_command = job.command.replace("```", "``\u200b`")
            if len(mirror_command) > 1600:
                mirror_command = f"{mirror_command[:1600]}…"
            await channel.send(
                f"🌐 **{actor.display_name}**\n```\n{mirror_command}\n```",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            capture = CaptureChannel(channel, job)
            message = WebMessage(job.command, guild, capture, actor)
            async def dispatch() -> None:
                for cog_name in ("Boss", "TTS", "Market", "Minigame", "Weather", "Help"):
                    cog = self.bot.get_cog(cog_name)
                    listener = getattr(cog, "on_message", None) if cog else None
                    if listener:
                        await listener(message)

                head = job.command.split(maxsplit=1)[0].lower()
                if head in {"v", "ㅍ"}:
                    spoken = job.command[len(head):].strip()
                    await capture.send(f"🔊 {message.author.display_name} TTS · {spoken}")

            if is_tts:
                async with self.tts_lock:
                    await dispatch()
            else:
                await dispatch()
            if not job.events:
                job.error_code = "UNSUPPORTED_COMMAND"
                job.error_message = "웹에서 지원하는 Discord 명령이 아닙니다."
                job.recovery_instructions = "도움말에서 지원 명령을 확인한 뒤 새 요청으로 다시 실행하세요."
                job.status = "failed"
            else:
                job.status = "succeeded"
        except Exception as exc:
            print(f"[web-bridge] command failed ({type(exc).__name__})")
            job.status = "failed"
            job.error_code = "COMMAND_FAILED"
            job.error_message = "명령 실행에 실패했습니다. 003 봇 상태를 확인한 뒤 새 요청으로 다시 시도하세요."
            job.recovery_instructions = "003 봇 연결 상태를 확인한 뒤 같은 명령을 새 요청으로 다시 실행하세요."
        finally:
            if is_tts:
                self.tts_pending = max(0, self.tts_pending - 1)
            job.updated_at = _now_ms()

    async def draw_lottery(self, request: web.Request) -> web.Response:
        data = await self._json(request)
        request_id = str(data.get("requestId", ""))
        actor_ref = str(data.get("actorRef", ""))
        guild_id_raw = str(data.get("guildId", ""))
        try:
            message_id = int(request.match_info["message_id"])
            guild_id = int(guild_id_raw)
        except ValueError as exc:
            raise web.HTTPBadRequest(text="invalid lottery target") from exc
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", request_id) or not actor_ref:
            raise web.HTTPBadRequest(text="invalid lottery request")
        command = f"lottery:{message_id}"
        existing = self.jobs.get(request_id)
        if existing:
            if existing.actor_ref != actor_ref or existing.command != command:
                raise web.HTTPConflict(text="idempotency key conflict")
            return web.json_response(existing.payload())
        job = BridgeJob(id=request_id, actor_ref=actor_ref, command=command)
        self.jobs[request_id] = job
        job.status = "running"
        try:
            guild = self.bot.get_guild(guild_id)
            cog = self.bot.get_cog("Minigame")
            if not guild or not cog:
                raise RuntimeError("minigame unavailable")
            async with get_db() as db:
                async with db.execute(
                    "SELECT text_channel_id FROM guild_config WHERE guild_id=? AND bot_number=?",
                    (guild_id, self.bot.bot_number),
                ) as cur:
                    row = await cur.fetchone()
            channel = self.bot.get_channel(row[0]) if row else None
            if channel is None:
                raise RuntimeError("Discord channel unavailable")
            capture = CaptureChannel(channel, job)
            result = await cog.finish_lottery(message_id, actor_ref, capture)
            if isinstance(result, str):
                job.status = "failed"
                job.error_code = "LOTTERY_REJECTED"
                job.error_message = result
                job.recovery_instructions = "진행 중인 뽑기와 시작한 웹 세션을 확인한 뒤 새 요청으로 다시 실행하세요."
            else:
                job.status = "succeeded"
        except Exception as exc:
            print(f"[web-bridge] lottery failed ({type(exc).__name__})")
            job.status = "failed"
            job.error_code = "LOTTERY_FAILED"
            job.error_message = "추첨에 실패했습니다. 003 봇 상태를 확인한 뒤 새 요청으로 다시 시도하세요."
            job.recovery_instructions = "진행 중인 뽑기와 003 봇 상태를 확인한 뒤 새 요청으로 다시 실행하세요."
        finally:
            job.updated_at = _now_ms()
        return web.json_response(job.payload())

    async def start(self) -> None:
        app = web.Application(client_max_size=MAX_BODY_BYTES)
        app.router.add_get("/internal/v1/health", self.health)
        app.router.add_get("/internal/v1/targets", self.targets)
        app.router.add_get("/internal/v1/broadcast-events", self.broadcast_events)
        app.router.add_post("/internal/v1/component-actions", self.component_actions)
        app.router.add_post("/internal/v1/commands", self.create_command)
        app.router.add_get("/internal/v1/commands/{job_id}", self.get_command)
        app.router.add_post("/internal/v1/lottery/{message_id}/draw", self.draw_lottery)
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.UnixSite(self.runner, str(self.socket_path)).start()
        bridge_gid = os.getenv("BOTAM_BRIDGE_GID", "").strip()
        if bridge_gid:
            os.chown(self.socket_path, -1, int(bridge_gid))
        os.chmod(self.socket_path, 0o660)
        self._register_broadcast_listeners()
        print(f"[web-bridge] bot 003 Unix socket ready: {self.socket_path}")

    async def close(self) -> None:
        self._remove_broadcast_listeners()
        if self.runner:
            await self.runner.cleanup()
        if self.socket_path.exists():
            self.socket_path.unlink()


async def start_web_bridge(bot) -> WebBridge | None:
    enabled = os.getenv("BOTAM_WEB_BRIDGE_ENABLED", "").lower() in {"1", "true", "yes"}
    if not enabled or bot.bot_number != 3:
        return None
    secret = os.getenv("BOTAM_BRIDGE_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("BOTAM_BRIDGE_SECRET must be at least 32 characters")
    socket_path = Path(os.getenv("BOTAM_BRIDGE_SOCKET", "/app/data/botam-003.sock"))
    bridge = WebBridge(bot, secret, socket_path)
    await bridge.start()
    return bridge
