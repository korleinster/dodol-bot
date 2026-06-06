#!/bin/bash
# deploy.sh — Mac Mini 배포 가이드
# 실제 배포는 Mac Mini에서 직접 실행

echo "📋 Mac Mini 배포 절차:"
echo ""
echo "  1. SSH 접속"
echo "     ssh leinster@192.168.31.68        # 로컬"
echo "     ssh leinster@100.109.220.64       # 외부 (Tailscale)"
echo ""
echo "  2. 코드 업데이트 & 재배포"
echo "     cd ~/dodol-bot"
echo "     git pull origin main"
echo "     docker compose up -d --build"
echo "     docker compose logs -f            # 온라인 확인"
echo ""
echo "  3. DB 백업 (선택)"
echo "     cp ~/dodol-bot/data/bot.db ~/dodol-bot/backups/bot_\$(date +%Y%m%d).db"
