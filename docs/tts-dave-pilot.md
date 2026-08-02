# W8/W10 TTS and Voice Runtime Contract

This document applies to bots 001–004. W10 Revision 2 adds the owner-selected
bot-003 Edge Neural voice pilot. Production deployment and container recreation
remain a separate approval boundary.

## Command contract

Manual TTS has exactly two entrypoints:

```text
v <text>
ㅍ <text>
```

Each form is limited to 200 characters and enters that bot's queue only when a
configured voice channel and healthy voice runtime exist. Each bridge accepts
at most three queued web TTS jobs. `Z`, `Z+`, `z`, `z+`, `보탐`, and `보탐+` are
reservation-list commands and must never call the TTS cog or consume a slot.
Scheduled exact-time boss alerts retain their existing automatic Discord/TTS
path.

## Voice dependency contract

The image pins `discord.py[voice]==2.7.1`, `edge-tts==7.2.8`, `gTTS==2.5.1`,
`davey`, and PyNaCl, and installs FFmpeg for playback. A target advertises
`tts: true` only when its voice channel is configured and all pinned runtime
checks pass. Otherwise TTS fails closed with a safe capability/error response
and no queued job.

Bot 003 resolves these non-secret settings from its service environment:

```text
TTS_PROVIDER=edge
TTS_EDGE_VOICE=ko-KR-SunHiNeural
TTS_EDGE_RATE=+8%
TTS_EDGE_PITCH=+8Hz
```

Edge synthesis is limited to 20 seconds. A timeout, unavailable dependency, or
provider error triggers exactly one gTTS attempt. Temporary audio is removed on
synthesis failure, connection failure, playback failure, timeout, and
cancellation. Bots 001, 002, and 004 always select gTTS, even if an unrelated
global environment contains Edge settings. Setting `TTS_PROVIDER_003=gtts` in
the host environment disables the bot-003 pilot without a code change.

## Local verification

From a clean checkout, install the requirements and run:

```bash
python -m discord --version
python -c 'import discord, davey, nacl; assert discord.__version__ == "2.7.1"'
python -m compileall -q main.py src tests
python -m unittest discover -s tests -p 'test*.py'
docker compose config --quiet
```

Verify all four numbered bridge secrets and sockets are configured without
printing their values. Verify each target's text/voice capability and run `v`
and `ㅍ` probes plus `Z`/`Z+` queue-invariance probes in test doubles or a
non-production environment. For bot 003, also verify the exact SunHi voice,
rate, pitch, 20-second timeout, single gTTS fallback, and temporary-file cleanup.

## Secure image checks

Build the shared image with the multi-stage Dockerfile. Confirm the runtime
image contains only the pinned virtualenv and `main.py`/`src`, retains the root
runtime required by shared DB/socket permissions, and contains no `.env`,
secret, database, log, backup, VCS, or cache files. The deny-by-default
`.dockerignore` is part of the security boundary; do not bypass it with a
broad build context.

## Rollout and rollback order

After separate deployment and restart approval, recreate one service at a time
in this order:

`dodol-bot-004 → dodol-bot-001 → dodol-bot-002 → dodol-bot-003`

At each gate verify health, commit, numbered socket mode `660`, capability
payload, and safe TTS/text-only probes before touching the next service. If a
gate fails, restore the prior known-good image for only that bot and stop the
rollout; do not restart healthy siblings or touch later bots. Preserve the
database.
No new port or Tailscale change is permitted.
