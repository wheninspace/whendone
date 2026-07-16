#!/usr/bin/env python3
"""Regenerate calibration-summary.md from calibration.jsonl.

Usage: python3 calibration_summary.py <calibration.jsonl> <calibration-summary.md>

Statistics design (see docs/design.md for provenance):
- ratio = actualMin / estimateMin per completed subtask (rows with actualMin null excluded)
- observed = 20%-winsorized mean of ratios per category
- blended factor: continuous shrinkage toward PRIOR, (n*observed + K*PRIOR)/(n+K), K=5
- PRIOR = 1.0 (raw estimates are anchored to the default table, not free-form guesses)
- spread = interquartile range of ratios, shown once n>=5
Malformed lines are skipped and counted, never fatal.
"""
import json, math, statistics, sys
from datetime import date

PRIOR = 1.0
CATEGORIES = frozenset({
    "mechanical-implementation", "judgment-coding", "testing", "debugging",
    "research", "documentation", "review", "deploy-infra",
})
PARALLEL = "parallel-group"


def sanitize(s, maxlen=64):
    """Model strings come from the jsonl — strip newlines/pipes, cap length."""
    return str(s).replace("\n", " ").replace("\r", " ").replace("|", "/")[:maxlen]


def parse_row(line):
    """Validate one jsonl line. Returns (status, row): 'ok', 'skipped', or 'malformed'."""
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
    if act is None or act <= 0 or est <= 0:
        return "skipped", None
    return "ok", {"category": cat, "est": est, "act": act,
                  "model": sanitize(row.get("model") or "unknown"),
                  "date": row.get("date", ""),
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

def main(jsonl_path, out_path):
    try:
        lines = open(jsonl_path, encoding="utf-8").read().splitlines()
    except (OSError, UnicodeDecodeError) as e:
        print(f"cannot read {jsonl_path}: {e}", file=sys.stderr)
        return 1

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
            out.append(f"- {cat}: " + ", ".join(f"{m} ×{k}" for m, k in sorted(mix.items())))

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
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr); sys.exit(1)
    sys.exit(main(sys.argv[1], sys.argv[2]))
