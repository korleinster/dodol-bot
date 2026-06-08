#!/bin/bash
# fly-sync-db.sh
# 맥미니 → fly.io 각 앱 Volume에 DB 덮어씌우기
# 맥미니에서 실행 (flyctl 설치 + 로그인 필요)
# 권장: 매일 새벽 3시 cron 실행
#
# crontab 등록 예시:
#   0 18 * * * /bin/bash ~/dodol-bot/scripts/fly-sync-db.sh >> ~/dodol-bot/logs/fly-sync.log 2>&1
#   (KST 03:00 = UTC 18:00 전날)

set -e

DB_SRC="$HOME/dodol-bot/data/bot.db"
APPS=("dodol-bot-001" "dodol-bot-002" "dodol-bot-003" "dodol-bot-004")
VOLUMES=("dodol_bot_001_data" "dodol_bot_002_data" "dodol_bot_003_data" "dodol_bot_004_data")

echo "[fly-sync-db] 시작: $(date '+%Y-%m-%d %H:%M:%S')"

if [ ! -f "$DB_SRC" ]; then
    echo "[fly-sync-db] 오류: DB 파일 없음 ($DB_SRC)"
    exit 1
fi

for i in "${!APPS[@]}"; do
    APP="${APPS[$i]}"
    VOLUME="${VOLUMES[$i]}"
    echo ""
    echo "[$APP] DB 업로드 시작..."

    # 알파인 임시 머신으로 볼륨 마운트 (Discord 봇 미실행 — 토큰 충돌 없음)
    MACHINE_ID=$(flyctl machine run alpine \
        --app "$APP" \
        --volume "${VOLUME}:/data" \
        --restart no \
        --command "sleep 120" \
        --detach \
        --quiet 2>&1 | tail -1)

    echo "[$APP] 임시 머신 시작: $MACHINE_ID"
    sleep 15

    # DB 업로드
    flyctl sftp put --app "$APP" "$DB_SRC" /data/bot.db
    echo "[$APP] 업로드 완료"

    # 임시 머신 강제 종료
    flyctl machine destroy "$MACHINE_ID" --app "$APP" --force --yes 2>/dev/null || true
    echo "[$APP] 임시 머신 정리 완료"
done

echo ""
echo "[fly-sync-db] 전체 완료: $(date '+%Y-%m-%d %H:%M:%S')"
