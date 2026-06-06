# 도돌봇 개발 규칙

## 변경 발생 시 필수 작업

코드나 기능에 변경이 생기면 아래 순서를 반드시 따를 것:

1. **명세서 갱신** — `docs/` 내 관련 파일 업데이트
2. **인앱 메뉴 갱신** — 명령어 추가/변경/삭제 시 `src/cogs/help.py`의 `_build_embeds` 함께 수정
3. **README 갱신** — 변경 내용이 README에 반영되어야 하면 업데이트
4. **커밋**
5. **푸시** — `git push origin main`

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
- 백업: `cp ~/dodol-bot/data/bot.db ~/dodol-bot/backups/bot_$(date +%Y%m%d).db`
