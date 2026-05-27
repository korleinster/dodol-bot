# 도돌봇 — 리니지2M 보스 알림 디스코드 봇

리니지2M 보스 컷/멍/예약 관리, TTS 알림, 거래소 시세 검색, 날씨, 미니게임 기능을 제공하는 디스코드 봇입니다.

[![License](https://img.shields.io/badge/license-All%20Rights%20Reserved-red.svg)](LICENSE)
[![Sponsor](https://img.shields.io/badge/Sponsor-EA4AAA?style=flat&logo=githubsponsors&logoColor=white)](https://github.com/sponsors/korleinster)

---

## 목차

1. [기술 스택](#기술-스택)
2. [설치 및 실행](#설치-및-실행)
3. [환경변수 설정](#환경변수-설정)
4. [봇 배치 (소환)](#봇-배치-소환)
5. [명령어 목록](#명령어-목록)
   - [채널 설정](#채널-설정)
   - [보스 관리](#보스-관리)
   - [컷 / 멍 / 젠](#컷--멍--젠)
   - [예약 관리](#예약-관리)
   - [서버오픈](#서버오픈)
   - [고정 일정 보스](#고정-일정-보스)
   - [자동예약](#자동예약)
   - [TTS](#tts)
   - [시세 검색](#시세-검색)
   - [날씨](#날씨)
   - [미니게임](#미니게임)
   - [도움말](#도움말)
6. [멀티 인스턴스](#멀티-인스턴스)
7. [fly.io 배포](#flyio-배포)
8. [프로젝트 구조](#프로젝트-구조)

---

## 기술 스택

| 항목 | 내용 |
|---|---|
| 언어 | Python 3.11 |
| 봇 프레임워크 | discord.py 2.3 |
| TTS | edge-tts (Microsoft, `ko-KR-SunHiNeural`) |
| 데이터베이스 | SQLite + aiosqlite |
| 시세 API | PLAYNC 개발자센터 (`dev-api.plaync.com/l2m/v1.0`) |
| 날씨 API | Open-Meteo (무료, 키 불필요) |
| 배포 | fly.io (Volume 영구 저장소 + Docker FFmpeg) |

---

## 로컬 개발 환경

```bash
# Python 3.11 가상환경 생성
python3.11 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt

# 봇 실행
python main.py
```

## 자동화 테스트

`test_bot.py` 는 별도 테스터 봇을 이용한 E2E 기능 테스트입니다.

```env
# .env 에 추가
TESTER_TOKEN=테스터봇_토큰
TEST_CHANNEL_ID=테스트할_채널_ID
TESTER_BOT_ID=테스터봇_USER_ID
```

```bash
python test_bot.py
```

테스터 봇은 도돌봇이 배치된 채널에 자동으로 명령어를 전송하고 응답을 검증합니다. 17개 항목 전체 통과 시 정상.

---

## 설치 및 실행

```bash
# 의존성 설치
pip install -r requirements.txt

# .env 파일 생성
cp .env.example .env
# .env 편집 후 토큰/API 키 입력

# 실행
python main.py
```

> **FFmpeg 필요** — TTS 음성 재생에 사용됩니다.  
> macOS: `brew install ffmpeg` / Ubuntu: `apt install ffmpeg`

---

## 환경변수 설정

`.env.example` 을 복사해 `.env` 로 저장 후 아래 값을 입력합니다.

```env
# 봇 토큰 (번호별 — 없는 번호는 인스턴스 미생성)
DISCORD_TOKEN_001=your_token_here
#DISCORD_TOKEN_002=your_token_here
#DISCORD_TOKEN_003=your_token_here

# PLAYNC 개발자센터 API 키
PLAYNC_API_KEY=your_api_key_here

# DB 경로 (fly.io Volume 마운트 경로로 변경)
DB_PATH=./data/bot.db
```

봇 토큰은 [Discord Developer Portal](https://discord.com/developers/applications) 에서 발급합니다.  
PLAYNC API 키는 [PLAYNC 개발자센터](https://developers.plaync.com) 에서 발급합니다.

---

## 봇 배치 (소환)

봇을 처음 서버에 추가하면 모든 채널에서 `소환 도돌봇001` 명령으로 배치합니다.

```
소환                ← 서버 내 모든 도돌봇 배치 현황 확인
소환 도돌봇001      ← 도돌봇001을 현재 텍스트 + 음성 채널로 배치
설정                ← 현재 이 봇의 배치 채널 확인
```

- 음성 채널은 명령 입력 시 사용자가 **입장해 있는 음성방**으로 자동 설정됩니다.
- 배치 후에는 설정된 텍스트 채널에서만 명령이 동작합니다.

---

## 명령어 목록

명령어 앞에 별도 접두사 없이 바로 입력합니다.

### 채널 설정

| 명령어 | 설명 |
|---|---|
| `소환` | 서버 내 모든 도돌봇 배치 현황 |
| `소환 도돌봇001` | 도돌봇001을 현재 채널로 배치 |
| `설정` | 현재 봇의 채널 설정 확인 |

---

### 보스 관리

#### 보스 목록 확인
```
보스
보스목록
보스리스트
```

고정 일정 보스가 맨 위, 이후 리스폰 시간 오름차순으로 표시됩니다.  
🔄 표시는 서버 재시작 시 스폰되는 보스로, 서버오픈 시 첫 등장 딜레이를 함께 표시합니다.

- `이름 컷` / `이름멍` 등 입력 시 **공백 유무 무관**하게 매칭됩니다 (예: `블랙릴리` ↔ `블랙 릴리`).

> 봇 배치 시 61개 기본 보스 + 고정 일정 보스가 자동으로 등록됩니다.  
> 기본 보스는 `보스삭제` 로 삭제할 수 없습니다.

#### 보스 삭제
```
보스삭제 체르투바
```

---

### 컷 / 멍 / 젠

**컷** — 보스를 처치했을 때 입력. 처치 시각 기준으로 다음 리스폰 자동 예약.

```
체르 컷               ← 지금 잡은 경우
컷 체르
체르투바 컷

체르 컷 0530          ← 05:30 에 잡은 경우 (HHMM 또는 HH:MM)
컷 체르 0530
05:30 체르 컷

ㅊㄹ ㅋ               ← 초성(3글자 이상) + ㅋ(컷 단축키)
ㅋ ㅊㄹ               ← 앞뒤 모두 가능
컷 ㅊㄹ

체르투바컷            ← 공백 없이 입력 가능
ㅊㄹㅌㅂㅋ            ← 초성+ㅋ 공백 없이
ㅋㅊㄹㅌㅂ            ← ㅋ를 앞에 붙여서
```

**멍** — 보스가 스폰됐지만 처치하지 못한 경우. 직전 예약 시각 기준으로 다음 리스폰 계산.

```
체르 멍
멍 체르
ㅊㄹ ㅁ               ← 초성(3글자 이상) + ㅁ(멍 단축키)
ㅁ ㅊㄹ               ← 앞뒤 모두 가능
체르투바멍            ← 공백 없이 입력 가능
ㅊㄹㅌㅂㅁ
```

**젠** — 보스가 뜬 시각을 기록. 입력 시각 기준으로 다음 리스폰 자동 예약.

```
체르 젠               ← 지금 뜬 경우
젠 체르
0000 체르투바 젠      ← 00:00 에 뜬 경우 (HHMM 또는 HH:MM)
0000 체르투바 스폰    ← 스폰 키워드도 가능
0000 체르투바         ← 키워드 생략 가능 (보스 이름이면 자동으로 젠 처리)
체르투바 0000         ← 보스명 + 시각 순서도 가능
체르투바젠            ← 공백 없이 입력 가능
ㅊㄹ ㅈ               ← 초성 + ㅈ(젠 단축키)
```

> 보스 이름만으로는 임의 예약이 불가합니다. `0000 밥 먹자` 처럼 보스 이름이 아닌 내용만 임의 예약 가능.

---

### 예약 관리

보스 출현 시각이 되면 **3단계 알림**이 자동 발송됩니다.

| 시점 | 색상 | 내용 |
|---|---|---|
| 5분 전 | 🟡 노랑 | ⏰ 5분 후 출현 |
| 1분 전 | 🟠 주황 | ⚠️ 1분 후 출현 |
| 정각 | 🔴 빨강 | ⚔️ 보스 출현! + TTS + **컷/멍 버튼** |

정각 알림에는 **✅ 컷 / 😶 멍** 버튼이 표시됩니다. 버튼을 누르면 바로 컷/멍 처리됩니다 (10분 유효).

```
보탐 / ㅂㅌ / ㅋ / z  ← 가까운 예약 5건 (전체 건수 표시)
보탐+ / ㅂㅌ+ / ㅋ+ / z+  ← 전체 예약 목록

Z                    ← 가까운 5건 + TTS 음성 알림

전체삭제              ← 고정 제외 모든 예약 삭제
초기화                ← 위와 동일

22:30 체르투바        ← 22:30 에 체르투바 알림 예약 (임의 내용도 가능)
22:30 밥 먹자         ← 22:30 에 '밥 먹자' 알림
```

#### 자동 미입력

정각 알림 후 **10분(기본값)** 안에 컷/멍 입력이 없으면 자동으로 다음 리스폰 예약이 생성됩니다.  
예약 이름 뒤에 `(미입력×N)` 이 표시되며, 컷/멍을 입력하면 사라집니다.

---

### 서버오픈

점검 후 서버 기동 시각 입력. 등록된 보스 전체를 리스폰 기준으로 미입력 예약.

```
서버오픈 05:00     ← 05:00 오픈 기준으로 전체 보스 예약
05:00 서버오픈     ← 동일
오픈 05:00         ← 동일
```

- 이미 예약된 보스는 스킵.

---

### 고정 일정 보스

매주 정해진 요일·시각에 자동으로 알림이 발송되는 보스입니다.

| 보스 | 요일 | 시각 |
|---|---|---|
| 타이런트 | 수 | 22:30 |
| 셀리호든 | 금 | 19:00 |
| 월드 보스 | 매일 | 12:00, 20:00 |
| 오만/신념의 탑 보스 | 매일 | 19:00 |

- 서버 기동 시 자동으로 다음 등장 시각으로 예약됩니다.
- 알림 발송 후 다음 등장 시각으로 자동 재예약됩니다.
- 컷/멍 처리 및 서버오픈 예약 대상에서 **제외**됩니다.

---

### 자동예약

컷/멍 미입력 시 자동으로 예약 처리되는 유예 시간 설정 (기본 10분).

```
자동예약                           ← 보스별 자동예약 시간 확인
자동예약 0:30:00 체르투바          ← 체르투바 자동예약 유예시간 30분으로 변경
```

---

### TTS

배치된 음성 채널에서 텍스트를 읽어줍니다. 음성: `ko-KR-SunHiNeural`.

```
ㅍ 좋은 아침입니다         ← 해당 텍스트를 음성 채널에서 읽어줌
v 좋은 아침입니다          ← 위와 동일
정신차려                   ← 봇 전체 재시작
재시작                     ← 위와 동일
```

---

### 시세 검색

PLAYNC 개발자센터 API를 통해 거래소 시세를 조회합니다. 서버별 최저가도 함께 표시됩니다.

```
시세 집행검                ← 아이템 검색 + 가격 통계 + 서버별 최저가
```

---

### 날씨

3일치 날씨를 표시합니다 (Open-Meteo API).

```
날씨                       ← 오늘 / 내일 / 모레 날씨 + 강수확률
```

---

### 미니게임

```
주사위                     ← 1~6 주사위
주사위 100                 ← 1~100 주사위
동전                       ← 앞면/뒷면

숫자게임시작               ← 1~100 숫자 맞추기 시작
숫자맞추기 42              ← 숫자 입력

뽑기                       ← 참여자 중 1명 추첨
뽑기3                      ← 참여자 중 3명 추첨
뽑기 철수 영희 민수        ← 지정 인원 중 추첨

경마                       ← 2마리 경마 (기본)
경마 철수 영희             ← 이름 지정 2마리
경마 A B C D               ← 이름 지정 4마리 (최대 8마리)

랭킹                       ← 미니게임 점수 TOP 10
```

---

### 도움말

```
메뉴                       ← 섹션별 명령어 안내 (8개 embed 순서대로 표시)
도움말                     ← 위와 동일
?                          ← 위와 동일
```

---

## 멀티 인스턴스

하나의 서비스에서 여러 봇을 동시에 운용할 수 있습니다.

```env
DISCORD_TOKEN_001=token_for_bot_001
DISCORD_TOKEN_002=token_for_bot_002
DISCORD_TOKEN_003=token_for_bot_003
```

- 토큰이 설정된 번호만 자동으로 인스턴스가 생성됩니다.
- 각 봇은 서버별로 독립된 채널/보스/예약 데이터를 가집니다.
- 같은 서버에 여러 봇을 운용하면 채널별로 분리 관리 가능합니다.

---

## fly.io 배포

### 최초 배포

```bash
# flyctl 설치
brew install flyctl

# 로그인
fly auth login

# 앱 생성
fly apps create dodol-bot

# Volume 생성 (DB 영구 저장)
fly volumes create dodolbot_data --region nrt --size 1 --app dodol-bot --yes

# 시크릿 설정
fly secrets set DISCORD_TOKEN_001=your_token PLAYNC_API_KEY=your_key --app dodol-bot

# 배포
fly deploy --app dodol-bot
```

배포 후 Discord에서 `소환 도돌봇001` 을 입력해 채널을 등록합니다.

### 코드 업데이트

```bash
fly deploy --app dodol-bot
```

### 환경변수

| 변수 | 설명 |
|---|---|
| `DISCORD_TOKEN_001` | 봇 토큰 |
| `PLAYNC_API_KEY` | PLAYNC 개발자센터 API 키 |
| `DB_PATH` | `/app/data/bot.db` (fly.toml에 기본 설정) |

> fly.io Tokyo(nrt) 리전 기준, KST(UTC+9) 시간으로 동작합니다.

---

## 프로젝트 구조

```
DiscordBot/
├── main.py                  # 진입점 — 멀티 인스턴스 실행
├── requirements.txt
├── Dockerfile               # fly.io 빌드 (Python 3.11 + FFmpeg)
├── fly.toml                 # fly.io 앱 설정 + Volume 마운트
├── .env.example
├── .gitignore
├── LICENSE
├── data/
│   └── bot.db               # SQLite DB (fly.io Volume 마운트)
└── src/
    ├── db.py                # DB 초기화 / 연결 / 기본 보스 목록
    ├── korean.py            # 초성 검색, 부분 매칭
    └── cogs/
        ├── setup.py         # 소환, 채널 설정
        ├── boss.py          # 보스 관리/컷/멍/예약/서버오픈/고정일정
        ├── tts.py           # TTS + 봇 재시작
        ├── market.py        # PLAYNC 시세 API
        ├── minigame.py      # 미니게임, 뽑기, 경마
        ├── weather.py       # 날씨 (Open-Meteo)
        └── help.py          # 도움말 (섹션별 embed)
```

---

## 상세 명세

| 문서 | 내용 |
|---|---|
| [docs/db-schema.md](docs/db-schema.md) | DB 테이블 구조 및 컬럼 설명 |
| [docs/boss-data.md](docs/boss-data.md) | 기본 보스 전체 목록 및 데이터 기준 |
| [docs/notification-logic.md](docs/notification-logic.md) | 알림 타이밍, 자동 재예약, 자동 미입력 흐름 |
| [docs/error-cases.md](docs/error-cases.md) | 예외 상황별 봇 동작 |
| [docs/test-spec.md](docs/test-spec.md) | E2E 테스트 17개 항목 및 검증 기준 |

---

## 저작권

Copyright (c) 2025 [korleinster](https://github.com/korleinster) — All Rights Reserved.
