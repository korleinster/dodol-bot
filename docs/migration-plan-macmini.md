# Mac Mini 이전 플랜 (Fly.io → Ubuntu + Docker)

> 백로그 항목. 실행 전 아래 순서대로 진행할 것.

---

## 구성 변경 요약

| 항목 | 현재 (Fly.io) | 이후 (Mac Mini) |
|---|---|---|
| 실행 환경 | Fly.io 관리형 컨테이너 | Ubuntu + Docker Compose |
| DB | Fly.io 볼륨 `/app/data/bot.db` | 로컬 볼륨 마운트 |
| 시크릿 | `fly secrets` | `.env` 파일 (서버 로컬) |
| 재시작 | Fly.io 자동 | `restart: always` + systemd |
| 봇 수 | 001~003 (현재) | 동일 |

---

## 사전 준비 (Mac Mini 세팅)

### 1. Docker 설치

```bash
# Ubuntu에서
sudo apt update && sudo apt install -y ca-certificates curl gnupg
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt update && sudo apt install -y docker-ce docker-compose-plugin
sudo usermod -aG docker $USER
```

### 2. 프로젝트 클론

```bash
git clone https://github.com/korleinster/dodol-bot.git /opt/dodol-bot
cd /opt/dodol-bot
```

### 3. docker-compose.yml 작성

`/opt/dodol-bot/docker-compose.yml` 생성:

```yaml
services:
  dodol-bot:
    build: .
    restart: always
    env_file: .env
    volumes:
      - ./data:/app/data
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "5"
```

### 4. .env 파일 생성

```bash
# /opt/dodol-bot/.env  (git에 절대 올리지 말 것)
DISCORD_TOKEN_001=...
DISCORD_TOKEN_002=...
DISCORD_TOKEN_003=...
DB_PATH=./data/bot.db
```

### 5. 사전 빌드 & 테스트 (DB 없이)

```bash
cd /opt/dodol-bot
docker compose build
docker compose up --no-start   # 실행은 아직 안 함
```

---

## 전환 당일 절차

> **핵심 원칙**: Fly.io 중단 → DB 스냅샷 → Mac Mini 시작 순서 엄수.  
> 중간에 두 환경이 동시에 같은 DB를 보는 일이 없어야 함.

### Step 1. 최신 코드 동기화

```bash
# Mac Mini에서
cd /opt/dodol-bot && git pull origin main
docker compose build
```

### Step 2. Fly.io 봇 중단

```bash
# 로컬 맥에서
fly scale count 0 --app dodol-bot
```

봇 3개가 모두 오프라인으로 전환되는 것 Discord에서 확인.

### Step 3. DB 최종 백업 & 다운로드

```bash
# 로컬 맥에서 (deploy.sh 방식과 동일)
fly ssh console --app dodol-bot -C "sqlite3 /app/data/bot.db .dump" > backups/bot_final_migration.sql

# 또는 바이너리 파일 직접 복사
fly sftp get /app/data/bot.db backups/bot_final_migration.db
```

### Step 4. DB를 Mac Mini로 전송

```bash
# 로컬 맥에서
scp backups/bot_final_migration.db user@macmini-ip:/opt/dodol-bot/data/bot.db
```

### Step 5. Mac Mini에서 봇 시작

```bash
# Mac Mini에서
cd /opt/dodol-bot
docker compose up -d
docker compose logs -f   # 001/002/003 온라인 확인
```

### Step 6. 정상 동작 확인

- Discord에서 3개 봇 온라인 상태 확인
- `보탐` 명령으로 기존 예약 데이터 보존 여부 확인
- 컷/멍/보탐 등 주요 기능 테스트

### Step 7. Fly.io 정리 (확인 후 진행)

```bash
# 이상 없으면 Fly.io 앱 완전 삭제
fly apps destroy dodol-bot
```

---

## 재시작 자동화 (Mac Mini 리부팅 대비)

```bash
# Docker가 systemd에 의해 자동 시작되도록 설정
sudo systemctl enable docker

# docker compose 자체를 systemd 서비스로 등록
sudo nano /etc/systemd/system/dodol-bot.service
```

```ini
[Unit]
Description=Dodol Bot
Requires=docker.service
After=docker.service

[Service]
WorkingDirectory=/opt/dodol-bot
ExecStart=/usr/bin/docker compose up
ExecStop=/usr/bin/docker compose down
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable dodol-bot
sudo systemctl start dodol-bot
```

---

## 주의사항

- `.env` 파일은 git에 올리지 말 것 (`.gitignore`에 이미 포함되어 있음)
- DB는 `./data/` 폴더에 볼륨 마운트 → 컨테이너 재빌드 시에도 보존됨
- 정기 백업 cron 권장:
  ```bash
  # crontab -e
  0 4 * * * cp /opt/dodol-bot/data/bot.db /opt/dodol-bot/backups/bot_$(date +\%Y\%m\%d).db
  ```
- Mac Mini IP가 바뀌는 경우 대비해 고정 IP 또는 tailscale 설정 권장

---

## 롤백 플랜

Mac Mini에서 문제 발생 시:

```bash
# Mac Mini 봇 중단
docker compose down

# Fly.io 재시작
fly scale count 1 --app dodol-bot
```

DB는 Fly.io 볼륨에 그대로 있으므로 데이터 손실 없음.  
(Step 3에서 뽑은 DB를 fly.io 볼륨에 복원할 필요는 없음 — 전환 직전 스냅샷이 Fly.io 볼륨에 그대로임)
