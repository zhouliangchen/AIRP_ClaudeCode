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


class ObjectiveWorldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.card.mkdir()
        self.objective_world = load_module("objective_world")

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_missing_returns_empty_archive(self):
        self.assertEqual(
            self.objective_world.read_objective_world(self.card),
            {"facts": [], "sources": []},
        )

    def test_append_fact_persists_nonblank_record(self):
        payload = self.objective_world.append_fact(
            self.card,
            scope="archive",
            fact="The locked door leads to a moon base.",
            source="gm.output.json",
        )

        path = self.card / "memory" / "objective_world.json"
        self.assertTrue(path.exists())
        self.assertTrue(path.read_text(encoding="utf-8").endswith("\n"))
        self.assertEqual(
            payload["facts"],
            [
                {
                    "scope": "archive",
                    "fact": "The locked door leads to a moon base.",
                    "source": "gm.output.json",
                }
            ],
        )
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)

    def test_append_fact_ignores_blank_fact(self):
        payload = self.objective_world.append_fact(
            self.card,
            scope="archive",
            fact="  ",
            source="gm.output.json",
        )

        self.assertEqual(payload, {"facts": [], "sources": []})
        self.assertFalse((self.card / "memory" / "objective_world.json").exists())

    def test_read_invalid_json_returns_empty_facts_with_diagnostic_source(self):
        path = self.card / "memory" / "objective_world.json"
        path.parent.mkdir(parents=True)
        path.write_text("{bad json", encoding="utf-8")

        payload = self.objective_world.read_objective_world(self.card)

        self.assertEqual(payload["facts"], [])
        self.assertEqual(payload["sources"][0]["type"], "diagnostic")
        self.assertEqual(payload["sources"][0]["reason"], "invalid_json")

    def test_read_non_object_returns_empty_facts_with_diagnostic_source(self):
        path = self.card / "memory" / "objective_world.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

        payload = self.objective_world.read_objective_world(self.card)

        self.assertEqual(payload["facts"], [])
        self.assertEqual(payload["sources"][0]["type"], "diagnostic")
        self.assertEqual(payload["sources"][0]["reason"], "non_object")

    def test_read_normalizes_facts_and_sources_to_lists(self):
        path = self.card / "memory" / "objective_world.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"facts": {"fact": "single"}, "sources": "source"}, ensure_ascii=False),
            encoding="utf-8",
        )

        self.assertEqual(
            self.objective_world.read_objective_world(self.card),
            {"facts": [{"fact": "single"}], "sources": ["source"]},
        )


if __name__ == "__main__":
    unittest.main()
