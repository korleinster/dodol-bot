"""텔레그램 → Discord 공지 리스너
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 환경변수 필요.
봇-001 컨테이너에서만 실행되며, /공지 명령어로 전체 채널에 발송.
"""
import asyncio
import os

import discord
from discord.ext import commands
from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from src.db import get_db


async def _broadcast(bot: commands.Bot, text: str) -> int:
    """DB의 모든 배치 채널에 공지 발송. 발송 성공 채널 수 반환."""
    await bot.wait_until_ready()

    async with get_db() as db:
        async with db.execute(
            "SELECT DISTINCT text_channel_id FROM guild_config"
        ) as cur:
            rows = await cur.fetchall()

    sent = 0
    for (ch_id,) in rows:
        ch = bot.get_channel(ch_id)
        if not isinstance(ch, discord.TextChannel):
            continue
        try:
            embed = discord.Embed(
                title="📢 공지사항",
                description=text,
                color=0x5865F2,
            )
            await ch.send(embed=embed)
            sent += 1
        except Exception as e:
            print(f"[텔레그램→Discord] 채널 {ch_id} 발송 실패: {e}")

    return sent


async def run_telegram_listener(bot: commands.Bot) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    raw_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if not token or not raw_chat_id:
        print("[텔레그램] TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 미설정 — 리스너 비활성화")
        return

    allowed_chat_id = int(raw_chat_id)
    app = Application.builder().token(token).build()

    async def cmd_announce(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat or update.effective_chat.id != allowed_chat_id:
            return  # 허용되지 않은 사용자 무시

        text = " ".join(context.args).strip() if context.args else ""
        if not text:
            await update.message.reply_text(
                "❌ 내용을 입력해주세요.\n예: /announce 서버 점검이 예정되어 있습니다"
            )
            return

        await update.message.reply_text("📤 발송 중...")
        sent = await _broadcast(bot, text)
        await update.message.reply_text(f"✅ {sent}개 채널에 공지 발송 완료")

    app.add_handler(CommandHandler("announce", cmd_announce))

    print("[텔레그램] 공지 리스너 시작")
    async with app:
        await app.start()
        # 기존 명령어 목록 유지하면서 announce만 추가
        existing = await app.bot.get_my_commands()
        if not any(cmd.command == "announce" for cmd in existing):
            await app.bot.set_my_commands(
                list(existing) + [BotCommand("announce", "Discord 전체 채널 공지")]
            )
        await app.updater.start_polling()
        await asyncio.Event().wait()  # 봇 종료 시까지 대기
