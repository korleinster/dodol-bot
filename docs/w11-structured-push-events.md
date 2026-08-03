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

## Production rollout

Production rollout completed on 2026-08-03 at commit `1b32a2c`.

- The no-cache image build and secret-absence inspection passed.
- Containers were recreated in `004 → 001 → 002 → 003` order. At each gate,
  only the target container ID changed and all siblings stayed online.
- All four containers run image
  `sha256:0e7b29717f1011e2cb12179989c8af98d9f986ade49e9992dcd42c47b5c43d76`.
- Every bot reported the expected startup commit, a mode-660 per-bot Unix
  socket, signed bridge health `ok`, scheduler state `ready`, and one configured
  target.
- Bot 003 retained Edge TTS with `ko-KR-SunHiNeural`, `+8%` rate, and `+8Hz`
  pitch after recreation.
- The unreferenced W10 rollback image and 91.54MB of unused build cache were
  removed without deleting containers or database data.

Installed-iPhone and desktop push delivery remain owner-device acceptance
checks because they require an authenticated guest session and notification
permission. They must not be simulated with forged credentials.
