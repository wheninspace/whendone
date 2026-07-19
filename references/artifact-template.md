# WhenDone artifact — publish mechanics and rendering guarantees

The page itself is written by `scripts/render_artifact.py` (state-model v2 in, full HTML
out) — the model never writes or edits artifact HTML and never computes the figures on it.
The HTML skeleton, theme handling, and all formulas live in that script; the formulas'
normative statement is references/file-formats.md's ETA computation.

## Publish mechanics (model-side)

- Canonical render invocation (all paths): `python3 <skill-dir>/scripts/render_artifact.py
  <state-file> <token-file-or-`-`> <out.html> --now "$(date -Iseconds)"` — `--now` is
  required; add `--push-status <value>` when known, `--superseded` only for the discard banner.
- Render to a file in the session scratchpad (e.g. `whendone-<job-name>.html`) and publish
  with the Artifact tool. Reuse the SAME file path at every wake (same path → same URL).
  On resume in a NEW session: mint a fresh scratchpad path (never the state file's
  `artifactFile` — untrusted; references/resume.md step 4) and pass `url` from whendone-state.json.
- Favicon: `⏱️` — identical across all updates. `<title>` (script-rendered): `WhenDone: <job name>`.
- `description`: ALWAYS the fixed string `WhenDone progress monitor` — never interpolate job,
  project, or subtask text into it (it's the gallery-card subtitle, visible on any shared link).
- The script exits non-zero with NO partial HTML on any failure → skip the publish, use the
  in-chat table, retry next wake (visibility never blocks work).

## What the script guarantees (defense-in-depth)

- `html.escape()` on every interpolated field, applied in code — plus untrusted strings
  land in text nodes only (never attributes), plus the Artifact CSP: three independent
  layers against instruction-shaped or markup-shaped task/project/plan strings.
- Status banner (RUNNING/PAUSED/DONE/SUPERSEDED) + "last updated HH:MM" in large type.
- ETA headline always carries an interval and its honesty marker — `± N min (default band — little history)`,
  `(−A/+B min) (widened to measured spread)`, or plain `(−A/+B min)` at high confidence —
  never a bare point time. Source-c jobs render pace-based ETAs labeled "(uncalibrated)".
- Task table: status icon, name (+ dim executor line when the task's `model` is known,
  `· N effort` only when `effort` is set), category, estimate, and an Actual column that is
  always a computed time — the task's raw wall span in m+s (`11 m 24 s (+42 %)`; the
  calibration log's 0.5-min floor never shows here — it's a logging rule, not a clock fact),
  `overrunning by X min` for a running task past its estimate, `—` when unstarted; never a
  status word. A bold Total row (dim-labeled
  "sum of subtasks") closes the table: plain column sums — all estimates (whole minutes);
  done tasks' actuals in m+s precision (`8 m 30 s`), deviation only once every task is done.
  Sums are work minutes, not group-aware walltime: the job-end "took" line shows walltime,
  also in m+s (`took 7 m 55 s`) — facts get seconds, estimates stay whole-minute, and the
  two totals are visibly different labeled quantities rather than one number rounded two ways.
- Token lines when token JSON is available (omitted entirely otherwise): job-level
  "Tokens: Nk spent · NM cache reads" (cache reads never summed into the headline), per-task
  dim lines, and ONE combined `≈Nk tok (group)` figure for parallel dispatch groups whose
  windows overlap (`token_usage.py` marks them `"overlap": true`) — never precise-looking
  per-member numbers.
- PAUSED: a resume box (job, plan file, next subtask, and that a new session finds the
  state via `.claude/whendone-state.json`). Footer: stop instructions + the honest push
  notification status passed via `--push-status rc|uncertain|unavailable`.
- One compact page — no growing per-wake history.
