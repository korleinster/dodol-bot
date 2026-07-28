# W6 TTS/DAVE Pilot (Bot 003 Only)

This document is the operational contract for Revision 2 of the voice change.
It is intentionally limited to the `dodol-bot-003` pilot and does not authorize
an all-bot rollout.

## Command contract

Manual TTS has exactly two entrypoints:

```text
v <text>
ㅍ <text>
```

Both forms enqueue the supplied text for Korean voice playback, subject to the
existing bridge queue and length limits. No other manual command may enqueue a
voice job.

`Z` and `Z+` are reservation-list commands. They render text only and must not
call the TTS cog, increment the TTS queue, or claim a TTS slot. Lowercase
reservation aliases (`z`, `z+`, `보탐`, and `보탐+`) are text-only as well.

Scheduled exact-time boss alerts are different: their existing background
notification still sends Discord text and automatic TTS. This scheduled path
is not changed by the manual command routing rule.

## Voice dependency contract

The pilot image must install the voice extra for the pinned framework:

```text
discord.py[voice]==2.7.1
```

`discord.py` 2.7.1 requires the `davey` package when a voice connection uses
Discord's DAVE protocol. The voice extra also supplies the PyNaCl requirement;
FFmpeg remains required for gTTS playback. Do not rely on a transitive package
being present: verify `davey` and PyNaCl in the actual runtime image.

## Local verification (no Discord token required)

Run these checks from a clean checkout after installing the approved revision:

```bash
python3.11 -m venv .venv-w6
source .venv-w6/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# Version and DAVE/voice imports must succeed.
python -m discord --version
python -c 'import discord, davey, nacl; assert discord.__version__ == "2.7.1"; print("voice dependencies: ok")'

# Compile and run the complete test suite, including the command-routing tests.
python -m compileall -q main.py src tests
python -m unittest discover -s tests -p 'test*.py'
```

The routing smoke test must demonstrate all four outcomes below in a test
guild or test double:

| Input | Discord/list response | TTS queue |
|---|---|---|
| `v W6 manual voice probe` | normal command response | exactly one job |
| `ㅍ W6 manual voice probe` | normal command response | exactly one job |
| `Z` | next reservation list | unchanged |
| `Z+` | full reservation list | unchanged |

Do not paste `.env`, bridge credentials, or raw request signatures into test
output.

## Container verification

For local Compose checks, create the ignored env files from their examples only
when they are absent. Use real secrets only in the deployment environment.

```bash
test -f .env || cp .env.example .env
test -f botam-003-bridge.env || cp botam-003-bridge.env.example botam-003-bridge.env

# Model validation must pass before any container is started.
docker compose config --quiet

# The bridge settings must resolve only on bot 003. This jq command emits
# only true/false and never prints environment values or secrets.
docker compose config --format json | jq -e '
  .services as $services |
  ($services["dodol-bot-003"].environment.BOTAM_WEB_BRIDGE_ENABLED == "1") and
  ($services["dodol-bot-003"].environment.BOTAM_BRIDGE_SOCKET == "/app/data/botam-003.sock") and
  (["dodol-bot-001", "dodol-bot-002", "dodol-bot-004"] | all(. as $name |
    (($services[$name].environment // {}) | has("BOTAM_WEB_BRIDGE_ENABLED") | not)))
'

# Build the shared image without recreating any service.
export GIT_COMMIT=$(git rev-parse --short HEAD)
docker compose build dodol-bot-001

# Verify dependencies and tests inside the built image without starting Discord.
docker compose run --rm --no-deps --entrypoint python dodol-bot-003 -m discord --version
docker compose run --rm --no-deps --entrypoint python dodol-bot-003 -c \
  'import discord, davey, nacl; assert discord.__version__ == "2.7.1"; print("voice dependencies: ok")'
docker compose run --rm --no-deps --entrypoint python dodol-bot-003 \
  -m unittest discover -s tests -p 'test*.py'
```

After an approved pilot deployment, verify the socket and service boundary
without exposing env contents:

```bash
docker compose ps
stat -c 'mode=%a owner=%U group=%G' data/botam-003.sock
docker compose logs --tail=50 dodol-bot-003 | grep 'commit:'
```

The socket must be mode `660`, and only bot 003 may create it. A missing socket
is a bridge-start failure, not permission to restart another bot.

## Pilot deployment (owner approval required)

Deployment and the bot-003 restart are separate owner approvals. Before either
approval, record the current container IDs and start times:

```bash
docker inspect --format '{{.Name}} id={{.Id}} started={{.State.StartedAt}}' \
  dodol-bot-001 dodol-bot-002 dodol-bot-003 dodol-bot-004
```

Tag the known-good shared image before building the pilot image:

```bash
docker image tag dodol-bot:latest dodol-bot:pre-w6
export GIT_COMMIT=$(git rev-parse --short HEAD)
docker compose build dodol-bot-001
```

Building the shared image does not recreate a container. After the explicit
restart approval, recreate only bot 003:

```bash
docker compose up -d --no-deps --force-recreate dodol-bot-003
```

Never run a bare `docker compose up -d`, a full `docker compose restart`, or a
rolling restart of 001/002/004 for this pilot. Those three containers must keep
the exact IDs and start times recorded above. Run the container verification,
then exercise `v <text>`, `ㅍ <text>`, `Z`, and `Z+` in the configured bot-003
guild and record the expected queue behavior before declaring the pilot ready.

## Rollback (bot 003 only)

If the pilot fails its dependency, voice, or routing checks, preserve the
database and revert only the bot-003 image. The `pre-w6` tag must have been
created before deployment:

```bash
docker image tag dodol-bot:pre-w6 dodol-bot:latest
docker compose up -d --no-deps --force-recreate dodol-bot-003
```

Re-run the container verification and compare the recorded IDs/start times for
001, 002, and 004. If `dodol-bot:pre-w6` is unavailable, stop and request a new
owner-approved recovery plan; do not rebuild from an unknown checkout and do
not restart the other bot instances. Never delete the SQLite database as part
of this rollback.
