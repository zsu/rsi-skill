"""A stand-in for the `claude` CLI used by the tests.

--help prints the flags run_case.py probes for. Otherwise it reads the prompt from stdin, writes
out.txt (the prompt) into the cwd, and prints a stream-json transcript with one Skill tool call
and a result event. FAKE_CLAUDE_SLEEP makes it hang (timeout test); FAKE_CLAUDE_FAIL exits 1.
"""
import json
import os
import sys
import time

if "--help" in sys.argv:
    print("--no-session-persistence --max-budget-usd --settings --permission-mode --plugin-dir --setting-sources")
    sys.exit(0)
if sys.argv[1:3] == ["auth", "status"]:
    print(json.dumps({"loggedIn": not os.environ.get("FAKE_CLAUDE_LOGGED_OUT"), "authMethod": "fake"}))
    sys.exit(0)
prompt = sys.stdin.read()
if os.environ.get("FAKE_CLAUDE_SLEEP"):
    time.sleep(float(os.environ["FAKE_CLAUDE_SLEEP"]))
with open("out.txt", "w", encoding="utf-8") as f:
    f.write(prompt)
with open("argv.json", "w", encoding="utf-8") as f:
    json.dump(sys.argv[1:], f)
events = [
    {"type": "system", "subtype": "init", "skills": ["csv-summary"]},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Working on it."},
        {"type": "tool_use", "name": "Skill", "input": {"skill": "csv-summary"}},
        {"type": "tool_use", "name": "Bash", "input": {"command": "python summarize.py"}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok", "is_error": False}]}},
    {"type": "result", "subtype": "success", "result": "Done. Median is 4.5.", "total_cost_usd": 0.0123,
     "num_turns": 3, "duration_api_ms": 1000,
     "usage": {"input_tokens": 100, "output_tokens": 50, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 1000}},
]
for e in events:
    print(json.dumps(e), flush=True)
sys.exit(1 if os.environ.get("FAKE_CLAUDE_FAIL") else 0)
