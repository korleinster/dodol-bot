# 에러 / 예외 케이스 명세

각 상황에서 봇이 어떻게 동작하는지 정리.

---

## 채널 미배치 상태

`소환 도돌봇001`을 하지 않은 상태.

| 상황 | 동작 |
|---|---|
| 어떤 채널에서 일반 명령 입력 | 배치 채널이 없으므로 침묵 |
| `설정` 입력 | 배치 채널이 없거나 다른 채널이면 침묵 |
| 보스/예약 명령 입력 | 배치 채널 확인 로직에서 `assigned=None` → **침묵 (응답 없음)** |
| `소환` 입력 | "아직 소환된 봇이 없습니다." |

> 소환 전에는 `소환`과 서버 관리자의 대상 지정 소환만 동작한다.

## 서버 이동과 나가기

| 상황 | 동작 |
|---|---|
| 다른 서버에서 `소환 뚠뚠봇NNN` | 기존 음성 연결과 이전 채널 바인딩을 해제하고 보스·예약·기여 데이터를 새 서버로 이동 |
| 이전·대상 서버에 모두 데이터 존재 | 자동 병합·삭제 없이 중단하고 안전한 충돌 안내 |
| 이동 중 기본 보스 동기화 또는 stale 일정 복구 실패 | `BEGIN IMMEDIATE` 트랜잭션 전체 rollback. 원본 길드 데이터·바인딩과 대상 길드의 기존 상태를 보존하고 대상 활성화 없음 |
| commit 뒤 이전 음성 해제 또는 대상 음성 연결 실패/미연결 | 게임 데이터 rollback 없음. `음성 확인 필요` 상태로 안내하고 재소환 전 상태 확인 필요 |
| 관리자 권한 없는 소환·나가기 | 데이터 변경 전 거부 |
| `음성나가기` | 음성 연결과 `voice_channel_id`만 해제 |
| `채팅나가기` | 확인 메시지 전송 후 `text_channel_id`만 해제 |
| `전체나가기` | 두 채널 설정을 해제하되 게임 데이터는 보존 |

---

## 음성 채널 미설정

소환 시 사용자가 음성방에 없었거나, 음성 채널이 삭제된 경우.

| 상황 | 동작 |
|---|---|
| `소환` 시 음성방 미입장 | `voice_channel_id=NULL`로 저장, "음성 채널: 미설정" 표시 |
| TTS 명령 입력 | `get_voice_channel` → NULL → `speak()` 즉시 return (무응답) |
| 정각 알림 TTS | 동일하게 무응답 (TTS 없이 텍스트 알림만 발송) |
| 음성 채널이 삭제된 경우 | `guild.get_channel(vc_id)` → None → `speak()` return (무응답) |

For W8 web targets, the same condition is explicit: `tts` is reported as
false and a web TTS request fails closed before creating a job. Commands,
components, and games remain available when the text channel is configured.

---

## TTS 연결 실패

| 상황 | 동작 |
|---|---|
| 음성 채널 연결 실패 (권한 부족 등) | `except Exception` → 로그 출력 후 return (무응답) |
| 이미 다른 채널에 연결된 경우 | `voice_client.move_to(vc_channel)` 로 자동 이동 |
| TTS 재생 중 새 TTS 요청 | `voice_client.stop()` 후 새 오디오 재생 (이전 TTS 중단) |
| TTS 재생 완료 후 | 음성 채널 상시 유지 (`voice_keepalive` 60초 루프가 연결 관리) |

001~004번은 각자 Edge Neural 음성 합성을 먼저 시도한다. Edge 의존성이 없거나,
네트워크·서비스 오류가 발생하거나, 합성이 20초를 넘으면 해당 봇만 기존 gTTS를
정확히 한 번 시도한다. 두 합성이 모두 실패하면 웹 요청은 실패로 종료되고
Discord의 일반 요청과 자동 알림은 텍스트 동작을 방해하지 않는다. 모든
실패·취소 경로는 임시 MP3를 정리한다. 봇별 provider 환경값은 서로 공유하지 않는다.

---

## 컷·멍 버튼 응답

| 상황 | 동작 |
|---|---|
| 정상 처리 | 원본 버튼과 공개 결과만 갱신하고 개인 성공 메시지는 표시하지 않음 |
| 이미 처리됨 | 본인에게 이미 처리됐다는 비공개 안내 1회 |
| 권한 거부 또는 처리 실패 | 본인에게 안전한 실패 사유를 비공개로 1회 표시 |

Discord 상호작용 제한시간을 지키기 위해 정상 처리도 내부적으로는 조용한
deferred message update로 승인한다. 공용 dispatcher와 영속 claim은 우회하지 않는다.

---

## 보스 검색 실패

`_find_boss()` 결과가 None인 경우.

| 상황 | 동작 |
|---|---|
| 존재하지 않는 보스명 컷/멍/젠 | "❌ **{query}** 에 해당하는 보스를 찾을 수 없습니다." |
| 고정 보스 컷/멍/젠 시도 | "❌ **{name}** 은 고정 타임 보스입니다. 컷/멍/젠 처리를 할 수 없습니다." |
| 리스폰 미설정 보스 컷/멍/젠 | "❌ **{name}** 은 리스폰이 설정되지 않았습니다." |

---

---

## API 실패

### PLAYNC 시세 API

| 상황 | 동작 |
|---|---|
| API 키 미설정 | Authorization 헤더 없이 요청 → 401 → 빈 결과 |
| 검색 결과 없음 (200 OK, 빈 배열) | "❌ **{keyword}** 검색 결과가 없습니다." |
| HTTP 오류 (비 200) | 빈 리스트 반환 → "검색 결과가 없습니다." |
| 네트워크 오류 / 타임아웃 | `except Exception: return []` → "검색 결과가 없습니다." |

### Open-Meteo 날씨 API

| 상황 | 동작 |
|---|---|
| HTTP 오류 (비 200) | "❌ 날씨 정보를 가져올 수 없습니다." |
| 네트워크 오류 / 타임아웃 (10초) | "❌ 날씨 서버에 연결할 수 없습니다." |

---

## check_schedules 루프 중 채널 없음

알림 발송 시점에 채널이 삭제되거나 봇이 채널에 접근 불가한 경우.

| 상황 | 동작 |
|---|---|
| `bot.get_channel(text_channel_id)` → None, 정각 유예 창 안 | 해당 tick은 전송하지 않고 pending 유지 |
| 채널 미접근 상태로 정각 15초 초과 | 다음 tick의 recovery가 채널 조회 전에 무음 완료하고 안전한 미래 일정만 복구 |

> 5분·1분 경고는 각 구간 안에서만 전송한다. 정각 알림은 최대 15초까지만
> 복구를 기다리며, 그 이후에는 재입장하더라도 backlog를 replay하지 않는다.

## 늦은 일정 / 재접속 / Discord 5xx

| 상황 | 동작 |
|---|---|
| `scheduled_at < now - 15초`인 미처리 일정 | 채널 조회 전에 직렬화 recovery. Discord/TTS 없이 완료 처리하므로 오래된 backlog는 재접속 때 burst하지 않음 |
| 정확히 `now - 15초`인 일정 | 유예 창에 포함되어 조건부 claim 뒤 최대 한 번 정각 알림 |
| 창 밖 일반·고정 보스 | 무음 완료 뒤 각각 안전한 미래 일반/고정 예약 한 건 생성 가능 |
| 창 밖 임의 예약 | 무음 완료만 하고 재생성하지 않음 |
| 명시적 Discord 5xx | DB retry 상태로 재시도하되, 다음 retry가 `scheduled_at + 15초`를 넘으면 `DISCORD_SERVER_ERROR_WINDOW_EXPIRED`로 종료 |
| timeout/전송 결과 불명 오류 | Discord가 이미 수락했을 가능성이 있어 재시도하지 않음 |

---

## 명령 처리 중 예외

`boss.py`의 `on_message`에서 `_dispatch` 호출 시 예외 발생.

```python
except Exception as e:
    traceback.print_exc()
    await message.channel.send(f"⚠️ 오류: {e}")
```

> 예외 메시지를 채널에 노출하고 스택 트레이스를 서버 로그에 출력.

---

## 임의 예약 시각 처리

`HH:MM 내용` 형식으로 임의 예약 시 이미 지난 시각이면 다음 날로 처리.

```python
at = now().replace(hour=h, minute=m, ...)
if at < now():
    at += timedelta(days=1)
```

## 컷 명령 과거 시각 처리

`체르 컷 0530` 처럼 시각을 지정할 때 현재보다 1분 이상 미래이면 어제로 처리.

```python
if base_time > now() + timedelta(minutes=1):
    base_time -= timedelta(days=1)
```
