# Pacekeeper

Self-calibrating ETAs and live progress for long Claude Code runs.

Every finished subtask logs its estimate against its actual duration, per category. That
correction factor sharpens every future estimate. LLMs are [badly miscalibrated on how long work
will take](https://arxiv.org/html/2604.00010v1) — Pacekeeper closes that loop empirically,
instead of asking the model to just guess better.

## What you get

- **A live progress artifact** — task list, per-task status, and a total ETA with an
  uncertainty interval, updated at every subtask boundary. Check it and see whether the job will
  finish before you leave.
- **Self-improving calibration** — no manual tuning; the correction factor per category updates
  itself from logged history.
- **Graceful stop and resume** — say "stop after the current subtask" or drop a `.claude/STOP`
  file; resume later, in a new session if needed, and the job picks up where it left off.
- **Best-effort push notifications** — job done, job stopped, or the ETA slipping badly.

## Requirements

Claude Code (CLI or desktop) or Cowork. It does not work in claude.ai chat — there's no file
system to write the state file or the artifact to. `python3` is recommended for the calibration
statistics; without it, the skill falls back to computing them by hand.

## Install

```bash
git clone https://github.com/WhenInSpace/pacekeeper ~/.claude/skills/pacekeeper
```

Windows PowerShell:

```powershell
git clone https://github.com/WhenInSpace/pacekeeper "$env:USERPROFILE\.claude\skills\pacekeeper"
```

It activates automatically the next session a long job starts — no configuration needed.
Calibration data is per-user, stored in `~/.claude/pacekeeper-data/` (created on first run). It's
never shared and never committed to any project repo.

## Update

```bash
git -C ~/.claude/skills/pacekeeper pull
```

Calibration data lives outside the skill directory, so it survives updates untouched.

## Usage

Normally there's nothing to do — it triggers on its own once a job looks long enough. You can
also say:

- "run with pacekeeper" / "run without pacekeeper" — force it on or off for this job
- "stop after the current subtask" — graceful stop
- "resume the job" — pick a paused job back up
- "how accurate is pacekeeper?" — a calibration accuracy report

Push notifications only reach your phone if Remote Control is active and push is enabled in
`/config`; otherwise delivery is uncertain and the artifact says so honestly.

## How the calibration works

Short version: every subtask gets a raw estimate from a frozen default table first, then a
per-category correction factor is applied — in that order, so the estimate itself never sees the
factor (anchoring protection). Completed subtasks feed a 20%-trimmed median per category, blended
with a prior in phases as data accumulates, with a confidence label attached. Full rationale in
[`docs/design.md`](docs/design.md).

## Privacy note

The progress artifact is published to claude.ai. It's default-private but shareable by link. The
skill flags job or task names that look sensitive (client names, personal names, confidential
project details) before the first publish, so you can rename or approve first. The calibration
log itself never leaves your machine.

## Credits

Ideas adapted from three MIT-licensed projects:

- [pocket-watch](https://github.com/MiguelDotL/pocket-watch) — phased Bayesian blending, trimmed
  median, anchoring protection
- [task-progress-bar](https://github.com/PRAFULREDDYM/task-progress-bar) — compute outside the
  model
- [agent-estimation](https://github.com/ZhangHanDong/agent-estimation) — max-of-parallel-group
  ETA

## License

MIT © WhenInSpace AB
