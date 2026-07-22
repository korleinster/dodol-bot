# Bot 003 Web Bridge Pilot

## Scope

The bridge is a bot-003-only adapter between leinsterCenter and the existing
Discord command handlers. It uses `data/botam-003.sock`; no network listener is
created. Bot 001, 002, and 004 do not receive bridge environment variables and
must not create the socket.

## Request authentication

leinsterCenter signs the timestamp, nonce, HTTP method, path, and SHA-256 body
digest with `BOTAM_BRIDGE_SECRET`. The bridge rejects invalid signatures,
requests outside the 30-second window, reused nonces, bodies over 8 KiB, and
invalid actors. The secret must be distinct from every web session or guest
credential secret and must never be written to logs.

The secret lives in the ignored `botam-003-bridge.env`, which is loaded only by
the 003 Compose service. It must not be added to the shared `.env` used by every
bot instance.

## Guest command boundary

Allowed behavior includes Botam records, immediate reset, TTS, utilities, and
mini-games. `재시작`, `정신차려`, summon, settings, and other process or channel
operations are rejected before any command handler runs.

Web users receive stable negative actor IDs and display as `웹 · nickname`.
Contributions retain the legacy `user_id` field while recording additive
`actor_type` and `actor_ref` values.

## Shared game behavior

- Dice, coin, and number games use the same in-process handlers as Discord.
- Race edits are captured as ordered job events while the Discord message is
  edited normally.
- Web-started lotteries create the normal Discord reaction message. Discord
  users may join with the existing reaction. The initiating web actor may draw
  through an idempotent bridge request.
- Ranking names prefer the stored web display name when no Discord member
  exists for a negative actor ID.

## Deployment boundary

Building the shared image does not authorize recreating every service. After
separate approval, recreate only bot 003:

```bash
docker compose up -d --no-deps dodol-bot-003
```

Record the container IDs and start times for 001, 002, and 004 before and after
the pilot deployment; they must remain unchanged.

## Verification

1. Run unit tests and Python compilation.
2. Validate the Compose model and confirm bridge variables appear only on 003.
3. Confirm the socket owner group and `0660` mode.
4. Submit identical reset requests with the same idempotency key and verify one
   execution.
5. Run TTS, dice, number game, race, and lottery from web and confirm Discord
   output and web events agree.
6. Verify process commands are rejected.
7. Stop only the 003 bridge/container and confirm the other bots continue.

## Recovery

Bridge and command failures are explicit; clients must not assume success. For
capacity, authentication, or timeout failures, inspect bot-003 health and retry
only through a new owner-approved operational action. Preserve the shared DB and
socket directory. Never delete the database or restart the other bot instances
as an automatic recovery step.
