import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiosqlite

from src import db as db_module
from src.cogs import boss as boss_module
from src.cogs.boss import Boss


class ScheduleResetTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_path = db_module.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        db_module.DB_PATH = Path(self.tmp.name) / "bot.db"
        await db_module.init_db()
        self.reference = datetime(2026, 10, 7, 5, 41, 18)
        self.cog = Boss.__new__(Boss)
        self.cog.bn = 4
        self.cog.bot = SimpleNamespace(
            bot_number=4,
            get_channel=Mock(return_value=None),
            get_cog=Mock(return_value=None),
        )
        self.message = SimpleNamespace(
            guild=SimpleNamespace(id=100),
            channel=SimpleNamespace(send=AsyncMock()),
        )
        with sqlite3.connect(db_module.DB_PATH) as db:
            for guild_id, bot_number in ((100, 4), (101, 4), (100, 3)):
                db.execute(
                    "INSERT INTO guild_config (guild_id, bot_number, text_channel_id) "
                    "VALUES (?,?,200)",
                    (guild_id, bot_number),
                )
                db.execute(
                    "INSERT INTO bosses (guild_id, bot_number, name, respawn_seconds) "
                    "VALUES (?,?,'Normal',3600)",
                    (guild_id, bot_number),
                )
                db.execute(
                    "INSERT INTO schedules "
                    "(guild_id, bot_number, boss_name, content, scheduled_at, miss_count, notified) "
                    "VALUES (?,?,'Normal','Normal',?,7,1)",
                    (guild_id, bot_number, (self.reference - timedelta(minutes=15)).isoformat()),
                )
                if (guild_id, bot_number) != (100, 4):
                    db.execute(
                        "INSERT INTO schedules "
                        "(guild_id, bot_number, boss_name, content, scheduled_at) "
                        "VALUES (?,?,'Normal','Normal',?)",
                        (guild_id, bot_number, (self.reference + timedelta(hours=1)).isoformat()),
                    )
                db.execute(
                    "INSERT INTO contributions "
                    "(guild_id, bot_number, user_id, username, boss_name) "
                    "VALUES (?,?,300,'TestUser','Normal')",
                    (guild_id, bot_number),
                )
            db.execute(
                "INSERT INTO bosses (guild_id, bot_number, name, fixed, fixed_days, fixed_time) "
                "VALUES (100,4,'Fixed',1,'2','12:00')",
            )
            db.execute(
                "INSERT INTO schedules "
                "(guild_id, bot_number, boss_name, content, scheduled_at, is_fixed) "
                "VALUES (100,4,'Fixed','Fixed','2026-10-07T12:00:00',1)",
            )

    async def asyncTearDown(self):
        db_module.DB_PATH = self.previous_path
        self.tmp.cleanup()

    def _rows(self, query, params=()):
        with sqlite3.connect(db_module.DB_PATH) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(query, params)]

    def _normal_rows(self):
        return self._rows(
            "SELECT boss_name, scheduled_at, miss_count, notified FROM schedules "
            "WHERE guild_id=100 AND bot_number=4 AND is_fixed=0 ORDER BY id",
        )

    async def _tick(self):
        with patch.object(boss_module, "now", return_value=self.reference):
            await self.cog._check_schedules_inner()

    async def _tick_with_concurrent_write(self, action):
        original_execute = aiosqlite.Connection.execute
        interrupted = False

        def execute(db, sql, parameters=()):
            nonlocal interrupted
            if (
                not interrupted
                and sql.lstrip().startswith("INSERT INTO schedules")
                and 100 in parameters
                and 4 in parameters
                and "Normal" in parameters
            ):
                interrupted = True

                async def insert_after_action():
                    # The scheduler has already read history and calculated its
                    # next spawn. Commit the competing action just before INSERT.
                    await action()
                    return await original_execute(db, sql, parameters)

                return insert_after_action()
            return original_execute(db, sql, parameters)

        with patch.object(aiosqlite.Connection, "execute", new=execute):
            await self._tick()
        self.assertTrue(interrupted, "the test must reach the auto-miss write")

    async def test_reset_during_auto_miss_insert_does_not_restore_history(self):
        preserved = self._rows(
            "SELECT * FROM schedules WHERE guild_id!=100 OR bot_number!=4 OR is_fixed=1 ORDER BY id",
        )

        async def reset():
            await self.cog._cmd_botam_reset(self.message)
            self.assertEqual(self._normal_rows(), [])

        await self._tick_with_concurrent_write(reset)
        self.assertEqual(self._normal_rows(), [])
        await self._tick()
        await self.cog._recover_schedules(self.reference)
        self.assertEqual(self._normal_rows(), [])
        self.assertEqual(preserved, self._rows(
            "SELECT * FROM schedules WHERE guild_id!=100 OR bot_number!=4 OR is_fixed=1 ORDER BY id",
        ))
        self.assertEqual(self._rows(
            "SELECT guild_id, bot_number FROM contributions ORDER BY guild_id, bot_number",
        ), [{"guild_id": 100, "bot_number": 3}, {"guild_id": 101, "bot_number": 4}])

    async def test_reset_deletes_pending_history_and_custom_but_keeps_fixed(self):
        with sqlite3.connect(db_module.DB_PATH) as db:
            db.executemany(
                "INSERT INTO schedules "
                "(guild_id, bot_number, boss_name, content, scheduled_at) "
                "VALUES (100,4,?,?,?)",
                [
                    ("Normal", "Normal", "2026-10-07T06:26:18"),
                    (None, "Custom", "2026-10-07T06:30:00"),
                ],
            )
        await self.cog._cmd_botam_reset(self.message)
        await self._tick()
        self.assertEqual(self._normal_rows(), [])
        self.message.channel.send.reset_mock()
        await self.cog._cmd_botam(self.message, include_fixed=False)
        self.message.channel.send.assert_awaited_once_with("예약된 일정이 없습니다.")
        self.message.channel.send.reset_mock()
        await self.cog._cmd_botam(self.message, include_fixed=True)
        embed = self.message.channel.send.await_args.kwargs["embed"]
        self.assertIn("Fixed", embed.description)
        self.assertNotIn("Normal", embed.description)
        self.assertNotIn("Custom", embed.description)

    async def test_reset_after_auto_miss_commit_stays_empty(self):
        await self._tick()
        self.assertEqual(len(self._normal_rows()), 2)
        await self.cog._cmd_botam_reset(self.message)
        await self._tick()
        self.assertEqual(self._normal_rows(), [])

    async def test_concurrent_manual_reservation_is_not_duplicated(self):
        async def manual_reservation():
            async with db_module.get_db() as db:
                await db.execute(
                    "INSERT INTO schedules "
                    "(guild_id, bot_number, boss_name, content, scheduled_at) "
                    "VALUES (100,4,'Normal','Manual','2026-10-07T07:00:00')",
                )
                await db.commit()

        await self._tick_with_concurrent_write(manual_reservation)
        pending = [row for row in self._normal_rows() if not row["notified"]]
        self.assertEqual(pending, [{
            "boss_name": "Normal",
            "scheduled_at": "2026-10-07T07:00:00",
            "miss_count": 0,
            "notified": 0,
        }])

    async def test_auto_miss_without_reset_preserves_timing_and_count(self):
        await self._tick()
        await self._tick()
        pending = [row for row in self._normal_rows() if not row["notified"]]
        self.assertEqual(pending, [{
            "boss_name": "Normal",
            "scheduled_at": "2026-10-07T06:26:18",
            "miss_count": 8,
            "notified": 0,
        }])
