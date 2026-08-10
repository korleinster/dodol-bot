import sqlite3
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

if "gtts" not in sys.modules:
    try:
        __import__("gtts")
    except ModuleNotFoundError:
        gtts_stub = types.ModuleType("gtts")
        gtts_stub.gTTS = object
        sys.modules["gtts"] = gtts_stub

from src import db as db_module
from src.cogs import boss as boss_module
from src.cogs.boss import Boss, format_schedule_tts, scheduler_delivery_retry_delay
from src.web_bridge import WebBridge, _scheduler_health_payload


def _bare_boss(bot_number=3):
    cog = Boss.__new__(Boss)
    cog.bot = SimpleNamespace(
        bot_number=bot_number,
        scheduler_health={
            "status": "starting",
            "bootstrapCompletedAt": None,
            "lastTickAt": None,
            "errorCode": None,
        },
    )
    cog.bn = bot_number
    cog._scheduler_error_key = None
    cog._scheduler_error_reported_at = 0.0
    cog._scheduler_bootstrapped = False
    return cog


class ManualBossTtsPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_z_variants_are_text_only_and_never_resolve_tts_cog(self):
        for command, include_fixed in (
            ("z", False),
            ("Z", False),
            ("z+", True),
            ("Z+", True),
        ):
            with self.subTest(command=command):
                cog = _bare_boss()
                cog._cmd_botam = AsyncMock()
                cog.bot.get_cog = Mock(side_effect=AssertionError("manual Z must not resolve TTS"))
                message = SimpleNamespace(guild=SimpleNamespace(id=100))

                await cog._dispatch(message, command)

                cog._cmd_botam.assert_awaited_once_with(
                    message,
                    include_fixed=include_fixed,
                )
                cog.bot.get_cog.assert_not_called()


class ScheduleTtsTextTest(unittest.TestCase):
    def test_zero_misses_are_omitted(self):
        self.assertEqual(
            format_schedule_tts("발록", 0, "출현 중"),
            "발록 출현 중",
        )

    def test_miss_count_is_spoken_once(self):
        for miss_count in (1, 30):
            with self.subTest(miss_count=miss_count):
                spoken = format_schedule_tts("발록", miss_count, "출현 중")
                self.assertEqual(
                    spoken,
                    f"발록 미입력 {miss_count}회 출현 중",
                )
                self.assertEqual(spoken.count("미입력"), 1)


class SchedulerRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_path = db_module.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        db_module.DB_PATH = Path(self.tmp.name) / "bot.db"
        await db_module.init_db()

    async def asyncTearDown(self):
        db_module.DB_PATH = self.previous_path
        self.tmp.cleanup()

    def _rows(self, query, params=()):
        connection = sqlite3.connect(db_module.DB_PATH)
        connection.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in connection.execute(query, params)]
        finally:
            connection.close()

    async def test_recovery_is_quiet_scoped_and_idempotent(self):
        recovery_at = datetime(2026, 7, 27, 10, 0, 0, 500000)
        connection = sqlite3.connect(db_module.DB_PATH)
        connection.executemany(
            "INSERT INTO guild_config (guild_id, bot_number, text_channel_id) VALUES (?,?,?)",
            [(100, 3, 200), (101, 1, 201)],
        )
        connection.executemany(
            """INSERT INTO bosses
               (guild_id, bot_number, name, respawn_seconds, fixed, fixed_days, fixed_time)
               VALUES (?,?,?,?,?,?,?)""",
            [
                (100, 3, "일반", 3600, 0, None, None),
                (100, 3, "정각", 3600, 0, None, None),
                (100, 3, "미등록", 3600, 0, None, None),
                (100, 3, "고정", None, 1, "0", "12:00"),
                (101, 1, "다른봇", 3600, 0, None, None),
            ],
        )
        connection.executemany(
            """INSERT INTO schedules
               (guild_id, bot_number, boss_name, content, scheduled_at, notified)
               VALUES (?,?,?,?,?,?)""",
            [
                (100, 3, "일반", "일반", "2026-07-27T08:00:00", 0),
                (100, 3, None, "임의 예약", "2026-07-27T09:00:00", 0),
                (100, 3, "고정", "고정", "2026-07-27T09:30:00", 0),
                (100, 3, "정각", "정각", "2026-07-27T10:00:00", 0),
                (100, 3, "정각", "정각 중복", "2026-07-27T11:00:00", 0),
                (101, 1, "다른봇", "다른봇", "2026-07-27T09:00:00", 0),
            ],
        )
        connection.commit()
        connection.close()

        cog = _bare_boss()
        first = await cog._recover_schedules(recovery_at)
        snapshot = self._rows(
            """SELECT guild_id, bot_number, boss_name, content, scheduled_at,
                      miss_count, warned_5min, warned_1min, notified
               FROM schedules ORDER BY bot_number, guild_id, boss_name, scheduled_at"""
        )
        second = await cog._recover_schedules(recovery_at)

        self.assertEqual(first["normalCreated"], 1)
        self.assertEqual(first["fixedCreated"], 1)
        self.assertEqual(first["duplicates"], 1)
        self.assertEqual(second, {
            "expired": 0,
            "normalCreated": 0,
            "fixedCreated": 0,
            "duplicates": 0,
        })
        self.assertEqual(snapshot, self._rows(
            """SELECT guild_id, bot_number, boss_name, content, scheduled_at,
                      miss_count, warned_5min, warned_1min, notified
               FROM schedules ORDER BY bot_number, guild_id, boss_name, scheduled_at"""
        ))

        normal_pending = self._rows(
            """SELECT scheduled_at, miss_count FROM schedules
               WHERE bot_number=3 AND boss_name='일반' AND notified=0"""
        )
        self.assertEqual(normal_pending, [{
            "scheduled_at": "2026-07-27T11:00:00",
            "miss_count": 3,
        }])
        fixed_pending = self._rows(
            """SELECT scheduled_at FROM schedules
               WHERE bot_number=3 AND boss_name='고정' AND notified=0"""
        )
        self.assertEqual(fixed_pending, [{"scheduled_at": "2026-07-27T12:00:00"}])
        exact_pending = self._rows(
            """SELECT content, scheduled_at FROM schedules
               WHERE bot_number=3 AND boss_name='정각' AND notified=0"""
        )
        self.assertEqual(exact_pending, [{
            "content": "정각",
            "scheduled_at": "2026-07-27T10:00:00",
        }])
        self.assertEqual(self._rows(
            """SELECT content FROM schedules
               WHERE bot_number=3 AND boss_name='정각'
                 AND scheduled_at='2026-07-27T11:00:00'"""
        ), [])
        custom = self._rows(
            """SELECT notified FROM schedules
               WHERE bot_number=3 AND boss_name IS NULL"""
        )
        self.assertEqual(custom, [{"notified": 1}])
        other_bot = self._rows(
            """SELECT notified FROM schedules
               WHERE bot_number=1 AND boss_name='다른봇'"""
        )
        self.assertEqual(other_bot, [{"notified": 0}])
        self.assertEqual(self._rows(
            """SELECT * FROM schedules
               WHERE bot_number=3 AND boss_name='미등록'"""
        ), [])

    async def test_scheduler_errors_are_deduplicated_and_health_recovers(self):
        cog = _bare_boss()
        with patch("src.utils.notify.alert", new=AsyncMock()) as alert:
            await cog._mark_scheduler_failed(
                "SCHEDULER_TICK_FAILED",
                RuntimeError("one"),
                context="실행 실패",
            )
            await cog._mark_scheduler_failed(
                "SCHEDULER_TICK_FAILED",
                ValueError("two"),
                context="실행 실패",
            )
            self.assertEqual(alert.await_count, 1)
            self.assertEqual(cog.bot.scheduler_health["status"], "failed")
            self.assertEqual(
                cog.bot.scheduler_health["errorCode"],
                "SCHEDULER_TICK_FAILED",
            )

            cog._mark_scheduler_ready(bootstrap=True)
            self.assertEqual(cog.bot.scheduler_health["status"], "starting")
            self.assertIsNotNone(
                cog.bot.scheduler_health["bootstrapCompletedAt"],
            )
            cog._mark_scheduler_ready()
            self.assertEqual(cog.bot.scheduler_health["status"], "ready")
            self.assertIsNone(cog.bot.scheduler_health["errorCode"])
            self.assertIsNotNone(cog.bot.scheduler_health["lastTickAt"])

            await cog._mark_scheduler_failed(
                "SCHEDULER_TICK_FAILED",
                RuntimeError("new incident"),
                context="실행 실패",
            )
            self.assertEqual(alert.await_count, 2)

    async def test_bootstrap_failure_retries_without_stopping_loop(self):
        cog = _bare_boss()
        stats = {
            "expired": 0,
            "normalCreated": 0,
            "fixedCreated": 0,
            "duplicates": 0,
        }
        cog._recover_schedules = AsyncMock(
            side_effect=[RuntimeError("locked"), stats],
        )
        with patch("src.utils.notify.alert", new=AsyncMock()) as alert:
            self.assertFalse(await cog._bootstrap_scheduler())
            self.assertFalse(cog._scheduler_bootstrapped)
            self.assertEqual(
                cog.bot.scheduler_health["errorCode"],
                "SCHEDULER_BOOTSTRAP_FAILED",
            )

            self.assertTrue(await cog._bootstrap_scheduler())
            self.assertTrue(cog._scheduler_bootstrapped)
            self.assertEqual(cog.bot.scheduler_health["status"], "starting")
            self.assertEqual(alert.await_count, 1)

    async def test_monitoring_failure_does_not_break_scheduler_error_path(self):
        cog = _bare_boss()
        with patch(
            "src.utils.notify.alert",
            new=AsyncMock(side_effect=RuntimeError("monitor unavailable")),
        ):
            await cog._mark_scheduler_failed(
                "SCHEDULER_TICK_FAILED",
                RuntimeError("tick failed"),
                context="실행 실패",
            )
        self.assertEqual(cog.bot.scheduler_health["status"], "failed")
        self.assertEqual(
            cog.bot.scheduler_health["errorCode"],
            "SCHEDULER_TICK_FAILED",
        )

    async def test_tts_failure_cannot_repeat_a_claimed_notification(self):
        scheduled_at = datetime(2026, 7, 27, 10, 0, 0)
        connection = sqlite3.connect(db_module.DB_PATH)
        connection.execute(
            "INSERT INTO guild_config (guild_id, bot_number, text_channel_id) VALUES (?,?,?)",
            (100, 3, 200),
        )
        connection.execute(
            """INSERT INTO schedules
               (guild_id, bot_number, content, scheduled_at, miss_count)
               VALUES (?,?,?,?,?)""",
            (100, 3, "임의 예약", scheduled_at.isoformat(), 30),
        )
        connection.commit()
        connection.close()

        channel = SimpleNamespace(
            guild=SimpleNamespace(id=100),
            send=AsyncMock(),
        )
        tts = SimpleNamespace(
            speak=AsyncMock(side_effect=RuntimeError("tts failed")),
        )
        cog = _bare_boss()
        cog.bot.get_channel = lambda channel_id: channel
        cog.bot.get_cog = lambda name: tts if name == "TTS" else None

        with patch.object(boss_module, "now", return_value=scheduled_at):
            with self.assertRaisesRegex(RuntimeError, "tts failed"):
                await cog._check_schedules_inner()
            await cog._check_schedules_inner()

        self.assertEqual(channel.send.await_count, 1)
        self.assertEqual(tts.speak.await_count, 1)
        self.assertEqual(
            tts.speak.await_args.args[1],
            "임의 예약 미입력 30회 출현 중",
        )
        self.assertEqual(tts.speak.await_args.args[1].count("미입력"), 1)
        self.assertEqual(self._rows(
            "SELECT notified, warned_5min, warned_1min FROM schedules"
        ), [{
            "notified": 1,
            "warned_5min": 1,
            "warned_1min": 1,
        }])

    async def test_discord_5xx_releases_final_claim_then_retries_once_due(self):
        scheduled_at = datetime(2026, 7, 27, 10, 0, 0)
        connection = sqlite3.connect(db_module.DB_PATH)
        connection.execute(
            "INSERT INTO guild_config (guild_id, bot_number, text_channel_id) VALUES (?,?,?)",
            (100, 3, 200),
        )
        connection.execute(
            """INSERT INTO schedules
               (guild_id, bot_number, content, scheduled_at)
               VALUES (?,?,?,?)""",
            (100, 3, "임의 예약", scheduled_at.isoformat()),
        )
        connection.commit()
        connection.close()

        discord_5xx = discord.DiscordServerError(
            SimpleNamespace(status=503, reason="unavailable"),
            "temporary Discord failure",
        )
        channel = SimpleNamespace(
            guild=SimpleNamespace(id=100),
            send=AsyncMock(side_effect=[discord_5xx, None]),
        )
        cog = _bare_boss()
        cog.bot.get_channel = lambda channel_id: channel
        cog.bot.get_cog = lambda name: None

        with patch.object(boss_module, "now", return_value=scheduled_at):
            with self.assertRaises(discord.DiscordServerError):
                await cog._check_schedules_inner()
            # The durable delay prevents a per-second retry storm.
            await cog._check_schedules_inner()

        failed = self._rows(
            """SELECT notified, warned_5min, warned_1min, delivery_retry_count,
                      delivery_error_code, delivery_retry_after
               FROM schedules"""
        )
        self.assertEqual(failed[0]["notified"], 0)
        self.assertEqual(failed[0]["warned_5min"], 1)
        self.assertEqual(failed[0]["warned_1min"], 1)
        self.assertEqual(failed[0]["delivery_retry_count"], 1)
        self.assertEqual(failed[0]["delivery_error_code"], "DISCORD_SERVER_ERROR")
        self.assertEqual(
            failed[0]["delivery_retry_after"],
            (scheduled_at + timedelta(seconds=5)).isoformat(),
        )
        self.assertEqual(channel.send.await_count, 1)

        with patch.object(
            boss_module,
            "now",
            return_value=scheduled_at + timedelta(seconds=5),
        ):
            await cog._check_schedules_inner()

        self.assertEqual(channel.send.await_count, 2)
        self.assertEqual(self._rows(
            """SELECT notified, delivery_retry_count, delivery_error_code,
                      delivery_retry_after FROM schedules"""
        ), [{
            "notified": 1,
            "delivery_retry_count": 0,
            "delivery_error_code": None,
            "delivery_retry_after": None,
        }])


class SchedulerDeliveryPolicyTest(unittest.TestCase):
    def test_retry_delay_is_bounded_and_ordered(self):
        self.assertEqual(
            [scheduler_delivery_retry_delay(attempt) for attempt in range(1, 7)],
            [5, 15, 30, 60, 120, 120],
        )


class SchedulerDeliveryMigrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_retry_columns_are_added_to_existing_schedule_rows(self):
        previous = db_module.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bot.db"
            connection = sqlite3.connect(path)
            connection.executescript("""
                CREATE TABLE schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    bot_number INTEGER NOT NULL,
                    boss_name TEXT,
                    content TEXT NOT NULL,
                    scheduled_at TEXT NOT NULL,
                    is_fixed INTEGER NOT NULL DEFAULT 0,
                    miss_count INTEGER NOT NULL DEFAULT 0,
                    warned_5min INTEGER NOT NULL DEFAULT 0,
                    warned_1min INTEGER NOT NULL DEFAULT 0,
                    notified INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                );
                INSERT INTO schedules
                    (guild_id, bot_number, content, scheduled_at)
                    VALUES (100, 3, 'legacy reservation', '2026-07-27T10:00:00');
            """)
            connection.commit()
            connection.close()
            db_module.DB_PATH = path
            try:
                await db_module.init_db()
                connection = sqlite3.connect(path)
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(schedules)")
                }
                legacy = connection.execute(
                    """SELECT delivery_retry_after, delivery_retry_count,
                              delivery_error_code FROM schedules""",
                ).fetchone()
                connection.close()
                self.assertTrue({
                    "delivery_retry_after",
                    "delivery_retry_count",
                    "delivery_error_code",
                }.issubset(columns))
                self.assertEqual(legacy, (None, 0, None))
            finally:
                db_module.DB_PATH = previous


class SchedulerHealthPayloadTest(unittest.TestCase):
    def test_payload_is_safe_and_fail_closed(self):
        bot = SimpleNamespace(scheduler_health={
            "status": "failed",
            "bootstrapCompletedAt": 100,
            "lastTickAt": 200,
            "errorCode": "SCHEDULER_TICK_FAILED",
            "details": "secret traceback",
        })
        self.assertEqual(_scheduler_health_payload(bot), {
            "status": "failed",
            "bootstrapCompletedAt": 100,
            "lastTickAt": 200,
            "errorCode": "SCHEDULER_TICK_FAILED",
        })
        self.assertEqual(_scheduler_health_payload(SimpleNamespace()), {
            "status": "starting",
            "bootstrapCompletedAt": None,
            "lastTickAt": None,
            "errorCode": None,
        })
        self.assertEqual(
            _scheduler_health_payload(SimpleNamespace(scheduler_health={
                "status": "ready",
                "bootstrapCompletedAt": None,
                "lastTickAt": None,
                "errorCode": None,
            }))["errorCode"],
            "SCHEDULER_STATUS_UNKNOWN",
        )
        self.assertEqual(
            _scheduler_health_payload(SimpleNamespace(scheduler_health={
                "status": "starting",
                "bootstrapCompletedAt": 100,
                "lastTickAt": 200,
                "errorCode": "SHOULD_NOT_LEAK",
            })),
            {
                "status": "starting",
                "bootstrapCompletedAt": 100,
                "lastTickAt": None,
                "errorCode": None,
            },
        )


class BridgeTargetSchedulerHealthTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_path = db_module.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        db_module.DB_PATH = Path(self.tmp.name) / "bot.db"
        await db_module.init_db()
        connection = sqlite3.connect(db_module.DB_PATH)
        connection.execute(
            """INSERT INTO guild_config
               (guild_id, bot_number, text_channel_id, voice_channel_id)
               VALUES (?,?,?,?)""",
            (100, 3, 200, 300),
        )
        connection.commit()
        connection.close()

    async def asyncTearDown(self):
        db_module.DB_PATH = self.previous_path
        self.tmp.cleanup()

    async def test_target_contract_includes_scheduler_health(self):
        bot = SimpleNamespace(
            bot_number=3,
            scheduler_health={
                "status": "ready",
                "bootstrapCompletedAt": 100,
                "lastTickAt": 200,
                "errorCode": None,
            },
            get_guild=lambda guild_id: SimpleNamespace(name="테스트 길드"),
        )
        bridge = WebBridge(bot, "b" * 32, Path(self.tmp.name) / "bridge.sock")
        bridge._authenticate = AsyncMock(return_value=b"")

        response = await bridge.targets(SimpleNamespace())
        payload = json.loads(response.text)

        self.assertEqual(payload["targets"][0]["scheduler"], {
            "status": "ready",
            "bootstrapCompletedAt": 100,
            "lastTickAt": 200,
            "errorCode": None,
        })


if __name__ == "__main__":
    unittest.main()
