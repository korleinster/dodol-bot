"""Discord channel binding, relocation, and leave commands."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import discord
from discord.ext import commands

from src.db import ensure_default_bosses, get_db


_BOT_TARGET_RE = re.compile(r"^(?:뚠뚠봇)?0*([1-4])$")
_DATA_TABLES = ("bosses", "schedules", "contributions")


class BindingConflict(RuntimeError):
    """The target already owns data that must not be overwritten or merged."""


@dataclass(frozen=True)
class BindingMove:
    disconnected_guild_ids: tuple[int, ...]
    source_guild_id: int | None


def _can_manage_guild(author: object) -> bool:
    permissions = getattr(author, "guild_permissions", None)
    return bool(
        getattr(permissions, "administrator", False)
        or getattr(permissions, "manage_guild", False)
    )


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bn = bot.bot_number
        self._binding_lock = asyncio.Lock()

    async def get_config(self, guild_id: int) -> dict | None:
        async with get_db() as db:
            async with db.execute(
                "SELECT * FROM guild_config WHERE guild_id=? AND bot_number=?",
                (guild_id, self.bn),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def _table_row_count(self, db, table: str, guild_id: int) -> int:
        async with db.execute(
            f"SELECT COUNT(*) FROM {table} WHERE guild_id=? AND bot_number=?",
            (guild_id, self.bn),
        ) as cur:
            row = await cur.fetchone()
            return int(row[0])

    async def _data_row_count(self, db, guild_id: int) -> int:
        return sum([
            await self._table_row_count(db, table, guild_id)
            for table in _DATA_TABLES
        ])

    async def move_config(
        self,
        guild_id: int,
        text_channel_id: int,
        voice_channel_id: int | None,
    ) -> BindingMove:
        """Atomically move this bot's one active binding and operational data.

        Historical broadcast and component-claim rows remain attached to their
        original Discord messages. Bosses, schedules, and contributions follow
        the active bot so moving servers does not reset live gameplay state.
        """
        async with self._binding_lock:
            async with get_db() as db:
                await db.execute("BEGIN IMMEDIATE")
                try:
                    async with db.execute(
                        """SELECT guild_id FROM guild_config
                           WHERE bot_number=? AND guild_id<>?
                             AND (text_channel_id IS NOT NULL OR voice_channel_id IS NOT NULL)""",
                        (self.bn, guild_id),
                    ) as cur:
                        active_sources = [int(row[0]) async for row in cur]

                    data_sources = [
                        source for source in active_sources
                        if await self._data_row_count(db, source) > 0
                    ]
                    if len(data_sources) > 1:
                        raise BindingConflict("multiple active data sources")

                    source_guild_id = data_sources[0] if data_sources else (
                        active_sources[0] if len(active_sources) == 1 else None
                    )
                    if source_guild_id is not None:
                        target_count = await self._data_row_count(db, guild_id)
                        if target_count > 0:
                            raise BindingConflict("target already contains bot data")
                        for table in _DATA_TABLES:
                            await db.execute(
                                f"UPDATE {table} SET guild_id=? WHERE guild_id=? AND bot_number=?",
                                (guild_id, source_guild_id, self.bn),
                            )

                    await db.execute(
                        """UPDATE guild_config
                           SET text_channel_id=NULL, voice_channel_id=NULL
                           WHERE bot_number=? AND guild_id<>?""",
                        (self.bn, guild_id),
                    )
                    await db.execute(
                        """INSERT INTO guild_config
                               (guild_id, bot_number, text_channel_id, voice_channel_id)
                           VALUES (?,?,?,?)
                           ON CONFLICT(guild_id, bot_number) DO UPDATE SET
                               text_channel_id=excluded.text_channel_id,
                               voice_channel_id=excluded.voice_channel_id""",
                        (guild_id, self.bn, text_channel_id, voice_channel_id),
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise

        return BindingMove(tuple(active_sources), source_guild_id)

    async def _disconnect_voice(self, guild_ids: tuple[int, ...]) -> None:
        for guild_id in guild_ids:
            guild = self.bot.get_guild(guild_id)
            voice_client = getattr(guild, "voice_client", None) if guild else None
            if voice_client:
                try:
                    await voice_client.disconnect(force=True)
                except Exception:
                    # A stale Discord voice connection must not roll back the
                    # already committed, authoritative channel binding.
                    pass

    async def _connect_target_voice(self, guild: discord.Guild) -> None:
        tts = self.bot.get_cog("TTS")
        ensure_connected = getattr(tts, "ensure_connected", None) if tts else None
        if ensure_connected:
            await ensure_connected(guild)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        content = message.content.strip()
        if content:
            await self._dispatch(message, content)

    async def _dispatch(self, message: discord.Message, cmd: str):
        parts = cmd.split()
        if not parts:
            return
        head = parts[0].lower()

        if head == "소환" and len(parts) == 1:
            await self._cmd_list_bots(message)
            return
        if head == "소환" and len(parts) == 2:
            await self._cmd_summon(message, parts[1])
            return

        config = await self.get_config(message.guild.id)
        if not config or config.get("text_channel_id") != message.channel.id:
            return

        if head == "설정" and len(parts) == 1:
            await self._cmd_status(message, config)
        elif head in {"음성나가기", "채팅나가기", "전체나가기"} and len(parts) == 1:
            await self._cmd_leave(message, head)

    async def _cmd_list_bots(self, message: discord.Message):
        """Show this server's bindings; only one bot process answers."""
        async with get_db() as db:
            async with db.execute(
                """SELECT * FROM guild_config
                   WHERE guild_id=? AND text_channel_id IS NOT NULL
                   ORDER BY bot_number""",
                (message.guild.id,),
            ) as cur:
                rows = [dict(row) async for row in cur]

        if rows and self.bn != int(rows[0]["bot_number"]):
            return
        if not rows and self.bn != 1:
            return
        if not rows:
            await message.channel.send(
                "아직 소환된 봇이 없습니다. `소환 뚠뚠봇001`으로 이 채널에 봇을 배치하세요.",
            )
            return

        lines = []
        for row in rows:
            text_channel = message.guild.get_channel(row["text_channel_id"])
            voice_channel = message.guild.get_channel(row["voice_channel_id"]) if row["voice_channel_id"] else None
            lines.append(
                f"**뚠뚠봇{row['bot_number']:03d}**  |  명령: "
                f"{text_channel.mention if text_channel else '미설정'}  |  "
                f"음성: {voice_channel.name if voice_channel else '미설정'}",
            )

        await message.channel.send(embed=discord.Embed(
            title="📋 뚠뚠봇 배치 현황",
            description="\n".join(lines),
            color=0x5865F2,
        ))

    async def _cmd_summon(self, message: discord.Message, target: str):
        match = _BOT_TARGET_RE.fullmatch(target)
        if not match or int(match.group(1)) != self.bn:
            return
        if not _can_manage_guild(message.author):
            await message.channel.send("이 명령은 서버 관리자만 사용할 수 있습니다.")
            return

        voice_channel_id = None
        if message.author.voice and message.author.voice.channel:
            voice_channel_id = message.author.voice.channel.id

        try:
            move = await self.move_config(
                message.guild.id,
                message.channel.id,
                voice_channel_id,
            )
        except BindingConflict:
            await message.channel.send(
                "기존 데이터와 대상 서버 데이터가 모두 있어 자동으로 합칠 수 없습니다. "
                "데이터를 삭제하거나 덮어쓰지 않았습니다.",
            )
            return

        await self._disconnect_voice(move.disconnected_guild_ids)
        await ensure_default_bosses(message.guild.id, self.bn)
        if voice_channel_id:
            await self._connect_target_voice(message.guild)

        voice_channel = message.guild.get_channel(voice_channel_id) if voice_channel_id else None
        embed = discord.Embed(title=f"✅ 뚠뚠봇{self.bn:03d} 배치 완료", color=0x57F287)
        embed.add_field(name="명령 채널", value=message.channel.mention)
        embed.add_field(
            name="음성 채널",
            value=voice_channel.name if voice_channel else "미설정 (음성방 입장 후 소환하세요)",
        )
        if move.source_guild_id is not None:
            embed.set_footer(text="기존 보스·예약·기여 데이터를 그대로 옮겼습니다.")
        await message.channel.send(embed=embed)

    async def _cmd_status(self, message: discord.Message, config: dict):
        text_channel = message.guild.get_channel(config["text_channel_id"])
        voice_channel = message.guild.get_channel(config["voice_channel_id"]) if config["voice_channel_id"] else None
        embed = discord.Embed(title=f"⚙️ 뚠뚠봇{self.bn:03d} 설정", color=0x5865F2)
        embed.add_field(name="명령 채널", value=text_channel.mention if text_channel else "미설정")
        embed.add_field(name="음성 채널", value=voice_channel.name if voice_channel else "미설정")
        await message.channel.send(embed=embed)

    async def _cmd_leave(self, message: discord.Message, command: str):
        if not _can_manage_guild(message.author):
            await message.channel.send("이 명령은 서버 관리자만 사용할 수 있습니다.")
            return

        clear_text = command in {"채팅나가기", "전체나가기"}
        clear_voice = command in {"음성나가기", "전체나가기"}

        # Chat-leave confirmation has to be delivered before its route is
        # intentionally cleared. No gameplay data is touched by this update.
        await message.channel.send(
            f"✅ 뚠뚠봇{self.bn:03d}의 "
            f"{'음성·채팅' if clear_text and clear_voice else '채팅' if clear_text else '음성'} 연결을 해제했습니다. "
            "보스·예약·기여 데이터는 그대로 보존됩니다.",
        )
        async with get_db() as db:
            assignments = []
            if clear_text:
                assignments.append("text_channel_id=NULL")
            if clear_voice:
                assignments.append("voice_channel_id=NULL")
            await db.execute(
                f"UPDATE guild_config SET {', '.join(assignments)} WHERE guild_id=? AND bot_number=?",
                (message.guild.id, self.bn),
            )
            await db.commit()

        if clear_voice and message.guild.voice_client:
            try:
                await message.guild.voice_client.disconnect(force=True)
            except Exception:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))
