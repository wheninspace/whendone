# Changelog

## Unreleased

- Per-subtask executor visibility: the artifact shows which model ran each subtask
  (versioned, e.g. "Haiku 4.5") and — only when explicitly set — its reasoning effort.
  token_usage.py reports the distinct models per task window from the transcript;
  calibration.jsonl's `model` field now records the subtask's executor (not the lead
  session's model), with an optional `effort` key.

## v0.1.0 — 2026-07-16

First public release. Hardened after a five-persona adversarial review (state-machine
crash recovery, resume confirmation gate, calibration input whitelisting, continuous
shrinkage statistics, script-computed accuracy reports, per-subtask token display).
