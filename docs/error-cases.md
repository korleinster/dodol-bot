# 에러 / 예외 케이스 명세

각 상황에서 봇이 어떻게 동작하는지 정리.

---

## 채널 미배치 상태

`소환 도돌봇001`을 하지 않은 상태.

| 상황 | 동작 |
|---|---|
| 어떤 채널에서 명령 입력 | `setup.py`는 모든 채널 허용 → `소환`, `설정` 명령은 응답 |
| `설정` 입력 | "아직 배치되지 않았습니다. `소환 도돌봇001`을 입력하세요." |
| 보스/예약 명령 입력 | 배치 채널 확인 로직에서 `assigned=None` → **침묵 (응답 없음)** |
| `소환` 입력 | "아직 소환된 봇이 없습니다." |

> 소환 전에는 채널 필터가 None이라 boss/tts/market 등 모든 명령에 응답하지 않음. `소환`·`설정` 명령만 동작.

---

## 음성 채널 미설정

소환 시 사용자가 음성방에 없었거나, 음성 채널이 삭제된 경우.

| 상황 | 동작 |
|---|---|
| `소환` 시 음성방 미입장 | `voice_channel_id=NULL`로 저장, "음성 채널: 미설정" 표시 |
| TTS 명령 입력 | `get_voice_channel` → NULL → `speak()` 즉시 return (무응답) |
| 정각 알림 TTS | 동일하게 무응답 (TTS 없이 텍스트 알림만 발송) |
| 음성 채널이 삭제된 경우 | `guild.get_channel(vc_id)` → None → `speak()` return (무응답) |

---

## TTS 연결 실패

| 상황 | 동작 |
|---|---|
| 음성 채널 연결 실패 (권한 부족 등) | `except Exception` → 로그 출력 후 return (무응답) |
| 이미 다른 채널에 연결된 경우 | `voice_client.move_to(vc_channel)` 로 자동 이동 |
| TTS 재생 중 새 TTS 요청 | `voice_client.stop()` 후 새 오디오 재생 (이전 TTS 중단) |
| TTS 재생 완료 후 | 음성 채널 상시 유지 (`voice_keepalive` 60초 루프가 연결 관리) |

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
| `bot.get_channel(text_channel_id)` → None | `continue` (해당 예약 알림 건너뜀, notified 변경 없음) |

> 채널이 복구되면 다음 루프에서 다시 시도함.  
> 단, 5분·1분 경고는 구간이 지나면 영구 누락.

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
