---
description: Analyze web and document sources with portable RLM context batches and native subagents
argument-hint: <task with URLs, files, or connector pointers>
---

Read `${CLAUDE_PLUGIN_ROOT}/skills/rlm-context/SKILL.md` and follow its workflow.
If the host does not expand that variable, locate the installed `rlm-context`
skill through the host's skill discovery rather than assuming a home directory.

Task: $ARGUMENTS

Resolve all supplied pointers, fetch/capture accessible sources, and record
unavailable sources. Use the host's native subagents when available and permitted,
or the skill's sequential fallback. Use bounded tasks even when the pointer list
is short. Keep the original coverage gate through final synthesis.
