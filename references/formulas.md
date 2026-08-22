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

One line per COMPLETED subtask (moved here from file-formats.md — the model never constructs
rows; `tail_progress.py`/`append_calibration.py` do):

```json
{"date":"2026-07-16","project":"<project directory name>","job":"<job name>","category":"debugging","rawEstimateMin":10,"startedAt":"2026-07-16T09:30:00+02:00","finishedAt":"2026-07-16T09:56:00+02:00","actualMin":26.0,"model":"claude-haiku-4-5-20251001","effort":"low","client":"desktop|web|cli|unknown"}
```

`date` = local date. `client` from the environment; unsure → `unknown`. `rawEstimateMin` = the
raw estimate before the category factor. `model` = the full versioned id that executed THE
SUBTASK (not necessarily the lead's); dispatch alias never resolved → log the alias.

**Alias→versioned upgrade (N9).** Before a row is built, `handle_completion` tries once to
resolve a bare dispatch alias (e.g. `"haiku"`) to the full versioned id that actually ran: it
reads the task's token-usage window (the distinct models observed in it, ordered busiest
first) and searches EVERY id in that list for a same-family substring match against the
alias — not only the busiest model, since on a delegated task the lead's own usage is usually
that first, busiest entry and would otherwise mask the subagent's model. First match in that
order wins and replaces the alias; no match anywhere in the window → the alias stands and is
logged as-is (previous paragraph). This order is a heuristic, not a guarantee: when a single
task window holds two versions of one family (e.g. `claude-sonnet-4-5` and `claude-sonnet-5`),
the match can be a sibling subagent's id rather than the one this task actually ran under.
`model` is display/advisory metadata only — it never feeds factor, q1, or q3 math — so this
residual ambiguity cannot skew calibration.

Include `"effort"` ONLY when non-null. A parallel-group member's row additionally carries
`"parallel": true` (P2, fixes C5).

`parallel` (optional boolean) — set on a parallel-group MEMBER's row so factors and
`--report`'s per-category split can tell contention-affected spans apart. Absent (every
ordinary sequential row, and every pre-P2 row) means sequential; `build_row()` rejects the
row if present but not actually a bool, same posture as an invalid `rawEstimateMin`.

The synthetic `"parallel-group"` category row is RETIRED: `handle_completion` never writes a
new one. Each member now logs its OWN row (real category, `"parallel": true`) on its OWN
confirmed close — no waiting for the rest of the group — and that row enters its category's
factor exactly like a sequential row. Rows already logged under `"parallel-group"` before this
change remain valid to parse and stay excluded from every category's factor (bookkeeping only,
unchanged). `maxAdjusted`/`sumAdjusted` (optional, numeric) describe only those legacy rows:
`maxAdjusted` = max of the group's ADJUSTED estimates, `sumAdjusted` = their sum — the old ETA
rule's actual operands. `parse_row` reads both as optional and degrades gracefully — a legacy
row missing one or both simply doesn't contribute to that field's median.

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
- **Hours rollover (`fmt_min`/`fmt_min_s`):** both formatters roll to h+m (`fmt_min`) or
  h+m+s (`fmt_min_s`) once the displayed duration reaches 60 minutes — below that, whole
  minutes (`fmt_min`) or m+s (`fmt_min_s`) as before. Pinned examples: `fmt_min(275)="4 h 35
  m"`, `fmt_min(240)="4 h"`, `fmt_min_s(403 + 52/60)="6 h 43 m 52 s"`, `fmt_min_s(60)="1 h"`,
  `fmt_min_s(60.05)="1 h 0 m 3 s"`, `fmt_min_s(90)="1 h 30 m"`. This rollover applies to every
  duration rendered through these two functions — the Actual column, the delegated/lead-review
  split, the Total row, elapsed, and the job-end "took" line — but NOT to the ETA interval
  bounds (`(−A/+B min)`, `± N min`) or any numeric JSON status field (`idleMin`, `stalledMin`,
  `sincePublishMin`, `orchestrationMin`), which stay plain minutes. The elapsed-meta line and
  the "overrunning by X min" line render through `fmt_min` too, so below the hour they now show
  "m" rather than the literal word "min" — an accepted, intended side effect of sharing the
  formatter, not a separate rule.
- **Totals block** closes the table — row names and layout are the D9 section's below; the
  one nuance not restated there: `Sum of subtasks` (both Est and Actual columns) is
  group-aware, unconditionally — the SAME aggregation as the ETA and `estimateTotalMin`
  (sequential tasks summed, parallel groups MAX-aggregated, mirroring `units()`), never a
  plain per-task sum. This is what makes `Sum of subtasks` match the headline ETA's basis and
  lets `Sum + orchestration = Total` hold by construction on every topology, sequential or
  parallel alike (P1, fixes C1+C10: the pre-2026-08-22 plain sum double-counted a parallel
  group's overlapping wall-clock and could read higher than `Total`, contradicting it). The
  old double-counting per-task sum is not discarded — it remains available as one dim line,
  `agent-minutes across parallel tasks: X`, directly under the totals block, shown only when
  at least one task carries a non-null `group`.
- **Job-end `Ended HH:MM` line:** once `status == "done"`, a second meta line appears
  immediately under `Started HH:MM`: `Ended HH:MM`, computed from the latest task
  `finishedAt` across every declared task. This is the SAME anchor `elapsed_min`'s done branch
  uses for the "took" figure (`_done_end`) — "took" and "Ended" are derived from one shared
  timestamp and can never disagree with each other. Done status only; a running or paused job
  shows no `Ended` line.
- Token lines when token JSON is available (omitted entirely otherwise): job-level
  "Tokens: Nk spent · NM cache reads" (cache reads never summed into the headline), per-task
  dim lines, and ONE combined `≈Nk tok (group)` figure for parallel dispatch groups whose
  windows overlap (`token_usage.py` marks them `"overlap": true`) — never precise-looking
  per-member numbers.

## Orchestration line & delegated split (D9 — artifact honesty)

D0's invariant is that every wall-clock minute between job start and job end is attributed
somewhere visible: a task's span, the job-level orchestration line, or a user pause. This
section is the ONE fixed rule for the two artifact elements that make that true.

Honest caveat: the three buckets cover the wall clock but do not partition it. A pause taken
INSIDE a task's span is counted twice — once inside that task's `startedAt`→`finishedAt`
interval, once in `pausedTotalMin` (measured: a 40-min wall clock with a 30-min pause inside a
task reads union 40 + orchestration 0 + paused 30 = 70 attributed minutes), and the pause
minutes also ride into that task's `actualMin` calibration row. Nothing goes negative — the
`max(0, …)` clamp holds — and SKILL.md's "never stop mid-subtask" rule keeps it rare, but the
whole-lifecycle task spans introduced in v0.5.0 widen the window in which a stop can land.

- **Between-subtask orchestration** = `max(0, elapsed − span-union)`, where `elapsed` is the
  existing pause-adjusted `elapsed_min` (job `startedAt` → now/`pausedAt`/latest `finishedAt`,
  minus `pausedTotalMin`) and `span-union` merges every task's own `startedAt`→`finishedAt`
  interval (running tasks use `now` as the open end) into non-overlapping runs before summing
  — a parallel group's overlapping wall-clock spans are counted ONCE, never summed per member.
  Always exported on the JSON status line as `orchestrationMin` (0 when nothing to attribute,
  or for source C — see references/source-c.md — which has no calibrated elapsed baseline to
  subtract against).
- **Totals block** (replaces the pre-2026-08-22 dim `<p>` orchestration line; made
  unconditionally group-aware by P1, same date, fixing C1+C10): the task table closes with up
  to four rows. `Sum of subtasks` uses the SAME aggregation as the ETA and `estimateTotalMin`
  — sequential tasks summed, parallel groups MAX-aggregated per unit (mirrors `units()`),
  applied to both columns (estimates whole-minute, done tasks' actuals in m+s, deviation only
  once every task is done) — never a plain per-task sum, on any topology. `Between-subtask
  orchestration` (the formula above, its own row, shown only when ≥ 1 min) and `Total` (=
  `elapsed_min`'s endpoint — the SAME value the job-end "took"/`Ended` header line uses, so the
  table and the header can never disagree) follow; because `Sum of subtasks` is group-aware,
  `Sum + orchestration = Total` holds by construction whether or not any task carries a
  `group`. Last, only when at least one task carries a non-null `group`, one dim line —
  `agent-minutes across parallel tasks: X` — surfaces the old double-counting per-task sum for
  reference (the information is kept; the contradiction with `Total` is not). Source C (no
  calibrated elapsed baseline) renders only the `Sum of subtasks` row (plus the dim line, when
  it has groups).
- **Close markers: the PRIMARY close authority (added 2026-08-22, reframed P8 same date).** A
  task's `completed` marker in `.claude/whendone-closes.jsonl` is the close authority that's
  always available, on every harness. TodoWrite/TaskCreate/TaskUpdate, where a harness provides
  them, are EQUIVALENT evidence for the same close, handled identically — mechanics unchanged:
  the model still writes marker lines only when ToolSearch found no todo tool at declare time
  (format: references/file-formats.md). Each valid `{"task","status","ts"}` line is
  TODO-EQUIVALENT evidence — `tail_progress.py`'s
  `read_close_markers` synthesizes it into the same `todos` event stream the tailer already
  consumes for todo/TaskUpdate transitions, so each status carries the same authority a real
  todo transition would: an `in_progress` marker sets/confirms that task's `startedAt` exactly like
  a todo `in_progress` transition does (winning over the dispatch-start fallback), and a
  `completed` marker is a CONFIRMED close — it feeds the calibration row, the alias→versioned-id
  upgrade described above, and an immediate `all-done` — no separate rule branch for either
  status. Markers timestamped before the job's `startedAt` are stale leftovers from an earlier
  job and are ignored (stale-file guard); a missing/oversized/malformed file or line contributes
  nothing (fail-soft), never an exception.
- **A dispatch's span end depends on whether it runs in the background.** A dispatch counts as
  background when its input carries a truthy `run_in_background`, or, flag-less, when its
  `tool_result` text matches the launch-acknowledgment shape ("Async agent launched"). For a
  foreground dispatch the span ends on that `tool_result`, same as before. For a background
  dispatch the `tool_result` is only the launch ack — it never ends the span; the span ends on
  the agent's own completion notification (a `<task-notification>` transcript entry),
  correlated back to the dispatch by `tool_use` id. A repeat notification for the same id (a
  resumed/relaunched agent) extends that span's end rather than opening a new one, and only
  forward in time — last wins, monotonically. There is no fallback if a notification is lost:
  `open` for that task never decrements, which is more consequential than a missing
  `delegatedMin`. The task can never display-close while `open` stays truthy, and if it was
  already display-closed by an earlier (matched) dispatch, this still-open one reopens it
  (N4) and it can never re-close — so for that task, `all-done` never fires for the job VIA
  OBSERVED EVIDENCE ALONE. This is a deliberate trade, not a regression: the alternative
  (closing on the ack, v0.5.0's bug) silently under-counted the agent's actual runtime, whereas
  this failure mode never blocks the underlying work and is not silent — `check_staleness`
  still emits one `stale` event for the task that never stops "running", and a `marker-missing`
  nudge (P3) fires once the dispatch has gone unconfirmed for 3 minutes. Not a dead end,
  though (P5, source-a.md): a lead-written `completed` marker closes the task regardless of
  `open` — `todoFinishedAt` wins `plan_transitions`' branch order unconditionally — so a lost
  notification can never permanently strand a task.
- **Only main-session transcripts carry event authority.** A job's declared `sessionIds` name
  main transcripts; each also has a discoverable sibling directory of subagent transcript
  files (`token_usage.py`'s `transcript_paths`). Of the two, only the MAIN files are parsed for
  `todos`/`dispatch`/`result`/`artifact`/`agent-done` events — subagent transcripts feed
  nothing but the staleness/grace clock
  (the newest entry timestamp across every file, main or subagent) and token counts. An
  oversized subagent file is skipped silently (fail-soft: fewer timestamps, never a raised
  error); an oversized MAIN file still raises `TranscriptTooLarge` and degrades the whole tail,
  same as before this rework.
- **Delegated / lead-review split**: `delegatedMin` (Task 3's `tail_progress.py` field) is
  agent-minutes summed over the task's matched subagent dispatch→completion spans — a dispatch
  counts only when its `description` matches the task's `name` under `tail_progress.py`'s
  `normalize`, which tolerates case, surrounding/collapsed whitespace, and ONE leading ordinal
  (`3.`, `3)`, `3:`, `Task 3:`) — and nothing looser. Because these are
  agent-minutes, not wall-clock minutes, a task with two or more matched dispatches running in
  parallel can legitimately show `delegatedMin` GREATER than the task's own wall span. Lead/
  review time = the task's `display_actual` minus `delegatedMin`, clamped at 0 — never
  negative, even when parallel delegation pushed `delegatedMin` past the wall span. Rendered
  as a dim line under a done task's Actual-column cell, immediately after the time+deviation
  line and before the token line (Task 7's fixed order: time+deviation, then "delegated Xm ·
  lead/review Ym" via `fmt_min_s`, then the token line) — shown only when the task is done,
  `delegatedMin` is a known number, and the task has a non-null actual. The `unconfirmed`
  marker (below) stays in the name cell, not the Actual column.
- **`unconfirmed` closes never produce calibration rows.** A task display-closed on subagent
  evidence alone — no todo/TaskUpdate `completed` transition was ever observed for it
  (references/source-a.md) — is marked `t["unconfirmed"] = true`. Such a task IS `status ==
  "done"` (so it still appears finished in the artifact) but was never logged to
  calibration.jsonl, so its `actualMin` is null and the Actual column falls back to
  `display_actual`'s timestamp-derived span. The renderer marks it with a dim
  "unconfirmed — closed on agent completion" line under the task name so the artifact never
  implies a confirmed close it doesn't have. Two things can happen to it next: todo `completed`
  evidence upgrades it to a confirmed close (row appended, once); or, before that, a todo
  `in_progress` sighting for it OR a new matched dispatch still in flight REOPENS it back to
  `running` and clears the marker — the lead is still working the task, so the provisional
  close was premature either way.
- **`all-done` is held under a quiet-transcript grace while any close is `unconfirmed`.**
  Whenever every declared task is `status == "done"` but at least one carries `unconfirmed`,
  the tailer withholds the `all-done` event until the newest transcript entry seen (across
  every main and subagent transcript) is at least `staleAfterMin` minutes old — the same
  threshold `check_staleness`/`check_idle` use, defaulting to 10. No transcript timestamp at
  all never holds (fail-soft: `all-done` fires rather than hanging forever). A confirmed close
  on every task fires `all-done` immediately, with no grace. `all-done` no longer requires the
  completing transition to have happened in the SAME cycle that detects completeness — a
  quiet job that was already all-done when the tailer next looks still fires it.

Implemented in `scripts/render_artifact.py` (`_span_union_min`, the per-task delegated/lead
dim line and the totals-block rows in `task_rows`, `total_estimate`/`total_actual` for the
group-aware Sum row, and the `orch` computation and `orchestrationMin` in `render`) and
`scripts/tail_progress.py` (`delegatedMin`/`unconfirmed` accounting on close); pinned by both
scripts' tests.
