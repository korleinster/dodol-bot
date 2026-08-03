# W11 Structured Push Event Contract

The DiscordBot bridge remains an authenticated, per-bot Unix-socket service.
It mirrors only messages authored by its own bot in the configured guild text
channel. The existing generic create/update/delete feed is preserved.

Scheduler embeds are classified at capture time:

| Discord embed | Event type | Push eligible |
|---|---|---|
| `5분 후 출현` | `boss_warning_5m` | Yes |
| `1분 후 출현` | `boss_warning_1m` | Yes |
| `보스 출현` | `boss_spawn` | Yes |
| `예약 알림` | `reservation` | No |
| Any other bot message | `generic` | No |

Boss names are extracted only from the bounded bold scheduler description.
The classifier never interprets arbitrary user messages, mentions, or other
bots. Deletions remain tombstones and do not create a push notification.

For an exact non-fixed spawn, the bridge still exposes only components already
registered in the central dispatcher. LeinyGames may present cut/miss through a
short-lived device-bound token, but every request is revalidated against the
actual Discord message, bot/guild/channel, custom ID, disabled state,
`allowNonAdmin`, and the durable message-level claim.

Rollout uses the established `004 → 001 → 002 → 003` order. A bridge or
classification failure must not interrupt Discord login, scheduler ticks,
messages, TTS, or sibling bots. Stop and roll back only the failed bot before
continuing.
