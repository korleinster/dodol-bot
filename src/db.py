from __future__ import annotations

import json
import os
import time
import aiosqlite
from pathlib import Path
from contextlib import asynccontextmanager

DB_PATH = Path(os.getenv("DB_PATH", "./data/bot.db"))

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id    INTEGER NOT NULL,
    bot_number  INTEGER NOT NULL,
    text_channel_id  INTEGER,
    voice_channel_id INTEGER,
    PRIMARY KEY (guild_id, bot_number)
);

CREATE TABLE IF NOT EXISTS bosses (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id              INTEGER NOT NULL,
    bot_number            INTEGER NOT NULL,
    name                  TEXT    NOT NULL,
    aliases               TEXT    NOT NULL DEFAULT '[]',
    respawn_seconds       INTEGER,
    fixed                 INTEGER NOT NULL DEFAULT 0,
    fixed_days            TEXT,
    fixed_time            TEXT,
    spawns_on_open        INTEGER NOT NULL DEFAULT 0,
    open_delay_seconds    INTEGER NOT NULL DEFAULT 0,
    auto_schedule_seconds INTEGER NOT NULL DEFAULT 600,
    open_time_seconds     INTEGER,
    is_default            INTEGER NOT NULL DEFAULT 0,
    UNIQUE (guild_id, bot_number, name)
);

CREATE TABLE IF NOT EXISTS schedules (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    bot_number   INTEGER NOT NULL,
    boss_name    TEXT,
    content      TEXT    NOT NULL,
    scheduled_at TEXT    NOT NULL,
    is_fixed     INTEGER NOT NULL DEFAULT 0,
    miss_count   INTEGER NOT NULL DEFAULT 0,
    warned_5min  INTEGER NOT NULL DEFAULT 0,
    warned_1min  INTEGER NOT NULL DEFAULT 0,
    notified     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS contributions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    bot_number   INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    username     TEXT    NOT NULL,
    actor_type   TEXT    NOT NULL DEFAULT 'discord',
    actor_ref    TEXT,
    boss_name    TEXT    NOT NULL,
    cut_at       TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 각 봇이 연결된 Discord 채널에 직접 발화한 내용을 웹 포털에
-- 안전하게 중계하기 위한 append-only 이벤트 로그다.
CREATE TABLE IF NOT EXISTS web_broadcast_event (
    cursor           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key        TEXT    NOT NULL UNIQUE,
    bot_number       INTEGER NOT NULL DEFAULT 3,
    guild_id         INTEGER NOT NULL,
    channel_id       INTEGER NOT NULL,
    message_id       INTEGER NOT NULL,
    kind             TEXT    NOT NULL CHECK (kind IN ('message', 'message_update', 'message_delete')),
    content          TEXT,
    embeds_json      TEXT    NOT NULL DEFAULT '[]',
    attachments_json TEXT    NOT NULL DEFAULT '[]',
    components_json  TEXT    NOT NULL DEFAULT '[]',
    event_type       TEXT    NOT NULL DEFAULT 'generic',
    boss_name        TEXT,
    created_at       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_web_broadcast_event_guild_cursor
    ON web_broadcast_event (guild_id, cursor);
CREATE INDEX IF NOT EXISTS idx_web_broadcast_event_created_at
    ON web_broadcast_event (created_at);

-- A durable, per-component execution ledger. The idempotency key is chosen
-- by the component registry (for example both cut/miss buttons of one boss
-- alert intentionally share one key).
CREATE TABLE IF NOT EXISTS component_action_claim (
    idempotency_key TEXT PRIMARY KEY,
    request_id      TEXT    NOT NULL,
    bot_number      INTEGER NOT NULL DEFAULT 3,
    guild_id        INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    message_id      INTEGER NOT NULL,
    custom_id       TEXT    NOT NULL,
    action_key      TEXT    NOT NULL,
    actor_type      TEXT    NOT NULL,
    actor_ref       TEXT,
    status          TEXT    NOT NULL CHECK (status IN ('processing', 'succeeded', 'failed')),
    error_code      TEXT,
    attempt_count   INTEGER NOT NULL DEFAULT 1,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    completed_at    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_component_action_claim_message
    ON component_action_claim (guild_id, message_id, updated_at);
"""

WEB_BROADCAST_RETENTION_MS = 24 * 60 * 60 * 1000
WEB_BROADCAST_MAX_EVENTS_PER_GUILD = 500

# 구 이름 → 신 이름 (기존 DB 마이그레이션용)
_BOSS_RENAMES: dict[str, str] = {
    "크루마":       "돌연변이 크루마",
    "드래곤비스트": "드래곤 비스트",
    "블랙릴리":     "블랙 릴리",
    "오염된크루마": "오염된 크루마",
    "코어수스켑터": "코어 수스켑터",
    "판드라이드":   "판 드라이드",
    "판나로드":     "판 나로드",
    "망각의거울":   "망각의 거울",
}

# (이름, 리스폰초, spawns_on_open, open_delay초, aliases_json, fixed, fixed_days, fixed_time)
# fixed_days: "0"~"6" 콤마 구분 (월=0, 화=1, 수=2, 목=3, 금=4, 토=5, 일=6)
# fixed_time: "HH:MM" 또는 콤마 구분 복수 시각
DEFAULT_BOSSES: list[tuple] = [
    # ── 지연 없음 (서버오픈 시 확률 등장) ─────────────────────────────────────
    ("펠리스",                7200, 0,      0, '[]',              0, None,              None        ),
    ("바실라",                9000, 0,      0, '[]',              0, None,              None        ),
    ("판 나로드",            10800, 0,      0, '[]',              0, None,              None        ),
    ("체르투바",             10800, 0,      0, '[]',              0, None,              None        ),
    ("엔쿠라",               12600, 0,      0, '[]',              0, None,              None        ),
    ("템페스트",             12600, 0,      0, '[]',              0, None,              None        ),
    ("스탄",                 14400, 0,      0, '[]',              0, None,              None        ),
    ("브래카",               14400, 0,      0, '[]',              0, None,              None        ),
    ("마투라",               14400, 0,      0, '[]',              0, None,              None        ),
    ("트롬바",               16200, 0,      0, '[]',              0, None,              None        ),
    ("탈킨",                 18000, 0,      0, '[]',              0, None,              None        ),
    ("레피로",               18000, 0,      0, '[]',              0, None,              None        ),
    ("티미트리스",           18000, 0,      0, '[]',              0, None,              None        ),
    ("탈라킨",               25200, 0,      0, '[]',              0, None,              None        ),
    ("사르카",               25200, 0,      0, '[]',              0, None,              None        ),
    ("메두사",               25200, 0,      0, '[]',              0, None,              None        ),
    ("셀루",                 27000, 0,      0, '[]',              0, None,              None        ),
    ("판 드라이드",          28800, 0,      0, '[]',              0, None,              None        ),
    # ── 지연 있음 (서버오픈 + 지연시간 후 첫 등장) ────────────────────────────
    ("여왕개미",             21600, 1,  50400, '[]',              0, None,              None        ),
    ("켈소스",               21600, 1,  21600, '[]',              0, None,              None        ),
    ("가레스",               21600, 1,  21600, '[]',              0, None,              None        ),
    ("베히모스",             21600, 1,  21600, '[]',              0, None,              None        ),
    ("히실로메",             21600, 1,  21600, '[]',              0, None,              None        ),
    ("돌연변이 크루마",      28800, 1,  21600, '["돌크","크루마"]', 0, None,            None        ),
    ("글라키",               28800, 1,  50400, '[]',              0, None,              None        ),
    ("티미니엘",             28800, 1,  21600, '[]',              0, None,              None        ),
    ("발보",                 28800, 1,  21600, '[]',              0, None,              None        ),
    ("란도르",               28800, 1,  36000, '[]',              0, None,              None        ),
    ("오염된 크루마",        28800, 1,  21600, '["오크"]',        0, None,              None        ),
    ("플린트",               28800, 1,  36000, '[]',              0, None,              None        ),
    ("카탄",                 28800, 1,  21600, '[]',              0, None,              None        ),
    ("코룬",                 36000, 1,  21600, '[]',              0, None,              None        ),
    ("나이아스",             43200, 1,  36000, '[]',              0, None,              None        ),
    ("드래곤 비스트",        43200, 1,  50400, '[]',              0, None,              None        ),
    ("망각의 거울",          43200, 1,  36000, '[]',              0, None,              None        ),
    ("무프",                 43200, 1,  50400, '[]',              0, None,              None        ),
    ("카브리오",             43200, 1,  36000, '[]',              0, None,              None        ),
    ("안드라스",             43200, 1,  36000, '[]',              0, None,              None        ),
    ("사반",                 43200, 1,  21600, '[]',              0, None,              None        ),
    ("사무엘",               43200, 1,  36000, '[]',              0, None,              None        ),
    ("코어 수스켑터",        43200, 1,  50400, '[]',              0, None,              None        ),
    ("블랙 릴리",            43200, 1,  36000, '[]',              0, None,              None        ),
    ("실라",                 43200, 1,  50400, '[]',              0, None,              None        ),
    ("노르무스",             64800, 1,  50400, '[]',              0, None,              None        ),
    ("우칸바",               64800, 1,  50400, '[]',              0, None,              None        ),
    ("모데우스",             86400, 1,  50400, '[]',              0, None,              None        ),
    ("바론",                 86400, 1,  50400, '[]',              0, None,              None        ),
    ("발락",                 86400, 1,  21600, '[]',              0, None,              None        ),
    ("오르펜",               86400, 1,  50400, '[]',              0, None,              None        ),
    ("올크스",               86400, 1,  50400, '[]',              0, None,              None        ),
    ("샤디스",               86400, 1,  36000, '[]',              0, None,              None        ),
    ("사이락스",             86400, 1,  21600, '[]',              0, None,              None        ),
    ("피닉스",               86400, 1,  50400, '[]',              0, None,              None        ),
    ("하프",                 86400, 1,  36000, '[]',              0, None,              None        ),
    ("헤카톤",               86400, 1,  21600, '[]',              0, None,              None        ),
    ("타나토스",             86400, 1,  50400, '[]',              0, None,              None        ),
    ("라하",                118800, 1,  50400, '[]',              0, None,              None        ),
    # ── 고정 일정 (fixed=1) ───────────────────────────────────────────────────
    # fixed_days: 월=0, 화=1, 수=2, 목=3, 금=4, 토=5, 일=6
    ("타이런트",               None, 0,      0, '[]',              1, "2",               "22:30"     ),
    ("셀리호든",               None, 0,      0, '[]',              1, "4",               "19:00"     ),
    ("월드 보스",              None, 0,      0, '[]',              1, "0,1,2,3,4,5,6",   "12:00,20:00"),
    ("오만/신념의 탑 보스",    None, 0,      0, '[]',              1, "0,1,2,3,4,5,6",   "19:00"     ),
]


async def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        # 다중 프로세스(컨테이너별 봇) 동시 접근을 위해 WAL 모드 활성화
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.executescript(_CREATE_SQL)
        # 컬럼 마이그레이션 (이미 존재하면 무시)
        for sql in [
            "ALTER TABLE bosses ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE bosses ADD COLUMN fixed_days TEXT",
            "ALTER TABLE bosses ADD COLUMN fixed_time TEXT",
            "ALTER TABLE schedules ADD COLUMN warned_5min INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE schedules ADD COLUMN warned_1min INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE contributions ADD COLUMN actor_type TEXT NOT NULL DEFAULT 'discord'",
            "ALTER TABLE contributions ADD COLUMN actor_ref TEXT",
            "ALTER TABLE web_broadcast_event ADD COLUMN bot_number INTEGER NOT NULL DEFAULT 3",
            "ALTER TABLE web_broadcast_event ADD COLUMN event_type TEXT NOT NULL DEFAULT 'generic'",
            "ALTER TABLE web_broadcast_event ADD COLUMN boss_name TEXT",
            "ALTER TABLE component_action_claim ADD COLUMN bot_number INTEGER NOT NULL DEFAULT 3",
        ]:
            try:
                await db.execute(sql)
            except Exception:
                pass
        # These indexes must be created after the additive column migrations.
        # Otherwise an existing pre-M44 table would fail CREATE_SQL before its
        # missing bot_number column could be added.
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_web_broadcast_event_bot_guild_cursor
               ON web_broadcast_event (bot_number, guild_id, cursor)""",
        )
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_component_action_claim_bot_message
               ON component_action_claim (bot_number, guild_id, message_id, updated_at)""",
        )
        await db.commit()

    # 전체 길드 보스 데이터 마이그레이션
    await _migrate_all_guilds()


async def _migrate_all_guilds() -> None:
    """기존 모든 길드에 보스 데이터 마이그레이션 적용"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT guild_id, bot_number FROM guild_config") as cur:
            configs = await cur.fetchall()

    for guild_id, bot_number in configs:
        await ensure_default_bosses(guild_id, bot_number)


async def ensure_default_bosses(guild_id: int, bot_number: int) -> None:
    """길드 기본 보스 삽입 및 데이터 최신화 (이름 변경, 필드 업데이트 포함)"""
    async with aiosqlite.connect(DB_PATH) as db:
        # ── 이름 변경 마이그레이션 ─────────────────────────────────────────────
        for old_name, new_name in _BOSS_RENAMES.items():
            await db.execute(
                "UPDATE bosses SET name=? WHERE guild_id=? AND bot_number=? AND name=? AND is_default=1",
                (new_name, guild_id, bot_number, old_name),
            )
            # 관련 예약도 함께 업데이트
            await db.execute(
                "UPDATE schedules SET boss_name=?, content=? "
                "WHERE guild_id=? AND bot_number=? AND boss_name=? AND content=?",
                (new_name, new_name, guild_id, bot_number, old_name, old_name),
            )

        # ── 신규 삽입 + 기존 구조 필드 업데이트 ──────────────────────────────
        for b in DEFAULT_BOSSES:
            name, respawn_sec, spawns_on_open, open_delay_sec, aliases, fixed, fixed_days, fixed_time = b
            await db.execute(
                """INSERT OR IGNORE INTO bosses
                   (guild_id, bot_number, name, aliases, respawn_seconds,
                    spawns_on_open, open_delay_seconds, fixed, fixed_days, fixed_time, is_default)
                   VALUES (?,?,?,?,?,?,?,?,?,?,1)""",
                (guild_id, bot_number, name, aliases, respawn_sec,
                 spawns_on_open, open_delay_sec, fixed, fixed_days, fixed_time),
            )
            await db.execute(
                """UPDATE bosses SET
                   aliases=?, respawn_seconds=?, spawns_on_open=?, open_delay_seconds=?,
                   fixed=?, fixed_days=?, fixed_time=?
                   WHERE guild_id=? AND bot_number=? AND name=? AND is_default=1""",
                (aliases, respawn_sec, spawns_on_open, open_delay_sec,
                 fixed, fixed_days, fixed_time,
                 guild_id, bot_number, name),
            )

        await db.commit()


@asynccontextmanager
async def get_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout=5000")
        yield db


def _broadcast_json(value: object) -> str:
    """Serialize bridge payloads consistently before storing them in SQLite."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode_broadcast_json(value: str | None, fallback: object) -> object:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        # A malformed legacy row must not make the whole guest feed unavailable.
        return fallback


def _validate_bridge_bot_number(bot_number: int) -> int:
    if type(bot_number) is not int or not 1 <= bot_number <= 4:
        raise ValueError("bot_number must be 1-4")
    return bot_number


def _scoped_bridge_key(bot_number: int, key: str) -> str:
    """Keep globally unique legacy key columns isolated without a table rebuild."""
    return f"bot:{bot_number:03d}:{key}"


async def append_web_broadcast_event(
    *,
    event_key: str,
    bot_number: int = 3,
    guild_id: int,
    channel_id: int,
    message_id: int,
    kind: str,
    content: str | None,
    embeds: list[dict],
    attachments: list[dict],
    components: list[dict],
    event_type: str = "generic",
    boss_name: str | None = None,
    created_at: int | None = None,
) -> int:
    """Persist one bot-scoped broadcast event and return its stable cursor.

    ``event_key`` is derived from Discord's message identity/revision, so a
    duplicate gateway delivery returns the cursor already assigned to the
    original event rather than creating a second client-visible item.
    """
    bot_number = _validate_bridge_bot_number(bot_number)
    if kind not in {"message", "message_update", "message_delete"}:
        raise ValueError("invalid broadcast event kind")
    if event_type not in {"generic", "boss_warning_5m", "boss_warning_1m", "boss_spawn", "reservation"}:
        raise ValueError("invalid broadcast event type")
    boss_name = boss_name.strip()[:100] if isinstance(boss_name, str) and boss_name.strip() else None
    created_at = int(created_at if created_at is not None else time.time() * 1000)
    stored_event_key = _scoped_bridge_key(bot_number, event_key)
    async with get_db() as db:
        # Bot 003 may already have pre-M44 unscoped rows. Treat them as the same
        # event so a gateway replay during rollout cannot duplicate a broadcast.
        lookup_keys = (
            (stored_event_key, event_key) if bot_number == 3
            else (stored_event_key,)
        )
        placeholders = ",".join("?" for _ in lookup_keys)
        async with db.execute(
            f"""SELECT cursor FROM web_broadcast_event
                WHERE bot_number=? AND event_key IN ({placeholders})
                ORDER BY event_key=? DESC LIMIT 1""",
            (bot_number, *lookup_keys, stored_event_key),
        ) as cur:
            existing = await cur.fetchone()
        if existing is not None:
            return int(existing["cursor"])

        await db.execute(
            """INSERT OR IGNORE INTO web_broadcast_event
               (event_key, bot_number, guild_id, channel_id, message_id, kind, content,
                embeds_json, attachments_json, components_json, event_type, boss_name, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                stored_event_key,
                bot_number,
                guild_id,
                channel_id,
                message_id,
                kind,
                content,
                _broadcast_json(embeds),
                _broadcast_json(attachments),
                _broadcast_json(components),
                event_type,
                boss_name,
                created_at,
            ),
        )
        async with db.execute(
            """SELECT cursor FROM web_broadcast_event
               WHERE bot_number=? AND event_key=?""",
            (bot_number, stored_event_key),
        ) as cur:
            row = await cur.fetchone()
        if row is None:  # pragma: no cover - protects a corrupted concurrent DB only
            raise RuntimeError("broadcast event was not persisted")

        # Keep the shared bot DB bounded. Events are append-only, so trimming
        # other guilds here is safe and keeps the retention policy deterministic.
        cutoff = int(time.time() * 1000) - WEB_BROADCAST_RETENTION_MS
        await db.execute("DELETE FROM web_broadcast_event WHERE created_at < ?", (cutoff,))
        await db.execute(
            """DELETE FROM web_broadcast_event
               WHERE bot_number=? AND guild_id=? AND cursor NOT IN (
                 SELECT cursor FROM web_broadcast_event
                 WHERE bot_number=? AND guild_id=? ORDER BY cursor DESC LIMIT ?
               )""",
            (
                bot_number, guild_id, bot_number, guild_id,
                WEB_BROADCAST_MAX_EVENTS_PER_GUILD,
            ),
        )
        await db.commit()
        return int(row["cursor"])


async def list_web_broadcast_events(
    *,
    bot_number: int = 3,
    guild_id: int,
    after: int = 0,
    limit: int = 100,
    channel_id: int | None = None,
) -> tuple[list[dict], int]:
    """Return a bot-and-guild-scoped slice of the retained feed."""
    bot_number = _validate_bridge_bot_number(bot_number)
    if after < 0:
        raise ValueError("after must not be negative")
    if not 1 <= limit <= WEB_BROADCAST_MAX_EVENTS_PER_GUILD:
        raise ValueError(f"limit must be 1-{WEB_BROADCAST_MAX_EVENTS_PER_GUILD}")

    async with get_db() as db:
        # Reads enforce retention too, so a quiet guild cannot expose stale rows
        # that have not yet had a subsequent insert to trigger cleanup.
        where = "bot_number=? AND guild_id=? AND created_at>=?"
        base_params: tuple[int, ...] = (
            bot_number,
            guild_id,
            int(time.time() * 1000) - WEB_BROADCAST_RETENTION_MS,
        )
        if channel_id is not None:
            where += " AND channel_id=?"
            base_params += (channel_id,)
        if after == 0:
            query = f"""SELECT * FROM (
                SELECT * FROM web_broadcast_event
                WHERE {where} ORDER BY cursor DESC LIMIT ?
            ) ORDER BY cursor ASC"""
            params = base_params + (limit,)
        else:
            query = f"""SELECT * FROM web_broadcast_event
                       WHERE {where} AND cursor>? ORDER BY cursor ASC LIMIT ?"""
            params = base_params + (after, limit)
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()

    events = [
        {
            "cursor": int(row["cursor"]),
            "messageId": str(row["message_id"]),
            "kind": row["kind"],
            "botNumber": int(row["bot_number"]),
            "guildId": str(row["guild_id"]),
            "channelId": str(row["channel_id"]),
            "content": row["content"],
            "embeds": _decode_broadcast_json(row["embeds_json"], []),
            "attachments": _decode_broadcast_json(row["attachments_json"], []),
            "components": _decode_broadcast_json(row["components_json"], []),
            "eventType": row["event_type"],
            "bossName": row["boss_name"],
            "createdAt": int(row["created_at"]),
        }
        for row in rows
    ]
    next_cursor = int(events[-1]["cursor"]) if events else after
    return events, next_cursor


async def has_web_broadcast_message(
    *, bot_number: int = 3, guild_id: int, channel_id: int, message_id: int,
) -> bool:
    """Whether a prior mirrored message can safely receive a raw-delete tombstone."""
    bot_number = _validate_bridge_bot_number(bot_number)
    async with get_db() as db:
        async with db.execute(
            """SELECT 1 FROM web_broadcast_event
               WHERE bot_number=? AND guild_id=? AND channel_id=? AND message_id=?
                 AND kind IN ('message', 'message_update')
               LIMIT 1""",
            (bot_number, guild_id, channel_id, message_id),
        ) as cur:
            return await cur.fetchone() is not None


async def claim_component_action(
    *,
    idempotency_key: str,
    request_id: str,
    bot_number: int = 3,
    guild_id: int,
    channel_id: int,
    message_id: int,
    custom_id: str,
    action_key: str,
    actor_type: str,
    actor_ref: str | None,
) -> str:
    """Claim an action exactly once until its handler reports a retryable failure.

    The insert/update happens under ``BEGIN IMMEDIATE`` so separate bot
    processes cannot both begin a mutually-exclusive cut/miss operation. A
    succeeded claim remains terminal; a failed claim is auditable and may be
    retried with a new request ID.
    """
    bot_number = _validate_bridge_bot_number(bot_number)
    now_ms = int(time.time() * 1000)
    stored_key = _scoped_bridge_key(bot_number, idempotency_key)
    lookup_keys = (
        (stored_key, idempotency_key) if bot_number == 3
        else (stored_key,)
    )
    placeholders = ",".join("?" for _ in lookup_keys)
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                f"""SELECT idempotency_key, status FROM component_action_claim
                    WHERE bot_number=? AND idempotency_key IN ({placeholders})
                    ORDER BY idempotency_key=? DESC LIMIT 1""",
                (bot_number, *lookup_keys, stored_key),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                await db.execute(
                    """INSERT INTO component_action_claim
                       (idempotency_key, request_id, bot_number, guild_id, channel_id, message_id,
                        custom_id, action_key, actor_type, actor_ref, status,
                        attempt_count, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing', 1, ?, ?)""",
                    (
                        stored_key, request_id, bot_number, guild_id, channel_id, message_id,
                        custom_id, action_key, actor_type, actor_ref, now_ms, now_ms,
                    ),
                )
                state = "claimed"
            elif row["status"] == "succeeded":
                state = "succeeded"
            elif row["status"] == "processing":
                state = "in_progress"
            else:
                await db.execute(
                    """UPDATE component_action_claim
                       SET request_id=?, custom_id=?, action_key=?, actor_type=?, actor_ref=?,
                           status='processing', error_code=NULL, updated_at=?, completed_at=NULL,
                           attempt_count=attempt_count+1
                       WHERE idempotency_key=? AND bot_number=?""",
                    (
                        request_id, custom_id, action_key, actor_type, actor_ref,
                        now_ms, row["idempotency_key"], bot_number,
                    ),
                )
                state = "claimed"
            await db.commit()
            return state
        except Exception:
            await db.rollback()
            raise


async def complete_component_action_claim(
    idempotency_key: str,
    *,
    bot_number: int = 3,
    status: str,
    error_code: str | None = None,
) -> None:
    """Finalize a claim without storing exception text or sensitive input."""
    if status not in {"succeeded", "failed"}:
        raise ValueError("component action claim must finish succeeded or failed")
    bot_number = _validate_bridge_bot_number(bot_number)
    now_ms = int(time.time() * 1000)
    stored_key = _scoped_bridge_key(bot_number, idempotency_key)
    lookup_keys = (
        (stored_key, idempotency_key) if bot_number == 3
        else (stored_key,)
    )
    placeholders = ",".join("?" for _ in lookup_keys)
    async with get_db() as db:
        await db.execute(
            f"""UPDATE component_action_claim
               SET status=?, error_code=?, updated_at=?, completed_at=?
               WHERE bot_number=? AND idempotency_key IN ({placeholders})
                 AND status='processing'""",
            (status, error_code, now_ms, now_ms, bot_number, *lookup_keys),
        )
        await db.commit()


async def get_component_action_claim_status(
    idempotency_key: str, *, bot_number: int = 3,
) -> str | None:
    """Read a terminal claim for disabled-button reconciliation."""
    bot_number = _validate_bridge_bot_number(bot_number)
    stored_key = _scoped_bridge_key(bot_number, idempotency_key)
    lookup_keys = (
        (stored_key, idempotency_key) if bot_number == 3
        else (stored_key,)
    )
    placeholders = ",".join("?" for _ in lookup_keys)
    async with get_db() as db:
        async with db.execute(
            f"""SELECT status FROM component_action_claim
                WHERE bot_number=? AND idempotency_key IN ({placeholders})
                ORDER BY idempotency_key=? DESC LIMIT 1""",
            (bot_number, *lookup_keys, stored_key),
        ) as cur:
            row = await cur.fetchone()
    return str(row["status"]) if row is not None else None
