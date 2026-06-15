# Repository Guidelines

## Project Structure & Module Organization

This repository turns Claude Code into a local RP orchestration engine. Core Python source lives in `skills/`: `server.py` serves the browser bridge on port `8765`, `start_server.py` launches services, `import_prepare.py` handles startup import, and `round_prepare.py` / `round_deliver.py` run each turn pipeline. The Node MVU validator is `skills/mvu_server.js`, with shared helpers in `skills/mvu_shared.js`. Frontend and runtime assets are in `skills/styles/`, including `index.html`, local libraries, templates, and style profiles under `skills/styles/profiles/`. Claude Code command configuration is tracked in `.claude/`. Top-level card/story folders are user runtime data and ignored by default.

## Build, Test, and Development Commands

Use PowerShell on Windows for manual operation.

- `setup-claude-code.bat`: configure/check the user environment from the repo root.
- `python skills/start_server.py .`: start the local bridge server and MVU service.
- `python skills/import_prepare.py "<card_folder>" "."`: run card/world import.
- `python skills/round_prepare.py "<card_folder>" "."`: build per-turn context.
- `python skills/round_deliver.py "<card_folder>" "."`: deliver `response.txt` and update state.
- `cd skills; npm install`: install the MVU service dependency (`zod`).
- `python -m py_compile skills/<file>.py`: syntax-check edited Python files.

`npm test` is currently a placeholder and exits with an error.

## Coding Style & Naming Conventions

Python uses 4-space indentation, `snake_case` functions, uppercase module constants, and `pathlib.Path` for filesystem paths where practical. Keep helpers small and prefer shared utilities such as `io_utils.py` and `response_parser.py` over duplicating parsing or encoding logic. JavaScript is CommonJS (`require`, `'use strict'`) with semicolons and `camelCase` variables. Preserve UTF-8 content and existing Chinese profile/UI text.

## Testing Guidelines

There is no formal test suite. For Python changes, run `python -m py_compile` on every edited module. For pipeline changes, verify with a temporary card folder through `import_prepare.py`, `start_server.py`, and a browser check at `http://localhost:8765`. Do not commit generated files such as `content.js`, `state.js`, `.pending`, `round_context.txt`, or `import_context.txt`.

## Commit & Pull Request Guidelines

Recent history uses short imperative messages, often prefixed with `fix:`, `feat:`, or `chore:`; Chinese summaries are common. Keep commits focused, for example `fix: preserve blank bootstrap state`. Pull requests should describe the affected runtime path, list verification commands, mention generated artifacts, and include screenshots or recordings for UI changes.

## Security & Configuration Tips

Never commit `image_config.local.json`, `skills/image_config.local.json`, `.image_api.json`, or `*.secret.json`. Keep user card folders, chat logs, memory, generated images, and local runtime state out of version control.
