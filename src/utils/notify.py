"""운영 알림 유틸리티

예외 발생 / 재시작 등 운영 이벤트를 Discord + Telegram으로 동시 발송.
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 환경변수 필요.
"""
from __future__ import annotations

import os
import traceback

import aiohttp
import discord
from discord.ext import commands


_TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


async def send_telegram(text: str) -> None:
    """텔레그램으로 단순 텍스트 메시지 발송."""
    if not _TELEGRAM_TOKEN or not _TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{_TELEGRAM_TOKEN}/sendMessage"
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(url, data={
                "chat_id": _TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
            })
    except Exception:
        pass  # 알림 실패가 봇 동작에 영향을 주지 않도록


async def send_discord_alert(bot: commands.Bot, bot_number: int, text: str) -> None:
    """해당 봇 번호의 모든 Discord 채널에 경고 메시지 발송."""
    from src.db import get_db
    try:
        async with get_db() as db:
            async with db.execute(
                "SELECT text_channel_id FROM guild_config WHERE bot_number=?",
                (bot_number,),
            ) as cur:
                rows = await cur.fetchall()
    except Exception:
        return

    embed = discord.Embed(
        title="⚠️ 운영 알림",
        description=text,
        color=0xFF0000,
    )
    for (ch_id,) in rows:
        ch = bot.get_channel(ch_id)
        if isinstance(ch, discord.TextChannel):
            try:
                await ch.send(embed=embed)
            except Exception:
                pass


async def alert(bot: commands.Bot, bot_number: int, text: str) -> None:
    """Discord + Telegram 동시 발송."""
    await send_discord_alert(bot, bot_number, text)
    await send_telegram(f"[도돌봇{bot_number:03d}] {text}")
