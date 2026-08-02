import asyncio
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

if "gtts" not in sys.modules:
    try:
        __import__("gtts")
    except ModuleNotFoundError:
        gtts_stub = types.ModuleType("gtts")
        gtts_stub.gTTS = object
        sys.modules["gtts"] = gtts_stub

from src.cogs import tts as tts_module
from src.cogs.tts import (
    DEFAULT_EDGE_PITCH,
    DEFAULT_EDGE_RATE,
    DEFAULT_EDGE_VOICE,
    TTS,
    TTSProviderSettings,
    get_tts_provider_settings,
)


class TTSProviderSettingsTest(unittest.TestCase):
    def test_003_defaults_to_selected_edge_female_voice(self):
        self.assertEqual(
            get_tts_provider_settings(3, {}),
            TTSProviderSettings(
                provider="edge",
                voice=DEFAULT_EDGE_VOICE,
                rate=DEFAULT_EDGE_RATE,
                pitch=DEFAULT_EDGE_PITCH,
            ),
        )

    def test_003_keeps_the_verified_voice_and_sanitizes_invalid_values(self):
        configured = get_tts_provider_settings(3, {
            "TTS_PROVIDER": "edge",
            "TTS_EDGE_VOICE": DEFAULT_EDGE_VOICE,
            "TTS_EDGE_RATE": "+12%",
            "TTS_EDGE_PITCH": "-3Hz",
        })
        self.assertEqual(
            configured,
            TTSProviderSettings("edge", DEFAULT_EDGE_VOICE, "+12%", "-3Hz"),
        )
        invalid = get_tts_provider_settings(3, {
            "TTS_PROVIDER": "edge",
            "TTS_EDGE_VOICE": "ko-KR-SeoHyeonNeural",
            "TTS_EDGE_RATE": "fast",
            "TTS_EDGE_PITCH": "high",
        })
        self.assertEqual(
            invalid,
            TTSProviderSettings("edge", DEFAULT_EDGE_VOICE, DEFAULT_EDGE_RATE, DEFAULT_EDGE_PITCH),
        )

    def test_non_003_bots_remain_gtts_even_if_global_edge_settings_exist(self):
        environment = {"TTS_PROVIDER": "edge", "TTS_EDGE_VOICE": "ko-KR-SeoHyeonNeural"}
        for bot_number in (1, 2, 4):
            with self.subTest(bot_number=bot_number):
                self.assertEqual(get_tts_provider_settings(bot_number, environment), TTSProviderSettings("gtts"))


class TTSSynthesisFallbackTest(unittest.IsolatedAsyncioTestCase):
    def _cog(self, bot_number=3):
        return TTS(SimpleNamespace(bot_number=bot_number))

    async def test_edge_synthesis_uses_selected_voice_parameters(self):
        cog = self._cog()
        settings = TTSProviderSettings("edge", DEFAULT_EDGE_VOICE, DEFAULT_EDGE_RATE, DEFAULT_EDGE_PITCH)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "speech.mp3")
            async def save_edge(_text, edge_path, _settings):
                Path(edge_path).write_bytes(b"edge")

            edge = AsyncMock(side_effect=save_edge)
            with (
                patch.object(cog, "_provider_settings", return_value=settings),
                patch.object(tts_module, "_save_edge_tts", edge),
            ):
                self.assertEqual(await cog._synthesize("보스 출현", path), "edge")
            edge.assert_awaited_once_with("보스 출현", f"{path}.edge", settings)

    async def test_edge_client_receives_the_selected_voice_rate_and_pitch(self):
        settings = TTSProviderSettings("edge", DEFAULT_EDGE_VOICE, DEFAULT_EDGE_RATE, DEFAULT_EDGE_PITCH)
        client = SimpleNamespace(save=AsyncMock())
        edge_module = SimpleNamespace(Communicate=Mock(return_value=client))
        with patch.object(tts_module, "edge_tts", edge_module):
            await tts_module._save_edge_tts("보스 출현", "/tmp/voice.mp3", settings)
        edge_module.Communicate.assert_called_once_with(
            "보스 출현",
            voice=DEFAULT_EDGE_VOICE,
            rate=DEFAULT_EDGE_RATE,
            pitch=DEFAULT_EDGE_PITCH,
        )
        client.save.assert_awaited_once_with("/tmp/voice.mp3")

    async def test_timeout_uses_gtts_once_and_late_edge_write_cannot_replace_fallback(self):
        cog = self._cog()
        settings = TTSProviderSettings("edge", DEFAULT_EDGE_VOICE, DEFAULT_EDGE_RATE, DEFAULT_EDGE_PITCH)

        async def delayed_edge(_text, edge_path, _settings):
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                # Simulate a provider coroutine which completes a buffered write
                # after cancellation. It must stay isolated from the fallback.
                Path(edge_path).write_text("edge-late", encoding="utf-8")

        def save_gtts(_text, target_path):
            Path(target_path).write_text("gtts-fallback", encoding="utf-8")

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "speech.mp3")
            with (
                patch.object(cog, "_provider_settings", return_value=settings),
                patch.object(tts_module, "_save_edge_tts", delayed_edge),
                patch.object(tts_module, "_save_tts", save_gtts),
                patch.object(tts_module, "EDGE_TTS_TIMEOUT_SECONDS", 0.001),
            ):
                self.assertEqual(await cog._synthesize("보스 출현", path), "gtts")
            await asyncio.sleep(0)
            self.assertEqual(Path(path).read_text(encoding="utf-8"), "gtts-fallback")
            self.assertFalse(Path(f"{path}.edge").exists())

    async def test_edge_error_uses_gtts_once(self):
        cog = self._cog()
        settings = TTSProviderSettings("edge", DEFAULT_EDGE_VOICE, DEFAULT_EDGE_RATE, DEFAULT_EDGE_PITCH)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "speech.mp3")
            def fallback(_text, target_path):
                Path(target_path).write_bytes(b"gtts")

            fallback = Mock(side_effect=fallback)
            with (
                patch.object(cog, "_provider_settings", return_value=settings),
                patch.object(tts_module, "_save_edge_tts", AsyncMock(side_effect=RuntimeError("network"))),
                patch.object(tts_module, "_save_tts", fallback),
            ):
                self.assertEqual(await cog._synthesize("테스트", path), "gtts")
            fallback.assert_called_once_with("테스트", f"{path}.gtts")

    async def test_cancelled_gtts_worker_cleans_its_late_private_file(self):
        cog = self._cog(bot_number=1)
        started = threading.Event()
        release = threading.Event()

        def delayed_gtts(_text, private_path):
            started.set()
            release.wait(timeout=2)
            Path(private_path).write_text("late", encoding="utf-8")

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "speech.mp3")
            task = None
            with patch.object(tts_module, "_save_tts", delayed_gtts):
                task = asyncio.create_task(cog._synthesize("테스트", path))
                await asyncio.get_running_loop().run_in_executor(None, started.wait)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                release.set()
                await asyncio.sleep(0.05)
            self.assertFalse(Path(path).exists())
            self.assertFalse(Path(f"{path}.gtts").exists())

    async def test_speak_removes_temp_file_when_all_synthesis_fails(self):
        cog = self._cog()
        cog._voice_runtime_ready = Mock(return_value=True)
        cog.get_voice_channel = AsyncMock(return_value=42)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "speech.mp3")
            Path(path).touch()

            class _TempFile:
                def __init__(self, name):
                    self.name = name

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

            with (
                patch.object(tts_module.tempfile, "NamedTemporaryFile", return_value=_TempFile(path)),
                patch.object(cog, "_synthesize", AsyncMock(side_effect=RuntimeError("offline"))),
            ):
                self.assertFalse(await cog.speak(SimpleNamespace(id=100), "테스트"))
            self.assertFalse(os.path.exists(path))
