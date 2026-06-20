# Round State Machine and Agent Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Implement Scheme A: explicit schema v2 round states, frontend progress detail, post-delivery subagent cleanup, and conservative actor context refresh.

**Architecture:** Add skills/round_state.py for declared progress states and skills/agent_lifecycle.py for side-thread cleanup plus actor context versions. Existing compatibility wrappers remain, while new pipeline code writes declared state IDs and rebuilds actor packets immediately before dispatch.

**Tech Stack:** Python standard library, unittest, existing JSON mailbox contracts, existing vanilla HTML and JavaScript frontend.

---

## Scope Check

This is one implementation project. The state machine, lifecycle cleanup, and actor context refresh must be delivered together because the frontend completion state depends on delivery, memory, and cleanup, while actor dispatch progress depends on fresh per-dispatch actor packets.

## File Structure

- Create: skills/round_state.py
- Create: skills/agent_lifecycle.py
- Modify: skills/handler.py
- Modify: skills/styles/index.html
- Modify: skills/round_prepare.py
- Modify: skills/rp_generate_cli.py
- Modify: skills/agent_turn_loop.py
- Modify: skills/subgm_turn_loop.py
- Modify: skills/subgm_threads.py
- Modify: skills/round_deliver.py
- Modify: skills/agent_packets.py
- Modify: README.md
- Modify: CLAUDE.md
- Modify: .claude/skills/rp-orchestrator.md
- Test: tests/test_round_state.py
- Test: tests/test_handler_progress.py
- Test: tests/test_frontend_progress_source.py
- Test: tests/test_agent_lifecycle.py
- Test: tests/test_actor_context_versions.py
- Modify tests: tests/test_rp_generate_cli.py, tests/test_agent_turn_loop.py, tests/test_subgm_turn_loop.py, tests/test_subgm_threads.py, tests/test_multi_agent_round_e2e.py, tests/test_agent_packets.py

---

### Task 1: Round State Core

**Files:**
- Create: skills/round_state.py
- Create: tests/test_round_state.py

- [ ] **Step 1: Write failing tests**

Create tests/test_round_state.py with tests for schema v2 progress writes, undeclared state rejection, legacy retry mapping, and manifest progress append.

Run:

    python -m unittest tests.test_round_state -v

Expected before implementation: module import failure.

- [ ] **Step 2: Implement skills/round_state.py**

Implement STATE_DEFS with every state in the confirmed spec. Implement RoundStateError, build_progress_record, read_progress, write_progress_state, and write_compat_progress. write_progress_state rejects undeclared states, writes UTF-8 JSON, clamps numeric percent to 0..100, normalizes string detail into a message object, and appends manifest progress through agent_run.append_manifest_stage when manifest_run_dir is supplied.

- [ ] **Step 3: Verify and commit**

Run:

    python -m unittest tests.test_round_state -v
    git add skills/round_state.py tests/test_round_state.py
    git commit -m "feat: 增加回合状态机进度核心"

Expected: tests pass and one focused commit is created.

---

### Task 2: Handler And Frontend Progress Display

**Files:**
- Modify: skills/handler.py
- Modify: skills/styles/index.html
- Create: tests/test_handler_progress.py
- Create: tests/test_frontend_progress_source.py

- [ ] **Step 1: Write failing tests**

Create tests/test_handler_progress.py to import handler.py, override handler.STYLES to a temporary directory, call handler.write_progress("received", "已接收玩家输入", percent=10), and assert schema_version 2, state input.received, legacy_stage received. Also assert handler.read_progress returns idle when progress.json is absent.

Create tests/test_frontend_progress_source.py to read skills/styles/index.html and assert it contains id="reply-progress-detail", progress.schema_version === 2, progress.state || progress.stage, and formatProgressDetail.

Run:

    python -m unittest tests.test_handler_progress tests.test_frontend_progress_source -v

Expected before implementation: frontend source test fails and handler test fails until handler imports round_state.

- [ ] **Step 2: Implement handler wrapper**

In skills/handler.py, add import round_state. Replace write_progress and read_progress with wrappers that call round_state.write_compat_progress(_progress_path(), stage, label, percent=percent, detail=detail) and round_state.read_progress(_progress_path()).

- [ ] **Step 3: Implement frontend detail rendering**

In skills/styles/index.html:

- Add span id="reply-progress-detail" inside div id="reply-progress".
- Add CSS for reply-progress-detail with muted color, small font, max-width 260px, overflow hidden, text-overflow ellipsis, and white-space nowrap.
- Add formatProgressDetail(progress), collecting progress.state or progress.stage, detail.agent, detail.subgm_thread_id, detail.actor_call_id, detail.batch_id, detail.attempt, and detail.message.
- Update setProgressVisible(progress) so schema_version 2 uses phase and terminal to decide visibility, while legacy progress still uses the existing activeStages list.

- [ ] **Step 4: Verify and commit**

Run:

    python -m unittest tests.test_round_state tests.test_handler_progress tests.test_frontend_progress_source -v
    git add skills/handler.py skills/styles/index.html tests/test_handler_progress.py tests/test_frontend_progress_source.py
    git commit -m "feat: 支持前端状态机进度显示"

Expected: targeted tests pass and one focused commit is created.

---

### Task 3: Pipeline State Transitions

**Files:**
- Modify: skills/round_prepare.py
- Modify: skills/rp_generate_cli.py
- Modify: skills/agent_turn_loop.py
- Modify: skills/subgm_turn_loop.py
- Modify: skills/round_deliver.py
- Modify: tests/test_rp_generate_cli.py
- Modify: tests/test_agent_turn_loop.py
- Modify: tests/test_subgm_turn_loop.py

- [ ] **Step 1: Write failing tests**

In tests/test_rp_generate_cli.py:

- Extend test_run_round_writes_subagent_artifacts_and_invokes_delivery to assert skills/styles/progress.json has schema_version 2 and state complete after successful fake delivery.
- Add test_run_round_writes_retry_progress_when_delivery_requests_retry. Use fake Claude responses from _basic_responses, a fake delivery command returning {"action": "retry", "reason": "word_count"}, then assert progress.state is delivery.retrying and progress.detail.reason is word_count.

In tests/test_agent_turn_loop.py:

- Add a test that calls run_interactive_loop(..., progress_root=self.root), dispatches one GM actor call to character:Ada, and asserts progress.json is schema v2 with state gm_loop.actor_dispatch or gm_loop.completed.

Run:

    python -m unittest tests.test_rp_generate_cli.RpGenerateCliTest.test_run_round_writes_subagent_artifacts_and_invokes_delivery tests.test_rp_generate_cli.RpGenerateCliTest.test_run_round_writes_retry_progress_when_delivery_requests_retry tests.test_agent_turn_loop.AgentTurnLoopTest.test_interactive_loop_writes_actor_dispatch_progress -v

Expected before implementation: progress file is missing or still uses legacy shape.

- [ ] **Step 2: Implement state writes**

In skills/rp_generate_cli.py:

- Import round_state.
- Add helper _progress(root, state, run_dir=None, detail=None, label=None, percent=None) that writes to Path(root) / "skills" / "styles" and passes manifest_run_dir=run_dir.
- Write input_analysis.running before _ensure_input_analysis and input_analysis.applied after it.
- Write gm_loop.starting before _run_interactive_agent_loop and gm_loop.completed after it.
- Inside story dispatch, write story.running or story.preflight_repair before story agent dispatch.
- Write critic.running before critic dispatch.
- After delivery, write complete, delivery.retrying, or blocked based on delivery result.

In skills/agent_turn_loop.py:

- Change run_interactive_loop signature to accept progress_root=None.
- Add _write_loop_progress(progress_root, run_dir, state, detail=None).
- Pass progress_root from rp_generate_cli._run_interactive_agent_loop.
- Write gm_loop.gm_dispatch before GM dispatch.
- Write gm_loop.actor_batch before each actor batch.
- Write gm_loop.actor_dispatch before each actor dispatch.
- Write gm_loop.waiting_player_decision when stop_reason is player_decision; otherwise write gm_loop.completed before return.

In skills/subgm_turn_loop.py:

- Change run_side_thread and run_ready_side_threads to accept progress_root=None.
- Write gm_loop.subgm_dispatch before each subGM dispatch with detail.subgm_thread_id and detail.step.
- Pass progress_root through agent_turn_loop._run_ready_side_threads.

In skills/round_prepare.py and skills/round_deliver.py, write the exact states from the spec for preparation, delivery validation, delivery, memory, cleanup, complete, blocked, and error.

- [ ] **Step 3: Verify and commit**

Run:

    python -m unittest tests.test_rp_generate_cli tests.test_agent_turn_loop tests.test_subgm_turn_loop -v
    git add skills/round_prepare.py skills/rp_generate_cli.py skills/agent_turn_loop.py skills/subgm_turn_loop.py skills/round_deliver.py tests/test_rp_generate_cli.py tests/test_agent_turn_loop.py tests/test_subgm_turn_loop.py
    git commit -m "feat: 串联回合状态机进度"

Expected: targeted tests pass and one focused commit is created.

---

### Task 4: Post-Delivery Subagent Cleanup

**Files:**
- Create: skills/agent_lifecycle.py
- Create: tests/test_agent_lifecycle.py
- Modify: skills/subgm_threads.py
- Modify: skills/round_deliver.py
- Modify: tests/test_subgm_threads.py
- Modify: tests/test_multi_agent_round_e2e.py

- [ ] **Step 1: Write failing tests**

Create tests/test_agent_lifecycle.py:

- Create a temp card/.agent_runs/round-000005/manifest.json.
- Use subgm_threads.apply_gm_commands to start side_a with allowed character character:Ada.
- Call agent_lifecycle.cleanup_round_agents(card, run_dir, reason="delivered").
- Assert result.ok is true, side_a is in paused_side_threads, state.json status is paused, next_resume_point contains resume when the main GM schedules, active_character_reservations returns empty, and manifest.agent_lifecycle_cleanup exists.
- Add a second test where completed remains completed and paused remains paused.

In tests/test_subgm_threads.py, add test_append_subgm_message_persists_next_resume_point. Start a side thread, call append_subgm_message with status paused and next_resume_point, then assert state.next_resume_point is saved.

Run:

    python -m unittest tests.test_agent_lifecycle tests.test_subgm_threads.SubgmThreadsTest.test_append_subgm_message_persists_next_resume_point -v

Expected before implementation: agent_lifecycle import fails and next_resume_point is not persisted.

- [ ] **Step 2: Implement cleanup module and status support**

In skills/subgm_threads.py:

- Include max_steps in ACTIVE_STATUSES.
- Keep THREAD_STATUSES equal to ACTIVE_STATUSES plus paused and completed.
- In append_subgm_message, persist non-empty message.next_resume_point when state.next_resume_point is empty.

Create skills/agent_lifecycle.py with:

- ACTIVE_CLEANUP_STATUSES = {"running", "merging", "needs_gm", "blocked", "max_steps"}
- cleanup_round_agents(card_folder, run_dir, reason="delivered")
- actor_context_source_paths, compute_actor_context_version, attach_actor_context_version, returned_context_is_stale for Task 5

cleanup_round_agents must load subgm_threads.load_thread_summaries(run_dir), pause active side threads through subgm_threads.append_subgm_message(status="paused"), preserve completed and paused threads, write manifest.agent_lifecycle_cleanup, and return failed entries without rolling back delivery.

- [ ] **Step 3: Call cleanup from round_deliver.py**

After post_round_memory is attempted and before final complete progress:

- Write state agent_lifecycle.cleanup through round_state.
- Call agent_lifecycle.cleanup_round_agents(card_folder, current_run, reason="delivered") when current_run exists.
- Include agent_lifecycle_cleanup in final JSON.
- If cleanup raises, return action done with agent_lifecycle_cleanup.status degraded and failed.cleanup containing the exception text.

- [ ] **Step 4: Add e2e assertion**

In tests/test_multi_agent_round_e2e.py, create or reuse a running side thread before delivery. After delivery, assert load_thread_summaries has no status in running, merging, needs_gm, blocked, max_steps and manifest.agent_lifecycle_cleanup exists.

- [ ] **Step 5: Verify and commit**

Run:

    python -m unittest tests.test_agent_lifecycle tests.test_subgm_threads tests.test_multi_agent_round_e2e -v
    git add skills/agent_lifecycle.py skills/subgm_threads.py skills/round_deliver.py tests/test_agent_lifecycle.py tests/test_subgm_threads.py tests/test_multi_agent_round_e2e.py
    git commit -m "feat: 交付后清理未关闭subagent"

Expected: targeted tests pass and one focused commit is created.

---

### Task 5: Actor Context Versioning And Conservative Refresh

**Files:**
- Modify: skills/agent_packets.py
- Modify: skills/agent_turn_loop.py
- Modify: skills/agent_lifecycle.py
- Create: tests/test_actor_context_versions.py
- Modify: tests/test_agent_turn_loop.py
- Modify: tests/test_agent_packets.py

- [ ] **Step 1: Write failing tests**

Create tests/test_actor_context_versions.py:

- Create temp card/memory/characters/Ada/profile.md.
- Build a character packet through agent_packets.build_character_packet.
- Assert packet.context_version.hash starts with sha256: and source_paths contains memory/characters/Ada/profile.md.
- Change profile.md, rebuild the packet, and assert the hash changes.
- Call agent_lifecycle.returned_context_is_stale before and after profile change to prove false then true.

In tests/test_agent_turn_loop.py, add test_second_actor_call_reloads_profile_changed_after_first_call:

- Register character Ada.
- Write profile.md with old archive text.
- GM dispatch returns one actor call in step 1 and one actor call in step 2.
- The fake actor dispatch captures packets and rewrites profile.md after the first actor packet.
- Assert packet 1 contains old archive, packet 2 contains sealed archive key, and hashes differ.

Run:

    python -m unittest tests.test_actor_context_versions tests.test_agent_turn_loop.AgentTurnLoopTest.test_second_actor_call_reloads_profile_changed_after_first_call -v

Expected before implementation: packet lacks context_version and second dispatch does not reload disk profile.

- [ ] **Step 2: Attach context_version in agent_packets.py**

Add import agent_lifecycle. In build_player_packet and build_character_packet, assign the result of agent_projection.project_actor_context to packet and return agent_lifecycle.attach_actor_context_version(card_folder, packet).

- [ ] **Step 3: Rebuild actor packets immediately before dispatch**

In skills/agent_turn_loop.py:

- Import agent_packets and agent_lifecycle.
- Change _actor_packet to receive run_dir.
- Derive card_folder with existing _card_folder_for_run(run_dir).
- Build player packet with agent_packets.build_player_packet(card_folder, routed_input, recent_chat, actor_state=actor_state, world_state=world_state, gm_prompt=safe_prompt, gm_visibility_basis=visibility_basis).
- Build character packet with agent_packets.build_character_packet(card_folder, actor_state, routed_input, recent_chat, world_state=world_state, gm_prompt=safe_prompt, gm_visibility_basis=visibility_basis).
- Change _dispatch_actor_call to pass run_dir into _actor_packet.
- After dispatch returns and actor output validates, call agent_lifecycle.returned_context_is_stale(card_folder, packet). If true, append lifecycle_warnings entry with code actor_context_stale_after_dispatch and policy accepted_current_output_rebuild_next_call. Do not discard the actor output.

- [ ] **Step 4: Verify and commit**

Run:

    python -m unittest tests.test_actor_context_versions tests.test_agent_packets tests.test_agent_turn_loop -v
    git add skills/agent_lifecycle.py skills/agent_packets.py skills/agent_turn_loop.py tests/test_actor_context_versions.py tests/test_agent_packets.py tests/test_agent_turn_loop.py
    git commit -m "feat: 增加角色上下文版本失效机制"

Expected: targeted tests pass and one focused commit is created.

---

### Task 6: Documentation And Acceptance Verification

**Files:**
- Modify: README.md
- Modify: CLAUDE.md
- Modify: .claude/skills/rp-orchestrator.md

- [ ] **Step 1: Update docs**

In README.md:

- Update the browser progress paragraph to describe schema v2 progress, high-level phase, and structured detail.
- Update the core role subagent section to state that post-delivery cleanup pauses unfinished subGM side threads, releases reservations, and does not mark unfinished side stories completed.
- Add that player/character profile, background, or memory updates do not interrupt running calls; the next actor call rebuilds context from disk and receives a new context_version.

In CLAUDE.md, add that round execution reports declared schema v2 state IDs and completion happens after delivery, post-round memory work, and lifecycle cleanup.

In .claude/skills/rp-orchestrator.md, add that orchestration should use state-machine progress and verify cleanup plus fresh actor packet rebuilds after delivery.

- [ ] **Step 2: Run docs diff check**

Run:

    git diff --check README.md CLAUDE.md .claude/skills/rp-orchestrator.md

Expected: no whitespace errors.

- [ ] **Step 3: Commit docs**

Run:

    git add README.md CLAUDE.md .claude/skills/rp-orchestrator.md
    git commit -m "docs: 说明状态机进度与agent生命周期"

Expected: one focused documentation commit is created.

- [ ] **Step 4: Run full automated acceptance**

Run:

    python -m unittest discover -s tests -v
    python skills/control_plane_smoke.py --repo .
    python -m py_compile skills/agent_workflow.py skills/control_plane_smoke.py skills/agent_outputs.py skills/agent_prompts.py skills/round_prepare.py skills/input_analysis.py skills/input_analysis_apply.py skills/character_registry.py skills/rp_generate_cli.py skills/agent_turn_loop.py skills/subgm_turn_loop.py skills/subgm_threads.py skills/round_deliver.py skills/round_state.py skills/agent_lifecycle.py

Expected: unittest passes, smoke prints JSON with ok true, and py_compile exits 0 with no output.

- [ ] **Step 5: Run manual frontend and RP acceptance**

Run:

    python skills/start_server.py .

Verify:

- http://localhost:8765 opens.
- The printed LAN URL opens from a same-LAN phone or another device.
- During generation, progress shows high-level state and structured detail.
- In Claude Code, run /rp against a blank folder and complete at least five player turns.
- Player input appears immediately.
- Important-character dialogue boxes remain independent.
- UI and image hot refresh still work.
- The system stops at real player decision points.
- After delivery, current run side-thread summaries have no running, merging, needs_gm, blocked, or max_steps status.

- [ ] **Step 6: Inspect final status**

Run:

    git status --short --branch

Expected: clean working tree, or only intentional verification corrections. Commit corrections with:

    git add <changed-files>
    git commit -m "fix: 完成状态机与agent生命周期验证"

Skip the correction commit when no files changed.

---

## Self-Review Against Spec

- State machine: Tasks 1 through 3 declare states, write schema v2 progress, keep compatibility, and wire pipeline transitions.
- Frontend hybrid display: Task 2 adds detail rendering without broad UI redesign.
- Cleanup: Task 4 pauses active subGM side threads after delivery and preserves unfinished story continuity.
- Actor refresh: Task 5 rebuilds actor packets from disk before dispatch and does not interrupt in-flight calls.
- Tests: Each runtime behavior has focused unittest coverage plus full suite, smoke, compile, browser, LAN, and five-turn live acceptance.
- Docs: Task 6 updates only runtime documentation required by this change.
