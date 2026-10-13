"""Best-effort operational notifications, with Discord bindings unchanged."""
from __future__ import annotations

import os
import asyncio
import re
import time
from collections import OrderedDict

import aiohttp
import discord
from discord.ext import commands


_SLACK_URL = "https://slack.com/api/chat.postMessage"
_BACKENDS = frozenset({"telegram", "slack", "dual"})
_MAX_IN_FLIGHT = 16
_DEDUP_SECONDS = 60
_RECENT_LIMIT = 256
_in_flight: set[tuple[str, str]] = set()
_recent: OrderedDict[tuple[str, str], float] = OrderedDict()
_SECRET_FIELD = r"(?:api[_-]?key|password|passwd|secret|token|authorization|set-cookie|cookie|credential)"
_FIELD_PREFIX = rf"(?:[\"']{_SECRET_FIELD}[\"']|(?<![\w-]){_SECRET_FIELD}\b)[ \t]*(?::|=|%3[dD])[ \t]*"
_VALUE_PREFIX = rf"({_FIELD_PREFIX})(?:(?:Bearer|Basic)[ \t]+)?"
# A malformed value ends at the physical CR/LF boundary, not at a later quote.
_LINE_END = r"(?=\r\n|\r|\n|$)"
_UNTERMINATED_DOUBLE = r'"(?:\\[^\r\n]|[^"\\\r\n]|\\' + _LINE_END + r")*" + _LINE_END
_UNTERMINATED_SINGLE = r"'(?:\\[^\r\n]|[^'\\\r\n]|\\" + _LINE_END + r")*" + _LINE_END
_HEADER = re.compile(r"(\b(?:authorization|set-cookie|cookie)[ \t]*:[ \t]*)[^\r\n]+", re.I)
_QUOTED_ASSIGNMENT = re.compile(
    _VALUE_PREFIX + r"(\"(?:\\[^\r\n]|[^\"\\\r\n])*\"|'(?:\\[^\r\n]|[^'\\\r\n])*')",
    re.I,
)
_UNTERMINATED_QUOTED_ASSIGNMENT = re.compile(
    _VALUE_PREFIX + f"({_UNTERMINATED_DOUBLE}|{_UNTERMINATED_SINGLE})",
    re.I,
)
_BRACKETED_ASSIGNMENT = re.compile(
    _VALUE_PREFIX + r"\[(?:\\[^\r\n]|[^\]\\\r\n])*\][^\s\"'&,;}]*", re.I
)
_UNTERMINATED_BRACKETED_ASSIGNMENT = re.compile(
    _VALUE_PREFIX + r"\[(?:\\[^\r\n]|[^\]\\\r\n]|\\" + _LINE_END
    + r")*" + _LINE_END,
    re.I,
)
_PLAIN_ASSIGNMENT = re.compile(
    "(" + _FIELD_PREFIX + r")"
    r"(?:(?:Bearer|Basic)[ \t]+(?![\"'\[])|(?!(?:Bearer|Basic)[ \t]+))"
    r"[^\s\"'&,;}\[]+", re.I
)
_BEARER = re.compile(
    r"(\bBearer[ \t]+)(?:\"(?:\\[^\r\n]|[^\"\\\r\n])*\"|"
    r"'(?:\\[^\r\n]|[^'\\\r\n])*'|[^\s\"',;>}]+)", re.I
)
_UNTERMINATED_BEARER = re.compile(
    r"(\bBearer[ \t]+)(?:" + _UNTERMINATED_DOUBLE + "|" + _UNTERMINATED_SINGLE + ")",
    re.I,
)
_KNOWN_TOKEN = re.compile(
    r"\b(?:xox[baprs]-[A-Za-z0-9-]{8,}|sk-[\w-]{8,}|"
    r"github_pat_[A-Za-z0-9_]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|"
    r"\d{6,}:[A-Za-z0-9_-]{20,})\b"
)


def notification_backend() -> str:
    """Unknown transport values fail closed without affecting bot execution."""
    value = os.getenv("OPS_NOTIFICATION_BACKEND", "telegram")
    return value if value in _BACKENDS else "invalid"


def _safe_text(text: str) -> str:
    # Replace whole values before applying the length bound. A word-based match
    # would leave the rest of a quoted value visible in every transport.
    text = _HEADER.sub(r"\1[redacted]", text)
    text = _QUOTED_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)[0]}[redacted]{match.group(2)[-1]}", text
    )
    text = _UNTERMINATED_QUOTED_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)[0]}[redacted]{match.group(2)[0]}", text
    )
    text = _BRACKETED_ASSIGNMENT.sub(r"\1[redacted]", text)
    text = _UNTERMINATED_BRACKETED_ASSIGNMENT.sub(r"\1[redacted]", text)
    text = _PLAIN_ASSIGNMENT.sub(r"\1[redacted]", text)
    text = _UNTERMINATED_BEARER.sub(r"\1[redacted]", text)
    text = _BEARER.sub(r"\1[redacted]", text)
    text = _KNOWN_TOKEN.sub("[redacted]", text)
    return text.replace("<", "").replace(">", "").replace("@", "＠")[:3000]


async def _send_telegram(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data={
                "chat_id": chat_id, "text": _safe_text(text),
                "disable_web_page_preview": "true",
            }):
                pass
    except Exception:
        pass


async def _send_slack(text: str, kind: str) -> None:
    token = os.getenv("SLACK_BOT_TOKEN", "")
    channel_key = {
        "alerts": "SLACK_ALERTS_CHANNEL_ID",
        "routine": "SLACK_ROUTINE_CHANNEL_ID",
        "operations": "SLACK_OPERATIONS_CHANNEL_ID",
    }.get(kind)
    channel = os.getenv(channel_key, "") if channel_key else ""
    if not token or not channel:
        return
    payload = {
        "channel": channel, "text": _safe_text(text), "parse": "none",
        "link_names": False, "unfurl_links": False, "unfurl_media": False,
    }
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for attempt in range(3):
                try:
                    async with session.post(
                        _SLACK_URL, json=payload,
                        headers={"Authorization": f"Bearer {token}"},
                    ) as response:
                        if response.status == 429 or response.status >= 500:
                            retry_after = response.headers.get("Retry-After", "1")
                            try:
                                delay = min(2.0, max(0.0, float(retry_after)))
                            except ValueError:
                                delay = 1.0
                        elif response.status != 200:
                            return
                        else:
                            body = await response.json(content_type=None)
                            if isinstance(body, dict) and body.get("ok") is True:
                                return
                            # A definitive API refusal must not be replayed.
                            return
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                    delay = 0.25 * (attempt + 1)
                if attempt < 2:
                    await asyncio.sleep(delay)
    except Exception:
        pass


async def send_operational(text: str, kind: str = "alerts") -> None:
    backend = notification_backend()
    if backend == "invalid" or kind not in {"alerts", "routine", "operations"}:
        return
    safe_text = _safe_text(text)
    key = (kind, safe_text)
    now = time.monotonic()
    while _recent and next(iter(_recent.values())) <= now - _DEDUP_SECONDS:
        _recent.popitem(last=False)
    if key in _in_flight or key in _recent or len(_in_flight) >= _MAX_IN_FLIGHT:
        return
    # Reserve before the first await so a storm cannot create unbounded sends.
    _in_flight.add(key)
    _recent[key] = now
    if len(_recent) > _RECENT_LIMIT:
        _recent.popitem(last=False)
    try:
        if backend in {"telegram", "dual"}:
            try:
                await _send_telegram(safe_text)
            except Exception:
                pass
        if backend in {"slack", "dual"}:
            try:
                await _send_slack(safe_text, kind)
            except Exception:
                pass
    finally:
        _in_flight.discard(key)


async def send_telegram(text: str) -> None:
    """Compatibility wrapper used by process-ready notifications."""
    await send_operational(text, "routine")


async def send_discord_alert(bot: commands.Bot, bot_number: int, text: str) -> None:
    """Send an operational alert only to currently active text bindings."""
    from src.db import get_db
    try:
        async with get_db() as db:
            async with db.execute(
                """SELECT DISTINCT text_channel_id FROM guild_config
                   WHERE bot_number=? AND text_channel_id IS NOT NULL""",
                (bot_number,),
            ) as cur:
                rows = await cur.fetchall()
    except Exception:
        return

    embed = discord.Embed(
        title="⚠️ 운영 알림",
        description=_safe_text(text),
        color=0xFF0000,
    )
    for (ch_id,) in rows:
        ch = bot.get_channel(ch_id)
        if isinstance(ch, discord.TextChannel):
            try:
                await ch.send(embed=embed)
            except Exception:
                pass


async def alert(bot: commands.Bot, bot_number: int, text: str) -> None:
    """Discord bindings plus the configured outbound operations transport."""
    try:
        await send_discord_alert(bot, bot_number, text)
    except Exception:
        pass
    await send_operational(f"[뚠뚠봇{bot_number:03d}] {text}", "alerts")
