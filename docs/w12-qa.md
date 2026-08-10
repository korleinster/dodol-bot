# W12 Revision 1 QA

## Automated verification

- Full unit suite: 88 tests passed.
- Python compile: `main.py`, `src`, and `tests` passed.
- Docker Compose validation: not available on the MacBook because Docker is not
  installed; it remains a pre-rollout Mac Mini gate.

Coverage includes:

- silence for `설정` outside the assigned text channel;
- server-management authorization for summon and leave mutations;
- transactional relocation of bosses, schedules, and contributions;
- conflict rollback without overwriting either guild;
- leave commands preserving all gameplay data;
- inactive bindings omitted from bridge target discovery;
- one alert per continuous scheduler incident and one recovery alert after 30
  successful ticks; and
- escaped plain-text web command rendering without a code fence or public
  origin badge.

## Production rollout

The pre-implementation read-only check found one active configuration for each
bot. Bot 004 had no scheduler error rows, every one of its 926 schedules was in
the ready state, and no recent structured scheduler-failure broadcast was
stored.

The owner separately approved deployment and recreation on 2026-08-10. Before
rollout, the shared SQLite database passed `PRAGMA integrity_check` and was
backed up to:

- `/home/leinster/backups/dodol-bot/bot-pre-w12-20260810T051749Z.sqlite`

The previous image was retained as `dodol-bot:rollback-w12-e3f35aa`. Commit
`253fe17` was built once and rolled out in the gated order
`004 -> 001 -> 002 -> 003`. Every container reported the new commit, remained
running with zero restarts, exposed its numbered socket as `0660 root:leinster`,
and connected to Discord. Bot 003 also reconnected its configured TTS voice
channel. The post-rollout database integrity check passed, one active binding
remained for each bot, and recent logs contained no traceback,
`SCHEDULER_TICK_FAILED`, or scheduler-failure alert.
