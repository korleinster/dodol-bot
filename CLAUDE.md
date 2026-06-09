# 도돌봇 개발 규칙

## 배포 원칙

- 배포(맥미니 SSH → docker compose 명령)는 **반드시 사용자 확인 후** 진행할 것
- 코드 변경 → 커밋 → 푸시까지는 자율 진행 가능, 배포 명령어 실행은 사용자 승인 필요

## 변경 발생 시 필수 작업

코드나 기능에 변경이 생기면 아래 순서를 반드시 따를 것:

1. **명세서 갱신** — `docs/` 내 관련 파일 업데이트
2. **인앱 메뉴 갱신** — 명령어 추가/변경/삭제 시 `src/cogs/help.py`의 `_build_embeds` 함께 수정
3. **README 갱신** — 변경 내용이 README에 반영되어야 하면 업데이트
4. **커밋**
5. **푸시** — `git push origin main`

## 배포 후 필수 작업

배포(맥미니 `docker compose up -d`) 완료 후 **반드시** 전체 문서를 갱신할 것:

- `README.md` — 신규 기능, 명령어, 구조 변경 반영
- `docs/` — 관련 명세 파일 업데이트
- `CLAUDE.md` — 운영 규칙/절차 변경 시 반영
- 커밋 & 푸시

## 배포 방법

**맥미니 Ubuntu + Docker Compose 기반 (봇별 독립 컨테이너)**  
SSH 접속 후 아래 순서로 진행:

```bash
# 맥미니 SSH 접속
ssh leinster@192.168.31.68          # 로컬
ssh leinster@100.109.220.64         # 외부 (Tailscale)

cd ~/dodol-bot
git pull origin main
```

**전체 재배포** (코드 대규모 변경 시):
```bash
export GIT_COMMIT=$(git rev-parse --short HEAD)
docker compose build
docker compose up -d
```

**순차 배포** (봇별 롤링 업데이트, 영향 최소화):
```bash
export GIT_COMMIT=$(git rev-parse --short HEAD)
docker compose build
docker compose up -d --no-deps dodol-bot-001
docker compose up -d --no-deps dodol-bot-002
docker compose up -d --no-deps dodol-bot-003
docker compose up -d --no-deps dodol-bot-004
```

**버전 확인** (각 컨테이너 배포 커밋 해시):
```bash
docker compose logs --tail=5 | grep "commit:"
```

**특정 봇만 재시작**:
```bash
docker compose restart dodol-bot-003
```

**신규 봇 추가** (docker-compose.yml에 서비스 추가 후):
```bash
docker compose up -d --no-deps dodol-bot-005
```

**로그 확인**:
```bash
docker compose logs -f dodol-bot-003
```

- DB 위치: `/home/leinster/dodol-bot/data/bot.db` (Docker 볼륨 마운트, 공유)
- 컨테이너 재빌드해도 DB 보존됨
- 자동 백업: 매일 04:00 KST `backup.sh` cron 실행 → Google Drive `dodol-bot-backups/` (7일 보관)
- 수동 백업: `~/dodol-bot/backup.sh`

## fly.io 이중화 (DR — 도쿄 리전 nrt)

**목적**: 맥미니 장애 시 즉시 전환할 수 있는 staging 상태 유지  
**구조**: 코드는 항상 최신 (GitHub Actions 자동 배포), DB는 매일 덮어씌우기  
**주의**: 맥미니와 fly.io는 동시 실행 불가 (Discord 토큰 충돌) — 진짜 failover 전용

### fly.io 앱 구성
| 앱 이름 | BOT_NUMBER | Volume | 리전 |
|---------|-----------|--------|------|
| dodol-bot-001 | 1 | dodol_bot_001_data | nrt |
| dodol-bot-002 | 2 | dodol_bot_002_data | nrt |
| dodol-bot-003 | 3 | dodol_bot_003_data | nrt |
| dodol-bot-004 | 4 | dodol_bot_004_data | nrt |

### 자동 배포
`git push origin main` → GitHub Actions → fly.io 4개 앱 빌드 + 배포 + scale=0 유지

### 긴급 활성화 (맥미니 다운 시)
```bash
bash scripts/fly-activate.sh     # fly.io 전체 봇 scale=1 → ~30초 후 온라인
```

### 복구 후 비활성화
```bash
bash scripts/fly-deactivate.sh   # fly.io 전체 봇 scale=0 → staging 대기
```

### DB 동기화 (맥미니에서 실행, flyctl 필요)
```bash
bash ~/dodol-bot/scripts/fly-sync-db.sh   # 수동 실행
# cron: 0 18 * * * (KST 03:00) 자동 실행 중
```

### fly.io secrets 재등록 (최초 1회 또는 토큰 변경 시)
```bash
# 맥미니에서 실행
for n in 001 002 003 004; do
    flyctl secrets import --app "dodol-bot-$n" < ~/dodol-bot/.env
done
```

### fly.io 상태 확인
```bash
flyctl apps list
flyctl status --app dodol-bot-001
```
