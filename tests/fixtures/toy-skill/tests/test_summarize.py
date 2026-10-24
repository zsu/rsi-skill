import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "csv-summary" / "scripts" / "summarize.py"
spec = importlib.util.spec_from_file_location("summarize", SCRIPT)
summarize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summarize)


class TestSummarize(unittest.TestCase):
    def test_odd_count(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.csv"
            p.write_text("a,b\n1,10\n2,20\n3,30\n", encoding="utf-8")
            s = summarize.summarize(p)
            self.assertEqual(s["a"]["median"], 2)
            self.assertEqual(s["b"]["mean"], 20)
            self.assertEqual(s["a"]["count"], 3)


if __name__ == "__main__":
    unittest.main()
