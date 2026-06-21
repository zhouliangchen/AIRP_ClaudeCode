# Repository Guidelines

## Project Structure & Module Organization

This repository runs a Claude Code-driven local RP engine. Core Python runtime lives in `skills/`: `server.py` and `start_server.py` serve the browser bridge, `import_prepare.py` initializes card/story folders, `round_prepare.py` creates per-turn context, `input_analysis_apply.py` validates semantic input analysis, and `round_deliver.py` gates approved output. Multi-agent file contracts are handled by `agent_workflow.py`, `agent_messages.py`, `agent_intents.py`, `agent_snapshots.py`, `agent_prompts.py`, `agent_outputs.py`, `agent_interactions.py`, `agent_memory.py`, `agent_schemas.py`, `input_analysis.py`, and `character_registry.py`; `agent_messages.py`, `agent_intents.py`, and `agent_snapshots.py` implement the message runtime, executable intent lifecycle, and rollback snapshots; `control_plane_smoke.py` runs the deterministic no-live-model control-plane smoke. Browser assets and generated runtime files live under `skills/styles/`; treat `content.js`, `state.js`, `.pending`, `round_context.txt`, and `progress.json` as runtime artifacts. Claude Code prompts and slash commands live in `.claude/`. Tests are under `tests/`.

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
- `python -m py_compile skills/agent_workflow.py skills/agent_messages.py skills/agent_intents.py skills/agent_snapshots.py skills/control_plane_smoke.py skills/agent_outputs.py skills/agent_prompts.py skills/round_prepare.py skills/input_analysis.py skills/input_analysis_apply.py skills/character_registry.py skills/rp_generate_cli.py`
- Start `python skills/start_server.py .` and verify `http://localhost:8765`.
- Verify the printed LAN URL from a phone or other LAN device.
- In Claude Code, run `/rp` against a blank folder and complete at least five player turns, checking immediate player-input display, independent important-character dialogue boxes, progress updates, hot UI/image refresh, and stopping at player decisions.

## Coding Style & Naming Conventions

Use Python standard-library code unless the project already depends on a package. Keep file protocols explicit JSON objects with stable keys and UTF-8 encoding. Prefer small helper modules over hidden behavior in `round_prepare.py` or `round_deliver.py`. Use snake_case for Python functions and files; preserve existing mixed Chinese/English user-facing text only when it is part of the tested UI, runtime output, or prompt contract.

## Semantic Input Policy

Production code must not infer the meaning, intent, routing, visibility, character participation, hidden facts, retcons, or important-character declarations of user-authored input through fixed keyword, substring, or regex matches. User-input semantics must come from explicit structured UI payloads or validated model-produced `input_analysis.output.json`. Fallback code may preserve raw text, hashes, and file-mailbox state, but must not persist or route semantic decisions from player text. Tests may use scenario text as fixtures, but source logic must not special-case fixture phrases.

## Testing Guidelines

Add focused `unittest` coverage for every behavior change. For multi-agent changes, test missing artifacts, schema rejection, semantic input routing, context isolation, interaction traces, `repair_history.jsonl`, scheduled `memory_summaries/`, and preservation of raw player input. Keep live model calls out of tests; use temporary directories and fixtures such as `tests/fixtures/multi_agent_round/`.

## Project Agent Memory

Maintain technical documentation under `docs/` and keep `README.md` as the top-level user guide. At the start of every non-trivial project task, read `README.md` and the relevant files under `docs/`, then review the user's request item by item before acting. User instructions may be unreasonable, inaccurate, or imprecise; reject unsafe or incoherent requests, correct inaccurate wording into a technically sound objective, and execute the corrected objective.

After each task, update `README.md` and the relevant `docs/` files when the implementation, workflow, commands, or architecture changed, but don't update the `docs/superpowers/` file without permission. If no documentation needs to change, state that explicitly in the final response.

This project is still in active development. Do not add compatibility layers for old generated game files, old runtime artifacts, or obsolete internal APIs unless the user explicitly requests it. Prefer simple current-format logic over backward-compatible branches.

For newly created documentation files only, use Simplified Chinese for `README.md`-style user guides and technical documents under `docs/` by default; English technical terms are acceptable. Newly created files under `docs/superpowers/**` should use English because those files are Superpowers specs, plans, and workflow records. Newly created repository instruction files, including `AGENTS.md`, `CLAUDE.md`, and `.claude/skills/*.md`, should also use English. When editing existing documents, preserve the existing document language and do not translate, rewrite, or simplify them solely to satisfy this language rule unless the user explicitly requests that language conversion.

When resolving recurring issues such as encoding problems, path naming mistakes, shell-specific behavior, or repeated toolchain failures, record the confirmed solution in project-level memory or the relevant technical document so future tasks do not repeat the same failure.

## Commit & Pull Request Guidelines

When creating git commits for this repository, write commit messages in a standard Simplified Chinese format. Recent history uses concise prefixes such as `fix:`, `docs:`, and `test:`; keep the prefix if it helps, but write the subject and body in clear Simplified Chinese, for example `fix: 阻止缺失 agent 产物时继续交付`. Keep subjects short and specific. PRs should describe runtime impact, list verification commands, and mention any frontend or LAN-access checks. Do not commit card folders, generated images, `.agent_runs/`, memory files, or local secrets.
