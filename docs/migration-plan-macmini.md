# 배포 인프라 이력 및 현황

---

## 현재 구조 (2026-06-08~)

**Primary**: Mac Mini (Ubuntu 26.04, Docker Compose)  
**DR Standby**: Fly.io (도쿄 nrt, scale=0 대기)

```
git push origin main
        ↓
GitHub Actions
   ├── Mac Mini: git pull + docker compose up  (수동 배포)
   └── Fly.io: 이미지 빌드 + 레지스트리 푸시 (자동, scale=0 유지)

맥미니 장애 시:
   bash scripts/fly-activate.sh  →  ~2분 내 Discord 온라인
```

---

## Fly.io DR 구성

| 항목 | 값 |
|---|---|
| 앱 | dodol-bot-001 ~ 004 |
| 리전 | nrt (도쿄) |
| 머신 | shared-cpu-1x, 256MB |
| Volume | dodol_bot_001~004_data (1GB 각) |
| 평시 상태 | scale=0 (비용 없음) |
| DB 동기화 | 매일 03:00 KST 맥미니 cron → fly-sync-db.sh |

### 활성화 (맥미니 장애 시)

```bash
bash scripts/fly-activate.sh     # scale=1 → ~2분 후 온라인
```

### 비활성화 (맥미니 복구 후)

```bash
bash scripts/fly-deactivate.sh   # scale=0 → staging 복귀
```

### Fly.io secrets 재등록 (토큰 변경 시)

```bash
# 맥미니에서
cd ~/dodol-bot
for n in 001 002 003 004; do
    flyctl secrets import --app "dodol-bot-$n" < .env
done
```

> ⚠️ **동시 실행 불가** — 맥미니와 fly.io가 같은 Discord 토큰으로 동시 실행 시 Discord가 한쪽을 강제 종료. 완전한 failover 구조.

---

## Mac Mini 서버 정보

| 항목 | 값 |
|---|---|
| OS | Ubuntu 26.04 LTS |
| Local IP | `192.168.31.68` |
| Tailscale IP | `100.109.220.64` |
| SSH alias | `macmini` (로컬 .zshrc) |
| 프로젝트 경로 | `/home/leinster/dodol-bot/` |
| DB 경로 | `/home/leinster/dodol-bot/data/bot.db` |

---

## 이전 이력

### 2026-06-06: Fly.io → Mac Mini 이전 완료

| 항목 | Fly.io (구) | Mac Mini (현) |
|---|---|---|
| 실행 환경 | 관리형 컨테이너 | Ubuntu + Docker Compose |
| DB | Fly.io 볼륨 `/app/data/bot.db` | 로컬 볼륨 마운트 |
| 시크릿 | `fly secrets` | `.env` 파일 |
| 봇 수 | 001~003 | 001~004 |

## M44/W8 deployment boundary (2026-07-30)

The local multi-bot bridge implementation and production rollout are
owner-approved; evidence remains pending. Each bot receives a separate token,
numbered Unix socket, and separately sourced HMAC secret at runtime. Compose
maps only the matching token and HMAC secret into each service; sibling
credentials are absent from that container. Secrets are never copied into the
shared image or committed files. Build the hardened
multi-stage image once, then recreate services sequentially `004 → 001 → 002 →
003`, recording health and socket/capability evidence before each next step.
If a gate fails, roll back only that bot and stop; later bots remain untouched.
Preserve the database. No new port is opened
and Tailscale configuration is unchanged.

### 2026-06-08: Fly.io DR 이중화 추가

맥미니 장애 대비 fly.io를 staging 상태로 상시 대기.  
GitHub Actions가 `git push` 시마다 fly.io 이미지를 자동으로 최신 상태로 유지.
