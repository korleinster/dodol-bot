import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.health_probe import is_healthy
from src.runtime_health import (
    application_health_payload,
    initialize_runtime_health,
    runtime_health_payload,
    update_voice_health,
    voice_target_health_payload,
    write_health_file,
)


class RuntimeHealthProbeTest(unittest.TestCase):
    def _ready_bot(self):
        bot = SimpleNamespace(scheduler_health={"status": "ready"})
        initialize_runtime_health(bot)
        bot.runtime_health["discord"] = "ready"
        return bot

    def test_initial_voice_contract_is_unconfigured_when_no_target_exists(self):
        bot = SimpleNamespace()
        initialize_runtime_health(bot)
        self.assertEqual(runtime_health_payload(bot)["voice"], {
            "configured": False,
            "connected": False,
            "state": "unconfigured",
            "lastErrorCode": None,
            "nextRetryAt": None,
        })

    def test_no_config_fails_closed_to_unconfigured_even_if_internal_state_is_bad(self):
        bot = SimpleNamespace()
        initialize_runtime_health(bot)
        bot.runtime_health["voice"]["state"] = "connecting"
        self.assertEqual(runtime_health_payload(bot)["voice"]["state"], "unconfigured")

    def test_probe_accepts_only_fresh_fully_ready_application_state(self):
        bot = self._ready_bot()
        self.assertEqual(application_health_payload(bot)["status"], "ready")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "health.json"
            with patch("src.runtime_health.health_file_path", return_value=path):
                write_health_file(bot)
            self.assertTrue(is_healthy(path))
            self.assertFalse(is_healthy(path, now_ms=10**15))

    def test_configured_voice_requires_real_connected_observation(self):
        bot = self._ready_bot()
        update_voice_health(
            bot,
            10,
            configured=True,
            state="unavailable",
            error_code="VOICE_RECONNECT_EXHAUSTED",
        )
        self.assertEqual(runtime_health_payload(bot)["voice"]["connected"], False)
        self.assertEqual(application_health_payload(bot)["status"], "starting")

    def test_each_bridge_target_keeps_its_own_voice_liveness(self):
        bot = self._ready_bot()
        update_voice_health(bot, 10, configured=True, state="connected")

        self.assertEqual(voice_target_health_payload(bot, 10, configured=True)["state"], "connected")
        self.assertEqual(voice_target_health_payload(bot, 20, configured=False), {
            "configured": False,
            "connected": False,
            "state": "unconfigured",
            "lastErrorCode": None,
            "nextRetryAt": None,
        })
        self.assertEqual(
            voice_target_health_payload(bot, 30, configured=True)["state"],
            "connecting",
        )


if __name__ == "__main__":
    unittest.main()
