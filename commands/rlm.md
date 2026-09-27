---
description: Use the portable RLM context workflow or inspect an existing run
argument-hint: "<task or status and run path>"
---

Read `${CLAUDE_PLUGIN_ROOT}/skills/rlm-context/SKILL.md` and follow it for the
user's task: $ARGUMENTS.

For a status request, run the bundled helper's `status` command against the run
path the user names, or the current task's known run. Do not invent a run or
claim host configuration changed. For a processing request, use native model
execution and the documented coverage contract. Respect an explicit request to
stop using RLM. Legacy API model/configuration controls apply only when the user
explicitly selects that backend; desktop subscriptions do not provide API keys.
