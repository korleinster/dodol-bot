"""도돌봇 기능 테스트 봇"""
import asyncio
import os
import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN      = os.getenv("TESTER_TOKEN")
CHANNEL_ID = int(os.getenv("TEST_CHANNEL_ID", "0"))

PASS = "✅ PASS"
FAIL = "❌ FAIL"
SKIP = "⏭️  SKIP"

results: list[tuple[str, str, str]] = []  # (테스트명, 결과, 사유)


class Tester(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.channel: discord.TextChannel | None = None
        self.last_response: discord.Message | None = None
        self.waiting_event = asyncio.Event()

    async def on_ready(self):
        print(f"테스터 봇 온라인: {self.user}")
        self.channel = self.get_channel(CHANNEL_ID)
        if not self.channel:
            print(f"❌ 채널을 찾을 수 없습니다 (ID: {CHANNEL_ID})")
            await self.close()
            return
        await self.run_tests()
        self.print_summary()
        await self.close()

    async def on_message(self, message: discord.Message):
        if message.channel.id != CHANNEL_ID:
            return
        if message.author == self.user:
            return
        self.last_response = message
        self.waiting_event.set()

    async def send_and_wait(self, cmd: str, timeout: float = 5.0) -> discord.Message | None:
        self.last_response = None
        self.waiting_event.clear()
        await self.channel.send(cmd)
        try:
            await asyncio.wait_for(self.waiting_event.wait(), timeout=timeout)
            return self.last_response
        except asyncio.TimeoutError:
            return None

    def check(self, name: str, msg: discord.Message | None, *keywords: str):
        if msg is None:
            results.append((name, FAIL, "응답 없음 (timeout)"))
            print(f"{FAIL} {name} — 응답 없음")
            return False

        content = msg.content or ""
        if msg.embeds:
            for e in msg.embeds:
                content += (e.title or "") + (e.description or "")
                for f in e.fields:
                    content += f.name + f.value

        for kw in keywords:
            if kw not in content:
                results.append((name, FAIL, f"'{kw}' 없음"))
                print(f"{FAIL} {name} — '{kw}' 응답에 없음")
                return False

        results.append((name, PASS, ""))
        print(f"{PASS} {name}")
        return True

    async def run_tests(self):
        print(f"\n{'='*50}")
        print(f"  도돌봇 기능 테스트 시작 (채널: {self.channel.name})")
        print(f"{'='*50}\n")
        await asyncio.sleep(1)

        # ── 도움말 ────────────────────────────────────────
        msg = await self.send_and_wait("메뉴")
        self.check("도움말 (메뉴)", msg, "도돌봇")

        await asyncio.sleep(1)

        # ── 보스 등록 ─────────────────────────────────────
        msg = await self.send_and_wait("보스등록 0300 테스트보스(ㅌㅅ)")
        self.check("보스 등록", msg, "등록 완료", "테스트보스")

        await asyncio.sleep(1)

        # ── 보스 목록 ─────────────────────────────────────
        msg = await self.send_and_wait("보스")
        self.check("보스 목록", msg, "테스트보스")

        await asyncio.sleep(1)

        # ── 컷 ───────────────────────────────────────────
        msg = await self.send_and_wait("테스트보스 컷")
        self.check("컷 처리", msg, "컷 처리", "다음 리스폰")

        await asyncio.sleep(1)

        # ── 초성 컷 ───────────────────────────────────────
        msg = await self.send_and_wait("ㅌㅅ ㅋ")
        self.check("초성 컷 (ㅌㅅ ㅋ)", msg, "컷 처리")

        await asyncio.sleep(1)

        # ── 멍 ───────────────────────────────────────────
        msg = await self.send_and_wait("테스트보스 멍")
        self.check("멍 처리", msg, "멍 처리")

        await asyncio.sleep(1)

        # ── 초성 멍 ───────────────────────────────────────
        msg = await self.send_and_wait("ㅌㅅ ㅁ")
        self.check("초성 멍 (ㅌㅅ ㅁ)", msg, "멍 처리")

        await asyncio.sleep(1)

        # ── 보탐 ─────────────────────────────────────────
        msg = await self.send_and_wait("보탐")
        self.check("보탐 (예약 목록)", msg, "테스트보스")

        await asyncio.sleep(1)

        # ── 임의 예약 ─────────────────────────────────────
        msg = await self.send_and_wait("23:59 테스트알림")
        self.check("임의 예약", msg, "테스트알림", "예약 완료")

        await asyncio.sleep(1)

        # ── 예약 삭제 ─────────────────────────────────────
        msg = await self.send_and_wait("테스트알림 삭제")
        self.check("예약 삭제", msg, "삭제")

        await asyncio.sleep(1)

        # ── 초기화 ────────────────────────────────────────
        msg = await self.send_and_wait("초기화")
        self.check("전체 초기화", msg, "초기화")

        await asyncio.sleep(1)

        # ── 날씨 ─────────────────────────────────────────
        msg = await self.send_and_wait("날씨", timeout=10.0)
        self.check("날씨", msg, "날씨 예보")

        await asyncio.sleep(1)

        # ── 시세 ─────────────────────────────────────────
        msg = await self.send_and_wait("시세 싸울아비 장검", timeout=10.0)
        self.check("시세 검색", msg, "검색")

        await asyncio.sleep(1)

        # ── 주사위 ────────────────────────────────────────
        msg = await self.send_and_wait("주사위")
        self.check("주사위", msg, "🎲")

        await asyncio.sleep(1)

        # ── 동전 ─────────────────────────────────────────
        msg = await self.send_and_wait("동전")
        # 앞면 또는 뒷면 중 하나만 나옴
        if msg and ("앞면" in (msg.content or "") or "뒷면" in (msg.content or "")):
            results.append(("동전", PASS, ""))
            print(f"{PASS} 동전")
        else:
            results.append(("동전", FAIL, "앞면/뒷면 없음"))
            print(f"{FAIL} 동전")

        await asyncio.sleep(1)

        # ── 경마 ─────────────────────────────────────────
        msg = await self.send_and_wait("경마", timeout=20.0)
        self.check("경마", msg, "경마")

        await asyncio.sleep(1)

        # ── 보스 삭제 (정리) ──────────────────────────────
        msg = await self.send_and_wait("보스삭제 테스트보스")
        self.check("보스 삭제 (정리)", msg, "삭제 완료")

    def print_summary(self):
        total  = len(results)
        passed = sum(1 for _, r, _ in results if r == PASS)
        failed = total - passed

        print(f"\n{'='*50}")
        print(f"  결과: {passed}/{total} 통과  |  실패: {failed}")
        print(f"{'='*50}")

        if failed:
            print("\n실패 항목:")
            for name, result, reason in results:
                if result != PASS:
                    print(f"  {result} {name} — {reason}")
        print()


client = Tester()
client.run(TOKEN)
