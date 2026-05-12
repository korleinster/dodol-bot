import os
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
    notified     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""

# (이름, 리스폰 초)
DEFAULT_BOSSES: list[tuple[str, int]] = [
    ("펠리스",         7200),
    ("바실라",         9000),
    ("판나로드",      10800),
    ("체르투바",      10800),
    ("템페스트",      12600),
    ("엔쿠라",        12600),
    ("스탄",          14400),
    ("브래카",        14400),
    ("마투라",        14400),
    ("트롬바",        16200),
    ("탈킨",          18000),
    ("레피로",        18000),
    ("티미트리스",    18000),
    ("히실로메",      21600),
    ("가레스",        21600),
    ("여왕개미",      21600),
    ("켈소스",        21600),
    ("베히모스",      21600),
    ("탈라킨",        25200),
    ("사르카",        25200),
    ("메두사",        25200),
    ("셀루",          27000),
    ("글라키",        28800),
    ("크루마",        28800),
    ("티미니엘",      28800),
    ("발보",          28800),
    ("판드라이드",    28800),
    ("란도르",        28800),
    ("오염된크루마",  28800),
    ("플린트",        28800),
    ("카탄",          28800),
    ("코룬",          36000),
    ("나이아스",      43200),
    ("드래곤비스트",  43200),
    ("망각의거울",    43200),
    ("무프",          43200),
    ("카브리오",      43200),
    ("안드라스",      43200),
    ("사반",          43200),
    ("사무엘",        43200),
    ("코어수스켑터",  43200),
    ("블랙릴리",      43200),
    ("실라",          43200),
    ("노르무스",      64800),
    ("우칸바",        64800),
    ("모데우스",      86400),
    ("바론",          86400),
    ("발락",          86400),
    ("오르펜",        86400),
    ("올크스",        86400),
    ("샤디스",        86400),
    ("사이락스",      86400),
    ("피닉스",        86400),
    ("하프",          86400),
    ("헤카톤",        86400),
    ("타나토스",      86400),
    ("라하",         118800),
]


async def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_CREATE_SQL)
        # 기존 DB 마이그레이션: is_default 컬럼 추가
        try:
            await db.execute("ALTER TABLE bosses ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass  # 이미 존재
        await db.commit()


async def ensure_default_bosses(guild_id: int, bot_number: int) -> None:
    """길드에 기본 보스가 없으면 삽입 (이미 있는 이름은 스킵)."""
    async with aiosqlite.connect(DB_PATH) as db:
        for name, respawn_sec in DEFAULT_BOSSES:
            await db.execute(
                """INSERT OR IGNORE INTO bosses
                   (guild_id, bot_number, name, aliases, respawn_seconds, is_default)
                   VALUES (?, ?, ?, '[]', ?, 1)""",
                (guild_id, bot_number, name, respawn_sec),
            )
        await db.commit()


@asynccontextmanager
async def get_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db
