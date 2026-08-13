#!/usr/bin/env python3
"""Tests for token_usage.py. Run: python3 scripts/test_token_usage.py -v"""
import json, os, sys, tempfile, unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import token_usage as tu


class ParseTsTimezoneTest(unittest.TestCase):
    """Local-time policy: timestamps the scripts emit carry the system-local
    UTC offset, never a hardcoded +00:00. parse_ts anchors that — same
    instant, local offset (trivially equal on a UTC machine)."""

    def test_parse_ts_preserves_instant_in_local_offset(self):
        dt = tu.parse_ts("2026-07-18T10:00:00.000Z")
        ref = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(dt, ref)                                    # same instant
        self.assertEqual(dt.utcoffset(), ref.astimezone().utcoffset())  # local offset


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
        with open(self.state, "w") as f:
            json.dump(STATE, f)

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
        with open(self.state, "w") as f:
            json.dump({"sessionIds": ["nope"], "tasks": []}, f)
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertFalse(res["available"])

    def test_path_traversal_sid_rejected(self):
        # Untrusted state file (e.g. from a cloned repo) ships a malicious sessionId
        # designed to escape projects_dir via glob. It must be filtered out before
        # ever reaching glob.glob, so no transcript is found and the job degrades.
        with open(self.state, "w") as f:
            json.dump({"sessionIds": ["../../../../etc/passwd"], "tasks": []}, f)
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertEqual(res, {"available": False, "reason": "no transcript found for session ids"})

    def test_tasks_not_a_list_degrades_gracefully(self):
        # Malformed untrusted state file: "tasks" is a string instead of a list.
        # Must not raise — must return a valid {"available": ...} dict.
        with open(self.state, "w") as f:
            json.dump({"sessionIds": ["sess1"], "tasks": "not-a-list"}, f)
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertIsInstance(res, dict)
        self.assertIn("available", res)

    def test_tasks_with_non_dict_item_degrades_gracefully(self):
        # Malformed untrusted state file: "tasks" is a list, but contains a non-dict item.
        # Must not raise — must return a valid {"available": ...} dict.
        with open(self.state, "w") as f:
            json.dump({"sessionIds": ["sess1"], "tasks": ["oops"]}, f)
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertIsInstance(res, dict)
        self.assertIn("available", res)

    def test_task_with_non_string_started_at_degrades_gracefully(self):
        # Malformed untrusted state file: a task dict has a non-string startedAt
        # (e.g. an integer timestamp). parse_ts must not raise.
        with open(self.state, "w") as f:
            json.dump({"sessionIds": ["sess1"],
                       "tasks": [{"nr": 1, "startedAt": 12345, "finishedAt": None}]},
                      f)
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

    # -- C2: job/subagents buckets are bounded by the job's startedAt --------------

    def test_job_bucket_excludes_entries_before_job_start(self):
        # The session transcript has a big entry BEFORE the job's own startedAt (unrelated
        # prior work in the same session). The job/subagents headline must exclude it, even
        # though the un-bounded old behavior would have counted it.
        state = dict(STATE)
        state["startedAt"] = "2026-07-16T10:00:00+00:00"
        with open(self.transcript, "a", encoding="utf-8") as f:
            f.write(entry("pre1", "2026-07-16T09:00:00.000Z", out=99999) + "\n")
        with open(self.state, "w") as f:
            json.dump(state, f)
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertTrue(res["available"])
        self.assertEqual(res["job"]["output"], 500)  # unchanged: pre-job entry excluded

    def test_job_bucket_falls_back_to_whole_session_without_state_started_at(self):
        # No job startedAt in state (old/malformed state file): degrade gracefully to the
        # old whole-session behavior rather than failing.
        state = dict(STATE)
        state.pop("startedAt", None)
        with open(self.state, "w") as f:
            json.dump(state, f)
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertTrue(res["available"])
        self.assertEqual(res["job"]["output"], 500)

    # -- C13: --task N mode ---------------------------------------------------------

    def test_task_filter_emits_only_requested_task(self):
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"),
                            task_nr=1)
        self.assertTrue(res["available"])
        self.assertIn("job", res)
        self.assertIn("subagents", res)
        self.assertEqual(len(res["tasks"]), 1)
        self.assertEqual(res["tasks"][0]["nr"], 1)
        self.assertEqual(res["tasks"][0]["output"], 200)

    def test_task_filter_unknown_nr_emits_empty_task_list(self):
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"),
                            task_nr=999)
        self.assertTrue(res["available"])
        self.assertEqual(res["tasks"], [])

    def test_cli_task_flag(self):
        import subprocess
        fake_home = tempfile.TemporaryDirectory()
        try:
            proj = os.path.join(fake_home.name, ".claude", "projects", "slug")
            os.makedirs(proj)
            with open(os.path.join(proj, "sessA.jsonl"), "w", encoding="utf-8") as f:
                f.write(entry("cli1", "2026-07-16T10:05:00.000Z", out=42) + "\n")
            cli_state = os.path.join(fake_home.name, "state.json")
            with open(cli_state, "w") as f:
                json.dump({"sessionIds": ["sessA"], "tasks": [
                    {"nr": 1, "startedAt": "2026-07-16T10:00:00+00:00",
                     "finishedAt": "2026-07-16T10:10:00+00:00"},
                    {"nr": 2, "startedAt": "2026-07-16T10:10:00+00:00", "finishedAt": None},
                ]}, f)
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token_usage.py")
            out = subprocess.run(
                [sys.executable, script, cli_state, "--task", "1"],
                # HOME steers expanduser on POSIX, USERPROFILE on Windows (3.8+)
                env={**os.environ, "HOME": fake_home.name,
                     "USERPROFILE": fake_home.name},
                capture_output=True, text=True, check=True,
            )
            res = json.loads(out.stdout)
            self.assertTrue(res["available"])
            self.assertEqual(len(res["tasks"]), 1)
            self.assertEqual(res["tasks"][0]["nr"], 1)
        finally:
            fake_home.cleanup()

    # -- M18: dedup across transcript files, not just within one file ---------------

    def test_dedup_across_session_files(self):
        # A resumed job accumulates a second sessionIds entry with its own transcript file.
        # The SAME message id appears in both files (hypothetical replay) — it must be
        # counted once, not once per file.
        proj = os.path.join(self.td.name, "projects", "slug")
        transcript2 = os.path.join(proj, "sess2.jsonl")
        with open(transcript2, "w", encoding="utf-8") as f:
            f.write(entry("m1", "2026-07-16T10:20:00.000Z", out=777) + "\n")
        state = dict(STATE)
        state["sessionIds"] = ["sess1", "sess2"]
        with open(self.state, "w") as f:
            json.dump(state, f)
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertTrue(res["available"])
        # m1 appears in both sess1.jsonl (out=200, kept-last within file) and sess2.jsonl
        # (out=777) — cross-file dedup keeps exactly one value for id "m1", not both.
        self.assertEqual(res["job"]["output"], 777 + 300)

    # -- M1: overlap detection between task windows ---------------------------------

    def test_overlap_flag_set_for_identical_windows(self):
        state = {"sessionIds": ["sess1"], "startedAt": "2026-07-16T10:00:00+00:00",
                 "tasks": [
                     {"nr": 1, "startedAt": "2026-07-16T10:00:00+00:00",
                      "finishedAt": "2026-07-16T10:10:00+00:00"},
                     {"nr": 2, "startedAt": "2026-07-16T10:00:00+00:00",
                      "finishedAt": "2026-07-16T10:10:00+00:00"},
                 ]}
        with open(self.state, "w") as f:
            json.dump(state, f)
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertTrue(res["available"])
        self.assertTrue(res["tasks"][0].get("overlap"))
        self.assertTrue(res["tasks"][1].get("overlap"))

    def test_overlap_flag_set_for_partially_overlapping_windows(self):
        state = {"sessionIds": ["sess1"], "startedAt": "2026-07-16T10:00:00+00:00",
                 "tasks": [
                     {"nr": 1, "startedAt": "2026-07-16T10:00:00+00:00",
                      "finishedAt": "2026-07-16T10:10:00+00:00"},
                     {"nr": 2, "startedAt": "2026-07-16T10:05:00+00:00",
                      "finishedAt": "2026-07-16T10:15:00+00:00"},
                 ]}
        with open(self.state, "w") as f:
            json.dump(state, f)
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertTrue(res["available"])
        self.assertTrue(res["tasks"][0].get("overlap"))
        self.assertTrue(res["tasks"][1].get("overlap"))

    def test_no_overlap_flag_for_sequential_windows(self):
        # STATE's two tasks are back-to-back (task 2 starts exactly when task 1 ends) —
        # [start, end) semantics means they do NOT overlap.
        res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        self.assertTrue(res["available"])
        self.assertNotIn("overlap", res["tasks"][0])
        self.assertNotIn("overlap", res["tasks"][1])

    # -- N3: input-size bound ---------------------------------------------------------

    def test_oversized_transcript_degrades_to_unavailable(self):
        orig_bytes = tu.MAX_TRANSCRIPT_BYTES
        tu.MAX_TRANSCRIPT_BYTES = 10  # tiny cap so our small fixture file already exceeds it
        try:
            res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        finally:
            tu.MAX_TRANSCRIPT_BYTES = orig_bytes
        self.assertEqual(res, {"available": False, "reason": "transcript exceeds size cap"})

    def test_oversized_transcript_by_line_count_degrades_to_unavailable(self):
        orig_lines = tu.MAX_TRANSCRIPT_LINES
        tu.MAX_TRANSCRIPT_LINES = 2  # our fixture file has more than 2 lines
        try:
            res = tu.summarize(self.state, projects_dir=os.path.join(self.td.name, "projects"))
        finally:
            tu.MAX_TRANSCRIPT_LINES = orig_lines
        self.assertEqual(res, {"available": False, "reason": "transcript exceeds size cap"})


class TranscriptPathsTest(unittest.TestCase):
    def test_finds_main_and_subagents_and_filters_bad_sids(self):
        with tempfile.TemporaryDirectory() as d:
            proj = os.path.join(d, "proj-x")
            os.makedirs(os.path.join(proj, "sid1", "subagents"))
            open(os.path.join(proj, "sid1.jsonl"), "w").close()
            open(os.path.join(proj, "sid1", "subagents", "agent-a.jsonl"), "w").close()
            got = tu.transcript_paths(["sid1", "../evil", ""], d)
            self.assertEqual(len(got), 1)
            main, subs = got[0]
            self.assertTrue(main.endswith("sid1.jsonl"))
            self.assertEqual(len(subs), 1)
            self.assertTrue(subs[0].endswith("agent-a.jsonl"))

    def test_no_match_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(tu.transcript_paths(["sidz"], d), [])

    def test_includes_workflow_agent_transcripts(self):
        with tempfile.TemporaryDirectory() as d:
            proj = os.path.join(d, "proj-x")
            wfdir = os.path.join(proj, "sid1", "subagents", "workflows", "wf_ab12-cd")
            os.makedirs(os.path.join(proj, "sid1", "subagents"), exist_ok=True)
            os.makedirs(wfdir)
            open(os.path.join(proj, "sid1.jsonl"), "w").close()
            open(os.path.join(proj, "sid1", "subagents", "agent-a.jsonl"), "w").close()
            open(os.path.join(wfdir, "agent-b.jsonl"), "w").close()
            _, subs = tu.transcript_paths(["sid1"], d)[0]
            self.assertEqual(len(subs), 2)
            self.assertTrue(any("workflows" in s for s in subs))


if __name__ == "__main__":
    unittest.main()
