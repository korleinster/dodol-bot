import hashlib
import hmac
import json
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web

if "gtts" not in sys.modules:
    try:
        __import__("gtts")
    except ModuleNotFoundError:
        gtts_stub = types.ModuleType("gtts")
        gtts_stub.gTTS = object
        sys.modules["gtts"] = gtts_stub

from src import db as db_module
from src.cogs import tts as tts_module
from src.cogs.tts import TTS, parse_tts_command
from src.web_bridge import WebBridge, _actor_id, _is_tts_command


class WebBridgePolicyTest(unittest.TestCase):
    def test_web_actor_id_is_stable_and_never_a_discord_snowflake(self):
        self.assertLess(_actor_id("profile:session"), 0)
        self.assertEqual(_actor_id("profile:session"), _actor_id("profile:session"))

    def test_system_commands_are_rejected_but_tts_and_games_are_allowed(self):
        for command in ("재시작", "정신차려", "소환 뚠뚠봇003", "설정"):
            with self.subTest(command=command), self.assertRaises(web.HTTPException):
                WebBridge._validate_command(command)

        for command in ("v 안녕하세요", "ㅍ 보스 출현", "Z", "Z+", "주사위 20", "경마 A B"):
            with self.subTest(command=command):
                WebBridge._validate_command(command)

    def test_tts_length_is_bounded(self):
        with self.assertRaises(web.HTTPBadRequest):
            WebBridge._validate_command("v " + "가" * 201)

    def test_manual_tts_parser_and_bridge_reject_non_space_delimiters(self):
        for command in ("v\t테스트", "ㅍ\t테스트", "v\n테스트", "ㅍ\n테스트"):
            with self.subTest(command=command):
                self.assertIsNone(parse_tts_command(command))
                self.assertFalse(_is_tts_command(command))
                with self.assertRaises(web.HTTPBadRequest):
                    WebBridge._validate_command(command)

    def test_only_v_and_korean_keyboard_alias_enter_manual_tts_queue(self):
        for command in ("v 안녕하세요", "V 안녕하세요", "ㅍ 보스 출현"):
            with self.subTest(command=command):
                self.assertTrue(_is_tts_command(command))
        for command in ("z", "Z", "z+", "Z+", "보탐", "주사위", "v", "ㅍ"):
            with self.subTest(command=command):
                self.assertFalse(_is_tts_command(command))


class VoiceRuntimeCapabilityTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        tts_module.detect_voice_runtime_capability.cache_clear()

    def test_pinned_dave_runtime_is_accepted(self):
        versions = {
            "discord.py": "2.7.1",
            "davey": "0.1.6",
            "PyNaCl": "1.5.0",
        }
        with (
            patch.object(tts_module.importlib.metadata, "version", side_effect=versions.__getitem__),
            patch.object(tts_module.importlib.util, "find_spec", return_value=object()),
            patch.object(tts_module.shutil, "which", return_value="/usr/bin/ffmpeg"),
        ):
            self.assertEqual(
                tts_module.detect_voice_runtime_capability(),
                (True, "VOICE_RUNTIME_READY"),
            )

    def test_wrong_discord_version_fails_with_safe_code(self):
        versions = {
            "discord.py": "2.6.4",
            "davey": "0.1.6",
            "PyNaCl": "1.5.0",
        }
        with patch.object(
            tts_module.importlib.metadata,
            "version",
            side_effect=versions.__getitem__,
        ):
            self.assertEqual(
                tts_module.detect_voice_runtime_capability(),
                (False, "DISCORD_PY_VERSION_UNSUPPORTED"),
            )

    async def test_speak_fails_before_file_generation_when_runtime_is_unavailable(self):
        cog = TTS(SimpleNamespace(bot_number=3))
        cog._voice_runtime_ready = Mock(return_value=False)
        with patch.object(tts_module, "_save_tts") as save_tts:
            played = await cog.speak(SimpleNamespace(id=100), "테스트")
        self.assertFalse(played)
        save_tts.assert_not_called()


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
        for command in ("v 테스트", "ㅍ 테스트"):
            with self.subTest(command=command):
                bot = SimpleNamespace(bot_number=3)
                cog = TTS(bot)
                cog.get_text_channel = AsyncMock(return_value=10)
                cog.speak = AsyncMock(return_value=False)
                message = SimpleNamespace(
                    author=SimpleNamespace(bot=False, actor_type="web_guest"),
                    guild=SimpleNamespace(id=20),
                    channel=SimpleNamespace(id=10),
                    content=command,
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


class _FakeEmbed:
    def __init__(self, payload):
        self.payload = payload

    def to_dict(self):
        return self.payload


class _FakeMessage:
    def __init__(
        self, *, message_id=1, guild_id=100, channel_id=200, author_id=999,
        content="안녕 <@123>", edited_at=None, embeds=None, attachments=None, components=None,
    ):
        self.id = message_id
        self.guild = SimpleNamespace(id=guild_id) if guild_id is not None else None
        self.channel = SimpleNamespace(id=channel_id)
        self.author = SimpleNamespace(id=author_id)
        self.clean_content = content
        self.content = content
        self.edited_at = edited_at
        self.embeds = embeds or []
        self.attachments = attachments or []
        self.components = components or []


class _FakeBridgeRequest:
    def __init__(self, path_qs, headers):
        parsed = urlsplit(path_qs)
        self.path_qs = path_qs
        self.method = "GET"
        self.headers = headers
        self.query = dict(parse_qsl(parsed.query))

    async def read(self):
        return b""


class WebBroadcastBridgeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_path = db_module.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        db_module.DB_PATH = Path(self.tmp.name) / "bot.db"
        await db_module.init_db()
        async with db_module.get_db() as db:
            await db.execute(
                "INSERT INTO guild_config (guild_id, bot_number, text_channel_id) VALUES (?, ?, ?)",
                (100, 3, 200),
            )
            await db.execute(
                "INSERT INTO guild_config (guild_id, bot_number, text_channel_id) VALUES (?, ?, ?)",
                (101, 3, 201),
            )
            await db.commit()
        self.bot = SimpleNamespace(bot_number=3, user=SimpleNamespace(id=999))
        self.bridge = WebBridge(self.bot, "b" * 32, Path(self.tmp.name) / "bridge.sock")

    async def asyncTearDown(self):
        db_module.DB_PATH = self.previous_path
        self.tmp.cleanup()

    async def test_filters_to_003_own_configured_guild_channel(self):
        await self.bridge._on_broadcast_message(_FakeMessage(message_id=1))
        await self.bridge._on_broadcast_message(_FakeMessage(message_id=2, author_id=998))
        await self.bridge._on_broadcast_message(_FakeMessage(message_id=3, channel_id=201))
        await self.bridge._on_broadcast_message(_FakeMessage(message_id=4, guild_id=None))

        events, cursor = await db_module.list_web_broadcast_events(guild_id=100)
        self.assertEqual(cursor, events[-1]["cursor"])
        self.assertEqual([event["messageId"] for event in events], ["1"])
        self.assertEqual(events[0]["content"], "안녕 <@123>")

    async def test_create_update_delete_are_deduplicated_and_preserve_message_id(self):
        created = _FakeMessage(
            message_id=10,
            embeds=[_FakeEmbed({
                "title": "제목", "description": "내용", "color": 123,
                "fields": [{"name": "보스", "value": "안타라스"}],
                "footer": {"text": "003"}, "url": "https://example.test",
                "image": {"url": "https://example.test/image.png"},
                "thumbnail": {"url": "https://example.test/thumb.png"},
                "author": {"name": "뚠뚠봇"},
            })],
        )
        await self.bridge._on_broadcast_message(created)
        await self.bridge._on_broadcast_message(created)
        updated = _FakeMessage(
            message_id=10, content="수정됨", edited_at=SimpleNamespace(isoformat=lambda: "2026-07-23T01:02:03+00:00"),
        )
        await self.bridge._on_broadcast_message_edit(created, updated)
        await self.bridge._on_broadcast_message_edit(created, updated)
        await self.bridge._on_broadcast_message_delete(updated)
        await self.bridge._on_broadcast_message_delete(updated)
        await self.bridge._on_raw_broadcast_message_delete(
            SimpleNamespace(guild_id=100, channel_id=200, message_id=10),
        )

        events, _ = await db_module.list_web_broadcast_events(guild_id=100, limit=10)
        self.assertEqual([event["kind"] for event in events], ["message", "message_update", "message_delete"])
        self.assertTrue(all(event["messageId"] == "10" for event in events))
        self.assertEqual(events[0]["embeds"][0]["imageUrl"], "https://example.test/image.png")
        self.assertEqual(events[1]["content"], "수정됨")
        self.assertIsNone(events[2]["content"])
        self.assertEqual(events[2]["embeds"], [])

    async def test_raw_delete_only_tombstones_a_previously_mirrored_bot_message(self):
        await self.bridge._on_raw_broadcast_message_delete(
            SimpleNamespace(guild_id=100, channel_id=200, message_id=88),
        )
        await self.bridge._on_broadcast_message(_FakeMessage(message_id=88))
        await self.bridge._on_raw_broadcast_message_delete(
            SimpleNamespace(guild_id=100, channel_id=200, message_id=88),
        )
        await self.bridge._on_raw_broadcast_message_delete(
            SimpleNamespace(guild_id=100, channel_id=201, message_id=88),
        )
        events, _ = await db_module.list_web_broadcast_events(guild_id=100)
        self.assertEqual([event["kind"] for event in events], ["message", "message_delete"])

    async def test_guild_cursor_limit_and_channel_isolation(self):
        for message_id in range(1, 4):
            await self.bridge._on_broadcast_message(_FakeMessage(message_id=message_id))
        await self.bridge._on_broadcast_message(
            _FakeMessage(message_id=4, guild_id=101, channel_id=201),
        )
        initial, initial_cursor = await db_module.list_web_broadcast_events(guild_id=100, limit=2)
        self.assertEqual([item["messageId"] for item in initial], ["2", "3"])
        self.assertEqual(initial_cursor, initial[-1]["cursor"])
        later, next_cursor = await db_module.list_web_broadcast_events(
            guild_id=100, after=initial[0]["cursor"], limit=10,
        )
        self.assertEqual([item["messageId"] for item in later], ["3"])
        self.assertEqual(next_cursor, later[-1]["cursor"])
        other, _ = await db_module.list_web_broadcast_events(guild_id=101)
        self.assertEqual([item["messageId"] for item in other], ["4"])

    async def test_retention_excludes_expired_rows_and_caps_each_guild(self):
        expired_at = int(time.time() * 1000) - db_module.WEB_BROADCAST_RETENTION_MS - 1
        await db_module.append_web_broadcast_event(
            event_key="expired",
            guild_id=100,
            channel_id=200,
            message_id=9000,
            kind="message",
            content="expired",
            embeds=[],
            attachments=[],
            components=[],
            created_at=expired_at,
        )
        for message_id in range(1000, 1501):
            await db_module.append_web_broadcast_event(
                event_key=f"retained:{message_id}",
                guild_id=100,
                channel_id=200,
                message_id=message_id,
                kind="message",
                content=str(message_id),
                embeds=[],
                attachments=[],
                components=[],
            )

        events, _ = await db_module.list_web_broadcast_events(
            guild_id=100,
            channel_id=200,
            limit=db_module.WEB_BROADCAST_MAX_EVENTS_PER_GUILD,
        )
        self.assertEqual(len(events), db_module.WEB_BROADCAST_MAX_EVENTS_PER_GUILD)
        self.assertEqual(events[0]["messageId"], "1001")
        self.assertEqual(events[-1]["messageId"], "1500")
        self.assertNotIn("9000", {event["messageId"] for event in events})

    async def test_003_only_listener_registration_and_capture(self):
        calls = []
        bot = SimpleNamespace(
            bot_number=1,
            user=SimpleNamespace(id=999),
            add_listener=lambda *args: calls.append(args),
            remove_listener=lambda *args: calls.append(args),
        )
        bridge = WebBridge(bot, "b" * 32, Path(self.tmp.name) / "unused.sock")
        bridge._register_broadcast_listeners()
        await bridge._on_broadcast_message(_FakeMessage(message_id=33))
        events, _ = await db_module.list_web_broadcast_events(guild_id=100)
        self.assertEqual(calls, [])
        self.assertEqual(events, [])

    async def test_hmac_broadcast_endpoint_binds_query_and_returns_contract(self):
        await self.bridge._on_broadcast_message(_FakeMessage(message_id=50))
        path = "/internal/v1/broadcast-events?guildId=100&after=0&limit=10"
        timestamp = str(int(time.time()))
        nonce = "n" * 16
        body_hash = hashlib.sha256(b"").hexdigest()
        canonical = "\n".join((timestamp, nonce, "GET", path, body_hash)).encode("utf-8")
        signature = hmac.new(b"b" * 32, canonical, hashlib.sha256).hexdigest()
        response = await self.bridge.broadcast_events(_FakeBridgeRequest(path, {
            "x-lc-timestamp": timestamp,
            "x-lc-nonce": nonce,
            "x-lc-signature": signature,
        }))
        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual([event["messageId"] for event in payload["events"]], ["50"])
        self.assertEqual(payload["nextCursor"], payload["events"][-1]["cursor"])

        # A signature for guild 100 cannot be replayed against a new query.
        with self.assertRaises(web.HTTPUnauthorized):
            await self.bridge.broadcast_events(_FakeBridgeRequest(
                "/internal/v1/broadcast-events?guildId=101&after=0&limit=10",
                {
                    "x-lc-timestamp": timestamp,
                    "x-lc-nonce": "m" * 16,
                    "x-lc-signature": signature,
                },
            ))


if __name__ == "__main__":
    unittest.main()
