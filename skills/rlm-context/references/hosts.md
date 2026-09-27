# Host adapters

The same source/task/result contract works in Claude Code, Claude Desktop Cowork,
Codex Desktop/CLI, and Codex cloud. Native agent tools and permission rules belong
to the host. Capability-detect them in the current session; no adapter guarantees
that native subagents, network access, or a signed-in browser are available.

- **Claude Desktop / Cowork:** install the generated Claude plugin ZIP in Customize
  > Plugins. Invoke `/rlm-claude-code:web` or the `rlm-context` skill. Run helpers in
  Cowork's code environment using its Python, not a copied macOS `.venv`. Use the
  connected desktop browser for login-only sources, then ingest its captured text.
  Ordinary Claude Desktop chat without code execution cannot run this workflow.
- **Codex Desktop/CLI:** install the generated self-contained skill under a supported
  skills directory, or use the Codex plugin archive via the app's supported plugin
  installation flow. Invoke `$rlm-context`. No Claude hooks or `Task` tool are assumed.
- **Codex cloud:** use the repository instructions in AGENTS.md and the helper at
  `scripts/rlm.py` relative to the installed SKILL.md. Repository `.agents/skills` also exposes the
  skill where supported. Python's standard library suffices for local snapshots,
  planning and coverage. Public web access needs agent-phase network permission;
  install optional packages during environment setup. Setup-only secrets do not
  become model/API credentials. With no network, use staged/captured source files.
- **Sequential fallback:** when a host cannot spawn agents, process one bounded task
  at a time. Keep the same persistent ledger and use recursive splitting as needed.

Optional browser setup, in a writable environment-specific virtual environment:

```bash
python3 -m venv /writable/path/rlm-web-env
/writable/path/rlm-web-env/bin/python -m pip install 'httpx>=0.27,<1' 'playwright>=1.45,<2'
/writable/path/rlm-web-env/bin/python -m playwright install chromium
```

On Windows use the environment's `Scripts/python.exe`; no Bash is required by the
skill launcher. Browser dependencies are optional; do not install the legacy Rust
or ML stack for this workflow. Never auto-install packages from a prompt hook.
Use the selected Python executable for the helper too.

`RLM_BROWSER_CDP_URL` is explicitly configured only for a debugging endpoint
reachable from the execution environment. It does not tunnel to the user's Chrome.
`RLM_BROWSER_MAX_CHARS=0` preserves the extracted page text without clipping; a
finite cap sets truncation metadata and prevents full-coverage claims. HTTP/body
extraction still materializes a page in memory, so use bounded crawling or staged
files for exceptionally large pages. Non-text sources require host conversion.
