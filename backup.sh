#!/bin/bash
# backup.sh — DB 백업 후 Google Drive 업로드
# 매일 04:00 KST cron으로 자동 실행 (맥미니)
# crontab: 0 4 * * * /home/leinster/dodol-bot/backup.sh >> /home/leinster/dodol-bot/backups/backup.log 2>&1

BACKUP_DIR="/home/leinster/dodol-bot/backups"
DB_PATH="/home/leinster/dodol-bot/data/bot.db"
DATE=$(date +%Y%m%d)
BACKUP_FILE="$BACKUP_DIR/bot_$DATE.db"

mkdir -p "$BACKUP_DIR"

# DB 복사
cp "$DB_PATH" "$BACKUP_FILE" && echo "[$DATE] 백업 완료: $BACKUP_FILE"

# Google Drive 업로드
/usr/bin/rclone copy "$BACKUP_DIR/" gdrive:dodol-bot-backups/ --include "*.db" 2>&1 && echo "[$DATE] Drive 업로드 완료"

# Google Drive 7일 이상 된 백업 삭제
/usr/bin/rclone delete gdrive:dodol-bot-backups/ --min-age 7d --include "*.db" 2>&1 && echo "[$DATE] Drive 구버전 정리 완료"

# 로컬 백업 30일 이상 된 파일 삭제
find "$BACKUP_DIR" -name "bot_*.db" -mtime +30 -delete
