"""Serialized, side-effect-free reconciliation for pending boss schedules."""
from __future__ import annotations

from datetime import datetime, timedelta

from src.db import DEFAULT_BOSSES, _BOSS_RENAMES, get_db


SCHEDULE_LATE_DELIVERY_GRACE_SECONDS = 15


def _next_fixed_occurrence(
    days_str: str,
    times_str: str,
    reference: datetime,
) -> datetime | None:
    days = [int(day) for day in days_str.split(",")]
    times = [tuple(map(int, value.split(":"))) for value in times_str.split(",")]
    candidates: list[datetime] = []
    for offset in range(8):
        day = reference + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        for hour, minute in times:
            candidate = day.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            if candidate > reference:
                candidates.append(candidate)
    return min(candidates) if candidates else None


async def sync_default_bosses_in_transaction(
    db,
    guild_id: int,
    bot_number: int,
) -> None:
    """Synchronize default definitions on an existing caller transaction."""
    for old_name, new_name in _BOSS_RENAMES.items():
        await db.execute(
            "UPDATE bosses SET name=? "
            "WHERE guild_id=? AND bot_number=? AND name=? AND is_default=1",
            (new_name, guild_id, bot_number, old_name),
        )
        await db.execute(
            "UPDATE schedules SET boss_name=?, content=? "
            "WHERE guild_id=? AND bot_number=? AND boss_name=? AND content=?",
            (new_name, new_name, guild_id, bot_number, old_name, old_name),
        )

    for definition in DEFAULT_BOSSES:
        (
            name,
            respawn_seconds,
            spawns_on_open,
            open_delay_seconds,
            aliases,
            fixed,
            fixed_days,
            fixed_time,
        ) = definition
        await db.execute(
            """INSERT OR IGNORE INTO bosses
               (guild_id, bot_number, name, aliases, respawn_seconds,
                spawns_on_open, open_delay_seconds, fixed, fixed_days,
                fixed_time, is_default)
               VALUES (?,?,?,?,?,?,?,?,?,?,1)""",
            (
                guild_id,
                bot_number,
                name,
                aliases,
                respawn_seconds,
                spawns_on_open,
                open_delay_seconds,
                fixed,
                fixed_days,
                fixed_time,
            ),
        )
        await db.execute(
            """UPDATE bosses SET aliases=?, respawn_seconds=?,
                      spawns_on_open=?, open_delay_seconds=?, fixed=?,
                      fixed_days=?, fixed_time=?
               WHERE guild_id=? AND bot_number=? AND name=? AND is_default=1""",
            (
                aliases,
                respawn_seconds,
                spawns_on_open,
                open_delay_seconds,
                fixed,
                fixed_days,
                fixed_time,
                guild_id,
                bot_number,
                name,
            ),
        )


async def reconcile_schedules_in_transaction(
    db,
    *,
    bot_number: int,
    recovery_at: datetime,
    guild_id: int | None = None,
) -> dict[str, int]:
    """Quietly reconcile stale rows using the caller's write transaction."""
    cutoff = recovery_at - timedelta(
        seconds=SCHEDULE_LATE_DELIVERY_GRACE_SECONDS,
    )
    scope_sql = " AND guild_id=?" if guild_id is not None else ""
    scope_params: tuple[int, ...] = (guild_id,) if guild_id is not None else ()
    stats = {
        "expired": 0,
        "normalCreated": 0,
        "fixedCreated": 0,
        "duplicates": 0,
    }

    expired = await db.execute(
        "UPDATE schedules SET warned_5min=1, warned_1min=1, notified=1, "
        "delivery_retry_after=NULL "
        "WHERE bot_number=? AND notified=0 AND scheduled_at < ?" + scope_sql,
        (bot_number, cutoff.isoformat()) + scope_params,
    )
    stats["expired"] = max(expired.rowcount, 0)

    boss_scope = " AND b.guild_id=?" if guild_id is not None else ""
    async with db.execute(
        """SELECT b.guild_id, b.name, b.respawn_seconds, b.fixed,
                  b.fixed_days, b.fixed_time
           FROM bosses b
           JOIN guild_config gc
             ON b.guild_id=gc.guild_id AND b.bot_number=gc.bot_number
           WHERE b.bot_number=?""" + boss_scope,
        (bot_number,) + scope_params,
    ) as cursor:
        bosses = [dict(row) async for row in cursor]

    async with db.execute(
        """SELECT id, guild_id, boss_name, scheduled_at
           FROM schedules
           WHERE bot_number=? AND notified=0 AND boss_name IS NOT NULL
             AND scheduled_at>=?""" + scope_sql + " ORDER BY scheduled_at, id",
        (bot_number, cutoff.isoformat()) + scope_params,
    ) as cursor:
        pending_rows = [dict(row) async for row in cursor]

    pending_by_boss: dict[tuple[int, str], list[dict]] = {}
    for row in pending_rows:
        pending_by_boss.setdefault((row["guild_id"], row["boss_name"]), []).append(row)

    for rows in pending_by_boss.values():
        for duplicate in rows[1:]:
            await db.execute("DELETE FROM schedules WHERE id=?", (duplicate["id"],))
            stats["duplicates"] += 1

    history_scope = " AND s.guild_id=?" if guild_id is not None else ""
    async with db.execute(
        """SELECT s.guild_id, s.boss_name, s.scheduled_at, s.miss_count
           FROM schedules s
           JOIN bosses b
             ON s.guild_id=b.guild_id AND s.bot_number=b.bot_number
            AND s.boss_name=b.name
           WHERE s.bot_number=? AND s.notified=1
             AND s.boss_name IS NOT NULL AND b.fixed=0
             AND s.scheduled_at<=?""" + history_scope
        + " ORDER BY s.scheduled_at DESC, s.id DESC",
        (bot_number, recovery_at.isoformat()) + scope_params,
    ) as cursor:
        notified_rows = [dict(row) async for row in cursor]

    latest_notified: dict[tuple[int, str], dict] = {}
    for row in notified_rows:
        latest_notified.setdefault((row["guild_id"], row["boss_name"]), row)

    for boss in bosses:
        key = (boss["guild_id"], boss["name"])
        if pending_by_boss.get(key):
            continue

        if boss["fixed"]:
            if not boss["fixed_days"] or not boss["fixed_time"]:
                continue
            next_at = _next_fixed_occurrence(
                boss["fixed_days"],
                boss["fixed_time"],
                recovery_at,
            )
            if next_at is None:
                continue
            await db.execute(
                """INSERT INTO schedules
                   (guild_id, bot_number, boss_name, content, scheduled_at, is_fixed)
                   VALUES (?,?,?,?,?,1)""",
                (
                    boss["guild_id"],
                    bot_number,
                    boss["name"],
                    boss["name"],
                    next_at.isoformat(),
                ),
            )
            stats["fixedCreated"] += 1
            continue

        previous = latest_notified.get(key)
        respawn_seconds = boss["respawn_seconds"]
        if previous is None or not respawn_seconds:
            continue
        new_at = datetime.fromisoformat(previous["scheduled_at"])
        new_miss = int(previous["miss_count"] or 0)
        while new_at <= recovery_at:
            new_at += timedelta(seconds=respawn_seconds)
            new_miss += 1
        await db.execute(
            """INSERT INTO schedules
               (guild_id, bot_number, boss_name, content, scheduled_at, miss_count)
               VALUES (?,?,?,?,?,?)""",
            (
                boss["guild_id"],
                bot_number,
                boss["name"],
                boss["name"],
                new_at.isoformat(),
                new_miss,
            ),
        )
        stats["normalCreated"] += 1

    return stats


async def reconcile_schedules(
    *,
    bot_number: int,
    recovery_at: datetime,
    guild_id: int | None = None,
) -> dict[str, int]:
    """Run reconciliation under a serialized write lock."""
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            stats = await reconcile_schedules_in_transaction(
                db,
                bot_number=bot_number,
                recovery_at=recovery_at,
                guild_id=guild_id,
            )
            await db.commit()
            return stats
        except Exception:
            await db.rollback()
            raise
