# Changelog

## 0.9.0 — Portable native-agent workflow

- Add a self-contained skill, dependency-free context CLI, Cowork/Codex plugin
  builders, and a project skill installer with an explicit cloud AGENTS.md path.
- Snapshot complete UTF-8 sources, group by byte budget, recursively split work,
  and validate submitted chunk coverage, hashes and quoted evidence in SQLite.
- Keep acquisition failures and truncated pages visible; preserve browser text
  by default in the CLI and batch URL lists beyond the per-call page limit.
- Replace startup installation hooks with lightweight, correctly formatted
  advisory context; use native delegation with a truthful sequential fallback.
- Repair the optional API backend's child execute/observe loop, source clipping,
  dataclass prompts, shared client/trajectory wiring, global call budgets,
  asynchronous SDK calls, and the shadowed orchestrator CLI entry point.
- Add portable installation, workflow, transport and REPL regression tests.


## [0.8.0] - 2026-09-24

### Added
- Browser-use lane (`src/web_tools.py`): parallel HTTP fetch, parallel headless Chromium
  fallback for JavaScript-only / bot-blocked pages, and CDP attach to the user's logged-in
  Chrome for pages behind a login. Remaining pages go to a `chrome_queue` for the agent's own
  browser tools and come back through `ingest_page`.
- REPL functions `fetch_pages`, `crawl`, `browse`, `ingest_page`, `queue_for_chrome`,
  `web_status`, and the `web` / `web_meta` context dicts (blocked in micro mode).
- `scripts/rlm-web` CLI (`src/web_cli.py`) and the `/rlm-claude-code:web` command.
- `--files-dir` for `python -m src.orchestrator` to run the REPL orchestrator over a web run.
- URL policy: http(s) only, private/loopback/link-local refused by default, domain allow/deny
  lists, per-call page caps.
- 33 tests in `tests/unit/test_web_tools.py`.

## [0.7.4] - 2026-02-20

### Fixed
- Remove `type: "prompt"` hook from UserPromptSubmit that caused small-model gate to block legitimate user prompts with "Operation stopped by hook" errors
- Resolve all ty type checker diagnostics (4) and ruff linter errors (19) for clean static analysis

### Changed
- UserPromptSubmit now uses only the `complexity-check` command hook (no prompt-type gate)

## [0.7.3] - 2026-02-20

### Added
- Repository-level quality gate targets in `Makefile`:
  - `make check`
  - `make check-python`
  - `make check-go`
  - `make benchmark`
  - `make benchmark-bounded`
- Bounded benchmark execution mode for restricted environments:
  - `tests/benchmarks/conftest.py` adds `bounded_benchmark` fixture
  - controlled by `RLM_BENCHMARK_BOUNDED` and round/iteration env vars
- Integration coverage for complexity-check binary contract path (`tests/integration/test_complexity_check_binary.py`)

### Changed
- Benchmark suites now use bounded-capable benchmark fixture in:
  - `tests/benchmarks/test_phase3_benchmarks.py`
  - `tests/benchmarks/test_verification_benchmarks.py`
- Rich output now includes real spinner progress emission with throttling in `RLMConsole.emit_progress()` (SPEC-13.23, SPEC-13.25)
- Auto-activation escalation now applies a conservative budget guard to keep execution in micro mode when budget is below balanced-entry threshold (SPEC-14.23)
- Expanded explicit trace tagging and coverage for prioritized non-deferred gaps in SPEC-13/14/16/17

### Fixed
- Memory store compatibility with `rlm_core` builds that do not expose `MemoryStore.update_fields()`:
  - field updates now use a validated SQLite compatibility path when needed
  - node reads/queries/search in compatibility mode now use SQLite directly for consistent cross-session behavior
  - compatibility updates checkpoint WAL and use thread-safe connection handling for parallel session flows

### Verified
- Full quality gate pass:
  - `make check` (`3281 passed, 3 deselected`)
  - `make benchmark-bounded` (`37 passed`)
  - `uv run dp enforce pre-commit --policy dp-policy.json --json` (`ok=true`)
  - `uv run dp review --json` (`ok=true`)
  - `uv run dp verify --json` (`ok=true`)
  - `uv run dp enforce pre-push --policy dp-policy.json --json` (`ok=true`)

## [0.7.2] - 2026-02-19

### Fixed
- **maturin 1.12 build compatibility**: Created `rlm_core/` Python stub package to satisfy `python-source = "."` config (maturin 1.12 changed missing module directory from warning to error)
- Added PEP 561 `py.typed` marker and comprehensive `.pyi` type stubs for all PyO3-exported types (context, memory, LLM, trajectory, complexity, epistemic)

### Added
- Full IDE type support for `rlm_core` native extension (autocompletion, type checking)

## [0.7.1] - 2026-02-19

### Fixed
- Hook dispatch silences stderr to prevent noisy output in Claude Code
- complexity-check is now non-blocking with fail-open behavior
- plugin.json version aligned with marketplace.json

## [0.7.0] - 2026-02-02

### Changed
- **rlm-core is now a required bundled dependency** — no more optional/fallback mode
- `USE_RLM_CORE` / `RLM_USE_CORE` environment variable removed (always enabled)
- `use_rlm_core` config option removed from `rlm-config.json`
- Memory store delegates node CRUD to rlm_core's Rust SQLite backend
- Build system uses maturin for rlm-core PyO3 bindings
- rlm-core vendored as git submodule at `vendor/loop`

### Added
- `id` parameter on `rlm_core.Node()` constructor for creating nodes with pre-existing UUIDs
- `update_fields()` method on `rlm_core.MemoryStore` for field-level updates without Node immutability issues

### Fixed
- Node immutability crash (`updated.id = node_id`) by using `update_fields()` convenience method
- Foreign key constraint failures when Python SQLite and rlm_core share the same database file
- In-memory store connection lifecycle (persistent connection for `:memory:` mode)
- Evolution log schema compatibility with rlm_core's `node_id`/`reason` column names
- Provenance round-trip fidelity via metadata storage

## [0.6.1] - 2026-02-02

### Fixed
- **hook-dispatch.sh**: Export `CLAUDE_PLUGIN_ROOT` env var so Go binaries can find plugin root (fixes "plugin root not found" error)

### Removed
- `UserPromptSubmit` prompt hook — caused Claude to block normal messages; RLM guidance now lives in CLAUDE.md
- `PostToolUse` catch-all prompt hook — fired on every tool use adding cost/latency; redundant with PreToolUse complexity-check
- `PreCompact` prompt hook — low value; trajectory-save on Stop handles persistence

### Changed
- All hooks are now command-type only (no prompt hooks remain) — eliminates invisible gating behavior
- Hook architecture: command hooks provide dynamic state data, CLAUDE.md provides static workflow instructions

## [0.6.0] - 2026-02-02

### Added
- Go hook binaries replacing Python scripts (~5ms vs ~500ms startup)
- Cross-plugin event system (`~/.claude/events/`) for DP↔RLM coordination
- JSON Schema definitions for all event types
- Python event emission/consumption helpers (`src/events/`)
- Version-aware config migration (V1→V2) preserving user customizations
- Platform-aware hook dispatcher with fallback chain
- GitHub Actions CI for cross-compilation (5 platforms)
- Unit tests for hookio, events, and config packages

### Changed
- **rlm-core enabled by default** — now a required dependency as of v0.7.0
- rlm-core PyO3 bindings now support metadata, provenance, and embedding parameters
- rlm-core uses separate `-core.db` file to avoid schema conflicts with Python SQLite
- hooks.json now uses Go binaries + prompt-based hooks
- Complexity check responds to all DP phases (not just spec/review)
- RLM orchestrator agent informed about parallel tool call behavior
- session-init verifies rlm_core is importable on startup

### Fixed
- Hyperedge queries used `row["type"]` instead of `row["edge_type"]` matching SQLite schema

### Deprecated
- Python hook scripts moved to `scripts/legacy/`
- Set `RLM_USE_LEGACY_HOOKS=1` to use legacy Python hooks
