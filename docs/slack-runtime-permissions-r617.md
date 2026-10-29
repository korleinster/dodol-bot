# R6.17 runtime-health mirror permission contract

Status: source implemented; independent prepublication source review and staged Python 3.11 full suite passed; final-image cross-UID and live rollout evidence pending. This document describes the narrow observer-readability repair. It does not claim a completed Slack migration or a recreated bot.

## Why this changed

The Discord bot writes a small, non-secret runtime-health mirror to the bind-mounted `data` directory. The host observer reads `runtime-health-001.json` through `runtime-health-004.json` as the unprivileged `leinster` user. Before R6.17, the container's root process atomically published each mirror as `root:root 0600`; an isolated diagnostic with the prior new image reproduced a UID/GID-1000 `PermissionError`. The original four production bot containers had not been recreated at that point.

## Publication and failure states

`docker-compose.yml` sets `BOT_RUNTIME_HEALTH_GID` from `DODOL_GID`, defaulting to `1000`. Before deployment, verify the resolved numeric GID matches the observer's actual group. The variable must be a canonical positive decimal GID; an absent, malformed, zero, or unsupported value suppresses only the host mirror. The internal Docker health file remains `0600` and keeps its existing probe behavior.

For a valid GID, each mirror update uses a new exclusive temporary inode, sets owner/group to `root:<GID>` and mode `0640`, then atomically replaces only that bot's fixed mirror path. Group members can read; other users cannot read the file. A group-change or replacement failure preserves the previous mirror and removes only the temporary inode created by that attempt. A stale previous mirror may be reported as `unknown` after its freshness window; this is not a successful observation. No credential or guild identifier is included in the mirror payload.

The shared host `data` directory was observed as mode `0777` before rollout and is deliberately not chmodded here. This repair addresses the specific writer/reader permission mismatch, not the parent's broader integrity boundary. One-time permission changes to an old mirror are ineffective because every publication replaces its inode.

## Required verification before bot recreation

1. Run the full Python 3.11 suite and source/Compose checks against the frozen release commit and confirm the four-file hashes recorded in the R6.17 independent QA record.
2. Build the final image without changing dependencies or Dockerfile identity. In an isolated no-network container with disposable storage, invoke the actual publication function. Verify the host mirror is `root:<observer GID> 0640`, the internal health file remains `0600`, UID/GID 1000 can read, and an unrelated unprivileged UID/GID cannot. Repeat publication at least twice and verify the new inode and permissions each time; test group-assignment failure without damaging the previous mirror.
3. Recheck the original containers, protected environment, mounts, bridge, voice, database backup/integrity, observer identity, and resolved Compose GID immediately before rollout. Only then proceed `004 → 001 → 002 → 003`, gating each bot on Docker health, Discord/scheduler/bridge/voice, observer's actual mirror read, non-target preservation, and independent notification receipt. Stop on a failed gate and use only that bot's approved rollback path.

The independent focused tests mock group assignment and run under one UID. The staged full suite passed 136 tests against a read-only source bind as non-root UID 1000, but it likewise cannot substitute for step 2 using the final image and real root-to-observer permission boundary. API acknowledgment cannot substitute for actual receipt. Automatic recovery stays disabled until its separate approved gate.
