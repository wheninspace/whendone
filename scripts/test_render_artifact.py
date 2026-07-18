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


SUMMARY_MD = """# Calibration summary

Regenerated: 2026-07-18 (37 data points). Regenerated from calibration.jsonl by
scripts/calibration_summary.py at every job end.

## Per category

| Category | Factor (blended) | Data points | Confidence | Spread (IQR) |
|---|---|---|---|---|
| debugging | 1.40 | 12 | medium | 0.50–2.00 |
| documentation | — (prior 1.0) | 0 | — | — |
| testing | 1.20 | 25 | high | 0.90–1.50 |

## How to use when estimating

(prose elided)

## Per-category q1/q3 (machine-usable)

- debugging: q1=0.50 q3=2.00
- testing: q1=0.90 q3=1.50
"""


class TestLoadSummary(unittest.TestCase):
    def _load(self, text):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "s.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return ra.load_summary(path)

    def test_parses_factor_n_q1_q3(self):
        s = self._load(SUMMARY_MD)
        self.assertEqual(s["debugging"], {"factor": 1.40, "n": 12, "q1": 0.50, "q3": 2.00})
        self.assertEqual(s["testing"], {"factor": 1.20, "n": 25, "q1": 0.90, "q3": 1.50})

    def test_prior_row_is_factor_one_no_iqr(self):
        s = self._load(SUMMARY_MD)
        self.assertEqual(s["documentation"], {"factor": 1.0, "n": 0, "q1": None, "q3": None})

    def test_missing_file_returns_empty(self):
        self.assertEqual(ra.load_summary("/nonexistent/summary.md"), {})

    def test_header_and_separator_rows_ignored(self):
        s = self._load(SUMMARY_MD)
        self.assertNotIn("---", s)
        self.assertNotIn("Category", s)

    def test_parses_real_emitter_output(self):
        # Format-drift guard: parse what calibration_summary.py ACTUALLY writes today.
        import calibration_summary as cs
        rows = [json.dumps({"date": "2026-07-16", "project": "p", "job": "j",
                            "category": "testing", "rawEstimateMin": 8, "actualMin": 10.0,
                            "model": "claude-sonnet-5", "client": "cli"})] * 6
        with tempfile.TemporaryDirectory() as td:
            jp, op = os.path.join(td, "c.jsonl"), os.path.join(td, "s.md")
            with open(jp, "w", encoding="utf-8") as f:
                f.write("\n".join(rows) + "\n")
            self.assertEqual(cs.main(jp, op), 0)
            s = ra.load_summary(op)
        self.assertEqual(s["testing"]["n"], 6)
        self.assertIsNotNone(s["testing"]["q1"])
        self.assertGreater(s["testing"]["factor"], 1.0)


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


class TestComputationCore(unittest.TestCase):
    def setUp(self):
        self.now = ra.parse_ts(NOW)  # 10:00+02:00

    def test_units_groups_by_group_field(self):
        ts = [task(1), task(2, group="g1"), task(3, group="g1"), task(4)]
        us = ra.units(ts)
        self.assertEqual([len(u) for u in us], [1, 2, 1])
        self.assertEqual([t["nr"] for t in us[1]], [2, 3])

    def test_v1_tasks_without_group_are_sequential(self):
        us = ra.units([task(1), task(2)])
        self.assertEqual([len(u) for u in us], [1, 1])

    def test_remaining_pending_task_is_estimate(self):
        self.assertEqual(ra.unit_remaining([task(estimateMin=10)], self.now), 10)

    def test_remaining_pending_group_is_max(self):
        u = [task(1, group="g", estimateMin=5), task(2, group="g", estimateMin=8)]
        self.assertEqual(ra.unit_remaining(u, self.now), 8)

    def test_remaining_running_uses_inflight_rule(self):
        # started 09:55, est 10 -> max(0.2*10, 10-5) = 5
        t = task(status="running", startedAt="2026-07-18T09:55:00+02:00", estimateMin=10)
        self.assertAlmostEqual(ra.unit_remaining([t], self.now), 5.0)
        # started 09:51 (elapsed 9) -> max(2, 1) = 2 — never collapses to 0
        t["startedAt"] = "2026-07-18T09:51:00+02:00"
        self.assertAlmostEqual(ra.unit_remaining([t], self.now), 2.0)

    def test_remaining_done_unit_is_zero(self):
        t = task(status="done", actualMin=12.0)
        self.assertEqual(ra.unit_remaining([t], self.now), 0)

    def test_remaining_running_group_max_over_unfinished(self):
        u = [task(1, group="g", status="done", actualMin=4.0, estimateMin=5),
             task(2, group="g", status="running", estimateMin=10,
                  startedAt="2026-07-18T09:55:00+02:00"),
             task(3, group="g", status="pending", estimateMin=3)]
        # unfinished: max( max(2, 5)=5, 3 ) = 5
        self.assertAlmostEqual(ra.unit_remaining(u, self.now), 5.0)

    def test_slip_value_done_uses_actual(self):
        self.assertEqual(ra.unit_slip_value([task(status="done", actualMin=25.0)], self.now), 25.0)

    def test_slip_value_done_null_actual_derives_from_timestamps(self):
        t = task(status="done", actualMin=None,
                 startedAt="2026-07-18T09:00:00+02:00", finishedAt="2026-07-18T09:30:00+02:00")
        self.assertEqual(ra.unit_slip_value([t], self.now), 30.0)

    def test_slip_value_running_is_max_of_estimate_and_elapsed(self):
        t = task(status="running", estimateMin=10, startedAt="2026-07-18T09:30:00+02:00")
        self.assertEqual(ra.unit_slip_value([t], self.now), 30.0)  # elapsed 30 > est 10

    def test_slip_group_contributes_max_not_sum(self):
        # F1: a fully DONE group contributes MAX of members' actualMin, never the sum
        u = [task(1, group="g", status="done", actualMin=20.0),
             task(2, group="g", status="done", actualMin=15.0)]
        self.assertEqual(ra.unit_slip_value(u, self.now), 20.0)

    def test_total_estimate_uses_max_per_group(self):
        ts = [task(1, estimateMin=10), task(2, group="g", estimateMin=5),
              task(3, group="g", estimateMin=8)]
        self.assertEqual(ra.total_estimate(ra.units(ts)), 18.0)

    def test_elapsed_running_subtracts_paused_total(self):
        s = state(pausedTotalMin=10)
        self.assertAlmostEqual(ra.elapsed_min(s, self.now), 50.0)  # 60 wall - 10 paused

    def test_elapsed_paused_freezes_at_pausedAt(self):
        s = state(status="paused", pausedAt="2026-07-18T09:40:00+02:00")
        self.assertAlmostEqual(ra.elapsed_min(s, self.now), 40.0)

    def test_elapsed_done_ends_at_latest_finishedAt(self):
        s = state(status="done", tasks=[
            task(1, status="done", finishedAt="2026-07-18T09:20:00+02:00"),
            task(2, status="done", finishedAt="2026-07-18T09:50:00+02:00")])
        self.assertAlmostEqual(ra.elapsed_min(s, self.now), 50.0)

    def test_derived_actual_min_floor(self):
        t = task(status="done", startedAt="2026-07-18T09:00:00+02:00",
                 finishedAt="2026-07-18T09:00:10+02:00")
        self.assertEqual(ra.derived_actual(t), 0.5)


MEDIUM_SUMMARY = {"testing": {"factor": 1.0, "n": 12, "q1": 0.50, "q3": 2.00}}
HIGH_SUMMARY = {"testing": {"factor": 1.2, "n": 25, "q1": 0.90, "q3": 1.50}}


class TestInterval(unittest.TestCase):
    def setUp(self):
        self.now = ra.parse_ts(NOW)

    def test_low_confidence_flat_50(self):
        lo, hi, tier, widened = ra.task_band(task(estimateMin=10), {})
        self.assertEqual((lo, hi, tier, widened), (5.0, 15.0, "low", False))

    def test_medium_widens_to_envelope(self):
        # flat ±30% on est 10 = [7,13]; q-band on raw 10 = [10*0.5, 10*2.0] = [5,20]
        lo, hi, tier, widened = ra.task_band(task(), MEDIUM_SUMMARY)
        self.assertEqual((lo, hi, tier, widened), (5.0, 20.0, "medium", True))

    def test_medium_no_widening_when_flat_band_wider(self):
        s = {"testing": {"factor": 1.0, "n": 12, "q1": 0.95, "q3": 1.05}}
        lo, hi, tier, widened = ra.task_band(task(), s)
        self.assertEqual((lo, hi, tier, widened), (7.0, 13.0, "medium", False))

    def test_high_uses_pure_iqr_band(self):
        # raw 10: [10*min(0.9,1.2), 10*max(1.5,1.2)] = [9,15]
        lo, hi, tier, widened = ra.task_band(task(rawEstimateMin=10, estimateMin=12),
                                             HIGH_SUMMARY)
        self.assertEqual((lo, hi, tier, widened), (9.0, 15.0, "high", False))

    def test_interval_group_takes_max_not_sum(self):
        us = ra.units([task(1, group="g", estimateMin=10), task(2, group="g", estimateMin=10)])
        lowsum, highsum, w, h = ra.interval(us, {})
        self.assertEqual((lowsum, highsum), (5.0, 15.0))  # MAX per group, not 10/30

    def test_eta_text_nominal(self):
        # two pending tasks est 10, no summary: remaining 20 -> ETA 10:20;
        # band [10,30] -> N = 10
        s = state(tasks=[task(1), task(2)])
        txt = ra.eta_text(s, ra.units(s["tasks"]), {}, self.now)
        self.assertEqual(txt, "Done ~10:20 ± 10 min (nominal)")

    def test_eta_text_widened_marker(self):
        # per task widened band [5,20]; two tasks: low 10 high 40; remaining 20
        s = state(tasks=[task(1), task(2)])
        txt = ra.eta_text(s, ra.units(s["tasks"]), MEDIUM_SUMMARY, self.now)
        self.assertEqual(txt, "Done ~10:20 (−10/+20 min) (widened to measured spread)")

    def test_eta_text_high_asymmetric_no_marker(self):
        # est 12 raw 10, band [9,15] per task; two tasks: remaining 24, low 18, high 30
        s = state(tasks=[task(1, rawEstimateMin=10, estimateMin=12),
                         task(2, rawEstimateMin=10, estimateMin=12)])
        txt = ra.eta_text(s, ra.units(s["tasks"]), HIGH_SUMMARY, self.now)
        self.assertEqual(txt, "Done ~10:24 (−6/+6 min)")

    def test_ab_clamped_at_zero(self):
        # High-confidence running task, est 12 raw 10, started 09:51 (elapsed 9):
        # remaining = max(0.2*12, 12-9) = 3; band [9,15] -> A = max(0, 3-9) = 0 (clamped,
        # would be negative), B = 15-3 = 12. (The clamp only shows in asymmetric branches;
        # the nominal branch uses half band width and never computes A/B.)
        s = state(tasks=[task(rawEstimateMin=10, estimateMin=12, status="running",
                              startedAt="2026-07-18T09:51:00+02:00")])
        txt = ra.eta_text(s, ra.units(s["tasks"]), HIGH_SUMMARY, self.now)
        self.assertIn("(−0/+12 min)", txt)

    def test_interval_never_zero_while_running(self):
        # degenerate: est 0 task running -> bands 0; floor B to 1
        s = state(tasks=[task(estimateMin=0, rawEstimateMin=0, status="running",
                              startedAt="2026-07-18T09:59:00+02:00")])
        txt = ra.eta_text(s, ra.units(s["tasks"]), {}, self.now)
        self.assertNotIn("± 0 min", txt)
        self.assertNotIn("+0 min", txt)

    def test_source_c_pace_based(self):
        # 2 of 4 done, elapsed 60 -> pace 30/task -> 2 left -> ETA 11:00
        s = state(source="c", tasks=[task(1, status="done"), task(2, status="done"),
                                     task(3), task(4)])
        txt = ra.eta_text(s, ra.units(s["tasks"]), {}, self.now)
        self.assertEqual(txt, "Done ~11:00 (uncalibrated — pace-based)")

    def test_source_c_no_completions_yet(self):
        s = state(source="c", tasks=[task(1), task(2)])
        txt = ra.eta_text(s, ra.units(s["tasks"]), {}, self.now)
        self.assertEqual(txt, "ETA not yet known (uncalibrated)")


if __name__ == "__main__":
    unittest.main()
