"""미니게임 — 주사위, 동전, 숫자맞추기, 뽑기"""
import asyncio
import random

import discord
from discord.ext import commands

scores: dict[int, int] = {}


class Minigame(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bn  = bot.bot_number
        self.guess_games: dict[int, int] = {}
        self.lottery_sessions: dict[int, dict] = {}

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
        head = parts[0].lower()

        if head == "주사위":
            n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 6
            result = random.randint(1, n)
            await message.channel.send(f"🎲 {message.author.mention} → **{result}** (1~{n})")

        elif head == "동전":
            result = random.choice(["앞면 🪙", "뒷면 🌑"])
            await message.channel.send(f"{message.author.mention} → **{result}**")

        elif head == "숫자게임시작":
            ch = message.channel.id
            if ch in self.guess_games:
                await message.channel.send("이미 진행 중! `.숫자맞추기 숫자` 로 도전하세요.")
                return
            self.guess_games[ch] = random.randint(1, 100)
            await message.channel.send("🎯 숫자 맞추기 시작! 1~100 사이 숫자를 `.숫자맞추기 숫자` 로 입력하세요.")

        elif head == "숫자맞추기" and len(parts) == 2 and parts[1].isdigit():
            ch = message.channel.id
            if ch not in self.guess_games:
                await message.channel.send("진행 중인 게임 없음. `.숫자게임시작`")
                return
            guess  = int(parts[1])
            secret = self.guess_games[ch]
            if guess < secret:
                await message.channel.send(f"📈 {guess} — 더 큽니다!")
            elif guess > secret:
                await message.channel.send(f"📉 {guess} — 더 작습니다!")
            else:
                del self.guess_games[ch]
                uid = message.author.id
                scores[uid] = scores.get(uid, 0) + 10
                await message.channel.send(f"🎉 {message.author.mention} 정답 **{secret}**! (+10점)")

        elif head.startswith("경마"):
            await self._cmd_horserace(message, head, parts[1:])

        elif head.startswith("뽑기"):
            await self._cmd_lottery(message, head, parts[1:])

        elif head == "랭킹":
            await self._cmd_ranking(message)

    # ── 경마 ──────────────────────────────────────────────

    async def _cmd_horserace(self, message: discord.Message, head: str, args: list[str]):
        ICONS  = ["🐎", "🏇", "🦄", "🐴", "🐇", "🐆", "🦊", "🐅"]
        MEDALS = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]
        CROWD  = [
            " o   o   o   o   o  \n/|\\ /|\\ /|\\ /|\\ /|\\",
            "\\o/ \\o/ \\o/ \\o/ \\o/\n |   |   |   |   |  ",
        ]

        if args:
            labels = [a for a in args if a][:8]
        else:
            labels = ["1번", "2번"]
        n      = max(2, min(len(labels), 8))
        labels = labels[:n]
        icons  = ICONS[:n]
        TRACK  = 26
        pos    = [0] * n
        podium: list[int] = []

        def get_comment(tick: int) -> str:
            if podium:
                return f"🎊 **{labels[podium[0]]}** 결승 통과!"
            ranked = sorted(range(n), key=lambda i: -pos[i])
            leader, second = ranked[0], ranked[1]
            gap = pos[leader] - pos[second]
            near = pos[leader] >= TRACK * 0.75
            if near:
                return random.choice(["⚡ 막판 스퍼트!", "🔥 결승선이 코앞!", "💨 전력질주 중!"])
            if gap <= 2:
                return random.choice([
                    f"😤 **{labels[leader]}** vs **{labels[second]}** 접전!",
                    f"🤯 막상막하! 누가 먼저?",
                    f"👀 **{labels[leader]}**와 **{labels[second]}** 어깨를 나란히!",
                ])
            return random.choice([
                f"🏃 **{labels[leader]}** 독주 중!",
                f"💪 **{labels[leader]}** 앞서가고 있어요!",
                f"😰 **{labels[second]}** 따라잡을 수 있을까?",
            ])

        def render(tick: int, done: bool = False) -> str:
            lines = ["🏇 **경마 진행 중!**" if not done else "🏁 **경마 종료!**", ""]
            for i, lbl in enumerate(labels):
                p   = min(pos[i], TRACK)
                ico = icons[i]
                bar = (ico + "─" * TRACK) if p >= TRACK else ("╌" * (TRACK - p) + ico + "─" * p)
                lines.append(f"🏁  {bar}  `{lbl}`")
            if done and podium:
                lines.append("")
                for rank, idx in enumerate(podium):
                    lines.append(f"{MEDALS[rank]} **{labels[idx]}**")
            else:
                lines.append("")
                lines.append(get_comment(tick))
                lines.append(f"```\n{CROWD[tick % 2]}\n```")
            return "\n".join(lines)

        msg = await message.channel.send(render(0))

        for tick in range(1, 61):
            await asyncio.sleep(0.6)
            for i in range(n):
                if pos[i] < TRACK:
                    pos[i] += random.choices([0, 1, 2, 3], weights=[15, 40, 30, 15])[0]
                    if pos[i] >= TRACK and i not in podium:
                        podium.append(i)
            await msg.edit(content=render(tick))
            if len(podium) == n:
                break

        remaining = sorted([i for i in range(n) if i not in podium], key=lambda i: -pos[i])
        podium.extend(remaining)
        await msg.edit(content=render(tick, done=True))

    # ── 뽑기 ──────────────────────────────────────────────

    async def _cmd_lottery(self, message: discord.Message, head: str, args: list[str]):
        n_match = head[2:]
        n = int(n_match) if n_match.isdigit() else 1

        manual = [a for a in args if a]
        session = {
            "author": message.author.id,
            "n": n,
            "participants": set(manual),
            "msg_id": None,
        }

        embed = discord.Embed(
            title=f"🎰 뽑기 ({n}명)",
            description="📥 이모지를 눌러 참여하세요.\n주최자가 📤 를 눌러 시작합니다.",
            color=0xF59E0B,
        )
        if manual:
            embed.add_field(name="사전 등록", value=", ".join(manual))

        msg = await message.channel.send(embed=embed)
        await msg.add_reaction("📥")
        await msg.add_reaction("📤")
        session["msg_id"] = msg.id
        self.lottery_sessions[msg.id] = session

        # 5분 타임아웃
        await asyncio.sleep(300)
        if msg.id in self.lottery_sessions:
            del self.lottery_sessions[msg.id]

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        if user.bot:
            return
        msg_id = reaction.message.id
        if msg_id not in self.lottery_sessions:
            return

        session = self.lottery_sessions[msg_id]

        if str(reaction.emoji) == "📥":
            session["participants"].add(user.display_name)

        elif str(reaction.emoji) == "📤" and user.id == session["author"]:
            del self.lottery_sessions[msg_id]
            participants = list(session["participants"])
            if not participants:
                await reaction.message.channel.send("참여자가 없습니다.")
                return
            n = min(session["n"], len(participants))
            winners = random.sample(participants, n)
            embed = discord.Embed(
                title=f"🎉 뽑기 결과 ({n}명)",
                description="\n".join(f"🏆 {w}" for w in winners),
                color=0x57F287,
            )
            await reaction.message.channel.send(embed=embed)

    # ── 랭킹 ──────────────────────────────────────────────

    async def _cmd_ranking(self, message: discord.Message):
        if not scores:
            await message.channel.send("아직 점수가 없습니다.")
            return
        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:10]
        embed = discord.Embed(title="🏆 미니게임 랭킹 TOP 10", color=0xF59E0B)
        for i, (uid, score) in enumerate(top, 1):
            member = message.guild.get_member(uid)
            name = member.display_name if member else f"유저({uid})"
            embed.add_field(name=f"{i}위 {name}", value=f"{score}점", inline=False)
        await message.channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Minigame(bot))
