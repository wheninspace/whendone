# Changelog

## v0.2.0 — 2026-07-17

Hardened after a second (round-2) adversarial review — 6 personas + 3 README readers plus
an external threat model — closing every confirmed release blocker. **The jsonl row schema
and script interfaces changed** (see below), hence the minor-version bump.

Scripts:
- New `scripts/append_calibration.py`: the checkpoint now writes the calibration row as a JSON
  file (Write tool, data — never spliced into shell/Python source) and a validating helper
  appends it, closing the source-injection surface. The helper — not the model — computes
  `actualMin` from the timestamps (one decimal, min 0.5), logging `null` on backward clock skew.
- Calibration rows now carry `startedAt`/`finishedAt`; `calibration_summary.py` derives
  `actualMin` from them and skips rows whose logged value disagrees with the timestamps.
- **Factor computation changed (M2): the observed per-category factor is now the
  estimate-weighted (ratio-of-sums) winsorized mean of ratios**, which minimizes total-ETA
  error for a summed estimate. This changes factor values for any pre-existing calibration data.
  Ratios are also clamped to [0.1, 8] before pooling (active at all n).
- `token_usage.py`: job/subagent token buckets are bounded to the job's own window (was the
  whole session, >10× overcount when invoked mid-session); added `--task N` mode; cross-file
  message-id dedup; an `overlap` flag for parallel task windows; a per-file size cap.
- `calibration_summary.py` rotation is now guarded by a cross-platform lockfile with a
  re-read-under-lock (so a concurrent append is never destroyed), an idempotency guard, and a
  size cap; `--report` "Last-10" column is the unshrunk winsorized mean; legacy-key rows counted.

Protocol / docs:
- One computable ETA-interval rule, one slip formula, and an in-flight floor, stated identically
  across SKILL.md, references, and the script footer; the interval is asymmetric at high
  confidence, flat-nominal otherwise.
- Crash-safe checkpoint ordering (done-marker before append); single `startedAt` write site;
  optional `pausedAt` pause accounting; STOP-race fix; ownership check; malformed-state
  fail-closed.
- Write-target symlink hardening (state file, `.gitignore`, resumed `artifactFile`); resume-path
  completeness (re-classify added tasks, artifactUrl gate, planFile realpath).
- README honesty pass: scoped-allowlist guidance (no broad `Bash(python3:*)`/`Bash(printf:*)`),
  uninstall + no-publish-mode sections, tag-to-tag update instructions, real citation for the
  4–7× estimation-error claim, and current post-rename trigger data.

Per-subtask executor visibility (carried from unreleased): the artifact shows which model ran
each subtask (versioned, e.g. "Haiku 4.5") and — only when explicitly set — its reasoning
effort; calibration.jsonl's `model` field records the subtask's executor, with an optional
`effort` key.

## v0.1.0 — 2026-07-16

First public release. Hardened after a five-persona adversarial review (state-machine
crash recovery, resume confirmation gate, calibration input whitelisting, continuous
shrinkage statistics, script-computed accuracy reports, per-subtask token display).
