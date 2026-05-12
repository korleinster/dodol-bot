"""도움말 — .메뉴 / .도움말 / .?"""
import discord
from discord.ext import commands

PREFIX = "."
HELP_TRIGGERS = {"메뉴", "도움말", "?"}


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bn  = bot.bot_number

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if (message.author.bot and message.author.id != getattr(self.bot, "tester_id", 0)) or not message.guild:
            return
        content = message.content.strip()
        if not content.startswith(PREFIX):
            return
        cmd = content[len(PREFIX):].strip().lower()
        if cmd in HELP_TRIGGERS:
            await self._cmd_help(message)

    async def _cmd_help(self, message: discord.Message):
        embed = discord.Embed(
            title=f"📖 도돌봇{self.bn:03d} 명령어 안내",
            description="모든 명령어는 `.` 으로 시작합니다.",
            color=0x5865F2,
        )

        embed.add_field(
            name="⚙️ 채널 설정",
            value=(
                "`.소환` 배치 현황 확인\n"
                "`.소환 도돌봇001` 이 채널에 배치\n"
                "`.설정` 현재 설정 확인"
            ),
            inline=False,
        )
        embed.add_field(
            name="⚔️ 보스 관리",
            value=(
                "`.보스` 등록된 보스 목록\n"
                "`.보스등록` 등록 방법 안내\n"
                "`.보스삭제 이름` 보스 삭제"
            ),
            inline=False,
        )
        embed.add_field(
            name="🗡️ 컷 / 멍",
            value=(
                "`.이름 컷` / `.컷 이름` 처치 기록\n"
                "`.이름 컷 0530` 시각 지정\n"
                "`.이름 멍` / `.멍 이름` 미처치 기록\n"
                "초성 단축: `.ㅊㄹ ㅋ` (컷) / `.ㅊㄹ ㅁ` (멍)"
            ),
            inline=False,
        )
        embed.add_field(
            name="📋 예약 관리",
            value=(
                "`.보탐` 예약 목록 (고정 제외)\n"
                "`.보탐+` 예약 목록 (고정 포함)\n"
                "`.z` / `.ㅋ` 다음 예약 1건\n"
                "`.Z` 다음 예약 + TTS 알림\n"
                "`.전체삭제` / `.초기화` / `.보스전체삭제` 예약 초기화\n"
                "`.22:30 내용` 임의 예약\n"
                "`.내용 삭제` 예약 삭제"
            ),
            inline=False,
        )
        embed.add_field(
            name="🕐 오픈타임 / 서버오픈",
            value=(
                "`.오픈타임` 보스별 오픈타임 확인\n"
                "`.오픈타임 0300 이름` 오픈타임 설정\n"
                "`.자동예약` 자동예약 설정 확인\n"
                "`.05:00 서버오픈` 서버 기동 기준 전체 예약"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔊 TTS",
            value=(
                "`.v 텍스트` 음성 채널에서 읽어줌\n"
                "`.정신차려` / `.재시작` 봇 재시작"
            ),
            inline=False,
        )
        embed.add_field(
            name="💰 시세",
            value="`.시세 아이템명` 거래소 시세 + 서버별 최저가",
            inline=False,
        )
        embed.add_field(
            name="🎮 미니게임",
            value=(
                "`.주사위 [N]` `.동전`\n"
                "`.숫자게임시작` / `.숫자맞추기 N`\n"
                "`.뽑기[N]` / `.경마 [이름1] [이름2] ...`\n"
                "`.랭킹` 점수 TOP 10"
            ),
            inline=False,
        )
        embed.add_field(
            name="🌤️ 날씨",
            value="`.날씨` 성남시 오늘·내일·모레 날씨",
            inline=False,
        )

        await message.channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
