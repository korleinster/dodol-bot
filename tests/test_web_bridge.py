import hashlib
import hmac
import json
import os
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
from src.cogs.minigame import Minigame
from src.cogs.tts import TTS, parse_tts_command
from src.web_bridge import (
    MAX_BOT_DISPLAY_NAME,
    WebBridge,
    _actor_id,
    _is_tts_command,
    _safe_bot_display_name,
    start_web_bridge,
)


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

    def test_bot_display_name_is_plain_text_bounded_and_mention_safe(self):
        self.assertEqual(
            _safe_bot_display_name("  뚠뚠\x00\n<@everyone>  "),
            "뚠뚠 <＠everyone>",
        )
        self.assertEqual(
            _safe_bot_display_name("가" * (MAX_BOT_DISPLAY_NAME + 1)),
            "가" * MAX_BOT_DISPLAY_NAME,
        )
        self.assertIsNone(_safe_bot_display_name("\x00\u200b\n"))
        self.assertIsNone(_safe_bot_display_name(None))


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

    async def test_speak_fails_before_file_generation_without_a_configured_voice_channel(self):
        cog = TTS(SimpleNamespace(bot_number=2))
        cog._voice_runtime_ready = Mock(return_value=True)
        cog.get_voice_channel = AsyncMock(return_value=None)
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

    async def test_shared_bridge_rows_backfill_to_bot_003_additively(self):
        previous = db_module.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bot.db"
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE web_broadcast_event (
                    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT,
                    embeds_json TEXT NOT NULL DEFAULT '[]',
                    attachments_json TEXT NOT NULL DEFAULT '[]',
                    components_json TEXT NOT NULL DEFAULT '[]',
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE component_action_claim (
                    idempotency_key TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    custom_id TEXT NOT NULL,
                    action_key TEXT NOT NULL,
                    actor_type TEXT NOT NULL,
                    actor_ref TEXT,
                    status TEXT NOT NULL,
                    error_code TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    completed_at INTEGER
                );
                INSERT INTO web_broadcast_event
                    (event_key, guild_id, channel_id, message_id, kind, created_at)
                    VALUES ('message:1', 100, 200, 1, 'message', 1);
                INSERT INTO component_action_claim
                    (idempotency_key, request_id, guild_id, channel_id, message_id,
                     custom_id, action_key, actor_type, status, created_at, updated_at)
                    VALUES ('boss-message:100:1', 'legacy-request', 100, 200, 1,
                            'legacy', 'boss_cut', 'web_guest', 'succeeded', 1, 1);
            """)
            conn.commit()
            conn.close()
            db_module.DB_PATH = path
            try:
                await db_module.init_db()
                conn = sqlite3.connect(path)
                broadcast = conn.execute(
                    "SELECT bot_number, event_key FROM web_broadcast_event",
                ).fetchone()
                claim = conn.execute(
                    "SELECT bot_number, idempotency_key FROM component_action_claim",
                ).fetchone()
                conn.close()
                self.assertEqual(broadcast, (3, "message:1"))
                self.assertEqual(claim, (3, "boss-message:100:1"))
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
            await db.execute(
                """INSERT INTO guild_config
                   (guild_id, bot_number, text_channel_id, voice_channel_id)
                   VALUES (?, ?, ?, ?)""",
                (100, 1, 300, 350),
            )
            await db.commit()
        self.bot = SimpleNamespace(
            bot_number=3,
            user=SimpleNamespace(id=999, display_name="전역 뚠뚠봇", name="global-name"),
            get_guild=lambda guild_id: SimpleNamespace(
                id=guild_id,
                name=f"길드 {guild_id}",
                me=SimpleNamespace(display_name="서버 전용 뚠뚠봇") if guild_id == 100 else None,
            ),
        )
        self.bridge = WebBridge(self.bot, "b" * 32, Path(self.tmp.name) / "bridge.sock")

    async def asyncTearDown(self):
        db_module.DB_PATH = self.previous_path
        self.tmp.cleanup()

    async def test_filters_to_current_bots_own_configured_guild_channel(self):
        await self.bridge._on_broadcast_message(_FakeMessage(message_id=1))
        await self.bridge._on_broadcast_message(_FakeMessage(message_id=2, author_id=998))
        await self.bridge._on_broadcast_message(_FakeMessage(message_id=3, channel_id=201))
        await self.bridge._on_broadcast_message(_FakeMessage(message_id=4, guild_id=None))

        events, cursor = await db_module.list_web_broadcast_events(guild_id=100)
        self.assertEqual(cursor, events[-1]["cursor"])
        self.assertEqual([event["messageId"] for event in events], ["1"])
        self.assertEqual(events[0]["content"], "안녕 <@123>")
        self.assertEqual(events[0]["botNumber"], 3)

    async def test_same_guild_and_message_are_isolated_between_bots(self):
        bot1 = SimpleNamespace(bot_number=1, user=SimpleNamespace(id=111))
        bridge1 = WebBridge(bot1, "a" * 32, Path(self.tmp.name) / "bridge-001.sock")
        await self.bridge._on_broadcast_message(_FakeMessage(message_id=77))
        await bridge1._on_broadcast_message(
            _FakeMessage(message_id=77, channel_id=300, author_id=111),
        )

        bot3_events, _ = await db_module.list_web_broadcast_events(
            bot_number=3, guild_id=100,
        )
        bot1_events, _ = await db_module.list_web_broadcast_events(
            bot_number=1, guild_id=100,
        )
        self.assertEqual(
            [(item["botNumber"], item["messageId"]) for item in bot3_events],
            [(3, "77")],
        )
        self.assertEqual(
            [(item["botNumber"], item["messageId"]) for item in bot1_events],
            [(1, "77")],
        )

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

    async def test_001_listener_registration_and_capture(self):
        calls = []
        bot = SimpleNamespace(
            bot_number=1,
            user=SimpleNamespace(id=111),
            add_listener=lambda *args: calls.append(args),
            remove_listener=lambda *args: calls.append(args),
        )
        bridge = WebBridge(bot, "b" * 32, Path(self.tmp.name) / "unused.sock")
        bridge._register_broadcast_listeners()
        await bridge._on_broadcast_message(
            _FakeMessage(message_id=33, channel_id=300, author_id=111),
        )
        events, _ = await db_module.list_web_broadcast_events(
            bot_number=1, guild_id=100,
        )
        self.assertEqual(len(calls), 4)
        self.assertEqual([item["messageId"] for item in events], ["33"])

    async def test_out_of_scope_bot_does_not_register_or_capture(self):
        calls = []
        bot = SimpleNamespace(
            bot_number=5,
            user=SimpleNamespace(id=555),
            add_listener=lambda *args: calls.append(args),
            remove_listener=lambda *args: calls.append(args),
        )
        bridge = WebBridge(bot, "b" * 32, Path(self.tmp.name) / "unused-005.sock")
        bridge._register_broadcast_listeners()
        await bridge._on_broadcast_message(
            _FakeMessage(message_id=34, channel_id=300, author_id=555),
        )
        self.assertEqual(calls, [])

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

    async def test_targets_expose_tts_capability_only_with_voice_configuration(self):
        self.bridge._authenticate = AsyncMock(return_value=b"")
        response = await self.bridge.targets(SimpleNamespace())
        payload = json.loads(response.body)
        targets = {item["guildId"]: item for item in payload["targets"]}
        self.assertFalse(targets["100"]["capabilities"]["tts"])
        self.assertFalse(targets["101"]["capabilities"]["tts"])
        self.assertTrue(targets["100"]["capabilities"]["commands"])
        self.assertEqual(targets["100"]["botDisplayName"], "서버 전용 뚠뚠봇")
        self.assertEqual(targets["101"]["botDisplayName"], "전역 뚠뚠봇")

        bot1 = SimpleNamespace(
            bot_number=1,
            user=SimpleNamespace(id=111),
            get_guild=lambda guild_id: SimpleNamespace(id=guild_id, name="길드"),
        )
        bridge1 = WebBridge(bot1, "a" * 32, Path(self.tmp.name) / "bridge-001.sock")
        bridge1._authenticate = AsyncMock(return_value=b"")
        response1 = await bridge1.targets(SimpleNamespace())
        target1 = json.loads(response1.body)["targets"][0]
        self.assertEqual(target1["botNumber"], 1)
        self.assertTrue(target1["capabilities"]["tts"])

    async def test_targets_fall_back_to_a_safe_numbered_bot_name(self):
        self.bot.user = SimpleNamespace(id=999, display_name="\x00", name="\u200b")
        self.bot.get_guild = lambda guild_id: SimpleNamespace(id=guild_id, name=f"길드 {guild_id}")
        self.bridge._authenticate = AsyncMock(return_value=b"")
        response = await self.bridge.targets(SimpleNamespace())
        targets = json.loads(response.body)["targets"]
        self.assertEqual({target["botDisplayName"] for target in targets}, {"보탐봇 3"})

    async def test_owner_component_fallback_uses_administrator_label(self):
        self.bridge._json = AsyncMock(return_value={
            "requestId": "request-owner-001",
            "actorRef": "owner:session",
            "nickname": "",
            "actorType": "owner",
            "customId": "test-action",
            "guildId": "100",
            "messageId": "1",
        })
        self.bridge.component_dispatcher.dispatch = AsyncMock(
            return_value=SimpleNamespace(payload=lambda: {}),
        )

        await self.bridge.component_actions(SimpleNamespace())

        actor = self.bridge.component_dispatcher.dispatch.await_args.kwargs["actor"]
        self.assertEqual(actor.display_name, "웹 · 관리자")

    async def test_tts_without_voice_is_rejected_before_job_or_queue_creation(self):
        self.bridge._json = AsyncMock(return_value={
            "requestId": "request-tts-001",
            "command": "v 테스트",
            "actorRef": "guest:session",
            "nickname": "테스터",
            "guildId": "101",
        })
        with self.assertRaises(web.HTTPConflict) as raised:
            await self.bridge.create_command(SimpleNamespace())
        payload = json.loads(raised.exception.text)
        self.assertEqual(payload["errorCode"], "TTS_UNAVAILABLE")
        self.assertIn("음성 채널", payload["recoveryInstructions"])
        self.assertEqual(self.bridge.jobs, {})
        self.assertEqual(self.bridge.tts_pending, 0)


class WebBridgeEnvironmentTest(unittest.IsolatedAsyncioTestCase):
    async def test_numbered_secret_and_socket_are_used_for_each_bridge_bot(self):
        for bot_number in range(1, 5):
            label = f"{bot_number:03d}"
            socket_path = f"/tmp/test-botam-{label}.sock"
            env = {
                "BOTAM_WEB_BRIDGE_ENABLED": "true",
                f"BOTAM_BRIDGE_{label}_SECRET": label * 11,
                f"BOTAM_BRIDGE_{label}_SOCKET": socket_path,
            }
            with (
                self.subTest(bot_number=bot_number),
                patch.dict(os.environ, env, clear=True),
                patch.object(WebBridge, "start", new=AsyncMock()),
            ):
                bridge = await start_web_bridge(SimpleNamespace(bot_number=bot_number))
                self.assertIsNotNone(bridge)
                self.assertEqual(bridge.secret, (label * 11).encode())
                self.assertEqual(bridge.socket_path, Path(socket_path))

    async def test_legacy_unnumbered_fallback_is_limited_to_bot_003(self):
        env = {
            "BOTAM_WEB_BRIDGE_ENABLED": "true",
            "BOTAM_BRIDGE_SECRET": "legacy-" * 6,
            "BOTAM_BRIDGE_SOCKET": "/tmp/legacy-003.sock",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(WebBridge, "start", new=AsyncMock()),
        ):
            bridge = await start_web_bridge(SimpleNamespace(bot_number=3))
            self.assertIsNotNone(bridge)
            self.assertEqual(bridge.socket_path, Path("/tmp/legacy-003.sock"))
            with self.assertRaisesRegex(RuntimeError, "BOTAM_BRIDGE_001_SECRET"):
                await start_web_bridge(SimpleNamespace(bot_number=1))

    async def test_bot_003_can_pair_legacy_secret_with_numbered_socket_during_transition(self):
        env = {
            "BOTAM_WEB_BRIDGE_ENABLED": "true",
            "BOTAM_BRIDGE_SECRET": "legacy-" * 6,
            "BOTAM_BRIDGE_003_SOCKET": "/tmp/numbered-003.sock",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(WebBridge, "start", new=AsyncMock()),
        ):
            bridge = await start_web_bridge(SimpleNamespace(bot_number=3))
        self.assertIsNotNone(bridge)
        self.assertEqual(bridge.secret, ("legacy-" * 6).encode())
        self.assertEqual(bridge.socket_path, Path("/tmp/numbered-003.sock"))

    async def test_partial_bridge_startup_is_cleaned_before_error_propagates(self):
        env = {
            "BOTAM_WEB_BRIDGE_ENABLED": "true",
            "BOTAM_BRIDGE_004_SECRET": "four-" * 8,
            "BOTAM_BRIDGE_004_SOCKET": "/tmp/failing-004.sock",
        }
        close = AsyncMock()
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(
                WebBridge,
                "start",
                new=AsyncMock(side_effect=RuntimeError("socket unavailable")),
            ),
            patch.object(WebBridge, "close", new=close),
            self.assertRaisesRegex(RuntimeError, "socket unavailable"),
        ):
            await start_web_bridge(SimpleNamespace(bot_number=4))
        close.assert_awaited_once()


class MinigameIsolationTest(unittest.IsolatedAsyncioTestCase):
    async def test_lottery_draw_rejects_a_different_guild_without_consuming_session(self):
        cog = Minigame(SimpleNamespace(bot_number=2))
        cog.lottery_sessions[50] = {
            "author_ref": "guest:session",
            "bot_number": 2,
            "guild_id": 100,
            "participants": {"A"},
            "n": 1,
            "message": SimpleNamespace(channel=SimpleNamespace(send=AsyncMock())),
        }
        output = SimpleNamespace(send=AsyncMock())
        rejected = await cog.finish_lottery(
            50, "guest:session", output, guild_id=101,
        )
        self.assertIn("다른 서버", rejected)
        self.assertIn(50, cog.lottery_sessions)
        output.send.assert_not_awaited()

        accepted = await cog.finish_lottery(
            50, "guest:session", output, guild_id=100,
        )
        self.assertNotIsInstance(accepted, str)
        self.assertNotIn(50, cog.lottery_sessions)

    async def test_rankings_are_isolated_by_bot_instance_and_guild(self):
        cog = Minigame(SimpleNamespace(bot_number=2))
        cog.scores[(100, 7)] = 10
        cog.score_names[(100, 7)] = "길드100"
        cog.scores[(101, 8)] = 20
        cog.score_names[(101, 8)] = "길드101"

        channel = SimpleNamespace(send=AsyncMock())
        message = SimpleNamespace(
            guild=SimpleNamespace(id=100, get_member=lambda _uid: None),
            channel=channel,
        )
        await cog._cmd_ranking(message)
        sent_embed = channel.send.await_args.kwargs["embed"]
        self.assertEqual([field.name for field in sent_embed.fields], ["1위 길드100"])

        other_bot = Minigame(SimpleNamespace(bot_number=3))
        other_channel = SimpleNamespace(send=AsyncMock())
        await other_bot._cmd_ranking(SimpleNamespace(
            guild=SimpleNamespace(id=100, get_member=lambda _uid: None),
            channel=other_channel,
        ))
        other_channel.send.assert_awaited_once_with("아직 점수가 없습니다.")


if __name__ == "__main__":
    unittest.main()
