import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from src.cogs import tts as tts_module
from src.cogs.tts import TTS
from src.runtime_health import (
    runtime_health_payload,
)


class _VoiceChannel:
    def __init__(self, channel_id, client=None, error=None):
        self.id = channel_id
        self.name = "테스트 음성"
        self.client = client
        self.error = error
        self.connect = AsyncMock(side_effect=self._connect)

    async def _connect(self, **_kwargs):
        if self.error:
            raise self.error
        return self.client


class _VoiceClient:
    def __init__(self, channel_id, connected=True):
        self.channel = SimpleNamespace(id=channel_id)
        self.connected = connected
        self.disconnect = AsyncMock()

    def is_connected(self):
        return self.connected


class _Guild:
    def __init__(self, channel, voice_client=None):
        self.id = 10
        self._channel = channel
        self.voice_client = voice_client

    def get_channel(self, channel_id):
        return self._channel if channel_id == self._channel.id else None


class VoiceLivenessTest(unittest.IsolatedAsyncioTestCase):
    def _cog(self):
        return TTS(SimpleNamespace(bot_number=3))

    async def test_prior_live_voice_disconnect_with_none_client_opens_and_closes_recovery_incident(self):
        cog = self._cog()
        cog._voice_runtime_ready = Mock(return_value=True)
        cog.get_voice_channel = AsyncMock(return_value=77)
        cog._voice_connected_guilds.add(10)  # actual prior liveness observation
        replacement = _VoiceClient(77, connected=True)
        channel = _VoiceChannel(77, client=replacement)
        guild = _Guild(channel, voice_client=None)  # discord.py cleared it on disconnect
        alerts = Mock()
        cog._schedule_voice_alert = alerts

        with patch.object(tts_module.discord, "VoiceChannel", _VoiceChannel):
            result = await cog.ensure_connected(guild)

        self.assertIs(result, replacement)
        channel.connect.assert_awaited_once_with(timeout=15.0, reconnect=False)
        self.assertEqual(alerts.call_count, 2)  # disconnect + confirmed recovery
        self.assertNotIn(guild.id, cog._voice_incidents)
        self.assertEqual(runtime_health_payload(cog.bot)["voice"], {
            "configured": True,
            "connected": True,
            "state": "connected",
            "lastErrorCode": None,
            "nextRetryAt": None,
        })

    async def test_stale_client_is_force_cleaned_and_exhaustion_is_unhealthy(self):
        cog = self._cog()
        cog._voice_runtime_ready = Mock(return_value=True)
        cog.get_voice_channel = AsyncMock(return_value=77)
        stale = _VoiceClient(77, connected=False)
        channel = _VoiceChannel(77, error=RuntimeError("voice unavailable"))
        guild = _Guild(channel, voice_client=stale)
        cog._voice_connected_guilds.add(10)
        cog._schedule_voice_alert = Mock()

        with (
            patch.object(tts_module.discord, "VoiceChannel", _VoiceChannel),
            patch.object(tts_module, "VOICE_RECONNECT_ATTEMPTS", 2),
            patch.object(cog, "_retry_delay", return_value=0.0),
            patch.object(tts_module.asyncio, "sleep", new=AsyncMock()),
        ):
            result = await cog.ensure_connected(guild)

        self.assertIsNone(result)
        stale.disconnect.assert_awaited_once_with(force=True)
        self.assertEqual(channel.connect.await_count, 2)
        self.assertEqual(runtime_health_payload(cog.bot)["voice"]["state"], "unavailable")
        self.assertEqual(
            runtime_health_payload(cog.bot)["voice"]["lastErrorCode"],
            "VOICE_RECONNECT_EXHAUSTED",
        )
if __name__ == "__main__":
    unittest.main()
