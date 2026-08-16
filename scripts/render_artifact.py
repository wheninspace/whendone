#!/usr/bin/env python3
"""Render the WhenDone progress artifact as a FULL HTML page from state + token JSON.

Usage:
  python3 render_artifact.py <state.json> <token.json> <out.html> --now <ISO8601-with-tz>
                             [--summary <calibration-summary.md>]
                             [--push-status rc|uncertain|unavailable] [--superseded]

<token.json> is a file holding one token_usage.py output object, or `-` for none.

Contract (design spec §4.2 / round-3 F6):
- Python 3 stdlib only, no network.
- html.escape() on EVERY interpolated field; untrusted strings land in text nodes only
  (defense-in-depth alongside the Artifact CSP).
- ETA, interval, deviation and the 150%-slip check are computed HERE, from the fixed
  formulas in references/formulas.md ("one fixed rule — never improvise"). The model
  consumes this script's JSON status line and never recomputes them.
- Reads calibration-summary.md itself for factor/q1/q3 — the factor never enters model
  context. Missing/unreadable summary => factor 1.0, low confidence, flat bands (fail-soft).
- Same output path at every republish => same claude.ai URL.
- On ANY failure: non-zero exit, stderr note, NO partial HTML (temp file + os.replace;
  an existing output file is left untouched). The caller falls back to the in-chat
  table; visibility never blocks work.
- Success prints ONE JSON line to stdout:
  {"ok": true, "status": "...", "etaText": "...", "slipAlert": bool,
   "estimateTotalMin": N, "done": N, "total": M[, "slipTotalMin": N]}
  estimateTotalMin = the fixed originalTotalMin aggregation (sequential sum + MAX per
  parallel group) over every task's adjusted estimateMin, computed in code so the model
  never does that arithmetic (F1 bug class).
"""
import argparse, html, json, os, re, sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from token_usage import display_name
except ImportError:  # shipped side-by-side; degrade rather than fail (visibility never blocks)
    def display_name(model_id):
        return model_id.capitalize() if isinstance(model_id, str) else model_id

MINUS = "−"
# Exact suffixes eta_text() appends for the two calibration-honesty markers (see
# eta_text() below). Used only to split the ETA headline for display — the marker
# literals themselves live in eta_text() and must stay in sync with this tuple.
ETA_DIM_MARKERS = ("(widened to measured spread)", "(default band — little history)")
PUSH_STATUS = {
    "rc": "Push notifications: via Remote Control.",
    "uncertain": "Push notifications: uncertain delivery — requires Remote Control.",
    "unavailable": "Push notifications: unavailable in this environment.",
}
DEFAULT_SUMMARY = os.path.join("~", ".claude", "whendone-data", "calibration-summary.md")

CSS = """
:root { --bg:#fff; --fg:#1a1a1a; --dim:#666; --card:#f5f5f5; --link:#1257b0;
        --running:#1e6fd9; --paused:#a66800; --done:#1e8a3c; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#1a1a1a; --fg:#eee; --dim:#999; --card:#2a2a2a; --link:#6db3ff; } }
:root[data-theme="dark"] { --bg:#1a1a1a; --fg:#eee; --dim:#999; --card:#2a2a2a; --link:#6db3ff; }
:root[data-theme="light"] { --bg:#fff; --fg:#1a1a1a; --dim:#666; --card:#f5f5f5; --link:#1257b0; }
body { background:var(--bg); color:var(--fg); font:16px/1.5 system-ui,sans-serif;
       max-width:640px; margin:0 auto; padding:16px; }
a { color:var(--link); }
.banner { padding:12px 16px; border-radius:8px; color:#fff; font-weight:600; }
.banner.running{background:var(--running)} .banner.paused{background:var(--paused)}
.banner.done{background:var(--done)} .banner.dead{background:#8a1e1e}
.banner a { color:#fff; }
.eta { background:var(--card); border-radius:8px; padding:12px 16px; margin:12px 0; }
.eta strong { font-size:1.3em; }
table { width:100%; border-collapse:collapse; table-layout:fixed; }
td,th { padding:6px 8px; text-align:left; border-bottom:1px solid var(--card);
        overflow-wrap:anywhere; vertical-align:top; }
th { overflow-wrap:normal; }
th:nth-child(1),td:nth-child(1) { width:2em; }
th:nth-child(2),td:nth-child(2) { width:auto; }
th:nth-child(3),td:nth-child(3) { width:18%; }
th:nth-child(4),td:nth-child(4) { width:3.6em; white-space:nowrap; }
th:nth-child(5),td:nth-child(5) { width:26%; }
td:nth-child(5) .dev { white-space:nowrap; }
tr.total td { border-top:2px solid var(--dim); border-bottom:none; font-weight:600; }
.dim { color:var(--dim); font-size:.9em; }
.pause-box { border:2px solid var(--paused); border-radius:8px; padding:12px 16px; margin:12px 0; }
@media (max-width:480px) {
  body { font-size:15px; padding:12px; }
  td,th { padding:5px 5px; }
  th:nth-child(1),td:nth-child(1) { width:1.7em; }
  th:nth-child(3),td:nth-child(3) { width:17%; }
}
"""


def esc(s):
    return html.escape(str(s), quote=True)


def parse_ts(s):
    """ISO 8601 WITH timezone -> aware datetime; anything else (incl. naive) -> None."""
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


def minutes(a, b):
    return (b - a).total_seconds() / 60.0


def hhmm(dt, ref):
    """HH:MM in the timezone of the --now argument (ref)."""
    return dt.astimezone(ref.tzinfo).strftime("%H:%M")


def fmt_tok(n):
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return "%dk" % round(n / 1000)
    return "%.1fM" % (n / 1_000_000)


def _fmt_hm(mm):
    h, m = divmod(mm, 60)
    return "%d h" % h if m == 0 else "%d h %d m" % (h, m)


def fmt_min(m):
    """Hybrid human display: < 1 min -> whole seconds, < 60 min -> whole minutes,
    >= 60 displayed minutes -> h+m (N7). Display-only rounding — ETA/interval/sum
    math stays on the raw values."""
    m = float(m)
    seconds = int(m * 60 + 0.5)
    if seconds < 60:
        return "%d s" % seconds
    mm = int(m + 0.5)
    return _fmt_hm(mm) if mm >= 60 else "%d m" % mm


def fmt_min_s(m):
    """m+s (h+m+s from one hour, N7) precision for STATEMENTS OF FACT — the job-end
    "took", Actual column, delegated split, orchestration line. Estimates stay on
    fmt_min's whole-minute display: two precise numbers that differ read as
    different quantities, two rounded ones read as a contradiction."""
    seconds = int(float(m) * 60 + 0.5)
    mm, ss = divmod(seconds, 60)
    if mm >= 60:
        h, m2 = divmod(mm, 60)
        if ss == 0:
            return _fmt_hm(mm)
        return "%d h %d m %d s" % (h, m2, ss)
    if mm == 0:
        return "%d s" % ss
    if ss == 0:
        return "%d m" % mm
    return "%d m %d s" % (mm, ss)


def fmt_dev(actual, est):
    """Deviation baked into the Actual column: '(+38 %)' / '(−33 %)' (U+2212 minus)."""
    if not est:
        return ""
    p = round(round((actual - est) / est * 100, 9))
    return "(+%d %%)" % p if p >= 0 else "(%s%d %%)" % (MINUS, -p)


def token_map(tokens):
    """nr -> token entry, or None when token data is unavailable (omit all token
    elements — no error text)."""
    if not isinstance(tokens, dict) or not tokens.get("available"):
        return None
    return {e.get("nr"): e for e in tokens.get("tasks") or [] if isinstance(e, dict)}


def _spent(entry):
    return int(entry.get("output") or 0) + int(entry.get("freshInput") or 0)


def task_rows(tasks, tmap, now, job_status=None):
    out = []
    shown_groups = set()
    for t in tasks:
        if not isinstance(t, dict):
            continue
        status = t.get("status")
        icon = {"done": "✅", "running": "\U0001f504"}.get(status, "⬜")
        if job_status == "paused" and status == "running":
            icon = "⏸️"          # a paused job's mid-flight task never spins
        name = esc(t.get("name") or "task %s" % t.get("nr"))
        if t.get("model"):
            line = display_name(t["model"])
            if t.get("effort"):
                line = "%s · %s effort" % (line, t["effort"])
            name += '<br><span class="dim">%s</span>' % esc(line)
        if t.get("unconfirmed"):
            # D9: a display-close with no todo transition ever observed — no
            # calibration row was logged for this task (see formulas.md). Mark it
            # dim rather than hide it: the artifact must stay honest about which
            # closes are provisional.
            name += '<br><span class="dim">unconfirmed — closed on subagent result</span>'
        dm = t.get("delegatedMin")
        if status == "done" and isinstance(dm, (int, float)) \
                and not isinstance(dm, bool):
            # D9: delegated = agent-minutes over matched dispatch->result spans
            # (Task 3); with parallel matched dispatches this can exceed the
            # task's own wall span, so lead/review clamps at 0 rather than going
            # negative (formulas.md's "Orchestration line & delegated split").
            a0 = display_actual(t)
            if a0 is not None:
                name += ('<br><span class="dim">delegated %s · lead/review %s</span>'
                         % (esc(fmt_min_s(dm)), esc(fmt_min_s(max(a0 - dm, 0)))))
        if isinstance(t.get("agentsDone"), int) and (
                isinstance(t.get("agentsExpected"), int)
                or isinstance(t.get("agentsStarted"), int)):
            exp = t.get("agentsExpected")
            name += '<br><span class="dim">%s</span>' % esc(
                "%d/%s agents" % (t["agentsDone"],
                                  exp if isinstance(exp, int) else "?"))
        cat = esc(t.get("category")) if t.get("category") else "—"
        est = task_est(t)
        est_txt = esc(fmt_min(est)) if est else "—"
        if status == "done":
            a = display_actual(t)
            if a is not None and est:
                # separate nowrap islands: the line may wrap BETWEEN value and
                # deviation, so a narrow Actual column never widens the page
                actual = ('<span class="dev">%s</span> <span class="dev">%s</span>'
                          % (esc(fmt_min_s(a)), esc(fmt_dev(a, est))))
            elif a is not None:
                actual = '<span class="dev">%s</span>' % esc(fmt_min_s(a))
            else:
                actual = "—"
        elif status == "running":
            s = parse_ts(t.get("startedAt"))
            el = minutes(s, now) if s else 0.0
            actual = (esc("overrunning by %s" % fmt_min(el - est))
                      if est and el > est else "—")
        else:
            actual = "—"
        entry = tmap.get(t.get("nr")) if tmap else None
        if entry:
            if entry.get("overlap"):
                # M1: one combined ≈-figure per group (MAX over members — each
                # overlapping window already contains the shared usage), on the group's
                # first row only; never a precise-looking per-member number.
                g = t.get("group")
                if g is None:
                    actual += ('<br><span class="dim">≈%s tok (group)</span>'
                               % esc(fmt_tok(_spent(entry))))
                elif g not in shown_groups:
                    shown_groups.add(g)
                    vals = [_spent(tmap[m.get("nr")]) for m in tasks
                            if isinstance(m, dict) and m.get("group") == g
                            and tmap.get(m.get("nr"))]
                    actual += ('<br><span class="dim">≈%s tok (group)</span>'
                               % esc(fmt_tok(max(vals) if vals else _spent(entry))))
            elif _spent(entry):
                actual += ('<br><span class="dim">%s tok</span>'
                           % esc(fmt_tok(_spent(entry))))
        out.append("<tr><td>%s</td><td>%s</td>"
                   '<td class="dim">%s</td><td>%s</td><td>%s</td></tr>'
                   % (icon, name, cat, est_txt, actual))
    real = [t for t in tasks if isinstance(t, dict)]
    if real:
        # Totals row: plain column arithmetic — Est over every estimated task,
        # Actual (work minutes) over done tasks so far. NOT group-aware
        # walltime: the eta block above stays the walltime authority.
        # Deviation only once every task is done — a partial sum against the
        # full estimate would read as a huge false underrun mid-run.
        est_sum = sum(task_est(t) for t in real)
        acts = [a for a in (display_actual(t) for t in real
                            if t.get("status") == "done") if a is not None]
        est_cell = esc(fmt_min(est_sum)) if est_sum else "—"
        if acts:
            a_sum = sum(acts)
            if est_sum and all(t.get("status") == "done" for t in real):
                act_cell = ('<span class="dev">%s</span> <span class="dev">%s</span>'
                            % (esc(fmt_min_s(a_sum)), esc(fmt_dev(a_sum, est_sum))))
            else:
                act_cell = '<span class="dev">%s</span>' % esc(fmt_min_s(a_sum))
        else:
            act_cell = "—"
        out.append('<tr class="total"><td></td><td>Total'
                   '<br><span class="dim">sum of subtasks</span></td>'
                   '<td class="dim"></td><td>%s</td><td>%s</td></tr>'
                   % (est_cell, act_cell))
    return "\n".join(out)


def render(state, tokens, summary, now, push_status, superseded):
    """Assemble the full page + the JSON status dict. Raises on structurally invalid
    state (caller turns that into exit 1 / no partial HTML)."""
    if not isinstance(state, dict) or not isinstance(state.get("tasks"), list):
        raise ValueError("state file invalid: expected an object with a tasks array")
    tasks = [t for t in state["tasks"] if isinstance(t, dict)]
    us = units(tasks)
    job = str(state.get("job") or "job")
    status = state.get("status") or "running"
    done_count = sum(1 for t in tasks if t.get("status") == "done")
    total = len(tasks)
    tmap = token_map(tokens)
    now_hhmm = now.strftime("%H:%M")

    if superseded:
        banner = ('<div class="banner dead">⚠️ SUPERSEDED — this job\'s '
                  "state was discarded; this page will not update (last updated %s)</div>"
                  % now_hhmm)
    else:
        cls, label, icon = {"paused": ("paused", "PAUSED", "⏸️"),
                            "done": ("done", "DONE", "✅")}.get(
                                status, ("running", "RUNNING", "\U0001f504"))
        banner = '<div class="banner %s">%s %s — last updated %s</div>' % (
            cls, icon, label, now_hhmm)

    start = parse_ts(state.get("startedAt"))
    el = elapsed_min(state, now)
    if status == "done":
        if state.get("source") == "c":
            # Source C is never calibrated (references/source-c.md): quote the same
            # "(uncalibrated)" label eta_text() uses mid-job, verbatim, at job end too —
            # no elapsed/estimate arithmetic to present since none was ever calibrated.
            etxt = "Done (uncalibrated)"
        else:
            etxt = "Done — took %s" % (fmt_min_s(el) if el is not None else "?")
            orig = state.get("originalTotalMin")
            if isinstance(orig, (int, float)) and not isinstance(orig, bool):
                etxt += " (estimated %s)" % fmt_min(orig)
    elif status == "paused":
        etxt = "Paused — %d of %d subtasks done" % (done_count, total)
    else:
        etxt = eta_text(state, us, summary, now)
    # Display-only: the two honesty-band markers are long and visually dominant when
    # bolded (they wrap to a second bold line on mobile). Render them as dim small text
    # after the bold headline instead of inside it. etxt itself (and the JSON etaText
    # field derived from it) is untouched — this only changes how the string is split
    # for HTML. Matched by exact suffix, not regex, so a job name or "(estimated N min)"
    # is never mistaken for a marker; "(uncalibrated)" is intentionally not in this list.
    head, marker = etxt, None
    for suf in ETA_DIM_MARKERS:
        if etxt.endswith(suf):
            head, marker = etxt[: -len(suf)].rstrip(), suf
            break
    if marker:
        lines = ['<strong>%s</strong> <span class="dim">%s</span>' % (esc(head), esc(marker))]
    else:
        lines = ["<strong>%s</strong>" % esc(etxt)]
    meta = []
    if start:
        meta.append("Started %s" % hhmm(start, now))
    if el is not None:
        meta.append("%s elapsed" % fmt_min(el))
    meta.append("%d of %d subtasks done" % (done_count, total))
    lines.append('<span class="dim">%s</span>' % esc(" · ".join(meta)))
    if isinstance(tokens, dict) and tokens.get("available"):
        j = tokens.get("job") or {}
        lines.append('<span class="dim">Tokens: %s spent · %s cache reads</span>'
                     % (esc(fmt_tok(_spent(j))), esc(fmt_tok(int(j.get("cacheRead") or 0)))))
    br = "<br>\n"
    eta_block = '<div class="eta">%s</div>' % br.join(lines)

    slip_alert = False
    slip_total = None
    if status == "running" and not superseded:
        orig = state.get("originalTotalMin")
        if isinstance(orig, (int, float)) and not isinstance(orig, bool) and orig > 0:
            slip_total = sum(unit_slip_value(u, now) for u in us)
            slip_alert = slip_total > 1.5 * orig and not state.get("etaAlertSent")

    pause_box = ""
    if status == "paused" and not superseded:
        nxt = next((t.get("name") for t in tasks if t.get("status") != "done"), None)
        plan = state.get("planFile")
        pause_box = ('<div class="pause-box"><strong>How to resume:</strong> open this '
                     'project in Claude Code and say "resume the whendone job". '
                     "Job: %s · Plan: %s · Next subtask: %s. A new session finds "
                     "the state via <code>.claude/whendone-state.json</code>.</div>\n"
                     % (esc(job), esc(plan) if plan else "—",
                        esc(nxt) if nxt else "—"))

    if status == "done" and not superseded:
        footer = '<p class="dim">Job finished — this page is final.</p>'
    else:
        footer = ('<p class="dim">Stop: type "stop after the current subtask" in the '
                  "chat or create the file <code>.claude/STOP</code> in the project "
                  "root. %s</p>" % esc(PUSH_STATUS[push_status]))

    wf_line = ""
    if isinstance(state.get("wfAgentsStarted"), int):
        d = state.get("wfAgentsDone")
        d = d if isinstance(d, int) else 0
        wf_line = '<p class="dim">%s</p>\n' % esc(
            "Workflow agents: %s/%s finished" % (d, state["wfAgentsStarted"]))

    # D9 (invariant D0): every elapsed minute not inside a task's own span belongs
    # to no task at all — between-subtask orchestration (dispatching, reading
    # results, deciding next steps). Source C has no calibrated elapsed baseline
    # to subtract against (references/source-c.md), so it never gets this line.
    orch = 0.0
    if el is not None and state.get("source") != "c":
        orch = max(0.0, el - _span_union_min(tasks, now))
    orch_line = ""
    if orch >= 1:
        orch_line = ('<p class="dim">%s</p>\n'
                     % esc("Between-subtask orchestration: %s" % fmt_min_s(orch)))

    page = ("<title>%s</title>\n<style>%s</style>\n%s\n%s\n"
            '<table>\n<tr><th></th><th>Subtask</th><th class="dim">Category</th>'
            "<th>Est.</th><th>Actual</th></tr>\n%s\n</table>\n%s%s%s%s"
            % (esc("WhenDone: " + job), CSS, banner, eta_block,
               task_rows(tasks, tmap, now, status), wf_line, orch_line,
               pause_box, footer))

    stat = {"ok": True, "status": "superseded" if superseded else status,
            "etaText": etxt, "slipAlert": bool(slip_alert),
            "estimateTotalMin": round(total_estimate(us), 1),
            "done": done_count, "total": total,
            "orchestrationMin": round(orch, 1)}
    if slip_total is not None:
        stat["slipTotalMin"] = round(slip_total, 1)
    return page, stat


TABLE_ROW = re.compile(r"^\|\s*([a-z][a-z-]*)\s*\|\s*([0-9.]+|— \(prior 1\.0\))\s*\|\s*(\d+)\s*\|")
Q_LINE = re.compile(r"^- ([a-z][a-z-]*): q1=([0-9.]+) q3=([0-9.]+)\s*$")


def load_summary(path):
    """Parse calibration-summary.md (the exact format calibration_summary.py emits) into
    {category: {"factor", "n", "q1", "q3"}}. The factor stays script-side — it never
    enters model context (F6). Missing/unreadable file or unmatched lines are skipped:
    absent categories fall back to factor 1.0 / n 0 / flat ±50% bands (fail-soft)."""
    cats = {}
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return cats
    for line in text.splitlines():
        line = line.strip()
        m = TABLE_ROW.match(line)
        if m:
            factor = 1.0 if m.group(2).startswith("—") else float(m.group(2))
            cats[m.group(1)] = {"factor": factor, "n": int(m.group(3)),
                                "q1": None, "q3": None}
            continue
        q = Q_LINE.match(line)
        if q and q.group(1) in cats:
            cats[q.group(1)]["q1"] = float(q.group(2))
            cats[q.group(1)]["q3"] = float(q.group(3))
    return cats


def task_est(t):
    """Adjusted estimate for display/ETA — frozen at declaration in the state file.
    (The summary's CURRENT factor is deliberately NOT re-applied here: factors can
    shift between declaration and a resume, and the slip check compares against
    originalTotalMin, which was aggregated from these frozen values.)"""
    for k in ("estimateMin", "rawEstimateMin"):
        v = t.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return float(v)
    return 0.0


def task_raw(t):
    v = t.get("rawEstimateMin")
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
        return float(v)
    return task_est(t)


def display_actual(t):
    """Display-only wall span of a done task: the raw startedAt->finishedAt
    span — the 0.5-min floor is a calibration-LOG rule, not a fact about the
    clock — falling back to the logged actualMin when timestamps are missing.
    ETA/slip math stays on derived_actual (references/formulas.md's normative rule)."""
    s, e = parse_ts(t.get("startedAt")), parse_ts(t.get("finishedAt"))
    if s and e and e >= s:
        return minutes(s, e)
    return derived_actual(t)


def derived_actual(t):
    """A done task's actualMin: the logged value, else derived from its own timestamps
    (same rule as append_calibration.py: one decimal, minimum 0.5), else None."""
    v = t.get("actualMin")
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
        return float(v)
    s, e = parse_ts(t.get("startedAt")), parse_ts(t.get("finishedAt"))
    if s and e and e >= s:
        return max(0.5, round(minutes(s, e), 1))
    return None


def units(tasks):
    """Ordered aggregation units: [task] singletons for sequential tasks; tasks sharing
    the same non-null `group` value (state-model v2) form ONE unit, positioned at the
    first member. v1 state files (no group field) yield all-singleton units."""
    seen, out = {}, []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        g = t.get("group")
        if g is None:
            out.append([t])
        elif g in seen:
            seen[g].append(t)
        else:
            seen[g] = [t]
            out.append(seen[g])
    return out


def unit_remaining(unit, now):
    """One unit's contribution to `remaining` (references/formulas.md ETA computation): 0 when
    every member is done; else MAX over unfinished members of max(0.2×est, est−elapsed).
    A member with no startedAt has elapsed 0 and contributes its full estimate, which
    makes an all-pending group contribute MAX of member estimates — the same rule."""
    vals = []
    for t in unit:
        if t.get("status") == "done":
            continue
        est = task_est(t)
        s = parse_ts(t.get("startedAt"))
        elapsed = minutes(s, now) if s else 0.0
        vals.append(max(0.2 * est, est - elapsed))
    return max(vals) if vals else 0.0


def unit_slip_value(unit, now):
    """One unit's contribution to the 150%-slip left side — the SAME aggregation as
    originalTotalMin (F1: sequential sum + MAX per parallel group, BOTH sides). Per
    member: done → actualMin (derived when the log write was skipped; estimate as the
    last resort), running → max(estimate, elapsed), pending → estimate."""
    vals = []
    for t in unit:
        est = task_est(t)
        st = t.get("status")
        if st == "done":
            a = derived_actual(t)
            vals.append(a if a is not None else est)
        elif st == "running":
            s = parse_ts(t.get("startedAt"))
            elapsed = minutes(s, now) if s else 0.0
            vals.append(max(est, elapsed))
        else:
            vals.append(est)
    return max(vals) if vals else 0.0


def total_estimate(us):
    """The fixed originalTotalMin aggregation (sequential sum + MAX per parallel group)
    over every task's adjusted estimateMin. Exported on the status line as
    estimateTotalMin so the model copies it into the state file at job start instead of
    doing the arithmetic itself."""
    return sum(max(task_est(t) for t in u) for u in us if u)


def elapsed_min(state, now):
    """Elapsed = endpoint − job startedAt − pausedTotalMin, floored at 0. Endpoint by
    status: running → now; paused → pausedAt (fallback now); done → latest task
    finishedAt (fallback now)."""
    start = parse_ts(state.get("startedAt"))
    if not start:
        return None
    paused_total = state.get("pausedTotalMin")
    if not isinstance(paused_total, (int, float)) or isinstance(paused_total, bool):
        paused_total = 0
    status = state.get("status")
    end = now
    if status == "paused":
        end = parse_ts(state.get("pausedAt")) or now
    elif status == "done":
        ends = [parse_ts(t.get("finishedAt")) for t in state.get("tasks") or []
                if isinstance(t, dict)]
        ends = [e for e in ends if e]
        end = max(ends) if ends else now
    return max(0.0, minutes(start, end) - paused_total)


def _span_union_min(tasks, now):
    """D9: merged-interval minutes over every task with a parseable startedAt —
    the span-union half of "orchestration = elapsed - span-union" (formulas.md).
    Union, not sum, so a parallel group's overlapping wall-clock spans are never
    double-counted. end = finishedAt if parseable, else `now` for a still-running
    task (so orchestration keeps accruing live); a task with no startedAt (never
    began) is skipped entirely — it contributes no occupied time either way."""
    ivals = []
    for t in tasks:
        s = parse_ts(t.get("startedAt"))
        if s is None:
            continue
        e = parse_ts(t.get("finishedAt")) or (now if t.get("status") == "running" else None)
        if e is None or e < s:
            continue
        ivals.append((s, e))
    ivals.sort()
    total, cur_s, cur_e = 0.0, None, None
    for s, e in ivals:
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += minutes(cur_s, cur_e)
            cur_s, cur_e = s, e
        elif e > cur_e:
            cur_e = e
    if cur_e is not None:
        total += minutes(cur_s, cur_e)
    return total


def task_band(t, summary):
    """(low, high, tier, widened) per references/formulas.md's ONE fixed interval rule, keyed on
    the task's category's data-point count n (mirrors calibration_summary.confidence):
    n>=20 -> pure [raw*min(q1,factor), raw*max(q3,factor)]; 5<=n<20 -> flat +-30% on the
    adjusted estimate, widened to the envelope of the flat band and the q1/q3 band (take
    the lower low and the higher high) — the reported band is never tighter than the
    measured spread; n<5 -> flat +-50%, no q1/q3 exist, never fabricated."""
    est, raw = task_est(t), task_raw(t)
    c = summary.get(t.get("category")) or {}
    n, factor = c.get("n", 0), c.get("factor", 1.0)
    q1, q3 = c.get("q1"), c.get("q3")
    if n >= 20 and q1 is not None and q3 is not None:
        return raw * min(q1, factor), raw * max(q3, factor), "high", False
    pct = 0.3 if n >= 5 else 0.5
    lo, hi = est * (1 - pct), est * (1 + pct)
    widened = False
    if q1 is not None and q3 is not None:
        elo, ehi = raw * min(q1, factor), raw * max(q3, factor)
        if elo < lo:
            lo, widened = elo, True
        if ehi > hi:
            hi, widened = ehi, True
    return lo, hi, ("medium" if n >= 5 else "low"), widened


def interval(us, summary):
    """Sum per-unit lows/highs over units that are not fully done ("pending AND running
    tasks"). Within a parallel group: MAX of member lows / MAX of member highs over
    unfinished members — the same MAX-per-group aggregation the ETA and slip rules use
    (the prose states the rule per task and is silent on groups; 'BOTH sides use the
    same aggregation' decides it)."""
    lowsum = highsum = 0.0
    any_widened = any_high = False
    for unit in us:
        lows, highs = [], []
        for t in unit:
            if t.get("status") == "done":
                continue
            lo, hi, tier, w = task_band(t, summary)
            lows.append(lo)
            highs.append(hi)
            any_widened = any_widened or w
            any_high = any_high or tier == "high"
        if lows:
            lowsum += max(lows)
            highsum += max(highs)
    return lowsum, highsum, any_widened, any_high


def eta_text(state, us, summary, now):
    """The rendered ETA headline. Source c (spec §2): pace-based from item completion
    rate, visibly labeled uncalibrated, no calibrated interval. Sources a/b: point ETA
    from the in-flight rule + the fixed interval rule, marker included."""
    if state.get("source") == "c":
        done = sum(1 for u in us for t in u if t.get("status") == "done")
        total = sum(len(u) for u in us)
        el = elapsed_min(state, now)
        if done and total > done and el:
            eta = now + timedelta(minutes=el / done * (total - done))
            return "When done: ~%s (uncalibrated — pace-based)" % hhmm(eta, now)
        if total and done == total:
            return "Done (uncalibrated)"
        return "ETA not yet known (uncalibrated)"
    remaining = sum(unit_remaining(u, now) for u in us)
    lowsum, highsum, widened, high = interval(us, summary)
    eta = now + timedelta(minutes=remaining)
    a = max(0, round(remaining - lowsum))
    b = max(0, round(highsum - remaining))
    if a == 0 and b == 0:
        b = 1  # the interval never reads 0 while anything is pending/running
    if widened:
        return "When done: ~%s (%s%d/+%d min) (widened to measured spread)" % (
            hhmm(eta, now), MINUS, a, b)
    if high:
        return "When done: ~%s (%s%d/+%d min)" % (hhmm(eta, now), MINUS, a, b)
    n = max(1, round((highsum - lowsum) / 2))
    return "When done: ~%s ± %d min (default band — little history)" % (hhmm(eta, now), n)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("state")
    p.add_argument("tokens", help="token_usage.py output JSON file, or - for none")
    p.add_argument("out")
    p.add_argument("--now", required=True)
    p.add_argument("--summary", default=DEFAULT_SUMMARY)
    p.add_argument("--push-status", choices=sorted(PUSH_STATUS), default="uncertain")
    p.add_argument("--superseded", action="store_true")
    a = p.parse_args(argv)
    tmp = a.out + ".tmp"
    try:
        now = parse_ts(a.now)
        if now is None:
            raise ValueError("--now must be ISO 8601 with timezone, got %r" % a.now)
        with open(a.state, encoding="utf-8") as f:
            state = json.load(f)
        tokens = None
        if a.tokens != "-":
            try:  # fail-soft: the page renders without token lines
                with open(a.tokens, encoding="utf-8") as f:
                    tokens = json.load(f)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                tokens = None
        summary = load_summary(a.summary)  # fail-soft: {} => factor 1.0 / low everywhere
        page, stat = render(state, tokens, summary, now, a.push_status, a.superseded)
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(page)
            os.replace(tmp, a.out)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        print(json.dumps(stat))
        return 0
    except Exception as e:  # contract: exit 1, no partial HTML, note on stderr
        print("render_artifact: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
