#!/bin/bash
# fly-activate.sh
# 맥미니 장애 시 fly.io 긴급 활성화
# 실행 전: 맥미니 봇이 완전히 다운된 상태인지 확인할 것
# (동시 실행 시 Discord 토큰 충돌 발생)

APPS=("dodol-bot-001" "dodol-bot-002" "dodol-bot-003" "dodol-bot-004")
CONFIGS=("fly.001.toml" "fly.002.toml" "fly.003.toml" "fly.004.toml")

echo "🚨 fly.io 긴급 활성화 시작..."
echo "   리전: 도쿄 (nrt)"
echo ""

for i in "${!APPS[@]}"; do
    APP="${APPS[$i]}"
    CFG="${CONFIGS[$i]}"
    echo "[$APP] 배포 중..."
    flyctl deploy --config "$CFG" --app "$APP" \
        --remote-only \
        --strategy immediate \
        --smoke-checks=false
    echo "✅ $APP 활성화"
done

echo ""
echo "🟢 전체 봇 활성화 완료"
echo "   약 30~60초 후 Discord 온라인 상태가 됩니다."
echo ""
echo "비활성화할 때: bash scripts/fly-deactivate.sh"
