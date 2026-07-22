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

from src.db import get_db


BRIDGE_VERSION = 1
MAX_BODY_BYTES = 8 * 1024
MAX_TTS_CHARS = 200
MAX_TTS_QUEUE = 3
NONCE_TTL_SECONDS = 60
JOB_TTL_SECONDS = 15 * 60
BLOCKED_COMMANDS = {"재시작", "정신차려"}
BLOCKED_HEADS = {"소환", "설정"}


def _now_ms() -> int:
    return int(time.time() * 1000)


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
    }


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
        canonical = "\n".join((timestamp, nonce, request.method, request.path, body_hash)).encode("utf-8")
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
            "ok": self.bot.is_ready(), "botNumber": self.bot.bot_number, "version": BRIDGE_VERSION,
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
            })
        return web.json_response({"targets": targets})

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
        print(f"[web-bridge] bot 003 Unix socket ready: {self.socket_path}")

    async def close(self) -> None:
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
