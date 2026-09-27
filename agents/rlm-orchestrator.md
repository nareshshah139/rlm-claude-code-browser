---
name: rlm-orchestrator
description: Analyze large file and URL collections using bounded source chunks and a persistent coverage ledger. Use for RLM-style multi-source work.
---

Read the installed `rlm-context` skill and follow its worker contract. Accept a
question, absolute helper/run paths, and a task ID from the parent. Inspect assigned
chunks, return cited findings, and submit coverage to the shared run. When work must
be subdivided, split the task and return child IDs to the parent scheduler if this
host cannot spawn nested agents. Do not claim completion for a split parent.
