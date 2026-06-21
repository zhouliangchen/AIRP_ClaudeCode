import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("io_utils", ROOT / "skills" / "io_utils.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IoUtilsTest(unittest.TestCase):
    def setUp(self):
        self.io_utils = _load_module()

    def test_read_json_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            payload = json.dumps({"modelDebugMode": True}, ensure_ascii=False)
            path.write_bytes(payload.encode("utf-8-sig"))

            result = self.io_utils.read_json(path)

        self.assertEqual(result, {"modelDebugMode": True})


if __name__ == "__main__":
    unittest.main()
