# DiscordBot Agent Policy

`AGENTS.md` is the canonical repository policy. `CLAUDE.md` is only a
compatibility pointer. README files are always written in English.

## Safety and delivery

- Never expose or commit Discord, Telegram, bridge, or API credentials.
- Preserve the shared SQLite database and unrelated local changes.
- Owner approval is required before Mac Mini deployment, container recreation,
  or service interruption. Never change Tailscale or open a port.
- Use Conventional Commits and a reviewed branch; do not push implementation
  directly to `main`.
- Production rollout order is `004 → 001 → 002 → 003`. Validate Discord,
  scheduler, health, bridge, and voice state after each bot and stop on failure.

## Cross-surface completeness gate

For every new or changed capability, review implementation and authorization,
discoverability and menus, in-product help, operator documentation, contracts
and configuration, tests, current-state docs, rollout, and live acceptance.
Do not mark a parser or handler complete while its help or operating surface is
missing.

DiscordBot may send outbound Telegram alerts through `src/utils/notify.py`, but
it must never poll Telegram or register Telegram commands. The Mac Mini
`server_bot.py` process exclusively owns the Telegram update stream, command
menu, `/announce`, and private-owner authorization.

## Verification

Run the full unit suite, compile `main.py`, `src`, and `tests`, validate Docker
Compose, run shell syntax checks, and scan tracked files for credentials. After
an approved rollout, verify every recreated container reports the intended
commit and passes its health gate.
