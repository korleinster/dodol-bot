# W14 Telegram Command and Component Claim QA

## Scope

W14 removes the duplicate Telegram poller, registers the complete owner command
menu from the active controller, and validates the existing component-action
claim contract before the numbered bot rollout.

## Component claim race contract

Two competing cut or miss requests for the same message may observe either of
these safe loser outcomes:

- `failed` with `ACTION_IN_PROGRESS` while the winning request still owns the
  claim; or
- `already_processed` after the winning request has committed the durable
  result.

Both schedules must execute exactly one business handler, publish exactly one
Discord result, disable the shared cut/miss controls, and persist one successful
claim attempt. The test must not require one particular scheduler interleaving.

## Local verification

- The focused competing-click test passed 50 consecutive runs.
- The complete DiscordBot suite passed all 96 tests.
- Python bytecode compilation and `git diff --check` passed.

## Production gate

The production image must pass the same complete suite and the secret-file
inspection before rollout. Containers are recreated in the approved order
`004 -> 001 -> 002 -> 003`, with health, Discord gateway, scheduler, bridge,
socket, and deployed-commit checks after every target. A failed target stops the
remaining rollout and is rolled back independently.
