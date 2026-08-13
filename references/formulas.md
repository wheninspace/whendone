# WhenDone normative formulas and rendering spec

Maintainer/test reference — **never read at runtime, on any path**. Every rule here is
implemented in `scripts/render_artifact.py`, `scripts/append_calibration.py`, or
`scripts/tail_progress.py` and pinned by their tests; the model quotes script output
(`etaText`) and never computes any of it (SKILL.md Invariants). This file exists so each rule
has exactly ONE prose statement — the scripts' tests are the drift guard against it.

## ETA computation (one fixed formula — never improvise)

remaining = Σ `estimateMin` of pending sequential tasks
          + for each pending parallel group: MAX of its members' `estimateMin`
          + for each running task or running parallel group: MAX over its unfinished
            members of `max(0.2 × estimateMin_i, estimateMin_i − elapsed_i)`

ETA = now + remaining. Elapsed (in the artifact) = now − job `startedAt` − `pausedTotalMin`.
The interval never collapses to 0 while anything is running; once a running task's elapsed
time exceeds its `estimateMin`, show "overrunning by X min" instead of implying imminence.

**Interval (one fixed rule — never improvise):** At HIGH confidence (n ≥ 20): per-task
interval = `[raw_i × min(q1, factor), raw_i × max(q3, factor)]`, summed over pending AND
running tasks, rendered asymmetrically as `When done: ~HH:MM (−A/+B min)` (A = point ETA − low
sum, B = high sum − point ETA). At LOW/MEDIUM confidence: flat nominal bounds on each task's
adjusted `estimateMin` — low ±50 %, medium ±30 %. Where the category shows q1/q3 (n ≥ 5),
widen that task's band to the envelope of the flat band and `[raw_i × min(q1, factor), raw_i
× max(q3, factor)]` (lower low, higher high) — never tighter than the measured spread. No
q1/q3 (n < 5) → flat band stands; never fabricate q1/q3. Sum per-task lows/highs over pending
AND running tasks. Render `± N min (default band — little history)` when nothing was widened; if ANY task's band
was widened, render `(−A/+B min)` with the marker `(widened to measured spread)`. `q1`/`q3`
are the IQR bounds of the category's raw-ratio distribution in calibration-summary.md's
Spread column (also machine-readable in that file's footer, n ≥ 5); `factor` is that
category's blended factor. The renderer parses factor and q1/q3 from calibration-summary.md
itself — neither ever enters model context.

**150 %-slip alert:** both sides use the same aggregation — sequential sum + MAX per parallel
group, never a sum of every member. Left side: per task, `actualMin` if done else
`estimateMin` (in-flight → `max(estimateMin, elapsed)`), summed with per-group MAX added (a
fully DONE group contributes the MAX of members' `actualMin`). Alert when total `> 1.5 ×
originalTotalMin`, computed once at job start over ALL subtasks' initial `estimateMin`, never
revised.

**Implemented in `scripts/render_artifact.py` (F6).** The renderer computes remaining, ETA,
elapsed, interval, per-task deviation, and the 150 %-slip check from this section's rules and
prints a one-line JSON status (`etaText`, `slipAlert`, `estimateTotalMin`, counts) to quote
and act on — never recomputed by the model; this file is the normative statement the script's
tests pin against.

## Calibration row derivation

`maxAdjusted`/`sumAdjusted` (optional, numeric) — logged ONLY on the synthetic
`"parallel-group"` row, never an ordinary category row. `maxAdjusted` = max of the group's
ADJUSTED estimates; `sumAdjusted` = their sum — the ETA rule's actual operands.
`build_row()` includes each key only when present and rejects the row (same stderr+exit-1 path
as an invalid `rawEstimateMin`) if present but non-numeric/non-finite; `parse_row` reads both
as optional and degrades gracefully — a row missing one or both simply doesn't contribute to
that field's median.

`startedAt`/`finishedAt` are the subtask's own timestamps — same values as the state file's for
that task. `actualMin` is never LLM arithmetic: `append_calibration.py` computes it from these
two timestamps (one decimal, minimum 0.5); `calibration_summary.py::parse_row` independently
re-derives it at read time, falling back to the logged value only for legacy rows predating
this field, skipping any row where logged and derived values disagree by more than rounding.
Clock skew (`finishedAt` before `startedAt`) → the script logs `actualMin: null`, excluded,
never a wrong-but-finite duration and never silently dropped.

Legacy logs may carry `rawEstimateMin` as `estimateMin` — the summary script reads both.
`model`/`effort` are recorded only so historical runs can be compared across model versions
later; the summary script ignores both for factor computation.

## Rendered-page formatting (render_artifact.py)

- Task table: status icon, name (+ dim executor line when the task's `model` is known,
  `· N effort` only when `effort` is set), category, estimate, and an Actual column that is
  always a computed time — the task's raw wall span in m+s (`11 m 24 s (+42 %)`; the
  calibration log's 0.5-min floor never shows here — it's a logging rule, not a clock fact),
  `overrunning by X min` for a running task past its estimate, `—` when unstarted; never a
  status word.
- A bold Total row (dim-labeled "sum of subtasks") closes the table: plain column sums — all
  estimates (whole minutes); done tasks' actuals in m+s precision (`8 m 30 s`), deviation only
  once every task is done. Sums are work minutes, not group-aware walltime: the job-end "took"
  line shows walltime, also in m+s (`took 7 m 55 s`) — facts get seconds, estimates stay
  whole-minute, and the two totals are visibly different labeled quantities rather than one
  number rounded two ways.
- Token lines when token JSON is available (omitted entirely otherwise): job-level
  "Tokens: Nk spent · NM cache reads" (cache reads never summed into the headline), per-task
  dim lines, and ONE combined `≈Nk tok (group)` figure for parallel dispatch groups whose
  windows overlap (`token_usage.py` marks them `"overlap": true`) — never precise-looking
  per-member numbers.
