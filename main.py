import asyncio
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
from src.db import init_db

load_dotenv()

COGS = [
    "src.cogs.setup",
    "src.cogs.boss",
    "src.cogs.tts",
    "src.cogs.market",
    "src.cogs.minigame",
]


def make_bot(bot_number: int) -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.voice_states = True
    intents.members = True

    bot = commands.Bot(command_prefix="\x00", intents=intents)  # prefix 사용 안 함 (on_message로 처리)
    bot.bot_number = bot_number
    return bot


async def run_bot(bot_number: int, token: str) -> None:
    bot = make_bot(bot_number)

    @bot.event
    async def on_ready():
        print(f"[도돌봇{bot_number:03d}] {bot.user} 온라인")

    for cog in COGS:
        await bot.load_extension(cog)

    await bot.start(token)


async def main() -> None:
    await init_db()

    tasks = []
    for n in range(1, 10):
        token = os.getenv(f"DISCORD_TOKEN_{n:03d}")
        if token:
            tasks.append(run_bot(n, token))
            print(f"도돌봇{n:03d} 토큰 발견 — 인스턴스 준비")

    if not tasks:
        raise RuntimeError("DISCORD_TOKEN_001 이 설정되지 않았습니다. .env 파일을 확인하세요.")

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
