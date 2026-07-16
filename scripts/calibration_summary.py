#!/usr/bin/env python3
"""Regenerate calibration-summary.md from calibration.jsonl.

Usage: python3 calibration_summary.py <calibration.jsonl> <calibration-summary.md>

Statistics design (see docs/design.md for provenance):
- ratio = actualMin / estimateMin per completed subtask (rows with actualMin null excluded)
- observed = 20%-trimmed median of ratios per category
- blended factor: n<5 -> PRIOR; 5<=n<20 -> 0.5*PRIOR+0.5*observed; n>=20 -> 0.3*PRIOR+0.7*observed
- PRIOR = 1.0 (raw estimates are anchored to the default table, not free-form guesses)
- spread = interquartile range of ratios
Malformed lines are skipped and counted, never fatal.
"""
import json, statistics, sys
from datetime import date

PRIOR = 1.0
DEFAULTS = {  # frozen anchor estimates, minutes — learning lives in the factor
    "mechanical-implementation": 5, "judgment-coding": 12, "testing": 8,
    "debugging": 20, "research": 15, "documentation": 10, "review": 10,
    "deploy-infra": 15,
}

def trimmed_median(values, trim=0.2):
    vs = sorted(values)
    k = int(len(vs) * trim)
    core = vs[k:len(vs)-k] or vs
    return statistics.median(core)

def blend(observed, n):
    if n < 5: return PRIOR
    if n < 20: return 0.5 * PRIOR + 0.5 * observed
    return 0.3 * PRIOR + 0.7 * observed

def confidence(n):
    return "low" if n < 5 else ("medium" if n < 20 else "high")

def main(jsonl_path, out_path):
    cats, skipped, malformed = {}, 0, 0
    try:
        lines = open(jsonl_path, encoding="utf-8").read().splitlines()
    except (OSError, UnicodeDecodeError) as e:
        print(f"cannot read {jsonl_path}: {e}", file=sys.stderr)
        return 1
    for line in lines:
        if not line.strip(): continue
        try:
            row = json.loads(line)
            cat = row["category"]
            est, act = row["estimateMin"], row["actualMin"]
        except (json.JSONDecodeError, KeyError, TypeError):
            malformed += 1; continue
        if not isinstance(cat, str) or not isinstance(est, (int, float)) or (act is not None and not isinstance(act, (int, float))):
            malformed += 1; continue
        if act is None or not est:
            skipped += 1; continue
        cats.setdefault(cat, {"ratios": [], "models": {}})
        cats[cat]["ratios"].append(act / est)
        m = row.get("model") or "unknown"
        cats[cat]["models"][m] = cats[cat]["models"].get(m, 0) + 1

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
        "| Category | Default estimate | Factor (blended) | Data points | Confidence | Spread (IQR) |",
        "|---|---|---|---|---|---|",
    ]
    for cat in sorted(set(DEFAULTS) | set(cats)):
        d = cats.get(cat, {"ratios": [], "models": {}})
        n = len(d["ratios"])
        default = f"{DEFAULTS[cat]} min" if cat in DEFAULTS else "—"
        if n == 0:
            out.append(f"| {cat} | {default} | — | 0 | — | — |")
            continue
        factor = blend(trimmed_median(d["ratios"]), n)
        if n >= 4:
            q = statistics.quantiles(d["ratios"], n=4)
            spread = f"{q[0]:.2f}–{q[2]:.2f}"
        else:
            spread = "—"
        shown = f"{factor:.2f}" if n >= 5 else "— (prior 1.0)"
        out.append(f"| {cat} | {default} | {shown} | {n} | {confidence(n)} | {spread} |")

    mixes = {c: d["models"] for c, d in cats.items() if len(d["models"]) > 1}
    if mixes:
        out += ["", "## Model mix caveat", "",
                "These categories mix models with different speeds — factors conflate model and task variance:", ""]
        for cat, mix in sorted(mixes.items()):
            out.append(f"- {cat}: " + ", ".join(f"{m} ×{k}" for m, k in sorted(mix.items())))

    out += [
        "",
        "## How to use when estimating",
        "",
        "Produce the raw estimate from the default table FIRST (adjusted only for the subtask's scope),",
        "then multiply by the category factor. Uncertainty on the total ETA: confidence low -> +/-50 %,",
        "medium -> +/-30 %, high -> shrink toward the IQR. Never state a point time without an interval,",
        "and never mention factor values in chat or artifact.",
        "",
    ]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return 0

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr); sys.exit(1)
    sys.exit(main(sys.argv[1], sys.argv[2]))
