#!/bin/bash
# fly-deactivate.sh
# 맥미니 복구 후 fly.io staging 모드로 되돌리기
# 실행 전: 맥미니 봇이 정상 복구되었는지 확인할 것

APPS=("dodol-bot-001" "dodol-bot-002" "dodol-bot-003" "dodol-bot-004")

echo "🔄 fly.io staging 모드로 전환 중..."
echo ""

for APP in "${APPS[@]}"; do
    flyctl scale count 0 --app "$APP" --yes
    echo "⏸  $APP 비활성화"
done

echo ""
echo "✅ fly.io 전체 비활성화 완료 (staging 대기 상태)"
echo "   맥미니가 다시 primary입니다."
