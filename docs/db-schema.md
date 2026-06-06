# DB 스키마 명세

SQLite 단일 파일 (`bot.db`). 테이블 3개.

---

## guild_config

봇이 배치된 서버(길드)의 채널 설정.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `guild_id` | INTEGER | Discord 서버 ID |
| `bot_number` | INTEGER | 봇 번호 (001, 002, ...) |
| `text_channel_id` | INTEGER | 명령/알림 텍스트 채널 ID |
| `voice_channel_id` | INTEGER | TTS 음성 채널 ID (없으면 NULL) |

- PK: `(guild_id, bot_number)`
- `소환 도돌봇001` 명령 시 UPSERT

---

## bosses

등록된 보스 목록. 기본 보스(is_default=1)는 서버 최초 소환 시 자동 삽입.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | INTEGER | PK (자동 증가) |
| `guild_id` | INTEGER | 소속 서버 ID |
| `bot_number` | INTEGER | 소속 봇 번호 |
| `name` | TEXT | 보스 이름 |
| `aliases` | TEXT | 별칭 목록 (JSON 배열, 예: `["돌크","크루마"]`) |
| `respawn_seconds` | INTEGER | 리스폰 주기(초). 고정 보스는 NULL |
| `fixed` | INTEGER | 고정 일정 보스 여부 (0/1) |
| `fixed_days` | TEXT | 요일 (콤마 구분, 월=0~일=6). 고정 보스만 사용 |
| `fixed_time` | TEXT | 시각 (콤마 구분 `HH:MM`). 고정 보스만 사용 |
| `spawns_on_open` | INTEGER | 서버오픈 연동 여부 (0/1) |
| `open_delay_seconds` | INTEGER | 서버오픈 후 첫 등장까지 지연(초) |
| `auto_schedule_seconds` | INTEGER | 미입력 자동 예약 유예시간(초). 기본 600(10분) |
| `open_time_seconds` | INTEGER | 미사용 (예약 컬럼) |
| `is_default` | INTEGER | 기본 보스 여부 (소스 수정으로만 변경 가능) |

- UNIQUE: `(guild_id, bot_number, name)`

### fixed_days / fixed_time 예시

| 보스 | fixed_days | fixed_time |
|---|---|---|
| 타이런트 | `"2"` | `"22:30"` |
| 셀리호든 | `"4"` | `"19:00"` |
| 월드 보스 | `"0,1,2,3,4,5,6"` | `"12:00,20:00"` |
| 오만/신념의 탑 보스 | `"0,1,2,3,4,5,6"` | `"19:00"` |

---

## schedules

보스 출현 예약 목록.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | INTEGER | PK (자동 증가) |
| `guild_id` | INTEGER | 소속 서버 ID |
| `bot_number` | INTEGER | 소속 봇 번호 |
| `boss_name` | TEXT | 연결된 보스 이름. 임의 예약은 NULL |
| `content` | TEXT | 알림 표시 텍스트 |
| `scheduled_at` | TEXT | 예약 시각 (ISO 8601, KST) |
| `is_fixed` | INTEGER | 고정 보스 예약 여부 (0/1) |
| `miss_count` | INTEGER | 누적 미입력 횟수 |
| `warned_5min` | INTEGER | 5분 전 알림 발송 완료 (0/1) |
| `warned_1min` | INTEGER | 1분 전 알림 발송 완료 (0/1) |
| `notified` | INTEGER | 정각 알림 발송 완료 (0/1). 1이면 처리 종료 |
| `created_at` | TEXT | 생성 시각 |

- UNIQUE 제약 없음 → 중복 방지는 코드 레벨에서 처리
- `notified=1`인 행은 자동 미입력 처리 대상이 됨
- `notified=1`이고 `scheduled_at`이 30일 이상 지난 행은 매일 자동 삭제 (`cleanup_old_schedules` 태스크)

### 예약 상태 흐름

```
INSERT (notified=0, warned_5min=0, warned_1min=0)
  → warned_5min=1  (scheduled_at까지 90~330초 남았을 때)
  → warned_1min=1  (60~90초 남았을 때)
  → notified=1     (60초 이내 또는 경과 후)
```

---

## contributions

컷 처리자 기여 기록. `초기화` 명령 시 schedules와 함께 삭제됨.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | INTEGER | PK (자동 증가) |
| `guild_id` | INTEGER | 소속 서버 ID |
| `bot_number` | INTEGER | 소속 봇 번호 |
| `user_id` | INTEGER | Discord 유저 ID |
| `username` | TEXT | 처리 시점의 표시 이름 (닉네임 변경 대비) |
| `boss_name` | TEXT | 컷 처리한 보스 이름 |
| `cut_at` | TEXT | 컷 처리 시각 (ISO 8601, KST) |

- 텍스트 명령(`체르 컷`) 및 버튼(`✅ 컷`) 모두 기록됨
- 시즌/기간 구분 없음 — 초기화 전까지 누적

---

## 테이블 간 관계

```
guild_config (guild_id, bot_number)
    ↑ JOIN
bosses (guild_id, bot_number, name)
    ↑ boss_name 참조 (외래키 제약 없음)
schedules (guild_id, bot_number, boss_name)
contributions (guild_id, bot_number)
```

- 외래키 제약은 설정되지 않음 — 보스 삭제 시 관련 예약은 자동 삭제되지 않음

---

## 마이그레이션

`init_db()` 호출 시 실행. `ALTER TABLE ... ADD COLUMN`을 try/except로 감싸 이미 존재하는 컬럼은 무시.

적용 대상:
- `bosses.is_default`
- `bosses.fixed_days`
- `bosses.fixed_time`
- `schedules.warned_5min`
- `schedules.warned_1min`
