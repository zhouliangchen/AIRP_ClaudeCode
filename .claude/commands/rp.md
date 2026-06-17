Start or resume this card with the local RP orchestrator.

## Deterministic Bootstrap Result

```!
python -c "from pathlib import Path; import subprocess, sys; card=Path.cwd().resolve(); root=next((p for p in (card,*card.parents) if (p/'CLAUDE.md').exists() and (p/'.claude'/'skills').exists()), None); assert root is not None, f'Cannot locate repository root from {card}'; raise SystemExit(subprocess.call([sys.executable, str(root/'skills'/'rp_bootstrap.py'), str(card), str(root)]))"
```

Execution is mandatory, not advisory.

- The bootstrap block above is Claude Code dynamic context injection. It runs before you see this prompt and reports the actual startup action as JSON.
- If the bootstrap result says `opening_delivered`, do not run `handler.py --opening` again. Briefly report that the opening is delivered and wait for player input.
- If the bootstrap result says `turn_generated`, `rp_generate_cli.py` has already run `round_prepare.py`, dispatched `rp-input-analyst`, applied `input_analysis.output.json`, dispatched the required creative Claude Code subagents, written `gm.output.json`, `player.output.json`, `story.output.json`, and `critic.report.json`, and invoked `round_deliver.py`. Do not generate this turn again.
- If the bootstrap result says `generation_failed`, inspect `skills/styles/progress.json`, `.agent_runs/current`, and the current round artifacts before retrying.
- Native subagent dispatch uses the Agent tool. The CLI may list this capability as `Task`, but the native tool call emitted by Claude Code is `Agent`; this is normal.
- `rp_generate_cli.py` is the deterministic wrapper that invokes that Task/Agent path without relying on this top-level prompt to manually write artifacts.
- Do not describe a tooling mismatch, schema mismatch, XML/JSON wrapper issue, or recovery strategy when `rp_generate_cli.py` has already completed.
- If the bootstrap result says `waiting_for_player_input`, wait for browser input instead of inventing a player action.
- If the dynamic bootstrap output is unavailable or replaced by `[shell command execution disabled by policy]`, The first assistant action must be a PowerShell tool call.
- Do not reply with prose before the first tool result in the fallback path.
- Use the PowerShell tool on Windows. Do not use Bash for this project startup path.
- Do not call `rp-orchestrator` as an external registered skill. It may not be registered in all Claude Code modes.
- Find `{ROOT}` as the nearest ancestor containing `CLAUDE.md` and `.claude/skills/`; the current directory is the card folder unless the user says otherwise.
- Read `.claude/skills/rp.md` from `{ROOT}`.
- Read `.claude/skills/rp-orchestrator.md` from `{ROOT}`.
- Then execute those local instructions directly: determine startup mode, run import/start-server scripts when needed, read `skills/styles/import_context.txt` or `round_context.txt`, and coordinate subagents through `.agent_runs/<round>/`.
- For a new imported card with a prefilled `skills/styles/response.txt` and empty `chat_log.json`, deliver the opening with `python "{ROOT}/skills/handler.py" "<card_folder>" --opening` before waiting for player input.
- For pending player input in the fallback path, run `python "{ROOT}/skills/round_prepare.py" "<card_folder>" "{ROOT}"`, then run `python "{ROOT}/skills/rp_generate_cli.py" "<card_folder>" "{ROOT}"`. The CLI dispatches the input analyst and applies `input_analysis.output.json` before any GM/player/character creative agents run.
- The main agent handles orchestration, script execution, subagent dispatch, system iteration, and final quality gates. Routine narration and roleplay must be delegated to subagents.
- Each round uses `.agent_runs/<round>/` as the file mailbox. Only critic-approved delivery is mirrored to `skills/styles/response.txt`.
