import asyncio
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
from unittest.mock import AsyncMock

if "gtts" not in sys.modules:
    try:
        __import__("gtts")
    except ModuleNotFoundError:
        gtts_stub = types.ModuleType("gtts")
        gtts_stub.gTTS = object
        sys.modules["gtts"] = gtts_stub

from src import db as db_module
from src.component_actions import (
    COMPONENT_ACTIONS,
    ComponentActionDefinition,
    ComponentActionDispatcher,
    ComponentActionRegistry,
    boss_component_custom_id,
)
from src.web_bridge import WebBridge, _message_components


class _Button:
    def __init__(self, custom_id, *, label="button", disabled=False, style=1):
        self.custom_id = custom_id
        self.label = label
        self.disabled = disabled
        self.style = style
        self.type = 2


class _Row:
    def __init__(self, *children):
        self.children = children


class _ActionMessage:
    def __init__(self, *, message_id, guild_id=100, channel_id=200, author_id=999, components=()):
        self.id = message_id
        self.guild = SimpleNamespace(id=guild_id)
        self.channel = SimpleNamespace(id=channel_id, send=AsyncMock())
        self.author = SimpleNamespace(id=author_id)
        self.components = list(components)


class _ActionBoss:
    def __init__(self, *, delay=0, failures=0, db_failures=0, disable_failures=0):
        self.delay = delay
        self.failures = failures
        self.db_failures = db_failures
        self.disable_failures = disable_failures
        self.calls = []
        self.disable_calls = 0

    async def execute_registered_component(self, *, action, message, actor):
        self.calls.append((action.key, actor.id))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.db_failures:
            self.db_failures -= 1
            raise sqlite3.OperationalError("database is locked")
        if self.failures:
            self.failures -= 1
            return "❌ 보스 상태를 확인할 수 없습니다."
        return SimpleNamespace(kind="embed")

    async def disable_registered_component(self, *, message, action):
        self.disable_calls += 1
        if self.disable_failures:
            self.disable_failures -= 1
            raise RuntimeError("Discord message edit failed")
        for row in message.components:
            for item in row.children:
                resolved = COMPONENT_ACTIONS.resolve(item.custom_id, message.guild.id, message.id)
                if resolved is not None and resolved.idempotency_key == action.idempotency_key:
                    item.disabled = True
        return message


class _ActionBot:
    def __init__(self, boss, *, bot_number=3, user_id=999):
        self.bot_number = bot_number
        self.user = SimpleNamespace(id=user_id)
        self._boss = boss

    def get_cog(self, name):
        return self._boss if name == "Boss" else None


class _PostRequest:
    def __init__(self, path, body, headers):
        self.path_qs = path
        self.method = "POST"
        self.headers = headers
        self._body = body

    async def read(self):
        return self._body


class ComponentRegistryTest(unittest.TestCase):
    def test_registration_requires_a_literal_boolean_and_unknown_components_fail_closed(self):
        with self.assertRaises(TypeError):
            ComponentActionDefinition(
                key="bad", custom_id_pattern=__import__("re").compile("bad"),
                allow_non_admin=1, handler_cog="Boss", handler_name="run",
                idempotency_key=lambda *_: "bad",
            )

        metadata = COMPONENT_ACTIONS.metadata(
            custom_id="unregistered:button", label="알 수 없음", component_type=2,
            style=1, disabled=False, guild_id=100, message_id=50,
        )
        self.assertFalse(metadata["actionable"])
        self.assertFalse(metadata["allowNonAdmin"])

    def test_versioned_boss_buttons_are_registered_and_legacy_ids_are_not(self):
        custom_id = boss_component_custom_id("cut", 100, "체르투바")
        action = COMPONENT_ACTIONS.resolve(custom_id, 100, 50)
        self.assertIsNotNone(action)
        self.assertTrue(action.allow_non_admin)
        self.assertEqual(action.idempotency_key, "boss-message:100:50")
        self.assertIsNone(COMPONENT_ACTIONS.resolve("boss_cut:100:체르투바", 100, 50))

    def test_new_broadcast_metadata_carries_style_disabled_and_policy_flags(self):
        message = _ActionMessage(
            message_id=50,
            components=[_Row(_Button(boss_component_custom_id("cut", 100, "체르투바"), label="✅ 컷", style=3))],
        )
        metadata = _message_components(message)
        self.assertEqual(metadata, [{
            "label": "✅ 컷", "customId": boss_component_custom_id("cut", 100, "체르투바"),
            "type": 2, "style": 3, "disabled": False,
            "actionable": True, "allowNonAdmin": True,
        }])


class ComponentDispatcherTest(unittest.IsolatedAsyncioTestCase):
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
                (100, 1, 201),
            )
            await db.commit()
        self.boss = _ActionBoss()
        self.bot = _ActionBot(self.boss)
        self.dispatcher = ComponentActionDispatcher(self.bot)
        self.actor = SimpleNamespace(id=-42, name="웹 · 테스터", display_name="웹 · 테스터")
        self.cut_id = boss_component_custom_id("cut", 100, "체르투바")
        self.miss_id = boss_component_custom_id("miss", 100, "체르투바")

    async def asyncTearDown(self):
        db_module.DB_PATH = self.previous_path
        self.tmp.cleanup()

    def _message(self, **kwargs):
        return _ActionMessage(
            message_id=50,
            components=[_Row(_Button(self.cut_id, label="✅ 컷"), _Button(self.miss_id, label="😶 멍"))],
            **kwargs,
        )

    async def _dispatch(self, *, request_id="request-0001", custom_id=None, message=None):
        return await self.dispatcher.dispatch(
            request_id=request_id,
            guild_id=100,
            message_id=50,
            custom_id=custom_id or self.cut_id,
            actor=self.actor,
            actor_type="web_guest",
            actor_ref="guest:session",
            is_owner=False,
            message=message or self._message(),
        )

    async def test_cut_and_miss_share_one_durable_message_claim(self):
        message = self._message()
        first = await self._dispatch(message=message)
        second = await self._dispatch(request_id="request-0002", custom_id=self.miss_id, message=message)

        self.assertEqual(first.status, "succeeded")
        self.assertEqual(second.status, "already_processed")
        self.assertEqual(self.boss.calls, [("boss_cut", -42)])
        self.assertTrue(all(item.disabled for item in message.components[0].children))
        self.assertTrue(all(item["disabled"] for item in second.components))
        self.assertEqual(message.channel.send.await_count, 1)

        async with db_module.get_db() as db:
            row = await (await db.execute(
                """SELECT status, attempt_count FROM component_action_claim
                   WHERE bot_number=? AND guild_id=? AND message_id=?""",
                (3, 100, 50),
            )).fetchone()
        self.assertEqual((row["status"], row["attempt_count"]), ("succeeded", 1))

    async def test_concurrent_competing_clicks_execute_only_one_handler(self):
        self.boss.delay = 0.03
        message = self._message()
        first, second = await asyncio.gather(
            self._dispatch(message=message),
            self._dispatch(request_id="request-0002", custom_id=self.miss_id, message=message),
        )

        self.assertEqual(len(self.boss.calls), 1)
        self.assertEqual(sum(result.status == "succeeded" for result in (first, second)), 1)
        duplicate = second if first.status == "succeeded" else first
        self.assertIn(duplicate.status, {"failed", "already_processed"})
        if duplicate.status == "failed":
            self.assertEqual(duplicate.error_code, "ACTION_IN_PROGRESS")
        else:
            self.assertIsNone(duplicate.error_code)
        self.assertTrue(all(item.disabled for item in message.components[0].children))
        self.assertEqual(message.channel.send.await_count, 1)

        async with db_module.get_db() as db:
            row = await (await db.execute(
                """SELECT status, attempt_count FROM component_action_claim
                   WHERE bot_number=? AND guild_id=? AND message_id=?""",
                (3, 100, 50),
            )).fetchone()
        self.assertEqual((row["status"], row["attempt_count"]), ("succeeded", 1))

    async def test_business_failure_preserves_button_and_allows_a_new_retry(self):
        self.boss.failures = 1
        message = self._message()
        failed = await self._dispatch(message=message)

        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_code, "ACTION_REJECTED")
        self.assertFalse(any(item.disabled for item in message.components[0].children))
        retried = await self._dispatch(request_id="request-0002", message=message)
        self.assertEqual(retried.status, "succeeded")
        self.assertEqual(len(self.boss.calls), 2)

    async def test_database_handler_failure_keeps_buttons_enabled_and_retries_once(self):
        self.boss.db_failures = 1
        message = self._message()

        failed = await self._dispatch(message=message)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_code, "ACTION_FAILED")
        self.assertFalse(any(item.disabled for item in message.components[0].children))
        self.assertEqual(message.channel.send.await_count, 0)

        retried = await self._dispatch(request_id="request-0002", message=message)
        self.assertEqual(retried.status, "succeeded")
        self.assertEqual(self.boss.calls, [("boss_cut", -42), ("boss_cut", -42)])
        self.assertEqual(message.channel.send.await_count, 1)
        self.assertTrue(all(item.disabled for item in message.components[0].children))

        async with db_module.get_db() as db:
            row = await (await db.execute(
                """SELECT status, attempt_count FROM component_action_claim
                   WHERE bot_number=? AND guild_id=? AND message_id=?""",
                (3, 100, 50),
            )).fetchone()
        self.assertEqual((row["status"], row["attempt_count"]), ("succeeded", 2))

    async def test_disable_failure_keeps_terminal_claim_and_duplicate_does_not_repeat_work(self):
        self.boss.disable_failures = 2
        message = self._message()

        first = await self._dispatch(message=message)
        self.assertEqual(first.status, "succeeded")
        self.assertEqual(message.channel.send.await_count, 1)
        self.assertFalse(any(item.disabled for item in message.components[0].children))

        duplicate = await self._dispatch(request_id="request-0002", custom_id=self.miss_id, message=message)
        self.assertEqual(duplicate.status, "already_processed")
        self.assertEqual(self.boss.calls, [("boss_cut", -42)])
        self.assertEqual(message.channel.send.await_count, 1)
        self.assertFalse(any(item.disabled for item in message.components[0].children))

        async with db_module.get_db() as db:
            row = await (await db.execute(
                """SELECT status FROM component_action_claim
                   WHERE bot_number=? AND guild_id=? AND message_id=?""",
                (3, 100, 50),
            )).fetchone()
        self.assertEqual(row["status"], "succeeded")

    async def test_same_component_identity_is_claimed_independently_by_each_bot(self):
        message3 = self._message()
        result3 = await self._dispatch(message=message3)

        boss1 = _ActionBoss()
        bot1 = _ActionBot(boss1, bot_number=1, user_id=111)
        dispatcher1 = ComponentActionDispatcher(bot1)
        message1 = self._message(channel_id=201, author_id=111)
        result1 = await dispatcher1.dispatch(
            request_id="request-bot1",
            guild_id=100,
            message_id=50,
            custom_id=self.cut_id,
            actor=self.actor,
            actor_type="web_guest",
            actor_ref="guest:session",
            is_owner=False,
            message=message1,
        )

        self.assertEqual(result3.status, "succeeded")
        self.assertEqual(result1.status, "succeeded")
        self.assertEqual(self.boss.calls, [("boss_cut", -42)])
        self.assertEqual(boss1.calls, [("boss_cut", -42)])
        async with db_module.get_db() as db:
            rows = await (await db.execute(
                """SELECT bot_number, status FROM component_action_claim
                   WHERE guild_id=? AND message_id=? ORDER BY bot_number""",
                (100, 50),
            )).fetchall()
        self.assertEqual(
            [(row["bot_number"], row["status"]) for row in rows],
            [(1, "succeeded"), (3, "succeeded")],
        )

    async def test_legacy_unregistered_and_wrong_message_targets_are_rejected_without_claims(self):
        legacy = await self._dispatch(custom_id="boss_cut:100:체르투바")
        unknown = await self._dispatch(custom_id="unknown:button")
        foreign = await self._dispatch(message=self._message(author_id=123))
        cross_guild = await self._dispatch(custom_id=boss_component_custom_id("cut", 101, "체르투바"))
        wrong_channel = await self._dispatch(message=self._message(channel_id=201))

        self.assertEqual(legacy.error_code, "READ_ONLY_LEGACY")
        self.assertEqual(unknown.error_code, "UNREGISTERED_COMPONENT")
        self.assertEqual(foreign.error_code, "MESSAGE_NOT_BOT_AUTHORED")
        self.assertEqual(cross_guild.error_code, "MESSAGE_TARGET_MISMATCH")
        self.assertEqual(wrong_channel.error_code, "MESSAGE_CHANNEL_MISMATCH")
        async with db_module.get_db() as db:
            count = (await (await db.execute("SELECT COUNT(*) FROM component_action_claim")).fetchone())[0]
        self.assertEqual(count, 0)

    async def test_restricted_registration_rejects_non_admin_before_any_mutation(self):
        registry = ComponentActionRegistry()
        registry.register(ComponentActionDefinition(
            key="restricted", custom_id_pattern=__import__("re").compile(r"restricted:(?P<target>[^:]+)"),
            allow_non_admin=False, handler_cog="Boss", handler_name="execute_registered_component",
            idempotency_key=lambda guild_id, message_id, _params: f"restricted:{guild_id}:{message_id}",
        ))
        message = _ActionMessage(
            message_id=50,
            components=[_Row(_Button("restricted:one"))],
        )
        result = await ComponentActionDispatcher(self.bot, registry).dispatch(
            request_id="request-0001", guild_id=100, message_id=50,
            custom_id="restricted:one", actor=self.actor, actor_type="web_guest",
            actor_ref="guest:session", is_owner=False, message=message,
        )
        self.assertEqual(result.error_code, "OWNER_ACTION_REQUIRED")
        self.assertEqual(self.boss.calls, [])

    async def test_discord_and_web_dispatches_broadcast_one_public_result_after_claiming_success(self):
        message = self._message()
        result = await self.dispatcher.dispatch(
            request_id="discord:50", guild_id=100, message_id=50,
            custom_id=self.cut_id, actor=SimpleNamespace(id=7, name="디스코드", display_name="디스코드"),
            actor_type="discord", actor_ref="discord:7", is_owner=False, message=message,
        )
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(message.channel.send.await_count, 1)
        # A duplicate click reconciles the disabled view but never repeats the
        # public announcement or business handler.
        duplicate = await self.dispatcher.dispatch(
            request_id="discord:51", guild_id=100, message_id=50,
            custom_id=self.miss_id, actor=self.actor,
            actor_type="web_guest", actor_ref="guest:session", is_owner=False, message=message,
        )
        self.assertEqual(duplicate.status, "already_processed")
        self.assertEqual(message.channel.send.await_count, 1)

    async def test_component_bridge_endpoint_authenticates_and_returns_safe_result_contract(self):
        message = self._message()
        message.channel.fetch_message = AsyncMock(return_value=message)
        guild = SimpleNamespace(id=100)
        self.bot.get_guild = lambda guild_id: guild if guild_id == 100 else None
        self.bot.get_channel = lambda channel_id: message.channel if channel_id == 200 else None
        bridge = WebBridge(self.bot, "b" * 32, Path(self.tmp.name) / "bridge.sock")
        payload = {
            "requestId": "request-0001", "actorRef": "guest:session", "nickname": "테스터",
            "actorType": "web_guest", "guildId": "100", "messageId": "50", "customId": self.cut_id,
        }
        body = json.dumps(payload).encode("utf-8")
        timestamp = str(int(time.time()))
        nonce = "c" * 16
        canonical = "\n".join((timestamp, nonce, "POST", "/internal/v1/component-actions", hashlib.sha256(body).hexdigest())).encode("utf-8")
        signature = hmac.new(b"b" * 32, canonical, hashlib.sha256).hexdigest()
        response = await bridge.component_actions(_PostRequest(
            "/internal/v1/component-actions", body,
            {"x-lc-timestamp": timestamp, "x-lc-nonce": nonce, "x-lc-signature": signature},
        ))
        parsed = json.loads(response.body)
        self.assertEqual(response.status, 200)
        self.assertEqual(parsed["status"], "succeeded")
        self.assertTrue(all(item["disabled"] for item in parsed["components"]))


if __name__ == "__main__":
    unittest.main()
