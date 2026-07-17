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

Rotation concurrency + idempotency (C15): calibration.jsonl is a single global file
appended to by every session, so rotate() is guarded by a cross-platform create-exclusive
lockfile (<jsonl_path>.rotate.lock via os.open(O_CREAT|O_EXCL)). If another run holds the
lock, rotation is skipped this run — it retries at the next job end, so no data is ever
lost by skipping. A lock older than STALE_LOCK_SECONDS is treated as abandoned (a crashed
run) and reclaimed. Archive-append happens BEFORE the live-log truncate (so a crash between
the two is recoverable by re-running), guarded by an idempotency check: if the archive's
last line already equals the boundary line, the append is skipped, so a re-run after a
crash never double-appends.

Input-size bound (N3): before materializing calibration.jsonl into memory, main() checks
the file size against MAX_JSONL_BYTES. A pathologically large (hostile or bulk-imported)
log is streamed line-by-line for stats instead — bounded per-line memory — and rotation
(which needs the whole file as a sliceable list) is skipped that run; rotation resumes
once the file is back under the cap. In normal operation ROTATE_AT already keeps the file
small, so this cap is not expected to trigger outside a pathological import.
"""
import glob, json, math, os, re, statistics, sys, time
from datetime import date, datetime

PRIOR = 1.0
CATEGORIES = frozenset({
    "mechanical-implementation", "judgment-coding", "testing", "debugging",
    "research", "documentation", "review", "deploy-infra",
})
PARALLEL = "parallel-group"
ROTATE_AT, KEEP = 2000, 1000
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")  # anchored: self-defending regardless of match method

# C15: rotation lock staleness. Rotation itself runs in well under a second; 5 minutes
# is generous headroom so a crashed session's held lock can never wedge rotation forever.
STALE_LOCK_SECONDS = 300

# N3: input-size bound. calibration rows are small JSON (~150-300 bytes); at ROTATE_AT
# (2000) lines steady-state is well under 1 MB, so 20 MB gives ~40-100x headroom above
# normal operation before this cap can trigger — reserved for a pathological/hostile or
# bulk-imported log encountered before its first rotation.
MAX_JSONL_BYTES = 20_000_000


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


def _last_line(path):
    """Return a file's last line (no trailing newline), or None if missing/empty. Reads
    only a bounded tail, not the whole file — an archive can grow large over years of
    rotations, and this is called on every rotation attempt."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(-min(size, 8192), os.SEEK_END)
            tail = f.read()
    except OSError:
        return None
    chunks = tail.splitlines()
    return chunks[-1].decode("utf-8", errors="replace") if chunks else None


def rotate(jsonl_path, lines):
    """Move all but the newest KEEP lines to calibration-archive-<year>.jsonl.

    Concurrency (C15): guarded by a cross-platform create-exclusive lockfile
    (<jsonl_path>.rotate.lock). If another run holds it, or the stale-check itself
    fails, rotation is skipped for this run and `lines` is returned unchanged — it
    retries at the next job end, so skipping never loses data. A lock older than
    STALE_LOCK_SECONDS is treated as abandoned (e.g. a crashed session) and reclaimed.

    Idempotency: the archive append happens BEFORE the live-log truncate (so a crash
    between the two just means the next run repeats the append), guarded by a check:
    if the archive's last line already equals lines[-KEEP-1] (last run's boundary
    line), the append is skipped so a re-run after a crash does not duplicate rows.
    """
    if len(lines) <= ROTATE_AT:
        return lines

    lock_path = jsonl_path + ".rotate.lock"
    try:
        if os.path.exists(lock_path):
            if time.time() - os.stat(lock_path).st_mtime <= STALE_LOCK_SECONDS:
                return lines  # held by another run — skip, retry next job end
            os.remove(lock_path)  # older than the stale threshold: an abandoned lock
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except OSError:
        return lines  # lock busy (FileExistsError) or the stale-check failed — degrade

    try:
        archive = os.path.join(os.path.dirname(jsonl_path),
                               f"calibration-archive-{date.today().year}.jsonl")
        boundary = lines[-KEEP - 1]
        if _last_line(archive) != boundary:
            with open(archive, "a", encoding="utf-8") as f:
                f.write("\n".join(lines[:-KEEP]) + "\n")
        tmp = jsonl_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines[-KEEP:]) + "\n")
        os.replace(tmp, jsonl_path)
        return lines[-KEEP:]
    finally:
        try:
            os.remove(lock_path)
        except OSError:
            pass


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


def _iter_calibration_lines(jsonl_path):
    """Yield lines to process for the summary. Below MAX_JSONL_BYTES (the normal case):
    materialize the file (needed for rotate()'s slicing) and run rotation. At/above the
    cap (N3, a pathological or bulk-imported log): stream line-by-line instead — bounded
    per-line memory — and skip rotation this run, since rotation needs the whole file as
    a sliceable list; rotation resumes once the file is back under the cap."""
    if os.path.getsize(jsonl_path) > MAX_JSONL_BYTES:
        with open(jsonl_path, encoding="utf-8") as f:
            yield from f
        return
    with open(jsonl_path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    yield from rotate(jsonl_path, lines)


def main(jsonl_path, out_path):
    cats, skipped, malformed, parallel = {}, 0, 0, []
    try:
        for line in _iter_calibration_lines(jsonl_path):
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
    except (OSError, UnicodeDecodeError) as e:
        print(f"cannot read {jsonl_path}: {e}", file=sys.stderr)
        return 1

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
    iqr_by_cat = {}
    for cat in sorted(CATEGORIES):
        d = cats.get(cat, {"ratios": [], "models": {}})
        n = len(d["ratios"])
        factor = blend(winsorized_mean(d["ratios"]), n) if n else PRIOR
        if n >= 5:
            q = statistics.quantiles(d["ratios"], n=4)
            spread = f"{q[0]:.2f}–{q[2]:.2f}"
            iqr_by_cat[cat] = (q[0], q[2])
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
        "subtask's scope), then multiply by the category factor above. Uncertainty on the total ETA",
        "(one fixed rule — never improvise, stated identically in references/file-formats.md and",
        "SKILL.md): At HIGH confidence (n ≥ 20): per-task interval = `[raw_i × min(q1, factor),",
        "raw_i × max(q3, factor)]`, summed over pending AND running tasks, rendered asymmetrically",
        "as `Done ~HH:MM (−A/+B min)` (A = point ETA − low sum, B = high sum − point ETA). At LOW",
        "or MEDIUM confidence — regardless of whether q1/q3 happens to be shown — use flat, nominal",
        "(not empirical) bounds: low ±50 %, medium ±30 %. Never state a point time without an",
        "interval, and never mention factor values in chat or artifact.",
        "",
    ]
    if iqr_by_cat:
        out += [
            "## Per-category q1/q3 (machine-usable)",
            "",
            "The raw-ratio IQR bounds behind the Spread column above (shown from n >= 5). The",
            "high-confidence interval formula (n >= 20) applies these directly; below high",
            "confidence — including a medium-confidence category that already shows q1/q3 here —",
            "use the flat nominal bounds instead (categories with n < 5 have no q1/q3 at all;",
            "never fabricate one):",
            "",
        ]
        for cat in sorted(iqr_by_cat):
            q1, q3 = iqr_by_cat[cat]
            out.append(f"- {cat}: q1={q1:.2f} q3={q3:.2f}")
        out.append("")
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
