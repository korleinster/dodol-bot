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
