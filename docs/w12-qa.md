# W12 Revision 1 QA

## Automated verification

- Full unit suite: 88 tests passed.
- Python compile: `main.py`, `src`, and `tests` passed.
- Docker Compose validation: not available on the MacBook because Docker is not
  installed; it remains a pre-rollout Mac Mini gate.

Coverage includes:

- silence for `설정` outside the assigned text channel;
- server-management authorization for summon and leave mutations;
- transactional relocation of bosses, schedules, and contributions;
- conflict rollback without overwriting either guild;
- leave commands preserving all gameplay data;
- inactive bindings omitted from bridge target discovery;
- one alert per continuous scheduler incident and one recovery alert after 30
  successful ticks; and
- escaped plain-text web command rendering without a code fence or public
  origin badge.

## Production state

The pre-implementation read-only check found one active configuration for each
bot. Bot 004 had no scheduler error rows, every one of its 926 schedules was in
the ready state, and no recent structured scheduler-failure broadcast was
stored. W12 has not been deployed and no container has been recreated.
