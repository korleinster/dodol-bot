"""채널 설정 및 소환 명령"""
import discord
from discord.ext import commands
from src.db import get_db

PREFIX = "."


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bn = bot.bot_number

    # ── 헬퍼 ──────────────────────────────────────────────

    async def get_config(self, guild_id: int) -> dict | None:
        async with get_db() as db:
            async with db.execute(
                "SELECT * FROM guild_config WHERE guild_id=? AND bot_number=?",
                (guild_id, self.bn),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def set_config(self, guild_id: int, text_ch: int, voice_ch: int | None) -> None:
        async with get_db() as db:
            await db.execute(
                """INSERT INTO guild_config (guild_id, bot_number, text_channel_id, voice_channel_id)
                   VALUES (?,?,?,?)
                   ON CONFLICT(guild_id, bot_number) DO UPDATE SET
                       text_channel_id=excluded.text_channel_id,
                       voice_channel_id=excluded.voice_channel_id""",
                (guild_id, self.bn, text_ch, voice_ch),
            )
            await db.commit()

    def is_command_channel(self, message: discord.Message) -> bool:
        """메시지가 이 봇의 명령 채널에서 온 것인지 확인"""
        return True  # 소환 전에는 모든 채널 허용; 소환 후 필터링은 boss.py에서

    # ── 이벤트 ────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if (message.author.bot and message.author.id != getattr(self.bot, "tester_id", 0)) or not message.guild:
            return
        content = message.content.strip()
        if not content.startswith(PREFIX):
            return

        cmd = content[len(PREFIX):].strip()
        await self._dispatch(message, cmd)

    # ── 명령 파서 ─────────────────────────────────────────

    async def _dispatch(self, message: discord.Message, cmd: str):
        parts = cmd.split()
        if not parts:
            return

        head = parts[0].lower()

        if head == "소환" and len(parts) == 1:
            await self._cmd_list_bots(message)

        elif head == "소환" and len(parts) == 2:
            await self._cmd_summon(message, parts[1])

        elif head == "설정":
            await self._cmd_status(message)

    # ── .소환 ─────────────────────────────────────────────

    async def _cmd_list_bots(self, message: discord.Message):
        """서버 내 모든 도돌봇 설정 현황"""
        async with get_db() as db:
            async with db.execute(
                "SELECT * FROM guild_config WHERE guild_id=?",
                (message.guild.id,),
            ) as cur:
                rows = [dict(r) async for r in cur]

        if not rows:
            await message.channel.send("아직 소환된 봇이 없습니다. `.소환 도돌봇001` 으로 이 채널에 봇을 배치하세요.")
            return

        lines = []
        for r in sorted(rows, key=lambda x: x["bot_number"]):
            n = r["bot_number"]
            tch = message.guild.get_channel(r["text_channel_id"])
            vch = message.guild.get_channel(r["voice_channel_id"]) if r["voice_channel_id"] else None
            lines.append(
                f"**도돌봇{n:03d}**  |  명령: {tch.mention if tch else '미설정'}  |  음성: {vch.name if vch else '미설정'}"
            )

        embed = discord.Embed(title="📋 도돌봇 배치 현황", description="\n".join(lines), color=0x5865F2)
        await message.channel.send(embed=embed)

    # ── .소환 nnn ─────────────────────────────────────────

    async def _cmd_summon(self, message: discord.Message, target: str):
        """도돌봇nnn 을 현재 음성/텍스트 채널로 배치"""
        try:
            target_num = int(target.lstrip("도돌봇").lstrip("0") or "0")
        except ValueError:
            return

        if target_num != self.bn:
            return  # 다른 봇 번호 — 해당 봇이 처리

        voice_ch_id = None
        if message.author.voice and message.author.voice.channel:
            voice_ch_id = message.author.voice.channel.id

        await self.set_config(message.guild.id, message.channel.id, voice_ch_id)

        tch = message.channel
        vch = message.guild.get_channel(voice_ch_id) if voice_ch_id else None

        embed = discord.Embed(
            title=f"✅ 도돌봇{self.bn:03d} 배치 완료",
            color=0x57F287,
        )
        embed.add_field(name="명령 채널", value=tch.mention)
        embed.add_field(name="음성 채널", value=vch.name if vch else "미설정 (음성방 입장 후 소환하세요)")
        await message.channel.send(embed=embed)

    # ── .설정 ─────────────────────────────────────────────

    async def _cmd_status(self, message: discord.Message):
        cfg = await self.get_config(message.guild.id)
        if not cfg:
            await message.channel.send(f"도돌봇{self.bn:03d} 은 아직 배치되지 않았습니다. `.소환 도돌봇{self.bn:03d}` 을 입력하세요.")
            return

        tch = message.guild.get_channel(cfg["text_channel_id"])
        vch = message.guild.get_channel(cfg["voice_channel_id"]) if cfg["voice_channel_id"] else None

        embed = discord.Embed(title=f"⚙️ 도돌봇{self.bn:03d} 설정", color=0x5865F2)
        embed.add_field(name="명령 채널", value=tch.mention if tch else "미설정")
        embed.add_field(name="음성 채널", value=vch.name if vch else "미설정")
        await message.channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))
