# W15 Stale Schedule Recovery QA

## Scope

W15 adds serialized stale-schedule reconciliation for DiscordBot startup,
runtime scheduler ticks, and the stored portion of a cross-server summon. It
does not authorize deployment, container recreation, Discord configuration
changes, or production database changes.

## Contract checked locally

- The cutoff is exact: at reconciliation time `r`, only
  `scheduled_at < r - 15 seconds` is stale. A row exactly 15 seconds late can
  deliver once through the existing conditional claim.
- Stale normal and fixed rows are quiet: no channel lookup, Discord send, or
  TTS. Normal rows can advance from acknowledged history; fixed rows can create
  one next configured occurrence. Arbitrary (`boss_name IS NULL`) rows are
  acknowledged but never regenerated.
- Runtime stale detection invokes the same serialized reconciliation before
  selecting delivery rows. This prevents a reconnect from replaying an old
  backlog; repeated ticks and concurrent reconciliation leave one normal/fixed
  future pending row rather than duplicate sends.
- Explicit Discord 5xx retries remain durable and bounded. A retry cannot be
  scheduled later than `scheduled_at + 15 seconds`; window expiry records a
  safe error code instead of replaying a late alert. Ambiguous failures remain
  non-retryable.
- A summon moves stored gameplay data, default synchronization, and target
  schedule reconciliation in one SQLite transaction. A failure rolls the
  stored move back. Voice operations happen after commit and report degraded
  completion for disconnect failure, connection failure, or no voice client.

## Local evidence

Focused dependency-provisioned local virtualenv run:

```text
python -m unittest tests.test_scheduler_recovery tests.test_setup_lifecycle
Ran 26 tests
OK
```

The focused suite covers the existing quiet/idempotent recovery contract,
strict 16-second stale handling, exact 15-second delivery, concurrent recovery
duplicate prevention, 5xx retry/window expiry, summon data preservation,
recovery-failure rollback, and degraded voice completion. `git diff --check`
also passed for the implementation and test diff at review time.

The final local verification also passed:

```text
python -m unittest discover -s tests
Ran 103 tests
OK

python -m py_compile src/schedule_recovery.py src/cogs/boss.py src/cogs/setup.py
OK
```

The W15 source and documentation diff had no hardcoded-secret finding in the
targeted local pattern scan. Docker Compose was unavailable in this local
environment, so image build, container health, and live Discord/voice checks
remain unverified here.

## Production gate

Production rollout is pending separate owner approval. Before any deployment
or service recreation, take the approved backup, use the approved rollout
order, and verify the actual Discord scheduler and voice health after each
changed bot. Local tests are not production evidence.
