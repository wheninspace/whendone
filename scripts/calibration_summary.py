#!/usr/bin/env python3
"""Regenerate calibration-summary.md from calibration.jsonl.

Usage: python3 calibration_summary.py <calibration.jsonl> <calibration-summary.md>
       python3 calibration_summary.py --report <calibration.jsonl>

Statistics design (see docs/design.md for provenance):
- ratio = actualMin / estimateMin per completed subtask (rows with actualMin null excluded)
- each individual ratio is clamped to [0.1, 8] before it reaches any pooling step (M21):
  cheap, order-independent, active at every n, unlike winsorizing below (which is inert
  for n<=4 since k=int(n*0.2) is 0 there — exactly where one point has the most leverage)
- observed = the estimate-weighted (by rawEstimateMin), 20%-winsorized mean of the CLAMPED
  ratios per category — equivalently a ratio-of-sums (sum(actualMin)/sum(rawEstimateMin))
  computed on the clamped/winsorized values. ETA totals are sums, so the calibrated
  quantity must minimize error for a SUM: an unweighted mean gives a 0.5-min quick task the
  same vote as a 60-min task, and short tasks produce the noisiest, most extreme ratios —
  weighting by rawEstimateMin fixes that (see docs/design.md's Calibration statistics
  section for the worked example and the note on how this changed factor values already
  on disk pre-release)
- blended factor: continuous shrinkage toward PRIOR, (n*observed + K*PRIOR)/(n+K), K=5
- PRIOR = 1.0 (raw estimates are anchored to the default table, not free-form guesses)
- spread = interquartile range of the (clamped) ratios, shown once n>=5
- actualMin is derived from startedAt/finishedAt when both are present (never trusted
  as model-computed arithmetic); legacy rows without timestamps fall back to the
  logged actualMin; a row whose logged actualMin disagrees with the derived value by
  more than rounding is skipped
- rows matched only via the legacy estimateMin key (not rawEstimateMin) are counted and
  surfaced as "N legacy-key rows" in the summary header (M30) — a compatibility shim for
  pre-rename logs, not something that should apply silently forever
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
    """Minutes between two ISO 8601 timestamps, one decimal. None if unparseable.

    Mirrors append_calibration.py's write-time floor EXACTLY: a non-negative delta is
    floored to a minimum of 0.5 (round(max(delta_min, 0.5), 1)), matching what the
    writer actually logged for a genuinely fast subtask. Without this floor, a subtask
    under ~24s derives to < 0.5 here while the writer logged 0.5, the two disagree by
    more than parse_row's isclose abs_tol=0.1, and the row is silently skipped -- lost
    from calibration and inflating the "skipped" count (final-review fix).

    A negative delta (clock skew) is returned UNFLOORED, so parse_row's skew branch
    (derived <= 0, distinct from the writer's None-on-skew) keeps firing correctly --
    flooring a negative delta up to 0.5 would look like a normal positive duration and
    break clock-skew detection entirely.
    """
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        finish = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    delta_min = (finish - start).total_seconds() / 60.0
    if delta_min < 0:
        return round(delta_min, 1)
    return round(max(delta_min, 0.5), 1)


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
        legacy_key = "rawEstimateMin" not in row  # M30: matched only via the old key
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

    def _finite_or_none(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None

    return "ok", {"category": cat, "est": est, "act": act,
                  "model": sanitize(row.get("model") or "unknown"),
                  "date": date_str, "legacy_key": legacy_key,
                  # M22: the parallel-group ETA rule's actual operands (max/sum of the
                  # group's ADJUSTED estimates), logged only on synthetic parallel-group
                  # rows — absent (None) on every ordinary category row and on synthetic
                  # rows written before this field existed.
                  "maxAdjusted": _finite_or_none(row.get("maxAdjusted")),
                  "sumAdjusted": _finite_or_none(row.get("sumAdjusted")),
                  "project": row.get("project", ""), "job": row.get("job", "")}


K = 5  # prior weight in pseudo-observations; at n=5 identical to the old 0.5/0.5 blend

# M21: fixed sanity band for a single ratio, applied BEFORE any pooling step, at every n.
# Winsorizing below is inert for n<=4 (k=int(n*0.2) is 0) — exactly where one point has
# maximum leverage on a mean — so this clamp is what protects a fresh category from a
# single wild first data point; winsorizing then adds a second, rank-based cap once n>=5.
RATIO_CLAMP_LO, RATIO_CLAMP_HI = 0.1, 8.0


def clamp_ratio(r):
    """M21: bound a single ratio to [RATIO_CLAMP_LO, RATIO_CLAMP_HI] before pooling."""
    return min(max(r, RATIO_CLAMP_LO), RATIO_CLAMP_HI)


def winsorized_mean(ratios, weights=None, trim=0.2):
    """20%-winsorized mean of ratios, optionally weighted by `weights` (M2: estimate-weighted
    factor — weights are each row's rawEstimateMin, so this becomes a ratio-of-sums,
    sum(actualMin)/sum(rawEstimateMin), computed on the winsorized values). `weights=None`
    (the default) is an unweighted mean — every weight is implicitly 1.

    Winsorizing: sort the ratios, clamp the top/bottom `trim` fraction BY RANK to the
    nearest kept value, then take the (weighted) mean. ETA totals are sums, so the
    calibrated quantity has to track a MEAN, not a median. Callers are expected to have
    already run each ratio through clamp_ratio() (M21) — that clamp is the leverage
    control active at every n; this rank-based trim is a second cap that only engages
    at n>=5 (int(n*trim) is 0 below that)."""
    n = len(ratios)
    if n == 0:
        return 0.0
    if weights is None:
        weights = [1.0] * n
    order = sorted(ratios)
    k = int(n * trim)
    if k:
        lo, hi = order[k], order[-k - 1]
        vals = [min(max(v, lo), hi) for v in ratios]
    else:
        vals = list(ratios)
    denom = sum(weights)
    return sum(v * w for v, w in zip(vals, weights)) / denom if denom else sum(vals) / n


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

    `lines` (the caller's pre-lock read) is used only for the cheap up-front
    len(lines) <= ROTATE_AT gate, deciding whether it's worth taking the lock at all.
    Once the lock is held, the file is RE-READ fresh from disk and every decision below
    (what's archived, what's kept, the idempotency boundary) is made from that fresh
    read — never from the stale pre-lock snapshot. Without this, a row appended by a
    concurrent session (append_calibration.py's O_APPEND write, which is lock-unaware
    by design — see module docstring) in the window between the caller's read and this
    function acquiring the lock would be silently destroyed by the truncate, since it
    exists on disk but not in the stale `lines` list rotate() would otherwise operate
    on. Residual: an append landing in the much smaller window between THIS fresh
    re-read and os.replace() below is still not captured — acceptable, since rotation
    runs only once per job end and O_APPEND writes are atomic, so that row simply
    survives as the newest line of the freshly-truncated file until the next rotation.

    Idempotency: the archive append happens BEFORE the live-log truncate (so a crash
    between the two just means the next run repeats the append), guarded by a check:
    if the archive's last line already equals the fresh read's boundary line (last
    run's boundary), the append is skipped so a re-run after a crash does not
    duplicate rows.
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
        # Re-read fresh under the lock (C15): never operate on the caller's pre-lock
        # snapshot — see docstring above for why.
        try:
            if os.path.getsize(jsonl_path) > MAX_JSONL_BYTES:
                return lines  # oversized on the fresh read too (N3) — degrade, skip
            with open(jsonl_path, encoding="utf-8") as f:
                fresh_lines = f.read().splitlines()
        except (OSError, UnicodeDecodeError):
            return lines  # fresh re-read failed — degrade, never crash

        if len(fresh_lines) <= ROTATE_AT:
            return fresh_lines  # already at/under budget by the time the lock was held

        archive = os.path.join(os.path.dirname(jsonl_path),
                               f"calibration-archive-{date.today().year}.jsonl")
        boundary = fresh_lines[-KEEP - 1]
        if _last_line(archive) != boundary:
            with open(archive, "a", encoding="utf-8") as f:
                f.write("\n".join(fresh_lines[:-KEEP]) + "\n")
        tmp = jsonl_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(fresh_lines[-KEEP:]) + "\n")
        os.replace(tmp, jsonl_path)
        return fresh_lines[-KEEP:]
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
            with open(p, encoding="utf-8") as f:
                for line in f:
                    status, r = parse_row(line)
                    if status == "ok" and r["category"] != PARALLEL:
                        rows.append(r)
        except OSError:
            continue
    if not rows:
        print("No calibration data yet."); return 0
    print(f"# WhenDone accuracy report ({len(rows)} data points)\n")
    # M17: "Last-10 mean ratio" is the UNSHRUNK winsorized mean of the recent window —
    # comparable to the lifetime "Mean ratio (winsorized)" column. The old "Last-10
    # factor" column re-applied K=5 shrinkage at n<=10 while the lifetime factor column
    # is barely shrunk at large n, so the two were never on the same scale.
    print("| Category | n | Mean ratio (winsorized) | Lifetime factor | Last-10 mean ratio |")
    print("|---|---|---|---|---|")
    bycat = {}
    for r in rows:
        bycat.setdefault(r["category"], []).append(r)
    for cat in sorted(bycat):
        rs = bycat[cat]
        # M21: clamp every ratio before it reaches any pooling step. M2: weight by each
        # row's rawEstimateMin (r["est"]) so the mean tracks a SUM's error, not a
        # per-task-count-equal vote.
        ratios = [clamp_ratio(r["act"] / r["est"]) for r in rs]
        weights = [r["est"] for r in rs]
        recent, recent_w = ratios[-10:], weights[-10:]
        lifetime_mean = winsorized_mean(ratios, weights)
        recent_mean = winsorized_mean(recent, recent_w)  # unshrunk — no blend() here (M17)
        print(f"| {cat} | {len(ratios)} | {lifetime_mean:.2f} "
              f"| {blend(lifetime_mean, len(ratios)):.2f} "
              f"| {recent_mean:.2f} |")
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
    cats, skipped, malformed, legacy_count, parallel = {}, 0, 0, 0, []
    try:
        for line in _iter_calibration_lines(jsonl_path):
            if not line.strip():
                continue
            status, r = parse_row(line)
            if status == "malformed":
                malformed += 1; continue
            if status == "skipped":
                skipped += 1; continue
            if r["legacy_key"]:  # M30: matched only via the legacy estimateMin key
                legacy_count += 1
            if r["category"] == PARALLEL:
                parallel.append(r); continue
            # M21: clamp every ratio before it reaches any pooling step. M2: track each
            # row's rawEstimateMin as its weight so the per-category factor tracks a
            # SUM's error, not a per-task-count-equal vote.
            d = cats.setdefault(r["category"], {"ratios": [], "weights": [], "models": {}})
            d["ratios"].append(clamp_ratio(r["act"] / r["est"]))
            d["weights"].append(r["est"])
            d["models"][r["model"]] = d["models"].get(r["model"], 0) + 1
    except (OSError, UnicodeDecodeError) as e:
        print(f"cannot read {jsonl_path}: {e}", file=sys.stderr)
        return 1

    total = sum(len(c["ratios"]) for c in cats.values())
    extras = []
    if skipped or malformed:
        extras.append(f"{skipped} skipped, {malformed} malformed")
    if legacy_count:
        extras.append(f"{legacy_count} legacy-key rows")
    out = [
        "# Calibration summary",
        "",
        f"Regenerated: {date.today().isoformat()} ({total} data points"
        + (", " + ", ".join(extras) if extras else "") + "). "
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
        d = cats.get(cat, {"ratios": [], "weights": [], "models": {}})
        n = len(d["ratios"])
        factor = blend(winsorized_mean(d["ratios"], d["weights"]), n) if n else PRIOR
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
        # M22: log/report the ETA rule's actual operands (max-of-adjusted vs
        # sum-of-adjusted estimates) against the group's wall-clock, so the pair can
        # discriminate between aggregators — a lone "max-rule ratio" can't, since a
        # ratio far from 1.0 is confounded with ordinary per-category estimate bias.
        # Rows logged before this field existed (or a row missing one field) simply
        # don't contribute to that side's median — never fabricated, never crashes.
        wall_over_max = [p["act"] / p["maxAdjusted"] for p in parallel
                          if p["maxAdjusted"] and p["maxAdjusted"] > 0]
        wall_over_sum = [p["act"] / p["sumAdjusted"] for p in parallel
                          if p["sumAdjusted"] and p["sumAdjusted"] > 0]
        out += ["", f"{PARALLEL} rows: {len(parallel)} logged "
                "(bookkeeping — never pooled into factors)."]
        if wall_over_max:
            out.append(f"- wall-clock / max-adjusted ratio median: "
                       f"{statistics.median(wall_over_max):.2f}")
        if wall_over_sum:
            out.append(f"- wall-clock / sum-adjusted ratio median: "
                       f"{statistics.median(wall_over_sum):.2f}")
        if wall_over_max or wall_over_sum:
            out.append("  (informative about which aggregator tracks wall-clock more "
                       "closely — not proof either rule is statistically correct, since "
                       "both ratios are confounded with ordinary per-category estimate bias.)")

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
