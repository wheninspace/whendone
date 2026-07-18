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
  formulas in references/file-formats.md ("one fixed rule — never improvise"). The model
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
th:nth-child(1),td:nth-child(1) { width:2em; }
th:nth-child(2),td:nth-child(2) { width:auto; }
th:nth-child(3),td:nth-child(3) { width:18%; }
th:nth-child(4),td:nth-child(4) { width:4em; white-space:nowrap; }
th:nth-child(5),td:nth-child(5) { width:26%; }
td:nth-child(5) .dev { white-space:nowrap; }
.dim { color:var(--dim); font-size:.9em; }
.pause-box { border:2px solid var(--paused); border-radius:8px; padding:12px 16px; margin:12px 0; }
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


def fmt_min(m):
    m = float(m)
    return "%d m" % int(m) if m == int(m) else "%.1f m" % m


def fmt_dev(actual, est):
    """Deviation baked into the Actual column: '(+38 %)' / '(−33 %)' (U+2212 minus)."""
    if not est:
        return ""
    p = round(round((actual - est) / est * 100, 9))
    return "(+%d %%)" % p if p >= 0 else "(%s%d %%)" % (MINUS, -p)


def render(state, tokens, summary, now, push_status, superseded):
    """Minimal page for now: title + banner. Later tasks extend this in place —
    the signature and the (page, status_dict) return contract are final."""
    if not isinstance(state, dict) or not isinstance(state.get("tasks"), list):
        raise ValueError("state file invalid: expected an object with a tasks array")
    job = state.get("job") or "job"
    status = state.get("status") or "running"
    now_hhmm = now.strftime("%H:%M")
    if superseded:
        banner = ('<div class="banner dead">⚠️ SUPERSEDED — this job\'s state '
                  "was discarded; this page will not update (last updated %s)</div>" % now_hhmm)
    else:
        cls, label, icon = {"paused": ("paused", "PAUSED", "⏸️"),
                            "done": ("done", "DONE", "✅")}.get(
                                status, ("running", "RUNNING", "\U0001f504"))
        banner = '<div class="banner %s">%s %s — last updated %s</div>' % (
            cls, icon, label, now_hhmm)
    page = "<title>%s</title>\n<style>%s</style>\n%s\n" % (
        esc("WhenDone: " + str(job)), CSS, banner)
    done_count = sum(1 for t in state["tasks"]
                     if isinstance(t, dict) and t.get("status") == "done")
    stat = {"ok": True, "status": "superseded" if superseded else status,
            "etaText": "", "slipAlert": False, "estimateTotalMin": 0,
            "done": done_count, "total": len(state["tasks"])}
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
    """One unit's contribution to `remaining` (file-formats.md ETA computation): 0 when
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
