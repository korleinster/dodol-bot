# Telegram Integration Ownership

## Single polling owner

The Mac Mini `server_bot.py` systemd service is the only Telegram update
consumer. It owns private-owner authorization, command routing, `/announce`,
command-menu registration, help text, backlog discard, and live menu-scope
verification. DiscordBot must not start a second poller or call
`setMyCommands`; two consumers would race for updates and could overwrite menu
state.

DiscordBot uses `src/utils/notify.py` only for best-effort outbound operational
alerts. Each enabled bot may send an alert, and an alert failure never blocks
Discord startup, commands, scheduling, or recovery. Runtime secrets remain in
permission-restricted host environment files and are not copied into source,
images, logs, tests, or documentation.

## Change checklist

When a Telegram command changes in the host scripts repository, update its
single command catalog and regenerate the menu, help, README table, tests, and
live verifier there. DiscordBot documentation needs an update only if outbound
alert behavior or ownership changes.

After separately approved deployment, verify that the host server bot is the
only `getUpdates` consumer, the exact owner-chat menu matches the catalog, and
the default/all-private scopes are empty. Then confirm DiscordBot containers
still emit outbound alerts without receiving commands.
