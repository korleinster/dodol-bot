# Security maintenance — 2026-10-29

The security rebuild retains the deployed bot features and voice protocol while
updating aiohttp to 3.14.5, PyNaCl to 1.6.2, python-dotenv to 1.2.4, and the certifi
trust store to 2026.10.23. The runtime environment excludes pip and setuptools
after dependencies have been installed in the builder stage.

No bot commands, configuration fields, database schemas, or bridge permissions
change. Build and test the image without production credentials or a production
data mount. Run the complete unit suite, runtime imports and voice compatibility
checks, Python compilation, Compose validation, shell syntax checks, and a
credential scan before rollout.

Preserve a verified SQLite online backup and each container's image and runtime
configuration. Deploy 004 → 001 → 002 → 003, verifying the intended commit,
Discord connection, health, scheduler, authenticated bridge, and voice state at
each gate. Restore the prior image and configuration if a gate fails; do not
continue to the next bot after a failure.

The released discord.py 2.7.1 voice extra still caps PyNaCl below 1.6. The
project therefore declares discord.py and its voice dependencies explicitly,
including patched PyNaCl 1.6.2 and davey 0.1.6. This uses the maintainer's
confirmed compatibility guidance:
https://github.com/Rapptz/discord.py/discussions/10425
The offline suite exercises Discord's actual RTP AEAD encryptor, authenticated
decryption, and DAVE import without connecting to or sending audio to Discord.

The application's pinned voice capability check is aligned with PyNaCl 1.6.2.
Image construction now fails unless the real installed versions, voice imports
and ffmpeg pass that gate; a regression test checks actual package metadata.
During the first live gate, bot 003 rejected the old application pin and was
restored before retry. The corrected release is revalidated and rolled through
all four bots in the required order.

## Production acceptance (verified 2026-10-30 KST)

All four Mac Mini bots now run application commit
`1616747891d8d57bb061739cf3f069b8ec05b5fa` in image
`sha256:1c2e42741ef32e6f3959a87265e03ba3509717f07c9b722fef9f2b3899b3f0c0`.
The corrected release passed 138 offline Linux tests and remote CI. It was
rolled out in order 004, 001, 002, 003 with preserved per-service Compose
overrides and verified SQLite backup. All four containers have restart count
zero, Docker health healthy, Discord ready, scheduler ready, and authenticated
bridge health/target checks passed. Bot 003's configured voice is connected;
the other bots correctly report voice unconfigured. Live SQLite quick_check
is ok. No test Discord message, audio playback, or DB reset was sent.

Private backup and per-bot image overrides are retained under
`/var/backups/codex-security-20261029/20261029T033243Z/bot-rollout-v2`.
The earlier failed voice gate and rollback remain recorded separately.
Documentation commits after this runtime commit do not require image rebuilds.
