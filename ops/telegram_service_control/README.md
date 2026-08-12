# Telegram Service Control

This directory contains inert W13 installation assets for a dedicated,
owner-only Telegram service controller. Adding these files to the repository
does not install a helper, modify sudo policy, enable a unit, deploy code, or
restart a service.

## Fixed service map

| ID | Target | Health gate |
|---:|---|---|
| 1 | `dodol-bot-001` | Container is running and Docker health is `healthy` |
| 2 | `dodol-bot-002` | Container is running and Docker health is `healthy` |
| 3 | `dodol-bot-003` | Container is running and Docker health is `healthy` |
| 4 | `dodol-bot-004` | Container is running and Docker health is `healthy` |
| 5 | `leinster-center` | systemd is active and `127.0.0.1:8090/api/health` succeeds |

There is no dynamic service lookup. Home Assistant, host reboot, restart-all,
deployment, image recreation, Tailscale, Cloudflare, port changes, database
operations, and arbitrary shell commands are outside the allow list.

## Security model

- Use a dedicated BotFather bot token. Do not reuse the token consumed by the
  existing `/announce` long poller.
- Both the exact private chat ID and exact owner user ID must match.
- Only `/services`, `/status N`, `/restart N`, `/재시작 N`, and the strict
  Korean numbered restart phrase are parsed.
- A restart needs an inline confirmation that expires after 30 seconds and is
  atomically consumed once.
- Restart execution is serialized in both the controller and the privileged
  helper. Each service has a five-minute cooldown, and no more than two
  confirmations are accepted in ten minutes globally.
- The controller invokes `sudo` with an argv array and never invokes a shell.
  The root-owned helper independently validates the action and service ID.
- The controller does not mount or directly access the Docker socket. The
  root-owned helper selects exactly one existing bot container through both
  its fixed Compose project and service labels and does not parse the
  user-writable Compose file.
- The append-only event trail and request state retain only safe IDs, state,
  timestamps, and bounded error codes. Raw messages, tokens, secrets,
  environment values, and callback credentials are not stored.
- Process log formatting redacts the dedicated bot token before journal output.
- Telegram updates queued while the controller was offline are discarded.
  Unfinished confirmations in SQLite are invalidated at process startup.

## Files

- `core.py`: parsing, authorization, audit state, confirmation, cooldown, and
  fixed helper client.
- `controller.py`: Telegram handlers and result messages.
- `leinygames-service-control`: root-owned fixed allow-list helper.
- `sudoers.example`: ten fully enumerated allowed argv combinations.
- `leinster-telegram-control.service`: hardened host-side systemd template.
- `leinygames-telegram-control.env.example`: dedicated secret/config template.

## Installation procedure

Installation is a production mutation and requires explicit owner approval.
Run these steps only from the verified production checkout after tests pass.

1. Create a dedicated Telegram bot and record its token outside the repository.
2. Verify the private chat ID and the owner's Telegram user ID independently.
3. Install the helper as `/usr/local/sbin/leinygames-service-control`, owned by
   `root:root` with mode `0755`.
4. Install `sudoers.example` as
   `/etc/sudoers.d/leinygames-telegram-control`, owned by `root:root` with mode
   `0440`, then validate it with `visudo -cf` before proceeding.
5. Create `/var/lib/leinygames-telegram-control`, owned by `leinster:leinster`
   with mode `0700`.
6. Copy the environment template to
   `/etc/leinygames-telegram-control.env`, set the dedicated token and exact
   IDs, and restrict it to `root:root` mode `0600`.
7. Confirm the production virtual environment includes the pinned
   `python-telegram-bot==21.6` dependency.
8. Install the unit as `/etc/systemd/system/leinster-telegram-control.service`,
   reload systemd, and enable the unit only under the approved operation.

Do not add a wildcard to sudoers. Do not make the helper writable by
`leinster`. Do not place a Telegram token in a command line, chat, commit,
fixture, screenshot, or log.

## Acceptance sequence

1. Confirm the controller becomes active without changing any target service.
2. Run `/services`; all five entries must return a bounded state.
3. Run `/status N` for each target and compare it with host-side evidence.
4. Request one approved low-risk target restart, let the confirmation expire,
   and verify that clicking the old button cannot execute it.
5. Request it again, confirm once, and verify one helper invocation, one audit
   transition chain, and the target-specific health gate.
6. Confirm a repeated click, forged chat/user, invalid service number, and
   shell-like text are rejected without invoking the helper.
7. Confirm unrelated services retain their PID/container identity and that no
   port, Tailscale setting, database, or deployment state changed.

## Rollback

A rollback requires explicit production approval. Stop and disable only the
controller unit, remove only its sudoers entry, unit, helper, and environment
file, and reload systemd. Preserve the audit SQLite file unless the owner
separately approves its deletion. This rollback must not restart any Botam
container or `leinster-center` and must not touch their databases.
