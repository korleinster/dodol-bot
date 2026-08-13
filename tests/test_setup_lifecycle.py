import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from src import db as db_module
from src.cogs.setup import BindingConflict, BindingMove, Setup, _can_manage_guild


def _message(*, guild_id=200, channel_id=201, manager=True):
    channel = SimpleNamespace(id=channel_id, mention=f"<#{channel_id}>", send=AsyncMock())
    guild = SimpleNamespace(
        id=guild_id,
        voice_client=None,
        get_channel=Mock(return_value=None),
    )
    author = SimpleNamespace(
        bot=False,
        voice=None,
        guild_permissions=SimpleNamespace(
            administrator=False,
            manage_guild=manager,
        ),
    )
    return SimpleNamespace(
        author=author,
        guild=guild,
        channel=channel,
        content="",
    )


class SetupPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_path = db_module.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        db_module.DB_PATH = Path(self.tmp.name) / "bot.db"
        await db_module.init_db()
        self.bot = SimpleNamespace(
            bot_number=4,
            get_guild=Mock(return_value=None),
            get_cog=Mock(return_value=None),
        )
        self.cog = Setup(self.bot)

    async def asyncTearDown(self):
        db_module.DB_PATH = self.previous_path
        self.tmp.cleanup()

    def _execute(self, sql, params=()):
        connection = sqlite3.connect(db_module.DB_PATH)
        try:
            cursor = connection.execute(sql, params)
            connection.commit()
            return cursor.fetchall()
        finally:
            connection.close()

    def _seed_operational_data(self, guild_id: int):
        self._execute(
            "INSERT INTO guild_config (guild_id, bot_number, text_channel_id, voice_channel_id) VALUES (?,?,?,?)",
            (guild_id, 4, guild_id + 1, guild_id + 2),
        )
        self._execute(
            """INSERT INTO bosses
               (guild_id, bot_number, name, aliases, respawn_seconds)
               VALUES (?,?,?,?,?)""",
            (guild_id, 4, "테스트 보스", "[]", 3600),
        )
        self._execute(
            """INSERT INTO schedules
               (guild_id, bot_number, boss_name, content, scheduled_at)
               VALUES (?,?,?,?,?)""",
            (guild_id, 4, "테스트 보스", "테스트 보스", "2026-08-10T15:00:00"),
        )
        self._execute(
            """INSERT INTO contributions
               (guild_id, bot_number, user_id, username, boss_name)
               VALUES (?,?,?,?,?)""",
            (guild_id, 4, 1, "테스터", "테스트 보스"),
        )

    def test_management_permission_accepts_manage_guild_or_administrator(self):
        self.assertTrue(_can_manage_guild(SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_guild=True, administrator=False),
        )))
        self.assertTrue(_can_manage_guild(SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_guild=False, administrator=True),
        )))
        self.assertFalse(_can_manage_guild(SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_guild=False, administrator=False),
        )))

    async def test_settings_are_silent_outside_the_bound_channel(self):
        self._execute(
            "INSERT INTO guild_config (guild_id, bot_number, text_channel_id) VALUES (?,?,?)",
            (200, 4, 999),
        )
        message = _message(channel_id=201)
        self.cog._cmd_status = AsyncMock()

        await self.cog._dispatch(message, "설정")

        self.cog._cmd_status.assert_not_awaited()
        message.channel.send.assert_not_awaited()

    async def test_non_manager_cannot_summon_the_target_bot(self):
        message = _message(manager=False)
        self.cog.move_config = AsyncMock()

        await self.cog._dispatch(message, "소환 뚠뚠봇004")

        self.cog.move_config.assert_not_awaited()
        message.channel.send.assert_awaited_once()

    async def test_move_rebinds_operational_data_and_clears_old_channels(self):
        self._seed_operational_data(100)

        result = await self.cog.move_config(200, 201, 202)

        self.assertEqual(result.source_guild_id, 100)
        self.assertEqual(result.disconnected_guild_ids, (100,))
        old = self._execute(
            "SELECT text_channel_id, voice_channel_id FROM guild_config WHERE guild_id=100 AND bot_number=4",
        )[0]
        self.assertEqual(old, (None, None))
        target = self._execute(
            "SELECT text_channel_id, voice_channel_id FROM guild_config WHERE guild_id=200 AND bot_number=4",
        )[0]
        self.assertEqual(target, (201, 202))
        for table in ("bosses", "schedules", "contributions"):
            self.assertEqual(
                self._execute(f"SELECT COUNT(*) FROM {table} WHERE guild_id=100 AND bot_number=4")[0][0],
                0,
            )
        self.assertEqual(
            self._execute(
                "SELECT COUNT(*) FROM bosses WHERE guild_id=200 AND bot_number=4 AND name='테스트 보스'"
            )[0][0],
            1,
        )
        self.assertEqual(
            self._execute(
                "SELECT COUNT(*) FROM schedules WHERE guild_id=200 AND bot_number=4 AND boss_name='테스트 보스' AND notified=0"
            )[0][0],
            1,
        )
        self.assertEqual(
            self._execute(
                "SELECT COUNT(*) FROM schedules WHERE guild_id=200 AND bot_number=4 AND boss_name='테스트 보스' AND notified=1"
            )[0][0],
            1,
        )
        self.assertEqual(
            self._execute("SELECT COUNT(*) FROM contributions WHERE guild_id=200 AND bot_number=4")[0][0],
            1,
        )

    async def test_recovery_failure_rolls_back_data_move_and_destination_binding(self):
        self._seed_operational_data(100)
        before = {
            table: self._execute(
                f"SELECT * FROM {table} WHERE guild_id IN (100,200) AND bot_number=4 ORDER BY id"
            )
            for table in ("bosses", "schedules", "contributions")
        }
        before_config = self._execute(
            "SELECT * FROM guild_config WHERE guild_id IN (100,200) AND bot_number=4 ORDER BY guild_id"
        )

        with patch(
            "src.cogs.setup.reconcile_schedules_in_transaction",
            new=AsyncMock(side_effect=RuntimeError("recovery failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "recovery failed"):
                await self.cog.move_config(200, 201, 202)

        self.assertEqual(
            self._execute("SELECT text_channel_id, voice_channel_id FROM guild_config WHERE guild_id=100 AND bot_number=4")[0],
            (101, 102),
        )
        self.assertEqual(
            self._execute("SELECT COUNT(*) FROM guild_config WHERE guild_id=200 AND bot_number=4")[0][0],
            0,
        )
        for table in ("bosses", "schedules", "contributions"):
            self.assertEqual(
                self._execute(
                    f"SELECT * FROM {table} WHERE guild_id IN (100,200) AND bot_number=4 ORDER BY id"
                ),
                before[table],
            )
            self.assertGreater(
                self._execute(f"SELECT COUNT(*) FROM {table} WHERE guild_id=100 AND bot_number=4")[0][0],
                0,
            )
            self.assertEqual(
                self._execute(f"SELECT COUNT(*) FROM {table} WHERE guild_id=200 AND bot_number=4")[0][0],
                0,
            )
        self.assertEqual(
            self._execute(
                "SELECT * FROM guild_config WHERE guild_id IN (100,200) AND bot_number=4 ORDER BY guild_id"
            ),
            before_config,
        )

    async def test_post_commit_voice_failure_is_reported_as_degraded_completion(self):
        self._seed_operational_data(100)
        message = _message()
        self.cog._disconnect_voice = AsyncMock(return_value=False)

        await self.cog._cmd_summon(message, "뚠뚠봇004")

        self.assertEqual(
            self._execute("SELECT text_channel_id FROM guild_config WHERE guild_id=100 AND bot_number=4")[0][0],
            None,
        )
        self.assertEqual(
            self._execute("SELECT text_channel_id FROM guild_config WHERE guild_id=200 AND bot_number=4")[0][0],
            201,
        )
        self.assertEqual(
            self._execute("SELECT COUNT(*) FROM contributions WHERE guild_id=200 AND bot_number=4")[0][0],
            1,
        )
        sent_embed = message.channel.send.await_args.kwargs["embed"]
        self.assertIn("음성 확인 필요", sent_embed.title)
        self.assertTrue(any(field.name == "일정 복구" for field in sent_embed.fields))
        self.assertTrue(any(field.name == "확인 필요" for field in sent_embed.fields))

    async def test_target_voice_none_result_is_not_reported_as_full_success(self):
        message = _message()
        message.author.voice = SimpleNamespace(channel=SimpleNamespace(id=202))
        self.cog.move_config = AsyncMock(return_value=BindingMove(
            disconnected_guild_ids=(),
            source_guild_id=None,
            recovery={
                "expired": 0,
                "normalCreated": 0,
                "fixedCreated": 0,
                "duplicates": 0,
            },
        ))
        self.cog._disconnect_voice = AsyncMock(return_value=True)
        ensure_connected = AsyncMock(return_value=None)
        self.bot.get_cog = Mock(return_value=SimpleNamespace(
            ensure_connected=ensure_connected,
        ))

        await self.cog._cmd_summon(message, "뚠뚠봇004")

        sent_embed = message.channel.send.await_args.kwargs["embed"]
        self.assertIn("음성 확인 필요", sent_embed.title)
        self.assertTrue(any(field.name == "확인 필요" for field in sent_embed.fields))
        ensure_connected.assert_awaited_once_with(message.guild)

    async def test_move_conflict_rolls_back_without_overwriting_either_server(self):
        self._seed_operational_data(100)
        self._execute(
            "INSERT INTO guild_config (guild_id, bot_number) VALUES (?,?)",
            (200, 4),
        )
        self._execute(
            """INSERT INTO bosses
               (guild_id, bot_number, name, aliases, respawn_seconds)
               VALUES (?,?,?,?,?)""",
            (200, 4, "대상 보스", "[]", 3600),
        )

        with self.assertRaises(BindingConflict):
            await self.cog.move_config(200, 201, None)

        self.assertEqual(
            self._execute("SELECT text_channel_id FROM guild_config WHERE guild_id=100 AND bot_number=4")[0][0],
            101,
        )
        self.assertEqual(
            self._execute("SELECT COUNT(*) FROM bosses WHERE guild_id=100 AND bot_number=4")[0][0],
            1,
        )
        self.assertEqual(
            self._execute("SELECT COUNT(*) FROM bosses WHERE guild_id=200 AND bot_number=4")[0][0],
            1,
        )

    async def test_full_leave_clears_channels_but_keeps_all_gameplay_data(self):
        self._seed_operational_data(200)
        message = _message()
        message.guild.voice_client = SimpleNamespace(disconnect=AsyncMock())

        await self.cog._cmd_leave(message, "전체나가기")

        config = self._execute(
            "SELECT text_channel_id, voice_channel_id FROM guild_config WHERE guild_id=200 AND bot_number=4",
        )[0]
        self.assertEqual(config, (None, None))
        for table in ("bosses", "schedules", "contributions"):
            self.assertEqual(
                self._execute(f"SELECT COUNT(*) FROM {table} WHERE guild_id=200 AND bot_number=4")[0][0],
                1,
            )
        message.channel.send.assert_awaited_once()
        message.guild.voice_client.disconnect.assert_awaited_once_with(force=True)


if __name__ == "__main__":
    unittest.main()
