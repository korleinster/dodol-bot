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
        "　　음성방 입장 상태로 소환하면 음성 채널도 자동 연결\n"
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
        "**보스 이름 매칭 규칙**\n"
        "· 공백 유무 무관 (`블랙릴리` ↔ `블랙 릴리`)\n"
        "· 초성 검색 — 글자 수 제한 없음, 앞에서부터 매칭\n"
        "　　(`ㅊㄹ` → 체르투바 / `ㅂㄹ` → 블랙 릴리)\n"
        "· 단어 첫 글자 조합 (`오크` → 오염된 크루마)"
    ), inline=False)
    embeds.append(e)

    # ── 컷 / 멍 / 젠 ─────────────────────────────────────
    e = discord.Embed(title="🗡️ 컷 / 멍 / 젠", color=0x57F287)
    e.add_field(name="컷 — 처치 기록 → 다음 리스폰 예약", value=(
        "```\n"
        "체르 컷  /  컷 체르\n"
        "체르 컷 0530  /  05:30 체르 컷\n"
        "ㅊㄹ ㅋ  /  ㅋ ㅊㄹ\n"
        "체르투바컷  /  ㅊㄹㅌㅂㅋ\n"
        "```"
    ), inline=False)
    e.add_field(name="멍 — 미처치 기록 → 직전 예약 기준 재예약", value=(
        "```\n"
        "체르 멍  /  멍 체르\n"
        "ㅊㄹ ㅁ  /  ㅁ ㅊㄹ\n"
        "```"
    ), inline=False)
    e.add_field(name="젠 — 출현 예정 시각 직접 지정", value=(
        "```\n"
        "체르 젠  /  젠 체르\n"
        "체르 스폰  /  스폰 체르\n"
        "ㅊㄹ ㅈ  /  ㅈ ㅊㄹ\n"
        "0530 체르  /  체르 0530\n"
        "```"
        "· 컷: 잡은 시각 기준 → 다음 리스폰 자동 계산\n"
        "· 젠: 입력 시각이 곧 출현 예정 시각 (알림 발송 시각)\n"
        "· 시각 입력 시 미래 1분 초과이면 어제 기준 적용 (컷/멍)\n"
        "· 알림 메시지의 ✅ 컷 / 😶 멍 버튼으로도 처리 가능"
    ), inline=False)
    embeds.append(e)

    # ── 예약 관리 ─────────────────────────────────────────
    e = discord.Embed(title="📋 예약 관리", color=0xFEE75C)
    e.add_field(name="목록 확인", value=(
        "`보탐` / `ㅂㅌ` / `ㅋ` / `z` — 가까운 5건 (고정 제외)\n"
        "`보탐+` / `ㅂㅌ+` / `ㅋ+` / `z+` — 전체 목록 (고정 포함)\n"
        "`Z` — 가까운 5건 + 1순위 보스 TTS 읽기\n"
        "`Z+` — 전체 목록 + TTS"
    ), inline=False)
    e.add_field(name="초기화", value=(
        "`전체삭제` / `초기화` / `보스전체삭제`\n"
        "　→ 기여 랭킹 출력 후 고정 제외 전체 삭제"
    ), inline=False)
    e.add_field(name="임의 예약 / 직접 젠 입력", value=(
        "`22:30 공략` — 보스가 아닌 내용을 임의 시각에 예약\n"
        "`22:30 체르투바` — 보스명 입력 시 해당 시각으로 젠 처리\n"
        "`체르투바 22:30` — 위와 동일 (순서 무관)"
    ), inline=False)
    e.add_field(name="일괄 예약 (여러 줄 입력)", value=(
        "보탐 출력 붙여넣기 또는 직접 입력:\n"
        "```\n"
        "06/09 22:30 체르투바\n"
        "23:45 마투라\n"
        "```"
        "`(미입력×N)` · `— N시간 후` 등 뒤쪽 내용 자동 무시\n"
        "고정 일정 보스는 일괄 예약 불가"
    ), inline=False)
    e.add_field(name="🏆 컷 기여 랭킹", value=(
        "`기여자` / `보탐러` / `기여랭킹` — 초기화 전까지 누적 컷 기여 랭킹\n"
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
        "- 🔄 보스: 오픈시각 + 첫 등장 딜레이로 예약\n"
        "- 일반 보스: 오픈시각 + 리스폰 주기로 예약 (미입력×1 시작)\n"
        "- 이미 예약된 보스 · 고정 일정 보스는 **스킵**\n"
        "- 오픈 시각이 현재보다 1시간 초과 미래면 어제 기준 적용\n"
        "\n"
        "`자동예약` — 전체 보스 미입력 유예시간 확인\n"
        "`자동예약 0:10:00 체르투바` — 유예시간 변경 (시:분:초)"
    ), inline=False)
    embeds.append(e)

    # ── 고정 일정 보스 ────────────────────────────────────
    e = discord.Embed(title="📅 고정 일정 보스", color=0xFF8C00)
    e.add_field(name="​", value=(
        "매주 정해진 요일·시각에 자동 알림 (별도 예약 불필요)\n"
        "\n"
        "**타이런트** — 수 22:30\n"
        "**셀리호든** — 금 19:00\n"
        "**월드 보스** — 매일 12:00, 20:00\n"
        "**오만/신념의 탑 보스** — 매일 19:00\n"
        "\n"
        "컷/멍/젠 · 서버오픈 · 일괄 예약 대상에서 **제외**\n"
        "보탐+(`보탐+` / `z+` 등)에서만 목록에 포함됨"
    ), inline=False)
    embeds.append(e)

    # ── TTS ──────────────────────────────────────────────
    e = discord.Embed(title="🔊 TTS", color=0x5865F2)
    e.add_field(name="​", value=(
        "`v 텍스트` / `ㅍ 텍스트` — 음성 채널에서 읽어줌 (Google TTS)\n"
        "　　정각 알림 시 보스명 + 남은시간 자동 읽기\n"
        "　　`Z` 명령 시 1순위 보스 TTS 읽기\n"
        "`정신차려` / `재시작` — 봇 프로세스 전체 재시작"
    ), inline=False)
    embeds.append(e)

    # ── 시세 / 날씨 / 미니게임 ───────────────────────────
    e = discord.Embed(title="💰 시세 / 🌤️ 날씨 / 🎮 미니게임", color=0x5865F2)
    e.add_field(name="시세", value=(
        "`시세 집행검` — 리니지2M 거래소 시세\n"
        "　　등급 · 최저가 · 평균가 · 서버별 최저가 · 아이템 옵션"
    ), inline=False)
    e.add_field(name="날씨", value=(
        "`날씨` — 오늘 · 내일 · 모레 날씨 예보 (성남시 기준)"
    ), inline=False)
    e.add_field(name="미니게임", value=(
        "`주사위` / `주사위 100` — 주사위 굴리기 (기본 6면)\n"
        "`동전` — 동전 앞/뒷면\n"
        "`숫자게임시작` → `숫자맞추기 42` — 1~100 숫자 맞추기\n"
        "`뽑기` / `뽑기3` / `뽑기 철수 영희`\n"
        "　　📥 이모지로 참여 · 주최자 📤 로 시작 (5분 타임아웃)\n"
        "`경마` / `경마 A B C D` — 최대 8마리 실시간 경마\n"
        "`랭킹` — 미니게임 점수 랭킹 TOP 10"
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
        if not assigned or message.channel.id != assigned:
            return

        if content.strip().lower() in HELP_TRIGGERS:
            for embed in _build_embeds(self.bn):
                await message.channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
