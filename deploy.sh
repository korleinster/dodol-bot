#!/bin/bash
# deploy.sh — DB 백업 후 Fly.io 배포
# 사용법: ./deploy.sh [fly deploy 추가 옵션]

set -e

BACKUP_DIR="backups"
mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/bot_$(date +%Y%m%d_%H%M%S).db"

echo "📦 [1/2] DB 백업 중..."
if fly sftp get --app dodol-bot /app/data/bot.db "$BACKUP_FILE" 2>/dev/null; then
    SIZE=$(wc -c < "$BACKUP_FILE")
    echo "✅ 백업 완료: $BACKUP_FILE (${SIZE} bytes)"
else
    echo "⚠️  DB 백업 실패 (봇 오프라인이거나 볼륨이 비어있을 수 있음)"
    printf "그냥 배포하시겠습니까? (y/N) "
    read -r yn
    [[ "$yn" == [yY] ]] || { echo "배포 취소"; exit 1; }
    rm -f "$BACKUP_FILE"
fi

echo ""
echo "🚀 [2/2] 배포 중..."
fly deploy --app dodol-bot "$@"
