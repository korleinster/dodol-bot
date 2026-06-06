"""도움말 — 메뉴 / 도움말 / ?"""
import discord
from discord.ext import commands

from src.db import get_db

HELP_TRIGGERS = {"메뉴", "도움말", "?"}


def _build_embeds(bn: int) -> list[discord.Embed]:
    embeds = []

    # ── 채널 설정 ─────────────────────────────────────────
    e = discord.Embed(title="⚙️ 채널 설정", color=0x5865F2)
    e.add_field(name="​", value=(
        "`소환` — 서버 내 모든 도돌봇 배치 현황\n"
        "`소환 도돌봇001` — 이 채널에 배치\n"
        "`설정` — 현재 설정 확인"
    ), inline=False)
    embeds.append(e)

    # ── 보스 관리 ─────────────────────────────────────────
    e = discord.Embed(title="⚔️ 보스 관리", color=0xED4245)
    e.add_field(name="​", value=(
        "`보스` / `보스목록` — 등록된 보스 목록\n"
        "　　고정 일정 보스 맨 위 · 이후 리스폰 오름차순\n"
        "　　🔄 = 서버 재시작 시 스폰 보스\n"
        "\n"
        "보스 이름은 **공백 유무 무관** 매칭\n"
        "(예: `블랙릴리` ↔ `블랙 릴리`)"
    ), inline=False)
    embeds.append(e)

    # ── 컷 / 멍 ──────────────────────────────────────────
    e = discord.Embed(title="🗡️ 컷 / 멍", color=0x57F287)
    e.add_field(name="컷 — 처치 기록 (다음 리스폰 자동 예약)", value=(
        "```\n"
        "체르 컷  /  컷 체르\n"
        "체르 컷 0530  /  05:30 체르 컷\n"
        "ㅊㄹ ㅋ  /  ㅋ ㅊㄹ\n"
        "체르투바컷  /  ㅊㄹㅌㅂㅋ\n"
        "```"
    ), inline=False)
    e.add_field(name="멍 — 미처치 기록 (직전 예약 기준 재예약)", value=(
        "```\n"
        "체르 멍  /  멍 체르\n"
        "ㅊㄹ ㅁ  /  ㅁ ㅊㄹ\n"
        "```"
        "※ 초성 단축은 보스 이름 **3글자 이상**부터"
    ), inline=False)
    embeds.append(e)

    # ── 예약 관리 ─────────────────────────────────────────
    e = discord.Embed(title="📋 예약 관리", color=0xFEE75C)
    e.add_field(name="목록 확인", value=(
        "`보탐` / `ㅂㅌ` / `ㅋ` / `z` — 가까운 5건\n"
        "`보탐+` / `ㅂㅌ+` / `ㅋ+` / `z+` — 전체 목록\n"
        "`Z` — 가까운 5건 + TTS"
    ), inline=False)
    e.add_field(name="초기화 / 임의 예약", value=(
        "`전체삭제` / `초기화` — 기여 랭킹 출력 후 고정 제외 전체 삭제\n"
        "`22:30 체르투바` — 임의 시각 예약"
    ), inline=False)
    e.add_field(name="🏆 컷 기여 랭킹", value=(
        "`기여자` / `보탐러` — 초기화 전까지 누적된 컷 기여 랭킹\n"
        "컷 처리 시 처리자 이름이 자동 기록됨 (명령어·버튼 모두)"
    ), inline=False)
    e.add_field(name="3단계 자동 알림", value=(
        "🟡 5분 전 → 🟠 1분 전 → 🔴 정각 (컷/멍 버튼)\n"
        "정각 후 **10분** 미입력 시 자동으로 다음 리스폰 예약"
    ), inline=False)
    embeds.append(e)

    # ── 서버오픈 + 자동예약 ───────────────────────────────
    e = discord.Embed(title="🕐 서버오픈", color=0x5865F2)
    e.add_field(name="​", value=(
        "점검 후 서버 기동 시각 입력 → 전체 보스 일괄 예약\n"
        "```\n"
        "서버오픈 05:00\n"
        "05:00 서버오픈  /  오픈 05:00\n"
        "```"
        "- 🔄 보스: 오픈시각 + 첫 등장 딜레이\n"
        "- 일반 보스: 오픈시각 + 리스폰 주기\n"
        "- 이미 예약된 보스 · 고정 일정 보스는 **스킵**\n"
        "\n"
        "`자동예약` — 미입력 유예시간 확인\n"
        "`자동예약 0:30:00 체르투바` — 유예시간 변경"
    ), inline=False)
    embeds.append(e)

    # ── 고정 일정 보스 ────────────────────────────────────
    e = discord.Embed(title="📅 고정 일정 보스", color=0xFF8C00)
    e.add_field(name="​", value=(
        "매주 정해진 요일·시각에 자동 알림\n"
        "\n"
        "**타이런트** — 수 22:30\n"
        "**셀리호든** — 금 19:00\n"
        "**월드 보스** — 매일 12:00, 20:00\n"
        "**오만/신념의 탑 보스** — 매일 19:00\n"
        "\n"
        "컷/멍 · 서버오픈 대상에서 **제외**됩니다."
    ), inline=False)
    embeds.append(e)

    # ── TTS ──────────────────────────────────────────────
    e = discord.Embed(title="🔊 TTS", color=0x5865F2)
    e.add_field(name="​", value=(
        "`ㅍ 텍스트` / `v 텍스트` — 음성 채널에서 읽어줌\n"
        "`정신차려` / `재시작` — 봇 전체 재시작"
    ), inline=False)
    embeds.append(e)

    # ── 시세 / 날씨 / 미니게임 ───────────────────────────
    e = discord.Embed(title="💰 시세 / 🌤️ 날씨 / 🎮 미니게임", color=0x5865F2)
    e.add_field(name="시세", value="`시세 집행검` — 거래소 시세 + 서버별 최저가", inline=False)
    e.add_field(name="날씨", value="`날씨` — 오늘 · 내일 · 모레 날씨", inline=False)
    e.add_field(name="미니게임", value=(
        "`주사위` / `주사위 100` / `동전`\n"
        "`숫자게임시작` / `숫자맞추기 42`\n"
        "`뽑기` / `뽑기3` / `뽑기 철수 영희`\n"
        "`경마` / `경마 A B C D`\n"
        "`랭킹`"
    ), inline=False)
    embeds.append(e)

    # 마지막 embed에 푸터
    embeds[-1].set_footer(text=f"도돌봇{bn:03d}")

    return embeds


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bn  = bot.bot_number

    async def get_text_channel(self, guild_id: int) -> int | None:
        async with get_db() as db:
            async with db.execute(
                "SELECT text_channel_id FROM guild_config WHERE guild_id=? AND bot_number=?",
                (guild_id, self.bn),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        content = message.content.strip()
        if not content:
            return

        assigned = await self.get_text_channel(message.guild.id)
        if assigned and message.channel.id != assigned:
            return

        if content.strip().lower() in HELP_TRIGGERS:
            for embed in _build_embeds(self.bn):
                await message.channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
