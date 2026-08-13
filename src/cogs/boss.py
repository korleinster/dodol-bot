"""보스 등록/관리 + 컷/멍/예약/오픈타임"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

import discord
from discord.ext import commands, tasks

from src.component_actions import (
    COMPONENT_ACTIONS,
    LEGACY_COMPONENT_PREFIXES,
    ComponentActionDispatcher,
    RegisteredComponentAction,
    boss_component_custom_id,
)
from src.db import get_db
from src.korean import boss_matches, normalize_time
from src.schedule_recovery import (
    SCHEDULE_LATE_DELIVERY_GRACE_SECONDS,
    reconcile_schedules,
)

CUT_KW   = {"컷", "ㅋ", "cut"}
MISS_KW  = {"멍", "ㅁ"}
SPAWN_KW = {"젠", "스폰", "spawn", "ㅈ"}

# DiscordServerError is an explicit 5xx response: Discord did not accept the
# message, so the scheduler can safely retry. Timeouts and transport failures
# are intentionally not retried because Discord may already have accepted the
# request and a duplicate boss/TTS alert is worse than an observable unknown.
SCHEDULER_DELIVERY_RETRY_DELAYS = (5, 15, 30, 60, 120)
SCHEDULER_DELIVERY_MAX_RETRIES = len(SCHEDULER_DELIVERY_RETRY_DELAYS)
# Do not close an incident on one lucky 1-second tick. This prevents an
# intermittent upstream failure from reopening and notifying every few seconds.
SCHEDULER_RECOVERY_SUCCESS_TICKS = 30


def scheduler_delivery_retry_delay(attempt: int) -> int:
    """Return the bounded retry delay for a 1-based Discord 5xx attempt."""
    index = max(0, min(int(attempt) - 1, len(SCHEDULER_DELIVERY_RETRY_DELAYS) - 1))
    return SCHEDULER_DELIVERY_RETRY_DELAYS[index]


# ── 시간 파싱 헬퍼 ────────────────────────────────────────────────────────────

def parse_hms(text: str) -> int | None:
    """'2:00:00' → 7200 초 (하위 호환용)"""
    m = re.fullmatch(r"(\d+):(\d{2}):(\d{2})", text)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return None


def parse_boss_time(text: str) -> int | None:
    """'3:00' / '0300' / '0330' → seconds. 초 단위 없음.
    H:MM, HH:MM, HHMM 형식 지원.
    """
    m = re.fullmatch(r"(\d{1,3}):(\d{2})", text)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        if mn < 60:
            return h * 3600 + mn * 60
    m = re.fullmatch(r"(\d{2})(\d{2})", text)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        if mn < 60:
            return h * 3600 + mn * 60
    return None


def fmt_seconds(sec: int) -> str:
    h, r = divmod(sec, 3600)
    m = r // 60
    return f"{h}:{m:02d}"


def fmt_remain(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    if total <= 0:
        return "출현 중"
    h, r = divmod(total, 3600)
    m = r // 60
    if h:
        return f"{h}시간 {m}분 후"
    elif m:
        return f"{m}분 후"
    else:
        return "출현 중"


def format_schedule_tts(content: str, miss_count: int, remain: str) -> str:
    """Build a compact exact-time alert without repeating miss text."""
    miss = f" 미입력 {miss_count}회" if miss_count > 0 else ""
    return f"{content}{miss} {remain}"


def now() -> datetime:
    return datetime.now(KST).replace(tzinfo=None)


def next_fixed_occurrence(
    days_str: str,
    times_str: str,
    reference: datetime | None = None,
) -> datetime | None:
    """요일+시각 기반으로 다음 등장 시각 계산.
    days_str : "2" 또는 "0,1,2,3,4,5,6" (월=0, 일=6)
    times_str: "22:30" 또는 "12:00,20:00"
    """
    n = reference or now()
    days  = [int(d) for d in days_str.split(",")]
    times = [(int(t.split(":")[0]), int(t.split(":")[1])) for t in times_str.split(",")]
    candidates = []
    for offset in range(8):          # 오늘 포함 최대 8일 탐색
        dt = n + timedelta(days=offset)
        if dt.weekday() in days:
            for h, m in times:
                candidate = dt.replace(hour=h, minute=m, second=0, microsecond=0)
                if candidate > n:
                    candidates.append(candidate)
    return min(candidates) if candidates else None


def parse_cut_command(text: str) -> dict | None:
    """
    지원 형식 (PREFIX 제거 후):
      체르 컷 / 컷 체르 / 체르투바 컷 / 컷 체르투바
      체르 컷 0530 / 컷 체르 0530 / 20:45 체르 컷
      ㅊㄹ ㅋ / ㅋ ㅊㄹ / ㅊㄹㅂㅌ ㅋ
      체르 멍 / 멍 체르
      체르 젠 / 젠 체르 / 0000 체르 젠 / 0000 체르 스폰
    Returns {'action': 'cut'|'miss'|'spawn', 'query': str, 'time_hm': (h,m)|None}
    """
    text = text.strip()

    ALL_KW = CUT_KW | MISS_KW | SPAWN_KW

    # 앞 시각 '20:45 ...' 또는 '2045 ...'
    time_hm = None
    m = re.match(r"^(\d{2}:?\d{2})\s+(.+)$", text)
    if m:
        time_hm = normalize_time(m.group(1))
        text = m.group(2).strip()

    # 뒤 시각 '... 0530'
    if time_hm is None:
        m = re.search(r"\s+(\d{4})$", text)
        if m:
            time_hm = normalize_time(m.group(1))
            if time_hm:
                text = text[:m.start()].strip()

    parts = text.split()
    if not parts:
        return None

    def _action(kw: str) -> str:
        if kw in CUT_KW:   return "cut"
        if kw in MISS_KW:  return "miss"
        return "spawn"

    action = None
    query = None

    kw_last  = parts[-1] in ALL_KW
    kw_first = parts[0]  in ALL_KW

    if len(parts) >= 2 and kw_last:
        action = _action(parts[-1])
        query  = " ".join(parts[:-1])
    elif len(parts) >= 2 and kw_first:
        action = _action(parts[0])
        query  = " ".join(parts[1:])
    elif len(parts) == 1:
        # 공백 없는 경우: 체르투바컷, ㅊㄹㅌㅂㅋ, ㅋㅊㄹㅌㅂ
        # endswith 우선; 보스 조회 실패 시 _cmd_cut_miss에서 startswith 대안 시도 (_token 참조)
        t = parts[0]
        for kw in sorted(ALL_KW, key=len, reverse=True):
            if t != kw and t.endswith(kw):
                action = _action(kw)
                query  = t[:-len(kw)]
                return {"action": action, "query": query, "time_hm": time_hm, "_token": t}
        for kw in ("ㅋ", "ㅁ", "ㅈ"):
            if t != kw and t.startswith(kw):
                action = _action(kw)
                query  = t[len(kw):]
                return {"action": action, "query": query, "time_hm": time_hm}
        return None
    else:
        return None

    return {"action": action, "query": query.strip(), "time_hm": time_hm}


# ── 컷/멍 인라인 버튼 ─────────────────────────────────────────────────────────

class BossActionView(discord.ui.View):
    """Versioned, centrally registered actions for one boss alert."""
    def __init__(self, guild_id: int, boss_name: str, disabled: bool = False):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="✅ 컷",
            style=discord.ButtonStyle.success,
            custom_id=boss_component_custom_id("cut", guild_id, boss_name),
            disabled=disabled,
        ))
        self.add_item(discord.ui.Button(
            label="😶 멍",
            style=discord.ButtonStyle.secondary,
            custom_id=boss_component_custom_id("miss", guild_id, boss_name),
            disabled=disabled,
        ))


# ── Cog ──────────────────────────────────────────────────────────────────────

class Boss(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bn  = bot.bot_number
        self._scheduler_error_key: str | None = None
        self._scheduler_error_reported_at = 0.0
        self._scheduler_success_streak = 0
        self._scheduler_bootstrapped = False
        self.check_schedules.start()
        self.cleanup_old_schedules.start()

    def cog_unload(self):
        self.check_schedules.cancel()
        self.cleanup_old_schedules.cancel()

    def _scheduler_health(self) -> dict:
        health = getattr(self.bot, "scheduler_health", None)
        if not isinstance(health, dict):
            health = {
                "status": "starting",
                "bootstrapCompletedAt": None,
                "lastTickAt": None,
                "errorCode": None,
            }
            self.bot.scheduler_health = health
        return health

    async def _mark_scheduler_ready(self, *, bootstrap: bool = False) -> None:
        timestamp = int(time.time() * 1000)
        health = self._scheduler_health()
        was_failed = health.get("status") == "failed"
        if bootstrap:
            # Recovery is complete, but the first real notification tick still
            # has to succeed before the scheduler can truthfully report ready.
            health["bootstrapCompletedAt"] = timestamp
            health["lastTickAt"] = None
            if was_failed:
                self._scheduler_success_streak = 0
            else:
                health["status"] = "starting"
                health["errorCode"] = None
            return

        health["lastTickAt"] = timestamp
        if was_failed:
            self._scheduler_success_streak += 1
            if self._scheduler_success_streak < SCHEDULER_RECOVERY_SUCCESS_TICKS:
                return

            recovered_error = self._scheduler_error_key or health.get("errorCode")
            health["status"] = "ready"
            health["errorCode"] = None
            self._scheduler_error_key = None
            self._scheduler_error_reported_at = 0.0
            self._scheduler_success_streak = 0
            from src.utils.notify import alert
            try:
                await alert(
                    self.bot,
                    self.bn,
                    "보스 알림 스케줄러가 정상 복구되었습니다"
                    f" ({recovered_error or 'SCHEDULER_RECOVERED'}).",
                )
            except Exception as alert_error:
                print(
                    f"[뚠뚠봇{self.bn:03d}] 복구 알림 전송 실패 "
                    f"(SCHEDULER_RECOVERY_ALERT_FAILED, {type(alert_error).__name__})"
                )
        else:
            health["status"] = "ready"
            health["errorCode"] = None
            self._scheduler_success_streak = 0

    async def _mark_scheduler_failed(
        self,
        error_code: str,
        error: Exception,
        *,
        context: str,
    ) -> None:
        health = self._scheduler_health()
        incident_is_open = self._scheduler_error_key is not None
        health["status"] = "failed"
        health["errorCode"] = error_code
        self._scheduler_success_streak = 0

        # A continuous incident gets one opening alert, regardless of how its
        # exception type fluctuates. Thirty successful ticks close it later.
        if incident_is_open:
            return

        self._scheduler_error_key = error_code
        self._scheduler_error_reported_at = time.monotonic()
        print(
            f"[뚠뚠봇{self.bn:03d}] {context} "
            f"({error_code}, {type(error).__name__})"
        )
        from src.utils.notify import alert
        try:
            await alert(
                self.bot,
                self.bn,
                f"{context} ({error_code}). {self.bn:03d} 봇 상태와 스케줄러 로그를 확인해주세요.",
            )
        except Exception as alert_error:
            # Monitoring failure must never turn a recoverable scheduler error
            # into a stopped loop. Keep the message detail-free and observable.
            print(
                f"[뚠뚠봇{self.bn:03d}] 운영 알림 전송 실패 "
                f"(SCHEDULER_ALERT_FAILED, {type(alert_error).__name__})"
            )

    async def _clear_scheduler_delivery_error(self, schedule_id: int) -> None:
        async with get_db() as db:
            await db.execute(
                """UPDATE schedules
                   SET delivery_retry_after=NULL,
                       delivery_retry_count=0,
                       delivery_error_code=NULL
                   WHERE id=?""",
                (schedule_id,),
            )
            await db.commit()

    async def _record_scheduler_delivery_uncertain(
        self,
        schedule_id: int,
        error: Exception,
    ) -> None:
        """Record a non-retryable send failure without risking a duplicate."""
        async with get_db() as db:
            await db.execute(
                """UPDATE schedules
                   SET delivery_retry_after=NULL,
                       delivery_error_code='DISCORD_DELIVERY_UNCERTAIN'
                   WHERE id=?""",
                (schedule_id,),
            )
            await db.commit()
        print(
            f"[뚠뚠봇{self.bn:03d}] 보스 알림 전송 결과 불확실 "
            f"(SCHEDULER_DELIVERY_UNCERTAIN, "
            f"type={type(error).__name__}, "
            f"status={getattr(error, 'status', 'unknown')})"
        )

    async def _retry_scheduler_delivery(
        self,
        schedule_id: int,
        stage: str,
        error: Exception,
    ) -> bool:
        """Release a known Discord 5xx claim for bounded, durable retry.

        A claim remains final for ambiguous failures. This method is invoked
        only for ``discord.DiscordServerError``, where Discord explicitly
        rejected the request and retrying cannot duplicate an accepted alert.
        """
        release_sql = {
            "5m": "warned_5min=0",
            "1m": "warned_1min=0",
            # Final alerts keep the warning markers, but must become pending
            # again so the same schedule can be retried without creating a
            # duplicate auto-miss reservation.
            "spawn": "notified=0",
        }[stage]

        async with get_db() as db:
            async with db.execute(
                "SELECT delivery_retry_count, scheduled_at FROM schedules WHERE id=?",
                (schedule_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return False

            attempt = int(row[0] or 0) + 1
            if attempt > SCHEDULER_DELIVERY_MAX_RETRIES:
                await db.execute(
                    """UPDATE schedules
                       SET delivery_retry_after=NULL,
                           delivery_retry_count=?,
                           delivery_error_code='DISCORD_SERVER_ERROR_EXHAUSTED'
                       WHERE id=?""",
                    (attempt, schedule_id),
                )
                await db.commit()
                print(
                    f"[뚠뚠봇{self.bn:03d}] 보스 알림 재시도 한도 도달 "
                    f"(SCHEDULER_DELIVERY_RETRY_EXHAUSTED, stage={stage}, "
                    f"attempt={attempt}, status={getattr(error, 'status', 'unknown')})"
                )
                return False

            delay = scheduler_delivery_retry_delay(attempt)
            retry_after = now() + timedelta(seconds=delay)
            delivery_deadline = (
                datetime.fromisoformat(row["scheduled_at"])
                + timedelta(seconds=SCHEDULE_LATE_DELIVERY_GRACE_SECONDS)
            )
            if retry_after > delivery_deadline:
                await db.execute(
                    """UPDATE schedules
                       SET delivery_retry_after=NULL,
                           delivery_retry_count=?,
                           delivery_error_code='DISCORD_SERVER_ERROR_WINDOW_EXPIRED'
                       WHERE id=?""",
                    (attempt, schedule_id),
                )
                await db.commit()
                print(
                    f"[뚠뚠봇{self.bn:03d}] 보스 알림 재시도 유예 만료 "
                    f"(SCHEDULER_DELIVERY_WINDOW_EXPIRED, stage={stage}, "
                    f"attempt={attempt}, status={getattr(error, 'status', 'unknown')})"
                )
                return False
            await db.execute(
                f"""UPDATE schedules
                   SET {release_sql},
                       delivery_retry_after=?,
                       delivery_retry_count=?,
                       delivery_error_code='DISCORD_SERVER_ERROR'
                   WHERE id=?""",
                (retry_after.isoformat(), attempt, schedule_id),
            )
            await db.commit()

        print(
            f"[뚠뚠봇{self.bn:03d}] 보스 알림 Discord 5xx 재시도 예약 "
            f"(SCHEDULER_DELIVERY_RETRY, stage={stage}, attempt={attempt}, "
            f"retry_after={retry_after.isoformat()}, "
            f"status={getattr(error, 'status', 'unknown')})"
        )
        return True

    # ── 채널 가드 ─────────────────────────────────────────

    async def get_text_channel(self, guild_id: int) -> int | None:
        async with get_db() as db:
            async with db.execute(
                "SELECT text_channel_id FROM guild_config WHERE guild_id=? AND bot_number=?",
                (guild_id, self.bn),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

    # ── 이벤트 ────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Dispatch registered buttons after acknowledging the Discord click."""
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id", "")
        if not isinstance(custom_id, str):
            return
        guild_id = getattr(getattr(interaction, "guild", None), "id", None)
        message = getattr(interaction, "message", None)
        message_id = getattr(message, "id", None)
        if guild_id is None or message_id is None:
            return
        action = COMPONENT_ACTIONS.resolve(custom_id, int(guild_id), int(message_id))
        if action is None and not custom_id.startswith(LEGACY_COMPONENT_PREFIXES):
            # Another cog may own this component. Unregistered buttons are
            # never executed from this cog.
            return

        response = getattr(interaction, "response", None)
        if response is not None and not response.is_done():
            # A component interaction still needs an acknowledgement within
            # Discord's response window.  A deferred message update is silent
            # to the user; the dispatcher updates the source message itself.
            await response.defer(thinking=False)
        member_permissions = getattr(getattr(interaction, "user", None), "guild_permissions", None)
        interaction_permissions = getattr(interaction, "permissions", None)
        is_admin = bool(
            getattr(member_permissions, "administrator", False)
            or getattr(interaction_permissions, "administrator", False)
        )
        actor = interaction.user
        result = await ComponentActionDispatcher(self.bot).dispatch(
            request_id=f"discord:{getattr(interaction, 'id', message_id)}",
            guild_id=int(guild_id),
            message_id=int(message_id),
            custom_id=custom_id,
            actor=actor,
            actor_type="discord",
            actor_ref=f"discord:{getattr(actor, 'id', 'unknown')}",
            is_owner=is_admin,
            message=message,
        )
        followup = getattr(interaction, "followup", None)
        if followup is None:
            return
        if result.status == "already_processed":
            await followup.send("이미 처리된 보스 알림입니다.", ephemeral=True)
        elif not result.succeeded:
            await followup.send(result.error_message or "버튼 처리에 실패했습니다.", ephemeral=True)

    async def execute_registered_component(
        self,
        *,
        action: RegisteredComponentAction,
        message: discord.Message,
        actor: discord.User | discord.Member,
    ) -> discord.Embed | str:
        """The common dispatcher calls this for every registered boss action."""
        try:
            guild_id = int(action.params["guild_id"])
            boss_name = action.params["boss_name"]
        except (KeyError, TypeError, ValueError):
            return "❌ 실행할 보스 정보를 확인할 수 없습니다."
        if message.guild is None or int(message.guild.id) != guild_id:
            return "❌ 다른 서버의 보스 알림은 처리할 수 없습니다."
        action_name = "cut" if action.key == "boss_cut" else "miss"
        return await self._do_cut_miss(guild_id, boss_name, action_name, user=actor)

    async def disable_registered_component(
        self,
        *,
        message: discord.Message,
        action: RegisteredComponentAction,
    ) -> discord.Message:
        """Disable the entire mutually-exclusive boss action group on success."""
        guild_id = int(action.params["guild_id"])
        boss_name = action.params["boss_name"]
        return await message.edit(view=BossActionView(guild_id, boss_name, disabled=True))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        content = message.content.strip()
        if not content:
            return

        # 소환된 채널에서만 동작 (미배치 봇은 침묵)
        assigned = await self.get_text_channel(message.guild.id)
        if not assigned or message.channel.id != assigned:
            return

        cmd = content
        try:
            await self._dispatch(message, cmd)
        except Exception as e:
            import traceback
            traceback.print_exc()
            if getattr(message.author, "actor_type", "discord") == "web_guest":
                raise RuntimeError("Boss command failed") from e
            await message.channel.send(f"⚠️ 오류: {e}")

    # ── 명령 라우터 ───────────────────────────────────────

    async def _dispatch(self, message: discord.Message, cmd: str):
        # 멀티라인 일괄 처리
        lines = [l.strip() for l in cmd.splitlines() if l.strip()]
        if len(lines) >= 2:
            await self._cmd_bulk_record(message, lines)
            return

        parts = cmd.split()
        if not parts:
            return
        head = parts[0].lower()

        # 보스 목록
        if head in ("보스", "보스목록", "보스리스트") and len(parts) == 1:
            await self._cmd_boss_list(message)
            return

        # 자동예약
        if head == "자동예약":
            await self._cmd_auto_schedule(message, parts[1:])
            return

        # 보탐 / 보탐+
        if head in ("보탐", "보탐+", "ㅂㅌ", "ㅂㅌ+"):
            await self._cmd_botam(message, include_fixed="+" in head)
            return

        # 예약 전체 초기화
        if head in ("전체삭제", "초기화", "보스전체삭제"):
            await self._cmd_botam_reset(message)
            return

        # 기여 랭킹
        if head in ("기여자", "보탐러", "기여랭킹") and len(parts) == 1:
            await self._cmd_contributions(message)
            return

        # ㅋ/ㅋ+ (보탐 단축) — 단독 입력 시만
        if head in ("ㅋ", "ㅋ+") and len(parts) == 1:
            await self._cmd_botam(message, include_fixed="+" in head)
            return

        # z/Z (가까운 5건 보탐) / z+/Z+ (전체 보탐)
        if head in ("z", "Z", "z+", "Z+") and len(parts) == 1:
            if "+" in head:
                await self._cmd_botam(message, include_fixed=True)
            else:
                await self._cmd_botam(message, include_fixed=False)
            return

        # 서버오픈: "05:00 서버오픈" / "서버오픈 05:00" / "오픈 05:00"
        time_raw = None
        m = re.match(r"^(\d{2}:?\d{2})\s+서버오픈$", cmd)
        if m:
            time_raw = m.group(1)
        else:
            m2 = re.match(r"^(?:서버오픈|오픈)\s+(\d{2}:?\d{2})$", cmd)
            if m2:
                time_raw = m2.group(1)
        if time_raw:
            await self._cmd_server_open(message, time_raw)
            return

        # 컷/멍 명령 파싱
        parsed = parse_cut_command(cmd)
        if parsed:
            await self._cmd_cut_miss(message, parsed)
            return

        # HH:MM 내용 — 보스 이름이면 젠 처리, 아니면 임의 예약
        m = re.match(r"^(\d{2}:?\d{2})\s+(.+)$", cmd)
        if m:
            hm = normalize_time(m.group(1))
            if hm:
                content = m.group(2).strip()
                candidates = await self._find_bosses(message.guild.id, content)
                if len(candidates) > 1:
                    await self._ambiguous_reply(message.channel, candidates)
                    return
                if candidates:
                    result = await self._do_cut_miss(message.guild.id, content, "spawn", hm)
                    if isinstance(result, str):
                        await message.channel.send(result)
                    else:
                        await message.channel.send(embed=result)
                    return
                await self._cmd_schedule_custom(message, hm, content)
                return

        # 보스명 HH:MM — 보스 이름이면 젠 처리
        m = re.match(r"^(.+?)\s+(\d{2}:?\d{2})$", cmd)
        if m:
            hm = normalize_time(m.group(2))
            if hm:
                content = m.group(1).strip()
                candidates = await self._find_bosses(message.guild.id, content)
                if len(candidates) > 1:
                    await self._ambiguous_reply(message.channel, candidates)
                    return
                if candidates:
                    result = await self._do_cut_miss(message.guild.id, content, "spawn", hm)
                    if isinstance(result, str):
                        await message.channel.send(result)
                    else:
                        await message.channel.send(embed=result)
                    return


    # ── .보스 ─────────────────────────────────────────────

    async def _cmd_boss_list(self, message: discord.Message):
        _DAY = {"0":"월","1":"화","2":"수","3":"목","4":"금","5":"토","6":"일"}

        async with get_db() as db:
            async with db.execute(
                "SELECT name, respawn_seconds, spawns_on_open, open_delay_seconds, "
                "fixed, fixed_days, fixed_time "
                "FROM bosses WHERE guild_id=? AND bot_number=? "
                "ORDER BY fixed DESC, COALESCE(respawn_seconds,0) ASC, name ASC",
                (message.guild.id, self.bn),
            ) as cur:
                rows = [dict(r) async for r in cur]

        if not rows:
            await message.channel.send("등록된 보스가 없습니다.")
            return

        lines = []
        for r in rows:
            if r["fixed"]:
                days_str = r["fixed_days"] or ""
                day_label = "매일" if days_str == "0,1,2,3,4,5,6" \
                    else "/".join(_DAY.get(d, d) for d in days_str.split(","))
                lines.append(f"고정  {r['name']}  ({day_label} {r['fixed_time']})")
            else:
                sec = r["respawn_seconds"] or 0
                suffix = ""
                if r["spawns_on_open"]:
                    delay = r["open_delay_seconds"] or 0
                    suffix = f"  🔄{'즉시' if delay == 0 else '+' + fmt_seconds(delay)}"
                lines.append(f"{fmt_seconds(sec)}  {r['name']}{suffix}")

        embed = discord.Embed(
            title="⚔️ 등록된 보스 목록",
            description="\n".join(lines),
            color=0x5865F2,
        )
        embed.set_footer(text=f"총 {len(rows)}개  |  🔄 서버 재시작 시 스폰")
        await message.channel.send(embed=embed)

    # ── .자동예약 ─────────────────────────────────────────

    async def _cmd_auto_schedule(self, message: discord.Message, args: list[str]):
        if not args:
            async with get_db() as db:
                async with db.execute(
                    "SELECT name, auto_schedule_seconds FROM bosses WHERE guild_id=? AND bot_number=? ORDER BY name",
                    (message.guild.id, self.bn),
                ) as cur:
                    rows = [dict(r) async for r in cur]
            if not rows:
                await message.channel.send("등록된 보스가 없습니다.")
                return
            lines = [f"**{r['name']}** : {fmt_seconds(r['auto_schedule_seconds'])}" for r in rows]
            embed = discord.Embed(title="⏰ 자동예약 설정", description="\n".join(lines), color=0x5865F2)
            await message.channel.send(embed=embed)
            return

        if len(args) >= 2:
            sec = parse_hms(args[0])
            boss_q = " ".join(args[1:])
            if sec is None:
                await message.channel.send("❌ 시간 형식: `시:분:초` (예: `0:10:00`)")
                return
            boss = await self._find_boss(message.guild.id, boss_q)
            if not boss:
                await message.channel.send(f"❌ **{boss_q}** 에 해당하는 보스를 찾을 수 없습니다.")
                return
            async with get_db() as db:
                await db.execute(
                    "UPDATE bosses SET auto_schedule_seconds=? WHERE guild_id=? AND bot_number=? AND name=?",
                    (sec, message.guild.id, self.bn, boss["name"]),
                )
                await db.commit()
            await message.channel.send(f"✅ **{boss['name']}** 자동예약 → {fmt_seconds(sec)}")

    # ── 보탐 / 보탐+ ──────────────────────────────────────

    async def _cmd_botam(self, message: discord.Message, include_fixed: bool):
        async with get_db() as db:
            # 전체 개수는 항상 is_fixed 구분 없이 세어 보탐/보탐+ 간 숫자 일치
            count_q = "SELECT COUNT(*) FROM schedules WHERE guild_id=? AND bot_number=? AND notified=0"
            async with db.execute(count_q, (message.guild.id, self.bn)) as cur:
                total = (await cur.fetchone())[0]

            q = "SELECT * FROM schedules WHERE guild_id=? AND bot_number=? AND notified=0"
            if not include_fixed:
                q += " AND is_fixed=0"
            q += " ORDER BY scheduled_at"
            if not include_fixed:
                q += " LIMIT 5"
            async with db.execute(q, (message.guild.id, self.bn)) as cur:
                rows = [dict(r) async for r in cur]

        if not rows:
            await message.channel.send("예약된 일정이 없습니다.")
            return

        n = now()
        lines = []
        for r in rows:
            at = datetime.fromisoformat(r["scheduled_at"])
            remain = fmt_remain(at - n)
            miss = f" (미입력×{r['miss_count']})" if r["miss_count"] else ""
            lines.append(f"**{at.strftime('%m/%d %H:%M')}** {r['content']}{miss}  — {remain}")

        if not include_fixed:
            title = f"📋 예약 목록 (가까운 5건 / 전체 {total}건)"
        else:
            title = f"📋 예약 목록 전체 ({total}건)"

        # embed description 최대 4096자 — 초과 시 여러 메시지로 분할
        CHUNK = 4000
        body = "\n".join(lines)
        chunks = []
        while body:
            if len(body) <= CHUNK:
                chunks.append(body)
                break
            cut = body.rfind("\n", 0, CHUNK)
            if cut == -1:
                cut = CHUNK
            chunks.append(body[:cut])
            body = body[cut:].lstrip("\n")

        for i, chunk in enumerate(chunks):
            t = title if i == 0 else "📋 예약 목록 (계속)"
            embed = discord.Embed(title=t, description=chunk, color=0x5865F2)
            await message.channel.send(embed=embed)

    # ── .보탐 초기화 ──────────────────────────────────────

    async def _cmd_botam_reset(self, message: discord.Message):
        # 초기화 전 기여 랭킹 출력
        ranking_embed = await self._build_contributions_embed(message.guild.id)
        if ranking_embed:
            await message.channel.send(embed=ranking_embed)

        async with get_db() as db:
            # 일반/임의 예약의 과거 notified=1 행까지 삭제해야 자동 미입력 재생성을 막을 수 있다.
            await db.execute(
                "DELETE FROM schedules WHERE guild_id=? AND bot_number=? AND COALESCE(is_fixed,0)=0",
                (message.guild.id, self.bn),
            )
            await db.execute(
                "DELETE FROM contributions WHERE guild_id=? AND bot_number=?",
                (message.guild.id, self.bn),
            )
            await db.commit()
        await message.channel.send("🗑️ 고정 보스 제외 모든 예약 기록과 기여 기록이 초기화되었습니다.")

    # ── 기여 랭킹 ─────────────────────────────────────────

    async def _build_contributions_embed(self, guild_id: int) -> discord.Embed | None:
        """기여 랭킹 embed 생성. 기록 없으면 None 반환."""
        async with get_db() as db:
            async with db.execute(
                """SELECT user_id, username, COUNT(*) as cnt
                   FROM contributions
                   WHERE guild_id=? AND bot_number=?
                   GROUP BY user_id
                   ORDER BY cnt DESC""",
                (guild_id, self.bn),
            ) as cur:
                rows = [dict(r) async for r in cur]

        if not rows:
            return None

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, r in enumerate(rows):
            medal = medals[i] if i < 3 else "   "
            lines.append(f"{medal} **{r['username']}**  ⚔️ {r['cnt']}컷")

        total_cuts = sum(r["cnt"] for r in rows)
        embed = discord.Embed(
            title="🏆 컷 기여 랭킹",
            description="\n".join(lines),
            color=0xF1C40F,
        )
        embed.set_footer(text=f"총 {total_cuts}컷  |  참여자 {len(rows)}명")
        return embed

    async def _cmd_contributions(self, message: discord.Message):
        embed = await self._build_contributions_embed(message.guild.id)
        if embed is None:
            await message.channel.send("아직 기록된 컷이 없습니다.")
            return
        await message.channel.send(embed=embed)

    # ── z/ㅋ (다음 예약) ──────────────────────────────────

    # ── 컷/멍 공통 로직 ───────────────────────────────────

    async def _do_cut_miss(
        self, guild_id: int, query: str, action: str,
        time_hm: tuple | None = None,
        user: discord.User | discord.Member | None = None,
    ) -> discord.Embed | str:
        boss = await self._find_boss(guild_id, query)
        if not boss:
            return f"❌ **{query}** 에 해당하는 보스를 찾을 수 없습니다."

        if boss.get("fixed"):
            return f"❌ **{boss['name']}** 은 고정 타임 보스입니다. 컷/멍/젠 처리를 할 수 없습니다."

        if boss["respawn_seconds"] is None:
            return f"❌ **{boss['name']}** 은 리스폰이 설정되지 않았습니다."

        base_time = now()
        if time_hm:
            h, m = time_hm
            base_time = base_time.replace(hour=h, minute=m, second=0, microsecond=0)
            # cut/miss는 "언제 잡았는지" 기준 → 미래 시각이면 어제로 보정
            # spawn(젠)은 "언제 나오는지" 기준 → 미래 시각도 그대로 사용
            if action != "spawn" and base_time > now() + timedelta(minutes=1):
                base_time -= timedelta(days=1)

        n = now()
        if action == "cut":
            spawn_at   = base_time + timedelta(seconds=boss["respawn_seconds"] or 0)
            miss_count = 0
            label      = "컷"
        elif action == "spawn":
            # 젠: 입력한 시각이 바로 출현 예정 시각 → 그 시각에 알림 발송
            spawn_at   = base_time
            miss_count = 0
            label      = "젠"
        else:
            # 멍: 이미 알림 발송된(notified=1) 예약 기준으로 다음 리스폰 계산
            # (notified=0인 자동 미입력 예약 기준으로 삼으면 2사이클 연산되는 오류 방지)
            prev = await self._get_last_schedule(guild_id, boss["name"])
            if prev:
                base_time = datetime.fromisoformat(prev["scheduled_at"])
            spawn_at   = base_time + timedelta(seconds=boss["respawn_seconds"] or 0)
            miss_count = (prev["miss_count"] + 1) if prev else 1
            label      = "멍"

        # 계산된 시각이 과거면 리스폰 단위로 다음 미래 출현 시각으로 전진
        if boss["respawn_seconds"]:
            while spawn_at <= n:
                spawn_at   += timedelta(seconds=boss["respawn_seconds"])
                miss_count += (1 if action not in ("cut", "spawn") else 0)

        async with get_db() as db:
            await db.execute(
                "DELETE FROM schedules WHERE guild_id=? AND bot_number=? AND boss_name=? AND notified=0",
                (guild_id, self.bn, boss["name"]),
            )
            await db.execute(
                """INSERT INTO schedules (guild_id, bot_number, boss_name, content, scheduled_at, miss_count)
                   VALUES (?,?,?,?,?,?)""",
                (guild_id, self.bn, boss["name"], boss["name"], spawn_at.isoformat(), miss_count),
            )
            # 컷 처리자 기록
            if action == "cut" and user is not None:
                username = getattr(user, "display_name", None) or user.name
                actor_type = getattr(user, "actor_type", "discord")
                actor_ref = getattr(user, "actor_ref", f"discord:{user.id}")
                await db.execute(
                    """INSERT INTO contributions
                       (guild_id, bot_number, user_id, username, actor_type, actor_ref, boss_name, cut_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        guild_id, self.bn, user.id, username, actor_type, actor_ref,
                        boss["name"], now().isoformat(),
                    ),
                )
            await db.commit()

        emoji = "✅" if action == "cut" else ("🌀" if action == "spawn" else "😶")
        color = 0x57F287 if action == "cut" else (0x5865F2 if action == "spawn" else 0xFEE75C)
        embed = discord.Embed(
            title=f"{emoji} {boss['name']} {label} 처리",
            color=color,
        )
        if action == "spawn":
            embed.description = f"`{spawn_at.strftime('%m/%d %H:%M')}` · {fmt_remain(spawn_at - now())}"
        else:
            embed.description = (
                f"`{base_time.strftime('%H:%M')}` → `{spawn_at.strftime('%m/%d %H:%M')}`"
                f"  ·  {fmt_remain(spawn_at - now())}"
            )
        if action == "cut" and user is not None:
            username = getattr(user, "display_name", None) or user.name
            embed.set_footer(text=f"처리자: {username}")
        return embed

    async def _cmd_cut_miss(self, message: discord.Message, parsed: dict):
        candidates = await self._find_bosses(message.guild.id, parsed["query"])

        # 공백 없는 단일 토큰에서 endswith로 파싱했으나 보스를 못 찾은 경우,
        # startswith(ㅋ/ㅁ/ㅈ) 대안 파싱 시도
        if not candidates and "_token" in parsed:
            t = parsed["_token"]
            for kw in ("ㅋ", "ㅁ", "ㅈ"):
                if t != kw and t.startswith(kw):
                    alt_action = "cut" if kw == "ㅋ" else ("miss" if kw == "ㅁ" else "spawn")
                    alt_query = t[len(kw):]
                    alt_candidates = await self._find_bosses(message.guild.id, alt_query)
                    if alt_candidates:
                        parsed = {"action": alt_action, "query": alt_query, "time_hm": parsed["time_hm"]}
                        candidates = alt_candidates
                        break

        if len(candidates) > 1:
            await self._ambiguous_reply(message.channel, candidates)
            return
        result = await self._do_cut_miss(
            message.guild.id, parsed["query"], parsed["action"], parsed["time_hm"],
            user=message.author,
        )
        if isinstance(result, str):
            await message.channel.send(result)
        else:
            await message.channel.send(embed=result)

    # ── HH:MM 서버오픈 ────────────────────────────────────

    async def _cmd_server_open(self, message: discord.Message, time_raw: str):
        hm = normalize_time(time_raw)
        if not hm:
            return
        h, m = hm
        n = now()
        open_time = n.replace(hour=h, minute=m, second=0, microsecond=0)
        # 오픈 시각이 현재보다 미래(1시간 초과)이면 어제로 처리 (실수 입력 방지)
        if open_time > n + timedelta(hours=1):
            open_time -= timedelta(days=1)

        async with get_db() as db:
            async with db.execute(
                "SELECT * FROM bosses WHERE guild_id=? AND bot_number=? AND fixed=0",
                (message.guild.id, self.bn),
            ) as cur:
                bosses = [dict(r) async for r in cur]

        count = 0
        past_count = 0
        async with get_db() as db:
            for boss in bosses:
                # 이미 예약된 보스 스킵
                async with db.execute(
                    "SELECT 1 FROM schedules WHERE guild_id=? AND bot_number=? AND boss_name=? AND notified=0",
                    (message.guild.id, self.bn, boss["name"]),
                ) as cur:
                    if await cur.fetchone():
                        continue

                if boss["spawns_on_open"]:
                    # 지연 있음: 오픈시간 + 지연시간 = 첫 등장 (아직 등장 안 함 → miss_count=0)
                    delay_sec = boss["open_delay_seconds"] or 0
                    init_miss = 0
                else:
                    # 지연 없음: 오픈시각에 이미 등장 → 다음 = 오픈 + 리스폰 (등장을 기록 못 함 → miss_count=1)
                    delay_sec = boss["respawn_seconds"] or 0
                    init_miss = 1

                spawn_at = open_time + timedelta(seconds=delay_sec)

                # 계산된 시각이 과거면 리스폰 단위로 미래로 전진 (늦은 오픈 입력 시 알림 폭탄 방지)
                if boss["respawn_seconds"]:
                    while spawn_at <= n:
                        spawn_at  += timedelta(seconds=boss["respawn_seconds"])
                        init_miss += 1
                        past_count += 1  # 몇 번 전진했는지 추적 (로그용)

                await db.execute(
                    """INSERT INTO schedules (guild_id, bot_number, boss_name, content, scheduled_at, miss_count)
                       VALUES (?,?,?,?,?,?)""",
                    (message.guild.id, self.bn, boss["name"], boss["name"], spawn_at.isoformat(), init_miss),
                )
                count += 1
            await db.commit()

        lag_notice = f" (오픈 후 입력 지연 — 일부 보스 미입력 자동 누적)" if past_count > 0 else ""
        await message.channel.send(
            f"✅ 서버오픈 {h:02d}:{m:02d} 기준으로 **{count}개** 보스 미입력 예약 완료.{lag_notice}"
        )

    # ── 일괄 예약 ─────────────────────────────────────────

    async def _cmd_bulk_record(self, message: discord.Message, lines: list[str]):
        """
        멀티라인 일괄 예약.
        지원 형식:
          1) MM/DD HH:MM 보스명 [나머지 무시]  ← 보탐 출력 그대로 붙여넣기
          2) HH:MM[:SS] 보스명 [나머지 무시]
        (미입력×N), — X시간 후 등 뒤쪽 내용은 모두 무시하고 miss_count=0으로 등록.
        """
        ok_lines = []
        fail_lines = []

        for raw in lines:
            spawn_at   = None
            boss_query = ""

            miss_count = 0

            # ── 형식 1: MM/DD HH:MM 보스명 [무시] ──
            m1 = re.match(r"^(\d{2}/\d{2})\s+(\d{2}:\d{2})\s+(.+)", raw)
            if m1:
                date_str = m1.group(1)   # "05/28"
                time_str = m1.group(2)   # "23:24"
                rest     = m1.group(3)

                # 보스명: (미입력...) 또는 — 이후 모두 제거
                boss_query = re.split(r"\s+(?:[\(（]미입력|[—–-])", rest)[0].strip()

                # miss_count 파싱: (미입력×N) 에서 N 추출
                mc = re.search(r"[\(（]미입력×(\d+)[\)）]", rest)
                if mc:
                    miss_count = int(mc.group(1))

                try:
                    mo, dy = int(date_str[:2]), int(date_str[3:])
                    hh, mm = int(time_str[:2]), int(time_str[3:])
                    yr = now().year
                    spawn_at = datetime(yr, mo, dy, hh, mm, 0)
                except ValueError:
                    fail_lines.append(f"❌ `{raw}` — 날짜/시각 파싱 실패")
                    continue
                # 연말 경계: 1주일 이상 지난 날짜면 내년
                if spawn_at < now() - timedelta(days=7):
                    spawn_at = spawn_at.replace(year=yr + 1)

            else:
                # ── 형식 2: HH:MM[:SS] 보스명 [무시] ──
                m2 = re.match(r"^(\d{1,2}:\d{2}(?::\d{2})?)\s+(.+)", raw)
                if not m2:
                    fail_lines.append(f"❌ `{raw}` — 형식 오류")
                    continue

                time_raw = m2.group(1)
                rest     = m2.group(2)

                boss_query = re.split(r"\s+(?:[\(（]미입력|[—–-]|멍|젠|컷|스폰)", rest)[0].strip()

                # miss_count 파싱: (미입력×N) 에서 N 추출
                mc = re.search(r"[\(（]미입력×(\d+)[\)）]", rest)
                if mc:
                    miss_count = int(mc.group(1))

                hm = normalize_time(time_raw)
                if not hm:
                    fail_lines.append(f"❌ `{raw}` — 시각 파싱 실패")
                    continue

                h, mn = hm
                spawn_at = now().replace(hour=h, minute=mn, second=0, microsecond=0)
                if spawn_at <= now():
                    spawn_at += timedelta(days=1)

            if not boss_query:
                fail_lines.append(f"❌ `{raw}` — 보스명 없음")
                continue

            boss = await self._find_boss(message.guild.id, boss_query)
            if not boss:
                fail_lines.append(f"❌ `{boss_query}` — 보스를 찾을 수 없습니다")
                continue

            if boss.get("fixed"):
                fail_lines.append(f"⚠️ `{boss['name']}` — 고정 타임 보스는 일괄 예약할 수 없습니다")
                continue

            async with get_db() as db:
                await db.execute(
                    "DELETE FROM schedules WHERE guild_id=? AND bot_number=? AND boss_name=? AND notified=0",
                    (message.guild.id, self.bn, boss["name"]),
                )
                await db.execute(
                    """INSERT INTO schedules (guild_id, bot_number, boss_name, content, scheduled_at, miss_count)
                       VALUES (?,?,?,?,?,?)""",
                    (message.guild.id, self.bn, boss["name"], boss["name"],
                     spawn_at.isoformat(), miss_count),
                )
                await db.commit()

            remain = fmt_remain(spawn_at - now())
            ok_lines.append(f"✅ **{boss['name']}**  →  {spawn_at.strftime('%m/%d %H:%M')}  ({remain})")

        total = len(ok_lines) + len(fail_lines)
        desc = "\n".join(ok_lines + fail_lines)
        embed = discord.Embed(
            title=f"📋 일괄 예약 완료 ({len(ok_lines)}/{total}건)",
            description=desc,
            color=0x57F287 if not fail_lines else 0xFEE75C,
        )
        await message.channel.send(embed=embed)

    # ── 임의 예약 ─────────────────────────────────────────

    async def _cmd_schedule_custom(self, message: discord.Message, hm: tuple, content: str):
        h, m = hm
        at = now().replace(hour=h, minute=m, second=0, microsecond=0)
        if at < now():
            at += timedelta(days=1)
        async with get_db() as db:
            await db.execute(
                "INSERT INTO schedules (guild_id, bot_number, content, scheduled_at) VALUES (?,?,?,?)",
                (message.guild.id, self.bn, content, at.isoformat()),
            )
            await db.commit()
        await message.channel.send(f"📌 **{content}** → {at.strftime('%m/%d %H:%M')} 예약 완료.")

    # ── 헬퍼: 보스 검색 ───────────────────────────────────

    async def _find_bosses(self, guild_id: int, query: str) -> list[dict]:
        """쿼리에 매칭되는 모든 보스 목록 반환.
        완전 일치(이름 또는 별칭)가 있으면 해당 보스만 반환 — 부분일치 후보 제거.
        """
        async with get_db() as db:
            async with db.execute(
                "SELECT * FROM bosses WHERE guild_id=? AND bot_number=?",
                (guild_id, self.bn),
            ) as cur:
                rows = [dict(r) async for r in cur]

        q = query.lower().replace(' ', '')
        exact = [
            r for r in rows
            if any(
                n.lower().replace(' ', '') == q
                for n in [r["name"]] + json.loads(r.get("aliases") or "[]")
            )
        ]
        if exact:
            return exact

        return [
            r for r in rows
            if any(boss_matches(n, query) for n in [r["name"]] + json.loads(r.get("aliases") or "[]"))
        ]

    async def _find_boss(self, guild_id: int, query: str) -> dict | None:
        matches = await self._find_bosses(guild_id, query)
        return matches[0] if matches else None

    async def _ambiguous_reply(self, channel: discord.TextChannel, candidates: list[dict]) -> None:
        """여러 보스 후보가 있을 때 안내 메시지 전송"""
        names = "\n".join(f"· **{b['name']}**" for b in candidates)
        embed = discord.Embed(
            title="🤔 여러 보스가 매칭됩니다",
            description=f"{names}\n\n정확한 이름으로 다시 입력해주세요.",
            color=0xFEE75C,
        )
        await channel.send(embed=embed)

    async def _get_last_schedule(self, guild_id: int, boss_name: str) -> dict | None:
        """멍 처리의 기준이 되는 가장 최근 예약 반환.
        우선순위: 알림 발송 완료(notified=1) 중 가장 최신 → 없으면 미발송(notified=0) 중 가장 최신.
        notified=1 우선인 이유: 자동 미입력으로 생성된 다음 예약(notified=0)을 기준으로 삼으면
        멍 처리 시 2사이클 후 시각으로 계산되는 오류 방지.
        """
        async with get_db() as db:
            async with db.execute(
                "SELECT * FROM schedules WHERE guild_id=? AND bot_number=? AND boss_name=? "
                "ORDER BY notified DESC, scheduled_at DESC LIMIT 1",
                (guild_id, self.bn, boss_name),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    # ── 주기 체크: 알림 발송 ──────────────────────────────

    async def _bootstrap_scheduler(self) -> bool:
        if self._scheduler_bootstrapped:
            return True
        try:
            stats = await self._recover_schedules(now())
        except Exception as error:
            await self._mark_scheduler_failed(
                "SCHEDULER_BOOTSTRAP_FAILED",
                error,
                context="보스 알림 스케줄러 복구 실패",
            )
            return False

        self._scheduler_bootstrapped = True
        await self._mark_scheduler_ready(bootstrap=True)
        print(
            f"[뚠뚠봇{self.bn:03d}] 재시작 복구 완료 — "
            f"과거 {stats['expired']}건 정리, 일반 {stats['normalCreated']}건·"
            f"고정 {stats['fixedCreated']}건 생성, 중복 {stats['duplicates']}건 제외"
        )
        return True

    @tasks.loop(seconds=1)
    async def check_schedules(self):
        if not await self._bootstrap_scheduler():
            return
        try:
            await self._check_schedules_inner()
            await self._mark_scheduler_ready()
        except Exception as e:
            await self._mark_scheduler_failed(
                "SCHEDULER_TICK_FAILED",
                e,
                context="보스 알림 스케줄러 실행 실패",
            )

    async def _check_schedules_inner(self):
        n = now()
        stale_cutoff = n - timedelta(seconds=SCHEDULE_LATE_DELIVERY_GRACE_SECONDS)
        async with get_db() as db:
            async with db.execute(
                """SELECT 1 FROM schedules
                   WHERE bot_number=? AND notified=0 AND scheduled_at < ?
                   LIMIT 1""",
                (self.bn, stale_cutoff.isoformat()),
            ) as cursor:
                has_stale = await cursor.fetchone() is not None
        if has_stale:
            await reconcile_schedules(bot_number=self.bn, recovery_at=n)

        base = (
            "SELECT s.*, gc.text_channel_id, COALESCE(b.fixed, 0) AS boss_fixed "
            "FROM schedules s "
            "JOIN guild_config gc ON s.guild_id=gc.guild_id AND s.bot_number=gc.bot_number "
            "LEFT JOIN bosses b ON s.guild_id=b.guild_id AND s.bot_number=b.bot_number AND s.boss_name=b.name "
            "WHERE s.bot_number=? AND s.notified=0"
        )
        # 겹치지 않는 구간으로 분리
        # 5분: (90s, 330s] — 1분 구간 위부터
        # 1분: (2s, 90s]   — 정각 구간 위부터
        # 정각: (0s, 2s]   — 루프 1초 기준, 창 2초 → 출현 1~2초 전 알림
        t330 = (n + timedelta(seconds=330)).isoformat()
        t90  = (n + timedelta(seconds=90)).isoformat()
        t2   = (n + timedelta(seconds=2)).isoformat()

        async def fetch(where: str, params: tuple) -> list[dict]:
            async with get_db() as db:
                async with db.execute(
                    base + where + " AND (s.delivery_retry_after IS NULL OR s.delivery_retry_after <= ?)",
                    (self.bn,) + params + (n.isoformat(),),
                ) as cur:
                    return [dict(r) async for r in cur]

        rows_5min  = await fetch(" AND s.warned_5min=0 AND s.scheduled_at > ? AND s.scheduled_at <= ?", (t90, t330))
        rows_1min  = await fetch(" AND s.warned_1min=0 AND s.scheduled_at > ? AND s.scheduled_at <= ?", (t2,  t90))
        rows_final = await fetch(
            " AND s.scheduled_at >= ? AND s.scheduled_at <= ?",
            (stale_cutoff.isoformat(), t2),
        )
        delivery_errors: list[Exception] = []

        # ── 5분 전 경고 ───────────────────────────────────
        for r in rows_5min:
            channel = self.bot.get_channel(r["text_channel_id"])
            if not channel:
                continue
            async with get_db() as db:
                claimed = await db.execute(
                    """UPDATE schedules SET warned_5min=1
                       WHERE id=? AND notified=0 AND warned_5min=0
                         AND (delivery_retry_after IS NULL OR delivery_retry_after <= ?)""",
                    (r["id"], n.isoformat()),
                )
                await db.commit()
                if claimed.rowcount != 1:
                    continue
            at = datetime.fromisoformat(r["scheduled_at"])
            miss_str = f" (미입력×{r['miss_count']})" if r["miss_count"] else ""
            embed = discord.Embed(
                title="⏰ 5분 후 출현",
                description=f"**{r['content']}**{miss_str}  — {fmt_remain(at - n)}",
                color=0xFEE75C,
            )
            try:
                await channel.send(embed=embed)
            except discord.DiscordServerError as error:
                await self._retry_scheduler_delivery(r["id"], "5m", error)
                delivery_errors.append(error)
                continue
            except Exception as error:
                await self._record_scheduler_delivery_uncertain(r["id"], error)
                raise
            await self._clear_scheduler_delivery_error(r["id"])

        # ── 1분 전 경고 ───────────────────────────────────
        for r in rows_1min:
            channel = self.bot.get_channel(r["text_channel_id"])
            if not channel:
                continue
            async with get_db() as db:
                claimed = await db.execute(
                    """UPDATE schedules SET warned_5min=1, warned_1min=1
                       WHERE id=? AND notified=0 AND warned_1min=0
                         AND (delivery_retry_after IS NULL OR delivery_retry_after <= ?)""",
                    (r["id"], n.isoformat()),
                )
                await db.commit()
                if claimed.rowcount != 1:
                    continue
            at = datetime.fromisoformat(r["scheduled_at"])
            miss_str = f" (미입력×{r['miss_count']})" if r["miss_count"] else ""
            embed = discord.Embed(
                title="⚠️ 1분 후 출현",
                description=f"**{r['content']}**{miss_str}  — {fmt_remain(at - n)}",
                color=0xFF8C00,
            )
            try:
                await channel.send(embed=embed)
            except discord.DiscordServerError as error:
                await self._retry_scheduler_delivery(r["id"], "1m", error)
                delivery_errors.append(error)
                continue
            except Exception as error:
                await self._record_scheduler_delivery_uncertain(r["id"], error)
                raise
            await self._clear_scheduler_delivery_error(r["id"])

        # ── 정각 알림 ─────────────────────────────────────
        for r in rows_final:
            channel = self.bot.get_channel(r["text_channel_id"])
            if not channel:
                continue
            # Claim before any external side effect. If Discord accepted a
            # message but the client later raised, retrying next second could
            # create an unbounded notification/TTS burst.
            async with get_db() as db:
                claimed = await db.execute(
                    """UPDATE schedules
                       SET warned_5min=1, warned_1min=1, notified=1
                       WHERE id=? AND notified=0
                         AND (delivery_retry_after IS NULL OR delivery_retry_after <= ?)""",
                    (r["id"], n.isoformat()),
                )
                await db.commit()
                if claimed.rowcount != 1:
                    continue
            at = datetime.fromisoformat(r["scheduled_at"])
            remain = fmt_remain(at - n)
            miss_str = f"(미입력×{r['miss_count']}) " if r["miss_count"] else ""
            embed = discord.Embed(
                title="⚔️ 보스 출현!" if r["boss_name"] else "⏰ 예약 알림",
                description=f"**{r['content']}** {miss_str}— {remain}",
                color=0xED4245,
            )
            view = BossActionView(r["guild_id"], r["boss_name"]) if r["boss_name"] and not r["is_fixed"] and not r["boss_fixed"] else None
            try:
                await channel.send(embed=embed, view=view)
            except discord.DiscordServerError as error:
                await self._retry_scheduler_delivery(r["id"], "spawn", error)
                delivery_errors.append(error)
                continue
            except Exception as error:
                await self._record_scheduler_delivery_uncertain(r["id"], error)
                raise
            await self._clear_scheduler_delivery_error(r["id"])

            tts_cog = self.bot.get_cog("TTS")
            if tts_cog:
                await tts_cog.speak(
                    channel.guild,
                    format_schedule_tts(r["content"], r["miss_count"], remain),
                )

        # ── 고정 일정 보스 자동 예약 생성 ────────────────
        async with get_db() as db:
            async with db.execute(
                """SELECT b.guild_id, b.name, b.fixed_days, b.fixed_time
                   FROM bosses b
                   JOIN guild_config gc ON b.guild_id=gc.guild_id AND b.bot_number=gc.bot_number
                   WHERE b.bot_number=? AND b.fixed=1
                     AND b.fixed_days IS NOT NULL AND b.fixed_time IS NOT NULL""",
                (self.bn,),
            ) as cur:
                fixed_bosses = [dict(r) async for r in cur]

        for fb in fixed_bosses:
            async with get_db() as db:
                async with db.execute(
                    "SELECT 1 FROM schedules WHERE guild_id=? AND bot_number=? AND boss_name=? AND notified=0",
                    (fb["guild_id"], self.bn, fb["name"]),
                ) as cur:
                    if await cur.fetchone():
                        continue
            next_at = next_fixed_occurrence(fb["fixed_days"], fb["fixed_time"])
            if next_at is None:
                continue
            # 현재 정각 알림 처리 중인 시각(60초 이내)은 재예약 대상에서 제외
            # rows_final이 notified=1 세팅 직후 같은 시각으로 새 예약이 생기는 버그 방지
            if next_at <= n + timedelta(seconds=60):
                continue
            async with get_db() as db:
                await db.execute(
                    "INSERT OR IGNORE INTO schedules "
                    "(guild_id, bot_number, boss_name, content, scheduled_at, is_fixed) "
                    "VALUES (?,?,?,?,?,1)",
                    (fb["guild_id"], self.bn, fb["name"], fb["name"], next_at.isoformat()),
                )
                await db.commit()

        # ── 자동 미입력 처리 ──────────────────────────────
        async with get_db() as db:
            async with db.execute(
                """SELECT s.guild_id, s.boss_name, s.scheduled_at, s.miss_count,
                          b.respawn_seconds, b.auto_schedule_seconds, gc.text_channel_id
                   FROM schedules s
                   JOIN bosses b ON s.guild_id=b.guild_id AND s.bot_number=b.bot_number AND s.boss_name=b.name
                   JOIN guild_config gc ON s.guild_id=gc.guild_id AND s.bot_number=gc.bot_number
                   WHERE s.bot_number=? AND s.notified=1 AND s.boss_name IS NOT NULL
                     AND b.fixed=0
                   ORDER BY s.scheduled_at DESC""",
                (self.bn,),
            ) as cur:
                notified = [dict(r) async for r in cur]

        latest: dict[tuple, dict] = {}
        for row in notified:
            key = (row["guild_id"], row["boss_name"])
            if key not in latest:
                latest[key] = row

        for (guild_id, boss_name), row in latest.items():
            at = datetime.fromisoformat(row["scheduled_at"])
            if at + timedelta(seconds=row["auto_schedule_seconds"]) > n:
                continue
            if not row["respawn_seconds"]:
                continue
            async with get_db() as db:
                async with db.execute(
                    "SELECT 1 FROM schedules WHERE guild_id=? AND bot_number=? AND boss_name=? AND notified=0",
                    (guild_id, self.bn, boss_name),
                ) as cur:
                    if await cur.fetchone():
                        continue
                new_at   = at + timedelta(seconds=row["respawn_seconds"])
                new_miss = row["miss_count"] + 1
                # 계산된 시각이 과거면 미래 출현 시각으로 전진 (중간 미입력 누적)
                while new_at <= n:
                    new_at   += timedelta(seconds=row["respawn_seconds"])
                    new_miss += 1
                await db.execute(
                    """INSERT INTO schedules
                       (guild_id, bot_number, boss_name, content, scheduled_at, miss_count)
                       VALUES (?,?,?,?,?,?)""",
                    (guild_id, self.bn, boss_name, boss_name,
                     new_at.isoformat(), new_miss),
                )
                await db.commit()

        # A failed delivery must not prevent unrelated boss maintenance in this
        # tick. Raise only after every row and auto-schedule update has had a
        # chance to run, so the existing health/audit path still records it.
        if delivery_errors:
            raise delivery_errors[0]

    @tasks.loop(hours=24)
    async def cleanup_old_schedules(self):
        """30일 이상 된 notified=1 행 자동 삭제 (일 1회)"""
        async with get_db() as db:
            result = await db.execute(
                "DELETE FROM schedules "
                "WHERE bot_number=? AND notified=1 "
                "AND scheduled_at < datetime('now', 'localtime', '-30 days')",
                (self.bn,),
            )
            await db.commit()
            if result.rowcount:
                print(f"[뚠뚠봇{self.bn:03d}] 오래된 예약 {result.rowcount}건 정리 완료")

    async def _recover_schedules(self, recovery_at: datetime) -> dict[str, int]:
        """Restart-safe, serialized schedule reconciliation."""
        return await reconcile_schedules(
            bot_number=self.bn,
            recovery_at=recovery_at,
        )

    @cleanup_old_schedules.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    @check_schedules.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    @check_schedules.error
    async def on_check_schedules_error(self, error: Exception):
        """루프 예외로 중단됐을 때 알림 발송 + 자동 재시작"""
        # before_check already records a more specific bootstrap failure. Do not
        # overwrite it or send a second alert for the same failed startup.
        if self._scheduler_health().get("status") != "failed":
            await self._mark_scheduler_failed(
                "SCHEDULER_LOOP_STOPPED",
                error,
                context="보스 알림 스케줄러 중단",
            )
        await asyncio.sleep(1)
        # Loop.restart() must be invoked while the failed task still exists so
        # discord.py can attach its restart callback. Waiting for is_running()
        # to become false here leaves the loop permanently stopped.
        self.check_schedules.restart()


async def setup(bot: commands.Bot):
    await bot.add_cog(Boss(bot))
