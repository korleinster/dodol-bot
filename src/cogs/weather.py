"""날씨 — Open-Meteo API (성남시 기준)"""
from datetime import time, timezone, timedelta

import aiohttp
import discord
from discord.ext import commands, tasks

# 성남시 중심 좌표
LAT = 37.4449
LON = 127.1388

KST = timezone(timedelta(hours=9))

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


async def _fetch_weather_embed() -> discord.Embed | None:
    """날씨 API 호출 후 embed 반환. 실패 시 None."""
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
                    return None
                data = await r.json()
    except Exception:
        return None

    daily  = data.get("daily", {})
    times  = daily.get("time", [])
    t_max  = daily.get("temperature_2m_max", [])
    t_min  = daily.get("temperature_2m_min", [])
    rain_p = daily.get("precipitation_probability_max", [])
    codes  = daily.get("weathercode", [])

    embed = discord.Embed(title="🌏 날씨 예보", color=0x5865F2)
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
    return embed


class Weather(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bn  = bot.bot_number
        self.daily_weather.start()

    def cog_unload(self):
        self.daily_weather.cancel()

    async def _get_all_channels(self) -> list[int]:
        """이 봇 번호에 배치된 모든 텍스트 채널 ID 반환."""
        from src.db import get_db
        async with get_db() as db:
            async with db.execute(
                "SELECT text_channel_id FROM guild_config WHERE bot_number=?",
                (self.bn,),
            ) as cur:
                rows = await cur.fetchall()
        return [r[0] for r in rows]

    async def get_text_channel(self, guild_id: int) -> int | None:
        from src.db import get_db
        async with get_db() as db:
            async with db.execute(
                "SELECT text_channel_id FROM guild_config WHERE guild_id=? AND bot_number=?",
                (guild_id, self.bn),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

    # ── 매일 07:00 KST 자동 날씨 발송 ───────────────────────
    @tasks.loop(time=time(hour=7, minute=0, tzinfo=KST))
    async def daily_weather(self):
        embed = await _fetch_weather_embed()
        if embed is None:
            return
        embed.set_footer(text="매일 07:00 자동 발송")

        ch_ids = await self._get_all_channels()
        for ch_id in ch_ids:
            ch = self.bot.get_channel(ch_id)
            if isinstance(ch, discord.TextChannel):
                try:
                    await ch.send(embed=embed)
                except Exception as e:
                    print(f"[Weather] 자동 발송 실패 ch={ch_id}: {e}")

    @daily_weather.before_loop
    async def before_daily_weather(self):
        await self.bot.wait_until_ready()

    # ── on_message ────────────────────────────────────────
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

        if content == "날씨":
            embed = await _fetch_weather_embed()
            if embed:
                await message.channel.send(embed=embed)
            else:
                await message.channel.send("❌ 날씨 정보를 가져올 수 없습니다.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Weather(bot))
