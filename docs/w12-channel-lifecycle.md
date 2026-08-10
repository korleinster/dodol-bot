# W12 Channel Lifecycle and Scheduler Incident Contract

## Active binding

Each bot number has one active Discord binding. A server administrator may run
`소환 뚠뚠봇NNN` from any channel. The target bot atomically:

1. checks that there is at most one active data source;
2. refuses to overwrite pre-existing target data;
3. moves bosses, schedules, and contributions to the target guild;
4. clears old text and voice channel IDs;
5. writes the target binding; and
6. disconnects stale voice clients before connecting the target voice room.

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

## Web command rendering

The bridge renders a guest command as escaped plain text (`nickname: command`).
No fenced code block or public web-origin badge is added. The authoritative
`web_guest` actor type remains internal, and Discord mentions are disabled.

## Production rollout

Implementation approval did not by itself authorize production operations.
The owner separately approved deployment and container recreation on
2026-08-10. Commit `253fe17` was then rolled out one bot at a time in the order
`004 → 001 → 002 → 003`; all four health gates passed without rollback.
