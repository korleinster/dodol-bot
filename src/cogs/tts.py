"""TTS providers and persistent Discord voice-channel playback."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.metadata
import importlib.util
import os
import random
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Mapping

import discord
from discord.ext import commands, tasks

from gtts import gTTS

try:
    import edge_tts
except ImportError:  # pragma: no cover - covered through the safe fallback path
    edge_tts = None

RESTART_KW = {"정신차려", "재시작"}
VOICE_RUNTIME_VERSIONS = {
    "discord.py": "2.7.1",
    "davey": "0.1.6",
    "PyNaCl": "1.5.0",
}
EDGE_TTS_TIMEOUT_SECONDS = 20
DEFAULT_EDGE_VOICE = "ko-KR-SunHiNeural"
DEFAULT_EDGE_RATE = "+8%"
DEFAULT_EDGE_PITCH = "+8Hz"
EDGE_TTS_BOT_NUMBERS = frozenset(range(1, 5))
_EDGE_RATE_RE = re.compile(r"^[+-]\d+%$")
_EDGE_PITCH_RE = re.compile(r"^[+-]\d+Hz$")
_TTS_SYNTHESIS_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tts-synthesis")
VOICE_RECONNECT_ATTEMPTS = 3
VOICE_RECONNECT_BASE_DELAY_SECONDS = 1.0
VOICE_RECONNECT_MAX_DELAY_SECONDS = 8.0
VOICE_DISCONNECT_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class TTSProviderSettings:
    """Resolved, non-secret synthesis settings for one bot instance."""

    provider: str
    voice: str | None = None
    rate: str | None = None
    pitch: str | None = None


def _safe_env_value(
    environ: Mapping[str, str], key: str, default: str, pattern: re.Pattern[str],
) -> str:
    """Use a validated setting or a known-good default without failing playback."""
    value = environ.get(key, default).strip()
    return value if pattern.fullmatch(value) else default


def _safe_edge_voice(environ: Mapping[str, str]) -> str:
    """Keep every bot on the one verified free Edge Korean female voice."""
    value = environ.get("TTS_EDGE_VOICE", DEFAULT_EDGE_VOICE).strip()
    return value if value == DEFAULT_EDGE_VOICE else DEFAULT_EDGE_VOICE


def get_tts_provider_settings(
    bot_number: int, environ: Mapping[str, str] | None = None,
) -> TTSProviderSettings:
    """Select the approved Edge voice independently for bots 001-004.

    Each process receives its own mapped TTS_PROVIDER value. It may opt that
    bot out to gTTS for a quick operational rollback. Unknown bot numbers and
    unknown provider values fail safely to gTTS.
    """
    if bot_number not in EDGE_TTS_BOT_NUMBERS:
        return TTSProviderSettings(provider="gtts")

    environment = os.environ if environ is None else environ
    provider = environment.get("TTS_PROVIDER", "edge").strip().lower()
    if provider != "edge":
        return TTSProviderSettings(provider="gtts")

    return TTSProviderSettings(
        provider="edge",
        voice=_safe_edge_voice(environment),
        rate=_safe_env_value(environment, "TTS_EDGE_RATE", DEFAULT_EDGE_RATE, _EDGE_RATE_RE),
        pitch=_safe_env_value(environment, "TTS_EDGE_PITCH", DEFAULT_EDGE_PITCH, _EDGE_PITCH_RE),
    )


@lru_cache(maxsize=1)
def detect_voice_runtime_capability() -> tuple[bool, str]:
    """Validate the pinned DAVE voice runtime without exposing host details."""
    for distribution, expected in VOICE_RUNTIME_VERSIONS.items():
        safe_name = distribution.upper().replace(".", "_")
        try:
            actual = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            return False, f"{safe_name}_MISSING"
        if actual != expected:
            return False, f"{safe_name}_VERSION_UNSUPPORTED"

    for module, code in (("davey", "DAVEY_IMPORT_MISSING"), ("nacl", "PYNACL_IMPORT_MISSING")):
        try:
            available = importlib.util.find_spec(module) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            available = False
        if not available:
            return False, code

    if shutil.which("ffmpeg") is None:
        return False, "FFMPEG_MISSING"
    return True, "VOICE_RUNTIME_READY"


def _save_tts(text: str, path: str) -> None:
    """Save a gTTS file synchronously, for the default and fallback provider."""
    tts = gTTS(text=text, lang="ko")
    tts.save(path)


async def _save_edge_tts(text: str, path: str, settings: TTSProviderSettings) -> None:
    """Save one Edge Neural TTS file with the explicitly selected voice settings."""
    if edge_tts is None:
        raise RuntimeError("EDGE_TTS_UNAVAILABLE")
    if not settings.voice or not settings.rate or not settings.pitch:
        raise RuntimeError("EDGE_TTS_SETTINGS_INVALID")
    communicator = edge_tts.Communicate(
        text,
        voice=settings.voice,
        rate=settings.rate,
        pitch=settings.pitch,
    )
    await communicator.save(path)


def _remove_file(path: str) -> None:
    """Best-effort cleanup shared by every synthesis and playback failure path."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _discard_edge_task(task: "asyncio.Task[None]", edge_path: str) -> None:
    """Consume a delayed Edge task and remove its provider-private output."""
    try:
        task.exception()
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    _remove_file(edge_path)


async def _cancel_and_drain_edge_task(task: "asyncio.Task[None]", edge_path: str) -> None:
    """Cancel an Edge request without letting a late write replace fallback audio."""
    if not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=1)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass
    if task.done():
        _discard_edge_task(task, edge_path)
    else:
        task.add_done_callback(lambda completed: _discard_edge_task(completed, edge_path))
        _remove_file(edge_path)


async def _save_gtts_private(text: str, path: str) -> None:
    """Generate gTTS privately, then atomically publish it at ``path``.

    A cancelled asyncio task cannot stop the synchronous gTTS worker. Keeping
    its output private ensures a late worker cannot recreate the public temp
    file after cleanup has already happened.
    """
    gtts_path = f"{path}.gtts"
    future = _TTS_SYNTHESIS_EXECUTOR.submit(_save_tts, text, gtts_path)
    wrapped = asyncio.wrap_future(future)
    try:
        await asyncio.shield(wrapped)
        os.replace(gtts_path, path)
    except asyncio.CancelledError:
        future.add_done_callback(lambda _completed: _remove_file(gtts_path))
        _remove_file(gtts_path)
        raise
    except Exception:
        _remove_file(gtts_path)
        raise


def parse_tts_command(content: str) -> str | None:
    """Parse the two explicit manual TTS forms using a literal space."""
    command = content.strip()
    if command.lower().startswith("v ") or command.startswith("ㅍ "):
        text = command[2:].strip()
        return text or None
    return None


class TTS(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bn  = bot.bot_number
        self._connect_locks: dict[int, asyncio.Lock] = {}
        self._voice_incidents: set[int] = set()
        self._voice_connected_guilds: set[int] = set()
        self._voice_runtime_error_code: str | None = None

    def _voice_runtime_ready(self) -> bool:
        ready, code = detect_voice_runtime_capability()
        if ready:
            self._voice_runtime_error_code = None
            return True
        if self._voice_runtime_error_code != code:
            print(f"[TTS] 음성 런타임 사용 불가: {code}")
            self._voice_runtime_error_code = code
        return False

    def _provider_settings(self) -> TTSProviderSettings:
        return get_tts_provider_settings(self.bn)

    async def _synthesize(self, text: str, path: str) -> str:
        """Create one audio file and return the provider that succeeded.

        A timeout or provider error does not prevent an alert from being read:
        the existing gTTS implementation is attempted exactly once as a
        per-process fallback.
        """
        settings = self._provider_settings()
        if settings.provider == "edge":
            # Keep a provider-private path until the edge file is complete. A
            # timed-out request may finish late even after cancellation; it
            # must never overwrite the fallback gTTS file at ``path``.
            edge_path = f"{path}.edge"
            edge_task = asyncio.create_task(_save_edge_tts(text, edge_path, settings))
            try:
                await asyncio.wait_for(
                    asyncio.shield(edge_task),
                    timeout=EDGE_TTS_TIMEOUT_SECONDS,
                )
                os.replace(edge_path, path)
                return "edge"
            except asyncio.CancelledError:
                await _cancel_and_drain_edge_task(edge_task, edge_path)
                _remove_file(path)
                raise
            except Exception as exc:
                print(f"[TTS] Edge 합성 실패, gTTS로 대체: {type(exc).__name__}: {exc}")
                await _cancel_and_drain_edge_task(edge_task, edge_path)

        try:
            await _save_gtts_private(text, path)
            return "gtts"
        except asyncio.CancelledError:
            _remove_file(path)
            raise
        except Exception:
            _remove_file(path)
            raise

    # ── DB 헬퍼 ───────────────────────────────────────────

    async def get_voice_channel(self, guild_id: int) -> int | None:
        from src.db import get_db
        async with get_db() as db:
            async with db.execute(
                "SELECT voice_channel_id FROM guild_config WHERE guild_id=? AND bot_number=?",
                (guild_id, self.bn),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

    async def get_text_channel(self, guild_id: int) -> int | None:
        from src.db import get_db
        async with get_db() as db:
            async with db.execute(
                "SELECT text_channel_id FROM guild_config WHERE guild_id=? AND bot_number=?",
                (guild_id, self.bn),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

    # ── 음성채널 연결 유지 ────────────────────────────────

    @staticmethod
    def _voice_client_is_healthy(
        voice_client: discord.VoiceClient | None,
        expected_channel_id: int,
    ) -> bool:
        """A configured channel alone is never proof of voice connectivity."""
        if voice_client is None:
            return False
        try:
            channel = voice_client.channel
            return bool(
                voice_client.is_connected()
                and channel is not None
                and int(channel.id) == expected_channel_id
            )
        except (AttributeError, TypeError, ValueError):
            return False

    @staticmethod
    def _retry_delay(attempt: int) -> float:
        """Bounded exponential backoff with jitter for Discord voice recovery."""
        bounded = min(
            VOICE_RECONNECT_BASE_DELAY_SECONDS * (2 ** max(0, attempt - 1)),
            VOICE_RECONNECT_MAX_DELAY_SECONDS,
        )
        return bounded + random.uniform(0, bounded * 0.25)

    def _set_voice_health(
        self,
        guild_id: int,
        *,
        configured: bool,
        state: str,
        error_code: str | None = None,
        next_retry_at: int | None = None,
    ) -> None:
        from src.runtime_health import update_voice_health
        update_voice_health(
            self.bot,
            guild_id,
            configured=configured,
            state=state,
            error_code=error_code,
            next_retry_at=next_retry_at,
        )

    def _schedule_voice_alert(self, guild_id: int, text: str) -> None:
        """Alert asynchronously so an alert outage cannot hold a reconnect lock."""
        async def send() -> None:
            try:
                from src.utils.notify import alert
                await alert(self.bot, self.bn, text)
            except Exception:
                return
        asyncio.create_task(send(), name=f"voice-alert-{self.bn}-{guild_id}")

    def _open_voice_incident(self, guild_id: int, error_code: str) -> None:
        if guild_id in self._voice_incidents:
            return
        self._voice_incidents.add(guild_id)
        print(f"[TTS] 음성 연결 이상 감지 (guild={guild_id}, {error_code})")
        self._schedule_voice_alert(
            guild_id,
            f"음성 연결 이상을 감지해 안전하게 재연결을 시도합니다 ({error_code}).",
        )

    def _close_voice_incident(self, guild_id: int) -> None:
        if guild_id not in self._voice_incidents:
            return
        self._voice_incidents.discard(guild_id)
        print(f"[TTS] 음성 연결 복구 (guild={guild_id})")
        self._schedule_voice_alert(guild_id, "음성 연결이 복구되었습니다.")

    async def _cleanup_voice_client(self, voice_client: discord.VoiceClient) -> None:
        """Bound a stale-session cleanup before a new connect attempt."""
        try:
            await asyncio.wait_for(
                voice_client.disconnect(force=True),
                timeout=VOICE_DISCONNECT_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return
        except Exception:
            # The old client can already be half-closed. A subsequent connect is
            # still serialized, so do not let this best-effort cleanup leak out.
            pass

    async def ensure_connected(self, guild: discord.Guild) -> discord.VoiceClient | None:
        """Verify real voice liveness, then reconnect safely when needed."""
        vc_id = await self.get_voice_channel(guild.id)
        if not vc_id:
            self._voice_connected_guilds.discard(guild.id)
            self._voice_incidents.discard(guild.id)
            self._set_voice_health(
                guild.id,
                configured=False,
                state="unconfigured",
            )
            return None

        if not self._voice_runtime_ready():
            self._set_voice_health(
                guild.id,
                configured=True,
                state="unavailable",
                error_code="VOICE_RUNTIME_UNAVAILABLE",
            )
            self._open_voice_incident(guild.id, "VOICE_RUNTIME_UNAVAILABLE")
            return None

        vc_channel = guild.get_channel(vc_id)
        if not isinstance(vc_channel, discord.VoiceChannel):
            self._set_voice_health(
                guild.id,
                configured=True,
                state="unavailable",
                error_code="VOICE_CHANNEL_UNAVAILABLE",
            )
            self._open_voice_incident(guild.id, "VOICE_CHANNEL_UNAVAILABLE")
            return None

        voice_client: discord.VoiceClient | None = guild.voice_client  # type: ignore
        if self._voice_client_is_healthy(voice_client, vc_id):
            self._voice_connected_guilds.add(guild.id)
            self._set_voice_health(guild.id, configured=True, state="connected")
            self._close_voice_incident(guild.id)
            return voice_client

        # A healthy connection to the wrong room is stale too.  It is cleaned
        # up rather than moved blindly, since a bad websocket can report a
        # channel while no longer carrying audio.
        # discord.py may clear ``guild.voice_client`` immediately after a
        # disconnect. Keep observed liveness separately so that transition is
        # still recorded as a recovery incident instead of a fresh startup.
        previously_healthy = guild.id in self._voice_connected_guilds
        if previously_healthy:
            self._open_voice_incident(guild.id, "VOICE_CONNECTION_LOST")

        lock = self._connect_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            # Another task may have restored the client while this task waited.
            voice_client = guild.voice_client  # type: ignore
            if self._voice_client_is_healthy(voice_client, vc_id):
                self._voice_connected_guilds.add(guild.id)
                self._set_voice_health(guild.id, configured=True, state="connected")
                self._close_voice_incident(guild.id)
                return voice_client

            if voice_client:
                await self._cleanup_voice_client(voice_client)

            for attempt in range(1, VOICE_RECONNECT_ATTEMPTS + 1):
                delay = self._retry_delay(attempt) if attempt < VOICE_RECONNECT_ATTEMPTS else None
                next_retry_at = int((time.time() + delay) * 1000) if delay is not None else None
                self._set_voice_health(
                    guild.id,
                    configured=True,
                    state="recovering" if previously_healthy else "connecting",
                    error_code="VOICE_RECONNECTING" if previously_healthy else None,
                    next_retry_at=next_retry_at,
                )
                try:
                    # Disable discord.py's unbounded reconnect loop. This
                    # bounded, jittered worker owns all retry timing instead.
                    candidate = await vc_channel.connect(timeout=15.0, reconnect=False)
                    if self._voice_client_is_healthy(candidate, vc_id):
                        self._voice_connected_guilds.add(guild.id)
                        self._set_voice_health(guild.id, configured=True, state="connected")
                        self._close_voice_incident(guild.id)
                        print(f"[TTS] 음성채널 연결 확인: {vc_channel.name}")
                        return candidate
                    await self._cleanup_voice_client(candidate)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(
                        f"[TTS] 음성채널 연결 시도 {attempt}/{VOICE_RECONNECT_ATTEMPTS} 실패 "
                        f"({vc_channel.name}, {type(exc).__name__})",
                    )

                if attempt < VOICE_RECONNECT_ATTEMPTS:
                    assert delay is not None
                    # Store the exact scheduled retry rather than assuming the
                    # caller's configured room proves anything about liveness.
                    self._set_voice_health(
                        guild.id,
                        configured=True,
                        state="recovering" if previously_healthy else "connecting",
                        error_code="VOICE_RECONNECTING" if previously_healthy else None,
                        next_retry_at=int((time.time() + delay) * 1000),
                    )
                    await asyncio.sleep(delay)

            self._set_voice_health(
                guild.id,
                configured=True,
                state="unavailable",
                error_code="VOICE_RECONNECT_EXHAUSTED",
            )
            self._open_voice_incident(guild.id, "VOICE_RECONNECT_EXHAUSTED")
            return None

    @commands.Cog.listener()
    async def on_ready(self):
        """봇 준비 시 설정된 모든 길드 음성채널에 연결"""
        await asyncio.sleep(2)  # 봇 초기화 대기
        for guild in self.bot.guilds:
            await self.ensure_connected(guild)
        if not self.voice_keepalive.is_running():
            self.voice_keepalive.start()

    @tasks.loop(seconds=60)
    async def voice_keepalive(self):
        """Check actual voice state every minute and perform bounded recovery."""
        for guild in self.bot.guilds:
            await self.ensure_connected(guild)

    @voice_keepalive.before_loop
    async def before_keepalive(self):
        await self.bot.wait_until_ready()

    def cog_unload(self):
        self.voice_keepalive.cancel()

    # ── on_message ────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        content = message.content.strip()
        if not content:
            return

        assigned = await self.get_text_channel(message.guild.id)
        if not assigned or message.channel.id != assigned:
            return

        text = parse_tts_command(content)
        if text is not None:
            is_web = getattr(message.author, "actor_type", "discord") == "web_guest"
            played = await self.speak(message.guild, text, wait_until_complete=is_web)
            if is_web and not played:
                raise RuntimeError("TTS playback failed")
        elif content.lower() in RESTART_KW:
            await self._restart(message)

    # ── speak (외부에서 호출 가능) ────────────────────────

    async def speak(self, guild: discord.Guild, text: str, *, wait_until_complete: bool = False) -> bool:
        if not self._voice_runtime_ready():
            return False
        # Fail before generating a file or touching the network when this exact
        # bot+guild target has no configured voice channel.
        if not await self.get_voice_channel(guild.id):
            return False

        # 1. TTS 파일 먼저 생성
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp_path = f.name

        try:
            provider = await self._synthesize(text, tmp_path)
            print(f"[TTS] 파일 생성 완료 ({provider}): {tmp_path}")
        except asyncio.CancelledError:
            _remove_file(tmp_path)
            raise
        except Exception as e:
            print(f"[TTS] 파일 생성 실패: {type(e).__name__}: {e}")
            _remove_file(tmp_path)
            return False

        # 2. 연결 확인 (이미 연결 중이면 그대로, 아니면 재연결)
        voice_client = await self.ensure_connected(guild)
        if not voice_client:
            print("[TTS] 음성채널 연결 없음 — 재생 취소")
            _remove_file(tmp_path)
            return False

        # 3. 재생
        loop = asyncio.get_running_loop()
        playback_result = loop.create_future() if wait_until_complete else None

        def after(err):
            if err:
                print(f"[TTS] 재생 오류: {type(err).__name__}: {err}")
            _remove_file(tmp_path)
            if playback_result:
                def resolve_playback():
                    if not playback_result.done():
                        playback_result.set_result(err is None)
                loop.call_soon_threadsafe(resolve_playback)

        try:
            if voice_client.is_playing():
                voice_client.stop()
            source = await discord.FFmpegOpusAudio.from_probe(tmp_path)
            voice_client.play(source, after=after)
            print(f"[TTS] 재생 시작: {text[:30]}")
            if playback_result:
                try:
                    return await asyncio.wait_for(playback_result, timeout=120)
                except asyncio.TimeoutError:
                    if voice_client.is_playing():
                        voice_client.stop()
                    _remove_file(tmp_path)
                    print("[TTS] 웹 재생 완료 대기 시간 초과")
                    return False
                except asyncio.CancelledError:
                    if voice_client.is_playing():
                        voice_client.stop()
                    _remove_file(tmp_path)
                    raise
            return True
        except asyncio.CancelledError:
            if voice_client.is_playing():
                voice_client.stop()
            _remove_file(tmp_path)
            raise
        except Exception as e:
            print(f"[TTS] 재생 시작 실패: {type(e).__name__}: {e}")
            _remove_file(tmp_path)
            return False

    async def _restart(self, message: discord.Message) -> None:
        if message.guild and message.guild.voice_client:
            try:
                await message.guild.voice_client.disconnect(force=True)
            except Exception:
                pass
        await message.channel.send("🔄 봇을 재시작합니다...")
        await asyncio.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)


async def setup(bot: commands.Bot):
    await bot.add_cog(TTS(bot))
