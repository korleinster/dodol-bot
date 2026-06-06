# Dodol Bot — Lineage 2M Boss Alert Discord Bot

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
7. [Ubuntu + Docker Deployment](#ubuntu--docker-deployment)
8. [Project Structure](#project-structure)

---

## Tech Stack

| Item | Details |
|---|---|
| Language | Python 3.11 |
| Bot framework | discord.py 2.3 |
| TTS | gTTS (Google TTS, `ko`) |
| Database | SQLite + aiosqlite |
| Price API | PLAYNC Developer Center (`dev-api.plaync.com/l2m/v1.0`) |
| Weather API | Open-Meteo (free, no key required) |
| Deployment | Ubuntu 26.04 + Docker Compose (Mac Mini) |

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

> **FFmpeg required** — used for TTS audio playback.  
> macOS: `brew install ffmpeg` / Ubuntu: `apt install ffmpeg`

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
```

Bot tokens are issued at [Discord Developer Portal](https://discord.com/developers/applications).  
PLAYNC API keys are issued at [PLAYNC Developer Center](https://developers.plaync.com).

---

## Deploying the Bot

When you first add the bot to a server, deploy it with the `소환 도돌봇001` command in any channel.

```
소환                ← Check deployment status of all Dodol Bot instances on the server
소환 도돌봇001      ← Deploy Dodol Bot 001 to current text + voice channel
설정                ← Check this bot's current channel assignment
```

- The voice channel is automatically set to the voice room the user is **currently in** when the command is issued.
- After deployment, commands only work in the configured text channel.

---

## Command Reference

Commands are entered directly without any prefix.

### Channel Setup

| Command | Description |
|---|---|
| `소환` | View all Dodol Bot deployments on the server |
| `소환 도돌봇001` | Deploy Dodol Bot 001 to current channel |
| `설정` | Check current bot channel configuration |

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

ㅊㄹ ㅋ               ← Korean consonants (3+ chars) + ㅋ (kill shortcut)
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
ㅊㄹ ㅁ               ← consonants (3+ chars) + ㅁ (miss shortcut)
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

The exact-time alert shows **✅ Kill / 😶 Miss** buttons. Pressing them immediately processes the kill or miss (buttons not shown for fixed-schedule bosses).

```
보탐 / ㅂㅌ / ㅋ / z  ← next 5 upcoming reservations (total count shown)
보탐+ / ㅂㅌ+ / ㅋ+ / z+  ← full reservation list

Z                    ← next 5 + TTS voice alert

전체삭제              ← show contributor ranking, then reset reservations + contribution records
초기화                ← same as above

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

---

### Auto-Reservation

Configure the grace period before a boss is auto-scheduled when no kill/miss is entered (default: 10 minutes).

```
자동예약                           ← view auto-reservation time per boss
자동예약 0:30:00 체르투바          ← set 체르투바 auto-reservation grace to 30 minutes
```

---

### TTS

Reads text aloud in the assigned voice channel (gTTS, Korean). Maintains a permanent voice channel connection.

```
ㅍ 좋은 아침입니다         ← reads the text in the voice channel
v 좋은 아침입니다          ← same
정신차려                   ← full bot restart
재시작                     ← same
```

---

### Price Lookup

Queries marketplace prices via the PLAYNC Developer Center API. Also shows the lowest price per server.

```
시세 집행검                ← item search + price stats + lowest price per server
```

---

### Weather

Shows 3-day weather (Open-Meteo API).

```
날씨                       ← today / tomorrow / day after tomorrow + precipitation probability
```

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
- Each bot has independent channel/boss/reservation data per server.
- Running multiple bots on the same server allows management separated by channel.

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

```bash
ssh leinster@192.168.31.68
cd ~/dodol-bot
git pull origin main
docker compose up -d --build
```

### DB Backup

```bash
cp ~/dodol-bot/data/bot.db ~/dodol-bot/backups/bot_$(date +%Y%m%d).db
```

### Environment Variables

| Variable | Description |
|---|---|
| `DISCORD_TOKEN_001` ~ `004` | Bot tokens |
| `PLAYNC_API_KEY` | PLAYNC Developer Center API key |
| `DB_PATH` | `./data/bot.db` (Docker volume mount) |

> Timezone: KST (UTC+9) — configured via `timedatectl set-timezone Asia/Seoul`.

---

## Project Structure

```
DiscordBot/
├── main.py                  # Entry point — multi-instance runner
├── requirements.txt
├── Dockerfile               # Docker build (Python 3.11 + FFmpeg)
├── docker-compose.yml       # Mac Mini deployment config
├── deploy.sh                # Deployment guide script
├── .env.example
├── .gitignore
├── LICENSE
├── data/
│   └── bot.db               # SQLite DB (Docker volume mount → /home/leinster/dodol-bot/data/)
└── src/
    ├── db.py                # DB init / connection / default boss list
    ├── korean.py            # Korean consonant search, partial matching
    └── cogs/
        ├── setup.py         # Deploy, channel setup
        ├── boss.py          # Boss management / kill / miss / reservations / server-open / fixed-schedule
        ├── tts.py           # TTS + bot restart
        ├── market.py        # PLAYNC price API
        ├── minigame.py      # Mini-games, random draw, horse race
        ├── weather.py       # Weather (Open-Meteo)
        └── help.py          # Help (section embeds)
```

---

## Documentation

| Doc | Content |
|---|---|
| [docs/db-schema.md](docs/db-schema.md) | DB table structure and column descriptions |
| [docs/boss-data.md](docs/boss-data.md) | Full default boss list and data reference |
| [docs/notification-logic.md](docs/notification-logic.md) | Alert timing, auto re-scheduling, auto-miss flow |
| [docs/error-cases.md](docs/error-cases.md) | Bot behavior for edge cases |

---

## Copyright

Copyright (c) 2026 [korleinster](https://github.com/korleinster) — All Rights Reserved.
