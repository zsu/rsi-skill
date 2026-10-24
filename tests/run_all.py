"""Run the rsi-skill test suite: python -I tests/run_all.py [-k pattern]"""
import sys
import unittest
from pathlib import Path

if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))
    loader = unittest.defaultTestLoader
    if len(sys.argv) > 2 and sys.argv[1] == "-k":
        loader.testNamePatterns = [f"*{sys.argv[2]}*"]
    suite = loader.discover(str(here), pattern="test_*.py", top_level_dir=str(here))
    ok = unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful()
    sys.exit(0 if ok else 1)
