"""TTS — edge-tts SunHiNeural"""
import asyncio
import io
import tempfile

import discord
from discord.ext import commands

import edge_tts

VOICE = "ko-KR-SunHiNeural"
PREFIX = "."


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
        if message.author.bot or not message.guild:
            return
        content = message.content.strip()
        if not content.startswith(PREFIX):
            return

        assigned = await self.get_text_channel(message.guild.id)
        if assigned and message.channel.id != assigned:
            return

        cmd = content[len(PREFIX):].strip()

        if cmd.lower().startswith("v "):
            text = cmd[2:].strip()
            if text:
                await self.speak(message.guild, text)
        elif cmd.lower() == "정신차려":
            await self._rejoin(message.guild)

    # ── speak (외부에서 호출 가능) ────────────────────────

    async def speak(self, guild: discord.Guild, text: str) -> None:
        vc_id = await self.get_voice_channel(guild.id)
        if not vc_id:
            return

        vc_channel = guild.get_channel(vc_id)
        if not isinstance(vc_channel, discord.VoiceChannel):
            return

        # 현재 음성 연결 확인
        voice_client: discord.VoiceClient | None = guild.voice_client  # type: ignore
        try:
            if voice_client and voice_client.is_connected():
                if voice_client.channel.id != vc_id:
                    await voice_client.move_to(vc_channel)
            else:
                voice_client = await vc_channel.connect()
        except Exception:
            return

        # TTS 생성
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp_path = f.name

        communicate = edge_tts.Communicate(text, VOICE)
        await communicate.save(tmp_path)

        # 재생
        def after(_):
            asyncio.run_coroutine_threadsafe(self._maybe_disconnect(voice_client), self.bot.loop)

        if voice_client.is_playing():
            voice_client.stop()

        voice_client.play(discord.FFmpegPCMAudio(tmp_path), after=after)

    async def _maybe_disconnect(self, vc: discord.VoiceClient) -> None:
        await asyncio.sleep(1)
        if not vc.is_playing():
            await vc.disconnect()

    async def _rejoin(self, guild: discord.Guild) -> None:
        if guild.voice_client:
            await guild.voice_client.disconnect(force=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TTS(bot))
