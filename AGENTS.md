# Repository Guidelines

## Project Structure & Module Organization

This repository runs a Claude Code-driven local RP engine. Core Python runtime lives in `skills/`: `server.py` and `start_server.py` serve the browser bridge, `import_prepare.py` initializes card/story folders, `round_prepare.py` creates per-turn context, and `round_deliver.py` gates and delivers approved output. Multi-agent file contracts are handled by `agent_prompts.py`, `agent_outputs.py`, `agent_memory.py`, and `agent_schemas.py`. Browser assets and generated runtime files live under `skills/styles/`; treat `content.js`, `state.js`, `.pending`, `round_context.txt`, and `progress.json` as runtime artifacts. Claude Code prompts and slash commands live in `.claude/`. Tests are under `tests/`.

## Build, Test, and Development Commands

- `python skills/start_server.py .` starts the local/LAN frontend bridge.
- `python skills/import_prepare.py "<card_folder>" "."` initializes a card/story folder.
- `python skills/round_prepare.py "<card_folder>" "."` creates `round_context.txt` and `.agent_runs/<round>/`.
- `python skills/round_deliver.py "<card_folder>" "."` validates agent artifacts, mirrors approved story output, and delivers to the frontend.
- `python -m unittest discover -s tests -v` runs the full test suite.
- `python -m py_compile skills/<file>.py` checks a touched Python file.
- `cd skills; npm install` installs the MVU `zod` dependency. Do not use `npm test`; it is a placeholder.

## Coding Style & Naming Conventions

Use Python standard-library code unless the project already depends on a package. Keep file protocols explicit JSON objects with stable keys and UTF-8 encoding. Prefer small helper modules over hidden behavior in `round_prepare.py` or `round_deliver.py`. Use snake_case for Python functions and files; preserve existing mixed Chinese/English user-facing text where it is already part of the UI or prompt contract.

## Testing Guidelines

Add focused `unittest` coverage for every behavior change. For multi-agent changes, test missing artifacts, schema rejection, context isolation, and preservation of raw player input. Keep live model calls out of tests; use temporary directories and fixtures such as `tests/fixtures/multi_agent_round/`.

## Commit & Pull Request Guidelines

Recent history uses concise prefixes such as `fix:`, `docs:`, and `test:` with Chinese summaries. Follow that style, for example `fix: 阻断缺失agent产物的交付`. PRs should describe runtime impact, list verification commands, and mention any frontend or LAN-access checks. Do not commit card folders, generated images, `.agent_runs/`, memory files, or local secrets.
