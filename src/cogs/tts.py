"""TTS — gTTS (Google TTS)"""
import asyncio
import os
import sys
import tempfile
from functools import partial

import discord
from discord.ext import commands

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
        vc_id = await self.get_voice_channel(guild.id)
        if not vc_id:
            return

        vc_channel = guild.get_channel(vc_id)
        if not isinstance(vc_channel, discord.VoiceChannel):
            return

        voice_client: discord.VoiceClient | None = guild.voice_client  # type: ignore
        try:
            if voice_client and voice_client.is_connected():
                if voice_client.channel.id != vc_id:
                    await voice_client.move_to(vc_channel)
            else:
                # 좀비 연결(is_connected=False인데 voice_client 객체는 존재) 강제 해제
                if voice_client:
                    try:
                        await voice_client.disconnect(force=True)
                        await asyncio.sleep(0.5)  # Discord가 disconnect 처리할 시간 확보
                    except Exception:
                        pass
                voice_client = await vc_channel.connect(timeout=60.0)
        except Exception as e:
            print(f"[TTS] 음성채널 연결 실패 ({vc_channel.name}): {type(e).__name__}: {e}")
            return

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp_path = f.name

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, partial(_save_tts, text, tmp_path))
            print(f"[TTS] TTS 파일 생성 완료: {tmp_path}")
        except Exception as e:
            print(f"[TTS] TTS 파일 생성 실패: {type(e).__name__}: {e}")
            return

        def after(err):
            if err:
                print(f"[TTS] 재생 중 오류: {type(err).__name__}: {err}")
            asyncio.run_coroutine_threadsafe(self._maybe_disconnect(voice_client), self.bot.loop)

        try:
            if voice_client.is_playing():
                voice_client.stop()
            source = await discord.FFmpegOpusAudio.from_probe(tmp_path)
            voice_client.play(source, after=after)
            print(f"[TTS] 재생 시작: {text[:30]}")
        except Exception as e:
            print(f"[TTS] 재생 시작 실패: {type(e).__name__}: {e}")

    async def _maybe_disconnect(self, vc: discord.VoiceClient) -> None:
        await asyncio.sleep(1)
        if not vc.is_playing():
            await vc.disconnect()

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
