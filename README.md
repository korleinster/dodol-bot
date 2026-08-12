# DdunDdun Bot — Lineage 2M Boss Alert Discord Bot

A Discord bot for Lineage 2M that provides boss kill/miss/reservation management, TTS alerts, marketplace price lookup, weather, and mini-games.

[![License](https://img.shields.io/badge/license-All%20Rights%20Reserved-red.svg)](LICENSE)
[![Sponsor](https://img.shields.io/badge/Sponsor-EA4AAA?style=flat&logo=githubsponsors&logoColor=white)](https://github.com/sponsors/korleinster)

---

## Table of Contents

1. [Tech Stack](#tech-stack)
2. [Installation & Running](#installation--running)
3. [Environment Variables](#environment-variables)
4. [Deploying the Bot](#deploying-the-bot)
5. [Command Reference](#command-reference)
   - [Channel Setup](#channel-setup)
   - [Boss Management](#boss-management)
   - [Kill / Miss / Spawn](#kill--miss--spawn)
   - [Reservation Management](#reservation-management)
   - [Server Open](#server-open)
   - [Fixed-Schedule Bosses](#fixed-schedule-bosses)
   - [Auto-Reservation](#auto-reservation)
   - [TTS](#tts)
   - [Price Lookup](#price-lookup)
   - [Weather](#weather)
   - [Mini-Games](#mini-games)
   - [Help](#help)
6. [Multi-Instance](#multi-instance)
7. [Telegram Broadcast](#telegram-broadcast)
8. [Ubuntu + Docker Deployment](#ubuntu--docker-deployment)
9. [Project Structure](#project-structure)
10. [Multi-Bot Web Bridge](#multi-bot-web-bridge)
11. [TTS and Voice Runtime](#tts-and-voice-runtime)

---

## Tech Stack

| Item | Details |
|---|---|
| Language | Python 3.11 |
| Bot framework | discord.py[voice] 2.7.1 |
| Voice encryption | DAVE via the required `davey` runtime dependency |
| TTS | Per-bot Edge Neural `SunHi` with one gTTS fallback |
| Database | SQLite + aiosqlite |
| Price API | PLAYNC Developer Center (`dev-api.plaync.com/l2m/v1.0`) |
| Weather API | Open-Meteo (free, no key required) |
| Telegram | aiohttp (운영 알림 발송) / requests (server_bot 공지) |
| Primary deployment | Ubuntu 26.04 + Docker Compose (Mac Mini) |
| DR deployment | Fly.io — 도쿄 nrt, scale=0 대기 |

---

## Local Development

```bash
# Create Python 3.11 virtual environment
python3.11 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run bot
python main.py
```

## Installation & Running

```bash
# Install dependencies
pip install -r requirements.txt

# Create .env file
cp .env.example .env
# Edit .env and fill in your tokens/API keys

# Run
python main.py
```

> **FFmpeg required** — used for TTS audio playback. `discord.py[voice]==2.7.1`
> also requires the voice extras (including PyNaCl) and the `davey` package for
> Discord's DAVE voice encryption. Verify both before testing voice.
> macOS: `brew install ffmpeg` / Ubuntu: `apt install ffmpeg`

The dependency check is intentionally explicit because `discord.py` 2.7.1
raises when `davey` is missing from a voice-enabled process:

```bash
python -m discord --version       # must report discord.py 2.7.1 and davey
python -c 'import discord, davey, nacl; print(discord.__version__)'
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in the values below.

```env
# Bot tokens (by number — instances not created for missing numbers)
DISCORD_TOKEN_001=your_token_here
#DISCORD_TOKEN_002=your_token_here
#DISCORD_TOKEN_003=your_token_here
#DISCORD_TOKEN_004=your_token_here

# PLAYNC Developer Center API key
PLAYNC_API_KEY=your_api_key_here

# DB path (Docker volume mount — no change needed)
DB_PATH=./data/bot.db

# Telegram broadcast (optional — bot-001 only)
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id

```

Bot tokens are issued at [Discord Developer Portal](https://discord.com/developers/applications).  
PLAYNC API keys are issued at [PLAYNC Developer Center](https://developers.plaync.com).

---

## Deploying the Bot

When you first add the bot to a server, a server administrator deploys it with
the `소환 뚠뚠봇001` command in any channel. Repeating the targeted summon in a
different server moves that bot's one active binding and its boss, schedule,
and contribution data without resetting them.

```
소환                ← Check deployment status of all Dodol Bot instances on the server
소환 뚠뚠봇001      ← Deploy Dodol Bot 001 to current text + voice channel
설정                ← Check this bot's current channel assignment
음성나가기          ← Leave only the assigned voice channel
채팅나가기          ← Leave only the assigned text channel
전체나가기          ← Leave both channels
```

- The voice channel is automatically set to the voice room the user is **currently in** when the command is issued.
- After deployment, commands only work in the configured text channel.
- `설정` is silent outside that text channel. Summon and leave mutations require
  Discord server-management permission.
- Leave commands clear only channel bindings. They never delete bosses,
  schedules, contributions, or historical audit rows.

## Multi-Bot Web Bridge

Bots 001–004 reuse the existing Discord command handlers through separate local
Unix sockets. No TCP port is opened. A process starts its bridge only when
`BOTAM_WEB_BRIDGE_ENABLED=1` and its numbered secret and socket configuration
match `BOT_NUMBER`.

Docker Compose configures `/app/data/botam-001.sock` through
`/app/data/botam-004.sock`. Every bot has a distinct
`BOTAM_BRIDGE_00N_SECRET`; requests include an HMAC signature, timestamp, nonce,
and body digest. Sockets use mode `0660` and may be assigned to the host service
group with `BOTAM_BRIDGE_GID`. Secrets belong in ignored, permission-restricted
host environment sources and must never be committed or copied into the image.
Compose maps only the matching Discord token and numbered bridge secret into
each service; sibling credentials are not present in that container.

Authenticated guests may use Botam, reset, utilities, mini-games, and
policy-approved component actions. Process restart, bot summon, channel
settings, logs, deployment, and container controls remain owner-only and are
rejected by the bridge.

Each bot mirrors only its own bot-authored messages from its configured Discord
text channel to authenticated web guests for that bot and guild. The feed
includes automatic alerts, command replies, utilities, games, lifecycle
notices, message edits, and deletes. It excludes human messages, other bots,
DMs, and non-configured channels. The additive event log keeps up to 24 hours
and 500 events per bot and guild; the authenticated cursor endpoint replays at
most 100 events per request.

Bridge target discovery includes a consumer display name without changing the
Discord account username. The bridge prefers the bot member's guild-specific
nickname, falls back to the bot account display name, and finally uses a
generic `보탐봇 N` label. The value is bounded, excludes control and mention
characters, and is returned as plain text. This additive field lets LeinyGames
show the same nickname users see in that Discord guild while preserving the
numeric bot identity for authorization and audit.

### Shared Discord component actions (M42)

Newly registered buttons are handled by one dispatcher shared by Discord and
the leinsterCenter web portal. Every registered button must include:

- `custom_id` and its handler/claim rule;
- the current `style` and `disabled` state;
- `actionable=true`; and
- the required boolean `allowNonAdmin` policy.

`allowNonAdmin=true` permits authenticated web guests and ordinary Discord
members. `allowNonAdmin=false` requires the owner web surface or a Discord
guild administrator. The bridge rechecks this policy from the authenticated
actor and the configured guild; browser requests cannot supply a role, actor,
guild, channel, or permission override.

Components without a registry entry or without the M42 policy metadata remain
read-only, and disabled components are never dispatched. Explicitly restricted
controls are omitted from guest feeds while remaining visible in the owner
audit feed. Messages created before M42 are therefore safe to display but are
not silently upgraded into executable controls.

The signed per-bot bridge action endpoint is
`POST /internal/v1/component-actions`. It validates the original bot-authored
message, configured text channel, custom ID, and current component state before
calling the shared handler. Cut and miss buttons on one boss message share a
single message-level claim; other buttons default to `message_id + custom_id`.
Repeated requests return the recorded outcome without repeating the change.
Successful actions edit the Discord message and feed state together. A failed
or timed-out action keeps a safe retry path and never reports success by
assumption. Claim/audit data excludes passwords, cookies, bridge signatures,
HMAC secrets, and stack traces.

Discord acknowledges a successful kill/miss button interaction silently. It
does not add a private success card after the public result and source-button
update. Duplicate, denied, and failed actions still return one private notice
with the safe reason.

Bridge target responses also expose a detail-free scheduler status with
`starting`, `ready`, or `failed`, the bootstrap and latest successful tick
timestamps, and a bounded error code. Exception text and tracebacks are never
returned. One continuous scheduler incident produces one failure alert. It is
closed only after 30 consecutive successful ticks, followed by one recovery
alert, so intermittent upstream failures do not create alert storms.

Web-originated commands are mirrored to Discord as escaped plain text in the
form `nickname: command`. They are not wrapped in a code block and do not add a
public web-origin badge. The internal actor type and stable actor reference
remain available for authorization and audit, and Discord mentions stay
disabled.

Discord startup uses the explicit `login → load cogs → start bridge → connect`
sequence. An optional bridge startup failure does not stop Discord commands or
boss scheduling, while a fatal Discord startup error still exits the single-bot
container for supervisor recovery.

The approved W8 rollout recreates one service at a time in the order
`004 → 001 → 002 → 003`, with a health gate after every bot. If a gate fails,
roll back only that bot and stop before touching later bots. See
[`docs/web-bridge-pilot.md`](docs/web-bridge-pilot.md) for the full security,
verification, and recovery contract.

## TTS and Voice Runtime

Manual voice behavior is deliberately narrow:

- `v <text>` and `ㅍ <text>` are the only manual TTS entrypoints. They enqueue
  voice playback (subject to the existing web queue limit).
- `Z` and `Z+` are text/list-only reservation commands. They must not enqueue,
  claim, or otherwise invoke TTS. Lowercase reservation-list aliases follow the
  same rule.
- Scheduled exact-time boss alerts retain their existing automatic TTS; this
  background behavior is independent of manual command routing. Repeated miss
  counts keep the `(미입력×N)` display notation but are spoken once as
  `미입력 N회`, preventing the miss label from being repeated N times.

Bots 001–004 use the key-free Edge Neural `ko-KR-SunHiNeural` voice at `+8%`
rate and `+8Hz` pitch. Edge synthesis has a 20-second limit and falls back once
to the existing Korean gTTS voice. Each service maps its own numbered provider
and prosody variables, so `TTS_PROVIDER_00N=gtts` rolls back only that bot.
Bots without a configured voice channel keep the provider ready but do not
advertise or attempt playback until a channel is assigned.

The DAVE/voice dependency and exact local and container checks are defined in
[`docs/tts-dave-pilot.md`](docs/tts-dave-pilot.md). Each target advertises TTS
only when its voice channel is configured and the pinned runtime is healthy.
Bots without a voice channel keep commands, components, and games available but
return a safe TTS-unavailable response.

W13 separates voice configuration from actual liveness. A stored channel ID is
never treated as a connected voice client. The keepalive verifies the current
client and channel, cleans up stale sessions, and performs three serialized,
bounded reconnect attempts with jitter. Each guild receives its own safe voice
projection; another guild's live connection cannot make it appear connected.

Every container has a local file-based health check. It becomes ready only when
the Discord gateway and scheduler are ready and every configured voice target
is connected; bots with no voice target remain valid. The probe opens no port
and requires no container-runtime access. Docker Compose records `healthy` or
`unhealthy`; the health check does not by itself restart an unhealthy container.

---

## Command Reference

Commands are entered directly without any prefix.

### Channel Setup

| Command | Description |
|---|---|
| `소환` | View all Dodol Bot deployments on the server |
| `소환 뚠뚠봇001` | Move Dodol Bot 001 and its live data to the current server/channel (server administrator) |
| `설정` | Check configuration in this bot's assigned text channel |
| `음성나가기` | Clear and disconnect the voice binding without deleting data |
| `채팅나가기` | Clear the text binding without deleting data |
| `전체나가기` | Clear both bindings without deleting data |

---

### Boss Management

#### View Boss List
```
보스
보스목록
보스리스트
```

Fixed-schedule bosses appear at the top, then sorted by respawn time ascending.  
🔄 indicates a boss that spawns on server restart, with the first-spawn delay shown alongside.

- Matching works **regardless of spaces** (e.g. `블랙릴리` ↔ `블랙 릴리`).

> When the bot is deployed, 61 default bosses + fixed-schedule bosses are registered automatically.  
> Boss list can only be modified by editing the source (`src/db.py`).

---

### Kill / Miss / Spawn

**Kill (컷)** — Enter when you defeat a boss. Automatically schedules the next respawn based on kill time.

```
체르 컷               ← killed just now
컷 체르
체르투바 컷

체르 컷 0530          ← killed at 05:30 (HHMM or HH:MM)
컷 체르 0530
05:30 체르 컷

ㅊㄹ ㅋ               ← Korean consonants + ㅋ (kill shortcut, no length limit)
ㅋ ㅊㄹ               ← both orderings work
컷 ㅊㄹ

체르투바컷            ← no spaces required
ㅊㄹㅌㅂㅋ            ← consonants + ㅋ no spaces
ㅋㅊㄹㅌㅂ            ← ㅋ prefix form
```

**Miss (멍)** — Boss spawned but was not defeated. Calculates next respawn from the previous scheduled time.

```
체르 멍
멍 체르
ㅊㄹ ㅁ               ← consonants + ㅁ (miss shortcut, no length limit)
ㅁ ㅊㄹ               ← both orderings work
체르투바멍            ← no spaces required
ㅊㄹㅌㅂㅁ
```

**Spawn (젠)** — Records the time a boss appeared. Automatically schedules next respawn from the input time.

```
체르 젠               ← spawned just now
젠 체르
0000 체르투바 젠      ← spawned at 00:00 (HHMM or HH:MM)
0000 체르투바 스폰    ← 스폰 keyword also works
0000 체르투바         ← keyword optional (auto-treated as spawn if it's a boss name)
체르투바 0000         ← boss name + time order also works
체르투바젠            ← no spaces required
ㅊㄹ ㅈ               ← consonants + ㅈ (spawn shortcut)
```

> Boss name alone cannot create an arbitrary reservation. Only non-boss content (e.g. `0000 lunch`) can create arbitrary reminders.

---

### Bulk Reservation

Enter expected spawn times for multiple bosses at once. If the message contains 2 or more line breaks, it is processed in bulk mode.

```
17:07:00 탈킨 멍
17:07:08 티미트리스
05/28 17:34 켈소스 (미입력×1) — 1시간 후
05/28 17:36 사반 — 1시간 2분 후
```

- Supports `HH:MM` / `HH:MM:SS` formats (seconds ignored)
- Supports `MM/DD HH:MM boss_name ...` format — paste boss tracker output directly
- Content after the boss name (miss/spawn/`— X시간 후` etc.) is ignored
- `(미입력×N)` notation is reflected in the miss count
- Fixed-time bosses are skipped

---

### Reservation Management

When a boss's appearance time arrives, **3-stage alerts** are sent automatically.

| Timing | Color | Content |
|---|---|---|
| 5 min before | 🟡 Yellow | ⏰ Appears in 5 minutes |
| 1 min before | 🟠 Orange | ⚠️ Appears in 1 minute |
| Exact time | 🔴 Red | ⚔️ Boss appeared! + TTS + **Kill/Miss buttons** |

The exact-time alert shows **✅ Kill / 😶 Miss** buttons. Pressing them immediately processes the kill or miss (buttons not shown for fixed-schedule bosses). A successful Discord click is acknowledged silently; only duplicate, denied, or failed actions create a private notice. The same registered buttons are available in the web feed when their `allowNonAdmin` policy permits the current actor; cut and miss remain one idempotent message-level action.

```
보탐 / ㅂㅌ / ㅋ / z  ← next 5 upcoming reservations (total count shown)
보탐+ / ㅂㅌ+ / ㅋ+ / z+  ← full reservation list

Z                    ← text-only next-5 reservation list (never queues TTS)
Z+                   ← text-only full reservation list (never queues TTS)

전체삭제              ← show contributor ranking, then reset non-fixed reservations/history + contribution records
초기화                ← same as above (fixed-schedule bosses remain)

기여자                ← show current cut contributor ranking
보탐러                ← same as above

22:30 체르투바        ← schedule alert for 체르투바 at 22:30 (arbitrary content also works)
22:30 밥 먹자         ← schedule "lunch" reminder at 22:30
```

#### Kill Contributor Tracking

Every cut (via text command or button) records the user.  
- Kill embed shows `처리자: username` in the footer  
- `기여자` / `보탐러`: shows ranked leaderboard up to current reset  
- `초기화`: displays final ranking, then wipes all records
- Reset removes non-fixed pending and notified reservation history, so auto-miss will not recreate normal boss reservations after reset. Fixed-schedule boss reservations remain.

#### Auto-Miss

If no kill/miss input is received within **10 minutes (default)** after the exact-time alert, the next respawn is automatically scheduled.  
`(미입력×N)` is appended to the reservation name and disappears when kill/miss is entered.

---

### Server Open

Enter the server restart time after maintenance. Schedules all registered bosses as auto-miss reservations based on respawn times.

```
서버오픈 05:00     ← schedule all bosses based on 05:00 open time
05:00 서버오픈     ← same
오픈 05:00         ← same
```

- Bosses already scheduled are skipped.

---

### Fixed-Schedule Bosses

Bosses that automatically alert at a fixed day/time each week.

| Boss | Day | Time |
|---|---|---|
| 타이런트 | Wednesday | 22:30 |
| 셀리호든 | Friday | 19:00 |
| World Boss | Daily | 12:00, 20:00 |
| Arrogance/Faith Tower Boss | Daily | 19:00 |

- Automatically scheduled for the next appearance time on server startup.
- Automatically re-scheduled for the next time after each alert.
- **Excluded** from kill/miss processing and server-open scheduling.

Startup reconciliation quietly acknowledges overdue rows without replaying
missed Discord or TTS alerts. It preserves an exact-now row, creates one safe
future row for configured normal and fixed bosses when possible, and never
regenerates an arbitrary reservation.

---

### Auto-Reservation

Configure the grace period before a boss is auto-scheduled when no kill/miss is entered (default: 10 minutes).

```
자동예약                           ← view auto-reservation time per boss
자동예약 0:30:00 체르투바          ← set 체르투바 auto-reservation grace to 30 minutes
```

---

### TTS

Reads text aloud in the assigned voice channel and maintains a permanent voice connection. Bots 001–004 use the selected Edge Neural `SunHi` voice with an independent one-time gTTS fallback.
Manual TTS is deliberately limited to the two commands below:

```
ㅍ 좋은 아침입니다         ← queues the text for voice playback
v 좋은 아침입니다          ← same
정신차려                   ← full bot restart
재시작                     ← same
```

`Z` and `Z+` only render reservation lists. They are text/list commands and
must never create a TTS job. The same rule applies to `z`, `z+`, `보탐`, and
`보탐+`. Scheduled exact-time boss alerts continue to send their existing
automatic Discord notification and automatic TTS independently of these
manual commands. A repeated miss count is displayed as `(미입력×N)` and spoken
once as `미입력 N회`; zero misses omit that phrase.

---

### Price Lookup

Queries marketplace prices via the PLAYNC Developer Center API. Also shows the lowest price per server.

```
시세 집행검                ← item search + price stats + lowest price per server
```

---

### Weather

Shows 3-day weather (Open-Meteo API, Seongnam city).

```
날씨                       ← today / tomorrow / day after tomorrow + precipitation probability
```

- **Auto-send at 07:00 KST daily** — automatically posted to all configured channels every morning.

---

### Mini-Games

```
주사위                     ← roll 1–6
주사위 100                 ← roll 1–100
동전                       ← heads/tails

숫자게임시작               ← start 1–100 number guessing game
숫자맞추기 42              ← guess a number

뽑기                       ← pick 1 random participant
뽑기3                      ← pick 3 random participants
뽑기 철수 영희 민수        ← pick from specified names

경마                       ← 2-horse race (default)
경마 철수 영희             ← 2 named horses
경마 A B C D               ← up to 8 named horses

랭킹                       ← mini-game score TOP 10
```

---

### Help

```
메뉴                       ← command guide by section (8 embeds in order)
도움말                     ← same
?                          ← same
```

---

## Multi-Instance

Multiple bots can run simultaneously from a single service.

```env
DISCORD_TOKEN_001=token_for_bot_001
DISCORD_TOKEN_002=token_for_bot_002
DISCORD_TOKEN_003=token_for_bot_003
DISCORD_TOKEN_004=token_for_bot_004
```

- Instances are created only for token numbers that are set (001–009 supported).
- Each bot has one active server/channel binding. A cross-server summon moves
  its boss, reservation, and contribution data transactionally.
- Running multiple bots on the same server allows management separated by channel.

---

## Telegram Integration

### Broadcast (공지)

`/announce` is handled by **Mac Mini's `server_bot.py`** (systemd service), not the Discord bot itself.  
It reads all registered channels from `guild_config` DB and posts to each via Discord REST API using per-bot tokens.

```
/announce 서버 점검이 예정되어 있습니다   ← 모든 채널에 공지 embed 발송
```

**Required env in `/etc/server-bot.env`:**
- `DISCORD_TOKEN_001` ~ `DISCORD_TOKEN_004` (봇별 Discord 토큰)

### Operational Alerts (운영 알림)

The bot automatically sends Telegram notifications for operational events:

| Event | Telegram | Discord channel |
|---|---|---|
| ✅ Deployment start | ✅ | ✅ |
| ⚠️ Error restart (Docker restart policy) | ✅ | ✅ |
| 🔄 Gateway reconnection (`ready`) | ✅ | ❌ (noise prevention) |
| 🔄 Gateway session resume after 60+ seconds | ✅ once with downtime | ✅ once |
| ⚠️ `check_schedules` incident opens (loop kept alive) | ✅ once | ✅ once |
| ✅ 30 consecutive scheduler ticks recover an incident | ✅ once | ✅ once |
| ⚠️ `check_schedules` loop crash + restart | ✅ | ✅ |

Restart type is detected via `/tmp` marker file and `on_ready` call count.
Gateway health is cleared immediately on `disconnect` and restored by either
`ready` or Discord's session-only `resumed` event:
- No marker + first `on_ready` → deployment
- Marker exists + first `on_ready` → error restart (same container, process restarted)
- `on_ready` called 2+ times → network reconnection

**Requirements:** `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`  
If either is missing, all Telegram features are silently disabled.

---

## Ubuntu + Docker Deployment

Running on Mac Mini (Ubuntu 26.04 LTS) via Docker Compose.

### Server Info

| Item | Value |
|---|---|
| OS | Ubuntu 26.04 LTS |
| Local IP | `192.168.31.68` |
| Tailscale IP | `100.109.220.64` (remote access) |
| Project path | `/home/leinster/dodol-bot/` |
| DB path | `/home/leinster/dodol-bot/data/bot.db` |

### Initial Setup

```bash
# Clone project on Mac Mini
git clone https://github.com/korleinster/dodol-bot.git ~/dodol-bot
cd ~/dodol-bot

# Create .env
cp .env.example .env
nano .env  # fill in tokens and API key

# Create data directory
mkdir -p data

# Build and start
docker compose up -d --build
docker compose logs -f  # confirm bots online
```

### Update Code

Each bot runs in its own container — updates can be applied bot-by-bot without restarting all instances.

**Full redeploy** (major code changes):
```bash
ssh leinster@192.168.31.68
cd ~/dodol-bot
git pull origin main
export GIT_COMMIT=$(git rev-parse --short HEAD)
docker compose build
docker compose up -d
```

**Rolling deploy** (sequential per-bot restart, minimum disruption):
```bash
git pull origin main
export GIT_COMMIT=$(git rev-parse --short HEAD)
docker compose build
docker compose up -d --no-deps dodol-bot-001
docker compose up -d --no-deps dodol-bot-002
docker compose up -d --no-deps dodol-bot-003
docker compose up -d --no-deps dodol-bot-004
```

**Restart a single bot**:
```bash
docker compose restart dodol-bot-003
```

**Add a new bot** (after adding service to docker-compose.yml):
```bash
docker compose up -d --no-deps dodol-bot-005
```

**Check deployed version** (shows git commit hash per container):
```bash
docker compose logs --tail=5 | grep "commit:"
```

### DB Backup

Automated daily backup via `backup.sh` (runs at 04:00 KST via cron):
- Copies `bot.db` to `backups/` locally (retained 30 days)
- Uploads to Google Drive `dodol-bot-backups/` folder (retained 7 days)
- Requires rclone configured with `gdrive:` remote

```bash
# Manual backup
~/dodol-bot/backup.sh

# Check backup log
tail -20 ~/dodol-bot/backups/backup.log
```

### Environment Variables

| Variable | Description |
|---|---|
| `DISCORD_TOKEN_001` ~ `004` | Bot tokens |
| `PLAYNC_API_KEY` | PLAYNC Developer Center API key |
| `DB_PATH` | `./data/bot.db` (Docker volume mount) |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token (optional, bot-001 only) |
| `TELEGRAM_CHAT_ID` | Allowed Telegram chat ID for `/announce` |

> Timezone: KST (UTC+9) — configured via `timedatectl set-timezone Asia/Seoul`.

### Structured Web Notifications

Each enabled bot mirrors its own configured Discord channel through its
authenticated Unix-socket bridge. Broadcast rows are isolated by bot and
guild. Scheduler messages additionally expose a bounded structured type:

- `boss_warning_5m`
- `boss_warning_1m`
- `boss_spawn`
- `reservation`
- `generic`

Only the first three boss types are eligible for LeinyGames Web Push. Generic
bot speech, human conversation, other bots, and arbitrary reservations remain
ineligible. Exact non-fixed spawn messages retain their registered cut/miss
components so the web service can invoke the same dispatcher and durable claim
used by Discord. A bridge capture failure is best-effort and never interrupts
Discord delivery or the scheduler.

---

## Project Structure

```
DiscordBot/
├── main.py                  # Entry point — multi-instance runner
├── requirements.txt
├── Dockerfile               # Docker build (Python 3.11 + FFmpeg)
├── docker-compose.yml       # Mac Mini deployment config
├── fly.001.toml             # Fly.io DR config — bot-001 (nrt)
├── fly.002.toml             # Fly.io DR config — bot-002 (nrt)
├── fly.003.toml             # Fly.io DR config — bot-003 (nrt)
├── fly.004.toml             # Fly.io DR config — bot-004 (nrt)
├── backup.sh                # DB backup → Google Drive (cron daily 04:00)
├── .env.example
├── botam-003-bridge.env.example
├── .gitignore
├── LICENSE
├── .github/
│   └── workflows/
│       └── fly-deploy.yml   # Auto image build & push to fly.io on push
├── scripts/
│   ├── fly-activate.sh      # Emergency: activate fly.io DR (scale=1)
│   ├── fly-deactivate.sh    # Recovery: deactivate fly.io (scale=0)
│   └── fly-sync-db.sh       # Sync Mac Mini DB → fly.io volumes (cron daily 03:00)
├── data/
│   └── bot.db               # SQLite DB (Docker volume mount → /home/leinster/dodol-bot/data/)
└── src/
    ├── db.py                # DB init / connection / default boss list
    ├── korean.py            # Korean consonant search, partial matching
    ├── telegram_listener.py # Telegram → Discord broadcast (/announce)
    ├── utils/
    │   └── notify.py        # Operational alert utility (Discord + Telegram)
    └── cogs/
        ├── setup.py         # Deploy, channel setup
        ├── boss.py          # Boss management / kill / miss / reservations / server-open / fixed-schedule
        ├── tts.py           # TTS + bot restart
        ├── market.py        # PLAYNC price API
        ├── minigame.py      # Mini-games, random draw, horse race
        ├── weather.py       # Weather (Open-Meteo) + daily 07:00 KST auto-send
        └── help.py          # Help (section embeds)
```

---

## Documentation

| Doc | Content |
|---|---|
| [docs/db-schema.md](docs/db-schema.md) | DB table structure and column descriptions |
| [docs/boss-data.md](docs/boss-data.md) | Full default boss list and data reference |
| [docs/notification-logic.md](docs/notification-logic.md) | Alert timing (1s loop, ±2s precision), auto re-scheduling, auto-miss flow |
| [docs/error-cases.md](docs/error-cases.md) | Bot behavior for edge cases |
| [docs/migration-plan-macmini.md](docs/migration-plan-macmini.md) | Deployment infrastructure history and fly.io DR setup |

---

## Copyright

Copyright (c) 2026 [korleinster](https://github.com/korleinster) — All Rights Reserved.
