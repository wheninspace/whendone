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


class ObserveTest(unittest.TestCase):
    def test_todo_start_and_completion_first_seen_wins(self):
        idx = {"task 1": 1}
        events = [
            (tp.token_usage.parse_ts(T0), "todos", [{"content": "task 1", "status": "in_progress"}]),
            (tp.token_usage.parse_ts(T1), "todos", [{"content": "task 1", "status": "completed"}]),
            (tp.token_usage.parse_ts(T2), "todos", [{"content": "task 1", "status": "completed"}]),
        ]
        obs = tp.observe(events, idx)
        self.assertEqual(obs[1]["startedAt"], tp.token_usage.parse_ts(T0).isoformat())
        self.assertEqual(obs[1]["finishedAt"], tp.token_usage.parse_ts(T1).isoformat())

    def test_dispatch_result_pair(self):
        idx = {"task 2": 2}
        events = [
            (tp.token_usage.parse_ts(T0), "dispatch", {"id": "tu-1", "description": "Task 2: task 2"}),
            (tp.token_usage.parse_ts(T1), "result", {"tool_use_id": "tu-1"}),
            (tp.token_usage.parse_ts(T2), "result", {"tool_use_id": "tu-unknown"}),
        ]
        obs = tp.observe(events, idx)
        self.assertEqual(obs[2]["startedAt"], tp.token_usage.parse_ts(T0).isoformat())
        self.assertEqual(obs[2]["finishedAt"], tp.token_usage.parse_ts(T1).isoformat())

    def test_ambiguous_name_never_matches(self):
        obs = tp.observe(
            [(tp.token_usage.parse_ts(T0), "todos", [{"content": "dup", "status": "completed"}])],
            {"dup": None})
        self.assertEqual(obs, {})


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
        for st, want in ((mkstate(status="paused", tasks=[mktask(1)]), "no-op"),
                         (mkstate(source="b", tasks=[mktask(1)]), "unsupported-source")):
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
        os.environ["WHENDONE_DATA_DIR"] = "/dev/null/impossible"
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
        env, _ = self._env([todo_entry(T0, [item("task 1", "in_progress")]),
                            todo_entry(T1, [item("task 1", "completed")])],
                           mkstate(tasks=[mktask(1), mktask(2)]))
        st = env.state(); st["artifactFile"] = os.path.join(env.dir.name, "no", "such", "dir", "a.html")
        with open(env.state_path, "w", encoding="utf-8") as f:
            json.dump(st, f)
        try:
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


if __name__ == "__main__":
    unittest.main()
