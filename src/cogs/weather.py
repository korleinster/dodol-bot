"""날씨 — Open-Meteo API (성남시 기준)"""
import aiohttp
import discord
from discord.ext import commands

PREFIX = "."

# 성남시 중심 좌표
LAT = 37.4449
LON = 127.1388

WMO_LABEL = {
    0:  ("☀️", "맑음"),
    1:  ("🌤️", "대체로 맑음"),
    2:  ("⛅", "구름 조금"),
    3:  ("☁️", "흐림"),
    45: ("🌫️", "안개"),
    48: ("🌫️", "안개"),
    51: ("🌦️", "이슬비"),
    53: ("🌦️", "이슬비"),
    55: ("🌦️", "이슬비"),
    61: ("🌧️", "비"),
    63: ("🌧️", "비"),
    65: ("🌧️", "강한 비"),
    71: ("❄️", "눈"),
    73: ("❄️", "눈"),
    75: ("❄️", "강한 눈"),
    80: ("🌦️", "소나기"),
    81: ("🌦️", "소나기"),
    82: ("⛈️", "강한 소나기"),
    95: ("⛈️", "뇌우"),
    96: ("⛈️", "우박 동반 뇌우"),
    99: ("⛈️", "우박 동반 뇌우"),
}

DAY_LABEL = ["오늘", "내일", "모레"]


def wmo_to_label(code: int) -> tuple[str, str]:
    return WMO_LABEL.get(code, ("🌡️", f"코드{code}"))


class Weather(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bn  = bot.bot_number

    async def get_text_channel(self, guild_id: int) -> int | None:
        from src.db import get_db
        async with get_db() as db:
            async with db.execute(
                "SELECT text_channel_id FROM guild_config WHERE guild_id=? AND bot_number=?",
                (guild_id, self.bn),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if (message.author.bot and message.author.id != getattr(self.bot, "tester_id", 0)) or not message.guild:
            return
        content = message.content.strip()
        if not content.startswith(PREFIX):
            return

        assigned = await self.get_text_channel(message.guild.id)
        if assigned and message.channel.id != assigned:
            return

        cmd = content[len(PREFIX):].strip()
        if cmd == "날씨":
            await self._cmd_weather(message)

    async def _cmd_weather(self, message: discord.Message):
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude":  LAT,
            "longitude": LON,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode",
            "timezone": "Asia/Seoul",
            "forecast_days": 3,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status != 200:
                        await message.channel.send("❌ 날씨 정보를 가져올 수 없습니다.")
                        return
                    data = await r.json()
        except Exception:
            await message.channel.send("❌ 날씨 서버에 연결할 수 없습니다.")
            return

        daily = data.get("daily", {})
        times  = daily.get("time", [])
        t_max  = daily.get("temperature_2m_max", [])
        t_min  = daily.get("temperature_2m_min", [])
        rain_p = daily.get("precipitation_probability_max", [])
        codes  = daily.get("weathercode", [])

        embed = discord.Embed(title="🌏 성남시 날씨 예보", color=0x5865F2)
        for i in range(min(3, len(times))):
            emoji, label = wmo_to_label(int(codes[i]) if codes[i] is not None else 0)
            hi  = f"{t_max[i]:.0f}°" if t_max[i] is not None else "?"
            lo  = f"{t_min[i]:.0f}°" if t_min[i] is not None else "?"
            rp  = f"{int(rain_p[i])}%" if rain_p[i] is not None else "?"
            day_str = DAY_LABEL[i] if i < len(DAY_LABEL) else times[i]
            embed.add_field(
                name=f"{day_str} ({times[i][5:]})",
                value=f"{emoji} {label}\n🌡 {hi} / {lo}  ☔ {rp}",
                inline=True,
            )

        await message.channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Weather(bot))
