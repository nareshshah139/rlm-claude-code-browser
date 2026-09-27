# Cowork, Claude Desktop, Codex Desktop, and Codex cloud

Version 0.9 adds a portable, native-agent workflow. Python stores complete UTF-8
source snapshots, makes bounded work items, and verifies coverage and quoted
evidence. The host model performs analysis and schedules its own subagents when
available and permitted. No API key, Rust extension, nested CLI, or model SDK is
needed for this path. Python 3.10+ and a writable task folder are required.

| Host | Install or load | Execution |
|---|---|---|
| Claude Desktop in Cowork mode | Upload the generated Cowork plugin ZIP through Customize → Plugins | Skill, commands, and supported Claude hooks/agents; use native delegation when exposed |
| Codex Desktop | Install the self-contained skill into a project's `.agents/skills`, or use the generated Codex plugin in a supported plugin installer | Native tools and subagents when available |
| Codex cloud | Commit the self-contained skill plus the instruction below to the task repository | Repository instructions and Python; sequential fallback if no subagent tool |
| Claude Desktop ordinary Chat | Skill instructions may be readable, but execution requires a code/filesystem environment | Use Cowork for this workflow; ordinary Chat is not an equivalent runtime |

## Build the plugins

From a checkout of this repository:

```bash
python3 scripts/build_portable.py --out /absolute/new-output-directory
```

This produces a Cowork ZIP, a Codex ZIP, and reviewable unpacked plugin folders.
Both ZIPs contain the helper and skill. Cowork's package also has Claude commands,
an agent definition, and lightweight hooks. The Codex package has its own manifest
and intentionally excludes Claude hooks. Do not upload the entire development
checkout with virtual environments, test data, and the Rust submodule.

In Cowork, install the Cowork ZIP, select a writable task folder, and invoke:

```text
Use rlm-context to compare these sources: …
```

The plugin's `/rlm-claude-code:web` and `/rlm-claude-code:rlm-orchestrator`
commands also start this workflow where plugin commands are supported. Restart or
start a fresh session after installation if the host has cached its skill list.

## Codex Desktop and cloud in another project

Run the installer from this checkout, selecting the destination explicitly:

```bash
python3 scripts/install_portable.py --target /absolute/project/.agents/skills
```

It copies the helper into the skill and refuses to overwrite an existing skill.
In Codex Desktop, open that project and start a fresh chat. Ask it to use
`rlm-context`. For cloud tasks, commit the copied directory and append this to the
project's existing `AGENTS.md` (do not replace its other instructions):

```text
For large collections of file, URL, or connector pointers, read
.agents/skills/rlm-context/SKILL.md and follow its portable workflow.
Use Python 3.10+ to run .agents/skills/rlm-context/scripts/rlm.py.
Use native subagents when available and permitted, otherwise process work items
sequentially. Always report incomplete source coverage.
```

This repository already includes equivalent instructions and a local skill link.
The installer creates a real, self-contained copy suitable for committing to a
different repository; it does not depend on symlink support in the destination.
Cloud tasks do not inherit desktop plugin installations or desktop browser login.
The offline helper needs no setup-time secrets or internet access. Enable any
optional network access through the host's own environment controls.

## How large pointer contexts work

1. Resolve every pointer with host file, document, connector, or browser tools.
   Convert PDF/Office content to text with page/sheet references. Preserve source
   URIs. Record denied or unavailable sources instead of dropping them.
2. Snapshot text to disk, hash each chunk, and plan tasks with explicit UTF-8 byte
   limits (64,000 bytes by default). These are not tokenizer estimates. Sources
   freeze when planning; changed sources need a new run.
3. Give workers task IDs and chunk paths. Workers can split complex tasks into
   smaller children. The parent schedules new children after every wave. The
   Python helper does not launch agents itself.
4. Workers submit bounded summaries, reviewed chunk IDs, and exact quotations.
   The helper rejects foreign chunks, incomplete assignments, invented quotes,
   and altered snapshots. Failed tasks can be retried.
5. Synthesize results, returning to original chunks for cross-source joins and
   conflicts. If result files exceed the host context budget, create another
   bounded synthesis run. Keep original provenance and the original coverage gate.

`status --require-complete` exits with code 3 until all source spans have completed
leaf tasks and no unavailable/truncated source remains. This verifies bookkeeping
and provenance, not reasoning quality or whether a worker truly read every word.
Full review is a worker contract, not a property Python can prove.

The helper prevents silent source-tail loss. Disk space, task count, model budget,
host permissions, and acquisition quality still constrain a run. Large byte
budgets should be reduced when the model needs more room for instructions/output.

## Optional public-web acquisition

The helper's `web` subcommand uses httpx for HTTP and optionally Playwright for
headless rendering. Install these only in an environment where you are allowed:

```bash
python3 -m venv .venv-browser
.venv-browser/bin/python -m pip install httpx playwright
.venv-browser/bin/python -m playwright install chromium
```

On Windows use `.venv-browser\Scripts\python.exe`. Run the skill's helper with
that interpreter. The portable web CLI batches URL lists beyond the per-call
page cap and preserves full extracted text by default. An explicit positive
`RLM_BROWSER_MAX_CHARS` sets a cap; capped sources are reported as incomplete.
Fetch still holds individual responses in memory; this is not a streaming crawler.

For authenticated pages, prefer the host's signed-in browser or connector and
ingest its captured text with the original URI. Follow the user's browser
preference. A localhost CDP address in a cloud container/VM does not identify the
desktop browser. Resolve browser queues and truncated captures before planning.

## Verify an installation

Run `python3 /absolute/skill/scripts/rlm.py doctor`, then ask the host to analyze
several sources containing a distinctive fact at the end of the last file.
Require citations, coverage counts, and an explicit report of whether subagents
or sequential processing were used. Test a missing pointer too: it must remain
visible and coverage must remain incomplete.

Repository tests cover the helper and package contracts locally. They do not
certify the current UI, account entitlements, or model behavior inside live
Cowork, Codex Desktop, or Codex cloud. Explicit invocation is more dependable than
hoping a host automatically chooses a skill. Hook hints cannot force delegation.

## Legacy API backend

`src` remains a separate optional backend with its own Python dependencies,
compiled `rlm_core`, API credentials, model routing, and REPL. `pip install .` or
`uv sync` installs that heavier development stack; neither is required to use the
portable skill. Desktop subscriptions are not API credentials.

## Platform references

- [Claude plugins](https://support.claude.com/en/articles/13837440-use-plugins-in-claude)
- [Cowork architecture](https://support.claude.com/en/articles/14479288-claude-cowork-architecture-overview)
- [Claude hook contracts](https://code.claude.com/docs/en/hooks)
- [Codex skills](https://developers.openai.com/codex/skills/)
- [Codex plugin packaging](https://developers.openai.com/plugins/build/plugins)
- [Cloud environments](https://learn.chatgpt.com/docs/environments/cloud-environment)

## CI template

`docs/ci/portable.yml` is a GitHub Actions matrix for the portable tests and package
build on Linux, macOS and Windows with Python 3.10 and 3.13. To enable it, copy it
to `.github/workflows/portable.yml` using credentials allowed to write workflows.
It is supplied as a template because the publishing token used for this change
cannot add Actions workflows. The matrix has not been run on GitHub yet.
