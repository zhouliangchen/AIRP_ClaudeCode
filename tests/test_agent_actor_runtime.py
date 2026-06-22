import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentActorRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "round-000001"
        self.run_dir.mkdir()
        self.runtime = load_module("agent_actor_runtime")
        self.messages = load_module("agent_messages")
        self.intents = load_module("agent_intents")

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_request_actor_creates_message_and_request_projection_intent(self):
        call = {
            "call_id": "call-character-Ada-1",
            "actor_id": "character:Ada",
            "prompt": "Listen at the door.",
        }

        message_id, intent_id = self.runtime.record_request_actor(
            self.run_dir,
            "gm",
            "character:Ada",
            call,
        )

        self.assertEqual(message_id, "msg_000001")
        messages = self.messages.read_messages(self.run_dir)
        self.assertEqual(messages[0]["type"], "request_actor")
        self.assertEqual(messages[0]["from"], "gm")
        self.assertEqual(messages[0]["to"], ["projection"])
        self.assertEqual(messages[0]["source_call_id"], "call-character-Ada-1")
        accepted = self.intents.list_intents(self.run_dir, "accepted")
        self.assertEqual([intent["id"] for intent in accepted], [intent_id])
        self.assertEqual(accepted[0]["type"], "request_projection")
        self.assertEqual(accepted[0]["source_message_id"], message_id)
        self.assertEqual(
            accepted[0]["payload"],
            {"actor_id": "character:Ada", "source_call_id": "call-character-Ada-1"},
        )

    def test_record_projected_actor_message_completes_request_projection_intent(self):
        call = {"call_id": "call-character-Ada-1", "prompt": "Listen at the door."}
        _message_id, intent_id = self.runtime.record_request_actor(
            self.run_dir,
            "gm",
            "character:Ada",
            call,
        )
        packet = {"actor_id": "character:Ada", "visible_context": {"scene": "hall"}}

        projected_id = self.runtime.record_projected_actor_message(
            self.run_dir,
            "character:Ada",
            call,
            packet,
            intent_id,
        )

        self.assertEqual(projected_id, "msg_000002")
        completed = self.intents.list_intents(self.run_dir, "completed")
        self.assertEqual([intent["id"] for intent in completed], [intent_id])
        self.assertEqual(completed[0]["type"], "request_projection")
        self.assertEqual(completed[0]["result"]["outputs"], {"projected_message_id": projected_id})
        inbox = self.messages.read_inbox(self.run_dir, "character:Ada")
        self.assertEqual([message["type"] for message in inbox], ["projected_message"])
        self.assertEqual(inbox[0]["payload"]["packet"], packet)

    def test_record_actor_response_creates_gm_only_response_message(self):
        call = {"call_id": "call-character-Ada-1"}
        actor_output = {
            "agent": "character",
            "agent_id": "character:Ada",
            "events": [{"type": "wait_for_gm", "content": "I wait."}],
        }

        response_id = self.runtime.record_actor_response(
            self.run_dir,
            "character:Ada",
            call,
            actor_output,
        )

        self.assertEqual(response_id, "msg_000001")
        messages = self.messages.read_messages(self.run_dir)
        self.assertEqual(messages[0]["type"], "actor_response")
        self.assertEqual(messages[0]["from"], "character:Ada")
        self.assertEqual(messages[0]["to"], ["gm"])
        self.assertEqual(messages[0]["visibility"], "gm_only")
        self.assertEqual(messages[0]["payload"]["output"], actor_output)

    def test_append_actor_output_artifact_preserves_existing_outputs(self):
        artifact = self.run_dir / "artifacts" / "actor.outputs.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text(
            json.dumps({"character:Ada": [{"events": [{"content": "old"}]}]}, ensure_ascii=False),
            encoding="utf-8",
        )
        new_output = {"events": [{"content": "new"}]}

        payload = self.runtime.append_actor_output_artifact(
            self.run_dir,
            "character:Ada",
            new_output,
        )

        self.assertEqual(
            payload,
            {"character:Ada": [{"events": [{"content": "old"}]}, new_output]},
        )
        self.assertEqual(self.runtime.read_actor_outputs_artifact(self.run_dir), payload)

    def test_append_actor_output_artifact_adds_first_actor_output(self):
        actor_output = {"events": [{"content": "first"}]}

        payload = self.runtime.append_actor_output_artifact(
            self.run_dir,
            "character:Bea",
            actor_output,
        )

        self.assertEqual(payload, {"character:Bea": [actor_output]})
        self.assertEqual(self.runtime.read_actor_outputs_artifact(self.run_dir), payload)

    def test_record_request_actor_reports_missing_message_id(self):
        original_append = self.runtime.agent_messages.append_message
        self.runtime.agent_messages.append_message = lambda *_args, **_kwargs: {"ok": True, "message": {}}
        try:
            with self.assertRaisesRegex(
                self.runtime.AgentActorRuntimeError,
                "append request_actor message failed: missing message id",
            ):
                self.runtime.record_request_actor(
                    self.run_dir,
                    "gm",
                    "character:Ada",
                    {"call_id": "call-character-Ada-1"},
                )
        finally:
            self.runtime.agent_messages.append_message = original_append

    def test_record_request_actor_reports_missing_intent_id(self):
        original_create = self.runtime.agent_intents.create_intent
        self.runtime.agent_intents.create_intent = lambda *_args, **_kwargs: {"ok": True, "intent": {}}
        try:
            with self.assertRaisesRegex(
                self.runtime.AgentActorRuntimeError,
                "create request_projection intent failed: missing intent id",
            ):
                self.runtime.record_request_actor(
                    self.run_dir,
                    "gm",
                    "character:Ada",
                    {"call_id": "call-character-Ada-1"},
                )
        finally:
            self.runtime.agent_intents.create_intent = original_create


if __name__ == "__main__":
    unittest.main()
