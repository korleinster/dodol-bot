# W13 Runtime Health and Recovery

## Scope

W13 adds observable, fail-closed runtime health without exposing a new port or
granting a Discord bot access to Docker or the host service manager.

## Gateway health

- `on_disconnect` immediately marks the local runtime unhealthy.
- A full reconnect restores health in `on_ready`.
- A Discord session resume restores health in `on_resumed`; it does not repeat
  deployment or startup notifications.
- A recovery lasting at least 60 seconds emits one bounded operational alert
  with the observed downtime.

## Voice health

A configured `voice_channel_id` is configuration only. The runtime is connected
only when `VoiceClient.is_connected()` is true and the active channel matches
the configured channel.

Recovery is serialized per guild:

1. detect a missing, disconnected, or wrong-channel client;
2. force-disconnect a stale client with a five-second cleanup bound;
3. retry connection up to three times with bounded exponential jitter;
4. publish `connected`, `connecting`, `recovering`, or `unavailable` using safe
   error codes only;
5. emit one incident alert and one confirmed-recovery alert.

Bridge target health is projected per guild. The aggregate container state is
never copied across targets.

## Container probe

The process atomically refreshes `/tmp/dodol-bot-health.json` every five
seconds. `python -m src.health_probe` accepts only a fresh file whose Discord
gateway, scheduler, and configured voice state are ready. No guild ID, channel
ID, token, HMAC secret, traceback, or environment value is written.

Compose checks the file every 15 seconds with a 90-second start period and four
retries. Docker marks a failed probe unhealthy but does not automatically
restart it; restart automation requires its own separately approved host-side
control plane.

## Verification

- Run `python -m unittest discover -s tests`.
- Run `python -m compileall -q main.py src tests`.
- Run `docker compose config` on a host with Docker Compose.
- Confirm no service mounts `/var/run/docker.sock`.
- During rollout, verify each bot independently in the order approved by the
  owner and stop before the next bot if any health gate fails.
