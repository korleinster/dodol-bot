# Reset Race Fix Rollout

## Change and approval

The owner approved sequential deployment to all four Mac Mini bots on
2026-10-07. Implementation PR #20 was merged as `6d57ad8`.

Auto-miss now creates a reservation only if its source schedule ID still
exists and there is no pending reservation for the same guild, bot, and boss.
Both checks run in the same `INSERT ... SELECT` statement. This prevents an
in-flight history snapshot from restoring reservations after reset, and
prevents a concurrent manual reservation from receiving an extra auto-miss row.

## Pre-deployment verification

- All 108 unit tests and PR CI passed. Five reset tests cover reset/insert
  ordering, concurrent manual input, other guilds/bots, fixed schedules,
  contribution deletion, list output, and subsequent recovery.
- The reset race and manual-input race both failed against the old code and
  passed after the fix.
- Python compilation, shell syntax, tracked-credential checks, and Compose
  configuration validation passed.
- The source checkout advanced from `7253622` to `6d57ad8` with no tracked
  local changes. The unrelated untracked `logs/` directory was preserved.
- An online SQLite backup was created at
  `/home/leinster/dodol-bot/backups/bot_pre_reset_race_20261007T072350_KST.db`.
  Its `PRAGMA quick_check` returned `ok`.
- The previous image was retained as `dodol-bot:pre-reset-race-6d57ad8`.

## Sequential gates

Each service is explicitly removed and recreated with `--pull never` and
`--wait`. Before the next service, verify Docker health, runtime commit, image
ID equality, fresh application health, live Discord readiness, recent scheduler
ticks, authenticated bridge health/targets, and each target's voice state.

All times below are Korea Standard Time on 2026-10-07.

| Order | Bot | Gate time | Commit | Health / Discord / scheduler / bridge | Voice |
|---|---|---|---|---|---|
| 1 | 004 | 07:25:31 | `6d57ad8` | Passed | Unconfigured |
| 2 | 001 | 07:26:28 | `6d57ad8` | Passed | Unconfigured |
| 3 | 002 | 07:27:17 | `6d57ad8` | Passed | Unconfigured |
| 4 | 003 | 07:28:02 | `6d57ad8` | Passed | Connected |

Final checks passed at 07:28:42 KST:

- All four containers were `healthy`, ran commit `6d57ad8`, and matched image
  `sha256:34fe53fff34188d1142d67809048ec905d11fdb8c2598fb56aeb8b83040a577b`.
- Every container had restart count zero, fresh application health, Discord
  `ready`, scheduler `ready`, and the expected commit in its startup log.
- Authenticated bridge health and target requests passed for every bot. Each
  bridge exposed its one configured target with the matching bot number.
- Bot 003's configured voice connection was observed connected. Bots 001, 002,
  and 004 correctly reported voice as unconfigured.
- Startup logs contained no traceback, scheduler bootstrap/tick failure, or
  bridge startup failure.
- The live database returned `PRAGMA quick_check=ok`. There were zero pending
  rows older than the 15-second delivery grace and zero duplicate pending
  groups for the same bot, guild, and boss.
- `AGENTS.md` was reviewed; the existing approval and rollout-order policy
  required no change. Fly.io activation, ports, and Tailscale were not changed.

## Data boundary

Deployment does not invoke reset or delete existing reservations or contribution
records. The normal scheduler and startup reconciliation continue their usual
maintenance. Existing bot-004 reservations that were recreated before this fix
are not retroactively removed; newer user-entered reservations remain intact.
No live reset command or test Discord/TTS message is sent during verification.
