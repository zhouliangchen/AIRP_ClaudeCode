import importlib.util
import sys
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


def call(call_id, actor_id, source_call_id=""):
    payload = {
        "call_id": call_id,
        "actor_id": actor_id,
        "prompt": f"Prompt for {actor_id}",
        "reason": "test",
    }
    if source_call_id:
        payload["source_call_id"] = source_call_id
    return payload


class ActorBatchPlannerTest(unittest.TestCase):
    def setUp(self):
        self.batches = load_module("agent_actor_batches")

    def test_parallel_group_is_chunked_by_max_parallel_and_preserves_order(self):
        calls = [
            call("call-character-Ada-1", "character:Ada"),
            call("call-character-Bea-1", "character:Bea"),
            call("call-character-Cora-1", "character:Cora"),
            call("call-character-Dana-1", "character:Dana"),
        ]

        plan = self.batches.build_actor_batches(
            calls,
            [{"group_id": "group-main", "actors": ["character:Ada", "character:Bea", "character:Cora"]}],
            max_parallel=2,
        )

        self.assertEqual(plan["warnings"], [])
        self.assertEqual(
            [
                (batch["kind"], batch["group_id"], [item["actor_id"] for item in batch["calls"]])
                for batch in plan["batches"]
            ],
            [
                ("parallel", "group-main", ["character:Ada", "character:Bea"]),
                ("serial", "group-main", ["character:Cora"]),
                ("serial", "", ["character:Dana"]),
            ],
        )

    def test_duplicate_actor_group_downgrades_to_serial_with_warning(self):
        calls = [
            call("call-character-SuLi-1", "character:SuLi"),
            call("call-character-SuLi-2", "character:SuLi"),
        ]

        plan = self.batches.build_actor_batches(
            calls,
            [{"group_id": "group-duplicate", "call_ids": ["call-character-SuLi-1", "call-character-SuLi-2"]}],
            max_parallel=2,
        )

        self.assertEqual(
            [(batch["kind"], [item["call_id"] for item in batch["calls"]]) for batch in plan["batches"]],
            [
                ("serial", ["call-character-SuLi-1"]),
                ("serial", ["call-character-SuLi-2"]),
            ],
        )
        self.assertEqual(plan["warnings"][0]["code"], "duplicate_actor_in_parallel_group")
        self.assertEqual(plan["warnings"][0]["group_id"], "group-duplicate")

    def test_dependent_call_group_downgrades_to_serial_with_warning(self):
        calls = [
            call("call-character-Ada-1", "character:Ada", source_call_id="call-player-1"),
            call("call-character-Bea-1", "character:Bea"),
        ]

        plan = self.batches.build_actor_batches(
            calls,
            [{"group_id": "group-dependent", "actors": ["character:Ada", "character:Bea"]}],
            max_parallel=2,
        )

        self.assertEqual(
            [(batch["kind"], [item["actor_id"] for item in batch["calls"]]) for batch in plan["batches"]],
            [
                ("serial", ["character:Ada"]),
                ("serial", ["character:Bea"]),
            ],
        )
        self.assertEqual(plan["warnings"][0]["code"], "dependent_call_in_parallel_group")

    def test_unknown_group_member_downgrades_without_losing_calls(self):
        calls = [
            call("call-character-Ada-1", "character:Ada"),
            call("call-character-Bea-1", "character:Bea"),
        ]

        plan = self.batches.build_actor_batches(
            calls,
            [{"group_id": "group-missing", "actors": ["character:Ada", "character:Cora"]}],
            max_parallel=2,
        )

        self.assertEqual(
            [(batch["kind"], [item["actor_id"] for item in batch["calls"]]) for batch in plan["batches"]],
            [
                ("serial", ["character:Ada"]),
                ("serial", ["character:Bea"]),
            ],
        )
        self.assertEqual(plan["warnings"][0]["code"], "unknown_parallel_group_member")

    def test_scalar_actors_group_downgrades_to_serial_with_warning(self):
        calls = [
            call("call-character-Ada-1", "character:Ada"),
            call("call-character-Bea-1", "character:Bea"),
        ]

        plan = self.batches.build_actor_batches(
            calls,
            [{"group_id": "group-scalar-actors", "actors": 123}],
            max_parallel=2,
        )

        self.assertEqual(
            [(batch["kind"], [item["actor_id"] for item in batch["calls"]]) for batch in plan["batches"]],
            [
                ("serial", ["character:Ada"]),
                ("serial", ["character:Bea"]),
            ],
        )
        self.assertEqual(plan["warnings"][0]["code"], "empty_parallel_group")
        self.assertEqual(plan["warnings"][0]["group_id"], "group-scalar-actors")

    def test_scalar_call_ids_group_downgrades_to_serial_with_warning(self):
        calls = [
            call("call-character-Ada-1", "character:Ada"),
            call("call-character-Bea-1", "character:Bea"),
        ]

        plan = self.batches.build_actor_batches(
            calls,
            [{"group_id": "group-scalar-call-ids", "call_ids": 123}],
            max_parallel=2,
        )

        self.assertEqual(
            [(batch["kind"], [item["actor_id"] for item in batch["calls"]]) for batch in plan["batches"]],
            [
                ("serial", ["character:Ada"]),
                ("serial", ["character:Bea"]),
            ],
        )
        self.assertEqual(plan["warnings"][0]["code"], "empty_parallel_group")
        self.assertEqual(plan["warnings"][0]["group_id"], "group-scalar-call-ids")

    def test_nonnumeric_direct_max_parallel_defaults_safely(self):
        calls = [
            call("call-character-Ada-1", "character:Ada"),
            call("call-character-Bea-1", "character:Bea"),
            call("call-character-Cora-1", "character:Cora"),
        ]

        plan = self.batches.build_actor_batches(
            calls,
            [{"group_id": "group-main", "actors": ["character:Ada", "character:Bea"]}],
            max_parallel="bad",
        )

        self.assertEqual(plan["warnings"], [])
        self.assertEqual(
            [
                (batch["kind"], batch["group_id"], [item["actor_id"] for item in batch["calls"]])
                for batch in plan["batches"]
            ],
            [
                ("parallel", "group-main", ["character:Ada", "character:Bea"]),
                ("serial", "", ["character:Cora"]),
            ],
        )

    def test_max_parallel_from_input_reads_card_orchestration_and_defaults_to_two(self):
        self.assertEqual(
            self.batches.max_parallel_from_input({
                "card_data": {"character_orchestration": {"max_parallel_subagents": 3}}
            }),
            3,
        )
        self.assertEqual(self.batches.max_parallel_from_input({"card_data": {}}), 2)
        self.assertEqual(
            self.batches.max_parallel_from_input({
                "card_data": {"character_orchestration": {"max_parallel_subagents": 0}}
            }),
            1,
        )


if __name__ == "__main__":
    unittest.main()
