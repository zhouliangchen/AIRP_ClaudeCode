# Repository Guidelines

## Project Structure & Module Organization

This repository runs a Claude Code-driven local RP engine. Core Python runtime lives in `skills/`: `server.py` and `start_server.py` serve the browser bridge, `import_prepare.py` initializes card/story folders, `round_prepare.py` creates per-turn context, `input_analysis_apply.py` validates semantic input analysis, and `round_deliver.py` gates approved output. Multi-agent file contracts are handled by `agent_workflow.py`, `agent_prompts.py`, `agent_outputs.py`, `agent_interactions.py`, `agent_memory.py`, `agent_schemas.py`, `input_analysis.py`, and `character_registry.py`; `control_plane_smoke.py` runs the deterministic no-live-model control-plane smoke. Browser assets and generated runtime files live under `skills/styles/`; treat `content.js`, `state.js`, `.pending`, `round_context.txt`, and `progress.json` as runtime artifacts. Claude Code prompts and slash commands live in `.claude/`. Tests are under `tests/`.

## Build, Test, and Development Commands

- `python skills/start_server.py .` starts the local/LAN frontend bridge.
- `python skills/import_prepare.py "<card_folder>" "."` initializes a card/story folder.
- `python skills/round_prepare.py "<card_folder>" "."` creates `round_context.txt` and `.agent_runs/<round>/`.
- `python skills/input_analysis_apply.py "<card_folder>" "."` validates `input_analysis.output.json`, persists approved settings/important characters, and rebuilds routed agent packets.
- `python skills/round_deliver.py "<card_folder>" "."` validates agent artifacts, mirrors approved story output, and delivers to the frontend.
- `python -m unittest discover -s tests -v` runs the full test suite.
- `python skills/control_plane_smoke.py --repo .` runs a deterministic no-live-model multi-agent control-plane smoke test.
- `python -m py_compile skills/<file>.py` checks a touched Python file.
- `cd skills; npm install` installs the MVU `zod` dependency. Do not use `npm test`; it is a placeholder.

## Final Acceptance Checklist

- `python -m unittest discover -s tests -v`
- `python skills/control_plane_smoke.py --repo .`
- `python -m py_compile skills/agent_workflow.py skills/control_plane_smoke.py skills/agent_outputs.py skills/agent_prompts.py skills/round_prepare.py skills/input_analysis.py skills/input_analysis_apply.py skills/character_registry.py skills/rp_generate_cli.py`
- Start `python skills/start_server.py .` and verify `http://localhost:8765`.
- Verify the printed LAN URL from a phone or other LAN device.
- In Claude Code, run `/rp` against a blank folder and complete at least five player turns, checking immediate player-input display, independent important-character dialogue boxes, progress updates, hot UI/image refresh, and stopping at player decisions.

## Coding Style & Naming Conventions

Use Python standard-library code unless the project already depends on a package. Keep file protocols explicit JSON objects with stable keys and UTF-8 encoding. Prefer small helper modules over hidden behavior in `round_prepare.py` or `round_deliver.py`. Use snake_case for Python functions and files; preserve existing mixed Chinese/English user-facing text where it is already part of the UI or prompt contract.

## Testing Guidelines

Add focused `unittest` coverage for every behavior change. For multi-agent changes, test missing artifacts, schema rejection, semantic input routing, context isolation, interaction traces, `repair_history.jsonl`, scheduled `memory_summaries/`, and preservation of raw player input. Keep live model calls out of tests; use temporary directories and fixtures such as `tests/fixtures/multi_agent_round/`.

## Commit & Pull Request Guidelines

Recent history uses concise prefixes such as `fix:`, `docs:`, and `test:` with Chinese summaries. Follow that style, for example `fix: 阻断缺失agent产物的交付`. PRs should describe runtime impact, list verification commands, and mention any frontend or LAN-access checks. Do not commit card folders, generated images, `.agent_runs/`, memory files, or local secrets.
