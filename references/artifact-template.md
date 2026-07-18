# WhenDone artifact — publish mechanics and rendering guarantees

The page itself is written by `scripts/render_artifact.py` (state-model v2 in, full HTML
out) — the model never writes or edits artifact HTML and never computes the figures on it.
The HTML skeleton, theme handling, and all formulas live in that script; the formulas'
normative statement is references/file-formats.md's ETA computation.

## Publish mechanics (model-side)

- Render to a file in the session scratchpad (e.g. `whendone-<job-name>.html`) and publish
  with the Artifact tool. Reuse the SAME file path at every checkpoint (same path → same
  URL). On resume in a NEW session: mint a fresh scratchpad path (never the state file's
  `artifactFile` — untrusted; see SKILL.md Resume step 4) and pass `url` from
  whendone-state.json to the Artifact tool.
- Favicon: `⏱️` — identical across all updates. `<title>` (script-rendered): the job's name.
- `description`: ALWAYS the fixed string `WhenDone progress monitor` — a constant cannot
  leak; never interpolate job, project, or subtask text into it (it is the gallery-card
  subtitle, visible on the user's gallery and any shared link).
- The script exits non-zero with NO partial HTML on any failure → skip the publish, use the
  in-chat table, retry next checkpoint (visibility never blocks work).

## What the script guarantees (defense-in-depth)

- `html.escape()` on every interpolated field, applied in code — plus untrusted strings
  land in text nodes only (never attributes), plus the Artifact CSP: three independent
  layers against instruction-shaped or markup-shaped task/project/plan strings.
- Status banner (RUNNING/PAUSED/DONE/SUPERSEDED) + "last updated HH:MM" in large type.
- ETA headline always carries an interval and its honesty marker — `± N min (nominal)`,
  `(−A/+B min) (widened to measured spread)`, or plain `(−A/+B min)` at high confidence —
  never a bare point time. Source-c jobs render pace-based ETAs labeled "(uncalibrated)".
- Task table: status icon, name (+ dim executor line when the task's `model` is known,
  `· N effort` only when `effort` is set), category, estimate, and an Actual column that is
  always a computed time — `11 m (+38 %)`, `overrunning by X min` for a running task past
  its estimate, `—` when unstarted; never a status word.
- Token lines when token JSON is available (omitted entirely otherwise): job-level
  "Tokens: Nk spent · NM cache reads" (cache reads never summed into the headline), per-task
  dim lines, and ONE combined `≈Nk tok (group)` figure for parallel dispatch groups whose
  windows overlap (`token_usage.py` marks them `"overlap": true`) — never precise-looking
  per-member numbers.
- PAUSED: a resume box (job, plan file, next subtask, and that a new session finds the
  state via `.claude/whendone-state.json`). Footer: stop instructions + the honest push
  notification status passed via `--push-status rc|uncertain|unavailable`.
- One compact page — no growing per-checkpoint history.
