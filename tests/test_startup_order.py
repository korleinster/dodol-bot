import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main as bot_main
from src.cogs.boss import Boss
from src.cogs.weather import Weather


class _FakeBridge:
    def __init__(self, calls):
        self.calls = calls

    async def close(self):
        self.calls.append("bridge.close")


class _FakeBot:
    def __init__(self, bot_number=3):
        self.calls = []
        self.bot_number = bot_number
        self.user = "test-bot"
        self._ready_count = 0
        self._bridge_start_error_code = None
        self._bridge_start_error_reported = False
        self.scheduler_health = {
            "status": "starting",
            "bootstrapCompletedAt": None,
            "lastTickAt": None,
            "errorCode": None,
        }

    def event(self, callback):
        setattr(self, callback.__name__, callback)
        return callback

    async def login(self, token):
        self.calls.append("login")

    async def load_extension(self, name):
        # Boss cleanup/check and Weather loops may be constructed only after
        # discord.py has completed login() client initialization.
        self.calls.append(f"load:{name}")
        if name in {"src.cogs.boss", "src.cogs.weather"}:
            assert "login" in self.calls

    async def connect(self, *, reconnect):
        self.calls.append(f"connect:{reconnect}")

    async def close(self):
        self.calls.append("bot.close")


class StartupLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_login_precedes_cogs_bridge_and_connect(self):
        bot = _FakeBot()

        async def start_bridge(received):
            self.assertIs(received, bot)
            bot.calls.append("bridge.start")
            return _FakeBridge(bot.calls)

        with patch("src.web_bridge.start_web_bridge", side_effect=start_bridge):
            await bot_main.run_bot(3, "token", bot)

        self.assertEqual(bot.calls[0], "login")
        last_cog = max(
            index for index, call in enumerate(bot.calls)
            if call.startswith("load:")
        )
        self.assertLess(last_cog, bot.calls.index("bridge.start"))
        self.assertLess(bot.calls.index("bridge.start"), bot.calls.index("connect:True"))
        self.assertEqual(bot.calls[-2:], ["bridge.close", "bot.close"])

    async def test_bridge_failure_is_fail_open_for_all_bridge_bots_and_scheduler(self):
        for bot_number in range(1, 5):
            with self.subTest(bot_number=bot_number):
                bot = _FakeBot(bot_number)
                with patch(
                    "src.web_bridge.start_web_bridge",
                    new=AsyncMock(side_effect=RuntimeError("socket unavailable")),
                ):
                    await bot_main.run_bot(bot_number, "token", bot)

                self.assertEqual(bot._bridge_start_error_code, "BRIDGE_START_FAILED")
                self.assertEqual(bot.scheduler_health["status"], "starting")
                self.assertIn("connect:True", bot.calls)
                self.assertEqual(bot.calls[-1], "bot.close")

    async def test_fatal_bot_startup_is_not_swallowed(self):
        bot = _FakeBot()
        bot.connect = AsyncMock(side_effect=RuntimeError("fatal connect failure"))
        with (
            patch("src.web_bridge.start_web_bridge", new=AsyncMock(return_value=None)),
            self.assertRaisesRegex(RuntimeError, "fatal connect failure"),
        ):
            await bot_main.run_bot_safe(3, "token", bot)
        self.assertIn("bot.close", bot.calls)

    async def test_legacy_multi_bot_mode_can_isolate_one_fatal_bot(self):
        bot = _FakeBot()
        with patch(
            "main.run_bot",
            new=AsyncMock(side_effect=RuntimeError("one bot failed")),
        ):
            await bot_main.run_bot_safe(
                3,
                "token",
                bot,
                propagate=False,
            )

    async def test_bot_closes_even_when_bridge_cleanup_fails(self):
        bot = _FakeBot()
        bridge = _FakeBridge(bot.calls)
        bridge.close = AsyncMock(side_effect=RuntimeError("cleanup failed"))
        with (
            patch(
                "src.web_bridge.start_web_bridge",
                new=AsyncMock(return_value=bridge),
            ),
            self.assertRaisesRegex(RuntimeError, "cleanup failed"),
        ):
            await bot_main.run_bot(3, "token", bot)
        self.assertEqual(bot.calls[-1], "bot.close")

    async def test_gateway_disconnect_marks_runtime_unhealthy_and_reports_only_long_recovery(self):
        bot = _FakeBot()
        with patch("src.web_bridge.start_web_bridge", new=AsyncMock(return_value=None)):
            await bot_main.run_bot(3, "token", bot)

        with (
            patch.object(bot_main, "_notify_ready", new=AsyncMock()) as notify_ready,
            patch("src.utils.notify.alert", new=AsyncMock()) as alert,
        ):
            await bot.on_disconnect()
            self.assertEqual(bot.runtime_health["discord"], "stopped")
            bot._gateway_disconnect_started_at = time.monotonic() - 61
            await bot.on_resumed()

        self.assertEqual(bot.runtime_health["discord"], "ready")
        notify_ready.assert_not_awaited()  # a resume must not repeat startup messages
        alert.assert_awaited_once()
        self.assertIn("61초", alert.await_args.args[2])

    async def test_boss_cleanup_and_weather_hooks_wait_for_initialized_client(self):
        wait_until_ready = AsyncMock()
        bot = SimpleNamespace(wait_until_ready=wait_until_ready)
        boss = Boss.__new__(Boss)
        boss.bot = bot
        weather = Weather.__new__(Weather)
        weather.bot = bot

        await Boss.before_cleanup(boss)
        await Weather.before_daily_weather(weather)

        self.assertEqual(wait_until_ready.await_count, 2)


if __name__ == "__main__":
    unittest.main()
