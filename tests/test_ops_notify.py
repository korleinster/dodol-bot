"""Offline contracts for the operational notification adapter."""
import os
import unittest
from unittest.mock import AsyncMock, patch

from src.utils import notify


class _Response:
    def __init__(self, status, body=None, headers=None):
        self.status = status
        self.body = body or {}
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self, **_kwargs):
        return self.body


class _Session:
    def __init__(self, responses, calls):
        self.responses = responses
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class NotifyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        notify._recent.clear()
        notify._in_flight.clear()
        self.env = patch.dict(os.environ, {
            "SLACK_BOT_TOKEN": "fixture-token",
            "SLACK_ALERTS_CHANNEL_ID": "CALERTS",
            "SLACK_ROUTINE_CHANNEL_ID": "CROUTINE",
            "SLACK_OPERATIONS_CHANNEL_ID": "COPS",
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    async def test_default_and_dual_routing(self):
        with patch.object(notify, "_send_telegram", new_callable=AsyncMock) as telegram, \
             patch.object(notify, "_send_slack", new_callable=AsyncMock) as slack:
            await notify.send_operational("default")
            telegram.assert_awaited_once()
            slack.assert_not_awaited()
            os.environ["OPS_NOTIFICATION_BACKEND"] = "dual"
            await notify.send_operational("dual", "operations")
            self.assertEqual(telegram.await_count, 2)
            slack.assert_awaited_once_with("dual", "operations")

    async def test_slack_only_does_not_require_or_call_telegram(self):
        os.environ["OPS_NOTIFICATION_BACKEND"] = "slack"
        with patch.object(notify, "_send_telegram", new_callable=AsyncMock) as telegram, \
             patch.object(notify, "_send_slack", new_callable=AsyncMock) as slack:
            await notify.send_telegram("ready")
            telegram.assert_not_awaited()
            slack.assert_awaited_once_with("ready", "routine")

    async def test_unknown_backend_fails_closed_and_deduplicates(self):
        os.environ["OPS_NOTIFICATION_BACKEND"] = "invalid"
        with patch.object(notify, "_send_slack", new_callable=AsyncMock) as slack:
            await notify.send_operational("fault")
            slack.assert_not_awaited()
            os.environ["OPS_NOTIFICATION_BACKEND"] = "slack"
            await notify.send_operational("fault")
            await notify.send_operational("fault")
            slack.assert_awaited_once()

    async def test_saturated_sender_drops_new_work_without_waiting(self):
        os.environ["OPS_NOTIFICATION_BACKEND"] = "slack"
        notify._in_flight.update(("alerts", f"other-{i}") for i in range(notify._MAX_IN_FLIGHT))
        with patch.object(notify, "_send_slack", new_callable=AsyncMock) as slack:
            await notify.send_operational("new-fault")
            slack.assert_not_awaited()
        self.assertEqual(len(notify._in_flight), notify._MAX_IN_FLIGHT)

    async def test_slack_api_failure_and_rate_limit_are_bounded(self):
        responses = [
            _Response(429, headers={"Retry-After": "99999"}),
            _Response(503),
            _Response(200, {"ok": True}),
        ]
        calls = []
        with patch.object(notify.aiohttp, "ClientSession", side_effect=lambda **_kw: _Session(responses, calls)), \
             patch.object(notify.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await notify._send_slack("<@owner> token=private", "alerts")
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][0], "https://slack.com/api/chat.postMessage")
        self.assertEqual(calls[0][1]["json"]["channel"], "CALERTS")
        self.assertNotIn("private", calls[0][1]["json"]["text"])
        self.assertNotIn("@owner", calls[0][1]["json"]["text"])
        self.assertEqual(sleep.await_args_list[0].args, (2.0,))

    async def test_slack_api_false_is_not_retried(self):
        calls = []
        responses = [_Response(200, {"ok": False, "error": "fixture-private"})]
        with patch.object(notify.aiohttp, "ClientSession", side_effect=lambda **_kw: _Session(responses, calls)), \
             patch.object(notify.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await notify._send_slack("fault", "alerts")
        self.assertEqual(len(calls), 1)
        sleep.assert_not_awaited()

    async def test_transport_failure_does_not_break_caller(self):
        os.environ["OPS_NOTIFICATION_BACKEND"] = "dual"
        with patch.object(notify, "_send_telegram", new_callable=AsyncMock, side_effect=RuntimeError("private")), \
             patch.object(notify, "_send_slack", new_callable=AsyncMock) as slack:
            await notify.send_operational("fault")
            slack.assert_awaited_once()

    async def test_fake_secret_shapes_are_redacted_before_routing_and_dedup(self):
        os.environ["OPS_NOTIFICATION_BACKEND"] = "dual"
        message = (
            '장애 보고 https://example.test/status?view=brief '
            '"api_key":"fake-json-value" API-KEY=fake-query-value '
            "apikey='fake quoted value' passwd=\"fake two words\" "
            'PASSWORD: fake-password SECRET=fake-secret token=fake-token '
            'Authorization: Bearer fake-authorization\n'
            'Cookie: session=fake-cookie; csrf=fake-csrf\n'
            'bearer fake-bearer'
        )
        secrets = (
            "fake-json-value", "fake-query-value", "fake quoted value",
            "fake two words", "fake-password", "fake-secret", "fake-token",
            "fake-authorization", "fake-cookie", "fake-csrf", "fake-bearer",
        )
        with patch.object(notify, "_send_telegram", new_callable=AsyncMock) as telegram, \
             patch.object(notify, "_send_slack", new_callable=AsyncMock) as slack:
            await notify.send_operational(message)
            safe = telegram.await_args.args[0]
            slack.assert_awaited_once_with(safe, "alerts")
            for secret in secrets:
                self.assertNotIn(secret, safe)
            self.assertIn("장애 보고 https://example.test/status?view=brief", safe)
            self.assertEqual(next(iter(notify._recent))[1], safe)

    async def test_quoted_secret_is_redacted_before_length_limit(self):
        text = "정상 한글 " + ("x" * 2940) + ' passwd="fake hidden second word"'
        safe = notify._safe_text(text)
        self.assertLessEqual(len(safe), 3000)
        self.assertNotIn("fake hidden second word", safe)
        self.assertIn('passwd="[redacted]"', safe)

    async def test_json_query_headers_and_escaped_quotes(self):
        cases = (
            ('{"APIKEY":"fake-json-key","authorization":"Bearer fake-json-auth"}',
             ("fake-json-key", "fake-json-auth")),
            ("https://example.test/report?api-key=fake-url-key&token=fake-url-token",
             ("fake-url-key", "fake-url-token")),
            ("passwd='fake quoted \\' word' secret=\"fake quoted \\\" word\"",
             ("fake quoted",)),
            ("Set-Cookie: session=fake-session; csrf=fake-csrf\n보고서 정상",
             ("fake-session", "fake-csrf")),
            ("Bearer 'fake two word bearer'", ("fake two word bearer",)),
        )
        for raw, secrets in cases:
            with self.subTest(raw=raw):
                safe = notify._safe_text(raw)
                for secret in secrets:
                    self.assertNotIn(secret, safe)
        self.assertIn("https://example.test/report?", notify._safe_text(cases[1][0]))
        self.assertIn("보고서 정상", notify._safe_text(cases[3][0]))

    async def test_direct_telegram_payload_is_redacted(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "fake-telegram-transport-token"
        os.environ["TELEGRAM_CHAT_ID"] = "fake-chat-id"
        calls = []
        with patch.object(notify.aiohttp, "ClientSession", side_effect=lambda **_kw: _Session([_Response(200)], calls)):
            await notify._send_telegram('비상 "password":"fake-telegram-password"')
        self.assertEqual(len(calls), 1)
        self.assertNotIn("fake-telegram-password", calls[0][1]["data"]["text"])
        self.assertIn("비상", calls[0][1]["data"]["text"])

    async def test_redaction_is_stable_across_double_sanitization(self):
        cases = (
            "token=fake-token",
            "api_key=[fake bracketed value]",
            "passwd=[fake-value]suffix",
            '"password":"fake quoted value"',
            "Authorization: Bearer fake-header\nCookie: session=fake-cookie",
            "Bearer fake-bearer",
            "정상 한글 " + ("x" * 2940) + " token=[fake secret near limit]",
        )
        for raw in cases:
            with self.subTest(raw=raw[:60]):
                once = notify._safe_text(raw)
                self.assertEqual(notify._safe_text(once), once)
                self.assertNotIn("fake", once)
                self.assertLessEqual(len(once), 3000)
        self.assertEqual(notify._safe_text("api_key=[fake bracketed value]"), "api_key=[redacted]")
        self.assertEqual(notify._safe_text("passwd=[fake-value]suffix"), "passwd=[redacted]")

    async def test_unterminated_values_are_redacted_before_truncation(self):
        cases = (
            ('password="fake private value', 'password="[redacted]"'),
            ("passwd='fake private value", "passwd='[redacted]'"),
            ("token=[fake bracketed private value", "token=[redacted]"),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                safe = notify._safe_text(raw)
                self.assertEqual(safe, expected)
                self.assertEqual(notify._safe_text(safe), safe)

        prefix = "정상 한글 " + ("x" * 2940)
        for raw, _expected in cases:
            with self.subTest(boundary=raw):
                safe = notify._safe_text(prefix + " " + raw)
                self.assertNotIn("fake", safe)
                self.assertEqual(notify._safe_text(safe), safe)
                self.assertLessEqual(len(safe), 3000)

    async def test_quoted_multiword_authorization_reaches_neither_fake_transport(self):
        os.environ.update({
            "OPS_NOTIFICATION_BACKEND": "dual",
            "TELEGRAM_BOT_TOKEN": "fixture-telegram-token",
            "TELEGRAM_CHAT_ID": "fixture-chat",
        })
        calls = []
        responses = [_Response(200), _Response(200, {"ok": True})]
        with patch.object(notify.aiohttp, "ClientSession", side_effect=lambda **_kw: _Session(responses, calls)):
            await notify.send_operational(
                '상태 https://safe.example/status?view=good authorization=Bearer "FAKE_ALPHA FAKE_BETA FAKE_GAMMA"'
            )
        self.assertEqual(len(calls), 2)
        texts = [calls[0][1]["data"]["text"], calls[1][1]["json"]["text"]]
        texts.append(next(iter(notify._recent))[1])
        for text in texts:
            self.assertIn("상태 https://safe.example/status?view=good", text)
        leaks = [(index, word) for index, text in enumerate(texts)
                 for word in ("FAKE_ALPHA", "FAKE_BETA", "FAKE_GAMMA") if word in text]
        self.assertEqual(leaks, [], "all fake credential words must be removed before transport and dedup")

    async def test_authorization_value_forms_are_redacted_before_fake_transport_and_dedup(self):
        os.environ.update({
            "OPS_NOTIFICATION_BACKEND": "dual",
            "TELEGRAM_BOT_TOKEN": "fixture-telegram-token",
            "TELEGRAM_CHAT_ID": "fixture-chat",
        })
        credentials = (
            'authorization=Bearer "FAKE_ALPHA FAKE_BETA FAKE_GAMMA"',
            "Authorization = basic 'FAKE_ALPHA FAKE_BETA FAKE_GAMMA'",
            'AuThOrIzAtIoN: BEARER [FAKE_ALPHA FAKE_BETA FAKE_GAMMA]',
            'authorization:Basic "FAKE_ALPHA FAKE_BETA FAKE_GAMMA"',
            'AUTHORIZATION=Bearer FAKE_ALPHA',
            'authorization = Basic [FAKE_ALPHA FAKE_BETA FAKE_GAMMA',
            'authorization=Bearer "FAKE_ALPHA FAKE_BETA FAKE_GAMMA',
            "authorization=Basic 'FAKE_ALPHA FAKE_BETA FAKE_GAMMA",
        )
        for index, credential in enumerate(credentials):
            with self.subTest(credential=credential):
                notify._recent.clear()
                safe_prefix = f"상태 {index} https://safe.example/status?view=good"
                raw = safe_prefix + "\n" + credential
                safe = notify._safe_text(raw)
                self.assertEqual(notify._safe_text(safe), safe)
                self.assertLessEqual(len(safe), 3000)
                self.assertIn(safe_prefix, safe)
                calls = []
                responses = [_Response(200), _Response(200, {"ok": True})]
                with patch.object(
                    notify.aiohttp, "ClientSession",
                    side_effect=lambda **_kw: _Session(responses, calls),
                ):
                    await notify.send_operational(raw)
                self.assertEqual(len(calls), 2)
                surfaces = (
                    safe,
                    calls[0][1]["data"]["text"],
                    calls[1][1]["json"]["text"],
                    next(iter(notify._recent))[1],
                )
                for surface in surfaces:
                    self.assertIn(safe_prefix, surface)
                    for word in ("FAKE_ALPHA", "FAKE_BETA", "FAKE_GAMMA"):
                        self.assertNotIn(word, surface)

    async def test_authorization_redaction_precedes_length_limit(self):
        prefix = "정상 한글 https://safe.example/status " + ("x" * 2900)
        for credential in (
            'authorization=Bearer "FAKE_ALPHA FAKE_BETA FAKE_GAMMA"',
            "Authorization = Basic 'FAKE_ALPHA FAKE_BETA FAKE_GAMMA",
            'authorization=Bearer [FAKE_ALPHA FAKE_BETA FAKE_GAMMA',
        ):
            with self.subTest(credential=credential):
                safe = notify._safe_text(prefix + "\n" + credential)
                self.assertIn("정상 한글 https://safe.example/status", safe)
                self.assertIn("[redacted]", safe)
                self.assertNotIn("FAKE_", safe)
                self.assertLessEqual(len(safe), 3000)
                self.assertEqual(notify._safe_text(safe), safe)

    async def test_unclosed_authorization_stops_at_line_end_before_later_quote(self):
        os.environ.update({
            "OPS_NOTIFICATION_BACKEND": "dual",
            "TELEGRAM_BOT_TOKEN": "fixture-telegram-token",
            "TELEGRAM_CHAT_ID": "fixture-chat",
        })
        benign = '상태 "정상" https://safe.example/status'
        raw = 'authorization=Bearer "FAKE_ALPHA FAKE_BETA FAKE_GAMMA\n' + benign
        calls = []
        responses = [_Response(200), _Response(200, {"ok": True})]
        with patch.object(notify.aiohttp, "ClientSession", side_effect=lambda **_kw: _Session(responses, calls)):
            await notify.send_operational(raw)
        self.assertEqual(len(calls), 2)
        surfaces = (
            notify._safe_text(raw),
            calls[0][1]["data"]["text"],
            calls[1][1]["json"]["text"],
            next(iter(notify._recent))[1],
        )
        lost_benign = []
        for index, text in enumerate(surfaces):
            for word in ("FAKE_ALPHA", "FAKE_BETA", "FAKE_GAMMA"):
                self.assertNotIn(word, text)
            if "\n" + benign not in text:
                lost_benign.append(index)
        self.assertEqual(lost_benign, [], "malformed value must stop at its own line end")

    async def test_line_endings_and_escapes_preserve_next_line_in_fake_transports(self):
        os.environ.update({
            "OPS_NOTIFICATION_BACKEND": "dual",
            "TELEGRAM_BOT_TOKEN": "fixture-telegram-token",
            "TELEGRAM_CHAT_ID": "fixture-chat",
        })
        benign = '상태 "정상" https://safe.example/status?view=good'
        credentials = (
            'authorization=Bearer "FAKE_ALPHA FAKE_BETA FAKE_GAMMA',
            "authorization=Basic 'FAKE_ALPHA FAKE_BETA FAKE_GAMMA",
            'authorization=Bearer [FAKE_ALPHA FAKE_BETA FAKE_GAMMA',
            'authorization=Bearer "FAKE_ALPHA \\" FAKE_BETA FAKE_GAMMA',
            'authorization=Bearer "FAKE_ALPHA FAKE_BETA\\',
            'authorization=Bearer "FAKE_ALPHA\\nFAKE_BETA FAKE_GAMMA"',
            'authorization=Bearer [FAKE_ALPHA FAKE_BETA FAKE_GAMMA]',
            'Authorization: Bearer FAKE_ALPHA FAKE_BETA FAKE_GAMMA',
            'Bearer "FAKE_ALPHA FAKE_BETA FAKE_GAMMA"',
            'Bearer "FAKE_ALPHA FAKE_BETA FAKE_GAMMA',
        )
        for newline in ("\n", "\r\n", "\r"):
            for credential in credentials:
                with self.subTest(newline=repr(newline), credential=credential):
                    notify._recent.clear()
                    raw = credential + newline + benign
                    safe = notify._safe_text(raw)
                    self.assertEqual(notify._safe_text(safe), safe)
                    self.assertLessEqual(len(safe), 3000)
                    calls = []
                    responses = [_Response(200), _Response(200, {"ok": True})]
                    with patch.object(
                        notify.aiohttp, "ClientSession",
                        side_effect=lambda **_kw: _Session(responses, calls),
                    ):
                        await notify.send_operational(raw)
                    self.assertEqual(len(calls), 2)
                    surfaces = (
                        safe,
                        calls[0][1]["data"]["text"],
                        calls[1][1]["json"]["text"],
                        next(iter(notify._recent))[1],
                    )
                    for surface in surfaces:
                        self.assertTrue(surface.endswith(newline + benign))
                        for word in ("FAKE_ALPHA", "FAKE_BETA", "FAKE_GAMMA"):
                            self.assertNotIn(word, surface)

    async def test_empty_credential_prefix_does_not_consume_next_line(self):
        benign = '상태 "정상" https://safe.example/status'
        for newline in ("\n", "\r\n", "\r"):
            for prefix in ("authorization=", "authorization=Bearer", "Authorization:", "Bearer"):
                with self.subTest(newline=repr(newline), prefix=prefix):
                    safe = notify._safe_text(prefix + newline + benign)
                    self.assertTrue(safe.endswith(newline + benign))
                    self.assertEqual(notify._safe_text(safe), safe)


if __name__ == "__main__":
    unittest.main()
