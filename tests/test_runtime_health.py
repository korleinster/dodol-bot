import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.health_probe import is_healthy
from src.runtime_health import (
    _atomic_write,
    application_health_payload,
    host_visible_health_gid,
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

    def test_host_visible_mirror_is_per_bot_atomic_and_non_secret(self):
        bot = self._ready_bot()
        bot.bot_number = 4
        bot.scheduler_health["lastTickAt"] = 123456789
        bot.scheduler_health["privateToken"] = "never-publish"
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "local.json"
            gid = max(os.getgid(), 1)
            with patch.dict(os.environ, {"BOT_RUNTIME_HEALTH_DIR": directory,
                                      "BOT_RUNTIME_HEALTH_GID": str(gid)}), \
                 patch("src.runtime_health.health_file_path", return_value=local), \
                 patch("src.runtime_health.os.fchown") as chown:
                write_health_file(bot)
            chown.assert_called_once()
            self.assertEqual(chown.call_args.args[1:], (0, gid))
            mirror = Path(directory) / "runtime-health-004.json"
            payload = json.loads(mirror.read_text())
            self.assertEqual(payload["botNumber"], 4)
            self.assertEqual(payload["discord"], "ready")
            self.assertEqual(payload["schedulerStatus"], "ready")
            self.assertEqual(payload["schedulerLastTickAt"], 123456789)
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["version"], 1)
            self.assertIsInstance(payload["observedAt"], int)
            self.assertNotIn("never-publish", mirror.read_text())
            self.assertEqual(mirror.stat().st_mode & 0o777, 0o640)
            self.assertEqual(local.stat().st_mode & 0o777, 0o600)
            self.assertTrue(is_healthy(local))

    def test_invalid_bot_number_never_writes_host_visible_file(self):
        bot = self._ready_bot()
        bot.bot_number = 99
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"BOT_RUNTIME_HEALTH_DIR": directory}), \
                 patch("src.runtime_health.health_file_path", return_value=Path(directory) / "local.json"):
                write_health_file(bot)
            self.assertEqual(len(list(Path(directory).glob("runtime-health-*.json"))), 0)

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


class RuntimeHealthMirrorPermissionsTest(unittest.TestCase):
    def _bot(self):
        bot = SimpleNamespace(bot_number=4, scheduler_health={"status": "ready"})
        initialize_runtime_health(bot)
        bot.runtime_health["discord"] = "ready"
        return bot

    def test_gid_requires_explicit_positive_ascii_integer(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(host_visible_health_gid())
        for bad in ("", "0", "-1", "+1000", " 1000", "1000 ", "001", "１", "4294967295"):
            with self.subTest(gid=bad), patch.dict(os.environ, {"BOT_RUNTIME_HEALTH_GID": bad}):
                self.assertIsNone(host_visible_health_gid())
        with patch.dict(os.environ, {"BOT_RUNTIME_HEALTH_GID": "1000"}):
            self.assertEqual(host_visible_health_gid(), 1000)

    def test_missing_or_invalid_gid_skips_mirror_but_internal_stays_0600(self):
        for value in (None, "0", "not-a-gid"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                local = Path(directory) / "local.json"
                mirror = Path(directory) / "runtime-health-004.json"
                env = {"BOT_RUNTIME_HEALTH_DIR": directory}
                if value is not None:
                    env["BOT_RUNTIME_HEALTH_GID"] = value
                with patch.dict(os.environ, env, clear=True), \
                     patch("src.runtime_health.health_file_path", return_value=local), \
                     patch("src.runtime_health.os.fchown") as chown:
                    write_health_file(self._bot())
                self.assertTrue(local.is_file())
                self.assertEqual(local.stat().st_mode & 0o777, 0o600)
                self.assertFalse(mirror.exists())
                chown.assert_not_called()

    def test_chgrp_denial_keeps_previous_mirror_and_cleans_only_new_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "local.json"
            mirror = Path(directory) / "runtime-health-004.json"
            mirror.write_text("prior-mirror")
            with patch.dict(os.environ, {"BOT_RUNTIME_HEALTH_DIR": directory,
                                      "BOT_RUNTIME_HEALTH_GID": "1000"}), \
                 patch("src.runtime_health.health_file_path", return_value=local), \
                 patch("src.runtime_health.os.fchown", side_effect=PermissionError("denied")):
                write_health_file(self._bot())
            self.assertEqual(mirror.read_text(), "prior-mirror")
            self.assertEqual(local.stat().st_mode & 0o777, 0o600)
            self.assertEqual(sorted(path.name for path in Path(directory).iterdir()),
                             ["local.json", "runtime-health-004.json"])

    def test_repeated_atomic_replace_preserves_mirror_mode_and_cleans_temps(self):
        with tempfile.TemporaryDirectory() as directory:
            mirror = Path(directory) / "runtime-health-004.json"
            with patch("src.runtime_health.os.fchown") as chown:
                _atomic_write(mirror, {"version": 1}, mirror_gid=1000)
                first_inode = mirror.stat().st_ino
                _atomic_write(mirror, {"version": 2}, mirror_gid=1000)
            self.assertEqual(chown.call_count, 2)
            self.assertNotEqual(mirror.stat().st_ino, first_inode)
            self.assertEqual(json.loads(mirror.read_text()), {"version": 2})
            self.assertEqual(mirror.stat().st_mode & 0o777, 0o640)
            self.assertEqual([p.name for p in Path(directory).iterdir()], [mirror.name])

    def test_replace_failure_preserves_old_mirror_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            mirror = Path(directory) / "runtime-health-004.json"
            mirror.write_text("prior-mirror")
            with patch("src.runtime_health.os.fchown"), \
                 patch("src.runtime_health.os.replace", side_effect=OSError("denied")):
                with self.assertRaises(OSError):
                    _atomic_write(mirror, {"version": 2}, mirror_gid=1000)
            self.assertEqual(mirror.read_text(), "prior-mirror")
            self.assertEqual([p.name for p in Path(directory).iterdir()], [mirror.name])

    def test_preexisting_temp_symlink_is_not_followed_or_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            mirror = Path(directory) / "runtime-health-004.json"
            victim = Path(directory) / "victim"
            victim.write_text("do-not-touch")
            mirror.write_text("prior-mirror")
            temporary = mirror.with_name(f".{mirror.name}.{os.getpid()}.{'a' * 32}.tmp")
            temporary.symlink_to(victim)
            with patch("src.runtime_health.secrets.token_hex", return_value="a" * 32), \
                 patch("src.runtime_health.os.fchown") as chown:
                with self.assertRaises(FileExistsError):
                    _atomic_write(mirror, {"version": 2}, mirror_gid=1000)
            chown.assert_not_called()
            self.assertTrue(temporary.is_symlink())
            self.assertEqual(victim.read_text(), "do-not-touch")
            self.assertEqual(mirror.read_text(), "prior-mirror")

    def test_existing_mirror_symlink_is_replaced_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            mirror = Path(directory) / "runtime-health-004.json"
            victim = Path(directory) / "victim"
            victim.write_text("do-not-touch")
            mirror.symlink_to(victim)
            with patch("src.runtime_health.os.fchown"):
                _atomic_write(mirror, {"version": 1}, mirror_gid=1000)
            self.assertFalse(mirror.is_symlink())
            self.assertEqual(victim.read_text(), "do-not-touch")
            self.assertEqual(json.loads(mirror.read_text()), {"version": 1})


if __name__ == "__main__":
    unittest.main()
