"""TTS — gTTS (Google TTS), 음성채널 상시 연결"""
import asyncio
import os
import sys
import tempfile
from functools import partial

import discord
from discord.ext import commands, tasks

from gtts import gTTS

RESTART_KW = {"정신차려", "재시작"}


def _save_tts(text: str, path: str) -> None:
    """동기 함수 — executor에서 실행"""
    tts = gTTS(text=text, lang="ko")
    tts.save(path)


class TTS(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bn  = bot.bot_number
        self._connecting: set[int] = set()  # 현재 연결 시도 중인 guild_id

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

    async def ensure_connected(self, guild: discord.Guild) -> discord.VoiceClient | None:
        """음성채널에 연결되어 있지 않으면 연결. 이미 연결 중이면 그대로 반환."""
        vc_id = await self.get_voice_channel(guild.id)
        if not vc_id:
            return None

        vc_channel = guild.get_channel(vc_id)
        if not isinstance(vc_channel, discord.VoiceChannel):
            return None

        voice_client: discord.VoiceClient | None = guild.voice_client  # type: ignore

        if voice_client and voice_client.is_connected():
            if voice_client.channel.id != vc_id:
                await voice_client.move_to(vc_channel)
            return voice_client

        # 동시 연결 시도 방지 — 다른 코루틴이 이미 연결 중이면 잠시 대기 후 재확인
        if guild.id in self._connecting:
            await asyncio.sleep(2.0)
            vc: discord.VoiceClient | None = guild.voice_client  # type: ignore
            return vc if (vc and vc.is_connected()) else None

        self._connecting.add(guild.id)
        try:
            # 좀비 연결 강제 해제
            if voice_client:
                try:
                    await voice_client.disconnect(force=True)
                    await asyncio.sleep(0.5)
                except Exception:
                    pass

            # reconnect=False: discord.py 자동 재연결 비활성화
            # → 연결이 끊기면 voice_keepalive(60s)가 다음 재연결을 담당
            # reconnect=True 일 때 UDP 소켓 실패 시 1~3초마다 재연결 루프 발생
            voice_client = await vc_channel.connect(timeout=15.0, reconnect=False)
            print(f"[TTS] 음성채널 연결: {vc_channel.name}")
            return voice_client
        except Exception as e:
            print(f"[TTS] 음성채널 연결 실패 ({vc_channel.name}): {type(e).__name__}: {e}")
            return None
        finally:
            self._connecting.discard(guild.id)

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
        """30초마다 연결 상태 확인 및 재연결"""
        for guild in self.bot.guilds:
            vc_id = await self.get_voice_channel(guild.id)
            if not vc_id:
                continue
            voice_client: discord.VoiceClient | None = guild.voice_client  # type: ignore
            if not voice_client or not voice_client.is_connected():
                await self.ensure_connected(guild)

    @voice_keepalive.before_loop
    async def before_keepalive(self):
        await self.bot.wait_until_ready()

    # ── on_message ────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if (message.author.bot and message.author.id != getattr(self.bot, "tester_id", 0)) or not message.guild:
            return
        content = message.content.strip()
        if not content:
            return

        assigned = await self.get_text_channel(message.guild.id)
        if assigned and message.channel.id != assigned:
            return

        cmd = content

        if cmd.lower().startswith("v ") or cmd.startswith("ㅍ "):
            text = cmd[2:].strip()
            if text:
                await self.speak(message.guild, text)
        elif cmd.lower() in RESTART_KW:
            await self._restart(message)

    # ── speak (외부에서 호출 가능) ────────────────────────

    async def speak(self, guild: discord.Guild, text: str) -> None:
        # 1. TTS 파일 먼저 생성
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp_path = f.name

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, partial(_save_tts, text, tmp_path))
            print(f"[TTS] 파일 생성 완료: {tmp_path}")
        except Exception as e:
            print(f"[TTS] 파일 생성 실패: {type(e).__name__}: {e}")
            return

        # 2. 연결 확인 (이미 연결 중이면 그대로, 아니면 재연결)
        voice_client = await self.ensure_connected(guild)
        if not voice_client:
            print("[TTS] 음성채널 연결 없음 — 재생 취소")
            return

        # 3. 재생
        def after(err):
            if err:
                print(f"[TTS] 재생 오류: {type(err).__name__}: {err}")
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        try:
            if voice_client.is_playing():
                voice_client.stop()
            source = await discord.FFmpegOpusAudio.from_probe(tmp_path)
            voice_client.play(source, after=after)
            print(f"[TTS] 재생 시작: {text[:30]}")
        except Exception as e:
            print(f"[TTS] 재생 시작 실패: {type(e).__name__}: {e}")

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
