# 알림 로직 명세

`src/cogs/boss.py`의 `check_schedules` 태스크 기준.

---

## 실행 주기

`@tasks.loop(seconds=1)` — **1초**마다 실행.  
봇 준비 완료(`wait_until_ready`) 후 시작.

> **시각 정밀도**: `scheduled_at`은 초 단위까지 저장.  
> 컷/멍/젠 명령에서 시각을 직접 지정(`HH:MM`)하면 `second=0`으로 정규화됨.  
> 시각 지정 없이 입력하면 `now()` 그대로(초 포함) 사용.

봇 시작 순서는 `login → cog 로드 → web bridge → connect`다. 따라서
`wait_until_ready()`를 사용하는 보스·날씨 루프는 Discord 클라이언트가
초기화되기 전에 생성되지 않는다. 복구가 실패하면 루프 본문에서 다음 틱에
다시 시도하며, 성공 후 첫 실제 틱부터 `ready` 상태가 된다.

---

## 알림 3단계 타이밍

루프 실행 시각을 `n`이라 할 때 각 단계의 발송 구간:

| 단계 | 구간 | 조건 |
|---|---|---|
| 5분 전 | `n+90s < scheduled_at ≤ n+330s` | `warned_5min=0` |
| 1분 전 | `n+2s  < scheduled_at ≤ n+90s`  | `warned_1min=0` |
| 정각   | `scheduled_at ≤ n+2s`           | `notified=0` |

각 봇의 설정된 텍스트 채널로 실제 전송된 5분·1분·정각 알림은 해당 봇의
웹 bridge 전체 발화 스트림에도 기록된다. 이 동기화는 보스 스케줄러 전용
후킹이 아니라 각 봇 자신의 Discord 발화 이벤트를 기준으로
하므로 임의 예약 알림과 다른 정상 봇 응답도 동일한 방식으로 웹에 전달된다.

> 루프가 1초 간격이므로 정각 알림의 최대 오차는 **±2초** 이내.  
> 정각 구간은 `scheduled_at`이 이미 지난 경우도 포함 (미처리 건 처리).  
> 구간이 겹치지 않도록 설계: t2(2초) / t90(90초) / t330(330초) 경계값 사용.

### 발송 후 상태 변경

| 단계 | 변경 |
|---|---|
| 5분 전 | `warned_5min=1` |
| 1분 전 | `warned_5min=1, warned_1min=1` |
| 정각   | `warned_5min=1, warned_1min=1, notified=1` |

각 상태 변경은 Discord/TTS 같은 외부 전송 전에 조건부 `UPDATE`로 선점한다.
같은 행을 두 번 처리하지 않기 위한 계약이다. Discord가 명확한 5xx
(`DiscordServerError`)로 메시지를 거부한 경우에만 선점을 되돌리고
`5초 → 15초 → 30초 → 60초 → 120초` 간격으로 최대 5회 재시도한다.
재시도 상태는 `schedules`에 보존돼 재시작 뒤에도 유지된다. 전송 성공 시에만
재시도 메타데이터를 지운다.

타임아웃처럼 Discord가 이미 수락했을 가능성을 배제할 수 없는 오류는
중복 보스·TTS 알림을 막기 위해 자동 재전송하지 않는다. 이런 경우와 재시도
한도 초과는 `delivery_error_code`와 안전한 스케줄러 오류 코드로 기록된다. 한 행의 5xx는 같은 틱의
다른 알림·자동 재예약 처리를 막지 않는다. 동일 장애 알림은 5분 동안 한 번만
보내고, 정상 틱으로 회복한 뒤 새로 발생한 장애는 다시 한 번 보고한다.

### 런타임 stale 일정 정리 (W15)

시작 bootstrap과 일반 루프 모두 stale 미처리 행을 발견하면 같은 직렬화
reconciliation을 실행한다. 기준 시각 `r`의 정확한 경계는
`cutoff = r - 15초`다.

| 예약 시각 | 동작 |
|---|---|
| `scheduled_at < cutoff` | 채널 조회·Discord·TTS 없이 `notified=1`로 무음 완료. `delivery_retry_after`도 지운다. |
| `scheduled_at = cutoff` | 유예 창에 포함. 정각 알림 후보로 남아 조건부 선점 뒤 최대 한 번 발송한다. |
| `cutoff < scheduled_at ≤ n+2초` | 기존 정각 알림 규칙으로 처리한다. |

`BEGIN IMMEDIATE` write transaction이 stale 완료, 중복 제거, 다음 예약 생성까지
묶는다. 따라서 빠른 gateway 재접속이나 다음 1초 tick이 겹쳐도 일반/고정 보스의
미발송 미래 예약을 중복 생성하거나 같은 행의 Discord/TTS 알림을 재생하지 않는다.
15초 창 안에 서로 다른 정상 예약이 여러 개 있으면 각각의 기존 조건부 claim이
적용되며, 창 밖의 backlog는 절대 replay하지 않는다.

- **일반 보스**: 창 밖 행을 무음 완료한 뒤 최신 완료 시각과 `respawn_seconds`를
  미래까지 전진해 다음 한 건을 만든다. 창 안 행은 그대로 정각 처리한다.
- **고정 보스**: 창 밖 행을 무음 완료한 뒤 설정된 다음 미래 고정 시각 한 건을
  만든다. 창 안 행은 그대로 처리하며, 정각 처리 뒤 기존 고정 재예약 규칙을 따른다.
- **임의 예약** (`boss_name IS NULL`): 창 밖 행은 무음 완료하고 새 행을 만들지
  않는다. 창 안 행만 기존 정각 알림으로 최대 한 번 발송한다.

다른 `bot_number`는 reconciliation 범위 밖이다. `소환` 중에는 대상 길드만 같은
트랜잭션으로 reconciliation하므로 다른 길드의 데이터는 변경하지 않는다.

### Discord 5xx 재시도와 late window

명시적 Discord 5xx (`DiscordServerError`)만 5초 → 15초 → 30초 → 60초 → 120초
간격으로 최대 5회 재시도한다. 재시도 상태는 DB에 남고, 재시작 뒤에도 유예 창
안이면 `delivery_retry_after`가 될 때까지 기다린다. 다만 계산한 다음 retry 시각이
`scheduled_at + 15초`를 넘으면 선점을 되돌리지 않고
`DISCORD_SERVER_ERROR_WINDOW_EXPIRED`로 종료한다. 이 절대 deadline은 오래된
재접속 backlog 또는 retry storm을 막는다.

---

## 정각 알림 내용

- **embed 색상**: 빨강 (`0xED4245`)
- **타이틀**: 보스 예약이면 `⚔️ 보스 출현!`, 임의 예약이면 `⏰ 예약 알림`
- **본문**: `{content} {미입력×N} — {남은시간}`
  - 남은 시간 > 0: `N분 후`
  - 남은 시간 ≤ 0: `출현 중`
- **버튼**: 일반 보스 예약에만 `✅ 컷 / 😶 멍` 버튼 (View, `timeout=None` 만료 없음)
  - 고정 보스(`is_fixed=1` 또는 `bosses.fixed=1`)는 버튼 미표시
- **TTS**: 정각 알림 시 음성 채널에서 `{content} 미입력 N회 {남은시간}` 자동 읽기
  - 미입력이 없으면 해당 구문을 생략한다.
  - 화면의 `(미입력×N)` 표시는 유지하되, 음성에서는 횟수와 관계없이
    `미입력`을 한 번만 말한다.

수동 TTS는 `v <text>`와 `ㅍ <text>` 명령만 음성 큐에 넣는다. `Z`와 `Z+`는
예약 목록을 텍스트로만 출력하며 TTS 큐에 절대 들어가지 않는다. `z`, `z+`,
`보탐`, `보탐+` 별칭도 동일하게 텍스트 전용이다. 이 규칙은 스케줄러의
정각 알림에서 수행하는 자동 TTS와 별개다.

### W8 local verification boundary

The multi-bot implementation and tests are complete locally. Production
evidence is pending the owner-approved sequential order
`004 → 001 → 002 → 003`; do not record local test output as production proof.

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
1. notified=1 이고 fixed=0 인 예약 전체 조회 (notified DESC, scheduled_at DESC)
2. boss_name 기준으로 가장 최근 notified 행 1개씩 추출
3. scheduled_at + auto_schedule_seconds > now 이면 SKIP (유예시간 내)
4. notified=0 예약이 이미 있으면 SKIP (컷/멍 처리 완료)
5. new_at = scheduled_at + respawn_seconds (미래가 될 때까지 반복 전진, miss_count 누적)
6. miss_count = 이전 miss_count + 1 (전진 횟수만큼 추가 누적)
```

> `miss_count`는 컷/멍 처리 시 0으로 초기화됨.  
> `_get_last_schedule`은 `notified DESC` 정렬로 알림 완료 행을 우선 반환.

---

## 알림 남은 시간 표시 (`fmt_remain`)

```python
total = int(delta.total_seconds())
if total <= 0:  → "출현 중"
elif total < 3600:  → "N분 후"
else:  → "N시간 N분 후"
```

---

## 서버오픈 miss_count 초기값

| 보스 종류 | miss_count | 이유 |
|---|---|---|
| `spawns_on_open=0` (오픈 비연동) | 1 | 오픈시각에 이미 등장 → 기록 못 한 것으로 간주 |
| `spawns_on_open=1` (오픈 연동)  | 0 | 오픈+지연시간이 첫 등장 → 아직 등장 안 함 |

---

## 보탐(예약 목록) 표시 기준

- `notified=0` 행만 표시
- `보탐` / `ㅋ` / `z`: `is_fixed=0` 필터 + 최대 5건, 타이틀에 전체 건수(`is_fixed` 무관) 표시
- `보탐+` / `ㅋ+` / `z+`: 전체 (고정 포함) 건수 제한 없음
- `Z` / `Z+`: 예약 목록 텍스트 출력만 수행하며 TTS를 호출하지 않음

## 초기화 동작

- `전체삭제` / `초기화` / `보스전체삭제`는 기여 랭킹을 먼저 출력한 뒤 `is_fixed=0` 예약 기록을 모두 삭제한다.
- 삭제 범위에는 미발송 예약(`notified=0`)과 알림 완료 이력(`notified=1`)이 모두 포함된다.
- 고정 보스 예약(`is_fixed=1`)은 유지된다.
- 일반 보스의 알림 완료 이력까지 삭제하므로 초기화 직후 자동 미입력 처리로 일반 보스 예약이 다시 생성되지 않는다.
