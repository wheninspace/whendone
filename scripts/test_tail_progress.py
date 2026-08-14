#!/usr/bin/env python3
"""Tests for tail_progress.py. Run: python3 scripts/test_tail_progress.py -v"""
import contextlib, io, json, os, stat, sys, tempfile, unittest
import unittest.mock
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tail_progress as tp

T0 = "2026-07-18T10:00:00.000Z"
T1 = "2026-07-18T10:05:00.000Z"
T2 = "2026-07-18T10:12:00.000Z"
T3 = "2026-07-18T10:20:00.000Z"


def todo_entry(ts, todos, mid="m-todo"):
    """Assistant entry with one TodoWrite tool_use — the shape verified live 2026-07-18."""
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": mid, "usage": {},
                        "content": [{"type": "tool_use", "id": "tu-" + mid,
                                     "name": "TodoWrite", "input": {"todos": todos}}]}}


def dispatch_entry(ts, tool_id, description, name="Agent", model=None):
    inp = {"description": description, "prompt": "..."}
    if model is not None:
        inp["model"] = model
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": "m-" + tool_id, "usage": {},
                        "content": [{"type": "tool_use", "id": tool_id, "name": name,
                                     "input": inp}]}}


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

    def test_dispatch_model_passthrough_and_non_string_guard(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.jsonl")
            write_jsonl(p, [
                dispatch_entry(T0, "tu-1", "X", model="sonnet"),
                dispatch_entry(T1, "tu-2", "Y", model=True),   # non-string -> None
            ])
            events, _ = tp.extract_events([p])
            self.assertEqual(events[0][2]["model"], "sonnet")
            self.assertIsNone(events[1][2]["model"])

    def test_missing_file_yields_nothing(self):
        events, last_ts = tp.extract_events(["/nonexistent/x.jsonl"])
        self.assertEqual(events, [])
        self.assertIsNone(last_ts)


def taskcreate_entry(ts, tool_id, subject):
    """Assistant entry with one TaskCreate tool_use — the harness's TodoWrite
    successor; shape verified live 2026-08-13 (agent-orchestra transcript)."""
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": "m-" + tool_id, "usage": {},
                        "content": [{"type": "tool_use", "id": tool_id, "name": "TaskCreate",
                                     "input": {"subject": subject, "description": "...",
                                               "activeForm": subject}}]}}


def taskcreate_result(ts, tool_id, task_id, subject):
    return {"type": "user", "timestamp": ts,
            "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id,
                                     "content": "Task #%s created successfully: %s"
                                                % (task_id, subject)}]}}


def taskupdate_entry(ts, task_id, status, mid="m-tu"):
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": mid, "usage": {},
                        "content": [{"type": "tool_use", "id": "tu-" + mid,
                                     "name": "TaskUpdate",
                                     "input": {"taskId": task_id, "status": status}}]}}


class TaskToolsSynthesisTest(unittest.TestCase):
    """TaskCreate/TaskUpdate (the TodoWrite successor in newer harnesses) must
    synthesize the same 'todos' snapshot events the rest of the pipeline
    consumes — observe/observe_c/mirror_c stay untouched."""

    def _extract(self, entries):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.jsonl")
            write_jsonl(p, entries)
            events, _ = tp.extract_events([p])
        return events

    def test_create_bind_update_synthesizes_snapshots(self):
        events = self._extract([
            taskcreate_entry(T0, "tc-1", "Alpha"),
            taskcreate_result(T0, "tc-1", "1", "Alpha"),
            taskcreate_entry(T0, "tc-2", "Beta"),
            taskcreate_result(T0, "tc-2", "2", "Beta"),
            taskupdate_entry(T1, "1", "in_progress", mid="u1"),
            taskupdate_entry(T2, "1", "completed", mid="u2"),
        ])
        kinds = {k for _, k, _ in events}
        self.assertNotIn("taskcreate", kinds)
        self.assertNotIn("taskupdate", kinds)
        snaps = [p for _, k, p in events if k == "todos"]
        # bind-time snapshots expose pending items (total visible before any start)
        self.assertEqual(snaps[0], [{"content": "Alpha", "status": "pending"}])
        self.assertEqual(snaps[-1], [{"content": "Alpha", "status": "completed"},
                                     {"content": "Beta", "status": "pending"}])

    def test_unparseable_result_binds_in_creation_order(self):
        events = self._extract([
            taskcreate_entry(T0, "tc-1", "Alpha"),
            result_entry(T0, "tc-1"),                      # content "done", no "#N"
            taskupdate_entry(T1, "1", "in_progress", mid="u1"),
        ])
        snaps = [p for _, k, p in events if k == "todos"]
        self.assertEqual(snaps[-1], [{"content": "Alpha", "status": "in_progress"}])

    def test_unknown_status_and_unknown_task_are_ignored(self):
        events = self._extract([
            taskcreate_entry(T0, "tc-1", "Alpha"),
            taskcreate_result(T0, "tc-1", "1", "Alpha"),
            taskupdate_entry(T1, "1", "deleted", mid="u1"),   # unknown status
            taskupdate_entry(T1, "9", "completed", mid="u2"),  # unknown taskId
        ])
        snaps = [p for _, k, p in events if k == "todos"]
        self.assertEqual(snaps[-1], [{"content": "Alpha", "status": "pending"}])

    def test_result_content_as_block_list_binds_too(self):
        entry = taskcreate_result(T0, "tc-1", "1", "Alpha")
        block = entry["message"]["content"][0]
        block["content"] = [{"type": "text", "text": block["content"]}]
        events = self._extract([
            taskcreate_entry(T0, "tc-1", "Alpha"), entry,
            taskupdate_entry(T1, "1", "completed", mid="u1"),
        ])
        snaps = [p for _, k, p in events if k == "todos"]
        self.assertEqual(snaps[-1], [{"content": "Alpha", "status": "completed"}])

    def test_source_c_mirrors_task_tool_job_end_to_end(self):
        env = SyncEnv([
            taskcreate_entry(T0, "tc-1", "collect inputs"),
            taskcreate_result(T0, "tc-1", "1", "collect inputs"),
            taskcreate_entry(T0, "tc-2", "write summary"),
            taskcreate_result(T0, "tc-2", "2", "write summary"),
            taskupdate_entry(T1, "1", "in_progress", mid="u1"),
            taskupdate_entry(T2, "1", "completed", mid="u2"),
            taskupdate_entry(T2, "2", "in_progress", mid="u3"),
        ], mkstate(source="c", tasks=[], originalTotalMin=None))
        try:
            rc, lines = env.run_one_shot()
            self.assertEqual(rc, 0)
            st = env.state()
            self.assertEqual([(t["name"], t["status"]) for t in st["tasks"]],
                             [("collect inputs", "done"), ("write summary", "running")])
            ev = [l for l in lines if l["event"] == "progress"][-1]
            self.assertEqual((ev["done"], ev["total"]), (1, 2))
        finally:
            env.cleanup()

    def test_source_a_observe_sees_task_tool_checkpoints(self):
        events = self._extract([
            taskcreate_entry(T0, "tc-1", "Task 1: build tailer"),
            taskcreate_result(T0, "tc-1", "1", "Task 1: build tailer"),
            taskupdate_entry(T1, "1", "in_progress", mid="u1"),
            taskupdate_entry(T2, "1", "completed", mid="u2"),
        ])
        obs = tp.observe(events, tp.name_index([{"nr": 1, "name": "build tailer"}]))
        self.assertEqual(obs[1]["startedAt"], tp.token_usage.parse_ts(T1).isoformat())
        self.assertEqual(obs[1]["finishedAt"], tp.token_usage.parse_ts(T2).isoformat())


def mkstate(**kw):
    d = {"job": "demo job", "jobId": "20260718T0900", "planFile": None,
         "artifactUrl": None, "artifactFile": None,
         "startedAt": "2026-07-18T09:00:00+00:00", "sessionIds": ["sidA"],
         "originalTotalMin": 30, "pausedAt": None, "pausedTotalMin": 0,
         "resumedAt": None, "status": "running", "publish": True,
         "etaAlertSent": False, "source": "a", "tasks": []}
    d.update(kw)
    return d


def mktask(nr=1, **kw):
    d = {"nr": nr, "name": "task %d" % nr, "category": "testing",
         "rawEstimateMin": 10, "estimateMin": 10, "model": None, "effort": None,
         "group": None, "actualMin": None, "status": "pending",
         "startedAt": None, "finishedAt": None}
    d.update(kw)
    return d


class SyncEnv:
    """Builds a fake <projects-dir>/proj/sidA.jsonl + a state file in a tempdir."""
    def __init__(self, entries, state_dict):
        self.dir = tempfile.TemporaryDirectory()
        root = self.dir.name
        self.projects = os.path.join(root, "projects")
        os.makedirs(os.path.join(self.projects, "proj"))
        write_jsonl(os.path.join(self.projects, "proj", "sidA.jsonl"), entries)
        os.makedirs(os.path.join(root, "repo", ".claude"))
        self.state_path = os.path.join(root, "repo", ".claude", "whendone-state.json")
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(state_dict, f)

    def run_one_shot(self, now="2026-07-18T10:30:00+00:00", extra=()):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = tp.main([self.state_path, "--now", now,
                          "--projects-dir", self.projects, *extra])
        lines = [json.loads(l) for l in buf.getvalue().splitlines() if l.strip()]
        return rc, lines

    def state(self):
        with open(self.state_path, encoding="utf-8") as f:
            return json.load(f)

    def cleanup(self):
        self.dir.cleanup()


class LocalTimePolicyTest(unittest.TestCase):
    """Local-time policy: when no --now is given (L1 --follow and bare
    one-shot), the tailer's internal `now` must be local-tz aware — the
    renderer displays every time in now's tz, so a UTC default paints the
    whole artifact in UTC (live bug found in the 2026-07-19 resume drill)."""

    def test_one_shot_default_now_renders_local_hhmm(self):
        env = SyncEnv([], mkstate(tasks=[mktask(1)]))
        out_path = None
        try:
            before = datetime.now().astimezone()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = tp.main([env.state_path, "--projects-dir", env.projects])
            after = datetime.now().astimezone()
            self.assertEqual(rc, 0)
            out_path = tp._render_out_path(env.state())
            with open(out_path, encoding="utf-8") as f:
                html = f.read()
            accepted = {before.strftime("last updated %H:%M"),
                        after.strftime("last updated %H:%M")}
            self.assertTrue(any(s in html for s in accepted),
                            "banner must show local HH:MM (accepted %r)" % accepted)
        finally:
            if out_path:
                with contextlib.suppress(OSError):
                    os.remove(out_path)
            env.cleanup()

    def test_follow_default_now_stamps_local_offset(self):
        env = SyncEnv([], mkstate(tasks=[mktask(1, status="running",
                                               startedAt="2026-07-18T09:00:00+00:00")]))
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = tp.main([env.state_path, "--projects-dir", env.projects,
                              "--follow", "--max-cycles", "1", "--interval", "0"])
            self.assertEqual(rc, 0)
            stamp = env.state()["tasks"][0]["staleNotifiedAt"]
            self.assertIsNotNone(stamp)      # task started 2026-07-18 -> stale fired
            dt = datetime.fromisoformat(stamp)
            self.assertEqual(dt.utcoffset(), datetime.now().astimezone().utcoffset())
        finally:
            env.cleanup()


class ObserveTest(unittest.TestCase):
    IDX = {"alpha": 1}

    def _ev(self, entries):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.jsonl")
            write_jsonl(p, entries)
            events, _ = tp.extract_events([p])
        return events

    def test_todo_transitions_are_authoritative(self):
        ev = self._ev([
            todo_entry(T0, [item("Alpha", "in_progress")]),
            dispatch_entry(T1, "tu-1", "Alpha", model="sonnet"),
            result_entry(T2, "tu-1"),
            todo_entry(T3, [item("Alpha", "completed")]),
        ])
        o = tp.observe(ev, self.IDX)[1]
        self.assertEqual(o["startedAt"], tp.token_usage.parse_ts(T0).isoformat())
        self.assertEqual(o["todoFinishedAt"], tp.token_usage.parse_ts(T3).isoformat())
        self.assertTrue(o["todoSeen"])
        self.assertEqual(o["spans"], [(tp.token_usage.parse_ts(T1).isoformat(),
                                       tp.token_usage.parse_ts(T2).isoformat())])
        self.assertEqual(o["open"], 0)
        self.assertEqual(o["model"], "sonnet")

    def test_todo_start_wins_over_earlier_dispatch(self):
        """D1: dispatch is start-fallback ONLY — a batch of parallel dispatches
        followed by one todo update must not let the dispatch's earlier
        timestamp win, even though it was seen first in ts order."""
        ev = self._ev([
            dispatch_entry(T0, "tu-1", "Alpha"),
            todo_entry(T1, [item("Alpha", "in_progress")]),
            result_entry(T2, "tu-1"),
            todo_entry(T3, [item("Alpha", "completed")]),
        ])
        o = tp.observe(ev, self.IDX)[1]
        self.assertEqual(o["startedAt"], tp.token_usage.parse_ts(T1).isoformat())
        self.assertEqual(set(o), {"startedAt", "todoFinishedAt", "todoSeen",
                                  "spans", "open", "model"})

    def test_result_does_not_set_todo_finish(self):
        ev = self._ev([todo_entry(T0, [item("Alpha", "in_progress")]),
                       dispatch_entry(T1, "tu-1", "Alpha"),
                       result_entry(T2, "tu-1")])
        o = tp.observe(ev, self.IDX)[1]
        self.assertIsNone(o["todoFinishedAt"])   # the 2026-08-14 bug, inverted

    def test_pending_snapshot_does_not_set_todo_seen(self):
        ev = self._ev([todo_entry(T0, [item("Alpha", "pending")]),
                       dispatch_entry(T1, "tu-1", "Alpha"),
                       result_entry(T2, "tu-1")])
        o = tp.observe(ev, self.IDX)[1]
        self.assertFalse(o["todoSeen"])
        self.assertEqual(o["startedAt"], tp.token_usage.parse_ts(T1).isoformat())

    def test_multiple_spans_sum_and_open_counts(self):
        ev = self._ev([dispatch_entry(T0, "tu-1", "Alpha"),
                       result_entry(T1, "tu-1"),
                       dispatch_entry(T2, "tu-2", "Alpha")])
        o = tp.observe(ev, self.IDX)[1]
        self.assertEqual(len(o["spans"]), 1)
        self.assertEqual(o["open"], 1)

    def test_unmatched_names_ignored(self):
        ev = self._ev([dispatch_entry(T0, "tu-1", "Code quality review Alpha"),
                       result_entry(T1, "tu-1")])
        self.assertEqual(tp.observe(ev, self.IDX), {})


class SourceCObserveUnitTest(unittest.TestCase):
    """Spec §4.3: Source C has no declared plan — the TodoWrite list IS the plan."""

    def _ev(self, ts, todos):
        return (tp.token_usage.parse_ts(ts), "todos", todos)

    def test_latest_snapshot_wins_and_timestamps_are_first_seen(self):
        events = [
            self._ev(T0, [{"content": "Alpha", "status": "in_progress"},
                          {"content": "Beta", "status": "pending"}]),
            self._ev(T1, [{"content": "Alpha", "status": "completed"},
                          {"content": "Beta", "status": "in_progress"}]),
        ]
        snap, started, finished = tp.observe_c(events)
        self.assertEqual([i["content"] for i in snap], ["Alpha", "Beta"])
        self.assertEqual(started["alpha"], tp.token_usage.parse_ts(T0).isoformat())
        self.assertEqual(started["beta"], tp.token_usage.parse_ts(T1).isoformat())
        self.assertEqual(finished["alpha"], tp.token_usage.parse_ts(T1).isoformat())
        self.assertNotIn("beta", finished)

    def test_no_todos_events_yields_none_snapshot(self):
        snap, started, finished = tp.observe_c([])
        self.assertIsNone(snap)
        self.assertEqual((started, finished), ({}, {}))

    def test_mirror_maps_statuses_and_positions(self):
        snap = [{"content": "Alpha", "status": "completed"},
                {"content": "Beta", "status": "in_progress"},
                {"content": "Gamma", "status": "pending"},
                {"content": "", "status": "pending"},          # skipped: empty
                {"content": 7, "status": "pending"}]           # skipped: non-string
        tasks = tp.mirror_c(snap, {"alpha": T0, "beta": T1}, {"alpha": T1})
        self.assertEqual([(t["nr"], t["name"], t["status"]) for t in tasks],
                         [(1, "Alpha", "done"), (2, "Beta", "running"),
                          (3, "Gamma", "pending")])
        self.assertEqual(tasks[0]["startedAt"], T0)
        self.assertEqual(tasks[0]["finishedAt"], T1)
        self.assertEqual(tasks[1]["finishedAt"], None)
        self.assertNotIn("estimateMin", tasks[0])              # spec §5.1: no estimates
        self.assertNotIn("category", tasks[0])

    def test_mirror_revert_clears_finishedAt(self):
        # completed -> pending revert: status mirrors the newest snapshot;
        # a non-done task never displays a finish time
        snap = [{"content": "Alpha", "status": "pending"}]
        tasks = tp.mirror_c(snap, {"alpha": T0}, {"alpha": T1})
        self.assertEqual(tasks[0]["status"], "pending")
        self.assertIsNone(tasks[0]["startedAt"])
        self.assertIsNone(tasks[0]["finishedAt"])

    def test_unknown_status_degrades_to_pending(self):
        tasks = tp.mirror_c([{"content": "Alpha", "status": "someday"}], {}, {})
        self.assertEqual(tasks[0]["status"], "pending")


class SourceCSyncTest(unittest.TestCase):
    def _env(self, entries, **kw):
        return SyncEnv(entries, mkstate(source="c", tasks=[],
                                        originalTotalMin=None, **kw))

    def test_mirror_creates_tasks_marks_progress_and_labels_uncalibrated(self):
        env = self._env([
            todo_entry(T0, [item("collect inputs", "in_progress"),
                            item("write summary", "pending")], mid="m1"),
            todo_entry(T1, [item("collect inputs", "completed"),
                            item("write summary", "in_progress")], mid="m2"),
        ])
        try:
            rc, lines = env.run_one_shot()
            self.assertEqual(rc, 0)
            st = env.state()
            self.assertEqual([(t["name"], t["status"]) for t in st["tasks"]],
                             [("collect inputs", "done"), ("write summary", "running")])
            ev = [l for l in lines if l["event"] == "progress"][-1]
            self.assertEqual((ev["done"], ev["total"]), (1, 2))
            self.assertEqual(ev["justDone"], ["collect inputs"])
            self.assertIn("uncalibrated", ev.get("etaText") or "")
        finally:
            env.cleanup()

    def test_all_done_event_when_every_item_completes(self):
        env = self._env([
            todo_entry(T0, [item("only step", "in_progress")], mid="m1"),
            todo_entry(T1, [item("only step", "completed")], mid="m2"),
        ])
        try:
            rc, lines = env.run_one_shot()
            self.assertEqual(rc, 0)
            self.assertEqual(lines[-1]["event"], "all-done")
        finally:
            env.cleanup()

    def test_removed_and_reverted_items_follow_newest_snapshot(self):
        env = self._env([
            todo_entry(T0, [item("a", "completed"), item("b", "in_progress")], mid="m1"),
            todo_entry(T1, [item("a", "pending")], mid="m2"),   # b removed, a reverted
        ])
        try:
            env.run_one_shot()
            st = env.state()
            self.assertEqual([(t["name"], t["status"]) for t in st["tasks"]],
                             [("a", "pending")])
        finally:
            env.cleanup()

    def test_no_snapshot_yet_is_quiet_no_change(self):
        env = self._env([])                     # transcript has no TodoWrite at all
        try:
            rc, lines = env.run_one_shot()
            self.assertEqual(rc, 0)
            self.assertEqual(env.state()["tasks"], [])
            # one_shot forces a render (progress event) but done/total are 0/0
            ev = [l for l in lines if l["event"] == "progress"][-1]
            self.assertEqual((ev["done"], ev["total"], ev["changed"]), (0, 0, False))
        finally:
            env.cleanup()

    def test_non_running_status_is_noop(self):
        env = self._env([], status="paused")
        try:
            rc, lines = env.run_one_shot()
            self.assertEqual(lines[0]["event"], "no-op")
        finally:
            env.cleanup()

    def test_stale_flag_carries_across_mirror_rebuild(self):
        env = self._env([todo_entry(T0, [item("long step", "in_progress")], mid="m1")])
        try:
            env.run_one_shot()
            st = env.state()
            st["tasks"][0]["staleNotifiedAt"] = T1     # as check_staleness would set
            with open(env.state_path, "w", encoding="utf-8") as f:
                json.dump(st, f)
            write_jsonl(os.path.join(env.projects, "proj", "sidA.jsonl"), [
                todo_entry(T0, [item("long step", "in_progress")], mid="m1"),
                todo_entry(T2, [item("long step", "in_progress"),
                                item("new item", "pending")], mid="m2"),
            ])
            env.run_one_shot()
            st = env.state()
            self.assertEqual(st["tasks"][0]["staleNotifiedAt"], T1)
            self.assertEqual(len(st["tasks"]), 2)
        finally:
            env.cleanup()


class OneShotTest(unittest.TestCase):
    def test_completion_marks_done_and_stamps_transcript_times(self):
        env = SyncEnv([todo_entry(T0, [item("task 1", "in_progress")]),
                       todo_entry(T1, [item("task 1", "completed")])],
                      mkstate(tasks=[mktask(1)]))
        try:
            rc, lines = env.run_one_shot()
            self.assertEqual(rc, 0)
            t = env.state()["tasks"][0]
            self.assertEqual(t["status"], "done")
            self.assertEqual(t["startedAt"], tp.token_usage.parse_ts(T0).isoformat())
            self.assertEqual(t["finishedAt"], tp.token_usage.parse_ts(T1).isoformat())
            self.assertTrue(any(l["event"] in ("progress", "all-done") for l in lines))
        finally:
            env.cleanup()

    def test_idempotent_rescan_never_retouches_done(self):
        env = SyncEnv([todo_entry(T1, [item("task 1", "completed")])],
                      mkstate(tasks=[mktask(1, status="done",
                                            startedAt="2026-07-18T09:00:00+00:00",
                                            finishedAt="2026-07-18T09:10:00+00:00",
                                            actualMin=10.0)]))
        try:
            rc, _ = env.run_one_shot()
            t = env.state()["tasks"][0]
            self.assertEqual(t["finishedAt"], "2026-07-18T09:10:00+00:00")  # untouched
        finally:
            env.cleanup()

    def test_start_evidence_marks_running(self):
        env = SyncEnv([todo_entry(T0, [item("task 1", "in_progress")])],
                      mkstate(tasks=[mktask(1)]))
        try:
            env.run_one_shot()
            self.assertEqual(env.state()["tasks"][0]["status"], "running")
        finally:
            env.cleanup()

    def test_job_id_mismatch_writes_nothing(self):
        env = SyncEnv([todo_entry(T1, [item("task 1", "completed")])],
                      mkstate(tasks=[mktask(1)]))
        try:
            rc, lines = env.run_one_shot(extra=("--job-id", "OTHER"))
            self.assertEqual(rc, 3)
            self.assertEqual(lines[-1]["event"], "ownership-lost")
            self.assertEqual(env.state()["tasks"][0]["status"], "pending")
        finally:
            env.cleanup()

    def test_paused_and_unsupported_source_are_noops(self):
        # sources "b" and "c" are now observed sources (Tasks 4/3) — "x" pins the
        # unknown-source contract.
        for st, want in ((mkstate(status="paused", tasks=[mktask(1)]), "no-op"),
                         (mkstate(source="x", tasks=[mktask(1)]), "unsupported-source")):
            env = SyncEnv([todo_entry(T1, [item("task 1", "completed")])], st)
            try:
                rc, lines = env.run_one_shot()
                self.assertEqual(rc, 0)
                self.assertEqual(lines[-1]["event"], want)
                self.assertEqual(env.state()["tasks"][0]["status"], "pending")
            finally:
                env.cleanup()

    def test_malformed_state_fails_closed(self):
        env = SyncEnv([], mkstate())
        try:
            with open(env.state_path, "w") as f:
                f.write("{truncated")
            rc, lines = env.run_one_shot()
            self.assertNotEqual(rc, 0)
            self.assertEqual(lines[-1]["event"], "error")
        finally:
            env.cleanup()

    def test_atomic_write_leaves_no_temp_files(self):
        env = SyncEnv([todo_entry(T1, [item("task 1", "completed")])],
                      mkstate(tasks=[mktask(1)]))
        try:
            env.run_one_shot()
            leftovers = [n for n in os.listdir(os.path.dirname(env.state_path))
                         if n not in ("whendone-state.json",)]
            self.assertEqual(leftovers, [])
        finally:
            env.cleanup()

    def test_stop_file_emits_stop_requested_and_keeps_monitoring(self):
        # C1: Source-A twin of StopRequestedTest's first case.
        env = SyncEnv([todo_entry(T0, [item("task 1", "in_progress")])],
                      mkstate(tasks=[mktask(1)]))
        stop_path = os.path.join(os.path.dirname(env.state_path), "STOP")
        open(stop_path, "w").close()
        try:
            rc, lines = env.run_one_shot()
            kinds = [e["event"] for e in lines]
            self.assertIn("stop-requested", kinds)
            self.assertEqual(kinds.index("stop-requested"), 0)   # before source events
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(stop_path))  # tailer never deletes
        finally:
            env.cleanup()


def usage_entry(ts, model, out_tokens, mid):
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": mid, "model": model,
                        "usage": {"output_tokens": out_tokens, "input_tokens": 5}}}


class CompletionPipelineTest(unittest.TestCase):
    def setUp(self):
        self.calib = tempfile.TemporaryDirectory()
        os.environ["WHENDONE_DATA_DIR"] = self.calib.name

    def tearDown(self):
        os.environ["WHENDONE_DATA_DIR"] = _MODULE_CALIB.name   # restore module default
        self.calib.cleanup()

    def rows(self):
        p = os.path.join(self.calib.name, "calibration.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    def test_row_appended_with_transcript_times_and_resolved_alias(self):
        env = SyncEnv([todo_entry(T0, [item("task 1", "in_progress")]),
                       usage_entry(T1, "claude-haiku-4-5-20251001", 50, "m-u1"),
                       todo_entry(T2, [item("task 1", "completed")])],
                      mkstate(tasks=[mktask(1, model="haiku")]))
        try:
            env.run_one_shot()
            t = env.state()["tasks"][0]
            self.assertEqual(t["model"], "claude-haiku-4-5-20251001")   # substring upgrade
            rows = self.rows()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["model"], "claude-haiku-4-5-20251001")
            self.assertEqual(rows[0]["startedAt"], tp.token_usage.parse_ts(T0).isoformat())
            self.assertEqual(rows[0]["finishedAt"], tp.token_usage.parse_ts(T2).isoformat())
            self.assertEqual(t["actualMin"], rows[0]["actualMin"])       # (d) mirrors the log
        finally:
            env.cleanup()

    def test_cross_family_top_model_never_upgrades_alias(self):
        env = SyncEnv([todo_entry(T0, [item("task 1", "in_progress")]),
                       usage_entry(T1, "claude-opus-4-8", 500, "m-u2"),
                       todo_entry(T2, [item("task 1", "completed")])],
                      mkstate(tasks=[mktask(1, model="haiku")]))
        try:
            env.run_one_shot()
            self.assertEqual(env.state()["tasks"][0]["model"], "haiku")  # guard held
            self.assertEqual(self.rows()[0]["model"], "haiku")
        finally:
            env.cleanup()

    def test_completion_without_start_gets_no_row_and_null_actual(self):
        env = SyncEnv([todo_entry(T2, [item("task 1", "completed")])],
                      mkstate(tasks=[mktask(1)]))
        try:
            env.run_one_shot()
            t = env.state()["tasks"][0]
            self.assertEqual(t["status"], "done")
            self.assertIsNone(t["actualMin"])
            self.assertEqual(self.rows(), [])                            # D6
        finally:
            env.cleanup()

    def test_group_members_log_one_synthetic_row(self):
        tasks = [mktask(1, name="member a", group="g1", estimateMin=10, rawEstimateMin=8),
                 mktask(2, name="member b", group="g1", estimateMin=20, rawEstimateMin=25)]
        env = SyncEnv([dispatch_entry(T0, "tu-a", "member a"),
                       dispatch_entry(T0, "tu-b", "member b"),
                       result_entry(T1, "tu-a"),
                       result_entry(T2, "tu-b")],
                      mkstate(tasks=tasks))
        try:
            env.run_one_shot()
            rows = self.rows()
            self.assertEqual(len(rows), 1)
            r = rows[0]
            self.assertEqual(r["category"], "parallel-group")
            self.assertEqual(r["rawEstimateMin"], 25)
            self.assertEqual(r["maxAdjusted"], 20)
            self.assertEqual(r["sumAdjusted"], 30)
            self.assertEqual(r["startedAt"], tp.token_usage.parse_ts(T0).isoformat())
            self.assertEqual(r["finishedAt"], tp.token_usage.parse_ts(T2).isoformat())
        finally:
            env.cleanup()

    def test_append_failure_still_leaves_task_done(self):
        # a data dir beneath a regular file is uncreatable on every platform
        # (POSIX ENOTDIR, Windows "directory name is invalid"); /dev/null-style
        # poison paths are creatable on Windows and silently skip the failure
        blocker = os.path.join(self.calib.name, "blocker")
        open(blocker, "w").close()
        os.environ["WHENDONE_DATA_DIR"] = os.path.join(blocker, "impossible")
        env = SyncEnv([todo_entry(T0, [item("task 1", "in_progress")]),
                       todo_entry(T2, [item("task 1", "completed")])],
                      mkstate(tasks=[mktask(1)]))
        try:
            rc, _ = env.run_one_shot()
            self.assertEqual(rc, 0)                                      # fail-soft
            t = env.state()["tasks"][0]
            self.assertEqual(t["status"], "done")
            self.assertIsNone(t["actualMin"])
        finally:
            env.cleanup()

    def test_effort_and_client_passthrough(self):
        env = SyncEnv([todo_entry(T0, [item("task 1", "in_progress")]),
                       todo_entry(T2, [item("task 1", "completed")])],
                      mkstate(client="cli", tasks=[mktask(1, effort="low")]))
        try:
            env.run_one_shot()
            r = self.rows()[0]
            self.assertEqual(r["effort"], "low")
            self.assertEqual(r["client"], "cli")
        finally:
            env.cleanup()


class SourceCNeverCalibratesTest(unittest.TestCase):
    """Spec §5.1: no calibration rows are ever written from Source C jobs."""

    def setUp(self):
        self._own = tempfile.TemporaryDirectory()
        os.environ["WHENDONE_DATA_DIR"] = self._own.name

    def tearDown(self):
        os.environ["WHENDONE_DATA_DIR"] = _MODULE_CALIB.name
        self._own.cleanup()

    def _calib_path(self):
        return os.path.join(self._own.name, "calibration.jsonl")

    def test_full_source_c_completion_appends_nothing(self):
        env = SyncEnv([
            todo_entry(T0, [item("only step", "in_progress")], mid="m1"),
            todo_entry(T1, [item("only step", "completed")], mid="m2"),
        ], mkstate(source="c", tasks=[], originalTotalMin=None))
        try:
            rc, lines = env.run_one_shot()
            self.assertEqual(lines[-1]["event"], "all-done")
            self.assertFalse(os.path.exists(self._calib_path()))
        finally:
            env.cleanup()

    def test_handle_completion_refuses_source_c_outright(self):
        # Defense-in-depth: even a future caller that routes a C state here logs nothing
        st = mkstate(source="c", tasks=[mktask(1, status="running")])
        t = st["tasks"][0]
        with tempfile.TemporaryDirectory() as sd:
            sp = os.path.join(sd, "state.json")
            with open(sp, "w", encoding="utf-8") as f:
                json.dump(st, f)
            tp.handle_completion(sp, st, t, T0, T1, unittest.mock.Mock(projects_dir=None))
        self.assertNotEqual(t["status"], "done")        # guard fired before any write
        self.assertFalse(os.path.exists(self._calib_path()))

    def test_source_a_control_still_appends(self):
        # Control: the guard must not leak into Source A behavior
        env = SyncEnv([
            todo_entry(T0, [item("task 1", "in_progress")], mid="m1"),
            todo_entry(T1, [item("task 1", "completed")], mid="m2"),
        ], mkstate(tasks=[mktask(1)]))
        try:
            env.run_one_shot()
            self.assertTrue(os.path.exists(self._calib_path()))
        finally:
            env.cleanup()


class FinishCycleTest(unittest.TestCase):
    def setUp(self):
        self.calib = tempfile.TemporaryDirectory()
        os.environ["WHENDONE_DATA_DIR"] = self.calib.name

    def tearDown(self):
        os.environ["WHENDONE_DATA_DIR"] = _MODULE_CALIB.name   # restore module default
        self.calib.cleanup()

    def _env(self, entries, state_dict):
        env = SyncEnv(entries, state_dict)
        art = os.path.join(env.dir.name, "artifact.html")
        st = env.state(); st["artifactFile"] = art
        with open(env.state_path, "w", encoding="utf-8") as f:
            json.dump(st, f)
        return env, art

    def test_progress_event_carries_etatext_and_writes_html_and_tokens(self):
        env, art = self._env(
            [todo_entry(T0, [item("task 1", "in_progress")]),
             usage_entry(T1, "claude-haiku-4-5-20251001", 50, "m-u1"),
             todo_entry(T2, [item("task 1", "completed")])],
            mkstate(tasks=[mktask(1), mktask(2)]))
        try:
            rc, lines = env.run_one_shot()
            self.assertEqual(rc, 0)
            ev = [l for l in lines if l["event"] == "progress"][-1]
            self.assertTrue(ev["rendered"])
            self.assertIn("etaText", ev)
            self.assertEqual(ev["justDone"], ["task 1"])
            self.assertTrue(os.path.exists(art))
            self.assertTrue(os.path.exists(art + ".tokens.json"))
            with open(art, encoding="utf-8") as f:
                self.assertIn("task 1", f.read())
        finally:
            env.cleanup()

    def test_all_done_event_when_last_task_completes(self):
        env, _ = self._env([todo_entry(T0, [item("task 1", "in_progress")]),
                            todo_entry(T1, [item("task 1", "completed")])],
                           mkstate(tasks=[mktask(1)]))
        try:
            rc, lines = env.run_one_shot()
            self.assertEqual(lines[-1]["event"], "all-done")
        finally:
            env.cleanup()

    def test_render_failure_is_fail_soft(self):
        # artifactFile stays a validly-hardened path (see RenderOutPathHardeningTest for
        # the missing-parent/invalid-path cases, now intercepted upstream in
        # _render_out_path itself); the render step's own failure is forced directly.
        env, _ = self._env([todo_entry(T0, [item("task 1", "in_progress")]),
                            todo_entry(T1, [item("task 1", "completed")])],
                           mkstate(tasks=[mktask(1), mktask(2)]))
        try:
            with unittest.mock.patch.object(tp.render_artifact, "main", return_value=1):
                rc, lines = env.run_one_shot()
            self.assertEqual(rc, 0)                      # job never wedged
            ev = [l for l in lines if l["event"] == "progress"][-1]
            self.assertFalse(ev["rendered"])
            self.assertNotIn("etaText", ev)
            self.assertEqual(env.state()["tasks"][0]["status"], "done")  # state still advanced
        finally:
            env.cleanup()

    def test_slip_alert_once(self):
        # originalTotalMin 10; task 1 done at actual 30 -> left side 30+10 > 15 -> slip
        done = mktask(1, status="done", actualMin=30.0,
                      startedAt="2026-07-18T09:00:00+00:00",
                      finishedAt="2026-07-18T09:30:00+00:00")
        env, _ = self._env([todo_entry(T0, [item("task 2", "in_progress")]),
                            todo_entry(T1, [item("task 2", "completed")])],
                           mkstate(originalTotalMin=10, tasks=[done, mktask(2), mktask(3)]))
        try:
            rc, lines = env.run_one_shot()
            ev = [l for l in lines if l["event"] == "progress"][-1]
            self.assertTrue(ev.get("slipAlert"))
            self.assertTrue(env.state()["etaAlertSent"])
            rc2, lines2 = env.run_one_shot()             # re-render: no second alert
            ev2 = [l for l in lines2 if l["event"] in ("progress", "all-done")][-1]
            self.assertFalse(ev2.get("slipAlert", False))
        finally:
            env.cleanup()

    def test_unchanged_one_shot_still_renders_eta(self):
        env, art = self._env([], mkstate(tasks=[mktask(1, status="running",
                                                startedAt="2026-07-18T10:00:00+00:00")]))
        try:
            rc, lines = env.run_one_shot()
            ev = lines[-1]
            self.assertEqual(ev["event"], "progress")
            self.assertFalse(ev["changed"])
            self.assertTrue(ev["rendered"])              # L3 boundary refresh
        finally:
            env.cleanup()

    @staticmethod
    def _summary(nr, out, job_out):
        """A task-scoped token_usage.summarize() result, mimicking the real
        --task filter (token_usage.py:237): 'tasks' holds ONE entry."""
        entry = {"nr": nr, "output": out, "freshInput": 1, "cacheRead": 0, "models": []}
        job = {"output": job_out, "freshInput": 5, "cacheRead": 0, "models": []}
        subs = {"output": 0, "freshInput": 0, "cacheRead": 0, "models": []}
        return {"available": True, "job": job, "tasks": [entry], "subagents": subs}

    def test_sidecar_merges_across_sequential_completions(self):
        # Bug repro: each wake's handle_completion holds only ITS task's scoped
        # summary (token_usage.py:237 filters by nr). Before the fix, finish_cycle
        # overwrote the sidecar wholesale on wake 2, dropping task 1's entry.
        env, art = self._env(
            [todo_entry(T0, [item("task 1", "in_progress")]),
             todo_entry(T1, [item("task 1", "completed")])],
            mkstate(tasks=[mktask(1), mktask(2)]))
        sidecar = art + ".tokens.json"
        try:
            with unittest.mock.patch.object(tp.token_usage, "summarize",
                                            return_value=self._summary(1, 20, 100)):
                rc1, lines1 = env.run_one_shot()
            self.assertEqual(rc1, 0)
            with open(sidecar, encoding="utf-8") as f:
                after_first = json.load(f)
            self.assertEqual([e["nr"] for e in after_first["tasks"]], [1])

            # Wake 2: transcript now shows task 2 running -> completed too.
            write_jsonl(os.path.join(env.projects, "proj", "sidA.jsonl"), [
                todo_entry(T0, [item("task 1", "in_progress")]),
                todo_entry(T1, [item("task 1", "completed")]),
                todo_entry(T1, [item("task 2", "in_progress")]),
                todo_entry(T2, [item("task 2", "completed")]),
            ])
            with unittest.mock.patch.object(tp.token_usage, "summarize",
                                            return_value=self._summary(2, 50, 300)):
                rc2, lines2 = env.run_one_shot()
            self.assertEqual(rc2, 0)

            with open(sidecar, encoding="utf-8") as f:
                merged = json.load(f)
            self.assertEqual(sorted(e["nr"] for e in merged["tasks"]), [1, 2])
            by_nr = {e["nr"]: e for e in merged["tasks"]}
            self.assertEqual(by_nr[1]["output"], 20)      # earlier task carried forward
            self.assertEqual(by_nr[2]["output"], 50)       # newest wake's entry wins for nr 2
            self.assertEqual(merged["job"]["output"], 300)  # job totals: freshest held object
        finally:
            env.cleanup()

    def test_sidecar_merge_degrades_gracefully_on_corrupt_existing_file(self):
        env, art = self._env(
            [todo_entry(T0, [item("task 1", "in_progress")]),
             todo_entry(T1, [item("task 1", "completed")])],
            mkstate(tasks=[mktask(1)]))
        sidecar = art + ".tokens.json"
        try:
            with open(sidecar, "w", encoding="utf-8") as f:
                f.write("{not valid json at all")
            with unittest.mock.patch.object(tp.token_usage, "summarize",
                                            return_value=self._summary(1, 20, 100)):
                rc, lines = env.run_one_shot()          # must not raise
            self.assertEqual(rc, 0)
            with open(sidecar, encoding="utf-8") as f:
                merged = json.load(f)
            self.assertEqual([e["nr"] for e in merged["tasks"]], [1])
            self.assertEqual(merged["tasks"][0]["output"], 20)
        finally:
            env.cleanup()

    @unittest.skipUnless(os.name == "posix", "POSIX file-mode semantics")
    def test_no_publish_render_output_is_0600(self):
        # M9: under publish:false the tailer still renders every wake (the chat
        # table needs etaText) to a predictable /tmp path with a null artifactFile
        # (tempdir fallback) -- both the HTML and its .tokens.json sidecar must
        # land 0600, never the default-open 0644.
        job_id = "nopubtest01"
        out_path = os.path.join(tempfile.gettempdir(),
                                 "whendone-render-%s.html" % job_id)
        sidecar = out_path + ".tokens.json"
        for p in (out_path, sidecar):
            with contextlib.suppress(OSError):
                os.remove(p)
        env = SyncEnv(
            [todo_entry(T0, [item("task 1", "in_progress")]),
             usage_entry(T1, "claude-haiku-4-5-20251001", 50, "m-u1"),
             todo_entry(T2, [item("task 1", "completed")])],
            mkstate(jobId=job_id, publish=False, artifactFile=None,
                    tasks=[mktask(1), mktask(2)]))
        try:
            rc, lines = env.run_one_shot()
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(out_path))
            self.assertTrue(os.path.exists(sidecar))
            self.assertEqual(stat.S_IMODE(os.stat(out_path).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(sidecar).st_mode), 0o600)
        finally:
            env.cleanup()
            for p in (out_path, sidecar):
                with contextlib.suppress(OSError):
                    os.remove(p)


class FollowTest(unittest.TestCase):
    def setUp(self):
        self.calib = tempfile.TemporaryDirectory()
        os.environ["WHENDONE_DATA_DIR"] = self.calib.name

    def tearDown(self):
        os.environ["WHENDONE_DATA_DIR"] = _MODULE_CALIB.name   # restore module default
        self.calib.cleanup()

    def run_follow(self, env, extra=()):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = tp.main([env.state_path, "--follow", "--interval", "0",
                          "--projects-dir", env.projects, *extra])
        return rc, [json.loads(l) for l in buf.getvalue().splitlines() if l.strip()]

    def test_all_done_terminates_without_max_cycles(self):
        env = SyncEnv([todo_entry(T0, [item("task 1", "in_progress")]),
                       todo_entry(T1, [item("task 1", "completed")])],
                      mkstate(tasks=[mktask(1)]))
        try:
            rc, lines = self.run_follow(env)
            self.assertEqual(rc, 0)
            self.assertEqual(lines[-1]["event"], "all-done")
        finally:
            env.cleanup()

    def test_exit_on_event_stops_after_first_progress(self):
        env = SyncEnv([todo_entry(T1, [item("task 1", "completed"),
                                       item("task 2", "pending")]),
                       todo_entry(T0, [item("task 1", "in_progress")])],
                      mkstate(tasks=[mktask(1), mktask(2)]))
        try:
            rc, lines = self.run_follow(env, extra=("--exit-on-event",))
            self.assertEqual(rc, 0)
            self.assertEqual(sum(1 for l in lines if l["event"] == "progress"), 1)
        finally:
            env.cleanup()

    def test_quiet_cycles_emit_nothing(self):
        env = SyncEnv([], mkstate(tasks=[mktask(1)]))
        try:
            rc, lines = self.run_follow(env, extra=("--max-cycles", "3"))
            self.assertEqual(rc, 0)
            self.assertEqual([l for l in lines
                              if l["event"] in ("progress", "all-done")], [])
        finally:
            env.cleanup()

    def test_stale_event_once_and_persisted(self):
        # Deterministic anchor: pre-running task with an old startedAt and an
        # EMPTY transcript (no events, no last_ts) — staleness must not depend
        # on when the suite happens to run relative to the fixture timestamps.
        env = SyncEnv([], mkstate(tasks=[mktask(
            1, status="running", startedAt="2026-01-01T00:00:00+00:00")]))
        try:
            rc, lines = self.run_follow(env, extra=("--max-cycles", "3",
                                                    "--stale-min", "5"))
            stales = [l for l in lines if l["event"] == "stale"]
            self.assertEqual(len(stales), 1)             # once, not per cycle
            self.assertEqual(stales[0]["task"], 1)
            self.assertGreater(stales[0]["stalledMin"], 5)
            self.assertIsNotNone(env.state()["tasks"][0]["staleNotifiedAt"])
            rc2, lines2 = self.run_follow(env, extra=("--max-cycles", "2",
                                                      "--stale-min", "5"))
            self.assertEqual([l for l in lines2 if l["event"] == "stale"], [])
        finally:
            env.cleanup()

    def test_live_lock_refuses_dead_lock_taken_over(self):
        env = SyncEnv([], mkstate(tasks=[mktask(1)]))
        lock = os.path.join(os.path.dirname(env.state_path), "whendone-tail.lock")
        try:
            with open(lock, "w") as f:
                f.write(str(os.getpid()))                # alive -> refuse
            rc, lines = self.run_follow(env, extra=("--max-cycles", "1"))
            self.assertEqual(rc, 4)
            self.assertEqual(lines[-1]["event"], "already-running")
            with open(lock, "w") as f:
                f.write("999999999")                     # dead -> take over
            rc2, _ = self.run_follow(env, extra=("--max-cycles", "1"))
            self.assertEqual(rc2, 0)
            self.assertFalse(os.path.exists(lock))       # released on exit
        finally:
            env.cleanup()

    def test_job_id_mismatch_exits_3(self):
        env = SyncEnv([], mkstate(tasks=[mktask(1)]))
        try:
            rc, lines = self.run_follow(env, extra=("--job-id", "OTHER"))
            self.assertEqual(rc, 3)
            self.assertEqual(lines[-1]["event"], "ownership-lost")
        finally:
            env.cleanup()

    def test_paused_state_exits_clean(self):
        env = SyncEnv([], mkstate(status="paused", tasks=[mktask(1)]))
        try:
            rc, lines = self.run_follow(env)
            self.assertEqual(rc, 0)
            self.assertEqual(lines[-1]["event"], "no-op")
        finally:
            env.cleanup()


class SourceCFollowTest(unittest.TestCase):
    def _follow(self, env, extra=()):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = tp.main([env.state_path, "--follow", "--interval", "0.01",
                          "--debounce", "0", "--projects-dir", env.projects, *extra])
        return rc, [json.loads(l) for l in buf.getvalue().splitlines() if l.strip()]

    def test_watcher_exits_zero_on_all_done_with_uncalibrated_label(self):
        env = SyncEnv([
            todo_entry(T0, [item("only step", "in_progress")], mid="m1"),
            todo_entry(T1, [item("only step", "completed")], mid="m2"),
        ], mkstate(source="c", tasks=[], originalTotalMin=None))
        try:
            rc, lines = self._follow(env, extra=("--max-cycles", "3"))
            self.assertEqual(rc, 0)
            self.assertEqual(lines[-1]["event"], "all-done")
            self.assertIn("uncalibrated", lines[-1].get("etaText") or "")
        finally:
            env.cleanup()

    def test_stale_fires_once_for_a_hung_mirrored_item(self):
        env = SyncEnv([todo_entry(T0, [item("long step", "in_progress")], mid="m1")],
                      mkstate(source="c", tasks=[], originalTotalMin=None))
        try:
            rc, lines = self._follow(env, extra=("--max-cycles", "3",
                                                 "--stale-min", "0.001"))
            stale = [l for l in lines if l["event"] == "stale"]
            self.assertEqual(len(stale), 1)
            self.assertEqual(stale[0]["name"], "long step")
            self.assertIsNotNone(env.state()["tasks"][0].get("staleNotifiedAt"))
        finally:
            env.cleanup()

    def test_lock_released_after_all_done(self):
        env = SyncEnv([
            todo_entry(T0, [item("only step", "completed")], mid="m1"),
        ], mkstate(source="c", tasks=[], originalTotalMin=None))
        try:
            rc, lines = self._follow(env, extra=("--max-cycles", "3"))
            self.assertEqual(lines[-1]["event"], "all-done")
            lock = os.path.join(os.path.dirname(env.state_path), "whendone-tail.lock")
            self.assertFalse(os.path.exists(lock))
        finally:
            env.cleanup()


def wf_started(aid):
    return {"type": "started", "key": "v2:" + "0" * 63 + "1", "agentId": aid}


def wf_result(aid):
    return dict(wf_started(aid), type="result", result="ok")


def agent_entry(ts, text):
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user", "content": text}}


B_A1, B_A2, B_A3 = "a" * 17, "b1" + "c" * 15, "d" * 17
BT0, BT1, BT2 = ("2026-07-18T09:05:00.000Z", "2026-07-18T09:20:00.000Z",
                 "2026-07-18T09:35:00.000Z")


def mkstate_b(**kw):
    d = mkstate(source="b", workflowRunId="wf_test01-abc",
                tasks=[dict(mktask(1, name="Scan", category="review"),
                            wdTag="scan", agentsExpected=2),
                       dict(mktask(2, name="Fix", category="judgment-coding"),
                            wdTag="fix", agentsExpected=1)])
    d.update(kw)
    return d


class WfEnv(SyncEnv):
    """SyncEnv + a workflow run dir under projects/proj/sidA/."""
    def __init__(self, state_dict, journal=(), agents=()):
        super().__init__([], state_dict)
        self.run_dir = os.path.join(self.projects, "proj", "sidA",
                                    "subagents", "workflows", "wf_test01-abc")
        os.makedirs(self.run_dir)
        write_jsonl(os.path.join(self.run_dir, "journal.jsonl"), list(journal))
        for aid, entries in agents:
            write_jsonl(os.path.join(self.run_dir, "agent-%s.jsonl" % aid),
                        list(entries))

    def finish_run(self, status="completed"):
        # Real record shape (surveyed 2026-07-19): "status" always present;
        # killed/failed runs get a record too, so tests must write one.
        wf = os.path.join(self.projects, "proj", "sidA", "workflows")
        os.makedirs(wf, exist_ok=True)
        with open(os.path.join(wf, "wf_test01-abc.json"), "w") as f:
            json.dump({"runId": "wf_test01-abc", "status": status}, f)


class SourceBObserveTest(unittest.TestCase):
    def test_phase_starts_and_counters(self):
        env = WfEnv(mkstate_b(),
                    journal=[wf_started(B_A1), wf_started(B_A2)],
                    agents=[(B_A1, [agent_entry(BT0, "[wd:scan] go")]),
                            (B_A2, [agent_entry(BT1, "[wd:scan] go")])])
        try:
            rc, lines = env.run_one_shot()
            self.assertEqual(rc, 0)
            st = env.state()
            t1 = st["tasks"][0]
            self.assertEqual(t1["status"], "running")
            self.assertEqual(t1["startedAt"],
                             tp.token_usage.parse_ts(BT0).isoformat())
            self.assertEqual((t1["agentsStarted"], t1["agentsDone"]), (2, 0))
            self.assertEqual((st["wfAgentsStarted"], st["wfAgentsDone"]), (2, 0))
            self.assertEqual(st["tasks"][1]["status"], "pending")
        finally:
            env.cleanup()

    def test_display_done_when_expected_met_and_none_inflight(self):
        env = WfEnv(mkstate_b(),
                    journal=[wf_started(B_A1), wf_result(B_A1),
                             wf_started(B_A2), wf_result(B_A2)],
                    agents=[(B_A1, [agent_entry(BT0, "[wd:scan] go"),
                                    agent_entry(BT1, "x")]),
                            (B_A2, [agent_entry(BT0, "[wd:scan] go"),
                                    agent_entry(BT2, "x")])])
        try:
            rc, lines = env.run_one_shot()
            st = env.state()
            t1 = st["tasks"][0]
            self.assertEqual(t1["status"], "done")
            self.assertEqual(t1["finishedAt"],
                             tp.token_usage.parse_ts(BT2).isoformat())
            self.assertIsNone(t1["actualMin"])  # no row before finalize (B4)
            ev = [l for l in lines if l.get("event") == "progress"]
            self.assertTrue(ev and ev[-1]["agentsDone"] == 2)
        finally:
            env.cleanup()

    def test_late_agent_reverts_display_done(self):
        st = mkstate_b()
        st["tasks"][0].update(status="done", startedAt="2026-07-18T09:05:00+00:00",
                              finishedAt="2026-07-18T09:20:00+00:00",
                              agentsStarted=2, agentsDone=2)
        env = WfEnv(st,
                    journal=[wf_started(B_A1), wf_result(B_A1),
                             wf_started(B_A2), wf_result(B_A2),
                             wf_started(B_A3)],
                    agents=[(B_A1, [agent_entry(BT0, "[wd:scan] go"),
                                    agent_entry(BT1, "x")]),
                            (B_A2, [agent_entry(BT0, "[wd:scan] go"),
                                    agent_entry(BT1, "x")]),
                            (B_A3, [agent_entry(BT2, "[wd:scan] go")])])
        try:
            env.run_one_shot()
            self.assertEqual(env.state()["tasks"][0]["status"], "running")
        finally:
            env.cleanup()

    def test_untagged_agents_counted_job_level_only(self):
        env = WfEnv(mkstate_b(),
                    journal=[wf_started(B_A1)],
                    agents=[(B_A1, [agent_entry(BT0, "no tag at all")])])
        try:
            env.run_one_shot()
            st = env.state()
            self.assertEqual(st["wfAgentsStarted"], 1)
            self.assertEqual(st["tasks"][0]["status"], "pending")
        finally:
            env.cleanup()

    def test_run_dir_missing_tail_unavailable_once(self):
        env = SyncEnv([], mkstate_b(workflowRunId="wf_absent-run1"))
        try:
            rc, lines = env.run_one_shot()
            self.assertEqual(rc, 0)
            evs = [l for l in lines if l.get("event") == "tail-unavailable"]
            self.assertEqual(len(evs), 1)
            self.assertIn("run dir", evs[0]["reason"])
        finally:
            env.cleanup()

    def test_drift_event_once_and_no_phase_transitions(self):
        env = WfEnv(mkstate_b(),
                    journal=[dict(wf_started(B_A1), key="v9:" + "0" * 64)],
                    agents=[(B_A1, [agent_entry(BT0, "[wd:scan] go")])])
        try:
            rc, lines = env.run_one_shot()
            drift = [l for l in lines if l.get("event") == "journal-format-drift"]
            self.assertEqual(len(drift), 1)
            st = env.state()
            self.assertTrue(st["wfDriftNotified"])
            self.assertEqual(st["tasks"][0]["status"], "pending")
            rc2, lines2 = env.run_one_shot()   # second cycle: no repeat
            self.assertFalse([l for l in lines2
                              if l.get("event") == "journal-format-drift"])
        finally:
            env.cleanup()

    def test_drift_and_completed_run_terminates_all_done(self):
        """FIX 1: drift (>20% bad journal lines) discovered on the SAME cycle
        the completion record already exists must not leave the watcher
        looping forever — degrade to all-done with every task marked done,
        NO calibration rows (drift means no per-phase attribution)."""
        env = WfEnv(mkstate_b(),
                    journal=[dict(wf_started(B_A1), key="v9:" + "0" * 64)],
                    agents=[(B_A1, [agent_entry(BT0, "[wd:scan] go")])])
        env.finish_run()
        try:
            with unittest.mock.patch.object(
                    tp.append_calibration, "append_obj") as ap:
                rc, lines = env.run_one_shot()
            self.assertEqual(rc, 0)
            drift = [l for l in lines if l.get("event") == "journal-format-drift"]
            self.assertEqual(len(drift), 1)
            self.assertEqual(lines[-1]["event"], "all-done")
            st = env.state()
            self.assertTrue(all(t["status"] == "done" for t in st["tasks"]))
            self.assertTrue(all(t["actualMin"] is None for t in st["tasks"]))
            ap.assert_not_called()               # no rows: agents counted, phases unknown

            # second cycle: idempotent — no re-emitted drift, stays all-done
            with unittest.mock.patch.object(
                    tp.append_calibration, "append_obj") as ap2:
                rc2, lines2 = env.run_one_shot()
            self.assertEqual(rc2, 0)
            self.assertFalse([l for l in lines2
                              if l.get("event") == "journal-format-drift"])
            self.assertEqual(lines2[-1]["event"], "all-done")
            ap2.assert_not_called()
        finally:
            env.cleanup()

    def test_killed_record_does_not_finalize(self):
        """A session kill writes the completion record too (status "killed",
        observed live 2026-07-19, B12 resume drill): the tailer must keep the
        job open — no all-done, no calibration rows, no phase stamped done off
        the dead run — so a later resume can relaunch and finish it."""
        env = WfEnv(mkstate_b(),
                    journal=[wf_started(B_A1), wf_result(B_A1)],
                    agents=[(B_A1, [agent_entry(BT0, "[wd:scan] go"),
                                    agent_entry(BT1, "done")])])
        env.finish_run(status="killed")
        try:
            with unittest.mock.patch.object(
                    tp.append_calibration, "append_obj") as ap:
                rc, lines = env.run_one_shot()
            self.assertEqual(rc, 0)
            self.assertFalse([l for l in lines if l.get("event") == "all-done"])
            st = env.state()
            self.assertNotEqual(st["tasks"][1]["status"], "done")
            self.assertTrue(all(t["actualMin"] is None for t in st["tasks"]))
            ap.assert_not_called()
        finally:
            env.cleanup()

    def test_source_x_still_unsupported(self):
        env = SyncEnv([], mkstate(source="x"))
        try:
            rc, lines = env.run_one_shot()
            self.assertEqual(lines[0]["event"], "unsupported-source")
        finally:
            env.cleanup()


class DebounceGateTest(unittest.TestCase):
    """The sync_cycle gate: suppressed change-cycles coalesce into the next
    rendered event (the follow loop drives _render_ok/_force_render)."""
    def test_suppressed_then_coalesced(self):
        env = SyncEnv([todo_entry(T0, [item("task 1", "in_progress")]),
                       todo_entry(T1, [item("task 1", "completed")])],
                      mkstate(tasks=[mktask(1), mktask(2)]))
        try:
            import argparse as ap
            args = ap.Namespace(job_id=None, projects_dir=env.projects, now=None,
                                stale_min=None, _render_ok=False)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                _, ev1 = tp.sync_cycle(env.state_path,
                                       tp.token_usage.parse_ts(T2), args)
            self.assertEqual([e for e in ev1
                              if e.get("event") in ("progress", "all-done")], [])
            self.assertTrue(args._pending)
            self.assertEqual(args._pending_names, ["task 1"])
            args._render_ok, args._force_render = True, True
            with contextlib.redirect_stdout(buf):
                _, ev2 = tp.sync_cycle(env.state_path,
                                       tp.token_usage.parse_ts(T2), args)
            prog = [e for e in ev2 if e.get("event") == "progress"][-1]
            self.assertEqual(prog["justDone"], ["task 1"])   # carried over
        finally:
            env.cleanup()

    def test_all_done_and_slip_bypass_debounce(self):
        """D11: all-done (and the slipAlert it can reveal) must render+emit in
        the SAME cycle even while the debounce window is open (_render_ok=False,
        no _force_render) — only a plain progress completion stays debounced.
        Fixture mirrors FinishCycleTest.test_slip_alert_once's slip math, but
        the completing task is the job's LAST one, so this cycle is all-done."""
        done = mktask(1, status="done", actualMin=30.0,
                      startedAt="2026-07-18T09:00:00+00:00",
                      finishedAt="2026-07-18T09:30:00+00:00")
        env = SyncEnv([todo_entry(T0, [item("task 2", "in_progress")]),
                       todo_entry(T1, [item("task 2", "completed")])],
                      mkstate(originalTotalMin=10, tasks=[done, mktask(2)]))
        try:
            import argparse as ap
            args = ap.Namespace(job_id=None, projects_dir=env.projects, now=None,
                                stale_min=None, _render_ok=False)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                _, events = tp.sync_cycle(env.state_path,
                                          tp.token_usage.parse_ts(T2), args)
            all_done_evs = [e for e in events if e.get("event") == "all-done"]
            self.assertEqual(len(all_done_evs), 1)              # not suppressed
            self.assertTrue(all_done_evs[0].get("slipAlert"))
            self.assertFalse(getattr(args, "_pending", False))  # not deferred
            self.assertTrue(env.state()["etaAlertSent"])
        finally:
            env.cleanup()


class SourceBFinalizeTest(unittest.TestCase):
    def _env(self):
        env = WfEnv(mkstate_b(),
                    journal=[wf_started(B_A1), wf_result(B_A1),
                             wf_started(B_A2), wf_result(B_A2),
                             wf_started(B_A3), wf_result(B_A3)],
                    agents=[(B_A1, [agent_entry(BT0, "[wd:scan] go"),
                                    agent_entry(BT1, "x")]),
                            (B_A2, [agent_entry(BT0, "[wd:scan] go"),
                                    agent_entry(BT1, "x")]),
                            (B_A3, [agent_entry(BT1, "[wd:fix] go"),
                                    agent_entry(BT2, "x")])])
        env.finish_run()
        return env

    def test_finalize_appends_one_row_per_phase(self):
        env = self._env()
        data_dir = os.path.join(env.dir.name, "wd-data")
        try:
            with unittest.mock.patch.object(
                    tp.append_calibration, "append_obj",
                    side_effect=lambda row, data_dir=None:
                        (True, dict(row, actualMin=15.0))) as ap:
                rc, lines = env.run_one_shot()
            self.assertEqual(rc, 0)
            rows = [c.args[0] for c in ap.call_args_list]
            self.assertEqual([r["category"] for r in rows],
                             ["review", "judgment-coding"])
            self.assertEqual(rows[0]["startedAt"],
                             tp.token_usage.parse_ts(BT0).isoformat())
            self.assertEqual(rows[0]["finishedAt"],
                             tp.token_usage.parse_ts(BT1).isoformat())
            self.assertEqual(rows[0]["rawEstimateMin"], 10)
            self.assertEqual(rows[0]["model"], "unknown")  # no meta.json written
            st = env.state()
            self.assertTrue(all(t["status"] == "done" for t in st["tasks"]))
            self.assertEqual(st["tasks"][0]["actualMin"], 15.0)
            self.assertEqual(lines[-1]["event"], "all-done")
        finally:
            env.cleanup()

    def test_finalize_never_reappends_done_phase(self):
        env = self._env()
        st = env.state()
        st["tasks"][0].update(status="done", actualMin=9.9,
                              startedAt="2026-07-18T09:05:00+00:00",
                              finishedAt="2026-07-18T09:20:00+00:00")
        with open(env.state_path, "w", encoding="utf-8") as f:
            json.dump(st, f)
        try:
            with unittest.mock.patch.object(
                    tp.append_calibration, "append_obj",
                    side_effect=lambda row, data_dir=None:
                        (True, dict(row, actualMin=15.0))) as ap:
                env.run_one_shot()
            rows = [c.args[0] for c in ap.call_args_list]
            self.assertEqual(len(rows), 1)             # only "fix"
            self.assertEqual(rows[0]["category"], "judgment-coding")
            self.assertEqual(env.state()["tasks"][0]["actualMin"], 9.9)
        finally:
            env.cleanup()

    def test_phase_without_span_gets_no_row(self):
        env = WfEnv(mkstate_b(),
                    journal=[wf_started(B_A1), wf_result(B_A1)],
                    agents=[])                        # transcript never readable
        env.finish_run()
        try:
            with unittest.mock.patch.object(
                    tp.append_calibration, "append_obj") as ap:
                rc, lines = env.run_one_shot()
            ap.assert_not_called()                    # missing evidence -> no row
            st = env.state()
            self.assertTrue(all(t["status"] == "done" for t in st["tasks"]))
            self.assertTrue(all(t["actualMin"] is None for t in st["tasks"]))
            self.assertEqual(lines[-1]["event"], "all-done")
        finally:
            env.cleanup()

    def test_append_failure_fails_soft(self):
        env = self._env()
        try:
            with unittest.mock.patch.object(
                    tp.append_calibration, "append_obj",
                    side_effect=RuntimeError("disk gone")):
                rc, lines = env.run_one_shot()
            self.assertEqual(rc, 0)
            self.assertEqual(lines[-1]["event"], "all-done")
            self.assertTrue(all(t["actualMin"] is None
                                for t in env.state()["tasks"]))
        finally:
            env.cleanup()

    def test_display_done_and_finalize_same_cycle_no_duplicate_justdone(self):
        """FIX 2: a phase that display-transitions to done AND gets finalized
        in the SAME cycle must appear once in justDone, not twice."""
        env = self._env()
        try:
            with unittest.mock.patch.object(
                    tp.append_calibration, "append_obj",
                    side_effect=lambda row, data_dir=None:
                        (True, dict(row, actualMin=15.0))):
                rc, lines = env.run_one_shot()
            self.assertEqual(rc, 0)
            all_done = [l for l in lines if l["event"] == "all-done"]
            self.assertEqual(len(all_done), 1)
            jd = all_done[0]["justDone"]
            self.assertEqual(jd, ["Scan", "Fix"])
            self.assertEqual(len(jd), len(set(jd)))
        finally:
            env.cleanup()

    def test_uniform_meta_model_reaches_row(self):
        env = self._env()
        for aid in (B_A1, B_A2):
            with open(os.path.join(env.run_dir,
                                   "agent-%s.meta.json" % aid), "w") as f:
                json.dump({"agentType": "workflow-subagent",
                           "model": "claude-haiku-4-5-20251001"}, f)
        try:
            with unittest.mock.patch.object(
                    tp.append_calibration, "append_obj",
                    side_effect=lambda row, data_dir=None:
                        (True, dict(row, actualMin=15.0))) as ap:
                env.run_one_shot()
            rows = [c.args[0] for c in ap.call_args_list]
            self.assertEqual(rows[0]["model"], "claude-haiku-4-5-20251001")
        finally:
            env.cleanup()

    def test_paused_b_state_noops_without_writes(self):
        env = self._env()
        st = env.state()
        st["status"] = "paused"
        with open(env.state_path, "w", encoding="utf-8") as f:
            json.dump(st, f)
        before = env.state()
        try:
            with unittest.mock.patch.object(
                    tp.append_calibration, "append_obj") as ap:
                rc, lines = env.run_one_shot()
            self.assertEqual(rc, 0)
            self.assertEqual(lines[-1]["event"], "no-op")
            ap.assert_not_called()
            self.assertEqual(env.state(), before)   # not one byte written
        finally:
            env.cleanup()

    def test_finalized_phase_with_null_actual_is_never_reappended(self):
        # the I2 crash window: done + bFinalized on disk, actualMin never written
        env = self._env()
        st = env.state()
        st["tasks"][0].update(status="done", bFinalized=True, actualMin=None,
                              startedAt="2026-07-18T09:05:00+00:00",
                              finishedAt="2026-07-18T09:20:00+00:00")
        with open(env.state_path, "w", encoding="utf-8") as f:
            json.dump(st, f)
        try:
            with unittest.mock.patch.object(
                    tp.append_calibration, "append_obj",
                    side_effect=lambda row, data_dir=None:
                        (True, dict(row, actualMin=15.0))) as ap:
                env.run_one_shot()
            rows = [c.args[0] for c in ap.call_args_list]
            self.assertEqual(len(rows), 1)              # only "fix", "scan" never again
            self.assertEqual(rows[0]["category"], "judgment-coding")
        finally:
            env.cleanup()

    def test_finalize_sets_bfinalized_with_done_marker(self):
        env = self._env()
        try:
            with unittest.mock.patch.object(
                    tp.append_calibration, "append_obj",
                    side_effect=lambda row, data_dir=None:
                        (True, dict(row, actualMin=15.0))):
                env.run_one_shot()
            self.assertTrue(all(t.get("bFinalized") is True
                                for t in env.state()["tasks"]))
        finally:
            env.cleanup()


class RenderOutPathHardeningTest(unittest.TestCase):
    def test_valid_absolute_html_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "out.html")
            self.assertEqual(tp._render_out_path({"artifactFile": p}), p)

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "target.html")
            open(target, "w").close()
            link = os.path.join(d, "link.html")
            try:
                os.symlink(target, link)
            except OSError as e:   # Windows: symlinks need Developer Mode or admin
                self.skipTest("cannot create symlink here: %s" % e)
            self.assertNotEqual(tp._render_out_path({"artifactFile": link}), link)

    def test_wrong_suffix_and_missing_parent_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertNotEqual(
                tp._render_out_path({"artifactFile": os.path.join(d, "x.sh")}),
                os.path.join(d, "x.sh"))
            gone = os.path.join(d, "no-such-dir", "x.html")
            self.assertNotEqual(tp._render_out_path({"artifactFile": gone}), gone)

    def test_fallback_jobid_sanitized(self):
        out = tp._render_out_path({"jobId": "../../etc/passwd"})
        self.assertNotIn("..", os.path.basename(out))
        self.assertTrue(out.startswith(tempfile.gettempdir()))


class OneShotLockTest(unittest.TestCase):
    def test_one_shot_defers_to_live_lock_holder(self):
        env = WfEnv(mkstate_b(), journal=[wf_started(B_A1), wf_result(B_A1)],
                    agents=[(B_A1, [agent_entry(BT0, "[wd:scan] go"),
                                    agent_entry(BT1, "x")])])
        env.finish_run()
        before = env.state()
        lock = tp._lock_path(env.state_path)
        with open(lock, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))                 # this test process: alive
        try:
            with unittest.mock.patch.object(
                    tp.append_calibration, "append_obj") as ap:
                rc, lines = env.run_one_shot()
            self.assertEqual(rc, 4)
            self.assertEqual(lines[-1]["event"], "already-running")
            ap.assert_not_called()
            self.assertEqual(env.state(), before)
        finally:
            os.remove(lock)
            env.cleanup()

    def test_one_shot_proceeds_past_dead_pid_lock(self):
        env = WfEnv(mkstate_b(), journal=[wf_started(B_A1), wf_result(B_A1)],
                    agents=[(B_A1, [agent_entry(BT0, "[wd:scan] go"),
                                    agent_entry(BT1, "x")])])
        env.finish_run()
        lock = tp._lock_path(env.state_path)
        with open(lock, "w", encoding="utf-8") as f:
            f.write("99999999")                        # not a live pid
        try:
            rc, lines = env.run_one_shot()
            self.assertEqual(rc, 0)                    # proceeded normally
        finally:
            with contextlib.suppress(OSError):
                os.remove(lock)
            env.cleanup()


class StopRequestedTest(unittest.TestCase):
    def _stop_path(self, env):
        return os.path.join(os.path.dirname(env.state_path), "STOP")

    def test_stop_file_emits_stop_requested_and_keeps_monitoring(self):
        env = WfEnv(mkstate_b(), journal=[wf_started(B_A1)],
                    agents=[(B_A1, [agent_entry(BT0, "[wd:scan] go")])])
        open(self._stop_path(env), "w").close()
        try:
            rc, lines = env.run_one_shot()
            kinds = [e["event"] for e in lines]
            self.assertIn("stop-requested", kinds)
            self.assertEqual(kinds.index("stop-requested"), 0)   # before source events
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(self._stop_path(env)))  # tailer never deletes
        finally:
            env.cleanup()

    def test_no_stop_file_no_event(self):
        env = WfEnv(mkstate_b(), journal=[wf_started(B_A1)],
                    agents=[(B_A1, [agent_entry(BT0, "[wd:scan] go")])])
        try:
            rc, lines = env.run_one_shot()
            self.assertNotIn("stop-requested", [e["event"] for e in lines])
        finally:
            env.cleanup()

    def test_paused_status_suppresses_stop_requested(self):
        env = WfEnv(mkstate_b(), journal=[wf_started(B_A1)],
                    agents=[(B_A1, [agent_entry(BT0, "[wd:scan] go")])])
        st = env.state(); st["status"] = "paused"
        with open(env.state_path, "w", encoding="utf-8") as f:
            json.dump(st, f)
        open(self._stop_path(env), "w").close()
        try:
            rc, lines = env.run_one_shot()
            self.assertNotIn("stop-requested", [e["event"] for e in lines])
        finally:
            env.cleanup()


if __name__ == "__main__":
    unittest.main()
