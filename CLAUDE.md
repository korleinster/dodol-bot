# 도돌봇 개발 규칙

## 변경 발생 시 필수 작업

코드나 기능에 변경이 생기면 아래 순서를 반드시 따를 것:

1. **명세서 갱신** — `docs/` 내 관련 파일 업데이트
2. **인앱 메뉴 갱신** — 명령어 추가/변경/삭제 시 `src/cogs/help.py`의 `_build_embeds` 함께 수정
3. **README 갱신** — 변경 내용이 README에 반영되어야 하면 업데이트
4. **커밋**
5. **푸시** — `git push origin main`

## 배포 방법

**맥미니 Ubuntu + Docker Compose 기반**  
SSH 접속 후 아래 순서로 진행:

```bash
# 맥미니 SSH 접속
ssh leinster@192.168.31.68          # 로컬
ssh leinster@100.109.220.64         # 외부 (Tailscale)

# 코드 업데이트 & 재배포
cd ~/dodol-bot
git pull origin main
docker compose up -d --build
docker compose logs -f              # 온라인 확인
```

- DB 위치: `/home/leinster/dodol-bot/data/bot.db` (Docker 볼륨 마운트)
- 컨테이너 재빌드해도 DB 보존됨
- 백업: `cp ~/dodol-bot/data/bot.db ~/dodol-bot/backups/bot_$(date +%Y%m%d).db`
