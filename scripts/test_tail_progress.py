#!/usr/bin/env python3
"""Tests for tail_progress.py. Run: python3 scripts/test_tail_progress.py -v"""
import contextlib, io, json, os, sys, tempfile, unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tail_progress as tp

T0 = "2026-07-18T10:00:00.000Z"
T1 = "2026-07-18T10:05:00.000Z"
T2 = "2026-07-18T10:12:00.000Z"


def todo_entry(ts, todos, mid="m-todo"):
    """Assistant entry with one TodoWrite tool_use — the shape verified live 2026-07-18."""
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": mid, "usage": {},
                        "content": [{"type": "tool_use", "id": "tu-" + mid,
                                     "name": "TodoWrite", "input": {"todos": todos}}]}}


def dispatch_entry(ts, tool_id, description, name="Agent"):
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": "m-" + tool_id, "usage": {},
                        "content": [{"type": "tool_use", "id": tool_id, "name": name,
                                     "input": {"description": description, "prompt": "..."}}]}}


def result_entry(ts, tool_id):
    return {"type": "user", "timestamp": ts,
            "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id,
                                     "content": "done"}]}}


def item(content, status):
    return {"content": content, "status": status, "activeForm": content}


def write_jsonl(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write((e if isinstance(e, str) else json.dumps(e)) + "\n")


_MODULE_CALIB = None


def setUpModule():
    """No test in this module may ever touch the real ~/.claude/whendone-data
    (append_calibration honors WHENDONE_DATA_DIR). Classes that need their own
    isolated dir re-point the var in setUp and restore THIS default in tearDown."""
    global _MODULE_CALIB
    _MODULE_CALIB = tempfile.TemporaryDirectory()
    os.environ["WHENDONE_DATA_DIR"] = _MODULE_CALIB.name


def tearDownModule():
    os.environ.pop("WHENDONE_DATA_DIR", None)
    _MODULE_CALIB.cleanup()


class NormalizeTest(unittest.TestCase):
    def test_casefold_and_whitespace(self):
        self.assertEqual(tp.normalize("  Build   the Tailer "), "build the tailer")

    def test_strips_one_leading_ordinal(self):
        for raw in ("3. Build tailer", "3) Build tailer", "3: Build tailer",
                    "Task 3: Build tailer", "task 12. Build tailer"):
            self.assertEqual(tp.normalize(raw), "build tailer", raw)

    def test_non_string_is_empty(self):
        self.assertEqual(tp.normalize(None), "")
        self.assertEqual(tp.normalize(7), "")


class NameIndexTest(unittest.TestCase):
    def test_maps_names_and_marks_duplicates_ambiguous(self):
        tasks = [{"nr": 1, "name": "Alpha"}, {"nr": 2, "name": "Beta"},
                 {"nr": 3, "name": "alpha"}]
        idx = tp.name_index(tasks)
        self.assertIsNone(idx["alpha"])      # duplicate → ambiguous → unmatchable
        self.assertEqual(idx["beta"], 2)

    def test_skips_malformed_tasks(self):
        idx = tp.name_index([{"nr": 1}, "junk", {"name": "x", "nr": 4}])
        self.assertEqual(idx, {"x": 4})


class ExtractEventsTest(unittest.TestCase):
    def test_extracts_todos_dispatch_result_in_ts_order(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.jsonl")
            write_jsonl(p, [
                result_entry(T2, "tu-9"),                       # out of order on disk
                todo_entry(T0, [item("Alpha", "in_progress")]),
                dispatch_entry(T1, "tu-9", "Beta"),
                "{not json",                                    # skipped, no exception
                json.dumps({"type": "assistant", "message": {}}),  # no timestamp -> skipped
            ])
            events, last_ts = tp.extract_events([p])
            self.assertEqual([k for _, k, _ in events], ["todos", "dispatch", "result"])
            self.assertEqual(events[0][2][0]["content"], "Alpha")
            self.assertEqual(events[1][2]["description"], "Beta")
            self.assertEqual(events[2][2]["tool_use_id"], "tu-9")
            self.assertEqual(last_ts, tp.token_usage.parse_ts(T2))

    def test_task_tool_name_also_matches(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.jsonl")
            write_jsonl(p, [dispatch_entry(T0, "tu-1", "X", name="Task")])
            events, _ = tp.extract_events([p])
            self.assertEqual(events[0][1], "dispatch")

    def test_missing_file_yields_nothing(self):
        events, last_ts = tp.extract_events(["/nonexistent/x.jsonl"])
        self.assertEqual(events, [])
        self.assertIsNone(last_ts)


if __name__ == "__main__":
    unittest.main()
