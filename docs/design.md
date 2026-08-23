# Design rationale

Condensed reasoning behind WhenDone's design choices, so contributors don't need the original
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
  if numbers look wrong, the factor is the only place to look, not a moving baseline. The default
  minutes are hand-set priors from early development (no fitted data behind them); anchoring
  protection is why they are frozen, calibration is how they stop mattering.
- **Clamped, estimate-weighted, winsorizing mean over a raw mean.** A single blown estimate (a
  debugging subtask that took ten times longer than expected) shouldn't drag every future estimate
  with it, and a swarm of small tasks shouldn't out-vote the few large tasks that dominate actual
  wall-clock. Every individual ratio is first clamped to a fixed sanity band (`[0.1, 8]`) — cheap,
  order-independent, and active at every sample size, unlike winsorizing (see below); winsorizing
  then caps the leverage of the top/bottom 20% by rank; the resulting ratios are combined into a
  mean weighted by each row's raw estimate — instead of an unweighted mean — so a 0.5-min quick
  task and a 60-min task don't get an equal vote (see Calibration statistics). This robustness
  trade has a cost: the winsorized, clamped ratio-of-sums estimator asymptotically undershoots
  the documented sum-optimal objective (`Σ actualMin / Σ rawEstimateMin`) by roughly −4…−17%,
  a deliberate bias accepted in exchange for resistance to outlier/poisoned rows — no code
  change at current sample sizes.
- **Parallel subtasks are excluded from calibration.** When multiple subtasks run at once, their
  wall-clock durations overlap, so "actual minutes elapsed" no longer corresponds to "work done" —
  logging them would corrupt the ratio calculation. They're still shown individually in the
  artifact, but the group's contribution to the total ETA is the max of the group's estimates, not
  the sum. The synthetic parallel-group row logs the ETA rule's actual operands —
  `maxAdjusted` (max of the group's factor-adjusted estimates) and `sumAdjusted` (their sum) —
  alongside the group's wall-clock, and the summary reports wall-clock/max-adjusted and
  wall-clock/sum-adjusted medians side by side. That pair is bookkeeping, not proof: informative
  about which aggregator tracks wall-clock more closely, but either ratio is confounded with
  ordinary per-category estimate bias, so neither confirms the max rule (or the sum) is the
  statistically correct aggregator.

  **2026-08-22 reversal (P2, fixes C5):** superseded above. Parallel-group members now log
  individual calibration rows (`"parallel": true`) on their own confirmed close instead of the
  retired synthetic group row — real marker-derived member spans exist now, and the IQR-based
  spread already absorbs the contention variance the original argument worried about, so
  per-member rows no longer corrupt the ratio the way this bullet originally reasoned. Legacy
  `parallel-group` rows stay valid to parse and remain excluded from every category's factor
  (bookkeeping only, unchanged). Mechanics: `references/formulas.md`'s Calibration row
  derivation section, not restated here.

## Calibration statistics

Mirrors the docstring in `scripts/calibration_summary.py`:

- **ratio** = `actualMin / estimateMin` per completed subtask (rows with `actualMin: null` are
  excluded — those are crashed or interrupted subtasks, not data).
- **individual-ratio clamp (M21 hardening)** = every ratio is first clamped to a fixed sanity band
  `[0.1, 8]` before it reaches any pooling step. Winsorizing (next bullet) is inert below `n = 5`
  — `k = int(n * 0.2)` is `0` for `n <= 4` — exactly the sample sizes where a single blown estimate
  has the most leverage on a mean. The clamp is what actually protects a fresh category (its first
  one to four data points) from a single wild outlier; winsorizing then adds a second, rank-based
  cap once `n >= 5`. The clamp also bounds the same ratios that feed the displayed spread (IQR), so
  a single implausible ratio can't blow that out either.
- **observed factor** = the CLAMPED ratios' estimate-weighted, 20%-winsorized mean per category:
  sort the clamped ratios, then clamp the bottom and top 20% by rank to the nearest kept value
  instead of discarding them (inert below `n = 5`, see above), and take the mean of the resulting
  list — weighted by each row's raw estimate (`rawEstimateMin`), not an unweighted mean. Weighted
  this way, the calculation is equivalent to a ratio-of-sums, `Σ actualMin / Σ rawEstimateMin`,
  computed on the clamped/winsorized values rather than the raw ones.

  ETA totals are sums, so the calibrated quantity has to minimize error for a *sum* — a median
  (trimmed or not) estimates the wrong statistic for that purpose, and so does an *unweighted*
  mean of ratios: it gives a 0.5-minute quick task the same vote as a 60-minute task, and short
  tasks systematically produce the noisiest, most extreme ratios (amplified further by the
  `actualMin` floor of 0.5 min). An unweighted mean is therefore dominated by exactly the data that
  matters least to the total wall-clock the factor exists to predict. Weighting by
  `rawEstimateMin` fixes that: a category's few large tasks, which dominate its actual sum, get
  proportionally more say than a swarm of small ones.

  **This is a behavior change, made pre-release (hardening round 2, M2):** this section originally
  specified an *unweighted* mean of ratios, justified by the claim that "ETA totals are sums, so
  the calibrated quantity must track the mean ratio." That justification was true only if every
  task in a category shares the same raw estimate — not guaranteed, since scope-based adjustment
  of the raw estimate within a category is allowed. Switching to the estimate-weighted mean above
  changes the factor value computed from calibration data already on disk: categories whose raw
  estimates were already close to homogeneous are nearly unaffected; categories with a size-skewed
  mix of raw estimates will see their factor move toward whatever the large tasks' true ratio is,
  which is the correction this section was always meant to provide.
- **continuous shrinkage**: `factor = (n * observed + K * PRIOR) / (n + K)`, with `K = 5` acting
  as a fixed number of prior pseudo-observations. This replaces an earlier phased blend
  (`n < 5` → prior only, `5 <= n < 20` → 0.5/0.5, `n >= 20` → 0.3 * prior + 0.7 * observed) that
  had two problems: a dead zone below `n = 5` where new data was thrown away entirely, and hard
  jumps at the `n = 5` and `n = 20` boundaries. The shrinkage formula is identical to the old
  blend's value at exactly `n = 5` (`(5*observed + 5*1.0)/10 = 0.5*observed + 0.5*1.0`), starts
  using data from `n = 1` instead of `n = 5`, and converges smoothly toward the observed ratio as
  `n` grows instead of plateauing at `0.3 * prior + 0.7 * observed`.
- **PRIOR = 1.0.** pocket-watch, the project this scheme is adapted from, uses 1.3 as its prior,
  because its raw estimates are free-form guesses that empirically skew optimistic. WhenDone's
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

**This protection is partial, not complete — say so plainly.** The raw-first ordering and the
never-mention-factors rule only cover the channels they were designed for; at least two others
leak the same information:

- The state file stores `rawEstimateMin` and `estimateMin` side by side on every subtask (see
  `references/file-formats.md`), so any session that reads the state file — every checkpoint,
  every resume — can trivially divide one by the other and recover the per-category factor
  already baked into that job's estimates, with no need to read the calibration summary at all.
- Resume and accuracy-report sessions have factors in context by design: Resume's job-start
  classify/estimate steps (see the Resume section) run with the calibration summary already
  read for the ongoing job, and the accuracy report (see Accuracy report below) prints factors
  and mean ratios directly into chat on request. Nothing currently stops a raw estimate made
  later in that same session — for an unrelated job, or for tasks added to the same job — from
  being anchored by a factor value the session has already seen.

Neither leak is fixed at the prompt level in this round — a model that has already seen a factor
value cannot un-see it, and nothing in SKILL.md marks or excludes an estimate that was produced
with a factor already in context. The cheap mitigation available, documented here for anyone
hardening this further: after presenting an accuracy report, note in chat that any raw estimate
made later in the same session is anchored by having just seen the factors, so the user knows to
discount it. That only flags the compromise in the moment it happens — it doesn't prevent the
resulting row from quietly pulling the category factor back toward 1.0 if logged as raw. A more
complete fix would tag such rows (e.g. an `anchored: true` field on the logged entry, skipped by
`calibration_summary.py` when computing the factor) so contaminated estimates stop diluting the
statistic instead of merely being disclosed after the fact. That field is explicitly NOT added
this round — deferred, along with the read-side logic that would need to consume it.

**Project-mix caveat (round-3 F10, 2026-07-18):** the summary warns when a category pools
data from more than one project (mirroring the model-mix caveat) because a pooled factor can
be simultaneously "high confidence" and badly wrong for one of the projects. Advisory only:
the confidence label and factor are unchanged — a downgrade would alter the interval contract
shared with `references/formulas.md` (implemented in `render_artifact.py`). Revisit if pooled
cross-project "high confidence"
proves misleading in practice.

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
  `.claude/whendone-state.json` and must never be committed. Before the first write, the skill
  ensures it's covered by the project's `.gitignore` — this is a precondition to the write
  happening at all, not a follow-up step.
- **Write targets are validated.** A cloned or shared repo can ship `.claude/whendone-state.json`,
  `.gitignore`, `.claude/whendone-tail.lock`, or `.claude/whendone-closes.jsonl` as a symlink
  instead of a regular file, pointing anywhere the user has write access — e.g. `~/.zshrc`.
  Without a check, job start would overwrite whatever the symlink resolves to with state JSON,
  a gitignore edit, a lockfile, or a marker-file append, and attacker-influenced `job`/task-name
  free-text would land inside it.
  Before the first write to any of these paths, the skill verifies it either doesn't
  exist yet or is a regular file whose canonical path (`realpath`) resolves inside the project
  root; a symlink, or a canonical path outside the root, stops the write and flags the user
  instead of proceeding. The same untrusted-input treatment applies to `artifactFile` on resume:
  rather than validating the state-supplied path, the skill mints a fresh filename in its own
  scratchpad and never treats the state file's string as a write target at all — nothing
  state-controlled is left to canonicalize or reject. `.claude/STOP` is out of scope for this
  check: it is only ever deleted, never written, so a symlinked STOP is harmless to remove
  (unlinking a symlink doesn't touch its target).
- **Concurrency guard via planFile/job, not jobId.** If the state file already exists with
  `status: "running"`, the skill compares its `planFile`/`job` to the job being started, not its
  `jobId`. `jobId` is derived from the run's own start timestamp, so a same-job crash-and-restart
  always produces a NEW `jobId` — keying the guard on `jobId` would misclassify every ordinary
  crash-resume as "a different session owns it" and break the resume path this design relies on.
  Same `planFile`/`job` means a crashed or still-live prior session (offer resume / discard /
  abort); a different `planFile`/`job` means another session may genuinely own it (warn, let the
  user decide). `jobId` is instead used as the ownership key at every later checkpoint: each
  session remembers the `jobId` it saw at job start, and a mismatch there (not at job start) means
  another session discarded and replaced this state file while this one kept running.
- **Crash mid-subtask → `actualMin: null`.** A subtask found `running` with a `startedAt` but no
  `finishedAt` after a resume didn't fail gracefully — it crashed or the session died. Its
  duration is unknown, so it's recorded as `null` and never logged to calibration, instead of
  guessing a number that would corrupt the statistics.

## README asset provenance

The README's hero image (since v0.8.0) is the DONE-state artifact of the 2026-08-22 run that
executed the v0.8.0 flip-readiness plan itself (jobId 20260822T1611, 12 subtasks, monitored
by the then-installed whendone; state and closes archived in the maintainer's local
run-evidence directory). The page was re-rendered from that run's final state file with the
v0.8.0 renderer (`render_artifact.py`, `--now` = the run's own end timestamp) and captured
with headless Chrome (620 CSS px, device scale 2) — same data, current renderer; the live run
predates the group-aware totals it displays. The run used the dev working tree (the installed
skill dir is a symlink to this repo) — not a fresh clone at the tag. The previous hero (the
2026-07-17 14-subtask hardening run, recorded in
[docs/test-log.md](test-log.md#real-end-to-end-run-under-whendone-monitoring--2026-07-17))
was a manually captured screenshot.

The second README image (`assets/source-b-progress.png`, since v0.8.0) is a manually captured
screenshot of the live artifact mid-run during the Source B re-verification run (jobId
20260822T2155, 2026-08-22 22:06 — the run that dropped Source B's experimental label; state
archived in the maintainer's local run-evidence directory). Unlike the hero it is NOT a
re-render: it shows the running page exactly as published (RUNNING banner, live ETA
−4/+9 min widened to measured spread, progress bar, per-phase agent counts, 8/9 workflow
agents).

To confirm the hero screenshot is faithful,
open [`assets/real-run-artifact.html`](../assets/real-run-artifact.html) — the actual rendered
page. A simpler constructed example lives in
[`assets/demo-artifact.html`](../assets/demo-artifact.html), generated (not hand-edited) from
[`assets/demo-state.json`](../assets/demo-state.json); regenerate it with
`python3 scripts/render_artifact.py assets/demo-state.json - assets/demo-artifact.html --now
2026-07-18T14:35:00+02:00 --summary -`.

## Reproducing the README's synthetic calibration fixture

The Overhead section's `calibration-summary.md` provenance figure is generated fresh, not
copied from an old run — the log below produces a deterministic 60-row, 8-category, 3-project
synthetic calibration log, feeds it through the real `scripts/calibration_summary.py` unmodified,
and prints the resulting summary file's `wc -c`-equivalent size in chars. Re-running it reproduces
the same byte count every time (verified: 2,417 bytes on 2026-08-13, after the footer's restated
interval rule was cut to a pointer in the trigger-path slimming pass — was 3,401 bytes (3,330
chars) on both of two runs 2026-07-19, and 3,371 on 2026-07-18 before that day's later
`calibration_summary.py` display changes):

```bash
python3 - <<'EOF'
import json, subprocess, tempfile, os
cats = ["mechanical-implementation","judgment-coding","testing","debugging",
        "research","documentation","review","deploy-infra"]
rows = []
for i in range(60):
    cat = cats[i % 8]
    rows.append(json.dumps({"date": "2026-07-01", "project": "proj%d" % (i % 3),
        "job": "job", "category": cat, "rawEstimateMin": 10,
        "actualMin": 8.0 + (i % 7), "model": "claude-sonnet-5", "client": "cli"}))
td = tempfile.mkdtemp()
jp, op = os.path.join(td, "c.jsonl"), os.path.join(td, "s.md")
open(jp, "w").write("\n".join(rows) + "\n")
subprocess.run(["python3", "scripts/calibration_summary.py", jp, op], check=True)
print(os.path.getsize(op), "chars:", op)
EOF
```

## Provenance

A search for prior art turned up no existing tool that combines all four pieces: a live
task-list ETA, self-calibration from logged history, pause/resume across sessions, and ETA-slip
notifications. There's an open feature request for exactly this combination
(anthropics/claude-code#24666). The nearest prior art each covers roughly half the loop:

- **pocket-watch** — the calibration math (phased blending, trimmed median, anchoring
  protection), but no live artifact and no pause/resume. WhenDone deviates deliberately from
  pocket-watch's specific math on two points: trimmed median → winsorized mean (the calibrated
  quantity needs to be a mean, not a median — see Calibration statistics above), and phased
  blending → continuous shrinkage (removes the dead zone below `n = 5` and the jumps at the phase
  boundaries).
- **task-progress-bar** — the idea of computing progress outside the model rather than having the
  model narrate it, but no calibration loop.
- **agent-estimation** — ETA aggregation across parallel work (max of the group), but no
  self-calibration from outcomes.

WhenDone combines the calibration approach from the first with the artifact/compute-outside
philosophy from the second and the parallel-ETA logic from the third, and adds the pause/resume
and notification layer none of them have.

## Stage 3: declare-once, tail-thereafter — design rationale

**Crash-ordering analysis (implemented by `tail_progress.py::handle_completion`).** A crash
between (b) and (b2), or during (b2) itself, loses at most the token refresh and/or leaves the
model as the still-unresolved alias — cosmetic, and self-heals at the next checkpoint that
finds a bare alias (the task is already durably marked done from (b), so Resume never redoes
it). A crash between (b2) and (c) loses at most one calibration row — harmless, same reason. A
crash between (c) and (d) leaves `actualMin` null in the state file (display-only, cosmetic)
but the log already holds the row, with the resolved model if (b2) matched. A crash after (d)
but before step 6 runs (e.g. during the republish in step 2) leaves the next subtask simply
`pending` with no `startedAt` stamped — Resume then starts it fresh like any other pending
task, rather than finding a task stuck "running" that never actually started. All of
(b)/(b2)/(c) gaps are strictly safer than the reverse order (log first, mark done second),
which can double-log AND re-execute a finished subtask.

One consequence, by design: the checkpoint overhead in steps 2-5 (token refresh, artifact
republish, slip check) falls INSIDE the next task's measured window rather than an uncounted
gap — this is deliberate, since it keeps calibrated ETAs honest about wall-clock time for
whendone-monitored jobs. (Token refresh itself now happens earlier, in step 1(b2), before the
next-task start timestamp is captured in step 1(c) — so it is NOT part of this overlap; only
steps 2-5's artifact republish and slip check are.) Under stage-3 tailing this attribution
question is largely **moot (F14)**: both `startedAt` and `finishedAt` come from transcript
entry timestamps, so there is no model-executed gap between them to attribute.

The script keeps these out of the factors; it reports wall-clock/max-adjusted and
wall-clock/sum-adjusted medians as bookkeeping — informative about which aggregator tracks
wall-clock more closely, not proof that either rule is the statistically correct one.

The watcher itself follows a demotion ladder rather than requiring one specific mechanism: try
Monitor first (a persistent watch running `tail_progress.py --follow`) because it needs no
polling and gives the lowest latency; if Monitor is unavailable, denied, or fails, fall back to
a background Bash job running the same `--follow` loop with `--exit-on-event`, and if that too
is unavailable, fall back further to one-shot boundary-driven runs triggered by the model at
natural checkpoints — each demotion is stated once in chat rather than silently swallowed. The
tailer re-scans the tailed transcript(s) in full every cycle instead of tracking a byte/line
cursor (D4): correctness comes from comparing transcript state against the state file's task
statuses, not from stream position, so a cursor that drifted, wrapped, or pointed at a
rotated/truncated file can never cause a missed or duplicated transition — only
pending/running→done transitions act, and an already-`done` task is never re-transitioned or
re-appended, which kills the cursor-drift bug class outright at the cost of local-only CPU
that is accepted as cheap. The `.claude/whendone-tail.lock` pid lockfile exists because exactly
one process may hold single-writer ownership of `whendone-state.json` at a time; without it a
duplicate `--follow` invocation (e.g. from a relaunch race, or a second session attaching to
the same job) could interleave two writers' atomic rewrites and double-append calibration rows
for the same completion. Finally, the tailer exits as soon as `status` leaves `"running"`
(stopped, paused, or done) rather than continuing to watch, because ownership of the state file
is meant to pass back to the model at exactly that moment — a watcher that kept running past a
stop/pause request would race the model's own edits to the same file.

## Stage 4: Source B — journal survey, tag attribution, deferred calibration rows

**The journal-format survey (verified 2026-07-18).** Before any Source-B code was written, 17
real `journal.jsonl` files were surveyed under `~/.claude/projects/*/*/subagents/workflows/wf_*/`,
spanning roughly five weeks and seven projects, plus a fresh live probe run (`wf_6d1be0bd-c7d`)
run specifically to re-verify the survey against output produced in-session rather than trusting
historical samples alone. All 17 plus the probe agreed on one v2 schema: exactly two line shapes,
`started` and `result`, with no timestamps, phase names, or labels anywhere in the journal —
`key` is an opaque hash of `(prompt, opts)`, and its `v2:` prefix is the only version marker the
format exposes, so it's also the only thing the drift detector can key on
(`scripts/workflow_journal.py`'s `KEY_RE`). `agentId` is 17 lowercase hex characters in every
sampled line (the parser's `AID_RE` deliberately accepts a wider `[a-z0-9]{8,64}` band, so a
future engine change to agentId length doesn't itself count as drift). Every agent transcript's
first entry carries the `[wd:<slug>]` tag verbatim in its message content, and a completion record
(`<session-dir>/workflows/<runId>.json`) appears only at run end — its existence is the
deterministic all-done signal the finalize pass waits for.

**Spec §5.4 correction.** The spec states that the journal supplies per-agent started/result
events, which the survey confirms — but §5.4 also implies per-agent timing comes from the journal
itself, and it doesn't: the journal carries no timestamps of any kind. Timing comes from the
paired agent transcript's own entry timestamps (first entry ≈ start, last entry ≈ end), the same
file shape `token_usage.py` already parses. The spec file is left as written — it's the decision
record for what was planned — and this correction is recorded here instead, as the place
implementation-detail corrections belong once a spec has shipped.

**Why prompt-tag attribution (B1) over the alternatives.** Two other attribution schemes were on
the table before B1: declared-barrier count windows (treat everything between two `phase()` calls
as one window) and agents-counted-only (give up on per-phase attribution entirely and just show a
job-wide agent count). Barrier windows mis-attribute as soon as a script uses `pipeline()` — an
agent whose actual work spans a phase boundary gets silently assigned to whichever window's
counting happened to be active when it finished, with no signal in the journal to catch the
mistake. Agents-counted-only sidesteps that failure mode by refusing to attribute at all, but that
throws away exactly the per-phase calibration this stage exists to add. Tagging every `agent()`
prompt with `[wd:<slug>]` at authoring time sidesteps both: the tag is written by the lead,
travels with the agent regardless of how the script's control flow interleaves phases, and is the
only phase signal that has to reach disk at all — an untagged agent still counts job-wide, but
attributes to nothing rather than to the wrong thing.

**Why display states are revertible but calibration rows aren't (B4).** The state file's
per-task status can legally flip back from `done` to `running` — a late `pipeline()` agent
belonging to an already-`done` phase is not a bug, since the completion record hasn't been
written yet and the journal makes no promise about agent completion ordering. That's fine for a
display field, which exists to be looked at and is redrawn every cycle anyway. It would not be
fine for a calibration row: those are appended once, read later by `calibration_summary.py`, and
never revisited. So calibration rows wait for the run's completion record specifically — the one
signal in the whole format that's guaranteed not to arrive early — and are computed once from the
frozen set of attributed agents at that point. A display transition can be wrong for a few
seconds and self-correct; a calibration row can't be un-appended, so it's held to a stricter bar
than the field it summarizes.

**Why one row per phase, at the phase's declared category (B4, user-approved).** The alternative
shapes considered were one row per agent and a synthetic group row like Source A's parallel-group
bookkeeping. Per-agent rows were rejected because there's no per-agent estimate to divide by —
the lead declares an estimate per phase, not per agent, so a per-agent ratio would need a
denominator that was never actually estimated, i.e. a fabricated one. A synthetic group row
(mirroring the Source-A parallel-group row that logs `maxAdjusted`/`sumAdjusted`) doesn't fit
either, for the same reason in reverse: that row exists to compare wall-clock against the *rule's*
operands for a group of individually-estimated subtasks, and a Workflow phase has no per-member
estimates to aggregate in the first place. One row per phase, at the category the lead declared
for that phase, is the only shape with a real estimate behind it. It also has a side effect worth
stating plainly: the phase estimate was always implicitly a wall-clock estimate for however much
of the phase runs in parallel, so the factor learned from `span = min(start)/max(end)` over the
phase's attributed agents honestly folds in whatever parallel speedup the phase actually got —
the factor isn't pretending phases ran serially when they didn't.

**The degrade ladder (B5).** A journal line only counts as valid if its `type` is `started` or
`result`, its `agentId` matches the hex pattern, and its `key` carries the `v2:` prefix over a
64-hex hash — that's the version detector. Above a 20% invalid-line ratio (or a journal that's
unparseable or missing entirely) the tailer degrades rather than crashing: format drift falls back
to job-level agent counts only, with no phase attribution and no calibration rows, while a run
directory that can't be found or a journal too large to parse safely falls back further, to
`tail-unavailable` — the declared per-phase estimates still render and the ETA still runs on
them, just without live updates.

**Once-per-reason emission, and a correction to B10's own wording (B10).** Monitor turns every
stdout line into a model wake, so a tailer that re-emitted the same failure on every cycle would
cost a wake per cycle for information the model has already seen. B10 was originally written as
"once per watcher run" for both failure events, but the two ended up implemented differently, and
the implementation is the one worth keeping. `tail-unavailable` is emitted once per
`(event, reason)` pair via `_emit_once`, which tracks what it's already emitted in an in-process
set (`args._b_emitted`) — that scoping really is per watcher run, since the set doesn't survive
past the process. `journal-format-drift` instead gates on the persisted `wfDriftNotified`
state-file flag, which is written to disk the first time drift fires and never cleared — so it
survives a watcher restart, and a resumed watcher that reads the same still-drifted journal stays
silent instead of re-alerting. That's the better choice specifically for drift: drift is a
property of the engine's output format, not of any one watcher process, so re-announcing it every
time the watcher happens to restart would just be noise about a fact the user was already told.
`tail-unavailable`, by contrast, is closer to a transient condition (a run directory that hasn't
appeared yet, a momentarily oversized file) where re-checking on a fresh watcher run is more
defensible than suppressing it forever.

**Riders.** Two smaller changes rode along with this stage. `token_usage.transcript_paths()` now
also globs `subagents/workflows/wf_*/agent-*.jsonl` (B8) — without it, every agent transcript
spawned by a Workflow run was invisible to the token display, so a Source-B job's token line
would read as empty however much work the agents actually did. And `tail_progress.py`'s
`_render_out_path` (B14) now validates rather than trusts the state file's `artifactFile` string:
since `artifactFile` comes from the same untrusted-input class as everything else in the state
file, the renderer now requires an absolute, non-symlinked `.html` path with a non-symlinked
parent directory before using it, and falls back to a sanitized-jobId path in the system tempdir
otherwise — consistent with the fail-soft posture everywhere else in the tailer: a rejected path
degrades the render location, it never blocks the render.

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

## Doc-growth and decision-code policy (2026-08-22)

**Offset rule (P10, from the plan's Global Constraints):** any doc-growth pass that adds more
than 50 on-path tokens (SKILL.md, `references/source-a.md`, `references/file-formats.md`,
`references/artifact-template.md`) must name its offsetting cut in the same commit — a
net-positive on-path pass doesn't land without one. This is what keeps a multi-task wave from
ratcheting the trigger path upward one small, individually-reasonable addition at a time.

**Decision-code containment (P10, second half):** future waves use date-scoped decision-code
prefixes (e.g. `2026-08-22/P1`) inside design.md and plan files only, never inside reference
docs. Existing inline glosses already in reference docs (`(P2, ...)`, `(P3)`, `(P5, ...)`, and
similar) stay as-is until the passage they annotate is next touched for another reason, then
convert to plain language rather than picking up a fresh code.

## Release process (2026-08-23)

The README's install lines pin a release tag deliberately: a skill is instructions an agent
executes with the user's permissions, so "install an immutable, reviewed snapshot — the exact
tree the test-log and CI evidence describe" is a security and evidence property, not
versioning vanity (the Update section's tag-to-tag rule is the same posture). The known cost
is bookkeeping: hardcoded version strings and main-vs-tag drift. Three mechanisms keep that
cost near zero:

1. **One atomic motion:** `python3 scripts/release.py vX.Y.Z` bumps every README install
   line, commits, and tags that commit — so the tag's own install text names the tag by
   construction. It refuses dirty trees, non-main branches, and existing tags; `--dry-run`
   previews; pushing stays a separate owner action (or `--push`).
2. **CI backstop:** on any tag push, the `tag-consistency` job fails unless the README's
   install pins equal the pushed tag — a hand-cut tag that skips the script fails loudly.
3. **Patch-tag norm:** any user-visible change that lands on main after a tag (docs included)
   gets a cheap patch tag (vX.Y.Z+1) rather than living as drift — the GitHub-rendered README
   must describe the tool the install line delivers. Tags are free; drift is not. Published
   tags still never move.

## Appendix: trigger-figure measurement history

The README's Overhead table is the single current-state home for the trigger-path token figure
(P10) — read the number there, not here. This appendix instead records how that number moved
across the measurements that produced it, for anyone auditing the trend rather than just the
current value.

Fifteen movements, each under an identical raw `tiktoken cl100k_base` sum unless noted:

1. Between the stage-5 release (12,313) and the next pass, on-path commits (local-time policy,
   renderer display overhaul) grew `file-formats.md` and `artifact-template.md`, taking the
   trigger path to 12,552.
2. That pass then extracted the Resume mechanics from SKILL.md into `references/resume.md`
   (off the trigger path), dropping the path to 11,639 — a 913-token saving every non-resume
   trigger now gets.
3. The same day's consolidated pre-flip review then fixed stale/contradictory lines on the path
   (source shipped-status, step-8 Source-C carve-out, pointer repairs), adding +74 → 11,713.
4. A later task (honest raw-vs-as-read headroom) re-measured after further tasks grew
   `source-a.md`, `file-formats.md`, and `source-b.md` on- and off-path, plus a SKILL.md "When
   not to use" edit, moving the raw total to 11,849.
5. The same-day Stage-2 polish pass (nominal-band marker renamed to "default band — little
   history" with its file-formats.md mention, protocol write-back sentences in source-a.md,
   the matcher-tolerance note, and checkpoint→wake vocabulary) added +180 on-path → 12,029.
6. The 2026-08-13 TaskCreate/TaskUpdate support pass (newer harnesses replace TodoWrite;
   SKILL.md and source-a.md name both tool families once, other mentions generalized to
   "todo list") added +48 on-path → 12,077.
7. The 2026-08-13 trigger-path slimming pass moved the script-implemented formula spec
   (ETA/interval/slip rules, calibration-row derivation, renderer formatting guarantees) off
   the trigger path into the new maintainer-only `references/formulas.md`, moved pause
   accounting into `references/resume.md` (its only consumer), and cut the
   calibration-summary footer's restated interval rule to a pointer (the machine-readable
   q1/q3 lines are unchanged — `render_artifact.py` parses those, never the prose):
   −1,558 → 10,519 raw (SKILL.md 3,410 + source-a.md 2,115 + file-formats.md 3,517 +
   artifact-template.md 651 + calibration-summary allowance 826, the fixture now 2,417
   bytes), ≈11.9–12.0k as-read. This pass also lowered the "When not to use" time
   threshold from ~45 to ~30 min — the amortization line the threshold rests on moved with
   the trigger cost.
8. The 2026-08-13 trigger-path dedup pass (same day, later) removed third copies of rules
   whose normative homes already existed: file-formats.md's restatements of the write-target/
   gitignore preconditions, the job-start concurrency/STOP/malformed-JSON rules (normative:
   SKILL.md steps 1-4 and 7; rationale: this file's Safety decisions), and the calibration-row
   schema plus field rules (moved to maintainer-only `references/formulas.md` — the model
   never constructs rows); SKILL.md's copies of the trigger-cost figures (README's Overhead
   table is now their single home), the no-publish-gate restatement in "When not to use", the
   superseded-banner rationale, and a repeated symlink-safety note: −734 → 9,785 raw
   (SKILL.md 3,285 + source-a.md 2,115 + file-formats.md 2,908 + artifact-template.md 651 +
   calibration-summary allowance 826, fixture unchanged at 2,417 bytes), ≈11.0–11.2k as-read
   (383 on-path reference lines × 3.3–3.6).
9. The 2026-08-14 time-attribution-rework protocol pass (Task 7) documented the new close
   authority (todo/TaskUpdate `completed` transition only, never a subagent result;
   dispatch-naming scope now feeds a *delegated span* rather than deciding closure; the
   forgetful-fallback → `unconfirmed`/no-calibration-row rule; the shipped reopen contract —
   an `unconfirmed` task converts to a confirmed close the moment todo `completed` evidence
   appears, and only absent that does a later matched dispatch still in flight reopen it,
   narrower than the plan's draft "no todo evidence at all" condition since it also covers an
   observed `in_progress` with no `completed` yet), `rawEstimateMin`'s full-lifecycle scope, the two new
   Source-A-only wake rows (`idle`, `publishLag`), and D6 project-root pinning under
   worktrees (one statement in source-a.md's Declare-once section, pointed at from SKILL.md
   and file-formats.md rather than restated): +784 → 10,569 raw (SKILL.md 3,318 +
   source-a.md 2,574 + file-formats.md 3,200 + artifact-template.md 651 + calibration-summary
   allowance 826, fixture unchanged at 2,417 bytes), 11,958–12,085 as-read (421 on-path
   reference lines × 3.3–3.6) — 1,915–2,042 still to spare under the 14,000 budget.
10. The 2026-08-14 final whole-branch review then completed the D6 pinning the movement-9 pass
    had left half-applied (SKILL.md job-start step 1, the step-7 write-target precondition, the
    Stop procedure's STOP delete, and source-a.md's job-end `token_usage.py` command all still
    used bare cwd-relative `.claude/…` paths, which resolve to a linked worktree's empty
    `.claude/` in exactly the scenario D6 exists for) and corrected file-formats.md's
    `lastChangedEventAt` label (written on every source; only the `publishLag` comparison is
    Source-A-gated): +33 → 10,602 raw (SKILL.md 3,333 + source-a.md 2,578 + file-formats.md
    3,214 + artifact-template.md 651 + calibration-summary allowance 826, fixture unchanged at
    2,417 bytes), 11,995–12,121 as-read (422 on-path reference lines × 3.3–3.6, one line more
    than movement 9) — 1,879–2,005 still to spare. The same pass edited `references/resume.md`
    (1,502 → 1,563, off-path, resume-only) and `references/formulas.md` (off-path).
11. The 2026-08-16 background-dispatch/notification-close documentation pass (Task 8) grew
    `source-a.md` (+43) and `file-formats.md` (+30) on-path documenting the ack-vs-agent-done
    close authority, the `in_progress` reopen, and the all-done quiet-transcript grace: +73 →
    10,675 raw (SKILL.md 3,333 + source-a.md 2,621 + file-formats.md 3,244 + artifact-template.md
    651 + calibration-summary allowance 826, fixture unchanged at 2,417 bytes), 12,074–12,201
    as-read (424 on-path reference lines × 3.3–3.6, two lines more than movement 10) — 1,799–1,926
    still to spare under the 14,000 budget.
12. The 2026-08-22 close-authority/totals-block documentation pass (Task 3, revised after
    review, then adjusted by the same day's final whole-branch fix wave) documented the
    lead-written close-marker fallback (`whendone-closes.jsonl`, todo-EQUIVALENT evidence for
    both `in_progress` start-authority and `completed` close, added to source-a.md's
    Declare-once paragraph and file-formats.md as a new short mechanics-only entry — the
    authority semantics live in formulas.md alone, file-formats.md keeps only
    location/fields/caps/stale-guard) and the totals-block's three row names/reconciliation
    rule (moved off the old dim `<p>` orchestration line, consolidated into formulas.md's D9
    section, off-path); the fix wave additionally widened source-a.md's close-rule sentence to
    point at the marker-file equivalent (same line, no new reference line added): +381 →
    11,056 raw (SKILL.md 3,333 unchanged + source-a.md 2,840 + file-formats.md 3,406 +
    artifact-template.md 651 unchanged + calibration-summary allowance 826, fixture unchanged
    at 2,417 bytes), 12,518–12,651 as-read (443 on-path reference lines × 3.3–3.6, nineteen more
    than movement 11) — 1,349–1,482 still to spare under the 14,000 budget.
13. Four 2026-08-22 v0.8.0 flip-readiness-wave commits grew the path further before the wave's
    own token-budget task ran, none re-measuring the Overhead table at the time: the P2 fix
    (parallel-group members log individual calibration rows) added +49 on file-formats.md alone
    → 11,105; Task A3 (marker-missing nudge event, L1 death-detection protocol, no-dead-end doc)
    added +318 (source-a.md +215, file-formats.md +103) → 11,423; P6/P7/P8 (gate Source C on a
    todo tool, freeze Source B, marker channel primary) added +42 (SKILL.md +2, source-a.md +40)
    → 11,465; Task B2 (consistency fixes: `originalTotalMin` scope, closes.jsonl enumeration, tag
    history, P2 rationale) added +18 on SKILL.md → 11,483 raw (SKILL.md 3,353 + source-a.md
    3,095 + file-formats.md 3,558 + artifact-template.md 651 unchanged + calibration-summary
    allowance 826, fixture unchanged at 2,417 bytes), ≈13,018–13,157 as-read (465 on-path
    reference lines × 3.3–3.6, twenty-two more than movement 12) — reconstructed exactly via a
    fresh authoritative re-measure per P10, rather than chased commit-by-commit against any
    earlier partial figure.
14. **Task B3 (2026-08-22, P10 — docs single-home + registry containment)** trimmed prose
    duplication across all four on-path files rather than cutting any rule: collapsed the
    Watcher-ladder L1/L2/L3 restatement and the wake-handling "model's move" text that SKILL.md
    and source-a.md both carried in full, reordered source-a.md's close-marker paragraph
    (mechanism before authority-framing, per Task B1's review), shortened the sensitivity-check
    examples and the Source-C no-todo-tool chat line, dropped maintainer-only asides ("Zero new
    code", "Do not duplicate that mechanics here") and the fully-restated Source-B field list
    (source-b.md's own definitions are normative), and folded several repeated invariant
    reminders down to one on-path copy each. It also added `markerMissingNotifiedAt` to
    file-formats.md's JSON schema example (Task A3 had documented it in prose only) and fixed
    source-a.md's stale "the model's three moves" wording (the list had grown past three; the
    count was dropped rather than corrected to a new number): −588 → 10,895 raw (SKILL.md 3,353
    → 3,175 [−178] + source-a.md 3,095 → 2,907 [−188] + file-formats.md 3,558 → 3,343 [−215] +
    artifact-template.md 651 → 644 [−7] + calibration-summary allowance 826 unchanged), ≈12,387–
    12,522 as-read (452 on-path reference lines × 3.3–3.6, thirteen fewer than movement 13) —
    161 tokens of headroom under the Session B exit criterion (11,056 raw, movement 12's value),
    1,478–1,613 to spare under the 14,000 as-read budget. This pass also re-stamped the README
    Overhead table and converted design.md's own current-state statements (this appendix's
    overview and its Method note below) to point at that stamp instead of restating it, per P10.
15. **Task C3 (2026-08-22, P13 — five prescribed user-facing say-so sentences)** added four
    verbatim sentences to `references/source-a.md` (watcher-demotion, L1-relaunch,
    ownership-lost, no-todo-tool marker note) and one to `SKILL.md` (artifact-tool-absent):
    +136 → 11,031 raw (SKILL.md 3,175 → 3,200 [+25] + source-a.md 2,907 → 3,018 [+111] +
    file-formats.md 3,343 unchanged + artifact-template.md 644 unchanged + calibration-summary
    allowance 826 unchanged) — 25 tokens under the 11,056 ceiling. See the README Overhead
    table (P10's single current-state home) for the as-read figure this movement produced,
    rather than a range restated here.
16. **v0.8.0 release polish (2026-08-22, two small waves):** the final-review fix wave
    (P4 false-positive fix, softened topology claim, P10 restamp) added +11 to
    `references/source-a.md` (3,018 → 3,029), and Session D's interpreter-fallback robustness
    wording (fallback on failure, not only absence — Windows Store `python3` stub) added +12
    (SKILL.md 3,200 → 3,202 [+2] + source-a.md 3,029 → 3,039 [+10]): 11,031 → 11,054 raw
    (file-formats.md 3,343, artifact-template.md 644, calibration-summary allowance 826 all
    unchanged) — 2 tokens under the 11,056 ceiling. Current as-read figure: README Overhead
    table.

**Method note (for reproduction):** the figures above are a raw token sum of the file contents
on the trigger path (no Read-tool line-number prefixes) plus the calibration-summary output;
reconstructing the stage-5 commit this way reads 12,385 vs the 12,313 recorded at the time — a
~0.6% additive reconstruction variance that cancels out in the 913-token delta between
movements 1 and 2. Current headroom against the 14,000 as-read budget: see the README Overhead
table (P10's single current-state home) rather than a number restated here — movement 15 above
records the pass that produced it.