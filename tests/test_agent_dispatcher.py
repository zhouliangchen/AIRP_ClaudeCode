import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class AgentDispatcherFoundationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.run_dir = self.card / ".agent_runs" / "round-000001"
        self.run_dir.mkdir(parents=True)
        (self.card / ".agent_runs" / "current").write_text(str(self.run_dir.resolve()), encoding="utf-8")
        _write_json(self.run_dir / "manifest.json", {"round_id": "round-000001", "stage": "prepared"})
        _write_json(self.run_dir / "input.json", {"raw_text": "I listen.", "routed_input": {"role_channel": "I listen."}})
        self.dispatcher = _load("agent_dispatcher")
        self.intents = _load("agent_intents")
        if not hasattr(self.dispatcher, "agent_snapshots"):
            self.dispatcher.agent_snapshots = _load("agent_snapshots")
        self._restore_after_test(self.dispatcher.input_analysis_apply, "apply_current_run")
        self._restore_after_test(self.dispatcher.rp_generate_cli, "_dispatch_agent_payload")
        self._restore_after_test(self.dispatcher.rp_generate_cli, "_run_legacy_interactive_agent_loop")
        self._restore_after_test(self.dispatcher.rp_generate_cli, "_run_delivery")
        self._restore_after_test(self.dispatcher.agent_outputs, "build_story_input")
        self._restore_after_test(self.dispatcher, "_dispatch_agent_payload")
        if hasattr(self.dispatcher, "agent_snapshots"):
            self._restore_after_test(self.dispatcher.agent_snapshots, "restore_snapshot")

    def tearDown(self):
        self.tmp.cleanup()

    def _restore_after_test(self, owner, name):
        original = getattr(owner, name)
        self.addCleanup(setattr, owner, name, original)

    def _root_with_settings(self, settings):
        root = Path(self.tmp.name) / f"root_{len(list(Path(self.tmp.name).glob('root_*'))):02d}"
        settings_path = root / "skills" / "styles" / "settings.json"
        _write_json(settings_path, settings)
        return root

    def _legacy_to_capability_requests(self, requests):
        registry = _load("capability_registry")
        return [registry.legacy_routing_request_to_capability(item) for item in requests]

    def test_dispatcher_exposes_executor_modules(self):
        self.assertTrue(hasattr(self.dispatcher, "input_executor"))
        self.assertTrue(hasattr(self.dispatcher, "actor_executor"))
        self.assertTrue(hasattr(self.dispatcher, "delivery_executor"))

    def _stub_apply_result_with_routing_requests(self, requests, settings=None):
        capability_requests = self._legacy_to_capability_requests(requests)
        _write_json(
            self.run_dir / "manifest.json",
            {
                "round_id": "round-000001",
                "stage": "prepared",
                "runtime_settings": settings or {
                    "selfRepairMode": "limited",
                    "allowSourceCodeSelfRepair": False,
                },
            },
        )

        def fake_apply(card_folder, root_dir):
            _write_json(
                self.run_dir / "input_analysis.output.json",
                {
                    "schema_version": 1,
                    "routing_requests": requests,
                    "capability_requests": capability_requests,
                },
            )
            return {
                "ok": True,
                "stage": "analysis_applied",
                "run_dir": str(self.run_dir),
                "root_dir": str(root_dir),
                "hidden_facts_persisted": 0,
                "important_characters_persisted": [],
                "routed_input": {"role_channel": "I listen.", "user_instruction_channel": ""},
                "manifest": {
                    "round_id": "round-000001",
                    "stage": "analysis_applied",
                    "runtime_settings": settings or {
                        "selfRepairMode": "limited",
                        "allowSourceCodeSelfRepair": False,
                    },
                },
                "routing_requests": requests,
                "capability_requests": capability_requests,
            }

        self.dispatcher.input_analysis_apply.apply_current_run = fake_apply

    def _install_dispatcher_dependencies(self):
        if not hasattr(self.dispatcher, "agent_outputs"):
            self.dispatcher.agent_outputs = _load("agent_outputs")
        if not hasattr(self.dispatcher, "rp_generate_cli"):
            self.dispatcher.rp_generate_cli = _load("rp_generate_cli")

    def _write_valid_postprocess_artifact(self):
        postprocess = {
            "schema_version": 1,
            "core": {
                "summary": "You reach the sealed door.",
                "current_goal": "Confirm whether to force the door.",
                "options": ["Step back and reassess"],
            },
            "ui_extension_status": {"status": "ok", "issues": []},
        }
        _write_json(self.run_dir / "artifacts" / "postprocess.output.json", postprocess)
        return postprocess

    def _append_projected_actor_message(
        self,
        *,
        actor_id="character:Ada",
        source_call_id="call-character-Ada-1",
        packet=None,
        payload_actor_id=None,
        to=None,
    ):
        packet = packet if packet is not None else {"actor_id": actor_id, "visible_context": {"scene": "hall"}}
        return self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "projection",
                "to": to if to is not None else [actor_id],
                "type": "projected_message",
                "visibility": "actor_facing",
                "source_call_id": source_call_id,
                "payload": {
                    "actor_id": payload_actor_id if payload_actor_id is not None else actor_id,
                    "source_message_id": "msg_source",
                    "packet": packet,
                    "gm_prompt": "Listen at the door.",
                },
            },
        )["message"]

    def _create_run_actor_intent(
        self,
        projected_message,
        *,
        actor_id="character:Ada",
        source_call_id="call-character-Ada-1",
        extra_payload=None,
    ):
        payload = {
            "actor_id": actor_id,
            "projected_message_id": projected_message,
            "source_call_id": source_call_id,
        }
        if extra_payload:
            payload.update(extra_payload)
        return self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "projection",
                "type": "run_actor",
                "source_message_id": projected_message,
                "payload": payload,
            },
        )["intent"]

    def _install_projection_stub(self, decision="pass", final_actor_message="Listen at the door.", feedback=""):
        def fake_dispatch(agent_key, run_dir, root_dir, run_claude, extra_context):
            self.assertEqual(agent_key, "projection")
            packet = extra_context.get("projection_packet")
            self.assertIsInstance(packet, dict)
            return {
                "decision": decision,
                "target_actor_id": packet.get("target_actor_id", ""),
                "source_call_id": packet.get("source_call_id", ""),
                "final_actor_message": final_actor_message,
                "feedback": feedback,
            }

        self.dispatcher._dispatch_agent_payload = fake_dispatch

    def _actor_resolution_output(self, actor_id="character:Ada"):
        return {
            "agent": "player" if actor_id == "player" else "character",
            "agent_id": actor_id,
            "character_name": actor_id.split(":", 1)[1] if actor_id.startswith("character:") else "",
            "events": [
                {
                    "type": "custom_action",
                    "target": "archive door",
                    "content": "I test the lock.",
                    "metadata": {
                        "category": "interaction",
                        "visible_content": "I test the lock.",
                        "requires_gm_resolution": True,
                        "risk_level": "low",
                    },
                }
            ],
            "stop_reason": "continue",
        }

    def _actor_dialogue_continue_output(self, actor_id="character:Ada"):
        return {
            "agent": "player" if actor_id == "player" else "character",
            "agent_id": actor_id,
            "character_name": actor_id.split(":", 1)[1] if actor_id.startswith("character:") else "",
            "events": [
                {
                    "type": "dialogue",
                    "target": "player",
                    "content": "The door is quiet.",
                    "metadata": {
                        "exact_visible_words": "The door is quiet.",
                        "delivery_channel": "spoken",
                        "visible_tone_or_action": "calm report",
                    },
                }
            ],
            "stop_reason": "continue",
        }

    def _actor_player_decision_output(self, actor_id="character:Ada"):
        return {
            "agent": "player" if actor_id == "player" else "character",
            "agent_id": actor_id,
            "character_name": actor_id.split(":", 1)[1] if actor_id.startswith("character:") else "",
            "events": [
                {
                    "type": "dialogue",
                    "target": "player",
                    "content": "Choose before I open it.",
                    "metadata": {
                        "exact_visible_words": "Choose before I open it.",
                        "delivery_channel": "spoken",
                        "visible_tone_or_action": "quiet warning",
                    },
                }
            ],
            "stop_reason": "stop_for_player_decision",
        }

    def _run_two_actor_fanout_until_actors_pending(self):
        self._install_dispatcher_dependencies()
        self._write_multi_actor_input()
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "input_analyst", "type": "run_gm_turn", "payload": {}},
        )["intent"]
        actor_calls = [
            self._gm_actor_call(actor_id="character:Ada", call_id="call-character-Ada-1"),
            self._gm_actor_call(actor_id="character:Bea", call_id="call-character-Bea-1"),
        ]

        def fake_dispatch(agent_key, _run_dir, _root, _run_claude, _extra_context):
            if agent_key == "gm":
                return self._gm_output(actor_calls=actor_calls, stop_reason="continue")
            if agent_key == "projection":
                packet = _extra_context["projection_packet"]
                return {
                    "decision": "pass",
                    "target_actor_id": packet["target_actor_id"],
                    "source_call_id": packet["source_call_id"],
                    "final_actor_message": packet["requested_actor_message"],
                    "feedback": "",
                }
            return self._actor_outputs_by_agent[agent_key]

        self._actor_outputs_by_agent = {}
        self.dispatcher._dispatch_agent_payload = fake_dispatch

        gm_result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")
        self.assertTrue(gm_result["ok"])
        self.assertEqual(gm_result["intent_id"], created["id"])
        self.assertEqual([intent["type"] for intent in self.intents.list_intents(self.run_dir, "pending")], [
            "request_projection",
            "request_projection",
        ])
        self.assertTrue(self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")["ok"])
        self.assertTrue(self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")["ok"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([intent["type"] for intent in pending], ["run_actor", "run_actor"])
        return created

    def _write_subgm_input(self):
        _write_json(
            self.run_dir / "input.json",
            {
                "raw_text": "I stay in class.",
                "routed_input": {"role_channel": "I stay in class."},
                "character_contexts": {
                    "characters": [
                        {
                            "name": "SuLi",
                            "role": "quiet classmate",
                            "location": "school rooftop",
                            "sensory_channels": ["visual", "auditory"],
                        },
                        {
                            "name": "Bert",
                            "role": "distant witness",
                            "location": "library",
                            "sensory_channels": ["visual"],
                        },
                    ]
                },
            },
        )

    def _start_subgm_thread(self, *, thread_id="side_suli_rooftop", status="running"):
        self._write_subgm_input()
        subgm_threads = _load("subgm_threads")
        subgm_threads.apply_gm_commands(
            self.run_dir,
            [
                {
                    "action": "start",
                    "thread_id": thread_id,
                    "title": "Rooftop warning",
                    "outline": "SuLi checks the rooftop sigil.",
                    "time_window": "same morning",
                    "location": "school rooftop",
                    "objective": "Advance the off-screen clue.",
                    "allowed_characters": ["character:SuLi"],
                    "forbidden_characters": ["player"],
                    "priority": "normal",
                    "message": "Start now.",
                    "metadata": {},
                }
            ],
        )
        if status == "paused":
            subgm_threads.apply_gm_commands(
                self.run_dir,
                [{"action": "pause", "thread_id": thread_id, "message": "Pause.", "metadata": {}}],
            )
        elif status == "completed":
            subgm_threads.apply_gm_commands(
                self.run_dir,
                [{"action": "close", "thread_id": thread_id, "message": "Done.", "metadata": {}}],
            )

    def _create_run_subgm_thread_intent(self, thread_id="side_suli_rooftop"):
        return self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "run_subgm_thread",
                "payload": {"thread_id": thread_id, "reason": "gm_requested_side_thread"},
            },
        )["intent"]

    def _subgm_output(self, *, status="completed", actor_calls=None):
        return {
            "agent": "subGM",
            "thread_id": "side_suli_rooftop",
            "status": status,
            "scene_beats": [{"content": "SuLi sees chalk dust beside the rooftop vent."}],
            "events": [{"type": "scene", "content": "A chalk line glows on the vent."}],
            "actor_calls": actor_calls or [],
            "messages_to_gm": [{"content": "The rooftop clue is ready."}],
            "world_state_delta": [{"scope": "rooftop", "fact": "chalk dust found"}],
            "character_usage": ["character:SuLi"],
            "promotion_requests": [],
            "boundary_requests": [],
            "notes_for_story": ["Use only after GM merges it."],
            "next_resume_point": "resume at the rooftop vent",
        }

    def _subgm_actor_call(self, actor_id="character:SuLi", call_id="call-character-SuLi-1"):
        return {
            "call_id": call_id,
            "actor_id": actor_id,
            "prompt": "You notice chalk dust near the vent.",
            "reason": "The actor is present in the side thread.",
            "visibility_basis": {
                "mode": "direct",
                "summary": f"{actor_id} is directly addressed by this side-thread prompt.",
                "target_actor": actor_id,
                "visible_to": [actor_id],
            },
        }

    def _subgm_character_output(self):
        return {
            "agent": "character",
            "agent_id": "character:SuLi",
            "character_name": "SuLi",
            "events": [{"type": "dialogue", "target": "", "content": "I found chalk dust.", "metadata": {}}],
            "stop_reason": "continue",
        }

    def _write_character_context_packet(self, safe_name, *, role="archive guard"):
        _write_json(
            self.run_dir / "characters" / f"{safe_name}.context.json",
            {
                "actor_id": f"character:{safe_name}",
                "agent": "character",
                "visibility": "first_person_character",
                "immersive_context": f"{safe_name} remembers the {role} oath.",
                "memory": {"key_memories": [f"I serve as {role}."]},
                "visible_events": [{"type": "scene", "content": "The archive hall is quiet."}],
            },
        )

    def _write_gm_actor_input(self):
        _write_json(
            self.run_dir / "input.json",
            {
                "raw_text": "I listen.",
                "routed_input": {"role_channel": "I listen."},
                "character_contexts": {
                    "characters": [
                        {
                            "name": "Ada",
                            "role": "archive guard",
                            "location": "hall",
                            "sensory_channels": ["visual", "auditory"],
                        }
                    ]
                },
            },
        )
        self._write_character_context_packet("Ada")

    def _write_multi_actor_input(self):
        _write_json(
            self.run_dir / "input.json",
            {
                "raw_text": "I wait.",
                "routed_input": {"role_channel": "I wait."},
                "character_contexts": {
                    "characters": [
                        {
                            "name": "Ada",
                            "role": "archive guard",
                            "location": "hall",
                            "sensory_channels": ["visual", "auditory"],
                        },
                        {
                            "name": "Bea",
                            "role": "night clerk",
                            "location": "hall",
                            "sensory_channels": ["visual", "auditory"],
                        },
                    ]
                },
            },
        )
        self._write_character_context_packet("Ada", role="archive guard")
        self._write_character_context_packet("Bea", role="night clerk")

    def _gm_actor_call(self, actor_id="character:Ada", call_id="call-character-Ada-1"):
        return {
            "call_id": call_id,
            "actor_id": actor_id,
            "prompt": "You hear a quiet knock near the archive door.",
            "reason": "Ada is standing beside the archive door.",
            "metadata": {},
            "visibility_basis": {
                "mode": "direct",
                "summary": f"{actor_id} can directly hear the knock.",
                "target_actor": actor_id,
                "visible_to": [actor_id],
            },
        }

    def _gm_output(
        self,
        *,
        actor_calls=None,
        subgm_commands=None,
        decision_point=None,
        stop_reason="complete",
    ):
        return {
            "agent": "gm",
            "scene_beats": [{"content": "The archive door clicks once."}],
            "events": [],
            "actor_calls": actor_calls if actor_calls is not None else [],
            "parallel_groups": [],
            "world_state_delta": [],
            "perception_responses": [],
            "decision_point": decision_point,
            "stop_reason": stop_reason,
            "subgm_commands": subgm_commands if subgm_commands is not None else [],
        }

    def _subgm_start_command(self, thread_id="side_ada_archive"):
        return {
            "action": "start",
            "thread_id": thread_id,
            "title": "Ada checks the archive",
            "outline": "Ada checks a noise off screen.",
            "time_window": "same minute",
            "location": "archive door",
            "objective": "Find whether the archive is occupied.",
            "allowed_characters": ["character:Ada"],
            "forbidden_characters": ["player"],
            "message": "Start the archive side thread.",
            "metadata": {},
        }

    def test_dispatch_agent_payload_uses_loop_prompt_for_subgm_packets(self):
        prompts = []
        dispatch_calls = []

        def fake_read_loop_prompt(run_dir, manifest, agent_key, packet=None):
            prompts.append((Path(run_dir), manifest, agent_key, packet))
            return "generated subGM loop prompt"

        def fake_dispatch(agent_key, prompt, root_dir, run_claude, extra_context=None):
            dispatch_calls.append((agent_key, prompt, Path(root_dir), extra_context))
            return {"agent": "subGM", "thread_id": "side_suli_rooftop", "status": "completed"}

        self.dispatcher.rp_generate_cli._read_loop_prompt = fake_read_loop_prompt
        self.dispatcher.rp_generate_cli._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher._dispatch_agent_payload(
            "subGM:side_suli_rooftop",
            self.run_dir,
            ROOT,
            run_claude=lambda *_args: "{}",
            extra_context={"packet": {"thread_id": "side_suli_rooftop"}},
        )

        self.assertEqual(result["agent"], "subGM")
        self.assertEqual(prompts[0][2], "subGM:side_suli_rooftop")
        self.assertEqual(prompts[0][3], {"thread_id": "side_suli_rooftop"})
        self.assertEqual(dispatch_calls[0][1], "generated subGM loop prompt")

    def test_dispatch_agent_payload_does_not_fallback_on_loop_prompt_generation_bug(self):
        def fake_read_loop_prompt(_run_dir, _manifest, _agent_key, _packet=None):
            raise ValueError("prompt generation bug")

        self.dispatcher.rp_generate_cli._read_loop_prompt = fake_read_loop_prompt
        self.dispatcher.rp_generate_cli._dispatch_agent_payload = lambda *_args, **_kwargs: self.fail(
            "dispatch should not run after prompt generation bug"
        )

        with self.assertRaisesRegex(ValueError, "prompt generation bug"):
            self.dispatcher._dispatch_agent_payload(
                "subGM:side_suli_rooftop",
                self.run_dir,
                ROOT,
                run_claude=lambda *_args: "{}",
                extra_context={"packet": {"thread_id": "side_suli_rooftop"}},
            )

    def test_dispatch_next_blocks_unsupported_intent(self):
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "story", "type": "paint_scene", "payload": {"target": "scene"}},
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "unsupported_intent_type")
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])

    def test_assets_task_records_deferred_nonblocking_artifact_and_message(self):
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "story",
                "type": "assets_task",
                "payload": {
                    "kind": "scene",
                    "target": "rainy_pier",
                    "prompt": "Rainy pier at dusk.",
                    "source": "story.output.json",
                },
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "nonblocking_assets_task_deferred")
        completed = self.intents.list_intents(self.run_dir, "completed")
        self.assertEqual([item["id"] for item in completed], [created["id"]])
        self.assertEqual(completed[0]["result"]["outputs"]["status"], "deferred")
        self.assertTrue(completed[0]["result"]["outputs"]["nonblocking"])
        artifact_path = self.run_dir / "artifacts" / "assets_tasks" / f"{created['id']}.json"
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.assertEqual(artifact["status"], "deferred")
        self.assertEqual(artifact["intent_id"], created["id"])
        self.assertEqual(artifact["kind"], "scene")
        self.assertEqual(artifact["target"], "rainy_pier")
        self.assertTrue(artifact["nonblocking"])
        messages = self.dispatcher.agent_messages.read_messages(self.run_dir)
        self.assertEqual([message["type"] for message in messages], ["assets_task"])
        self.assertEqual(messages[0]["payload"]["status"], "deferred")
        self.assertEqual(messages[0]["payload"]["artifact"], f"artifacts/assets_tasks/{created['id']}.json")

    def test_assets_task_updates_ui_schema_and_postprocess_contract(self):
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "assets-ui",
                "type": "assets_task",
                "payload": {
                    "kind": "ui_schema",
                    "target": "relationship_panel",
                    "ui_schema": {
                        "version": 1,
                        "postprocess_data_required": ["ui_extensions.status_panels.relationships"],
                        "elements": {
                            "relationships": {"data_key": "ui_extensions.status_panels.relationships"}
                        },
                    },
                    "postprocess_contract": {
                        "ui_extensions": {
                            "status_panels": {
                                "relationships": {
                                    "type": "object",
                                    "description": "Visible relationship status panel data",
                                }
                            }
                        }
                    },
                },
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        completed = self.intents.list_intents(self.run_dir, "completed")
        self.assertEqual([item["id"] for item in completed], [created["id"]])
        outputs = completed[0]["result"]["outputs"]
        self.assertEqual(outputs["status"], "completed")
        self.assertEqual(outputs["ui_schema_status"], "applied")
        self.assertEqual(outputs["postprocess_contract_status"], "synced")
        ui_manifest = json.loads((self.card / "ui_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(ui_manifest["ui_schema"]["elements"]["relationships"]["data_key"], "ui_extensions.status_panels.relationships")
        contract = json.loads((self.card / "postprocess_contract.json").read_text(encoding="utf-8"))
        self.assertEqual(
            contract["ui_extensions"]["status_panels"]["relationships"]["type"],
            "object",
        )
        artifact_path = self.run_dir / "artifacts" / "assets_tasks" / f"{created['id']}.json"
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.assertEqual(artifact["status"], "completed")
        self.assertEqual(artifact["postprocess_contract_status"], "synced")

    def test_assets_task_records_postprocess_contract_repair_when_ui_schema_requires_data_without_contract(self):
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "assets-ui",
                "type": "assets_task",
                "payload": {
                    "kind": "ui_schema",
                    "target": "relationship_panel",
                    "ui_schema": {
                        "version": 1,
                        "postprocess_data_required": ["ui_extensions.status_panels.relationships"],
                    },
                },
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        completed = self.intents.list_intents(self.run_dir, "completed")
        outputs = completed[0]["result"]["outputs"]
        self.assertEqual(outputs["status"], "deferred")
        self.assertEqual(outputs["ui_schema_status"], "applied")
        self.assertEqual(outputs["postprocess_contract_status"], "repair_pending")
        queue_path = self.card / ".agent_runs" / "postprocess_repair_queue.jsonl"
        queue_items = [
            json.loads(line)
            for line in queue_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(queue_items[0]["scope"], "postprocess_contract")
        self.assertEqual(
            queue_items[0]["required_keys"],
            ["ui_extensions.status_panels.relationships"],
        )

    def test_dispatch_next_uses_oldest_pending_intent(self):
        first = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "story", "type": "assets_task", "payload": {"target": "first"}},
        )["intent"]
        second = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "story", "type": "assets_task", "payload": {"target": "second"}},
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertEqual(result["intent_id"], first["id"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["id"] for item in pending], [second["id"]])

    def test_dispatch_next_blocks_stalled_runtime_when_no_pending_intents(self):
        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "stalled")
        self.assertEqual(result["reason"], "dispatcher_stalled")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "blocked")
        self.assertEqual(manifest["dispatcher"]["reason"], "dispatcher_stalled")

    def test_dispatch_next_reports_delivered_without_blocking_when_manifest_delivered(self):
        _write_json(self.run_dir / "manifest.json", {"round_id": "round-000001", "stage": "delivered"})

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "delivered")
        self.assertEqual(result["reason"], "")
        self.assertEqual(self.intents.list_intents(self.run_dir, "blocked"), [])

    def test_artifact_path_rejects_absolute_relative_path(self):
        absolute = self.run_dir / "escape.json"

        with self.assertRaises(self.dispatcher.AgentDispatcherError):
            self.dispatcher.artifact_path(self.run_dir, str(absolute))

    def test_artifact_path_rejects_parent_escape(self):
        with self.assertRaises(self.dispatcher.AgentDispatcherError):
            self.dispatcher.artifact_path(self.run_dir, "../escape.json")

    def test_dispatch_next_preserves_existing_blocked_manifest_reason(self):
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "story", "type": "paint_scene", "payload": {"target": "scene"}},
        )["intent"]

        first_result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)
        manifest_after_first = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        first_history = list(manifest_after_first.get("stage_history", []))
        second_result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)
        manifest_after_second = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertFalse(first_result["ok"])
        self.assertEqual(first_result["reason"], "unsupported_intent_type")
        self.assertFalse(second_result["ok"])
        self.assertEqual(second_result["status"], "blocked")
        self.assertEqual(second_result["intent_id"], "")
        self.assertEqual(second_result["reason"], "unsupported_intent_type")
        self.assertEqual(manifest_after_second["dispatcher"]["reason"], "unsupported_intent_type")
        self.assertEqual(manifest_after_second.get("stage_history", []), first_history)
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])

    def test_analyze_input_completes_and_creates_run_gm_turn(self):
        _write_json(self.run_dir / "input_analysis.output.json", {"analysis_mode": "fixture"})
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "main_agent",
                "type": "analyze_input",
                "payload": {"input_analysis_request_path": "input_analysis.request.md"},
            },
        )["intent"]
        apply_calls = []

        def fake_apply(card_folder, root_dir):
            apply_calls.append((Path(card_folder), Path(root_dir)))
            return {"ok": True, "analysis": {"analysis_mode": "fixture"}}

        self.dispatcher.input_analysis_apply.apply_current_run = fake_apply

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["artifacts"], ["artifacts/input_analysis.output.json"])
        self.assertTrue((self.run_dir / "artifacts" / "input_analysis.output.json").exists())
        completed = self.intents.list_intents(self.run_dir, "completed")
        self.assertEqual([item["id"] for item in completed], [created["id"]])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["run_gm_turn"])
        self.assertEqual(result["created_intents"], [pending[0]["id"]])
        self.assertEqual(pending[0]["requested_by"], "input_analyst")
        self.assertEqual(pending[0]["payload"], {"reason": "input_analysis_applied"})
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(len(apply_calls), 1)
        messages = self.dispatcher.agent_messages.read_messages(self.run_dir)
        applied = [item for item in messages if item.get("type") == "analysis_applied"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(result["created_messages"], [applied[0]["id"]])
        self.assertEqual(applied[0]["to"], ["gm", "main_agent"])
        self.assertEqual(applied[0]["payload"]["applied"]["analysis"]["analysis_mode"], "fixture")

    def test_analyze_input_blocks_with_failure_when_apply_raises_after_accept(self):
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "main_agent",
                "type": "analyze_input",
                "payload": {"input_analysis_request_path": "input_analysis.request.md"},
            },
        )["intent"]

        def fail_apply(_card_folder, _root_dir):
            raise RuntimeError("fixture apply exploded")

        self.dispatcher.input_analysis_apply.apply_current_run = fail_apply

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "analyze_input_failed")
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertIn("fixture apply exploded", blocked[0]["result"]["outputs"]["error"])
        self.assertEqual(self.intents.list_intents(self.run_dir, "completed"), [])
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "blocked")
        self.assertEqual(manifest["dispatcher"]["reason"], "analyze_input_failed")
        self.assertIn("fixture apply exploded", manifest["dispatcher"]["detail"]["error"])
        self.assertEqual(self.dispatcher.agent_messages.read_messages(self.run_dir), [])

    def test_analyze_input_reuses_apply_path_analysis_applied_message(self):
        _write_json(self.run_dir / "input_analysis.output.json", {"analysis_mode": "fixture"})
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "main_agent",
                "type": "analyze_input",
                "payload": {"input_analysis_request_path": "input_analysis.request.md"},
            },
        )["intent"]

        def fake_apply(card_folder, root_dir):
            message_result = self.dispatcher.agent_messages.append_message(
                self.run_dir,
                {
                    "from": "input_analyst",
                    "to": ["gm"],
                    "type": "analysis_applied",
                    "visibility": "gm_only",
                    "payload": {
                        "input_path": "input.json",
                        "analysis_path": "input_analysis.output.json",
                        "routed_characters": [],
                    },
                },
            )
            self.assertTrue(message_result["ok"])
            return {"ok": True, "analysis": {"analysis_mode": "fixture"}}

        self.dispatcher.input_analysis_apply.apply_current_run = fake_apply

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        messages = self.dispatcher.agent_messages.read_messages(self.run_dir)
        applied = [item for item in messages if item.get("type") == "analysis_applied"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0]["to"], ["gm"])
        self.assertEqual(result["created_messages"], [applied[0]["id"]])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["run_gm_turn"])

    def test_analyze_input_ignores_stale_unrelated_analysis_applied_message(self):
        _write_json(self.run_dir / "input_analysis.output.json", {"analysis_mode": "fixture"})
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "main_agent",
                "type": "analyze_input",
                "payload": {"input_analysis_request_path": "input_analysis.request.md"},
            },
        )["intent"]
        stale = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "input_analyst",
                "to": ["gm"],
                "type": "analysis_applied",
                "visibility": "gm_only",
                "payload": {"unrelated": True},
            },
        )
        self.assertTrue(stale["ok"])

        def fake_apply(_card_folder, _root_dir):
            return {"ok": True, "analysis": {"analysis_mode": "fixture"}}

        self.dispatcher.input_analysis_apply.apply_current_run = fake_apply

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        messages = self.dispatcher.agent_messages.read_messages(self.run_dir)
        applied = [item for item in messages if item.get("type") == "analysis_applied"]
        self.assertEqual(len(applied), 2)
        self.assertEqual(applied[0]["payload"], {"unrelated": True})
        self.assertEqual(applied[1]["to"], ["gm", "main_agent"])
        self.assertEqual(applied[1]["payload"]["applied"]["analysis"]["analysis_mode"], "fixture")
        self.assertEqual(result["created_messages"], [applied[1]["id"]])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["run_gm_turn"])

    def test_analyze_input_processes_capability_requests_and_preserves_migration_output_keys(self):
        request = {
            "id": "cap-assets",
            "requested_by": "input_analyst",
            "target": "assets-ui",
            "capability": "assets.generate_image",
            "summary": "Create a scene image.",
            "reason": "User requested a visual update.",
            "source_channel": "user_instruction",
            "risk": "low",
            "authorization_gate": "none",
            "payload": {"kind": "scene", "target": "scene_illustration", "prompt": "misty bridge"},
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "draw bridge"},
        }

        def fake_apply(card_folder, root_dir):
            _write_json(
                self.run_dir / "input_analysis.output.json",
                {
                    "schema_version": 1,
                    "routing_requests": [],
                    "capability_requests": [request],
                },
            )
            return {
                "ok": True,
                "stage": "analysis_applied",
                "run_dir": str(self.run_dir),
                "root_dir": str(root_dir),
                "manifest": {
                    "round_id": "round-000001",
                    "stage": "analysis_applied",
                    "runtime_settings": {"selfRepairMode": "limited", "allowSourceCodeSelfRepair": False},
                },
                "routing_requests": [],
                "capability_requests": [request],
            }

        self.dispatcher.input_analysis_apply.apply_current_run = fake_apply
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "system", "type": "analyze_input", "payload": {"analysis_path": "input_analysis.output.json"}},
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, Path(self.tmp.name))

        self.assertTrue(result["ok"])
        pending_types = [item["type"] for item in self.intents.list_intents(self.run_dir, "pending")]
        self.assertIn("assets_task", pending_types)
        self.assertIn("run_gm_turn", pending_types)
        self.assertIn("capability_requests", result["detail"])
        self.assertIn("routing_requests", result["detail"])
        self.assertEqual(result["detail"]["capability_requests"], result["detail"]["routing_requests"])
        completed = self.intents.list_intents(self.run_dir, "completed")
        analyze = [item for item in completed if item["id"] == created["id"]][0]
        self.assertIn("capability_requests", analyze["result"]["outputs"])
        self.assertIn("routing_requests", analyze["result"]["outputs"])
        self.assertEqual(
            analyze["result"]["outputs"]["capability_requests"],
            analyze["result"]["outputs"]["routing_requests"],
        )
        artifact = json.loads(
            (self.run_dir / analyze["result"]["outputs"]["capability_requests"]["results"][0]["artifact"]).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(artifact["capability"], "assets.generate_image")

    def test_analyze_input_missing_capability_requests_does_not_double_process_legacy_routes(self):
        request = {
            "id": "route-assets",
            "type": "assets_ui_task",
            "source_channel": "user_instruction",
            "summary": "Create a scene image.",
            "target": "assets-ui",
            "payload": {"kind": "scene", "target": "scene_illustration", "prompt": "misty bridge"},
            "requires_authorization": False,
            "authorization_gate": "none",
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "draw bridge"},
        }

        def fake_apply(card_folder, root_dir):
            _write_json(
                self.run_dir / "input_analysis.output.json",
                {
                    "schema_version": 1,
                    "routing_requests": [request],
                },
            )
            return {
                "ok": True,
                "stage": "analysis_applied",
                "run_dir": str(self.run_dir),
                "root_dir": str(root_dir),
                "manifest": {
                    "round_id": "round-000001",
                    "stage": "analysis_applied",
                    "runtime_settings": {"selfRepairMode": "limited", "allowSourceCodeSelfRepair": False},
                },
                "routing_requests": [request],
            }

        self.dispatcher.input_analysis_apply.apply_current_run = fake_apply
        self.intents.create_intent(
            self.run_dir,
            {"requested_by": "system", "type": "analyze_input", "payload": {"analysis_path": "input_analysis.output.json"}},
        )

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, Path(self.tmp.name))

        self.assertTrue(result["ok"])
        pending_types = [item["type"] for item in self.intents.list_intents(self.run_dir, "pending")]
        self.assertEqual(pending_types, ["run_gm_turn"])
        self.assertEqual(result["detail"]["capability_requests"]["processed_count"], 0)

    def test_analyze_input_creates_assets_task_from_routing_request(self):
        request = {
            "id": "route-assets",
            "type": "assets_ui_task",
            "source_channel": "user_instruction",
            "summary": "Create a scene image.",
            "target": "assets-ui",
            "payload": {
                "kind": "scene",
                "target": "scene_illustration",
                "prompt": "misty bridge",
                "ui_schema": {"postprocess_data_required": ["ui_extensions.status_panels.weather"]},
                "postprocess_contract": {
                    "ui_extensions": {
                        "status_panels": {"weather": {"type": "object"}}
                    }
                },
            },
            "requires_authorization": False,
            "authorization_gate": "none",
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "draw bridge"},
        }
        self._stub_apply_result_with_routing_requests([request])
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "system", "type": "analyze_input", "payload": {"analysis_path": "input_analysis.output.json"}},
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, Path(self.tmp.name))

        self.assertTrue(result["ok"])
        pending_types = [item["type"] for item in self.intents.list_intents(self.run_dir, "pending")]
        self.assertIn("assets_task", pending_types)
        self.assertIn("run_gm_turn", pending_types)
        assets_intent = [
            item for item in self.intents.list_intents(self.run_dir, "pending")
            if item["type"] == "assets_task"
        ][0]
        self.assertEqual(
            assets_intent["payload"]["ui_schema"]["postprocess_data_required"],
            ["ui_extensions.status_panels.weather"],
        )
        self.assertEqual(
            assets_intent["payload"]["postprocess_contract"]["ui_extensions"]["status_panels"]["weather"]["type"],
            "object",
        )
        completed = self.intents.list_intents(self.run_dir, "completed")
        analyze = [item for item in completed if item["id"] == created["id"]][0]
        self.assertEqual(analyze["result"]["outputs"]["routing_requests"]["processed_count"], 1)
        self.assertEqual(
            analyze["result"]["outputs"]["capability_requests"],
            analyze["result"]["outputs"]["routing_requests"],
        )

    def test_analyze_input_card_data_edit_requires_authorization(self):
        request = {
            "id": "route-card",
            "type": "card_data_edit",
            "source_channel": "user_instruction",
            "summary": "Change Ada's title.",
            "target": "character:Ada",
            "payload": {"field": "title", "value": "Captain"},
            "requires_authorization": False,
            "authorization_gate": "none",
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "Ada is Captain"},
        }
        self._stub_apply_result_with_routing_requests([request])
        self.intents.create_intent(
            self.run_dir,
            {"requested_by": "system", "type": "analyze_input", "payload": {"analysis_path": "input_analysis.output.json"}},
        )

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, Path(self.tmp.name))

        self.assertTrue(result["ok"])
        pending_types = [item["type"] for item in self.intents.list_intents(self.run_dir, "pending")]
        self.assertNotIn("card_data_edit", pending_types)
        artifacts_dir = self.run_dir / "artifacts" / "capability_requests"
        artifacts = list(artifacts_dir.glob("*.json"))
        self.assertEqual(len(artifacts), 1)
        artifact = json.loads(artifacts[0].read_text(encoding="utf-8"))
        self.assertEqual(artifact["status"], "authorization_required")

    def test_analyze_input_story_retcon_consult_is_deferred_message(self):
        request = {
            "id": "route-retcon",
            "type": "story_retcon_consult",
            "source_channel": "user_instruction",
            "summary": "Discuss whether to replay the previous scene.",
            "target": "gm_story",
            "payload": {"scope": "previous_scene", "preferred_action": "consult"},
            "requires_authorization": False,
            "authorization_gate": "none",
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "replay previous scene"},
        }
        self._stub_apply_result_with_routing_requests([request])
        self.intents.create_intent(
            self.run_dir,
            {"requested_by": "system", "type": "analyze_input", "payload": {"analysis_path": "input_analysis.output.json"}},
        )

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, Path(self.tmp.name))

        self.assertTrue(result["ok"])
        artifacts_dir = self.run_dir / "artifacts" / "capability_requests"
        artifacts = list(artifacts_dir.glob("*.json"))
        self.assertEqual(len(artifacts), 1)
        artifact = json.loads(artifacts[0].read_text(encoding="utf-8"))
        self.assertEqual(artifact["status"], "deferred")
        messages = self.dispatcher.agent_messages.read_messages(self.run_dir)
        routing_messages = [item for item in messages if item.get("type") == "capability_request"]
        self.assertTrue(any("gm" in item.get("to", []) for item in routing_messages))

    def test_analyze_input_source_request_without_source_gate_is_authorization_required(self):
        request = {
            "id": "route-source",
            "type": "source_feature_request",
            "source_channel": "user_instruction",
            "summary": "Add export button.",
            "target": "main_agent",
            "payload": {"feature": "save_export"},
            "requires_authorization": True,
            "authorization_gate": "allowSourceCodeSelfRepair",
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "add export"},
        }
        self._stub_apply_result_with_routing_requests(
            [request],
            settings={"selfRepairMode": "full", "allowSourceCodeSelfRepair": False},
        )
        self.intents.create_intent(
            self.run_dir,
            {"requested_by": "system", "type": "analyze_input", "payload": {"analysis_path": "input_analysis.output.json"}},
        )

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, Path(self.tmp.name))

        self.assertTrue(result["ok"])
        pending_types = [item["type"] for item in self.intents.list_intents(self.run_dir, "pending")]
        self.assertNotIn("system_request", pending_types)
        completed = self.intents.list_intents(self.run_dir, "completed")
        routing_result = completed[0]["result"]["outputs"]["routing_requests"]
        artifact = json.loads((self.run_dir / routing_result["results"][0]["artifact"]).read_text(encoding="utf-8"))
        self.assertEqual(artifact["status"], "authorization_required")

    def test_analyze_input_source_request_with_source_gate_ignores_self_repair_mode(self):
        request = {
            "id": "route-source",
            "type": "source_feature_request",
            "source_channel": "user_instruction",
            "summary": "Add export button.",
            "target": "main_agent",
            "payload": {"feature": "save_export"},
            "requires_authorization": True,
            "authorization_gate": "allowSourceCodeSelfRepair",
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "add export"},
        }
        self._stub_apply_result_with_routing_requests(
            [request],
            settings={"selfRepairMode": "off", "allowSourceCodeSelfRepair": True},
        )
        self.intents.create_intent(
            self.run_dir,
            {"requested_by": "system", "type": "analyze_input", "payload": {"analysis_path": "input_analysis.output.json"}},
        )

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, Path(self.tmp.name))

        self.assertTrue(result["ok"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        system_requests = [item for item in pending if item["type"] == "system_request"]
        self.assertEqual(len(system_requests), 1)
        self.assertFalse(system_requests[0]["payload"]["selfRepairMode_required"])

    def test_analyze_input_source_request_uses_manifest_settings_when_applied_manifest_lacks_settings(self):
        request = {
            "id": "route-source",
            "type": "source_feature_request",
            "source_channel": "user_instruction",
            "summary": "Add export button.",
            "target": "main_agent",
            "payload": {"feature": "save_export"},
            "requires_authorization": True,
            "authorization_gate": "allowSourceCodeSelfRepair",
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "add export"},
        }
        capability_requests = self._legacy_to_capability_requests([request])
        _write_json(
            self.run_dir / "manifest.json",
            {
                "round_id": "round-000001",
                "stage": "prepared",
                "runtime_settings": {"selfRepairMode": "off", "allowSourceCodeSelfRepair": True},
            },
        )

        def fake_apply(card_folder, root_dir):
            _write_json(
                self.run_dir / "input_analysis.output.json",
                {
                    "schema_version": 1,
                    "routing_requests": [request],
                    "capability_requests": capability_requests,
                },
            )
            return {
                "ok": True,
                "stage": "analysis_applied",
                "run_dir": str(self.run_dir),
                "root_dir": str(root_dir),
                "manifest": {"round_id": "round-000001", "stage": "analysis_applied"},
                "routing_requests": [request],
                "capability_requests": capability_requests,
            }

        self.dispatcher.input_analysis_apply.apply_current_run = fake_apply
        self.intents.create_intent(
            self.run_dir,
            {"requested_by": "system", "type": "analyze_input", "payload": {"analysis_path": "input_analysis.output.json"}},
        )

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, Path(self.tmp.name))

        self.assertTrue(result["ok"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        system_requests = [item for item in pending if item["type"] == "system_request"]
        self.assertEqual(len(system_requests), 1)

    def test_request_projection_creates_projected_message_and_run_actor_intent(self):
        request = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["projection"],
                "type": "request_actor",
                "visibility": "gm_only",
                "source_call_id": "call-character-Ada-1",
                "payload": {
                    "actor_id": "character:Ada",
                    "call": {
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "Listen at the door.",
                    },
                    "packet": {
                        "actor_id": "character:Ada",
                        "visible_context": {"scene": "hall"},
                    },
                },
            },
        )["message"]
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "request_projection",
                "payload": {
                    "actor_id": "character:Ada",
                    "source_message_id": request["id"],
                    "source_call_id": "call-character-Ada-1",
                },
            },
        )["intent"]

        self._install_projection_stub()

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["intent_type"], "request_projection")
        completed = self.intents.list_intents(self.run_dir, "completed")
        self.assertEqual([item["id"] for item in completed], [created["id"]])
        inbox = self.dispatcher.agent_messages.read_inbox(self.run_dir, "character:Ada")
        self.assertEqual([message["type"] for message in inbox], ["projected_message"])
        projected = inbox[0]
        self.assertEqual(result["created_messages"], [projected["id"]])
        self.assertEqual(projected["from"], "projection")
        self.assertEqual(projected["source_call_id"], "call-character-Ada-1")
        self.assertEqual(projected["payload"]["actor_id"], "character:Ada")
        self.assertEqual(projected["payload"]["packet"]["visible_context"], {"scene": "hall"})
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([intent["type"] for intent in pending], ["run_actor"])
        self.assertEqual(result["created_intents"], [pending[0]["id"]])
        self.assertEqual(
            pending[0]["payload"],
            {
                "actor_id": "character:Ada",
                "projected_message_id": projected["id"],
                "source_call_id": "call-character-Ada-1",
            },
        )
        self.assertEqual(pending[0]["source_message_id"], projected["id"])
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(completed[0]["result"]["outputs"]["intent_type"], "request_projection")
        self.assertEqual(completed[0]["result"]["outputs"]["created_messages"], [projected["id"]])
        self.assertEqual(completed[0]["result"]["outputs"]["created_intents"], [pending[0]["id"]])

    def test_request_projection_dispatches_projection_agent_and_uses_edited_message(self):
        request = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["projection"],
                "type": "request_actor",
                "visibility": "gm_only",
                "source_call_id": "call-character-Bob-1",
                "payload": {
                    "actor_id": "character:Bob",
                    "call": {
                        "call_id": "call-character-Bob-1",
                        "actor_id": "character:Bob",
                        "prompt": "You see a vampire at Alice's door.",
                    },
                    "packet": {
                        "actor_id": "character:Bob",
                        "immersive_context": "Bob only knows black-robed strangers.",
                        "visible_events": [],
                        "call": {
                            "call_id": "call-character-Bob-1",
                            "actor_id": "character:Bob",
                            "prompt": "You see a vampire at Alice's door.",
                        },
                    },
                },
            },
        )["message"]
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "request_projection",
                "payload": {
                    "actor_id": "character:Bob",
                    "source_message_id": request["id"],
                    "source_call_id": "call-character-Bob-1",
                },
            },
        )["intent"]
        dispatch_calls = []

        def fake_dispatch(agent_key, run_dir, root_dir, run_claude, extra_context):
            dispatch_calls.append((agent_key, extra_context))
            self.assertEqual(agent_key, "projection")
            review_packet = extra_context["projection_packet"]
            self.assertEqual(review_packet["target_actor_id"], "character:Bob")
            self.assertEqual(review_packet["source_call_id"], "call-character-Bob-1")
            self.assertEqual(review_packet["source_message_id"], request["id"])
            self.assertEqual(review_packet["requested_actor_message"], "You see a vampire at Alice's door.")
            self.assertEqual(review_packet["actor_context"], "Bob only knows black-robed strangers.")
            return {
                "decision": "edited",
                "target_actor_id": "character:Bob",
                "source_call_id": "call-character-Bob-1",
                "final_actor_message": "You see a black-robed figure at Alice's door.",
                "feedback": "Removed objective vampire knowledge.",
            }

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertTrue(result["ok"])
        self.assertEqual([call[0] for call in dispatch_calls], ["projection"])
        inbox = self.dispatcher.agent_messages.read_inbox(self.run_dir, "character:Bob")
        self.assertEqual([message["type"] for message in inbox], ["projected_message"])
        projected = inbox[0]
        self.assertEqual(projected["payload"]["packet"]["gm_prompt"], "You see a black-robed figure at Alice's door.")
        self.assertEqual(projected["payload"]["gm_prompt"], "You see a black-robed figure at Alice's door.")
        projected_prompt = projected["payload"]["packet"]["gm_prompt"]
        self.assertNotIn("vampire", projected_prompt.lower())
        self.assertIn("black-robed figure", projected_prompt)
        nested_prompt = projected["payload"]["packet"]["call"]["prompt"]
        self.assertNotIn("vampire", nested_prompt.lower())
        self.assertIn("black-robed figure", nested_prompt)
        self.assertEqual(projected["payload"]["projection"]["decision"], "edited")
        self.assertTrue((self.run_dir / "artifacts" / "projections" / f"{created['id']}.json").exists())
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([intent["type"] for intent in pending], ["run_actor"])
        self.assertEqual(result["created_intents"], [pending[0]["id"]])

    def test_request_projection_edited_message_rewrites_nested_packet_call_prompt(self):
        request = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["projection"],
                "type": "request_actor",
                "visibility": "gm_only",
                "source_call_id": "call-character-Bob-1",
                "payload": {
                    "actor_id": "character:Bob",
                    "call": {
                        "call_id": "call-character-Bob-1",
                        "actor_id": "character:Bob",
                        "prompt": "SECRET old prompt",
                    },
                    "packet": {
                        "actor_id": "character:Bob",
                        "immersive_context": "Bob only sees safe visible details.",
                        "call": {
                            "call_id": "call-character-Bob-1",
                            "actor_id": "character:Bob",
                            "prompt": "SECRET old prompt",
                        },
                    },
                },
            },
        )["message"]
        self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "request_projection",
                "payload": {
                    "actor_id": "character:Bob",
                    "source_message_id": request["id"],
                    "source_call_id": "call-character-Bob-1",
                },
            },
        )

        def fake_dispatch(agent_key, run_dir, root_dir, run_claude, extra_context):
            self.assertEqual(agent_key, "projection")
            return {
                "decision": "edited",
                "target_actor_id": "character:Bob",
                "source_call_id": "call-character-Bob-1",
                "final_actor_message": "SAFE edited prompt",
                "feedback": "Removed unsafe prompt.",
            }

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertTrue(result["ok"])
        projected = self.dispatcher.agent_messages.read_inbox(self.run_dir, "character:Bob")[0]
        packet = projected["payload"]["packet"]
        packet_text = json.dumps(packet, ensure_ascii=False)
        prompt_text = self.dispatcher.agent_prompts.character_prompt_text(packet)
        self.assertIn("SAFE edited prompt", packet_text)
        self.assertIn("SAFE edited prompt", prompt_text)
        self.assertNotIn("SECRET old prompt", packet_text)
        self.assertNotIn("SECRET old prompt", prompt_text)

    def test_request_projection_needs_rewrite_creates_gm_follow_up_without_actor_dispatch(self):
        request = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["projection"],
                "type": "request_actor",
                "visibility": "gm_only",
                "source_call_id": "call-character-Bob-1",
                "payload": {
                    "actor_id": "character:Bob",
                    "call": {
                        "call_id": "call-character-Bob-1",
                        "actor_id": "character:Bob",
                        "prompt": "You see a vampire at Alice's door.",
                    },
                    "packet": {"actor_id": "character:Bob", "immersive_context": "Bob knows only rumors."},
                },
            },
        )["message"]
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "request_projection",
                "payload": {
                    "actor_id": "character:Bob",
                    "source_message_id": request["id"],
                    "source_call_id": "call-character-Bob-1",
                },
            },
        )["intent"]

        def fake_dispatch(agent_key, run_dir, root_dir, run_claude, extra_context):
            return {
                "decision": "needs_rewrite",
                "target_actor_id": "character:Bob",
                "source_call_id": "call-character-Bob-1",
                "final_actor_message": "",
                "feedback": "Rewrite without naming the hidden vampire.",
            }

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertTrue(result["ok"])
        self.assertEqual(self.dispatcher.agent_messages.read_inbox(self.run_dir, "character:Bob"), [])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([intent["type"] for intent in pending], ["run_gm_turn"])
        self.assertEqual(pending[0]["payload"]["reason"], "projection_needs_rewrite")
        self.assertEqual(pending[0]["payload"]["projection_feedback"], "Rewrite without naming the hidden vampire.")
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(result["created_messages"], [])
        self.assertEqual(result["created_intents"], [pending[0]["id"]])

    def test_request_projection_blocks_when_projection_agent_blocks(self):
        request = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["projection"],
                "type": "request_actor",
                "visibility": "gm_only",
                "source_call_id": "call-character-Bob-1",
                "payload": {
                    "actor_id": "character:Bob",
                    "call": {
                        "call_id": "call-character-Bob-1",
                        "actor_id": "character:Bob",
                        "prompt": "You see a vampire at Alice's door.",
                    },
                },
            },
        )["message"]
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "request_projection",
                "payload": {
                    "actor_id": "character:Bob",
                    "source_message_id": request["id"],
                    "source_call_id": "call-character-Bob-1",
                },
            },
        )["intent"]

        def fake_dispatch(agent_key, run_dir, root_dir, run_claude, extra_context):
            return {
                "decision": "blocked",
                "target_actor_id": "character:Bob",
                "source_call_id": "call-character-Bob-1",
                "final_actor_message": "",
                "feedback": "Projection request contains unrecoverable hidden facts.",
            }

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "projection_agent_blocked")
        self.assertEqual(self.dispatcher.agent_messages.read_inbox(self.run_dir, "character:Bob"), [])
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])

    def test_request_projection_reuses_existing_projected_message_on_retry(self):
        request = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["projection"],
                "type": "request_actor",
                "visibility": "gm_only",
                "source_call_id": "call-character-Ada-1",
                "payload": {
                    "actor_id": "character:Ada",
                    "call": {
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "Listen at the door.",
                    },
                    "packet": {
                        "actor_id": "character:Ada",
                        "visible_context": {"scene": "hall"},
                    },
                },
            },
        )["message"]
        existing = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "projection",
                "to": ["character:Ada"],
                "type": "projected_message",
                "visibility": "actor_facing",
                "source_call_id": "call-character-Ada-1",
                "payload": {
                    "actor_id": "character:Ada",
                    "source_message_id": request["id"],
                    "packet": {
                        "actor_id": "character:Ada",
                        "visible_context": {"scene": "hall"},
                    },
                    "gm_prompt": "Listen at the door.",
                },
            },
        )["message"]
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "request_projection",
                "payload": {
                    "actor_id": "character:Ada",
                    "source_message_id": request["id"],
                    "source_call_id": "call-character-Ada-1",
                },
            },
        )["intent"]

        self.dispatcher._dispatch_agent_payload = lambda *_args, **_kwargs: self.fail(
            "projection agent should not run when projected message already exists"
        )

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["created_messages"], [])
        self.assertEqual(result["detail"]["projected_message_id"], existing["id"])
        messages = self.dispatcher.agent_messages.read_messages(self.run_dir)
        projected_messages = [message for message in messages if message.get("type") == "projected_message"]
        self.assertEqual([message["id"] for message in projected_messages], [existing["id"]])
        inbox = self.dispatcher.agent_messages.read_inbox(self.run_dir, "character:Ada")
        self.assertEqual([message["id"] for message in inbox], [existing["id"]])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([intent["type"] for intent in pending], ["run_actor"])
        self.assertEqual(pending[0]["source_message_id"], existing["id"])
        self.assertEqual(pending[0]["payload"]["projected_message_id"], existing["id"])

    def test_run_actor_from_projected_message_writes_response_artifact_and_run_gm_turn(self):
        packet = {"actor_id": "character:Ada", "visible_context": {"scene": "hall"}}
        projected = self._append_projected_actor_message(packet=packet)
        created = self._create_run_actor_intent(projected["id"])
        actor_output = self._actor_resolution_output()
        dispatch_calls = []

        def fake_dispatch(agent_key, run_dir, root_dir, run_claude, extra_context):
            dispatch_calls.append((agent_key, run_dir, root_dir, extra_context))
            return actor_output

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertTrue(result["ok"])
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["intent_type"], "run_actor")
        self.assertEqual([call[0] for call in dispatch_calls], ["character:Ada"])
        self.assertEqual(dispatch_calls[0][3]["actor_packet"], packet)
        self.assertEqual(dispatch_calls[0][3]["projected_message_id"], projected["id"])
        messages = self.dispatcher.agent_messages.read_messages(self.run_dir)
        actor_responses = [message for message in messages if message.get("type") == "actor_response"]
        self.assertEqual(len(actor_responses), 1)
        self.assertEqual(actor_responses[0]["payload"]["output"], actor_output)
        root_payload = json.loads((self.run_dir / "actor.outputs.json").read_text(encoding="utf-8"))
        artifact_payload = json.loads((self.run_dir / "artifacts" / "actor.outputs.json").read_text(encoding="utf-8"))
        self.assertEqual(root_payload, {"character:Ada": [actor_output]})
        self.assertEqual(artifact_payload, root_payload)
        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        actor_events = [
            event
            for event in trace["events"]
            if event.get("actor") == "character:Ada"
        ]
        self.assertEqual(len(actor_events), 1)
        self.assertEqual(actor_events[0]["type"], "custom_action")
        self.assertEqual(actor_events[0]["source_call_id"], "call-character-Ada-1")
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "Ada is close enough to respond."}],
                        "events": [],
                        "actor_calls": [
                            {
                                "call_id": "call-character-Ada-1",
                                "actor_id": "character:Ada",
                                "prompt": "React to the archive door.",
                                "reason": "Ada is present in the scene.",
                                "visibility_basis": {
                                    "mode": "direct",
                                    "summary": "character:Ada is directly addressed by this test GM prompt.",
                                    "target_actor": "character:Ada",
                                    "visible_to": ["character:Ada"],
                                },
                            }
                        ],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "complete",
                    }
                ],
            },
        )
        story_input = self.dispatcher.agent_outputs.build_story_input(self.run_dir)
        self.assertEqual(len(story_input["loop_outputs"]["actors"]["character:Ada"]), 1)
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([intent["type"] for intent in pending], ["run_gm_turn"])
        self.assertEqual(result["created_intents"], [pending[0]["id"]])
        completed = self.intents.list_intents(self.run_dir, "completed")
        self.assertEqual([item["id"] for item in completed], [created["id"]])

    def test_run_actor_dialogue_continue_creates_run_gm_turn_without_resolution_requirement(self):
        packet = {"actor_id": "character:Ada", "visible_context": {"scene": "hall"}}
        projected = self._append_projected_actor_message(packet=packet)
        created = self._create_run_actor_intent(projected["id"])
        actor_output = self._actor_dialogue_continue_output()
        self.dispatcher._dispatch_agent_payload = lambda *_args, **_kwargs: actor_output

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertTrue(result["ok"])
        self.assertEqual(result["intent_id"], created["id"])
        self.assertFalse(result["detail"]["requires_gm_resolution"])
        self.assertFalse(result["detail"]["player_decision_required"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([intent["type"] for intent in pending], ["run_gm_turn"])
        self.assertEqual(result["created_intents"], [pending[0]["id"]])
        self.assertEqual(pending[0]["payload"]["reason"], "actor_response_continue")
        self.assertEqual(pending[0]["payload"]["actor_id"], "character:Ada")
        self.assertEqual(pending[0]["payload"]["actor_response_message_id"], result["created_messages"][0])

    def test_run_actor_waits_for_all_gm_fanout_siblings_before_continuing_gm(self):
        self._install_dispatcher_dependencies()
        self._write_multi_actor_input()
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "input_analyst", "type": "run_gm_turn", "payload": {}},
        )["intent"]
        actor_calls = [
            self._gm_actor_call(actor_id="character:Ada", call_id="call-character-Ada-1"),
            self._gm_actor_call(actor_id="character:Bea", call_id="call-character-Bea-1"),
        ]

        def fake_dispatch(agent_key, _run_dir, _root, _run_claude, _extra_context):
            if agent_key == "gm":
                return self._gm_output(actor_calls=actor_calls, stop_reason="continue")
            if agent_key == "projection":
                packet = _extra_context["projection_packet"]
                return {
                    "decision": "pass",
                    "target_actor_id": packet["target_actor_id"],
                    "source_call_id": packet["source_call_id"],
                    "final_actor_message": packet["requested_actor_message"],
                    "feedback": "",
                }
            return self._actor_resolution_output(agent_key)

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        gm_result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")
        self.assertTrue(gm_result["ok"])
        self.assertEqual(gm_result["intent_id"], created["id"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([intent["type"] for intent in pending], ["request_projection", "request_projection"])

        first_projection = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")
        second_projection = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")
        self.assertTrue(first_projection["ok"])
        self.assertTrue(second_projection["ok"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([intent["type"] for intent in pending], ["run_actor", "run_actor"])

        first_actor = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")
        self.assertTrue(first_actor["ok"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([intent["type"] for intent in pending], ["run_actor"])
        self.assertEqual(first_actor["created_intents"], [])

        second_actor = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")
        self.assertTrue(second_actor["ok"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([intent["type"] for intent in pending], ["run_gm_turn"])
        self.assertEqual(second_actor["created_intents"], [pending[0]["id"]])
        self.assertEqual(pending[0]["payload"]["reason"], "actor_fanout_complete")
        self.assertEqual(
            pending[0]["payload"]["expected_source_call_ids"],
            ["call-character-Ada-1", "call-character-Bea-1"],
        )
        self.assertEqual(pending[0]["policy"]["source_intent_id"], created["id"])

    def test_run_actor_fanout_decision_first_blocks_later_gm_continuation(self):
        self._run_two_actor_fanout_until_actors_pending()
        self._actor_outputs_by_agent = {
            "character:Ada": self._actor_player_decision_output("character:Ada"),
            "character:Bea": self._actor_resolution_output("character:Bea"),
        }

        decision_actor = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")
        self.assertTrue(decision_actor["ok"])
        self.assertTrue(decision_actor["detail"]["player_decision_required"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([intent["type"] for intent in pending], ["run_actor"])

        later_actor = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")
        self.assertTrue(later_actor["ok"])
        self.assertEqual(later_actor["created_intents"], [])
        self.assertTrue(later_actor["detail"]["fanout_player_decision_required"])
        self.assertTrue(later_actor["detail"]["fanout_continuation_blocked_by_player_decision"])
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        self.assertEqual(trace["status"], "decision_point")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertNotEqual(manifest.get("stage"), "blocked")
        completed_actor_outputs = [
            item["result"]["outputs"]
            for item in self.intents.list_intents(self.run_dir, "completed")
            if item.get("type") == "run_actor"
        ]
        self.assertEqual([output["player_decision_required"] for output in completed_actor_outputs], [True, False])

    def test_run_actor_fanout_decision_last_does_not_create_gm_continuation(self):
        self._run_two_actor_fanout_until_actors_pending()
        self._actor_outputs_by_agent = {
            "character:Ada": self._actor_resolution_output("character:Ada"),
            "character:Bea": self._actor_player_decision_output("character:Bea"),
        }

        ordinary_actor = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")
        self.assertTrue(ordinary_actor["ok"])
        self.assertFalse(ordinary_actor["detail"]["player_decision_required"])
        self.assertEqual(ordinary_actor["created_intents"], [])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([intent["type"] for intent in pending], ["run_actor"])

        decision_actor = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")
        self.assertTrue(decision_actor["ok"])
        self.assertTrue(decision_actor["detail"]["player_decision_required"])
        self.assertEqual(decision_actor["created_intents"], [])
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        self.assertEqual(trace["status"], "decision_point")
        completed_actor_outputs = [
            item["result"]["outputs"]
            for item in self.intents.list_intents(self.run_dir, "completed")
            if item.get("type") == "run_actor"
        ]
        self.assertEqual([output["player_decision_required"] for output in completed_actor_outputs], [False, True])

    def test_run_actor_recovers_when_complete_raises_after_side_effects(self):
        packet = {"actor_id": "character:Ada", "visible_context": {"scene": "hall"}}
        projected = self._append_projected_actor_message(packet=packet)
        created = self._create_run_actor_intent(projected["id"])
        actor_output = self._actor_resolution_output()
        original_complete = self.dispatcher.agent_intents.complete_intent

        def fake_dispatch(agent_key, run_dir, root_dir, run_claude, extra_context):
            return actor_output

        def complete_then_raise(run_dir, intent_id, outputs=None):
            original_complete(run_dir, intent_id, outputs=outputs)
            raise RuntimeError("raw complete secret")

        self.dispatcher._dispatch_agent_payload = fake_dispatch
        self.dispatcher.agent_intents.complete_intent = complete_then_raise
        self.addCleanup(setattr, self.dispatcher.agent_intents, "complete_intent", original_complete)

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "run_actor_complete_recovered")
        self.assertEqual(result["detail"]["transition_failure_recovered"], "run_actor_complete_failed")
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("raw complete secret", serialized)
        self.assertNotIn("RuntimeError: raw complete secret", serialized)
        self.assertEqual(self.intents.list_intents(self.run_dir, "blocked"), [])
        completed = self.intents.list_intents(self.run_dir, "completed")
        self.assertEqual([item["id"] for item in completed], [created["id"]])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([intent["type"] for intent in pending], ["run_gm_turn"])
        self.assertEqual(result["created_intents"], [pending[0]["id"]])
        self.assertTrue((self.run_dir / "artifacts" / "actor.outputs.json").exists())
        self.assertTrue((self.run_dir / "interaction.trace.json").exists())
        messages = self.dispatcher.agent_messages.read_messages(self.run_dir)
        actor_responses = [message for message in messages if message.get("type") == "actor_response"]
        self.assertEqual([message["id"] for message in actor_responses], result["created_messages"])
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertNotEqual(manifest.get("stage"), "blocked")

    def test_run_actor_blocks_when_complete_returns_failure(self):
        packet = {"actor_id": "character:Ada", "visible_context": {"scene": "hall"}}
        projected = self._append_projected_actor_message(packet=packet)
        created = self._create_run_actor_intent(projected["id"])
        actor_output = self._actor_resolution_output()
        original_complete = self.dispatcher.agent_intents.complete_intent

        self.dispatcher._dispatch_agent_payload = lambda *_args, **_kwargs: actor_output
        self.dispatcher.agent_intents.complete_intent = (
            lambda _run_dir, _intent_id, outputs=None: {"ok": False, "reason": "fixture_complete_failed"}
        )
        self.addCleanup(setattr, self.dispatcher.agent_intents, "complete_intent", original_complete)

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "run_actor_complete_failed")
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertEqual(blocked[0]["result"]["outputs"]["reason"], "run_actor_complete_failed")
        self.assertEqual(blocked[0]["result"]["outputs"]["transition_reason"], "fixture_complete_failed")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "blocked")
        self.assertEqual(manifest["dispatcher"]["reason"], "run_actor_complete_failed")

    def test_run_actor_player_decision_recovers_when_complete_raises_after_side_effects_without_follow_up(self):
        packet = {"actor_id": "character:Ada", "visible_context": {"scene": "door"}}
        projected = self._append_projected_actor_message(packet=packet)
        created = self._create_run_actor_intent(projected["id"])
        actor_output = {
            "agent": "character",
            "agent_id": "character:Ada",
            "character_name": "Ada",
            "events": [
                {
                    "type": "dialogue",
                    "target": "player",
                    "content": "Choose before I open it.",
                    "metadata": {
                        "exact_visible_words": "Choose before I open it.",
                        "delivery_channel": "spoken",
                        "visible_tone_or_action": "quiet warning",
                    },
                }
            ],
            "stop_reason": "stop_for_player_decision",
        }
        original_complete = self.dispatcher.agent_intents.complete_intent

        def complete_then_raise(run_dir, intent_id, outputs=None):
            original_complete(run_dir, intent_id, outputs=outputs)
            raise RuntimeError("raw complete secret")

        self.dispatcher._dispatch_agent_payload = lambda *_args, **_kwargs: actor_output
        self.dispatcher.agent_intents.complete_intent = complete_then_raise
        self.addCleanup(setattr, self.dispatcher.agent_intents, "complete_intent", original_complete)

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "run_actor_complete_recovered")
        self.assertTrue(result["detail"]["player_decision_required"])
        self.assertEqual(result["detail"]["follow_up_intent_id"], "")
        self.assertEqual(result["created_intents"], [])
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        self.assertEqual(self.intents.list_intents(self.run_dir, "blocked"), [])
        completed = self.intents.list_intents(self.run_dir, "completed")
        self.assertEqual([item["id"] for item in completed], [created["id"]])
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertNotEqual(manifest.get("stage"), "blocked")

    def test_run_actor_blocks_when_projected_message_missing(self):
        created = self._create_run_actor_intent("msg_999999")

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "projected_message_missing")
        self.assertEqual(result["intent_id"], created["id"])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual(blocked[0]["result"]["outputs"]["reason"], "projected_message_missing")

    def test_run_actor_blocks_when_payload_source_call_id_missing(self):
        projected = self._append_projected_actor_message()
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "projection",
                "type": "run_actor",
                "source_message_id": projected["id"],
                "payload": {
                    "actor_id": "character:Ada",
                    "projected_message_id": projected["id"],
                },
            },
        )["intent"]
        self.dispatcher._dispatch_agent_payload = lambda *_args, **_kwargs: self.fail("invalid payload was dispatched")

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "run_actor_payload_invalid")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertFalse((self.run_dir / "actor.outputs.json").exists())

    def test_run_actor_blocks_when_payload_projected_message_id_missing_despite_top_level_source(self):
        projected = self._append_projected_actor_message()
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "projection",
                "type": "run_actor",
                "source_message_id": projected["id"],
                "payload": {
                    "actor_id": "character:Ada",
                    "source_call_id": "call-character-Ada-1",
                },
            },
        )["intent"]
        self.dispatcher._dispatch_agent_payload = lambda *_args, **_kwargs: self.fail("invalid payload was dispatched")

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "run_actor_payload_invalid")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertFalse((self.run_dir / "actor.outputs.json").exists())

    def test_run_actor_blocks_when_payload_actor_id_missing(self):
        projected = self._append_projected_actor_message()
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "projection",
                "type": "run_actor",
                "source_message_id": projected["id"],
                "payload": {
                    "projected_message_id": projected["id"],
                    "source_call_id": "call-character-Ada-1",
                },
            },
        )["intent"]
        self.dispatcher._dispatch_agent_payload = lambda *_args, **_kwargs: self.fail("invalid payload was dispatched")

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "run_actor_payload_invalid")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertFalse((self.run_dir / "actor.outputs.json").exists())

    def test_run_actor_blocks_on_projected_actor_mismatch(self):
        projected = self._append_projected_actor_message(actor_id="character:Bea")
        self._create_run_actor_intent(projected["id"], actor_id="character:Ada")

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "projected_message_actor_mismatch")
        self.assertFalse((self.run_dir / "actor.outputs.json").exists())

    def test_run_actor_blocks_invalid_actor_schema_as_dispatch_failed(self):
        projected = self._append_projected_actor_message()
        self._create_run_actor_intent(projected["id"])
        self.dispatcher._dispatch_agent_payload = lambda *_args, **_kwargs: {
            "agent": "character",
            "agent_id": "character:Ada",
            "character_name": "Ada",
            "events": [],
            "stop_reason": "continue",
        }

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "actor_dispatch_failed")
        self.assertFalse((self.run_dir / "actor.outputs.json").exists())

    def test_run_actor_player_high_risk_stops_without_story_or_delivery_follow_up(self):
        packet = {"actor_id": "player", "visible_context": {"scene": "door"}}
        projected = self._append_projected_actor_message(
            actor_id="player",
            source_call_id="call-player-1",
            packet=packet,
        )
        self._create_run_actor_intent(projected["id"], actor_id="player", source_call_id="call-player-1")
        actor_output = {
            "agent": "player",
            "agent_id": "player",
            "events": [
                {
                    "type": "custom_action",
                    "target": "unstable bridge",
                    "content": "I jump onto the unstable bridge.",
                    "metadata": {
                        "category": "movement",
                        "visible_content": "I jump onto the unstable bridge.",
                        "requires_gm_resolution": True,
                        "risk_level": "high",
                    },
                }
            ],
            "stop_reason": "continue",
        }
        self.dispatcher._dispatch_agent_payload = lambda *_args, **_kwargs: actor_output

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertTrue(result["ok"])
        self.assertTrue(result["detail"]["player_decision_required"])
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        self.assertEqual(result["created_intents"], [])
        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        self.assertEqual(trace["status"], "decision_point")

    def test_run_actor_stop_for_player_decision_records_decision_point_without_follow_up(self):
        packet = {"actor_id": "character:Ada", "visible_context": {"scene": "door"}}
        projected = self._append_projected_actor_message(packet=packet)
        self._create_run_actor_intent(projected["id"])
        actor_output = {
            "agent": "character",
            "agent_id": "character:Ada",
            "character_name": "Ada",
            "events": [
                {
                    "type": "dialogue",
                    "target": "player",
                    "content": "Choose before I open it.",
                    "metadata": {
                        "exact_visible_words": "Choose before I open it.",
                        "delivery_channel": "spoken",
                        "visible_tone_or_action": "quiet warning",
                    },
                }
            ],
            "stop_reason": "stop_for_player_decision",
        }
        self.dispatcher._dispatch_agent_payload = lambda *_args, **_kwargs: actor_output

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertTrue(result["ok"])
        self.assertTrue(result["detail"]["player_decision_required"])
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        self.assertEqual(result["created_intents"], [])
        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        self.assertEqual(trace["status"], "decision_point")

    def test_run_actor_cannot_use_unprojected_raw_call_payload(self):
        raw_request = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["projection"],
                "type": "request_actor",
                "visibility": "gm_only",
                "source_call_id": "call-character-Ada-1",
                "payload": {
                    "actor_id": "character:Ada",
                    "call": {
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "Use this raw prompt.",
                    },
                    "packet": {"actor_id": "character:Ada", "visible_context": {"unsafe": True}},
                },
            },
        )["message"]
        self._create_run_actor_intent(
            raw_request["id"],
            extra_payload={"packet": {"actor_id": "character:Ada", "visible_context": {"unsafe": True}}},
        )
        self.dispatcher._dispatch_agent_payload = lambda *_args, **_kwargs: self.fail("raw request payload was dispatched")

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "projected_message_missing")
        self.assertFalse((self.run_dir / "actor.outputs.json").exists())

    def test_request_projection_blocks_structured_when_accept_fails(self):
        request = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["projection"],
                "type": "request_actor",
                "visibility": "gm_only",
                "source_call_id": "call-character-Ada-1",
                "payload": {
                    "actor_id": "character:Ada",
                    "call": {
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "Listen at the door.",
                    },
                },
            },
        )["message"]
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "request_projection",
                "payload": {
                    "actor_id": "character:Ada",
                    "source_message_id": request["id"],
                    "source_call_id": "call-character-Ada-1",
                },
            },
        )["intent"]
        original_accept = self.dispatcher.agent_intents.accept_intent

        self.dispatcher.agent_intents.accept_intent = (
            lambda _run_dir, _intent_id, outputs=None: {"ok": False, "reason": "fixture_accept_failed"}
        )
        self.addCleanup(setattr, self.dispatcher.agent_intents, "accept_intent", original_accept)

        self._install_projection_stub()

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "request_projection_accept_failed")
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertEqual(blocked[0]["result"]["outputs"]["reason"], "request_projection_accept_failed")
        self.assertEqual(blocked[0]["result"]["outputs"]["transition_reason"], "fixture_accept_failed")
        self.assertEqual(self.dispatcher.agent_messages.read_inbox(self.run_dir, "character:Ada"), [])

    def test_request_projection_blocks_structured_when_accept_raises(self):
        request = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["projection"],
                "type": "request_actor",
                "visibility": "gm_only",
                "source_call_id": "call-character-Ada-1",
                "payload": {
                    "actor_id": "character:Ada",
                    "call": {
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "Listen at the door.",
                    },
                },
            },
        )["message"]
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "request_projection",
                "payload": {
                    "actor_id": "character:Ada",
                    "source_message_id": request["id"],
                    "source_call_id": "call-character-Ada-1",
                },
            },
        )["intent"]
        original_accept = self.dispatcher.agent_intents.accept_intent

        def raise_accept(_run_dir, _intent_id, outputs=None):
            raise RuntimeError("raw accept secret")

        self.dispatcher.agent_intents.accept_intent = raise_accept
        self.addCleanup(setattr, self.dispatcher.agent_intents, "accept_intent", original_accept)

        self._install_projection_stub()

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "request_projection_accept_failed")
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("raw accept secret", serialized)
        self.assertNotIn("RuntimeError: raw accept secret", serialized)
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertEqual(blocked[0]["result"]["outputs"]["reason"], "request_projection_accept_failed")
        self.assertEqual(blocked[0]["result"]["outputs"]["exception_type"], "RuntimeError")
        self.assertEqual(self.dispatcher.agent_messages.read_inbox(self.run_dir, "character:Ada"), [])

    def test_request_projection_blocks_structured_when_complete_returns_failure(self):
        request = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["projection"],
                "type": "request_actor",
                "visibility": "gm_only",
                "source_call_id": "call-character-Ada-1",
                "payload": {
                    "actor_id": "character:Ada",
                    "call": {
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "Listen at the door.",
                    },
                },
            },
        )["message"]
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "request_projection",
                "payload": {
                    "actor_id": "character:Ada",
                    "source_message_id": request["id"],
                    "source_call_id": "call-character-Ada-1",
                },
            },
        )["intent"]
        original_complete = self.dispatcher.agent_intents.complete_intent
        self.dispatcher.agent_intents.complete_intent = (
            lambda _run_dir, _intent_id, outputs=None: {"ok": False, "reason": "fixture_complete_failed"}
        )
        self.addCleanup(setattr, self.dispatcher.agent_intents, "complete_intent", original_complete)

        self._install_projection_stub()

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "request_projection_complete_failed")
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertEqual(blocked[0]["result"]["outputs"]["reason"], "request_projection_complete_failed")
        self.assertEqual(blocked[0]["result"]["outputs"]["transition_reason"], "fixture_complete_failed")
        inbox = self.dispatcher.agent_messages.read_inbox(self.run_dir, "character:Ada")
        self.assertEqual([message["type"] for message in inbox], ["projected_message"])

    def test_request_projection_blocks_structured_when_complete_fails_after_side_effects(self):
        request = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["projection"],
                "type": "request_actor",
                "visibility": "gm_only",
                "source_call_id": "call-character-Ada-1",
                "payload": {
                    "actor_id": "character:Ada",
                    "call": {
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "Listen at the door.",
                    },
                },
            },
        )["message"]
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "request_projection",
                "payload": {
                    "actor_id": "character:Ada",
                    "source_message_id": request["id"],
                    "source_call_id": "call-character-Ada-1",
                },
            },
        )["intent"]
        original_complete = self.dispatcher.agent_intents.complete_intent

        def complete_then_raise(run_dir, intent_id, outputs=None):
            original_complete(run_dir, intent_id, outputs=outputs)
            raise RuntimeError("raw complete secret")

        self.dispatcher.agent_intents.complete_intent = complete_then_raise
        self.addCleanup(setattr, self.dispatcher.agent_intents, "complete_intent", original_complete)

        self._install_projection_stub()

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "request_projection_complete_failed")
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("raw complete secret", serialized)
        self.assertNotIn("RuntimeError: raw complete secret", serialized)
        self.assertEqual(self.intents.list_intents(self.run_dir, "completed"), [])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertEqual(blocked[0]["result"]["outputs"]["reason"], "request_projection_complete_failed")
        inbox = self.dispatcher.agent_messages.read_inbox(self.run_dir, "character:Ada")
        self.assertEqual([message["type"] for message in inbox], ["projected_message"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([intent["type"] for intent in pending], ["run_actor"])

    def test_request_projection_blocks_when_source_message_missing(self):
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "request_projection",
                "payload": {
                    "actor_id": "character:Ada",
                    "source_message_id": "msg_999999",
                    "source_call_id": "call-character-Ada-1",
                },
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "projection_source_missing")
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertEqual(blocked[0]["result"]["outputs"]["reason"], "projection_source_missing")

    def test_request_projection_blocks_when_source_is_not_request_actor(self):
        source = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["projection"],
                "type": "analysis_applied",
                "visibility": "gm_only",
                "payload": {"actor_id": "character:Ada"},
            },
        )["message"]
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "request_projection",
                "payload": {
                    "actor_id": "character:Ada",
                    "source_message_id": source["id"],
                    "source_call_id": "call-character-Ada-1",
                },
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "projection_source_invalid")
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])

    def test_request_projection_blocks_on_actor_mismatch(self):
        source = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["projection"],
                "type": "request_actor",
                "visibility": "gm_only",
                "source_call_id": "call-character-Ada-1",
                "payload": {
                    "actor_id": "character:Ada",
                    "call": {
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "Listen at the door.",
                    },
                },
            },
        )["message"]
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "request_projection",
                "payload": {
                    "actor_id": "character:Bea",
                    "source_message_id": source["id"],
                    "source_call_id": "call-character-Ada-1",
                },
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "projection_actor_mismatch")
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])

    def test_request_projection_blocks_when_projected_append_rejected(self):
        source = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["projection"],
                "type": "request_actor",
                "visibility": "gm_only",
                "source_call_id": "call-character-Ada-1",
                "payload": {
                    "actor_id": "character:Ada",
                    "call": {
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "Listen at the door.",
                    },
                },
            },
        )["message"]
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "request_projection",
                "payload": {
                    "actor_id": "character:Ada",
                    "source_message_id": source["id"],
                    "source_call_id": "call-character-Ada-1",
                },
            },
        )["intent"]
        original_append = self.dispatcher.agent_actor_runtime.agent_messages.append_message
        self.addCleanup(setattr, self.dispatcher.agent_actor_runtime.agent_messages, "append_message", original_append)

        def reject_projected_message(run_dir, message):
            if message.get("type") == "projected_message":
                return {"ok": False, "reason": "fixture_append_rejected"}
            return original_append(run_dir, message)

        self.dispatcher.agent_actor_runtime.agent_messages.append_message = reject_projected_message

        self._install_projection_stub()

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "projection_append_rejected")
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertEqual(blocked[0]["result"]["outputs"]["reason"], "projection_append_rejected")
        self.assertEqual(self.dispatcher.agent_messages.read_inbox(self.run_dir, "character:Ada"), [])

    def test_request_projection_blocks_when_projected_append_missing_id(self):
        source = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["projection"],
                "type": "request_actor",
                "visibility": "gm_only",
                "source_call_id": "call-character-Ada-1",
                "payload": {
                    "actor_id": "character:Ada",
                    "call": {
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "Listen at the door.",
                    },
                },
            },
        )["message"]
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "request_projection",
                "payload": {
                    "actor_id": "character:Ada",
                    "source_message_id": source["id"],
                    "source_call_id": "call-character-Ada-1",
                },
            },
        )["intent"]
        original_append = self.dispatcher.agent_actor_runtime.agent_messages.append_message
        self.addCleanup(setattr, self.dispatcher.agent_actor_runtime.agent_messages, "append_message", original_append)

        def missing_projected_message_id(run_dir, message):
            if message.get("type") == "projected_message":
                return {"ok": True, "message": {}}
            return original_append(run_dir, message)

        self.dispatcher.agent_actor_runtime.agent_messages.append_message = missing_projected_message_id

        self._install_projection_stub()

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "projection_append_missing_id")
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertEqual(blocked[0]["result"]["outputs"]["reason"], "projection_append_missing_id")
        self.assertEqual(self.dispatcher.agent_messages.read_inbox(self.run_dir, "character:Ada"), [])

    def test_run_subgm_thread_dispatches_ready_thread_and_completes(self):
        self._start_subgm_thread()
        created = self._create_run_subgm_thread_intent()
        dispatch_calls = []

        def fake_dispatch(agent_key, run_dir, root_dir, run_claude, extra_context):
            dispatch_calls.append((agent_key, Path(run_dir), Path(root_dir), extra_context.get("packet")))
            if agent_key == "subGM:side_suli_rooftop":
                return self._subgm_output(actor_calls=[self._subgm_actor_call()])
            if agent_key == "character:SuLi":
                return self._subgm_character_output()
            raise AssertionError(agent_key)

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["intent_type"], "run_subgm_thread")
        self.assertEqual(result["detail"]["side_thread_status"], "completed")
        self.assertEqual(result["detail"]["called_actors"], ["character:SuLi"])
        self.assertFalse(result["detail"]["noop"])
        self.assertEqual([item[0] for item in dispatch_calls], ["subGM:side_suli_rooftop", "character:SuLi"])
        self.assertEqual(dispatch_calls[0][1], self.run_dir)
        self.assertEqual(dispatch_calls[0][2], ROOT)
        self.assertEqual(dispatch_calls[0][3]["thread_id"], "side_suli_rooftop")
        completed = self.intents.list_intents(self.run_dir, "completed")
        self.assertEqual([item["id"] for item in completed], [created["id"]])

    def test_run_subgm_thread_paused_and_completed_threads_complete_as_noop(self):
        self._start_subgm_thread(thread_id="side_paused", status="paused")
        self._start_subgm_thread(thread_id="side_completed", status="completed")
        paused_intent = self._create_run_subgm_thread_intent("side_paused")
        completed_intent = self._create_run_subgm_thread_intent("side_completed")

        def fake_dispatch(*_args, **_kwargs):
            raise AssertionError("noop side thread must not dispatch")

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        paused_result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")
        completed_result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertTrue(paused_result["ok"])
        self.assertEqual(paused_result["intent_id"], paused_intent["id"])
        self.assertEqual(paused_result["detail"]["side_thread_status"], "paused")
        self.assertTrue(paused_result["detail"]["noop"])
        self.assertTrue(completed_result["ok"])
        self.assertEqual(completed_result["intent_id"], completed_intent["id"])
        self.assertEqual(completed_result["detail"]["side_thread_status"], "completed")
        self.assertTrue(completed_result["detail"]["noop"])

    def test_run_subgm_thread_missing_thread_blocks_as_dispatch_failed(self):
        created = self._create_run_subgm_thread_intent("side_missing")

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "subgm_dispatch_failed")
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertIn("side_missing", blocked[0]["result"]["outputs"]["error"])

    def test_run_subgm_thread_invalid_thread_id_blocks_as_dispatch_failed(self):
        created = self._create_run_subgm_thread_intent("Bad Thread")

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "subgm_dispatch_failed")
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertIn("thread_id", blocked[0]["result"]["outputs"]["error"])

    def test_run_subgm_thread_blocks_when_side_thread_calls_player(self):
        self._start_subgm_thread()
        created = self._create_run_subgm_thread_intent()

        def fake_dispatch(agent_key, _run_dir, _root_dir, _run_claude, extra_context):
            if agent_key == "subGM:side_suli_rooftop":
                call = self._subgm_actor_call(actor_id="player", call_id="call-character-SuLi-1")
                return self._subgm_output(actor_calls=[call])
            raise AssertionError((agent_key, extra_context))

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "subgm_dispatch_failed")
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertIn("player", blocked[0]["result"]["outputs"]["error"])

    def test_run_subgm_thread_blocks_when_side_thread_calls_out_of_boundary_character(self):
        self._start_subgm_thread()
        created = self._create_run_subgm_thread_intent()

        def fake_dispatch(agent_key, _run_dir, _root_dir, _run_claude, extra_context):
            if agent_key == "subGM:side_suli_rooftop":
                call = self._subgm_actor_call(actor_id="character:Bert", call_id="call-character-Bert-1")
                return self._subgm_output(actor_calls=[call])
            raise AssertionError((agent_key, extra_context))

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertFalse(result["ok"])
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "subgm_dispatch_failed")
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertIn("allowed_characters", blocked[0]["result"]["outputs"]["error"])

    def test_run_subgm_thread_needs_gm_creates_run_gm_turn_follow_up(self):
        self._start_subgm_thread()
        created = self._create_run_subgm_thread_intent()

        def fake_dispatch(agent_key, _run_dir, _root_dir, _run_claude, extra_context):
            self.assertEqual(agent_key, "subGM:side_suli_rooftop")
            self.assertEqual(extra_context["packet"]["thread_id"], "side_suli_rooftop")
            return self._subgm_output(status="needs_gm")

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "{}")

        self.assertTrue(result["ok"])
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["detail"]["side_thread_status"], "needs_gm")
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["run_gm_turn"])
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(
            pending[0]["payload"],
            {
                "thread_id": "side_suli_rooftop",
                "status": "needs_gm",
                "called_actors": [],
                "reason": "subgm_thread_needs_gm_arbitration",
            },
        )

    def test_run_gm_turn_actor_call_creates_projection_intent_without_actor_dispatch(self):
        self._install_dispatcher_dependencies()
        self._write_gm_actor_input()
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "input_analyst", "type": "run_gm_turn", "payload": {}},
        )["intent"]
        dispatch_calls = []

        def fake_dispatch(agent_key, _run_dir, _root, _run_claude, _extra_context):
            dispatch_calls.append(agent_key)
            if agent_key != "gm":
                self.fail(f"run_gm_turn should not dispatch {agent_key}")
            return self._gm_output(actor_calls=[self._gm_actor_call()], stop_reason="continue")

        self.dispatcher._dispatch_agent_payload = fake_dispatch
        self.dispatcher.rp_generate_cli._run_legacy_interactive_agent_loop = lambda *_args, **_kwargs: self.fail(
            "run_gm_turn must not use the broad interactive GM loop by default"
        )

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertTrue((self.run_dir / "artifacts" / "gm.output.json").exists())
        self.assertFalse((self.run_dir / "actor.outputs.json").exists())
        self.assertFalse((self.run_dir / "artifacts" / "actor.outputs.json").exists())
        self.assertEqual(result["artifacts"], ["artifacts/gm.output.json"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["request_projection"])
        self.assertEqual(pending[0]["payload"]["actor_id"], "character:Ada")
        self.assertEqual(pending[0]["payload"]["source_call_id"], "call-character-Ada-1")
        self.assertEqual(pending[0]["requested_by"], "gm")
        request_messages = self.dispatcher.agent_messages.read_messages(self.run_dir)
        request_actor = [message for message in request_messages if message.get("type") == "request_actor"][0]
        actor_packet = request_actor["payload"]["packet"]
        self.assertEqual(actor_packet["actor_id"], "character:Ada")
        self.assertEqual(actor_packet["immersive_context"], "Ada remembers the archive guard oath.")
        self.assertEqual(actor_packet["memory"]["key_memories"], ["I serve as archive guard."])
        self.assertEqual(result["detail"]["created_projection_intents"], [pending[0]["id"]])
        self.assertEqual(result["detail"]["loop_result"]["called_actors"], [])
        completed = self.intents.list_intents(self.run_dir, "completed")
        self.assertEqual([item["id"] for item in completed], [created["id"]])
        self.assertEqual(dispatch_calls, ["gm"])

    def test_run_gm_turn_passes_repair_context_to_gm_only_dispatch(self):
        self._install_dispatcher_dependencies()
        repair_context = {
            "critic_report_path": "artifacts/critic.report.json",
            "decision": "revise",
            "repair_instruction": "Replay the GM loop with stricter causality.",
            "repair_routing": {
                "stage": "gm_loop",
                "target_agents": ["gm"],
                "rollback": "round_progression",
                "can_auto_repair": True,
                "risk": "medium",
            },
            "repair_fingerprint": "fingerprint-gm",
        }
        self.intents.create_intent(
            self.run_dir,
            {"requested_by": "rollback", "type": "run_gm_turn", "payload": {"repair_context": repair_context}},
        )
        dispatch_contexts = []

        def fake_dispatch(agent_key, _run_dir, _root, _run_claude, extra_context):
            dispatch_contexts.append((agent_key, extra_context))
            return self._gm_output(actor_calls=[], stop_reason="complete")

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertTrue(result["ok"])
        self.assertEqual(dispatch_contexts[0][0], "gm")
        self.assertEqual(dispatch_contexts[0][1]["repair_context"], repair_context)

    def test_run_gm_turn_runnable_side_thread_creates_run_subgm_thread_intent(self):
        self._install_dispatcher_dependencies()
        self._write_gm_actor_input()
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "input_analyst", "type": "run_gm_turn", "payload": {}},
        )["intent"]

        def fake_dispatch(agent_key, _run_dir, _root, _run_claude, _extra_context):
            self.assertEqual(agent_key, "gm")
            return self._gm_output(
                actor_calls=[],
                subgm_commands=[self._subgm_start_command("side_ada_archive")],
                stop_reason="continue",
            )

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertTrue(result["ok"])
        self.assertEqual(result["intent_id"], created["id"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["run_subgm_thread"])
        self.assertEqual(pending[0]["requested_by"], "gm")
        self.assertEqual(pending[0]["payload"]["thread_id"], "side_ada_archive")
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(result["detail"]["created_subgm_intents"], [pending[0]["id"]])

    def test_run_gm_turn_without_collaboration_creates_compose_story(self):
        self._install_dispatcher_dependencies()
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "input_analyst", "type": "run_gm_turn", "payload": {}},
        )["intent"]

        def fake_dispatch(agent_key, _run_dir, _root, _run_claude, _extra_context):
            self.assertEqual(agent_key, "gm")
            return self._gm_output(actor_calls=[], stop_reason="word_target")

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertTrue(result["ok"])
        self.assertEqual(result["intent_id"], created["id"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["compose_story"])
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(pending[0]["payload"]["reason"], "gm_step_complete")
        self.assertEqual(pending[0]["payload"]["loop_result"]["stop_reason"], "word_target")
        self.assertFalse((self.run_dir / "actor.outputs.json").exists())

    def test_run_gm_turn_player_decision_marks_terminal_without_story_follow_up(self):
        self._install_dispatcher_dependencies()
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "input_analyst", "type": "run_gm_turn", "payload": {}},
        )["intent"]
        decision_point = {"reason": "Choose the archive door response.", "options": ["Knock", "Leave"]}

        def fake_dispatch(agent_key, _run_dir, _root, _run_claude, _extra_context):
            self.assertEqual(agent_key, "gm")
            return self._gm_output(
                actor_calls=[],
                decision_point=decision_point,
                stop_reason="player_decision",
            )

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertTrue(result["ok"])
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        self.assertTrue(result["detail"]["player_decision_required"])
        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        self.assertEqual(trace["status"], "decision_point")

    def test_run_gm_turn_default_path_does_not_reference_broad_loop_helper(self):
        self._install_dispatcher_dependencies()
        self.intents.create_intent(
            self.run_dir,
            {"requested_by": "input_analyst", "type": "run_gm_turn", "payload": {}},
        )

        def fake_legacy_loop(*_args, **_kwargs):
            self.fail("default run_gm_turn path must not call the legacy interactive loop helper")

        def fake_dispatch(agent_key, _run_dir, _root, _run_claude, _extra_context):
            self.assertEqual(agent_key, "gm")
            return self._gm_output(actor_calls=[], stop_reason="complete")

        self.dispatcher.rp_generate_cli._run_legacy_interactive_agent_loop = fake_legacy_loop
        self.dispatcher._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertTrue(result["ok"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([intent["type"] for intent in pending], ["compose_story"])

    def test_run_gm_turn_blocks_when_complete_returns_failure(self):
        self._install_dispatcher_dependencies()
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "input_analyst", "type": "run_gm_turn", "payload": {}},
        )["intent"]
        original_complete = self.dispatcher.agent_intents.complete_intent

        def fake_dispatch(agent_key, _run_dir, _root, _run_claude, _extra_context):
            self.assertEqual(agent_key, "gm")
            return self._gm_output(actor_calls=[], stop_reason="complete")

        self.dispatcher._dispatch_agent_payload = fake_dispatch
        self.dispatcher.agent_intents.complete_intent = (
            lambda _run_dir, _intent_id, outputs=None: {"ok": False, "reason": "fixture_complete_failed"}
        )
        self.addCleanup(setattr, self.dispatcher.agent_intents, "complete_intent", original_complete)

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "run_gm_turn_complete_failed")
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertEqual(blocked[0]["result"]["outputs"]["reason"], "run_gm_turn_complete_failed")
        self.assertEqual(blocked[0]["result"]["outputs"]["transition_reason"], "fixture_complete_failed")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "blocked")
        self.assertEqual(manifest["dispatcher"]["reason"], "run_gm_turn_complete_failed")

    def test_run_gm_turn_recovers_when_complete_raises_after_side_effects(self):
        self._install_dispatcher_dependencies()
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "input_analyst", "type": "run_gm_turn", "payload": {}},
        )["intent"]
        original_complete = self.dispatcher.agent_intents.complete_intent

        def fake_dispatch(agent_key, _run_dir, _root, _run_claude, _extra_context):
            self.assertEqual(agent_key, "gm")
            return self._gm_output(actor_calls=[], stop_reason="complete")

        def complete_then_raise(run_dir, intent_id, outputs=None):
            original_complete(run_dir, intent_id, outputs=outputs)
            raise RuntimeError("raw complete secret")

        self.dispatcher._dispatch_agent_payload = fake_dispatch
        self.dispatcher.agent_intents.complete_intent = complete_then_raise
        self.addCleanup(setattr, self.dispatcher.agent_intents, "complete_intent", original_complete)

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "run_gm_turn_complete_recovered")
        self.assertEqual(result["detail"]["transition_failure_recovered"], "run_gm_turn_complete_failed")
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("raw complete secret", serialized)
        self.assertEqual(self.intents.list_intents(self.run_dir, "blocked"), [])
        completed = self.intents.list_intents(self.run_dir, "completed")
        self.assertEqual([item["id"] for item in completed], [created["id"]])
        self.assertTrue((self.run_dir / "artifacts" / "gm.output.json").exists())
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([intent["type"] for intent in pending], ["compose_story"])

    def test_run_gm_turn_blocks_when_gm_only_dispatch_raises_after_accept(self):
        self._install_dispatcher_dependencies()
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "input_analyst", "type": "run_gm_turn", "payload": {}},
        )["intent"]

        def fake_dispatch(_agent_key, _run_dir, _root, _run_claude, _extra_context):
            raise RuntimeError("fixture gm dispatch exploded")

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "run_gm_turn_failed")
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertIn("fixture gm dispatch exploded", blocked[0]["result"]["outputs"]["error"])
        self.assertEqual(self.intents.list_intents(self.run_dir, "accepted"), [])
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "blocked")
        self.assertEqual(manifest["dispatcher"]["reason"], "run_gm_turn_failed")

    def test_compose_story_writes_story_output_artifact_and_creates_review_intent(self):
        self._install_dispatcher_dependencies()
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "gm", "type": "compose_story", "payload": {"reason": "loop_complete"}},
        )["intent"]
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["prompts"] = {"story": "prompts/story.custom.md"}
        _write_json(self.run_dir / "manifest.json", manifest)
        (self.run_dir / "prompts").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "prompts" / "story.custom.md").write_text("story prompt", encoding="utf-8")
        build_calls = []
        dispatch_calls = []

        def fake_build_story_input(run_dir):
            payload = {"round_id": "round-000001", "fixture": "story input"}
            build_calls.append(Path(run_dir))
            self.dispatcher.write_artifact(run_dir, "story.input.json", payload)
            return payload

        def fake_dispatch(agent_key, prompt, root_dir, run_claude, extra_context=None):
            dispatch_calls.append((agent_key, prompt, Path(root_dir), extra_context))
            self.assertEqual(agent_key, "story")
            return {"content": "<content>Story text.</content>", "metadata": {"round_id": "round-000001"}}

        original_build_story_input = self.dispatcher.agent_outputs.build_story_input
        original_dispatch = self.dispatcher.rp_generate_cli._dispatch_agent_payload
        try:
            self.dispatcher.agent_outputs.build_story_input = fake_build_story_input
            self.dispatcher.rp_generate_cli._dispatch_agent_payload = fake_dispatch

            result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")
        finally:
            self.dispatcher.agent_outputs.build_story_input = original_build_story_input
            self.dispatcher.rp_generate_cli._dispatch_agent_payload = original_dispatch

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["artifacts"], ["artifacts/story.input.json", "artifacts/story.output.json"])
        story_output = json.loads((self.run_dir / "artifacts" / "story.output.json").read_text(encoding="utf-8"))
        self.assertEqual(
            story_output["content"],
            "<content>Story text.</content>\n<character_dialogues>[]</character_dialogues>",
        )
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["review_critic"])
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(len(build_calls), 1)
        self.assertEqual(dispatch_calls[0][1], "story prompt")
        self.assertEqual(dispatch_calls[0][3], {"story_input": {"round_id": "round-000001", "fixture": "story input"}})

    def test_compose_story_writes_normalized_story_output_artifact(self):
        self._install_dispatcher_dependencies()
        self.intents.create_intent(
            self.run_dir,
            {"requested_by": "gm", "type": "compose_story", "payload": {"reason": "loop_complete"}},
        )
        (self.run_dir / "prompts").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "prompts" / "story.prompt.md").write_text("story prompt", encoding="utf-8")

        def fake_build_story_input(run_dir):
            payload = {"round_id": "round-000001", "loop_outputs": {"actors": {}}}
            self.dispatcher.write_artifact(run_dir, "story.input.json", payload)
            return payload

        def fake_dispatch(_agent_key, _run_dir, _root_dir, _run_claude, extra_context=None):
            return {
                "content": (
                    "<content>Story text.</content>"
                    "<TOKENS>in: NNNN\nout: NNNN\ntotal: NNNN</TOKENS>"
                ),
                "character_dialogues": "not a list",
                "tokens": {"in": "NNNN"},
                "token_usage": {"total": "NNNN"},
            }

        original_build_story_input = self.dispatcher.agent_outputs.build_story_input
        original_dispatch = self.dispatcher._dispatch_agent_payload
        try:
            self.dispatcher.agent_outputs.build_story_input = fake_build_story_input
            self.dispatcher._dispatch_agent_payload = fake_dispatch

            result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")
        finally:
            self.dispatcher.agent_outputs.build_story_input = original_build_story_input
            self.dispatcher._dispatch_agent_payload = original_dispatch

        self.assertTrue(result["ok"])
        story_output = json.loads((self.run_dir / "artifacts" / "story.output.json").read_text(encoding="utf-8"))
        self.assertNotIn("tokens", story_output)
        self.assertNotIn("token_usage", story_output)
        self.assertEqual(story_output["character_dialogues"], [])
        self.assertNotIn("<tokens>", story_output["content"].lower())
        self.assertNotIn("NNNN", story_output["content"])
        self.assertIn("<character_dialogues>[]</character_dialogues>", story_output["content"])

    def test_review_critic_pass_creates_run_postprocess_intent(self):
        self._install_dispatcher_dependencies()
        _write_json(self.run_dir / "artifacts" / "story.input.json", {"round_id": "round-000001"})
        _write_json(
            self.run_dir / "artifacts" / "story.output.json",
            {"content": "<content>Story text.</content>", "metadata": {"round_id": "round-000001"}},
        )
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["runtime_settings"] = {
            "style": "light",
            "wordCount": 1000,
            "nsfw": "关闭",
            "selfRepairMode": "limited",
            "allowSourceCodeSelfRepair": False,
        }
        manifest["style_profile"] = {
            "name": "light",
            "title": "Light Style",
            "content": "Use direct sentences.",
            "warning": "",
        }
        _write_json(self.run_dir / "manifest.json", manifest)
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "story", "type": "review_critic", "payload": {"reason": "story_ready"}},
        )["intent"]
        (self.run_dir / "prompts").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "prompts" / "critic.prompt.md").write_text("critic prompt", encoding="utf-8")
        dispatch_calls = []

        def fake_dispatch(agent_key, prompt, root_dir, run_claude, extra_context=None):
            dispatch_calls.append((agent_key, prompt, Path(root_dir), extra_context))
            self.assertEqual(agent_key, "critic")
            return {"decision": "pass", "hard_failures": [], "soft_issues": [], "repair_instruction": ""}

        self.dispatcher.rp_generate_cli._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["artifacts"], ["artifacts/critic.report.json"])
        critic_report = json.loads((self.run_dir / "artifacts" / "critic.report.json").read_text(encoding="utf-8"))
        self.assertEqual(critic_report["decision"], "pass")
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["run_postprocess"])
        self.assertEqual(pending[0]["requested_by"], "critic")
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(dispatch_calls[0][1], "critic prompt")
        critic_context = dispatch_calls[0][3]
        self.assertEqual(critic_context["story_input"], {"round_id": "round-000001"})
        self.assertEqual(
            critic_context["story_output"],
            {"content": "<content>Story text.</content>", "metadata": {"round_id": "round-000001"}},
        )
        self.assertEqual(critic_context["quality_metrics"]["style"], "light")
        self.assertEqual(critic_context["quality_metrics"]["style_profile"]["title"], "Light Style")
        self.assertEqual(critic_context["quality_metrics"]["word_count"]["target"], 1000)
        self.assertEqual(critic_context["quality_metrics"]["word_count"]["current"], 2)
        self.assertNotIn("nsfw", json.dumps(critic_context["quality_metrics"], ensure_ascii=False))

    def test_review_critic_normalizes_stale_token_failure_to_delivery(self):
        self._install_dispatcher_dependencies()
        _write_json(self.run_dir / "artifacts" / "story.input.json", {"round_id": "round-000001"})
        _write_json(
            self.run_dir / "artifacts" / "story.output.json",
            {
                "content": "<content>Clean story without token placeholders.</content>",
                "character_dialogues": [],
                "metadata": {},
            },
        )
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "story", "type": "review_critic", "payload": {"reason": "story_ready"}},
        )["intent"]
        (self.run_dir / "prompts").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "prompts" / "critic.prompt.md").write_text("critic prompt", encoding="utf-8")

        def fake_dispatch(_agent_key, _run_dir, _root_dir, _run_claude, extra_context=None):
            return {
                "decision": "revise",
                "hard_failures": ["story.output.json contains placeholder <tokens> values ('NNNN')"],
                "soft_issues": ["Prose could use sharper sensory detail."],
                "repair_instruction": "Remove fake token placeholders.",
                "system_iteration_suggestion": "",
            }

        original_dispatch = self.dispatcher._dispatch_agent_payload
        try:
            self.dispatcher._dispatch_agent_payload = fake_dispatch

            result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")
        finally:
            self.dispatcher._dispatch_agent_payload = original_dispatch

        self.assertTrue(result["ok"])
        self.assertEqual(result["intent_id"], created["id"])
        critic_report = json.loads((self.run_dir / "artifacts" / "critic.report.json").read_text(encoding="utf-8"))
        self.assertEqual(critic_report["decision"], "pass")
        self.assertEqual(critic_report["hard_failures"], [])
        self.assertEqual(critic_report["soft_issues"], ["Prose could use sharper sensory detail."])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["run_postprocess"])

    def test_run_postprocess_writes_output_and_creates_delivery_intent(self):
        self._install_dispatcher_dependencies()
        story_input = {
            "round_id": "round-000001",
            "interaction_trace": {
                "visible_events": [
                    {
                        "id": "critical-action-1",
                        "actor": "player",
                        "custom_action": {
                            "actor_id": "player",
                            "risk_level": "critical",
                            "visible_content": "I push open the sealed door.",
                        },
                    }
                ]
            },
        }
        story_output = {"content": "<content>Story text.</content>", "metadata": {"round_id": "round-000001"}}
        critic_report = {"decision": "pass", "hard_failures": [], "soft_issues": []}
        _write_json(self.run_dir / "artifacts" / "story.input.json", story_input)
        _write_json(self.run_dir / "artifacts" / "story.output.json", story_output)
        _write_json(self.run_dir / "artifacts" / "critic.report.json", critic_report)
        (self.run_dir / "prompts").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "prompts" / "postprocess.prompt.md").write_text("postprocess prompt", encoding="utf-8")
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "critic", "type": "run_postprocess", "payload": {"reason": "critic_passed"}},
        )["intent"]
        dispatch_calls = []

        def fake_dispatch(agent_key, run_dir, root_dir, run_claude, extra_context=None):
            dispatch_calls.append((agent_key, Path(run_dir), Path(root_dir), extra_context))
            return {
                "schema_version": 1,
                "core": {
                    "summary": "You reach the sealed door.",
                    "current_goal": "Confirm whether to force the door.",
                    "options": [
                        {
                            "label": "Confirm action: I push open the sealed door.",
                            "source": "player_agent_critical_action",
                            "requires_confirmation": True,
                        }
                    ],
                },
                "ui_extension_status": {"status": "ok", "issues": []},
            }

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["artifacts"], ["artifacts/postprocess.output.json", "postprocess.output.json"])
        postprocess_artifact = json.loads(
            (self.run_dir / "artifacts" / "postprocess.output.json").read_text(encoding="utf-8")
        )
        postprocess_root = json.loads((self.run_dir / "postprocess.output.json").read_text(encoding="utf-8"))
        self.assertEqual(postprocess_artifact, postprocess_root)
        self.assertEqual(postprocess_artifact["core"]["summary"], "You reach the sealed door.")
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["deliver_round"])
        self.assertEqual(pending[0]["requested_by"], "postprocess")
        self.assertEqual(pending[0]["payload"]["reason"], "postprocess_core_valid")
        self.assertEqual(dispatch_calls[0][0], "postprocess")
        postprocess_context = dispatch_calls[0][3]["postprocess_context"]
        self.assertEqual(postprocess_context["story_input"], story_input)
        self.assertEqual(postprocess_context["story_output"], story_output)
        self.assertEqual(postprocess_context["critic_report"], critic_report)
        self.assertEqual(postprocess_context["pending_repairs"], [])
        self.assertTrue(postprocess_context["critical_action_evidence"])

    def test_run_postprocess_blocks_core_invalid_without_delivery_intent(self):
        self._install_dispatcher_dependencies()
        _write_json(self.run_dir / "artifacts" / "story.input.json", {"round_id": "round-000001"})
        _write_json(self.run_dir / "artifacts" / "story.output.json", {"content": "<content>Story text.</content>"})
        _write_json(self.run_dir / "artifacts" / "critic.report.json", {"decision": "pass"})
        (self.run_dir / "prompts").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "prompts" / "postprocess.prompt.md").write_text("postprocess prompt", encoding="utf-8")
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "critic", "type": "run_postprocess", "payload": {"reason": "critic_passed"}},
        )["intent"]

        def fake_dispatch(_agent_key, _run_dir, _root_dir, _run_claude, extra_context=None):
            return {
                "schema_version": 1,
                "core": {"summary": "Only a summary."},
                "ui_extension_status": {"status": "ok", "issues": []},
            }

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["intent_type"], "run_postprocess")
        self.assertEqual(result["reason"], "postprocess_core_invalid")
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])

    def test_run_postprocess_records_ui_extension_repair_but_still_delivers(self):
        self._install_dispatcher_dependencies()
        _write_json(self.run_dir / "artifacts" / "story.input.json", {"round_id": "round-000001"})
        _write_json(self.run_dir / "artifacts" / "story.output.json", {"content": "<content>Story text.</content>"})
        _write_json(self.run_dir / "artifacts" / "critic.report.json", {"decision": "pass"})
        (self.run_dir / "prompts").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "prompts" / "postprocess.prompt.md").write_text("postprocess prompt", encoding="utf-8")
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "critic", "type": "run_postprocess", "payload": {"reason": "critic_passed"}},
        )["intent"]

        def fake_dispatch(_agent_key, _run_dir, _root_dir, _run_claude, extra_context=None):
            return {
                "schema_version": 1,
                "core": {
                    "summary": "You pause at the map table.",
                    "current_goal": "Choose the next route.",
                    "options": ["Study the map"],
                },
                "ui_extension_status": {
                    "status": "needs_repair",
                    "issues": [{"key": "ui_extensions.status_panels.relationships"}],
                },
            }

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["deliver_round"])
        repairs_dir = self.run_dir / "artifacts" / "postprocess_repairs"
        repair_files = list(repairs_dir.glob("*.json"))
        self.assertEqual(len(repair_files), 1)
        repair = json.loads(repair_files[0].read_text(encoding="utf-8"))
        self.assertEqual(repair["status"], "pending")
        self.assertEqual(repair["required_keys"], ["ui_extensions.status_panels.relationships"])
        queue_path = self.card / ".agent_runs" / "postprocess_repair_queue.jsonl"
        queue_items = [json.loads(line) for line in queue_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([item["id"] for item in queue_items], [repair["id"]])

    def test_review_critic_revise_records_repair_request_metadata_and_message(self):
        self._install_dispatcher_dependencies()
        _write_json(self.run_dir / "artifacts" / "story.input.json", {"round_id": "round-000001"})
        _write_json(
            self.run_dir / "artifacts" / "story.output.json",
            {"content": "<content>Story text.</content>", "metadata": {"round_id": "round-000001"}},
        )
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "story", "type": "review_critic", "payload": {"reason": "story_ready"}},
        )["intent"]
        (self.run_dir / "prompts").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "prompts" / "critic.prompt.md").write_text("critic prompt", encoding="utf-8")

        def fake_dispatch(_agent_key, _prompt, _root_dir, _run_claude, extra_context=None):
            self.assertEqual(extra_context["story_input"], {"round_id": "round-000001"})
            return {
                "decision": "revise",
                "hard_failures": [],
                "soft_issues": ["weak stop point"],
                "repair_instruction": "Rewrite the stop point around the player decision.",
                "repair_routing": {"stage": "story_composition", "rollback": "story_only"},
            }

        self.dispatcher.rp_generate_cli._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["repair_request"])
        repair_payload = pending[0]["payload"]
        self.assertEqual(repair_payload["critic_report_path"], "artifacts/critic.report.json")
        self.assertEqual(repair_payload["decision"], "revise")
        self.assertEqual(repair_payload["repair_instruction"], "Rewrite the stop point around the player decision.")
        self.assertEqual(
            repair_payload["repair_routing"],
            {
                "stage": "story_composition",
                "target_agents": ["story"],
                "rollback": "story_only",
                "can_auto_repair": True,
                "risk": "low",
            },
        )
        self.assertTrue(repair_payload["repair_fingerprint"])
        history_path = self.run_dir / "repair_history.jsonl"
        self.assertTrue(history_path.exists())
        history = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["repair_instruction"], "Rewrite the stop point around the player decision.")
        self.assertEqual(history[0]["repair_routing"], repair_payload["repair_routing"])
        messages = self.dispatcher.agent_messages.read_messages(self.run_dir)
        repair_messages = [item for item in messages if item.get("type") == "repair_request"]
        self.assertEqual(len(repair_messages), 1)
        self.assertEqual(repair_messages[0]["payload"]["repair_fingerprint"], repair_payload["repair_fingerprint"])

    def test_repair_request_story_only_creates_compose_story(self):
        _write_json(
            self.run_dir / "artifacts" / "critic.report.json",
            {
                "decision": "revise",
                "repair_instruction": "Rewrite the ending around a concrete player choice.",
                "repair_routing": {
                    "stage": "story_composition",
                    "rollback": "story_only",
                    "can_auto_repair": True,
                    "risk": "low",
                },
            },
        )
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "critic",
                "type": "repair_request",
                "payload": {"critic_report_path": "artifacts/critic.report.json"},
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["compose_story"])
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(pending[0]["payload"]["critic_report_path"], "artifacts/critic.report.json")
        self.assertEqual(
            pending[0]["payload"]["repair_routing"],
            {
                "stage": "story_composition",
                "target_agents": ["story"],
                "rollback": "story_only",
                "can_auto_repair": True,
                "risk": "low",
            },
        )
        self.assertEqual(
            pending[0]["payload"]["repair_instruction"],
            "Rewrite the ending around a concrete player choice.",
        )
        self.assertEqual(
            pending[0]["payload"]["repair_context"],
            {
                "critic_report_path": "artifacts/critic.report.json",
                "decision": "revise",
                "repair_instruction": "Rewrite the ending around a concrete player choice.",
                "repair_routing": {
                    "stage": "story_composition",
                    "target_agents": ["story"],
                    "rollback": "story_only",
                    "can_auto_repair": True,
                    "risk": "low",
                },
            },
        )
        self.assertEqual([item["id"] for item in self.intents.list_intents(self.run_dir, "completed")], [created["id"]])

    def test_repair_request_round_progression_creates_rollback_request(self):
        _write_json(
            self.run_dir / "artifacts" / "critic.report.json",
            {
                "decision": "revise",
                "repair_instruction": "Re-run the GM loop from the before-round snapshot.",
                "repair_routing": {
                    "stage": "gm_loop",
                    "rollback": "round_progression",
                    "can_auto_repair": True,
                    "risk": "medium",
                },
                "snapshot_id": "round-000001-20260621T123456123456Z-abcdef123456",
                "repair_fingerprint": "fingerprint-round",
            },
        )
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "critic",
                "type": "repair_request",
                "payload": {"critic_report_path": "artifacts/critic.report.json"},
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["rollback_request"])
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(
            pending[0]["payload"],
            {
                "mode": "round_progression",
                "reason": "critic_repair",
                "critic_report_path": "artifacts/critic.report.json",
                "snapshot_id": "round-000001-20260621T123456123456Z-abcdef123456",
                "repair_context": {
                    "critic_report_path": "artifacts/critic.report.json",
                    "decision": "revise",
                    "repair_instruction": "Re-run the GM loop from the before-round snapshot.",
                    "repair_routing": {
                        "stage": "gm_loop",
                        "target_agents": ["gm"],
                        "rollback": "round_progression",
                        "can_auto_repair": True,
                        "risk": "medium",
                    },
                    "repair_fingerprint": "fingerprint-round",
                },
            },
        )

    def test_repair_request_system_code_creates_bounded_system_request(self):
        root = self._root_with_settings(
            {"selfRepairMode": "full", "allowSourceCodeSelfRepair": True}
        )
        _write_json(
            self.run_dir / "artifacts" / "critic.report.json",
            {
                "decision": "revise",
                "repair_instruction": "Diagnose and fix the reusable dispatcher defect.",
                "system_iteration_suggestion": "Add a regression test before changing source code.",
                "repair_routing": {
                    "stage": "system_code",
                    "rollback": "none",
                    "can_auto_repair": True,
                    "risk": "medium",
                },
                "repair_fingerprint": "fingerprint-system-code",
            },
        )
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "critic",
                "type": "repair_request",
                "payload": {"critic_report_path": "artifacts/critic.report.json"},
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, root)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["system_request"])
        self.assertEqual(pending[0]["requested_by"], "repair")
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(
            pending[0]["payload"],
            {
                "reason": "source_code_self_repair",
                "bounded": True,
                "requires": {
                    "selfRepairMode": "full",
                    "allowSourceCodeSelfRepair": True,
                },
                "critic_report_path": "artifacts/critic.report.json",
                "repair_instruction": "Diagnose and fix the reusable dispatcher defect.",
                "system_iteration_suggestion": "Add a regression test before changing source code.",
                "repair_context": {
                    "critic_report_path": "artifacts/critic.report.json",
                    "decision": "revise",
                    "repair_instruction": "Diagnose and fix the reusable dispatcher defect.",
                    "repair_routing": {
                        "stage": "system_code",
                        "target_agents": ["system"],
                        "rollback": "none",
                        "can_auto_repair": True,
                        "risk": "medium",
                    },
                    "repair_fingerprint": "fingerprint-system-code",
                },
            },
        )
        messages = self.dispatcher.agent_messages.read_messages(self.run_dir)
        system_messages = [item for item in messages if item.get("type") == "system_request"]
        self.assertEqual(len(system_messages), 1)
        self.assertEqual(system_messages[0]["to"], ["system"])
        self.assertEqual(system_messages[0]["payload"]["intent_id"], pending[0]["id"])

    def test_repair_request_system_code_blocks_without_source_switch(self):
        root = self._root_with_settings(
            {"selfRepairMode": "full", "allowSourceCodeSelfRepair": False}
        )
        _write_json(
            self.run_dir / "artifacts" / "critic.report.json",
            {
                "decision": "revise",
                "repair_instruction": "Diagnose the reusable dispatcher defect.",
                "repair_routing": {
                    "stage": "system_code",
                    "rollback": "none",
                    "can_auto_repair": True,
                    "risk": "medium",
                },
            },
        )
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "critic",
                "type": "repair_request",
                "payload": {"critic_report_path": "artifacts/critic.report.json"},
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, root)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "source_code_self_repair_not_authorized")
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertEqual(blocked[0]["result"]["reason"], "source_code_self_repair_not_authorized")
        outputs = blocked[0]["result"]["outputs"]
        self.assertEqual(outputs["executor"], "repair_request")
        self.assertEqual(outputs["repair_routing"]["stage"], "system_code")
        self.assertTrue(outputs["requires_source_repair_authorization"])

    def test_system_request_blocks_without_executing_source_edits(self):
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "repair",
                "type": "system_request",
                "payload": {
                    "reason": "source_code_self_repair",
                    "bounded": True,
                    "repair_instruction": "Diagnose before editing source code.",
                },
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "system_request_requires_main_agent")
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertEqual(blocked[0]["result"]["outputs"]["executor"], "system_request")
        self.assertEqual(blocked[0]["result"]["outputs"]["payload"], created["payload"])

    def test_round_progression_repair_context_flows_through_rollback_to_gm_turn(self):
        _write_json(
            self.run_dir / "artifacts" / "critic.report.json",
            {
                "decision": "revise",
                "repair_instruction": "Replay the turn from GM state.",
                "repair_routing": {
                    "stage": "gm_loop",
                    "rollback": "round_progression",
                    "can_auto_repair": True,
                    "risk": "medium",
                },
                "snapshot_id": "round-000001-20260621T123456123456Z-abcdef123456",
                "repair_fingerprint": "fingerprint-round-flow",
            },
        )
        self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "critic",
                "type": "repair_request",
                "payload": {"critic_report_path": "artifacts/critic.report.json"},
            },
        )
        repair_result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)
        self.assertTrue(repair_result["ok"])
        rollback = self.intents.list_intents(self.run_dir, "pending")[0]
        repair_context = rollback["payload"]["repair_context"]

        def fake_restore(card_folder, snapshot_id, *, mode):
            return {"ok": True, "snapshot_id": snapshot_id, "mode": mode, "restored": [".agent_runs/current"], "removed": []}

        self.dispatcher.agent_snapshots.restore_snapshot = fake_restore
        rollback_result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(rollback_result["ok"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["run_gm_turn"])
        self.assertEqual(pending[0]["payload"]["repair_context"], repair_context)
        self.assertEqual(pending[0]["payload"]["repair_context"]["repair_fingerprint"], "fingerprint-round-flow")

    def test_rollback_request_blocks_when_snapshot_missing(self):
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "repair",
                "type": "rollback_request",
                "payload": {
                    "snapshot_id": "round-000001-20260621T123456123456Z-abcdef123456",
                    "mode": "round_progression",
                },
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "rollback_failed")
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertEqual(blocked[0]["result"]["outputs"]["restore"]["reason"], "snapshot_missing")

    def test_rollback_request_real_restore_creates_follow_up_in_restored_current_run(self):
        _write_json(self.run_dir / "manifest.json", {"round_id": "round-000001", "stage": "prepared"})
        snapshot = self.dispatcher.agent_snapshots.create_snapshot(
            self.card,
            "round-000002",
            reason="fixture",
        )
        failed_run = self.card / ".agent_runs" / "round-000002"
        _write_json(failed_run / "manifest.json", {"round_id": "round-000002", "stage": "prepared"})
        (self.card / ".agent_runs" / "current").write_text(str(failed_run.resolve()), encoding="utf-8")
        created = self.intents.create_intent(
            failed_run,
            {
                "requested_by": "repair",
                "type": "rollback_request",
                "payload": {"snapshot_id": snapshot["snapshot_id"], "mode": "round_progression"},
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(failed_run, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertFalse(failed_run.exists())
        self.assertEqual(self.dispatcher.agent_run.current_run_dir(self.card), self.run_dir.resolve())
        restored_pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in restored_pending], ["run_gm_turn"])
        self.assertEqual(restored_pending[0]["payload"]["rollback"]["snapshot_id"], snapshot["snapshot_id"])
        self.assertEqual(self.intents.list_intents(self.run_dir, "completed"), [])
        self.assertEqual(result["detail"]["completion"]["status"], "skipped")
        self.assertEqual(result["detail"]["follow_up_run_dir"], str(self.run_dir.resolve()))

    def test_rollback_request_story_only_cleanup_preserves_gm_actor_trace_without_snapshot_restore(self):
        _write_json(self.run_dir / "gm.output.json", {"agent": "gm_loop", "outputs": [{"id": "gm"}]})
        _write_json(self.run_dir / "actor.outputs.json", {"actor_outputs": {"player": []}})
        _write_json(self.run_dir / "interaction.trace.json", {"schema_version": 2, "events": [{"id": "trace"}]})
        _write_json(self.run_dir / "story.input.json", {"stale": "story_input"})
        _write_json(self.run_dir / "story.output.json", {"stale": "story_output"})
        _write_json(self.run_dir / "critic.report.json", {"decision": "revise"})
        _write_json(self.run_dir / "artifacts" / "gm.output.json", {"agent": "gm_loop", "outputs": [{"id": "gm"}]})
        _write_json(self.run_dir / "artifacts" / "actor.outputs.json", {"actor_outputs": {"player": []}})
        _write_json(self.run_dir / "artifacts" / "interaction.trace.json", {"schema_version": 2, "events": [{"id": "trace"}]})
        _write_json(self.run_dir / "artifacts" / "story.input.json", {"stale": "story_input"})
        _write_json(self.run_dir / "artifacts" / "story.output.json", {"stale": "story_output"})
        _write_json(self.run_dir / "artifacts" / "critic.report.json", {"decision": "revise"})
        restore_calls = []

        def fail_restore(*args, **kwargs):
            restore_calls.append((args, kwargs))
            raise AssertionError("story_only must not call restore_snapshot")

        self.dispatcher.agent_snapshots.restore_snapshot = fail_restore
        repair_context = {
            "critic_report_path": "artifacts/critic.report.json",
            "decision": "revise",
            "repair_instruction": "Rewrite only the prose.",
            "repair_routing": {
                "stage": "story_composition",
                "target_agents": ["story"],
                "rollback": "story_only",
                "can_auto_repair": True,
                "risk": "low",
            },
            "repair_fingerprint": "fingerprint-story",
        }
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "repair",
                "type": "rollback_request",
                "payload": {"snapshot_id": "unused", "mode": "story_only", "repair_context": repair_context},
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(restore_calls, [])
        self.assertEqual(json.loads((self.run_dir / "gm.output.json").read_text(encoding="utf-8"))["agent"], "gm_loop")
        self.assertTrue((self.run_dir / "actor.outputs.json").exists())
        self.assertTrue((self.run_dir / "interaction.trace.json").exists())
        self.assertTrue((self.run_dir / "artifacts" / "gm.output.json").exists())
        self.assertTrue((self.run_dir / "artifacts" / "actor.outputs.json").exists())
        self.assertTrue((self.run_dir / "artifacts" / "interaction.trace.json").exists())
        for relative in ["story.input.json", "story.output.json", "critic.report.json"]:
            self.assertFalse((self.run_dir / relative).exists(), relative)
            self.assertFalse((self.run_dir / "artifacts" / relative).exists(), relative)
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["compose_story"])
        self.assertEqual(pending[0]["payload"]["repair_context"], repair_context)
        self.assertEqual(pending[0]["payload"]["rollback"]["strategy"], "story_only_cleanup")

    def test_rollback_request_story_only_success_creates_compose_story(self):
        restore_calls = []

        def fake_restore(card_folder, snapshot_id, *, mode):
            restore_calls.append((Path(card_folder), snapshot_id, mode))
            raise AssertionError("story_only must not call restore_snapshot")

        self.dispatcher.agent_snapshots.restore_snapshot = fake_restore
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "repair",
                "type": "rollback_request",
                "payload": {"snapshot_id": "snapshot_fixture", "mode": "story_only"},
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["compose_story"])
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(pending[0]["payload"]["rollback"]["mode"], "story_only")
        self.assertEqual(pending[0]["payload"]["rollback"]["strategy"], "story_only_cleanup")
        self.assertEqual(restore_calls, [])

    def test_rollback_request_round_progression_success_creates_run_gm_turn(self):
        restore_calls = []

        def fake_restore(card_folder, snapshot_id, *, mode):
            restore_calls.append((Path(card_folder), snapshot_id, mode))
            return {"ok": True, "snapshot_id": snapshot_id, "mode": mode, "restored": [".agent_runs/current"], "removed": []}

        self.dispatcher.agent_snapshots.restore_snapshot = fake_restore
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "repair",
                "type": "rollback_request",
                "payload": {"snapshot_id": "snapshot_fixture", "mode": "round_progression"},
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["run_gm_turn"])
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(pending[0]["payload"]["rollback"]["mode"], "round_progression")
        self.assertEqual(restore_calls, [(self.card, "snapshot_fixture", "round_progression")])

    def test_deliver_round_marks_delivered_when_delivery_command_passes(self):
        self._install_dispatcher_dependencies()
        _write_json(self.run_dir / "artifacts" / "critic.report.json", {"decision": "pass"})
        self._write_valid_postprocess_artifact()
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "postprocess",
                "type": "deliver_round",
                "payload": {
                    "reason": "postprocess_core_valid",
                    "postprocess_output_path": "artifacts/postprocess.output.json",
                },
            },
        )["intent"]
        delivery_calls = []

        def fake_run_delivery(card_folder, root_dir, run_command):
            delivery_calls.append((Path(card_folder), Path(root_dir), run_command))
            return {"ok": True, "result": {"ok": True, "mode": "agent_run"}}

        self.dispatcher.rp_generate_cli._run_delivery = fake_run_delivery

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_command=lambda *args, **kwargs: None)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "delivered")
        self.assertEqual(result["intent_id"], created["id"])
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "delivered")
        self.assertEqual(manifest["dispatcher"]["status"], "delivered")
        completed = self.intents.list_intents(self.run_dir, "completed")
        self.assertEqual([item["id"] for item in completed], [created["id"]])
        self.assertEqual(len(delivery_calls), 1)

    def test_deliver_round_blocks_critic_origin_without_running_delivery(self):
        self._install_dispatcher_dependencies()
        _write_json(self.run_dir / "artifacts" / "critic.report.json", {"decision": "pass"})
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "critic", "type": "deliver_round", "payload": {"reason": "critic_passed"}},
        )["intent"]
        delivery_calls = []

        def fake_run_delivery(card_folder, root_dir, run_command):
            delivery_calls.append((Path(card_folder), Path(root_dir), run_command))
            return {"ok": True, "result": {"ok": True, "mode": "agent_run"}}

        self.dispatcher.rp_generate_cli._run_delivery = fake_run_delivery

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_command=lambda *args, **kwargs: None)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "postprocess_missing")
        self.assertEqual(delivery_calls, [])
        self.assertEqual(self.intents.list_intents(self.run_dir, "completed"), [])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "blocked")
        self.assertEqual(manifest["dispatcher"]["reason"], "postprocess_missing")

    def test_deliver_round_blocks_invalid_postprocess_artifact_without_running_delivery(self):
        self._install_dispatcher_dependencies()
        _write_json(self.run_dir / "artifacts" / "critic.report.json", {"decision": "pass"})
        _write_json(
            self.run_dir / "artifacts" / "postprocess.output.json",
            {"schema_version": 1, "core": {"summary": "Only a summary."}},
        )
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "postprocess",
                "type": "deliver_round",
                "payload": {
                    "reason": "postprocess_core_valid",
                    "postprocess_output_path": "artifacts/postprocess.output.json",
                },
            },
        )["intent"]
        delivery_calls = []

        def fake_run_delivery(card_folder, root_dir, run_command):
            delivery_calls.append((Path(card_folder), Path(root_dir), run_command))
            return {"ok": True, "result": {"ok": True, "mode": "agent_run"}}

        self.dispatcher.rp_generate_cli._run_delivery = fake_run_delivery

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_command=lambda *args, **kwargs: None)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "postprocess_core_invalid")
        self.assertEqual(delivery_calls, [])
        self.assertEqual(self.intents.list_intents(self.run_dir, "completed"), [])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "blocked")
        self.assertEqual(manifest["dispatcher"]["reason"], "postprocess_core_invalid")

    def test_deliver_round_does_not_wait_for_pending_assets_task(self):
        self._install_dispatcher_dependencies()
        _write_json(self.run_dir / "artifacts" / "critic.report.json", {"decision": "pass"})
        self._write_valid_postprocess_artifact()
        deliver = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "postprocess",
                "type": "deliver_round",
                "payload": {
                    "reason": "postprocess_core_valid",
                    "postprocess_output_path": "artifacts/postprocess.output.json",
                },
            },
        )["intent"]
        asset = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "critic",
                "type": "assets_task",
                "payload": {"kind": "scene", "target": "after_delivery", "prompt": "Optional scene image."},
            },
        )["intent"]

        def fake_run_delivery(_card_folder, _root_dir, _run_command):
            return {"ok": True, "result": {"ok": True, "mode": "agent_run"}}

        self.dispatcher.rp_generate_cli._run_delivery = fake_run_delivery

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_command=lambda *args, **kwargs: None)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "delivered")
        self.assertEqual(result["intent_id"], deliver["id"])
        self.assertEqual([item["id"] for item in self.intents.list_intents(self.run_dir, "completed")], [deliver["id"]])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["id"] for item in pending], [asset["id"]])
        self.assertEqual(pending[0]["type"], "assets_task")

    def test_deliver_round_blocks_when_delivery_command_fails(self):
        self._install_dispatcher_dependencies()
        _write_json(self.run_dir / "artifacts" / "critic.report.json", {"decision": "pass"})
        self._write_valid_postprocess_artifact()
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "postprocess",
                "type": "deliver_round",
                "payload": {
                    "reason": "postprocess_core_valid",
                    "postprocess_output_path": "artifacts/postprocess.output.json",
                },
            },
        )["intent"]

        def fake_run_delivery(_card_folder, _root_dir, _run_command):
            return {"ok": False, "returncode": 1, "result": {"ok": False, "reason": "fixture_delivery_error"}}

        self.dispatcher.rp_generate_cli._run_delivery = fake_run_delivery

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_command=lambda *args, **kwargs: None)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "delivery_failed")
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "blocked")
        self.assertEqual(manifest["dispatcher"]["reason"], "delivery_failed")

    def test_deliver_round_blocks_when_delivery_requests_retry_with_outer_ok(self):
        self._install_dispatcher_dependencies()
        _write_json(self.run_dir / "artifacts" / "critic.report.json", {"decision": "pass"})
        self._write_valid_postprocess_artifact()
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "postprocess",
                "type": "deliver_round",
                "payload": {
                    "reason": "postprocess_core_valid",
                    "postprocess_output_path": "artifacts/postprocess.output.json",
                },
            },
        )["intent"]

        def fake_run_delivery(_card_folder, _root_dir, _run_command):
            return {"ok": True, "result": {"action": "retry", "ok": True}}

        self.dispatcher.rp_generate_cli._run_delivery = fake_run_delivery

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_command=lambda *args, **kwargs: None)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "delivery_failed")
        self.assertEqual(self.intents.list_intents(self.run_dir, "completed"), [])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "blocked")
        self.assertEqual(manifest["dispatcher"]["reason"], "delivery_failed")


if __name__ == "__main__":
    unittest.main()
