#!/usr/bin/env python3
"""Tests for token_usage.py. Run: python3 scripts/test_token_usage.py -v"""
import json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import token_usage as tu


def entry(msg_id, ts, out=100, inp=10, cc=50, cr=1000, model="claude-sonnet-5"):
    msg = {"id": msg_id,
           "usage": {"input_tokens": inp, "output_tokens": out,
                     "cache_creation_input_tokens": cc,
                     "cache_read_input_tokens": cr}}
    if model is not None:
        msg["model"] = model
    return json.dumps({"type": "assistant", "timestamp": ts, "message": msg})


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

    def test_display_name(self):
        self.assertEqual(tu.display_name("claude-haiku-4-5-20251001"), "Haiku 4.5")
        self.assertEqual(tu.display_name("claude-fable-5"), "Fable 5")
        self.assertEqual(tu.display_name("claude-opus-4-8"), "Opus 4.8")
        self.assertEqual(tu.display_name("claude-sonnet-5"), "Sonnet 5")
        self.assertEqual(tu.display_name("claude-3-5-sonnet-20241022"), "Sonnet 3.5")
        self.assertEqual(tu.display_name("haiku"), "Haiku")  # dispatch alias, no version
        self.assertEqual(tu.display_name(""), "")            # falsy passes through
        self.assertIsNone(tu.display_name(None))
        # 1M-context annotation and Bedrock/Vertex vendor prefixes / deployment suffixes
        self.assertEqual(tu.display_name("claude-opus-4-8[1m]"), "Opus 4.8")
        self.assertEqual(tu.display_name("us.anthropic.claude-sonnet-4-20250514-v1:0"), "Sonnet 4")
        self.assertEqual(tu.display_name("eu.anthropic.claude-haiku-4-5-20251001-v1:0"), "Haiku 4.5")

    def test_models_per_window_ordered_by_output(self):
        # Task 1's window gets a haiku subentry with MORE output than the sonnet entry.
        with open(self.transcript, "a", encoding="utf-8") as f:
            f.write(entry("m3", "2026-07-16T10:06:00.000Z", out=500,
                          model="claude-haiku-4-5-20251001") + "\n")
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertTrue(res["available"])
        self.assertEqual([m["id"] for m in res["tasks"][0]["models"]],
                         ["claude-haiku-4-5-20251001", "claude-sonnet-5"])
        self.assertEqual(res["tasks"][0]["models"][0]["display"], "Haiku 4.5")
        self.assertEqual(res["tasks"][1]["models"],
                         [{"id": "claude-sonnet-5", "display": "Sonnet 5"}])
        # Job level: haiku 500 vs sonnet 200+300=500 — tie broken alphabetically by id.
        self.assertEqual([m["display"] for m in res["job"]["models"]],
                         ["Haiku 4.5", "Sonnet 5"])

    def test_synthetic_model_excluded_from_models_but_counted_in_totals(self):
        # M19: a real "<synthetic>" error-placeholder entry (nonzero output, e.g. a
        # partial output before an API error) must not appear in by_model/models,
        # but its tokens still count in the window/job totals.
        with open(self.transcript, "a", encoding="utf-8") as f:
            f.write(entry("m6", "2026-07-16T10:06:00.000Z", out=42, inp=5, cc=0,
                          model="<synthetic>") + "\n")
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertTrue(res["available"])
        self.assertEqual(res["tasks"][0]["output"], 200 + 42)   # counted in totals
        ids = [m["id"] for m in res["tasks"][0]["models"]]
        self.assertNotIn("<synthetic>", ids)                    # excluded from models

    def test_zero_output_and_zero_input_entry_excluded_from_models(self):
        # M19: any model with zero output AND zero input (synthetic/placeholder shape)
        # is dropped from by_model/models even if it isn't literally "<synthetic>".
        with open(self.transcript, "a", encoding="utf-8") as f:
            f.write(entry("m7", "2026-07-16T10:06:30.000Z", out=0, inp=0, cc=0,
                          model="claude-placeholder") + "\n")
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertTrue(res["available"])
        ids = [m["id"] for m in res["tasks"][0]["models"]]
        self.assertNotIn("claude-placeholder", ids)

    def test_entry_without_model_field_tolerated(self):
        # Missing or non-string model: tokens still counted, models list unaffected.
        with open(self.transcript, "a", encoding="utf-8") as f:
            f.write(entry("m4", "2026-07-16T10:06:30.000Z", out=10, model=None) + "\n")
            f.write(json.dumps({"type": "assistant",
                                "timestamp": "2026-07-16T10:07:00.000Z",
                                "message": {"id": "m5", "model": 123,
                                            "usage": {"output_tokens": 7}}}) + "\n")
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertTrue(res["available"])
        self.assertEqual(res["tasks"][0]["output"], 200 + 10 + 7)
        self.assertEqual([m["id"] for m in res["tasks"][0]["models"]], ["claude-sonnet-5"])


if __name__ == "__main__":
    unittest.main()
