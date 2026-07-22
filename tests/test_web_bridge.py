import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import web

if "gtts" not in sys.modules:
    try:
        __import__("gtts")
    except ModuleNotFoundError:
        gtts_stub = types.ModuleType("gtts")
        gtts_stub.gTTS = object
        sys.modules["gtts"] = gtts_stub

from src import db as db_module
from src.cogs.tts import TTS
from src.web_bridge import WebBridge, _actor_id


class WebBridgePolicyTest(unittest.TestCase):
    def test_web_actor_id_is_stable_and_never_a_discord_snowflake(self):
        self.assertLess(_actor_id("profile:session"), 0)
        self.assertEqual(_actor_id("profile:session"), _actor_id("profile:session"))

    def test_system_commands_are_rejected_but_tts_and_games_are_allowed(self):
        for command in ("재시작", "정신차려", "소환 뚠뚠봇003", "설정"):
            with self.subTest(command=command), self.assertRaises(web.HTTPException):
                WebBridge._validate_command(command)

        for command in ("v 안녕하세요", "ㅍ 보스 출현", "Z", "주사위 20", "경마 A B"):
            with self.subTest(command=command):
                WebBridge._validate_command(command)

    def test_tts_length_is_bounded(self):
        with self.assertRaises(web.HTTPBadRequest):
            WebBridge._validate_command("v " + "가" * 201)


class ContributionMigrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_actor_columns_are_added_without_rebuilding_existing_rows(self):
        previous = db_module.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bot.db"
            db_module.DB_PATH = path
            try:
                await db_module.init_db()
                conn = sqlite3.connect(path)
                columns = {row[1] for row in conn.execute("PRAGMA table_info(contributions)")}
                conn.close()
                self.assertIn("actor_type", columns)
                self.assertIn("actor_ref", columns)
            finally:
                db_module.DB_PATH = previous


class WebTtsResultTest(unittest.IsolatedAsyncioTestCase):
    async def test_web_tts_fails_closed_when_playback_does_not_start(self):
        bot = SimpleNamespace(bot_number=3)
        cog = TTS(bot)
        cog.get_text_channel = AsyncMock(return_value=10)
        cog.speak = AsyncMock(return_value=False)
        message = SimpleNamespace(
            author=SimpleNamespace(bot=False, actor_type="web_guest"),
            guild=SimpleNamespace(id=20),
            channel=SimpleNamespace(id=10),
            content="v 테스트",
        )
        with self.assertRaisesRegex(RuntimeError, "TTS playback failed"):
            await cog.on_message(message)

    async def test_discord_tts_preserves_existing_non_raising_behavior(self):
        bot = SimpleNamespace(bot_number=3)
        cog = TTS(bot)
        cog.get_text_channel = AsyncMock(return_value=10)
        cog.speak = AsyncMock(return_value=False)
        message = SimpleNamespace(
            author=SimpleNamespace(bot=False),
            guild=SimpleNamespace(id=20),
            channel=SimpleNamespace(id=10),
            content="v 테스트",
        )
        await cog.on_message(message)


if __name__ == "__main__":
    unittest.main()
