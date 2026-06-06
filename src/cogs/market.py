"""PLAYNC 리니지2M 시세 검색"""
import os

import aiohttp
import discord
from discord.ext import commands

BASE_URL = "https://dev-api.plaync.com/l2m/v1.0"

GRADE_COLOR = {
    "일반":  0x9B9B9B,
    "고급":  0x3B82F6,
    "희귀":  0xA855F7,
    "영웅":  0xF59E0B,
    "전설":  0xEF4444,
}


class Market(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot     = bot
        self.bn      = bot.bot_number
        self.api_key = os.getenv("PLAYNC_API_KEY", "")
        self._servers: list[dict] | None = None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def get_text_channel(self, guild_id: int) -> int | None:
        from src.db import get_db
        async with get_db() as db:
            async with db.execute(
                "SELECT text_channel_id FROM guild_config WHERE guild_id=? AND bot_number=?",
                (guild_id, self.bn),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

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

        cmd = content
        parts = cmd.split()
        if not parts:
            return

        if parts[0] == "시세" and len(parts) >= 2:
            keyword = " ".join(parts[1:])
            await self._cmd_search(message, keyword)

    # ── .시세 아이템명 ────────────────────────────────────

    async def _cmd_search(self, message: discord.Message, keyword: str):
        async with message.channel.typing():
            async with aiohttp.ClientSession(headers=self._headers()) as session:
                # 1) 아이템 검색
                search_res = await self._search_items(session, keyword)
                if not search_res:
                    await message.channel.send(f"❌ **{keyword}** 검색 결과가 없습니다.")
                    return

                # 2) 첫 번째 아이템 기준으로 상세 + 가격 통계
                item = search_res[0]
                item_id   = item.get("item_id")
                item_name = item.get("item_name", keyword)
                grade     = item.get("grade_name") or item.get("grade", "")

                detail_task = self._get_item_detail(session, item_id)
                price_task  = self._get_price_stats(session, item_id)
                detail, price_stats = await asyncio.gather(detail_task, price_task)

        color = GRADE_COLOR.get(grade, 0x5865F2)
        embed = discord.Embed(title=f"🔍 {item_name}", color=color)

        if grade:
            embed.add_field(name="등급", value=grade, inline=True)

        # 현재 최저가 (검색 결과 기준)
        prices = [r.get("now_min_unit_price") for r in search_res if r.get("now_min_unit_price")]
        if prices:
            embed.add_field(name="최저가", value=f"{min(prices):,} 아데나", inline=True)

        avg = next((r.get("avg_unit_price") for r in search_res if r.get("avg_unit_price")), None)
        if avg:
            embed.add_field(name="평균가", value=f"{avg:,} 아데나", inline=True)

        # 가격 통계
        if price_stats:
            stats = price_stats if isinstance(price_stats, dict) else {}
            fields = [
                ("현재가", stats.get("now")),
                ("최저가(통계)", stats.get("min")),
                ("최고가(통계)", stats.get("max")),
                ("최근 거래가", stats.get("last")),
                ("평균가(통계)", stats.get("avg")),
            ]
            for fname, fval in fields:
                if fval and isinstance(fval, dict):
                    unit = fval.get("unit_price")
                    if unit:
                        embed.add_field(name=fname, value=f"{unit:,} 아데나", inline=True)

        # 아이템 상세 옵션
        if detail:
            options = detail.get("options") or []
            if options:
                opt_lines = [o.get("display", "") for o in options[:5] if o.get("display")]
                if opt_lines:
                    embed.add_field(name="옵션", value="\n".join(opt_lines), inline=False)

            attr = detail.get("attribute") or {}
            desc = attr.get("description", "")
            if desc:
                embed.set_footer(text=desc[:100])

        # 서버별 시세 (검색 결과에서 추출)
        server_lines = []
        for r in search_res[:5]:
            sname = r.get("server_name") or r.get("world", "")
            price = r.get("now_min_unit_price")
            if sname and price:
                server_lines.append(f"{sname}: {price:,}")
        if server_lines:
            embed.add_field(name="서버별 최저가", value="\n".join(server_lines), inline=False)

        await message.channel.send(embed=embed)

    # ── API 호출 ──────────────────────────────────────────

    async def _search_items(self, session: aiohttp.ClientSession, keyword: str) -> list[dict]:
        try:
            async with session.get(
                f"{BASE_URL}/market/items/search",
                params={"search_keyword": keyword, "size": 10, "sale": "true"},
            ) as r:
                if r.status != 200:
                    return []
                data = await r.json()
                return data.get("contents") or []
        except Exception:
            return []

    async def _get_item_detail(self, session: aiohttp.ClientSession, item_id) -> dict:
        if not item_id:
            return {}
        try:
            async with session.get(f"{BASE_URL}/market/items/{item_id}") as r:
                if r.status != 200:
                    return {}
                return await r.json()
        except Exception:
            return {}

    async def _get_price_stats(self, session: aiohttp.ClientSession, item_id) -> dict:
        if not item_id:
            return {}
        try:
            async with session.get(f"{BASE_URL}/market/items/{item_id}/price") as r:
                if r.status != 200:
                    return {}
                return await r.json()
        except Exception:
            return {}

import asyncio  # noqa: E402 — gather 사용


async def setup(bot: commands.Bot):
    await bot.add_cog(Market(bot))
