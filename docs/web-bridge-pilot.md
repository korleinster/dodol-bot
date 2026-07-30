# W8 Multi-Bot Web Bridge

## Scope

The W8 bridge is the authenticated adapter between leinsterCenter and the
existing Discord command handlers for bots 001–004. Each process uses its own
Unix socket (`data/botam-001.sock` through `data/botam-004.sock`) and no TCP
listener. The guest profile determines the bot and guild; browser target
fields are ignored.

## Credentials and request authentication

Each bot receives a separately sourced `BOTAM_BRIDGE_00N_SECRET` and a
numbered socket path. Secrets are at least 32 characters, distinct from every
other bridge secret and from session/guest peppers, and are never logged. The
legacy unnumbered 003 variables are temporary compatibility aliases only.
Compose resolves the restricted host source but explicitly maps only the
current bot's Discord token and numbered bridge secret into that service.
Sibling credentials are not available inside the container.

Requests sign the timestamp, nonce, method, path, and SHA-256 body digest with
the selected bot's HMAC secret. The bridge rejects invalid signatures, requests
outside the 30-second window, reused nonces, invalid actors, and bodies over
8 KiB. Sockets are mode `0660` with the configured host group.

## Botam and capability policy

Every active access profile has its own 8–64 character password. leinsterCenter
stores only a scrypt hash and salt, and derives the bot/guild target from the
authenticated profile. Guests may use Botam records, reset, utilities, games,
and registered component actions; process restart, summon/settings, logs,
deployment, and container control remain owner-only at both layers.

Targets report `commands`, `components`, and `games` only when a text channel is
configured. `tts` is true only when a voice channel is configured and the
pinned voice runtime is healthy. Manual TTS accepts only `v <text>` and
`ㅍ <text>`, with a 200-character limit and at most three queued web jobs per
bridge. `Z`, `Z+`, and lowercase reservation aliases are text/list-only and do
not call the TTS cog. Scheduled exact-time alerts retain their automatic TTS
path independently.

## Shared component actions

The bridge exposes `/internal/v1/component-actions` for registered buttons.
Every registration declares its matcher, handler, `style`, `disabled`,
`actionable`, and mandatory boolean `allowNonAdmin`. Guest/ordinary Discord
actors require `allowNonAdmin=true`; owner web and Discord administrators may
use restricted controls. Unknown, policy-less, disabled, stale, or
wrong-channel components fail closed. Claims are idempotent and outcomes never
contain passwords, cookies, signatures, HMAC material, or tracebacks.

## Bot-authored feed and scheduler health

Each bot mirrors only its own bot-authored messages in its configured guild and
text channel. Human messages, other bots, DMs, and other channels are excluded.
Create/edit/delete events use the additive cursor feed, bounded to 24 hours and
500 events per guild; query parameters are included in the HMAC path.

`/internal/v1/targets` reports safe scheduler states: `starting`, `ready`, or
`failed` with bounded error codes and no traces. Startup is `login → load cogs →
start bridge → connect`; bridge failure is fail-open for Discord core behavior.
Recovery is bot-number scoped, quiet for overdue rows, exact-now preserving,
and idempotent.

## Secure image and build context

The Dockerfile uses a pinned Python virtualenv in a multi-stage build. The
root runtime is retained for shared DB/socket permission compatibility.
`.dockerignore` denies the context by default and
allows only the runtime sources and dependency manifest. Environment files,
secrets, databases, logs, backups, VCS metadata, caches, and build output stay
outside the image. Inspect the built image and context without starting a bot;
never inject production secrets into a build argument or image layer.

## Verification

1. Run unit tests, Python compilation, `docker compose config --quiet`, and
   inspect the image for the expected pinned voice dependencies.
2. Confirm each service has only its own numbered bridge variables and socket.
3. Verify profile login routes to the profile's bot/guild even when the request
   submits forged target fields.
4. Verify text-channel capability gating, voice-runtime/TTS gating, the 200
   character limit, three-job queue limit, and `Z`/`Z+` text-only behavior.
5. Verify component policy, feed filtering, HMAC query binding, cursor
   isolation, scheduler health, and idempotent retries.
6. With owner approval recorded, roll out sequentially `004 → 001 → 002 →
   003`, recording health, socket mode/group, capability, and safe probe
   evidence at every gate. If a gate fails, roll back only that bot and stop;
   later bots remain untouched pending a new decision.

## Deployment boundary

Local implementation and the sequential rollout are owner-approved; production
evidence is pending. Do not open a new port or change Tailscale. See the leinsterCenter
[`M44-DEPLOYMENT.md`](../../leinsterCenter/ops/M44-DEPLOYMENT.md) runbook for
the owner-approval gates and evidence record.
