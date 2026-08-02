# W10 Cut Interaction and TTS Pilot QA

## Implemented contract

- Successful Discord cut/miss button interactions use a silent deferred message
  update and do not send a private success card.
- Duplicate, denied, and failed actions keep one private safe-reason notice.
- Bots 001–004 select Edge `ko-KR-SunHiNeural` at `+8%` rate and `+8Hz` pitch.
- Edge synthesis is limited to 20 seconds and falls back to gTTS exactly once.
- Each bot can independently opt out to gTTS without changing a sibling.
- Edge and gTTS write provider-private files before an atomic replace so a
  timeout or cancelled late writer cannot overwrite playable audio.
- Manual `v` and `ㅍ`, web TTS, and scheduled exact-time alerts share the same
  `speak()` path. `z`, `Z`, and their reservation-list variants remain text-only.

## Local evidence

- `python -m unittest discover -s tests -p 'test*.py'`: 76 tests passed.
- `python -m compileall -q main.py src tests`: passed.
- Pinned runtime imports for discord.py 2.7.1, DAVE, PyNaCl, edge-tts, and gTTS:
  passed in the isolated Python 3.11 environment.
- Docker Compose v5.1.4 standalone binary checksum matched the official release
  SHA-256, and `config --quiet` passed with non-secret test credentials. The
  rendered configuration resolved every bot independently to Edge SunHi at
  `+8%`/`+8Hz`.
- A direct network synthesis through the implemented Edge adapter produced a
  non-empty SunHi `+8%`/`+8Hz` MP3.
- `git diff --check`: passed.

The tests cover silent success, duplicate/failure feedback, the exact Edge
client arguments, Edge timeout/error fallback, delayed Edge writes, delayed
gTTS cancellation cleanup, invalid voice fallback, bot isolation, web queue
completion/failure, scheduler recovery, and reservation commands that must not
enter TTS.

## Revision 3 implementation decision

The owner approved expanding the selected Edge voice to bots 001–004 and
approved direct implementation, deployment, and the required service
recreations. Each service maps numbered provider and prosody variables into the
same unnumbered process contract, preserving independent rollback. Unknown bot
numbers and unsupported provider or voice values fail safely to gTTS or the
verified SunHi default.

At approval time, production DB inspection showed configured voice channels for
bots 001 and 003 only. Bots 002 and 004 receive the Edge provider but keep TTS
capability unavailable until an administrator assigns their voice channels;
this revision does not change Discord channel settings.

## Revision 2 production rollout evidence

The owner separately approved an immediate bot-003-only rollout on 2026-08-02.

- Production source fast-forwarded from `a93f607` to implementation commit
  `5c67dd7`; the existing untracked `logs/` directory was preserved.
- The previous shared image ID was retained as
  `dodol-bot:rollback-a93f607` before the new image build.
- The shared image built successfully with `edge-tts==7.2.8`, and only
  `dodol-bot-003` was force-recreated. Bots 001, 002, and 004 remained on the
  previous image and stayed up without recreation.
- Bot 003 reported commit `5c67dd7`, Discord online, scheduler recovery
  complete, voice channel `일반` connected, and its web bridge socket ready.
- The numbered socket existed at mode `660` with the expected shared group.
- Runtime settings resolved to Edge `ko-KR-SunHiNeural`, `+8%`, and `+8Hz`.
- A direct synthesis inside the production container produced a non-empty
  20,304-byte MP3 and removed it immediately without voice-channel playback.
- The public LeinyGames HTTPS health endpoint returned `status: ok`; no new
  startup, scheduler, bridge, or TTS error appeared in the post-rollout logs.

The owner then approved the remaining bots on the same date. Because bot 003
was already healthy, the remaining rollout used `004 → 001 → 002` without
recreating bot 003 again.

- Bots 004, 001, and 002 were each force-recreated separately with `--no-deps`.
- Every bot reported Discord online, bridge socket ready, scheduler recovery
  complete, and implementation commit `5c67dd7` before the next gate started.
- All four containers remained up on the new `dodol-bot:latest` image with no
  restart loop or startup error.
- All four numbered sockets existed independently at mode `660` with the
  expected shared group.
- Runtime provider checks confirmed bot 003 uses the selected Edge voice while
  bots 001, 002, and 004 remain on gTTS. Bot 001 reconnected to its configured
  voice channel successfully.
- Post-rollout production logs recorded real bot-003 Edge synthesis and
  playback. No temporary MP3 remained afterward.

The visual Discord cut/miss interaction remains a user-facing smoke check. The
database, external ports, and Tailscale were not changed.
