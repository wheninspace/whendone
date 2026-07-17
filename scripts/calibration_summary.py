#!/usr/bin/env python3
"""Regenerate calibration-summary.md from calibration.jsonl.

Usage: python3 calibration_summary.py <calibration.jsonl> <calibration-summary.md>
       python3 calibration_summary.py --report <calibration.jsonl>

Statistics design (see docs/design.md for provenance):
- ratio = actualMin / estimateMin per completed subtask (rows with actualMin null excluded)
- observed = 20%-winsorized mean of ratios per category
- blended factor: continuous shrinkage toward PRIOR, (n*observed + K*PRIOR)/(n+K), K=5
- PRIOR = 1.0 (raw estimates are anchored to the default table, not free-form guesses)
- spread = interquartile range of ratios, shown once n>=5
- actualMin is derived from startedAt/finishedAt when both are present (never trusted
  as model-computed arithmetic); legacy rows without timestamps fall back to the
  logged actualMin; a row whose logged actualMin disagrees with the derived value by
  more than rounding is skipped
- date is validated at the source in parse_row: kept only if it is a str matching
  YYYY-MM-DD, else "" — end-to-end, every string this module re-emits is neutralized
  before reaching --report or calibration-summary.md: category via the
  CATEGORIES/PARALLEL whitelist, date via this anchored regex, model via sanitize()
  inside parse_row, and project/job via sanitize() at their report() print sites
Malformed lines are skipped and counted, never fatal.

Log rotation: main() rotates calibration.jsonl once it exceeds ROTATE_AT (2000) lines,
moving all but the newest KEEP (1000) to calibration-archive-<year>.jsonl (atomic replace).
--report prints a markdown accuracy report to stdout, reading the main jsonl plus any
calibration-archive-*.jsonl siblings — the LLM never reads the jsonl directly.
"""
import glob, json, math, os, re, statistics, sys
from datetime import date, datetime

PRIOR = 1.0
CATEGORIES = frozenset({
    "mechanical-implementation", "judgment-coding", "testing", "debugging",
    "research", "documentation", "review", "deploy-infra",
})
PARALLEL = "parallel-group"
ROTATE_AT, KEEP = 2000, 1000
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")  # anchored: self-defending regardless of match method


def sanitize(s, maxlen=64):
    """Model/project/job strings come from the jsonl — never trusted as markdown.
    Strip newlines/pipes (table delimiter) and backticks (breaks inline-code spans),
    and drop a leading '#' run (markdown heading), before capping length."""
    s = str(s).replace("\n", " ").replace("\r", " ").replace("|", "/").replace("`", "'")
    return s.lstrip("#")[:maxlen]


def _derive_actual_min(started_at, finished_at):
    """Minutes between two ISO 8601 timestamps, one decimal. None if unparseable."""
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        finish = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    return round((finish - start).total_seconds() / 60.0, 1)


def parse_row(line):
    """Validate one jsonl line. Returns (status, row): 'ok', 'skipped', or 'malformed'.

    actualMin is never trusted as model arithmetic: when startedAt/finishedAt are both
    present (append_calibration.py always writes them), actualMin is DERIVED from them
    here, independent of whatever the row's own actualMin says. Legacy rows without
    timestamps fall back to the logged actualMin. If a row somehow carries both a
    logged actualMin and timestamps that disagree by more than rounding, the row is
    untrustworthy and is skipped rather than silently trusting either number.
    """
    try:
        row = json.loads(line, parse_constant=lambda _: None)  # NaN/Inf -> None -> rejected
        cat = row["category"]
        est = row.get("rawEstimateMin", row.get("estimateMin"))
        act = row["actualMin"]
    except (json.JSONDecodeError, KeyError, TypeError, RecursionError):
        return "malformed", None
    if (not isinstance(cat, str) or (cat not in CATEGORIES and cat != PARALLEL)
            or not isinstance(est, (int, float)) or isinstance(est, bool)
            or not math.isfinite(est)
            or (act is not None and (not isinstance(act, (int, float))
                                     or isinstance(act, bool) or not math.isfinite(act)))):
        return "malformed", None
    started_at, finished_at = row.get("startedAt"), row.get("finishedAt")
    if isinstance(started_at, str) and isinstance(finished_at, str):
        derived = _derive_actual_min(started_at, finished_at)
        if derived is not None:
            if derived > 0:
                if act is not None and not math.isclose(derived, act, abs_tol=0.1):
                    return "skipped", None  # logged actualMin disagrees with the timestamps
                act = derived
            elif act is not None:
                # Clock skew (derived <= 0): append_calibration.py always logs actualMin:null
                # here, so any non-null logged value disagrees with the timestamps — same
                # tamper/hand-edit protection as the derived > 0 branch above.
                return "skipped", None
    if act is None or act <= 0 or est <= 0:
        return "skipped", None
    raw_date = row.get("date")
    date_str = raw_date if isinstance(raw_date, str) and DATE_RE.fullmatch(raw_date) else ""
    return "ok", {"category": cat, "est": est, "act": act,
                  "model": sanitize(row.get("model") or "unknown"),
                  "date": date_str,
                  "project": row.get("project", ""), "job": row.get("job", "")}


K = 5  # prior weight in pseudo-observations; at n=5 identical to the old 0.5/0.5 blend


def winsorized_mean(values, trim=0.2):
    """Clamp the top/bottom 20% to the cut values, then mean.
    ETA totals are sums, so the calibrated quantity must track the MEAN ratio;
    winsorizing (vs trimming) keeps real tail mass while capping single-point leverage."""
    vs = sorted(values)
    k = int(len(vs) * trim)
    if k:
        lo, hi = vs[k], vs[-k - 1]
        vs = [min(max(v, lo), hi) for v in vs]
    return sum(vs) / len(vs)


def blend(observed, n):
    """Continuous shrinkage toward PRIOR: no dead zone below n=5, no jumps, converges."""
    return (n * observed + K * PRIOR) / (n + K)

def confidence(n):
    return "low" if n < 5 else ("medium" if n < 20 else "high")


def rotate(jsonl_path, lines):
    """Move all but the newest KEEP lines to calibration-archive-<year>.jsonl. Atomic."""
    if len(lines) <= ROTATE_AT:
        return lines
    archive = os.path.join(os.path.dirname(jsonl_path),
                           f"calibration-archive-{date.today().year}.jsonl")
    with open(archive, "a", encoding="utf-8") as f:
        f.write("\n".join(lines[:-KEEP]) + "\n")
    tmp = jsonl_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines[-KEEP:]) + "\n")
    os.replace(tmp, jsonl_path)
    return lines[-KEEP:]


def report(jsonl_path):
    """Accuracy report on stdout — the LLM must never read the jsonl itself."""
    paths = sorted(glob.glob(os.path.join(os.path.dirname(jsonl_path),
                                          "calibration-archive-*.jsonl"))) + [jsonl_path]
    rows = []
    for p in paths:
        try:
            for line in open(p, encoding="utf-8"):
                status, r = parse_row(line)
                if status == "ok" and r["category"] != PARALLEL:
                    rows.append(r)
        except OSError:
            continue
    if not rows:
        print("No calibration data yet."); return 0
    print(f"# WhenDone accuracy report ({len(rows)} data points)\n")
    print("| Category | n | Mean ratio (winsorized) | Lifetime factor | Last-10 factor |")
    print("|---|---|---|---|---|")
    bycat = {}
    for r in rows:
        bycat.setdefault(r["category"], []).append(r)
    for cat in sorted(bycat):
        rs = bycat[cat]
        ratios = [r["act"] / r["est"] for r in rs]
        recent = ratios[-10:]
        print(f"| {cat} | {len(ratios)} | {winsorized_mean(ratios):.2f} "
              f"| {blend(winsorized_mean(ratios), len(ratios)):.2f} "
              f"| {blend(winsorized_mean(recent), len(recent)):.2f} |")
    print("\n## Biggest misses\n")
    worst = sorted(rows, key=lambda r: abs(math.log(r["act"] / r["est"])), reverse=True)[:5]
    for r in worst:
        print(f'- {r["date"]} {r["category"]}: est {r["est"]} min, actual {r["act"]} min '
              f'(project "{sanitize(r["project"])}", job "{sanitize(r["job"])}")')
    return 0


def main(jsonl_path, out_path):
    try:
        lines = open(jsonl_path, encoding="utf-8").read().splitlines()
    except (OSError, UnicodeDecodeError) as e:
        print(f"cannot read {jsonl_path}: {e}", file=sys.stderr)
        return 1
    lines = rotate(jsonl_path, lines)

    cats, skipped, malformed, parallel = {}, 0, 0, []
    for line in lines:
        if not line.strip():
            continue
        status, r = parse_row(line)
        if status == "malformed":
            malformed += 1; continue
        if status == "skipped":
            skipped += 1; continue
        if r["category"] == PARALLEL:
            parallel.append(r["act"] / r["est"]); continue
        d = cats.setdefault(r["category"], {"ratios": [], "models": {}})
        d["ratios"].append(r["act"] / r["est"])
        d["models"][r["model"]] = d["models"].get(r["model"], 0) + 1

    total = sum(len(c["ratios"]) for c in cats.values())
    out = [
        "# Calibration summary",
        "",
        f"Regenerated: {date.today().isoformat()} ({total} data points"
        + (f", {skipped} skipped, {malformed} malformed" if skipped or malformed else "") + "). "
        "Regenerated from calibration.jsonl by scripts/calibration_summary.py at every job end. "
        "Read at job start — never read the full jsonl at start.",
        "",
        "## Per category",
        "",
        "| Category | Factor (blended) | Data points | Confidence | Spread (IQR) |",
        "|---|---|---|---|---|",
    ]
    for cat in sorted(CATEGORIES):
        d = cats.get(cat, {"ratios": [], "models": {}})
        n = len(d["ratios"])
        factor = blend(winsorized_mean(d["ratios"]), n) if n else PRIOR
        if n >= 5:
            q = statistics.quantiles(d["ratios"], n=4)
            spread = f"{q[0]:.2f}–{q[2]:.2f}"
        else:
            spread = "—"
        shown = f"{factor:.2f}" if n >= 1 else "— (prior 1.0)"
        out.append(f"| {cat} | {shown} | {n} | {confidence(n) if n else '—'} | {spread} |")

    mixes = {c: d["models"] for c, d in cats.items() if len(d["models"]) > 1}
    if mixes:
        out += ["", "## Model mix caveat", "",
                "These categories mix models with different speeds — factors conflate model and task variance:", ""]
        for cat, mix in sorted(mixes.items()):
            out.append(f"- {cat}: " + ", ".join(f"`{m}` ×{k}" for m, k in sorted(mix.items())))

    if parallel:
        out += ["", f"{PARALLEL} rows: {len(parallel)} logged, max-rule ratio median "
                f"{statistics.median(parallel):.2f} (validation data — never pooled into factors)."]

    out += [
        "",
        "## How to use when estimating",
        "",
        "Produce the raw estimate FIRST from the default table in SKILL.md (adjusted only for the",
        "subtask's scope), then multiply by the category factor above. Uncertainty on the total ETA:",
        "confidence low -> +/-50 %, medium -> +/-30 %, high -> shrink toward the IQR. Never state a",
        "point time without an interval, and never mention factor values in chat or artifact.",
        "",
    ]
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(out))
    except OSError as e:
        print(f"cannot write {out_path}: {e}", file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--report":
        sys.exit(report(sys.argv[2]))
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr); sys.exit(1)
    sys.exit(main(sys.argv[1], sys.argv[2]))
