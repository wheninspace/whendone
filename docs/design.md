# Design rationale

Condensed reasoning behind Pacekeeper's design choices, so contributors don't need the original
planning session to understand why things are built the way they are.

## Core principles

- **Visibility never blocks work.** If the artifact publish, a notification, or a log write
  fails, the job continues regardless and the failure is retried at the next checkpoint. Progress
  tracking is a side effect of the job, never a dependency of it.
- **Checkpoint updates, not live updates.** The artifact is refreshed at subtask boundaries, not
  continuously. There's no background process and no polling loop — updates happen exactly when
  there's new, real information (a subtask finished), which keeps the design simple and avoids any
  need for an always-running watcher.
- **Raw estimates get logged, not adjusted ones.** The category factor exists to measure how far
  off the raw (pre-factor) estimate was. If the log recorded the already-adjusted estimate
  instead, the ratio would converge toward 1.0 on its own regardless of actual accuracy, and the
  factor would stop learning anything. Logging the raw number keeps the factor honest.
- **Frozen default anchors.** The default per-category estimate table never changes. All learning
  lives in the correction factor that multiplies it. This keeps the anchor stable and auditable —
  if numbers look wrong, the factor is the only place to look, not a moving baseline.
- **Winsorizing over a raw mean.** A single blown estimate (a debugging subtask that took ten
  times longer than expected) shouldn't drag every future estimate with it. Clamping the extreme
  20% of ratios to the nearest kept value before averaging blunts that leverage while still
  tracking the mean ratio the ETA math actually needs (see Calibration statistics).
- **Parallel subtasks are excluded from calibration.** When multiple subtasks run at once, their
  wall-clock durations overlap, so "actual minutes elapsed" no longer corresponds to "work done" —
  logging them would corrupt the ratio calculation. They're still shown individually in the
  artifact, but the group's contribution to the total ETA is the max of the group's estimates, not
  the sum.

## Calibration statistics

Mirrors the docstring in `scripts/calibration_summary.py`:

- **ratio** = `actualMin / estimateMin` per completed subtask (rows with `actualMin: null` are
  excluded — those are crashed or interrupted subtasks, not data).
- **observed factor** = a 20%-winsorized mean of ratios per category: sort the ratios, then clamp
  the bottom and top 20% to the nearest kept value instead of discarding them, and take the mean
  of the resulting list. ETA totals are sums, so the calibrated quantity has to track the *mean*
  ratio — a median (trimmed or not) estimates the wrong statistic for that purpose and would
  under-correct for right-skewed categories (e.g. debugging, where the tail is long overruns, not
  long underruns). Winsorizing keeps that tail's mass in the average while capping how much
  leverage any single point gets.
- **continuous shrinkage**: `factor = (n * observed + K * PRIOR) / (n + K)`, with `K = 5` acting
  as a fixed number of prior pseudo-observations. This replaces an earlier phased blend
  (`n < 5` → prior only, `5 <= n < 20` → 0.5/0.5, `n >= 20` → 0.3 * prior + 0.7 * observed) that
  had two problems: a dead zone below `n = 5` where new data was thrown away entirely, and hard
  jumps at the `n = 5` and `n = 20` boundaries. The shrinkage formula is identical to the old
  blend's value at exactly `n = 5` (`(5*observed + 5*1.0)/10 = 0.5*observed + 0.5*1.0`), starts
  using data from `n = 1` instead of `n = 5`, and converges smoothly toward the observed ratio as
  `n` grows instead of plateauing at `0.3 * prior + 0.7 * observed`.
- **PRIOR = 1.0.** pocket-watch, the project this scheme is adapted from, uses 1.3 as its prior,
  because its raw estimates are free-form guesses that empirically skew optimistic. Pacekeeper's
  raw estimates are different: they start from a frozen, table-anchored default per category, not
  an open guess. There's no equivalent built-in optimism bias to correct for up front, so the
  neutral prior of 1.0 is the right starting point here.
- **spread** = interquartile range of the ratios, shown once `n >= 5` (below that, quartiles are
  too noisy to be worth displaying) so a wide spread is visible even when the factor looks stable.
- Confidence labels follow the same `n` thresholds: `n < 5` low, `5 <= n < 20` medium, `n >= 20`
  high.
- The live summary table is factors-only — no default-estimate column. Anchoring protection (see
  below) depends on the raw estimate being produced from SKILL.md's frozen table before the
  factor is read; a table that showed both columns side by side would let that ordering be
  shortcut by reading one file instead of two.

## Anchoring protection

The raw estimate must be produced before the category factor is read, not after. If the model
saw the factor first, its own "raw" estimate would drift toward compensating for a known bias,
polluting the very number the factor is supposed to measure independently. Category factor values
are also never mentioned in chat or in the artifact, for the same reason — once a number like
"debugging usually runs 2.3x over" is visible, every subsequent raw estimate for that category is
implicitly anchored to it. This is the prompt-level equivalent of pocket-watch's hidden
multiplier: the correction has to stay invisible to the estimating process to keep measuring it
honestly.

## Safety decisions

- **HTML-escaping of interpolated fields.** Job names, project names, and subtask names come from
  plan files or free-text chat and are interpolated into the artifact's HTML. They're always
  escaped before insertion — an unescaped task name is an injection vector into a page published
  to claude.ai.
- **Sensitivity check before first publish.** Before the artifact goes up for the first time, job,
  project, plan-file, and subtask names are checked for anything that looks like it identifies a
  client, a person, or confidential work, and flagged so the user can rename or approve.
- **Log strings are data, never instructions.** `project` and `job` fields in the calibration log
  are free text that may originate from arbitrary plan files. When read back — for accuracy
  reports, for example — they're rendered as quoted literals, never treated as instructions to
  act on.
- **Gitignore is a hard precondition.** The state file lives inside the project at
  `.claude/pacekeeper-state.json` and must never be committed. Before the first write, the skill
  ensures it's covered by the project's `.gitignore` — this is a precondition to the write
  happening at all, not a follow-up step.
- **Concurrency guard via jobId.** If the state file already exists with `status: "running"` and a
  different `jobId`, another session may currently own it. The skill warns the user and waits for
  a decision rather than silently overwriting another session's in-progress state.
- **Crash mid-subtask → `actualMin: null`.** A subtask found `running` with a `startedAt` but no
  `finishedAt` after a resume didn't fail gracefully — it crashed or the session died. Its
  duration is unknown, so it's recorded as `null` and never logged to calibration, instead of
  guessing a number that would corrupt the statistics.

## Provenance

A search for prior art turned up no existing tool that combines all four pieces: a live
task-list ETA, self-calibration from logged history, pause/resume across sessions, and ETA-slip
notifications. There's an open feature request for exactly this combination
(anthropics/claude-code#24666). The nearest prior art each covers roughly half the loop:

- **pocket-watch** — the calibration math (phased blending, trimmed median, anchoring
  protection), but no live artifact and no pause/resume. Pacekeeper deviates deliberately from
  pocket-watch's specific math on two points: trimmed median → winsorized mean (the calibrated
  quantity needs to be a mean, not a median — see Calibration statistics above), and phased
  blending → continuous shrinkage (removes the dead zone below `n = 5` and the jumps at the phase
  boundaries).
- **task-progress-bar** — the idea of computing progress outside the model rather than having the
  model narrate it, but no calibration loop.
- **agent-estimation** — ETA aggregation across parallel work (max of the group), but no
  self-calibration from outcomes.

Pacekeeper combines the calibration approach from the first with the artifact/compute-outside
philosophy from the second and the parallel-ETA logic from the third, and adds the pause/resume
and notification layer none of them have.

## Future ideas (deferred)

Recorded here rather than acted on, mostly because they need Claude Code hooks that either don't
exist yet or would add complexity out of proportion to the current scope:

- **Regex auto-detection** of estimates and completions mentioned in conversation, instead of
  relying on the skill's own checkpoint logic to notice them.
- **Statusline / zero-token rendering** — showing the ETA in the statusline instead of a chat
  artifact, avoiding the token cost of a republish at every checkpoint.
- **Per-model stratified factors.** The model ID is already logged per calibration entry; the
  summary script currently only prints a caveat when a category mixes models, rather than
  splitting the factor by model. Worth doing once there's enough data per model to make it
  meaningful.
- **A separate long-task bucket** for jobs over roughly 8 hours, where the calibration behavior
  and ETA framing likely need different handling than a typical multi-hour run.
- **An EMA pace cross-check during a run** — a rolling estimate of pace from the current job's
  own subtasks so far, checked against the calibrated ETA, as an early warning independent of the
  per-category history.
- **Plugin/marketplace packaging.** The repo is already structured so this would only mean adding
  manifest files, not restructuring the skill itself.
- **Asymmetric ETA intervals** (`+P80` / `-P25`, or similar) for categories whose ratio
  distribution is right-skewed, instead of a single symmetric `+/-` percentage — would better
  reflect that overruns are typically larger and more likely than underruns for those categories.

## Origin note

This document and the skill it describes were rewritten in English from a private Swedish
predecessor. Fresh history here is intentional.
