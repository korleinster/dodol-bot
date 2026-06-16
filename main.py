import asyncio
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
from src.db import init_db, ensure_default_bosses

load_dotenv()

COGS = [
    "src.cogs.setup",
    "src.cogs.boss",
    "src.cogs.tts",
    "src.cogs.market",
    "src.cogs.minigame",
    "src.cogs.weather",
    "src.cogs.help",
]


# 재시작 유형별 레이블
_REASON_LABEL = {
    "deploy":    ("✅", "배포 후 시작"),
    "error":     ("⚠️", "오류 재시작"),
    "reconnect": ("🔄", "네트워크 재연결"),
}


def _detect_start_reason(bot_number: int, ready_count: int) -> str:
    """on_ready 호출 시 재시작 유형 판별.

    판별 로직:
    - ready_count > 0         → Discord WebSocket 재연결 (프로세스 살아있음)
    - /tmp 마커 없음           → 컨테이너 새로 생성 (배포)
    - /tmp 마커 있음           → 같은 컨테이너 내 프로세스 재시작 (오류/crash)
    /tmp는 컨테이너 재생성 시 초기화되므로 배포 vs 오류 구분 가능.
    """
    if ready_count > 0:
        return "reconnect"

    marker = f"/tmp/dodolbot_{bot_number:03d}_started"
    if os.path.exists(marker):
        return "error"

    # 첫 시작 — 마커 생성
    with open(marker, "w") as f:
        f.write(str(os.getpid()))
    return "deploy"


def make_bot(bot_number: int) -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.voice_states = True
    intents.members = True

    bot = commands.Bot(command_prefix="\x00", intents=intents)
    bot.bot_number = bot_number
    bot._ready_count = 0  # on_ready 호출 횟수 (네트워크 재연결 감지용)
    return bot


async def run_bot(bot_number: int, token: str, bot: commands.Bot | None = None) -> None:
    if bot is None:
        bot = make_bot(bot_number)

    @bot.event
    async def on_ready():
        commit = os.getenv("GIT_COMMIT", "unknown")
        reason = _detect_start_reason(bot_number, bot._ready_count)
        bot._ready_count += 1
        emoji, label = _REASON_LABEL[reason]
        print(f"[뚠뚠봇{bot_number:03d}] {bot.user} 온라인 — {label} (commit: {commit})")
        await _notify_ready(bot, bot_number, commit, reason)

    for cog in COGS:
        await bot.load_extension(cog)

    await bot.start(token)


async def run_bot_safe(bot_number: int, token: str, bot: commands.Bot | None = None) -> None:
    try:
        await run_bot(bot_number, token, bot)
    except Exception as e:
        print(f"[뚠뚠봇{bot_number:03d}] 오류로 종료: {e}")


async def _notify_ready(
    bot: commands.Bot, bot_number: int,
    commit: str = "unknown", reason: str = "deploy",
) -> None:
    from src.db import get_db
    from src.utils.notify import send_telegram
    from src.utils.notify import send_discord_alert

    emoji, label = _REASON_LABEL[reason]

    # Discord 채널: 배포·오류만 (재연결은 노이즈 방지)
    if reason != "reconnect":
        async with get_db() as db:
            async with db.execute(
                "SELECT guild_id, text_channel_id FROM guild_config WHERE bot_number=?",
                (bot_number,),
            ) as cur:
                rows = await cur.fetchall()

        for guild_id, ch_id in rows:
            await ensure_default_bosses(guild_id, bot_number)
            ch = bot.get_channel(ch_id)
            if ch:
                try:
                    await ch.send(f"{emoji} 뚠뚠봇{bot_number:03d} {label}. 온라인입니다.")
                except Exception:
                    pass

    # 텔레그램: 모든 유형 발송
    await send_telegram(f"{emoji} 뚠뚠봇{bot_number:03d} {label} (commit: {commit})")


async def main() -> None:
    await init_db()

    bot_number_env = os.getenv("BOT_NUMBER")

    if bot_number_env:
        # 단일봇 모드: BOT_NUMBER 환경변수로 특정 봇만 실행 (컨테이너 분리 배포용)
        n = int(bot_number_env)
        token = os.getenv(f"DISCORD_TOKEN_{n:03d}")
        if not token:
            raise RuntimeError(f"DISCORD_TOKEN_{n:03d} 이 설정되지 않았습니다. .env 파일을 확인하세요.")
        print(f"뚠뚠봇{n:03d} 토큰 발견 — 단일봇 모드로 시작")

        bot = make_bot(n)
        tasks = [run_bot_safe(n, token, bot)]

        await asyncio.gather(*tasks)

    else:
        # 멀티봇 모드: BOT_NUMBER 미설정 시 .env의 모든 토큰을 한 프로세스에서 실행
        tasks = []
        first_bot: commands.Bot | None = None

        for n in range(1, 10):
            token = os.getenv(f"DISCORD_TOKEN_{n:03d}")
            if token:
                bot = make_bot(n)
                if first_bot is None:
                    first_bot = bot  # 첫 번째 봇에 텔레그램 리스너 연결
                tasks.append(run_bot_safe(n, token, bot))
                print(f"뚠뚠봇{n:03d} 토큰 발견 — 인스턴스 준비")

        if not tasks:
            raise RuntimeError("DISCORD_TOKEN_001 이 설정되지 않았습니다. .env 파일을 확인하세요.")

        await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
