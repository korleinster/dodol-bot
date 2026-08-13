# W12 Channel Lifecycle and Scheduler Incident Contract

## Active binding

Each bot number has one active Discord binding. A server administrator may run
`소환 뚠뚠봇NNN` from any channel. The target bot atomically:

1. checks that there is at most one active data source;
2. refuses to overwrite pre-existing target data;
3. moves bosses, schedules, and contributions to the target guild;
4. creates an inactive destination configuration, synchronizes default-boss
   definitions, and reconciles destination stale schedules;
5. clears old text and voice channel IDs; and
6. writes the target binding.

These are one `BEGIN IMMEDIATE` SQLite transaction. A target-data conflict or
any default/reconciliation failure rolls back every stored change, including a
new destination configuration and moved rows. The destination is never exposed
as an active binding before that commit.

After the commit, the bot disconnects stale source voice clients and attempts
the target voice connection. Those Discord side effects are intentionally not
rolled back. A disconnect error, connection exception, or unavailable (`None`)
voice result produces a degraded “voice confirmation required” summon result,
not an unqualified success.

Historical web broadcast events and component claims remain attached to their
original Discord messages. An inactive configuration is omitted from bridge
target discovery so `NULL` text bindings never escape as an invalid target.

## Leave behavior

`음성나가기`, `채팅나가기`, and `전체나가기` require server-management
permission and run only in the assigned text channel. They update channel IDs
only. Bosses, schedules, contributions, and audit history remain intact.

## Scheduler incidents

The first failed tick opens an incident and sends one safe Discord/Telegram
alert. Further failures are silent while the incident remains open. Thirty
consecutive successful one-second ticks close the incident and send one
recovery alert. Operational Discord alerts target only non-null active text
bindings.

## W15 stale-schedule extension

The scheduler reconciles stale pending rows at startup and whenever it detects
one during a runtime tick. At reconciliation time `r`, only rows strictly
older than `r - 15 seconds` are quietly acknowledged; the exact 15-second
boundary remains eligible for one claimed exact-time notification. This keeps
a reconnect from replaying an unbounded old backlog while preserving a very
short interruption window. General and fixed bosses then receive one safe
future reservation when possible; arbitrary reservations are never recreated.
The reconciliation transaction serializes duplicate prevention for normal and
fixed pending rows.

Known Discord 5xx retries are durable but cannot schedule a send after the
same row's `scheduled_at + 15 seconds` deadline. This bounds retry and rejoin
noise without retrying ambiguous delivery failures.

## Web command rendering

The bridge renders a guest command as escaped plain text (`nickname: command`).
No fenced code block or public web-origin badge is added. The authoritative
`web_guest` actor type remains internal, and Discord mentions are disabled.

## Production rollout

Implementation approval did not by itself authorize production operations.
The owner separately approved deployment and container recreation on
2026-08-10. Commit `253fe17` was then rolled out one bot at a time in the order
`004 → 001 → 002 → 003`; all four health gates passed without rollback.
