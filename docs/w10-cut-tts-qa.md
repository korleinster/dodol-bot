# W10 Cut Interaction and TTS Pilot QA

## Implemented contract

- Successful Discord cut/miss button interactions use a silent deferred message
  update and do not send a private success card.
- Duplicate, denied, and failed actions keep one private safe-reason notice.
- Bot 003 selects Edge `ko-KR-SunHiNeural` at `+8%` rate and `+8Hz` pitch.
- Edge synthesis is limited to 20 seconds and falls back to gTTS exactly once.
- Bots 001, 002, and 004 always select gTTS.
- Edge and gTTS write provider-private files before an atomic replace so a
  timeout or cancelled late writer cannot overwrite playable audio.
- Manual `v` and `ㅍ`, web TTS, and scheduled exact-time alerts share the same
  `speak()` path. `z`, `Z`, and their reservation-list variants remain text-only.

## Local evidence

- `python -m unittest discover -s tests -p 'test*.py'`: 75 tests passed.
- `python -m compileall -q main.py src tests`: passed.
- Pinned runtime imports for discord.py 2.7.1, DAVE, PyNaCl, edge-tts, and gTTS:
  passed in the isolated Python 3.11 environment.
- Docker Compose v5.1.4 standalone binary checksum matched the official release
  SHA-256, and `config --quiet` passed with non-secret test credentials.
- A direct network synthesis through the implemented Edge adapter produced a
  non-empty SunHi `+8%`/`+8Hz` MP3.
- `git diff --check`: passed.

The tests cover silent success, duplicate/failure feedback, the exact Edge
client arguments, Edge timeout/error fallback, delayed Edge writes, delayed
gTTS cancellation cleanup, invalid voice fallback, bot isolation, web queue
completion/failure, scheduler recovery, and reservation commands that must not
enter TTS.

## Pending production evidence

- No production container was recreated and no service was restarted.
- Real Discord cut/miss interaction appearance and bot-003 voice-channel
  playback remain pending the separate deployment/restart approval.
- When approved, use the established `004 → 001 → 002 → 003` gate order and
  stop at the first failed bot. Do not change the database, ports, or Tailscale.

