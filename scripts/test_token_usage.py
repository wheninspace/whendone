#!/usr/bin/env python3
"""Tests for token_usage.py. Run: python3 scripts/test_token_usage.py -v"""
import json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import token_usage as tu


def entry(msg_id, ts, out=100, inp=10, cc=50, cr=1000):
    return json.dumps({"type": "assistant", "timestamp": ts,
                       "message": {"id": msg_id, "model": "claude-sonnet-5",
                                   "usage": {"input_tokens": inp, "output_tokens": out,
                                             "cache_creation_input_tokens": cc,
                                             "cache_read_input_tokens": cr}}})


STATE = {"sessionIds": ["sess1"], "tasks": [
    {"nr": 1, "startedAt": "2026-07-16T10:00:00+00:00", "finishedAt": "2026-07-16T10:10:00+00:00"},
    {"nr": 2, "startedAt": "2026-07-16T10:10:00+00:00", "finishedAt": None},
]}


class TestTokenUsage(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        proj = os.path.join(self.td.name, "projects", "slug")
        os.makedirs(proj)
        self.transcript = os.path.join(proj, "sess1.jsonl")
        with open(self.transcript, "w", encoding="utf-8") as f:
            f.write(entry("m1", "2026-07-16T10:05:00.000Z", out=50) + "\n")
            f.write(entry("m1", "2026-07-16T10:05:01.000Z", out=200) + "\n")  # dup: keep last
            f.write(entry("m2", "2026-07-16T10:15:00.000Z", out=300) + "\n")
            f.write("garbage line\n")
        self.state = os.path.join(self.td.name, "state.json")
        json.dump(STATE, open(self.state, "w"))

    def tearDown(self):
        self.td.cleanup()

    def test_dedup_and_windowing(self):
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertTrue(res["available"])
        self.assertEqual(res["tasks"][0]["output"], 200)   # dup collapsed, last kept
        self.assertEqual(res["tasks"][1]["output"], 300)   # open-ended in-flight window
        self.assertEqual(res["job"]["output"], 500)
        self.assertEqual(res["job"]["freshInput"], 10 + 50 + 10 + 50)

    def test_missing_transcript_degrades(self):
        json.dump({"sessionIds": ["nope"], "tasks": []}, open(self.state, "w"))
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertFalse(res["available"])

    def test_path_traversal_sid_rejected(self):
        # Untrusted state file (e.g. from a cloned repo) ships a malicious sessionId
        # designed to escape projects_dir via glob. It must be filtered out before
        # ever reaching glob.glob, so no transcript is found and the job degrades.
        json.dump({"sessionIds": ["../../../../etc/passwd"], "tasks": []}, open(self.state, "w"))
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertEqual(res, {"available": False, "reason": "no transcript found for session ids"})

    def test_tasks_not_a_list_degrades_gracefully(self):
        # Malformed untrusted state file: "tasks" is a string instead of a list.
        # Must not raise — must return a valid {"available": ...} dict.
        json.dump({"sessionIds": ["sess1"], "tasks": "not-a-list"}, open(self.state, "w"))
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertIsInstance(res, dict)
        self.assertIn("available", res)

    def test_tasks_with_non_dict_item_degrades_gracefully(self):
        # Malformed untrusted state file: "tasks" is a list, but contains a non-dict item.
        # Must not raise — must return a valid {"available": ...} dict.
        json.dump({"sessionIds": ["sess1"], "tasks": ["oops"]}, open(self.state, "w"))
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertIsInstance(res, dict)
        self.assertIn("available", res)

    def test_task_with_non_string_started_at_degrades_gracefully(self):
        # Malformed untrusted state file: a task dict has a non-string startedAt
        # (e.g. an integer timestamp). parse_ts must not raise.
        json.dump({"sessionIds": ["sess1"],
                   "tasks": [{"nr": 1, "startedAt": 12345, "finishedAt": None}]},
                  open(self.state, "w"))
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertIsInstance(res, dict)
        self.assertIn("available", res)


if __name__ == "__main__":
    unittest.main()
