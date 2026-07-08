# castcode

`AGENTS.md` is a symlink to this file. Edit either path; they are the same document.

An open-source terminal UI for the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/python), built in [Textual](https://textual.textualize.io/). The goal is a TUI on par with the Claude Code and Codex CLIs — tool rendering, conversation rewind/fork, history, permission modes, model picker, skills, and so on. The SDK provides these natively (it drives the Claude Code CLI over a JSON stream); our job is to wire them up and surface them in a UI we build. We use the **Python** SDK only.

## Docs

- `ref-docs/` — vendored reference material (see `ref-docs/README.md`): Agent SDK pages under `ref-docs/cas/` and Textual guides under `ref-docs/textual/`, each also as a combined single file. Prefer the per-page file; the combined files are large, so read them in chunks (the Read tool caps at ~25k tokens/call).
- `exp-docs/` — working/intermediate docs: `HANDOFF.md` (current state + the reasoning behind non-obvious choices — keep it in sync when behavior ships) and `tools/` (tool inventory, captures, and rendering plan).

## Layout

The app lives in the `castcode/` package, split by concern with an acyclic import graph (leaves at the bottom):

- `castcode/format.py` — pure format/string/constants helpers (loads the banner art from `castcode/assets/` via `importlib.resources`). Leaf.
- `castcode/assets/` — packaged data only: the `logo1.txt`/`logo2.txt` banner art.
- `castcode/records.py` — pure transcript-record dataclasses (`UserRecord`/`AssistantRecord`/`NoticeRecord`/`ToolRecord`, `BlockPreview`) — the save/restore contract for chat rows. Leaf.
- `castcode/commands.py` — the local slash-command registry: `route`, `popup_rows`, `help_text`. Leaf.
- `castcode/interaction.py` — `InteractionState` plus the idle/gate predicates (`full_idle`, `can_submit`, …) and `PERMISSION_MODES`. Leaf.
- `castcode/rewind.py` — pure rewind/fork planning over records + checkpoints (`plan_rewind`/`apply_rewind_uuid_map`/`build_branch_plan`). Imports `records`.
- `castcode/sidecar.py` — saved-view persistence: the `SidecarEnvelope` JSON codec and atomic `write_sidecar`/`read_sidecar`. Imports `records`.
- `castcode/tool_render/` — tool input/result rendering, as a package: `types` (`ToolDisplay`/`BlockPreview`/`ToolPreview`), `sanitize` (bounded-text core), `content` (block/image/dict extraction), `renderers` (the per-tool renderers + generic fallback), `registry` (name→renderer table), and `__init__` exposing `render_tool_input`/`render_tool_result`/`render_tool_result_detail`/`render_tool_result_soft_error`/`render_tool_error`. Imports `format`.
- `castcode/ui/` — chat-surface widgets, as a package: `messages` (message rows, `from_record`/`to_record`), `prompts` (approval/question prompts), `input` (prompt text area + `CommandPopup`), `layout` (`Banner`, `Chat` with `rebuild`/`records`), `status` (`StatusLine`/`StatusData`), `todos` (`todo_text`), `pickers` (`Switcher`/`RewindPicker`/`ModelPicker` bottom takeovers), `selectors` (shared selector-line rendering), `screens` (`ChatScreen`). Imports `format` and `records` only — never `tool_render`, `state`, or the SDK (widgets take pre-rendered strings).
- `castcode/session.py` — the **only** module that touches the SDK client (`ClaudeSDKClient`). `Session` wraps connect/query/receive/interrupt/set_permission_mode/set_model/disconnect plus session metadata (list/fork/sidecar paths, model and command rows, context usage); `build_options` builds `ClaudeAgentOptions`.
- `castcode/state.py` — `Conversation`, the active-conversation **aggregate**: owns everything that defines one conversation — `session`, `session_id`, `transcript`, `checkpoints`, uuid anchors, `todos`, `cost_usd`, `model`/`model_id`, `context_pct`, `permission_mode`, `commands`, `draft`, connect lifecycle, and the context-window math (`record_result`/`_context_window`). The unit of save/restore for rewind + switching. Also holds `TodoState`.
- `castcode/conversation.py` — the SDK receive loop (including checkpoint capture), as functions taking `app`. Reaches the client via `app.conversation.session`.
- `castcode/permissions.py` — the permission/approval flow, as functions taking `app`.
- `castcode/switching.py` — conversation switching (`switch_to`/`resume_cold`), as functions taking `app`.
- `castcode/app.py` — `CastcodeApp(App)`: hosts `ChatScreen` via `get_default_screen`, SDK connect/send workers, bindings, lifecycle, the activity line (the status line itself lives in `castcode/ui/status.py`; `app.py` only builds a `StatusData` and pushes it). SDK *message/result types* may be used here; only the *client* is isolated to `session.py`.

Root `app.py` is a thin shim (`from castcode.app import CastcodeApp` + a `__main__` guard) so `textual run --dev app.py` works; `castcode/cli.py::main` is the installed `castcode` console entry (`CastcodeApp().run()`); `castcode/app.tcss` is the single packaged stylesheet.

Gotcha-dense flows (the receive loop, the permission flow) live as plain functions taking `app` explicitly rather than as mixins; Textual handlers (`on_*`) and bindings stay on `CastcodeApp`. `connect`/`send` are `@work` methods so headless tests can stub them simply. Core flow modules import UI leaves (`castcode.ui.messages`, `castcode.ui.layout`) directly; `castcode/ui/__init__.py` is deliberately empty — there is no barrel.

## Running & testing

Runtime lives in `.venv/` (Python 3.12) — always invoke via `.venv/bin/...`.

- Run with live CSS reload: `.venv/bin/textual run --dev app.py` (quit with ctrl+q).
- Test headless with `App.run_test()`, stubbing `CastcodeApp.connect` with a no-op worker and injecting a fake client at `app.conversation.session.client` to avoid the network. Run the suite: `.venv/bin/python -m pytest`.
- Lint: `.venv/bin/ruff check .` (config in `pyproject.toml`; keep it clean).

## Packaging & release

`pyproject.toml` packages the runtime as the `castcode` CLI:

- Local/ZIP install smoke path: `uv tool install .`, then run `castcode`.
- `castcode` inherits the shell working directory as the agent working directory, because `castcode.format.CWD` is captured from `os.getcwd()` at process start and passed into `ClaudeAgentOptions.cwd`.
- The public GitHub default branch is `release`, not local dev `main`.
- The `release` branch is intentionally curated to contain only `README.md`, `pyproject.toml`, and `castcode/`; do not push tests, `ref-docs/`, `exp-docs/`, or other dev files there.
- To publish a new runtime snapshot after committing on local `main`, run `./scripts/push-release.sh`. It refuses dirty worktrees, rebuilds `release` from the allowlist, and pushes only `origin/release`.
- Use `./scripts/update-release-branch.sh` when you only want to refresh the local `release` branch without pushing.
- `./scripts/push-testing.sh` publishes a `testing` branch the same way: everything except `ref-docs/`, `exp-docs/`, and `scripts/` (runtime plus tests, `pytest.ini`, `conftest.py`, ruff config, lockfile). `./scripts/update-testing-branch.sh` refreshes it locally without pushing.

## Conventions

- Barebones; few or no comments; no fallbacks (let errors surface).
- All styling in `castcode/app.tcss` (multi-line, one property per line) — no color markup in Python.
- Code lives in the `castcode/` package, split by concern (not by noun); `castcode/app.tcss` stays the single packaged stylesheet. Add a module/class/registry/screen only when code landing *now* needs it — never for future use alone.
