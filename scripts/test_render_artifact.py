#!/usr/bin/env python3
"""Tests for render_artifact.py. Run: python3 scripts/test_render_artifact.py -v"""
import contextlib, io, json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_artifact as ra

NOW = "2026-07-18T10:00:00+02:00"


def state(**kw):
    d = {"job": "demo job", "jobId": "20260718T0900", "planFile": "docs/plan.md",
         "artifactUrl": None, "artifactFile": None,
         "startedAt": "2026-07-18T09:00:00+02:00", "sessionIds": [],
         "originalTotalMin": 60, "pausedAt": None, "pausedTotalMin": 0,
         "resumedAt": None, "status": "running", "publish": True,
         "etaAlertSent": False, "tasks": []}
    d.update(kw)
    return d


def task(nr=1, **kw):
    d = {"nr": nr, "name": "task %d" % nr, "category": "testing",
         "rawEstimateMin": 10, "estimateMin": 10, "model": None, "effort": None,
         "actualMin": None, "status": "pending", "startedAt": None, "finishedAt": None}
    d.update(kw)
    return d


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def run_cli(state_obj, tokens_obj=None, now=NOW, extra=()):
    """Run main() in-process against temp files. Returns (rc, page_or_None, stdout_line)."""
    with tempfile.TemporaryDirectory() as td:
        sp = os.path.join(td, "state.json")
        op = os.path.join(td, "out.html")
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(state_obj, f) if isinstance(state_obj, dict) else f.write(state_obj)
        if tokens_obj is None:
            tp = "-"
        else:
            tp = os.path.join(td, "tok.json")
            with open(tp, "w", encoding="utf-8") as f:
                json.dump(tokens_obj, f)
        buf = io.StringIO()
        argv = [sp, tp, op, "--now", now, "--summary", os.path.join(td, "no-summary.md")]
        argv += list(extra)
        with contextlib.redirect_stdout(buf):
            rc = ra.main(argv)
        page = read_text(op) if os.path.exists(op) else None
        return rc, page, buf.getvalue().strip()


class TestCliContract(unittest.TestCase):
    def test_valid_state_writes_page_and_status_line(self):
        rc, page, out = run_cli(state(tasks=[task()]))
        self.assertEqual(rc, 0)
        self.assertIn("<title>WhenDone: demo job</title>", page)
        self.assertIn("RUNNING", page)
        st = json.loads(out)
        self.assertTrue(st["ok"])
        self.assertEqual(st["status"], "running")

    def test_malformed_state_exits_nonzero_no_output_file(self):
        rc, page, _ = run_cli("this is not json")
        self.assertEqual(rc, 1)
        self.assertIsNone(page)

    def test_failure_leaves_existing_output_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            sp, op = os.path.join(td, "s.json"), os.path.join(td, "out.html")
            with open(sp, "w", encoding="utf-8") as f:
                f.write("{broken")
            with open(op, "w", encoding="utf-8") as f:
                f.write("OLD CONTENT")
            with contextlib.redirect_stdout(io.StringIO()):
                rc = ra.main([sp, "-", op, "--now", NOW])
            self.assertEqual(rc, 1)
            self.assertEqual(read_text(op), "OLD CONTENT")
            self.assertFalse(os.path.exists(op + ".tmp"))

    def test_state_without_tasks_array_is_invalid(self):
        rc, page, _ = run_cli({"job": "x", "status": "running"})
        self.assertEqual(rc, 1)
        self.assertIsNone(page)

    def test_bad_now_exits_nonzero(self):
        rc, page, _ = run_cli(state(tasks=[task()]), now="yesterdayish")
        self.assertEqual(rc, 1)

    def test_naive_now_rejected(self):
        rc, _, _ = run_cli(state(tasks=[task()]), now="2026-07-18T10:00:00")
        self.assertEqual(rc, 1)

    def test_job_name_html_escaped_in_title(self):
        rc, page, _ = run_cli(state(job='<script>alert(1)</script> & "x"', tasks=[task()]))
        self.assertEqual(rc, 0)
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)


class TestHelpers(unittest.TestCase):
    def test_parse_ts_requires_timezone(self):
        self.assertIsNotNone(ra.parse_ts("2026-07-18T10:00:00+02:00"))
        self.assertIsNotNone(ra.parse_ts("2026-07-18T08:00:00Z"))
        self.assertIsNone(ra.parse_ts("2026-07-18T10:00:00"))
        self.assertIsNone(ra.parse_ts(None))
        self.assertIsNone(ra.parse_ts("not a date"))

    def test_fmt_tok(self):
        self.assertEqual(ra.fmt_tok(950), "950")
        self.assertEqual(ra.fmt_tok(412_000), "412k")
        self.assertEqual(ra.fmt_tok(38_400), "38k")
        self.assertEqual(ra.fmt_tok(3_100_000), "3.1M")

    def test_fmt_min(self):
        self.assertEqual(ra.fmt_min(11), "11 m")
        self.assertEqual(ra.fmt_min(4.5), "4.5 m")
        self.assertEqual(ra.fmt_min(8.0), "8 m")

    def test_fmt_dev_signs(self):
        self.assertEqual(ra.fmt_dev(11.4, 8), "(+42 %)")
        self.assertEqual(ra.fmt_dev(4, 6), "(−33 %)")


if __name__ == "__main__":
    unittest.main()
