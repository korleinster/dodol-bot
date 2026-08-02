import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

if "gtts" not in sys.modules:
    try:
        __import__("gtts")
    except ModuleNotFoundError:
        gtts_stub = types.ModuleType("gtts")
        gtts_stub.gTTS = object
        sys.modules["gtts"] = gtts_stub

import discord

from src.cogs.boss import Boss
from src.component_actions import boss_component_custom_id


class _Response:
    def __init__(self, *, done=False):
        self.done = done
        self.defer = AsyncMock()

    def is_done(self):
        return self.done


class BossComponentInteractionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = SimpleNamespace()
        # Avoid starting scheduler loops: this listener only needs the bot.
        self.cog = object.__new__(Boss)
        self.cog.bot = self.bot
        self.cog.bn = 3
        self.custom_id = boss_component_custom_id("cut", 100, "체르투바")

    def _interaction(self, *, response_done=False):
        response = _Response(done=response_done)
        return SimpleNamespace(
            type=discord.InteractionType.component,
            data={"custom_id": self.custom_id},
            guild=SimpleNamespace(id=100),
            message=SimpleNamespace(id=50),
            id=777,
            user=SimpleNamespace(id=7, guild_permissions=SimpleNamespace(administrator=False)),
            permissions=SimpleNamespace(administrator=False),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def _dispatch(self, status, error_message=None, *, response_done=False):
        interaction = self._interaction(response_done=response_done)
        dispatcher = SimpleNamespace(
            dispatch=AsyncMock(return_value=SimpleNamespace(
                status=status,
                succeeded=status == "succeeded",
                error_message=error_message,
            )),
        )
        with patch("src.cogs.boss.ComponentActionDispatcher", return_value=dispatcher) as dispatcher_class:
            await self.cog.on_interaction(interaction)
        return interaction, dispatcher, dispatcher_class

    async def test_success_acknowledges_silently_without_a_followup(self):
        interaction, dispatcher, dispatcher_class = await self._dispatch("succeeded")

        interaction.response.defer.assert_awaited_once_with(thinking=False)
        interaction.followup.send.assert_not_awaited()
        dispatcher.dispatch.assert_awaited_once()
        dispatcher_class.assert_called_once_with(self.bot)

    async def test_already_processed_keeps_one_ephemeral_notice(self):
        interaction, dispatcher, _ = await self._dispatch("already_processed")

        interaction.response.defer.assert_awaited_once_with(thinking=False)
        interaction.followup.send.assert_awaited_once_with(
            "이미 처리된 보스 알림입니다.", ephemeral=True,
        )
        dispatcher.dispatch.assert_awaited_once()

    async def test_failure_keeps_one_ephemeral_notice(self):
        interaction, dispatcher, _ = await self._dispatch(
            "failed", "처리할 권한이 없습니다.",
        )

        interaction.response.defer.assert_awaited_once_with(thinking=False)
        interaction.followup.send.assert_awaited_once_with(
            "처리할 권한이 없습니다.", ephemeral=True,
        )
        dispatcher.dispatch.assert_awaited_once()

    async def test_completed_initial_response_is_not_deferred_again(self):
        interaction, dispatcher, _ = await self._dispatch("succeeded", response_done=True)

        interaction.response.defer.assert_not_awaited()
        interaction.followup.send.assert_not_awaited()
        dispatcher.dispatch.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
