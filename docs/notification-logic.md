# 알림 로직 명세

`src/cogs/boss.py`의 `check_schedules` 태스크 기준.

---

## 실행 주기

`@tasks.loop(seconds=30)` — 30초마다 실행.  
봇 준비 완료(`wait_until_ready`) 후 시작.

---

## 알림 3단계 타이밍

루프 실행 시각을 `now`라 할 때, 각 단계의 발송 구간:

| 단계 | 구간 | 조건 |
|---|---|---|
| 5분 전 | `now+90s < scheduled_at ≤ now+330s` | `warned_5min=0` |
| 1분 전 | `now+60s < scheduled_at ≤ now+90s` | `warned_1min=0` |
| 정각 | `scheduled_at ≤ now+60s` | `notified=0` |

> 구간이 겹치지 않도록 설계되어 있음.  
> 정각 구간은 `scheduled_at`가 이미 지난 경우도 포함 (미처리 건 처리).

### 발송 후 상태 변경

| 단계 | 변경 |
|---|---|
| 5분 전 | `warned_5min=1` |
| 1분 전 | `warned_5min=1, warned_1min=1` |
| 정각 | `warned_5min=1, warned_1min=1, notified=1` |

---

## 정각 알림 내용

- **embed 색상**: 빨강 (`0xED4245`)
- **타이틀**: 보스 예약이면 `⚔️ 보스 출현!`, 임의 예약이면 `⏰ 예약 알림`
- **본문**: `{content} {미입력×N} — {남은시간}`
  - 남은 시간 > 0: `N분 후`
  - 남은 시간 ≤ 0: `출현 중`
- **버튼**: 일반 보스 예약에만 `✅ 컷 / 😶 멍` 버튼 (View, 유효시간 600초)
  - 고정 보스(`is_fixed=1`)는 버튼 미표시
- **TTS**: 정각 알림 시 음성 채널에서 `{content} {미입력} {남은시간}` 자동 읽기

---

## 고정 보스 자동 재예약

정각 알림 처리 후(`notified=1`), 고정 보스의 다음 등장 시각을 자동으로 예약.

### 흐름

```
1. bosses 테이블에서 fixed=1인 보스 전체 조회
2. 보스별로 notified=0 예약이 이미 있으면 SKIP
3. next_fixed_occurrence(fixed_days, fixed_time) 로 다음 시각 계산
4. next_at <= now+60s 이면 SKIP (현재 처리 중인 시각 재예약 방지)
5. INSERT OR IGNORE 로 새 예약 생성
```

> **SKIP 조건 4번 이유**: 정각 알림 발송 직후 `notified=1`로 바뀌지만,  
> `next_fixed_occurrence`가 아직 미래인 동일 시각을 반환할 수 있음.  
> `now+60s` 버퍼로 이 경우를 차단.

### next_fixed_occurrence 알고리즘

오늘부터 최대 8일을 탐색해 `fixed_days`와 `fixed_time` 조합 중  
`> now`인 가장 가까운 시각을 반환.

---

## 자동 미입력 처리

정각 알림 후 `auto_schedule_seconds`(기본 10분) 내에 컷/멍 입력이 없으면  
자동으로 다음 리스폰 예약 생성.

### 흐름

```
1. notified=1 이고 fixed=0 인 예약 전체 조회 (DESC)
2. boss_name 기준으로 가장 최근 notified 행 1개씩 추출
3. scheduled_at + auto_schedule_seconds > now 이면 SKIP (유예시간 내)
4. notified=0 예약이 이미 있으면 SKIP (컷/멍 처리 완료)
5. new_at = scheduled_at + respawn_seconds 로 다음 예약 INSERT
6. miss_count = 이전 miss_count + 1
```

> `miss_count`는 컷/멍 처리 시 0으로 초기화됨.

---

## 알림 남은 시간 표시 (`fmt_remain`)

```python
total = int(delta.total_seconds())
if total <= 0:  → "출현 중"
elif total < 3600:  → "N분 후"
else:  → "N시간 N분 후"
```

---

## 보탐(예약 목록) 표시 기준

- `notified=0` 행만 표시
- `보탐` / `ㅋ` / `z`: `is_fixed=0` 필터 + 최대 5건
- `보탐+` / `ㅋ+` / `z+`: 전체 (고정 포함) 건수 제한 없음
- `Z`: 보탐 5건 + 1순위 보스 TTS 읽기
