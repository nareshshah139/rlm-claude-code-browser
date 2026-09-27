# Working in this repository

For large collections of document, file, URL, or connector pointers, read
`skills/rlm-context/SKILL.md` and use its portable helper. This also works in
Codex cloud when no plugin loader is available. The helper stores source chunks
and tracks coverage; the host supplies model reasoning and any available native
subagents. If subagents are unavailable, use the documented sequential fallback.

Portable verification (Python 3.10+, no dependencies):

```bash
python3 -m unittest discover -s tests/portable -v
python3 -m rlm_portable doctor
```

Optional browser tests need httpx/Playwright. Legacy `src` model/REPL tests need
its separately documented Python dependencies and compiled rlm_core. Never
claim a local test validates live Cowork or Codex cloud behavior.
