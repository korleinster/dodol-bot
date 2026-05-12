import os
import aiosqlite
from pathlib import Path

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
    notified     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


async def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_CREATE_SQL)
        await db.commit()


def get_db() -> aiosqlite.Connection:
    return aiosqlite.connect(DB_PATH)
