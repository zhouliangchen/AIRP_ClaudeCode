# Dispatcher-Native Agent Collaboration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make projection, actor dispatch, and subGM dispatch first-class dispatcher intents, while structurally shrinking old GM-loop orchestration code instead of keeping duplicate old and new paths alive.

**Architecture:** Add focused collaboration helpers around existing tested GM/actor/subGM behavior, wire `request_projection`, `run_actor`, and `run_subgm_thread` executors in `agent_dispatcher.py`, then reduce `run_gm_turn` so it creates follow-up intents instead of hiding all collaboration inside one loop call.

**Tech Stack:** Python standard library, JSON/JSONL file protocols, existing `unittest` suite, existing `.agent_runs/<round>/` runtime folders, existing Claude Code subprocess dispatch helper in `rp_generate_cli.py`.

---

## Scope Check

This plan implements Phase 1 through Phase 4 from `docs/superpowers/specs/2026-06-22-remaining-agent-autonomy-refactor-design.md`:

- extract collaboration helpers,
- implement projection and actor dispatcher executors,
- implement subGM dispatcher executor,
- shrink `run_gm_turn` and remove the replaced hidden collaboration path from the default live route.

This plan intentionally excludes:

- `assets_task`,
- `system_request`,
- source self-modification workflow,
- broad UI/image changes,
- manual five-turn `/rp` acceptance.

Those belong in the next plan after dispatcher-native collaboration is stable.

## Maintainability Rule

Every behavior task must include a simplification step. Do not add a new dispatcher path while leaving the old path as an equally active default route. A temporary compatibility branch is allowed only when a later task in this plan removes or quarantines it and tests prove it is no longer the default live route.

## File Structure

- Create `skills/agent_actor_runtime.py`: shared projection, actor packet, actor dispatch, response message, actor artifact, and trace helper functions extracted from `agent_turn_loop.py`.
- Create `tests/test_agent_actor_runtime.py`: focused helper tests with no live model calls.
- Modify `skills/agent_turn_loop.py`: delegate actor request/projection/dispatch/persistence to `agent_actor_runtime.py`; later shrink default GM-loop behavior.
- Modify `tests/test_agent_turn_loop.py`: keep behavior tests, update only implementation-shape assertions.
- Modify `skills/agent_dispatcher.py`: add `request_projection`, `run_actor`, and `run_subgm_thread` executors.
- Modify `tests/test_agent_dispatcher.py`: add executor tests and no-default-hidden-collaboration tests.
- Modify `skills/subgm_turn_loop.py`: expose a single-thread dispatcher-friendly wrapper or helper result shape if needed.
- Modify `tests/test_subgm_turn_loop.py`: preserve current side-thread behavior, add wrapper coverage if a wrapper is introduced.
- Modify `skills/control_plane_smoke.py`: prove dispatcher evidence includes explicit `request_projection` and `run_actor`; optionally one `run_subgm_thread`.
- Modify `tests/test_control_plane_smoke.py`: assert the expanded intent chain.
- Modify `README.md`, `CLAUDE.md`, and `AGENTS.md`: document dispatcher-native collaboration after behavior lands.

---

### Task 1: Characterize Current Collaboration Boundaries

**Files:**
- Modify: `tests/test_agent_dispatcher.py`
- Modify: `tests/test_agent_turn_loop.py`
- Modify: `tests/test_subgm_turn_loop.py`

- [ ] **Step 1: Add dispatcher gap tests for currently declared but unwired intent types**

In `tests/test_agent_dispatcher.py`, add failing tests that create pending intents of these types:

- `request_projection`
- `run_actor`
- `run_subgm_thread`

Each test should assert the final desired behavior, not `executor_not_wired`:

- `request_projection` accepts and completes the intent, appends one `projected_message`, and creates one `run_actor` intent.
- `run_actor` blocks with `projected_message_missing` when no projected message exists.
- `run_subgm_thread` dispatches one ready side thread through a fake dispatch function and completes the intent.

- [ ] **Step 2: Add a no-hidden-collaboration assertion for future `run_gm_turn`**

Add a test that proves the target `run_gm_turn` behavior:

- fake GM output contains one actor call,
- dispatcher completes `run_gm_turn`,
- dispatcher creates a `request_projection` intent,
- dispatcher does not call the actor dispatch function in the same `run_gm_turn` step,
- no `artifacts/actor.outputs.json` is written by `run_gm_turn` itself.

Mark this as the target behavior for Task 7. It should fail at this point.

- [ ] **Step 3: Preserve existing behavior tests before extraction**

Run:

```powershell
python -m unittest tests.test_agent_turn_loop tests.test_subgm_turn_loop tests.test_agent_dispatcher -v
```

Expected: only the new target tests fail. Existing GM/actor/subGM behavior must stay green.

- [ ] **Step 4: Commit failing characterization tests**

Commit only if the team policy allows committing failing tests for plan checkpoints. Otherwise keep these edits for Task 2 implementation before committing.

Preferred final commit after Task 2:

```powershell
git add tests/test_agent_dispatcher.py tests/test_agent_turn_loop.py tests/test_subgm_turn_loop.py
git commit -m "test: 描述dispatcher原生协作目标"
```

---

### Task 2: Extract Actor Collaboration Helpers Without Behavior Change

**Files:**
- Create: `skills/agent_actor_runtime.py`
- Create: `tests/test_agent_actor_runtime.py`
- Modify: `skills/agent_turn_loop.py`
- Modify: `tests/test_agent_turn_loop.py`

- [ ] **Step 1: Create helper module skeleton**

Create `skills/agent_actor_runtime.py` for logic that is shared by current GM loop and future dispatcher executors.

Initial public helpers should be narrow:

- `record_request_actor(...)`
- `project_actor_request(...)`
- `record_actor_response(...)`
- `append_actor_output_artifact(...)`
- `read_actor_outputs_artifact(...)`

Do not move batching, perception continuation, or dialogue-transfer policy yet unless required by these helpers.

- [ ] **Step 2: Move message/intent recording helpers out of `agent_turn_loop.py`**

Move equivalent behavior from:

- `_record_request_actor_intent`
- `_record_projected_actor_message`
- `_record_actor_response_message`

into `agent_actor_runtime.py`.

While moving, align names with the new runtime:

- create `request_projection` intents, not new `project_message` intents;
- keep a narrow migration reader for existing `project_message` only if an existing test requires it;
- do not keep both names as equal live write paths.

- [ ] **Step 3: Update `agent_turn_loop.py` to call the helper module**

Replace local helper calls with `agent_actor_runtime` calls.

The current loop behavior should remain unchanged:

- request message is appended,
- projected message is appended,
- actor response message is appended,
- actor packet still receives context version,
- stale context warnings still work.

- [ ] **Step 4: Add focused helper tests**

Create `tests/test_agent_actor_runtime.py` covering:

- request actor message plus `request_projection` intent creation,
- projected message completion,
- actor response message creation,
- actor artifact append preserves existing outputs,
- helper rejects missing message or intent IDs with clear errors.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
python -m unittest tests.test_agent_actor_runtime tests.test_agent_turn_loop -v
python -m py_compile skills/agent_actor_runtime.py skills/agent_turn_loop.py
```

Expected: PASS except Task 1 target dispatcher tests that still require executors.

- [ ] **Step 6: Commit**

```powershell
git add skills/agent_actor_runtime.py skills/agent_turn_loop.py tests/test_agent_actor_runtime.py tests/test_agent_turn_loop.py tests/test_agent_dispatcher.py tests/test_subgm_turn_loop.py
git commit -m "refactor: 抽取actor协作运行时 helpers"
```

---

### Task 3: Implement `request_projection` Executor

**Files:**
- Modify: `skills/agent_dispatcher.py`
- Modify: `tests/test_agent_dispatcher.py`
- Modify: `skills/agent_actor_runtime.py`
- Modify: `tests/test_agent_actor_runtime.py`

- [ ] **Step 1: Define projection intent payload contract**

Use this payload shape:

```json
{
  "actor_id": "character:Ada",
  "source_message_id": "msg_000001",
  "source_call_id": "call-character-Ada-1"
}
```

The executor should locate the source `request_actor` message, validate the target actor, and read the embedded call.

- [ ] **Step 2: Add tests for successful projection**

In `tests/test_agent_dispatcher.py`, assert:

- `request_projection` intent transitions pending -> accepted -> completed,
- one `projected_message` is delivered to the actor inbox,
- one `run_actor` intent is created,
- result contains `created_messages`, `created_intents`, and `intent_type: request_projection`.

- [ ] **Step 3: Add tests for projection failure**

Cover:

- missing source message -> `projection_source_missing`,
- source message is not `request_actor` -> `projection_source_invalid`,
- actor-facing leakage or visibility failure -> existing visibility/projection reason,
- rejected projected message -> blocked projection intent.

- [ ] **Step 4: Implement executor**

In `agent_dispatcher.py`:

- wire `if intent_type == "request_projection"`,
- accept the intent,
- call `agent_actor_runtime.project_actor_request(...)`,
- create a `run_actor` follow-up intent idempotently,
- complete or block with structured evidence.

- [ ] **Step 5: Remove old `project_message` live writer**

Search:

```powershell
rg -n "project_message" skills tests
```

Expected after cleanup:

- no live writer creates `project_message`,
- tests may mention it only as a legacy-read or migration case if explicitly retained.

- [ ] **Step 6: Run tests**

```powershell
python -m unittest tests.test_agent_dispatcher tests.test_agent_actor_runtime tests.test_agent_messages -v
python -m py_compile skills/agent_dispatcher.py skills/agent_actor_runtime.py
```

- [ ] **Step 7: Commit**

```powershell
git add skills/agent_dispatcher.py skills/agent_actor_runtime.py tests/test_agent_dispatcher.py tests/test_agent_actor_runtime.py tests/test_agent_messages.py
git commit -m "feat: dispatcher执行projection意图"
```

---

### Task 4: Implement `run_actor` Executor

**Files:**
- Modify: `skills/agent_dispatcher.py`
- Modify: `skills/agent_actor_runtime.py`
- Modify: `tests/test_agent_dispatcher.py`
- Modify: `tests/test_agent_actor_runtime.py`

- [ ] **Step 1: Define actor intent payload contract**

Use this payload shape:

```json
{
  "actor_id": "character:Ada",
  "projected_message_id": "msg_000002",
  "source_call_id": "call-character-Ada-1"
}
```

The actor executor must not build packets from raw GM call data directly. It reads the projected message payload and dispatches that packet.

- [ ] **Step 2: Add successful run_actor test**

In `tests/test_agent_dispatcher.py`, create a projected message and pending `run_actor` intent. Use a fake `run_claude`/dispatch path that returns valid actor output.

Assert:

- intent completes,
- `actor_response` message is appended,
- `artifacts/actor.outputs.json` includes the actor output,
- response includes artifact path,
- a GM continuation intent is created when the actor output requires GM resolution.

- [ ] **Step 3: Add safety tests**

Cover:

- missing projected message -> `projected_message_missing`,
- projected message target does not match actor -> `projected_message_actor_mismatch`,
- unprojected actor call data cannot run actor,
- invalid actor schema blocks with `actor_dispatch_failed`,
- player high-risk or `stop_for_player_decision` output creates player-decision state and does not create story/delivery follow-ups.

- [ ] **Step 4: Implement actor dispatch helper**

In `agent_actor_runtime.py`, add a helper that:

- extracts packet from projected message,
- dispatches actor through the existing dispatch function,
- validates actor output using existing schema path,
- records actor response message,
- appends `artifacts/actor.outputs.json`,
- records stale context warnings when returned context hash differs.

Reuse existing `agent_turn_loop` validation behavior instead of duplicating schema logic.

- [ ] **Step 5: Wire dispatcher executor**

In `agent_dispatcher.py`:

- add `run_actor` branch,
- require `run_claude`,
- accept intent,
- call actor helper,
- create follow-up `run_gm_turn` unless a player-decision stop or other pending progression intent makes that unsafe,
- complete or block with structured result.

- [ ] **Step 6: Run tests**

```powershell
python -m unittest tests.test_agent_dispatcher tests.test_agent_actor_runtime tests.test_agent_turn_loop -v
python -m py_compile skills/agent_dispatcher.py skills/agent_actor_runtime.py skills/agent_turn_loop.py
```

- [ ] **Step 7: Commit**

```powershell
git add skills/agent_dispatcher.py skills/agent_actor_runtime.py tests/test_agent_dispatcher.py tests/test_agent_actor_runtime.py tests/test_agent_turn_loop.py
git commit -m "feat: dispatcher执行actor意图"
```

---

### Task 5: Implement `run_subgm_thread` Executor

**Files:**
- Modify: `skills/agent_dispatcher.py`
- Modify: `skills/subgm_turn_loop.py`
- Modify: `tests/test_agent_dispatcher.py`
- Modify: `tests/test_subgm_turn_loop.py`

- [ ] **Step 1: Define subGM intent payload contract**

Use this payload shape:

```json
{
  "thread_id": "side_suli_rooftop",
  "reason": "gm_requested_side_thread"
}
```

- [ ] **Step 2: Add dispatcher tests**

Cover:

- ready thread completes one bounded dispatch and completes the intent,
- paused/completed thread completes as skipped/noop with evidence,
- invalid thread id blocks with `subgm_dispatch_failed`,
- side-thread actor calls still reject player and out-of-boundary characters,
- result includes side-thread status and called actors.

- [ ] **Step 3: Add a dispatcher-friendly wrapper if needed**

If `subgm_turn_loop.run_side_thread(...)` already returns enough evidence, use it directly. If not, add a small wrapper that:

- normalizes result shape,
- records common bus messages if not already mirrored,
- exposes created/updated side-thread artifact paths,
- leaves existing `run_side_thread` behavior intact.

Do not rewrite subGM loop internals in this task.

- [ ] **Step 4: Wire dispatcher executor**

In `agent_dispatcher.py`:

- add `run_subgm_thread` branch,
- require `run_claude`,
- accept intent,
- dispatch one thread,
- complete on successful thread advancement or noop,
- block only on invalid thread, permission, schema, or dispatch failure.

If the subGM result implies GM arbitration is needed, create a follow-up `run_gm_turn` intent.

- [ ] **Step 5: Run tests**

```powershell
python -m unittest tests.test_agent_dispatcher tests.test_subgm_turn_loop tests.test_subgm_threads -v
python -m py_compile skills/agent_dispatcher.py skills/subgm_turn_loop.py skills/subgm_threads.py
```

- [ ] **Step 6: Commit**

```powershell
git add skills/agent_dispatcher.py skills/subgm_turn_loop.py tests/test_agent_dispatcher.py tests/test_subgm_turn_loop.py tests/test_subgm_threads.py
git commit -m "feat: dispatcher执行subGM支线意图"
```

---

### Task 6: Make `run_gm_turn` Create Collaboration Intents

**Files:**
- Modify: `skills/agent_dispatcher.py`
- Modify: `skills/agent_turn_loop.py`
- Modify: `tests/test_agent_dispatcher.py`
- Modify: `tests/test_agent_turn_loop.py`

- [ ] **Step 1: Add a GM-only step helper**

Extract a helper that performs one GM dispatch and validation pass without dispatching actors or runnable subGM threads internally.

It should:

- read input and world state,
- dispatch GM once,
- validate and sanitize GM output,
- apply allowed promotions and subGM commands,
- record GM output and trace,
- return normalized GM output and derived follow-up work.

It should not:

- call actor agents,
- call subGM agents,
- write `actor.outputs.json`,
- call story/critic/delivery.

- [ ] **Step 2: Update `run_gm_turn` executor**

Change dispatcher `run_gm_turn` to use the GM-only helper.

Follow-up creation rules:

- actor calls -> `request_projection`,
- runnable side thread -> `run_subgm_thread`,
- GM player decision -> terminal player-decision state,
- no actor/subGM work and complete/word target/max steps -> `compose_story`,
- rollback/repair request -> existing repair/rollback intent path.

- [ ] **Step 3: Keep old loop only as explicit compatibility helper**

Rename or quarantine the broad old loop path so it is not the default live route.

Rules:

- `rp_generate_cli.run_round()` must not call the broad hidden-collaboration loop directly.
- `agent_dispatcher._execute_run_gm_turn()` must not call the broad hidden-collaboration loop.
- Existing tests that need the old loop behavior should call the compatibility helper directly and be labeled as helper regression tests.

- [ ] **Step 4: Add branch-reduction search**

Run:

```powershell
rg -n "_run_interactive_agent_loop|run_interactive_loop|actor.outputs.json|request_projection|project_message" skills tests
```

Expected:

- broad interactive loop is not used by live dispatcher path,
- `project_message` is gone or migration-only,
- actor output writing from GM turn is gone from default dispatcher path,
- tests clearly distinguish helper regression from live dispatcher behavior.

- [ ] **Step 5: Run tests**

```powershell
python -m unittest tests.test_agent_dispatcher tests.test_rp_generate_cli tests.test_agent_turn_loop tests.test_agent_outputs -v
python -m py_compile skills/agent_dispatcher.py skills/agent_turn_loop.py skills/rp_generate_cli.py
```

- [ ] **Step 6: Commit**

```powershell
git add skills/agent_dispatcher.py skills/agent_turn_loop.py tests/test_agent_dispatcher.py tests/test_agent_turn_loop.py tests/test_rp_generate_cli.py tests/test_agent_outputs.py
git commit -m "refactor: 让GM回合创建协作意图"
```

---

### Task 7: Expand Deterministic Smoke To Explicit Collaboration Intents

**Files:**
- Modify: `skills/control_plane_smoke.py`
- Modify: `tests/test_control_plane_smoke.py`

- [ ] **Step 1: Add smoke assertions**

In `tests/test_control_plane_smoke.py`, assert completed intent types include:

- `analyze_input`
- `run_gm_turn`
- `request_projection`
- `run_actor`
- `compose_story`
- `review_critic`
- `deliver_round`

If the smoke fixture includes a side thread, also assert `run_subgm_thread`.

- [ ] **Step 2: Update smoke fake dispatch**

Modify `control_plane_smoke.py` so fake GM output creates at least one actor call. The dispatcher should then process:

```text
run_gm_turn -> request_projection -> run_actor -> run_gm_turn or compose_story
```

Do not manually complete projection/actor intents in the smoke. The dispatcher should execute them.

- [ ] **Step 3: Add message evidence**

Smoke payload should include message types:

- `request_actor`
- `projected_message`
- `actor_response`

- [ ] **Step 4: Run smoke tests**

```powershell
python -m unittest tests.test_control_plane_smoke -v
python skills/control_plane_smoke.py --repo .
```

- [ ] **Step 5: Commit**

```powershell
git add skills/control_plane_smoke.py tests/test_control_plane_smoke.py
git commit -m "test: smoke覆盖dispatcher原生actor协作"
```

---

### Task 8: Documentation And Old-Path Cleanup

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `AGENTS.md`
- Modify: tests/docs as needed

- [ ] **Step 1: Update architecture docs**

Document that:

- dispatcher executes projection and actor intents explicitly,
- GM turn no longer hides actor/subGM dispatch as the default live path,
- `agent_actor_runtime.py` owns shared actor collaboration helpers,
- old broad loop behavior is helper/regression-only if retained.

- [ ] **Step 2: Remove stale fixed-flow descriptions**

Search and update:

```powershell
rg -n "GM loop.*actor|project_message|actor.outputs.json|story.input.json|fixed workflow|hidden collaboration|root artifact" README.md CLAUDE.md AGENTS.md
```

Keep references only where they describe current delivery or memory boundary exports.

- [ ] **Step 3: Run doc checks**

```powershell
git diff --check README.md CLAUDE.md AGENTS.md
```

- [ ] **Step 4: Commit**

```powershell
git add README.md CLAUDE.md AGENTS.md
git commit -m "docs: 记录dispatcher原生协作运行时"
```

---

### Task 9: Final Verification And Structural Simplification Audit

**Files:**
- Verify all touched files.

- [ ] **Step 1: Run full unit tests**

```powershell
python -m unittest discover -s tests -v
```

Expected: PASS.

- [ ] **Step 2: Run deterministic smoke**

```powershell
python skills/control_plane_smoke.py --repo .
```

Expected: JSON includes `"ok": true`, `"manifest_stage": "delivered"`, completed dispatcher intent types including `request_projection` and `run_actor`, and message types including `projected_message` and `actor_response`.

- [ ] **Step 3: Run compile checks**

```powershell
python -m py_compile skills/agent_dispatcher.py skills/agent_actor_runtime.py skills/agent_messages.py skills/agent_intents.py skills/agent_outputs.py skills/agent_turn_loop.py skills/subgm_threads.py skills/subgm_turn_loop.py skills/rp_generate_cli.py skills/round_prepare.py skills/round_deliver.py skills/control_plane_smoke.py
```

Expected: no output and exit code 0.

- [ ] **Step 4: Search for duplicate old/new orchestration paths**

```powershell
rg -n "project_message|executor_not_wired|_run_interactive_agent_loop|run_interactive_loop|advise_next_actions|agent_workflow|manifest.expected_outputs is required" skills tests README.md CLAUDE.md AGENTS.md --glob "!skills/node_modules/**"
```

Expected:

- no `agent_workflow` or `advise_next_actions`,
- no live `project_message` writer,
- no `executor_not_wired` for declared supported intents,
- broad interactive loop references are helper/regression-only and not the dispatcher default path.

- [ ] **Step 5: Search root artifact authority**

```powershell
rg -n "run_dir / \"story.input.json\"|root / \"story.input.json\"|run_dir / \"actor.outputs.json\"|root / \"actor.outputs.json\"|run_dir / \"gm.output.json\"|root / \"gm.output.json\"" skills tests --glob "!skills/node_modules/**"
```

Expected:

- root artifact references are delivery, memory, or explicit compatibility test boundaries,
- dispatcher-owned control paths use `artifacts/`.

- [ ] **Step 6: Check git status**

```powershell
git status --short --branch
```

Expected: only intentional files are changed.

- [ ] **Step 7: Commit verification fixes if any**

```powershell
git add <changed-files>
git commit -m "fix: 完成dispatcher原生协作验证修正"
```

---

## Self-Review

Spec coverage:

- `request_projection` executor is covered by Task 3.
- `run_actor` executor is covered by Task 4.
- `run_subgm_thread` executor is covered by Task 5.
- `run_gm_turn` structural shrink is covered by Task 6.
- Smoke evidence is covered by Task 7.
- Documentation and cleanup are covered by Tasks 8 and 9.

Maintainability coverage:

- Task 2 extracts only focused helpers.
- Task 6 removes the default hidden collaboration path instead of wrapping it forever.
- Task 9 searches for obsolete branches and root-artifact authority reads.

Deferred items:

- `assets_task` and `system_request` are intentionally deferred to the next plan.
- Manual five-turn `/rp` acceptance is deferred until after this implementation plan passes deterministic verification.

Ambiguity check:

- `run_gm_turn` target behavior is explicit: it dispatches GM and creates follow-up intents; it does not dispatch actor/subGM work internally on the default live path.
- `run_actor` requires a projected message; raw actor call payloads are not sufficient.
- subGM authority remains enforced by existing subGM validators.
