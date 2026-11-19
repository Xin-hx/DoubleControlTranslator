import json
from pathlib import Path
import tempfile
import unittest

from double_ctrl_translator.config import Config


class ConfigTests(unittest.TestCase):
    def test_unknown_field_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as d:
            p=Path(d)/"c.json"; p.write_text(json.dumps({"endpoint":"https://cloud.example"}))
            with self.assertRaises(ValueError): Config.load(p)

    def test_defaults_are_safe(self):
        c=Config.load(None); self.assertLessEqual(c.max_chars,2000); self.assertEqual(c.model,"Qwen3.5-4B-Q4_K_M.gguf")

    def test_larger_model_cannot_be_selected(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as d:
            p=Path(d)/"c.json"; p.write_text(json.dumps({"model":"Qwen3.5-27B"}))
            with self.assertRaises(ValueError): Config.load(p)


if __name__ == "__main__": unittest.main()
