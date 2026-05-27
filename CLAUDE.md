# 도돌봇 개발 규칙

## 변경 발생 시 필수 작업

코드나 기능에 변경이 생기면 아래 순서를 반드시 따를 것:

1. **명세서 갱신** — `docs/` 내 관련 파일 업데이트
2. **README 갱신** — 변경 내용이 README에 반영되어야 하면 업데이트
3. **커밋**
4. **푸시** — `git push origin main`

## 배포 방법

**반드시 `deploy.sh` 사용** — DB 백업 후 배포:

```bash
./deploy.sh
```

- 배포 전 Fly.io 볼륨의 SQLite DB를 `backups/` 폴더에 자동 백업
- `fly deploy --app dodol-bot` 직접 실행 금지 (DB 백업 누락 위험)
- 복구 시: `fly ssh console --app dodol-bot -C "sqlite3 /app/data/bot.db" < backups/bot_YYYYMMDD_HHMMSS.sql`
