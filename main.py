import asyncio
import os
import time
from contextlib import suppress
import discord
from discord.ext import commands
from dotenv import load_dotenv
from src.db import init_db, ensure_default_bosses
from src.runtime_health import (
    initialize_runtime_health,
    mark_discord_health,
    write_health_file,
)

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
GATEWAY_RECOVERY_ALERT_SECONDS = 60
MAX_RECORDED_GATEWAY_DOWNTIME_SECONDS = 24 * 60 * 60


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
    bot._bridge_start_error_code = None
    bot._bridge_start_error_reported = False
    bot._gateway_disconnect_started_at = None
    bot.scheduler_health = {
        "status": "starting",
        "bootstrapCompletedAt": None,
        "lastTickAt": None,
        "errorCode": None,
    }
    initialize_runtime_health(bot)
    return bot


async def _health_reporter(bot: commands.Bot) -> None:
    """Keep Docker's local probe current without exposing a network port."""
    while True:
        write_health_file(bot)
        await asyncio.sleep(5)


async def run_bot(bot_number: int, token: str, bot: commands.Bot | None = None) -> None:
    if bot is None:
        bot = make_bot(bot_number)
    if not hasattr(bot, "_ready_count"):
        bot._ready_count = 0
    if not hasattr(bot, "_bridge_start_error_code"):
        bot._bridge_start_error_code = None
    if not hasattr(bot, "_bridge_start_error_reported"):
        bot._bridge_start_error_reported = False
    if not hasattr(bot, "_gateway_disconnect_started_at"):
        bot._gateway_disconnect_started_at = None
    if not hasattr(bot, "scheduler_health"):
        bot.scheduler_health = {
            "status": "starting",
            "bootstrapCompletedAt": None,
            "lastTickAt": None,
            "errorCode": None,
        }
    if not isinstance(getattr(bot, "runtime_health", None), dict):
        initialize_runtime_health(bot)
    health_task = asyncio.create_task(_health_reporter(bot))

    def consume_gateway_recovery() -> int | None:
        disconnect_started_at = bot._gateway_disconnect_started_at
        bot._gateway_disconnect_started_at = None
        if isinstance(disconnect_started_at, (int, float)):
            return min(
                max(0, int(time.monotonic() - disconnect_started_at)),
                MAX_RECORDED_GATEWAY_DOWNTIME_SECONDS,
            )
        return None

    async def report_gateway_recovery(recovered_after_seconds: int | None) -> None:
        if recovered_after_seconds is None:
            return
        print(
            f"[뚠뚠봇{bot_number:03d}] Discord gateway 복구 "
            f"(downtime: {recovered_after_seconds}s)",
        )
        if recovered_after_seconds < GATEWAY_RECOVERY_ALERT_SECONDS:
            return
        from src.utils.notify import alert
        try:
            await asyncio.wait_for(
                alert(
                    bot,
                    bot_number,
                    "Discord gateway 연결이 "
                    f"{recovered_after_seconds}초 후 복구되었습니다.",
                ),
                timeout=5,
            )
        except Exception:
            # Recovery reporting must never delay Discord reconnect.
            pass

    @bot.event
    async def on_ready():
        recovered_after_seconds = consume_gateway_recovery()
        commit = os.getenv("GIT_COMMIT", "unknown")
        reason = _detect_start_reason(bot_number, bot._ready_count)
        bot._ready_count += 1
        mark_discord_health(bot, True)
        write_health_file(bot)
        emoji, label = _REASON_LABEL[reason]
        print(f"[뚠뚠봇{bot_number:03d}] {bot.user} 온라인 — {label} (commit: {commit})")
        await _notify_ready(bot, bot_number, commit, reason)
        await report_gateway_recovery(recovered_after_seconds)
        if bot._bridge_start_error_code and not bot._bridge_start_error_reported:
            bot._bridge_start_error_reported = True
            from src.utils.notify import alert
            await alert(
                bot,
                bot_number,
                "웹 연동 시작에 실패했습니다 "
                f"({bot._bridge_start_error_code}). Discord 핵심 기능은 계속 동작합니다.",
            )

    @bot.event
    async def on_disconnect():
        if bot._gateway_disconnect_started_at is None:
            bot._gateway_disconnect_started_at = time.monotonic()
            print(f"[뚠뚠봇{bot_number:03d}] Discord gateway 연결 끊김 감지")
        mark_discord_health(bot, False)
        write_health_file(bot)

    @bot.event
    async def on_resumed():
        # discord.py emits `resumed`, not `ready`, when an existing gateway
        # session resumes successfully. Restore health here so a normal resume
        # cannot leave the container permanently unhealthy.
        recovered_after_seconds = consume_gateway_recovery()
        mark_discord_health(bot, True)
        write_health_file(bot)
        await report_gateway_recovery(recovered_after_seconds)

    bridge = None
    try:
        # discord.py의 공개 생명주기를 명시적으로 나눈다. login()이 완료된 뒤
        # Cog의 background loop를 만들면 wait_until_ready()가 초기화 전 예외를
        # 내지 않고, bridge는 Discord 연결 전에 모든 생명주기 이벤트를 구독한다.
        await bot.login(token)
        for cog in COGS:
            await bot.load_extension(cog)

        try:
            from src.web_bridge import start_web_bridge
            bridge = await start_web_bridge(bot)
        except Exception:
            # 웹 bridge 장애가 Discord 보스 알림/TTS/게임까지 중단시키면 안 된다.
            # 상세 예외는 노출하지 않고 고정된 운영 코드만 기록한다.
            bot._bridge_start_error_code = "BRIDGE_START_FAILED"
            print(
                f"[뚠뚠봇{bot_number:03d}] 웹 연동 시작 실패 "
                "(BRIDGE_START_FAILED) — Discord 기능은 계속 시작합니다."
            )

        await bot.connect(reconnect=True)
    finally:
        mark_discord_health(bot, False)
        write_health_file(bot)
        health_task.cancel()
        with suppress(asyncio.CancelledError):
            await health_task
        try:
            if bridge is not None:
                await bridge.close()
        finally:
            await bot.close()


async def run_bot_safe(
    bot_number: int,
    token: str,
    bot: commands.Bot | None = None,
    *,
    propagate: bool = True,
) -> None:
    try:
        await run_bot(bot_number, token, bot)
    except Exception as e:
        print(f"[뚠뚠봇{bot_number:03d}] 오류로 종료: {e}")
        if propagate:
            raise


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

    # Short Discord gateway flaps are reported only after a >=60-second
    # recovery in on_ready. Deployment and process-error notifications retain
    # their established Telegram behavior.
    if reason != "reconnect":
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
                # Legacy multi-bot mode keeps healthy siblings alive. Production
                # single-bot containers still propagate fatal startup failures
                # so their supervisor can restart the failed bot.
                tasks.append(run_bot_safe(n, token, bot, propagate=False))
                print(f"뚠뚠봇{n:03d} 토큰 발견 — 인스턴스 준비")

        if not tasks:
            raise RuntimeError("DISCORD_TOKEN_001 이 설정되지 않았습니다. .env 파일을 확인하세요.")

        await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
