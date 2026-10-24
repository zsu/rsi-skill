"""Toy project's own test suite (a static gate for rsi-skill)."""
import sys
import unittest
from pathlib import Path

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent), pattern="test_*.py")
    ok = unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful()
    sys.exit(0 if ok else 1)
