# WhenDone

Live progress and self-calibrating ETAs for long Claude Code runs — one page, updated at
every subtask boundary, statistics computed outside the model.

![WhenDone progress artifact: task table with actual-vs-estimate, ETA with interval, token counts](assets/progress-artifact.png)

LLMs misjudge how long their own work will take — [measured at 4–7× off](https://arxiv.org/html/2604.00010v1).
WhenDone closes that loop empirically: every finished subtask logs its raw estimate against
its actual duration, per category, and a small stdlib Python script — not the model — turns
that history into per-category correction factors for the next job's ETA.

**Status:** v0.1.0 (2026-07-16), single author. Trigger tests and an end-to-end dry run are
published unedited — failures included — in [docs/test-log.md](docs/test-log.md). Design
rationale and threat model in [docs/design.md](docs/design.md).

## What you get

- **A live progress artifact** — task table, actual vs. estimate per task, which model ran
  each subtask (full version, e.g. "Haiku 4.5", plus reasoning effort when explicitly set),
  total ETA with an honest interval, and token consumption per task and per job. One compact page, same URL all
  job long, updated at every subtask boundary. It's a claude.ai page scoped to your account
  (listed in your `claude.ai/code/artifacts` gallery), so it's on your desktop while you work
  and opens on any other device signed in to the same account — including your phone.
- **Self-calibrating ETAs, honest cold start** — first runs use a frozen default table at
  ±50 %; correction factors move from your first logged data point and carry the label
  low/medium/high confidence as history accumulates. No manual tuning, no cloud: the log is a
  local JSONL, the statistics are a 200-line stdlib script.
- **Graceful stop/resume — including session death** — "stop after the current subtask" or a
  `.claude/STOP` file stops cleanly; a crashed session resumes in a new one (interrupted
  subtasks are excluded from calibration, never guessed).
- **Token visibility** — output + fresh input vs. cache reads, measured from the session
  transcript by a script, shown in the artifact. No dollar figures: subscribers don't pay per
  token, so whendone won't pretend to know your bill.
- **Push notifications via Remote Control** — run Claude Code with Remote Control and the
  session mirrors to the Claude mobile app: whendone's pings (job done, job stopped, ETA
  slipping past 150 %) reach your phone and the artifact link rides along in the mirrored
  session. Best-effort — Claude decides when to push; needs the Claude app signed in with
  `/config` push enabled. Alerts fire at subtask boundaries — a single hung subtask cannot
  alert.

## Overhead — read this first if you pay per token or watch your quota

The mechanism IS context spend, so here it is. Figures below are estimated with a
**4-characters-per-token proxy** (no tokenizer was run — see the note below the table for
provenance), applied to the actual current file sizes in this repo:

| When | Cost |
|---|---|
| Every session, used or not | ~140-token trigger description |
| When it triggers (incl. each resume session) | ~15–16k tokens reads (skill + two references + summary) + ~1.5–2k first artifact/state writes |
| Per subtask boundary | ~1–1.5k tokens (surgical artifact + state edits, one log append, `--task N` token-script output) — flat regardless of task count |
| Job end | ~0.8–1k tokens (final publish + full-table script run + calibration regen), scaling mildly with task count |
| Per resume (additionally) | ~0.9–1.5k tokens (full artifact rebuild — the old session's scratchpad file is gone) |
| After context compaction | one re-read of the checkpoint protocol section + the state file (~4–5k tokens) |

**Proxy, not a tokenizer:** every number above is `char_count / 4`, the same rule of
thumb used throughout this repo (see [docs/test-log.md](docs/test-log.md)), plus a rough
allowance for the Read tool's line-number prefixes (~1.4 tokens/line) where a full file is read.
No tokenizer was run; a real tokenizer typically runs a little denser on markdown/JSON, so these
are floors, not ceilings. **Provenance:** SKILL.md (33,134 chars), `references/file-formats.md`
(13,300 chars), and `references/artifact-template.md` (8,167 chars) are `wc -c` on the files as
they ship in this repo; `calibration-summary.md` (2,825 chars) is the actual output of
`scripts/calibration_summary.py` (unmodified) run against a synthetic 60-row, 8-category
calibration log. The per-boundary and job-end token-script figures come from calling
`token_usage.py`'s `summarize()` against a synthetic state file and transcript — confirming
`--task N` (landed for C13) now returns a flat ~0.1–0.25k tokens per checkpoint instead of
growing with task count (pre-fix, this script's own output alone measured up to ~2.5–3k+ tokens
by checkpoint 18–20 of a 20-task job). The resume-rebuild figure is grounded in this repo's own
`assets/demo-artifact.html` (3,519 chars ≈ 880 tokens for a 6-task page, +~65 tokens per
additional task row). The compaction figure is `wc -c`/`wc -l` on SKILL.md's Checkpoint protocol
section (lines 128–277) plus a typical state file re-read. **What's excluded:** the model's own
task-execution reasoning tokens — only whendone's own bookkeeping calls are counted. Current
Claude Code deployments may also load the host's OWN artifact-design skill before the first
Artifact publish — outside whendone's control and not counted here. **Why the trigger cost more
than doubled since the last measurement:** SKILL.md itself has grown from ~16.9 KB to 33.1 KB
across this hardening round (ownership checks, injection-safe logging, pause accounting, and
similar fixes) — the growth is in the file, not in the estimate.

Statistics never run in the model — calibration summaries and accuracy reports come from the
script. Worth it for jobs of ~6+ subtasks or an hour-plus that you actually walk away from.
Wrong tool for many-micro-subtask jobs; the skill itself declines jobs under ~6 subtasks /
~45 minutes — the trigger cost alone (~17–18k tokens) is hard to amortize below that.

## Usage — say "run with whendone"

Explicit invocation is the reliable path. Auto-triggering exists but is best-effort — in our
own published tests it loses to plan-execution skills that grab the same moment
([docs/test-log.md](docs/test-log.md)). If you run plan executions routinely, add one line to
your CLAUDE.md: `When executing a plan of 6+ tasks, also invoke the whendone skill to
monitor progress.`

- "run with whendone" / "run without whendone" — force it on or off for this job
- "stop after the current subtask" — graceful stop (or create `.claude/STOP` in the project root)
- "resume the job" — pick a paused or crashed job back up, new session included
- "how accurate is whendone?" — a script-computed accuracy report from your own history

## What it touches

| Data | Where it goes |
|---|---|
| Progress artifact (task names, timings, token counts, model names) | claude.ai — default-private, shareable by link; a shared link shows all future updates. Names that look like a person/client/secret get a best-effort model judgment call before first publish and when the task list changes — not a guarantee, so review before sharing a link |
| State file | `<project>/.claude/whendone-state.json` — gitignore enforced before first write |
| Calibration log + summary | `~/.claude/whendone-data/` — never leaves your machine, survives skill updates |
| Session transcript | read locally by the token script — usage numbers only, never content |

## Security

Untrusted strings (plan files, state files, log entries) are treated as data, never
instructions, and are HTML-escaped before entering the published page. Resuming from a found
state file requires your confirmation — a cloned repo can't silently start attacker-authored
work. The statistics script whitelists categories and sanitizes every string it re-emits, so a
poisoned log line can't plant instructions in the summary future sessions read. Install is
pinned to a release tag; updating a skill means updating instructions your agent will follow —
review the diff (see Update below). Full threat model: [docs/design.md](docs/design.md).

## Requirements

- Claude Code (CLI or desktop) signed in to claude.ai — the live artifact needs the Artifact
  tool. API-key-only / Bedrock / Vertex setups: whendone degrades to a progress table in
  chat. Cowork is expected to work but untested.
- Python 3 (`python3`, `python`, or `py`) for calibration statistics and token display.
  Without it both degrade off — whendone never does statistics in the model.
- It does not work in claude.ai chat — there's no file system there.

## First run — what it will ask you

Expect these prompts the first time: creating `~/.claude/whendone-data/` (outside the
project), adding the state file to your `.gitignore`, Bash `date` calls, a log append at each
checkpoint, and the artifact publish to claude.ai. To pre-approve the recurring ones for
unattended runs, allowlist in `.claude/settings.json`: `Bash(date:*)` and `Bash(python3:*)` for
the three shipped scripts (including the calibration append helper) if you've reviewed them.

## Install

```bash
git clone --branch v0.1.0 --depth 1 https://github.com/WhenInSpace/whendone ~/.claude/skills/whendone
```

Windows PowerShell:

```powershell
git clone --branch v0.1.0 --depth 1 https://github.com/WhenInSpace/whendone "$env:USERPROFILE\.claude\skills\whendone"
```

## Update

A skill update is an instruction update for your agent — look before you merge:

```bash
cd ~/.claude/skills/whendone
git fetch --tags
git log --oneline HEAD..origin/main
git diff HEAD origin/main -- SKILL.md scripts/
git merge origin/main   # when the diff looks right
```

Calibration data lives outside the skill directory and survives updates untouched.

## How the calibration works

Raw estimates come from a frozen default table FIRST; only then is the per-category correction
factor applied — that ordering means the estimate never sees the factor (anchoring
protection). Completed subtasks feed a 20 %-winsorized mean of actual/estimate ratios per
category, shrunk toward 1.0 by `(n·observed + 5)/(n + 5)` — factors move from the first data
point, converge to your observed reality, and never jump. Parallel subtasks are never pooled
(overlapping wall-clock lies); each parallel group logs one synthetic row that validates the
max-of-group ETA rule instead. Full rationale: [docs/design.md](docs/design.md).

## Alternatives

[pocket-watch](https://github.com/MiguelDotL/pocket-watch) calibrates conversational estimates
via hooks but doesn't monitor runs; [task-progress-bar](https://github.com/PRAFULREDDYM/task-progress-bar)
renders a terminal bar without recording estimates; [agent-estimation](https://github.com/ZhangHanDong/agent-estimation)
estimates in tool-call rounds without logging actuals. Usage dashboards show telemetry, not
task ETAs. None of them close the estimate→actual→correction loop for agent runs — that gap is
why whendone exists ([anthropics/claude-code#24666](https://github.com/anthropics/claude-code/issues/24666)).

## Credits

Ideas adapted from three MIT-licensed projects: [pocket-watch](https://github.com/MiguelDotL/pocket-watch)
(shrinkage-toward-prior, anchoring protection), [task-progress-bar](https://github.com/PRAFULREDDYM/task-progress-bar)
(compute outside the model), [agent-estimation](https://github.com/ZhangHanDong/agent-estimation)
(max-of-parallel-group ETA). WhenDone deviates deliberately where noted in
[docs/design.md](docs/design.md).

## License

MIT © WhenInSpace AB
