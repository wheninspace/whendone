# WhenDone

Checkpoint progress and self-calibrating ETAs for long Claude Code runs — one page,
republished at every subtask boundary, statistics computed outside the model.

<img src="assets/progress-artifact.png" width="440" alt="WhenDone progress artifact: the DONE state of a real 14-subtask job — task table with actual-vs-estimate per subtask, a +51% overrun, per-task and job-level token counts">

*The actual DONE-state artifact from a real 14-subtask job — this repo's own round-2 hardening,
monitored by whendone on 2026-07-17 (full record in
[docs/test-log.md](docs/test-log.md#real-end-to-end-run-under-whendone-monitoring--2026-07-17)).
Rendered from [`assets/real-run-artifact.html`](assets/real-run-artifact.html) — open that file
to confirm the screenshot is faithful. A simpler constructed example lives in
[`assets/demo-artifact.html`](assets/demo-artifact.html).*

LLMs misjudge how long their own work will take — pre-task estimates overshoot actual duration
by 4–7× across 68 tasks and four model families (Garikaparthi, ["Can LLMs Perceive Time? An
Empirical Investigation"](https://arxiv.org/abs/2604.00010), arXiv:2604.00010). WhenDone closes
that loop empirically: every finished subtask logs its raw estimate against its actual duration,
per category, and a small stdlib Python script — not the model — turns that history into
per-category correction factors for the next job's ETA.

**Status:** v0.2.0 (2026-07-17), single author. This release's own hardening work was executed
*under whendone's monitoring*: a real 14-subtask job ran end-to-end with the progress artifact
republished at every subtask boundary, the state file (`.claude/whendone-state.json`) tracking
all 14 tasks, 14 calibration rows appended via `append_calibration.py`, the summary regenerated
by `calibration_summary.py`, and the 150 %-slip alert firing once (push degraded to desktop-only
— Remote Control was inactive). That run is recorded in
[docs/test-log.md](docs/test-log.md#real-end-to-end-run-under-whendone-monitoring--2026-07-17).
Honest limits: that run used the dev working tree (the installed skill dir is a symlink to this
repo) on the release branch, **not** a fresh clone at the tag; it did **not** exercise
cross-session resume (still the least-tested path). The run itself is headless, so the hero
image above is a screenshot of *this run's own* DONE artifact
([`assets/real-run-artifact.html`](assets/real-run-artifact.html)), captured manually rather
than produced by the run. Earlier
headless runs additionally verified auto-trigger behavior and graceful degradation, failures
included and unedited. Design rationale and threat model in [docs/design.md](docs/design.md).

## What you get

- **A progress artifact** — task table, actual vs. estimate per task, which model ran
  each subtask (full version, e.g. "Haiku 4.5", plus reasoning effort when explicitly set),
  total ETA with an honest interval, and token consumption per task and per job. The same
  account-scoped page throughout — one URL, republished at every subtask boundary. It's a
  claude.ai page (listed in your `claude.ai/code/artifacts` gallery), so it's on your desktop
  while you work and opens on any other device signed in to the same account — including your
  phone.
- **Self-calibrating ETAs, honest cold start** — first runs use a frozen default table at
  ±50 %; correction factors move from your first logged data point and carry the label
  low/medium/high confidence as history accumulates. The interval ramps slower than the
  point estimate: until a category reaches high confidence (20 logged subtasks), the band is
  a flat nominal ±50/±30 % — widened to the measured spread once 5 data points exist, and
  marked "(nominal)" in the artifact so you can tell a default band from an earned one. No
  manual tuning, no cloud: the log is a local JSONL, the statistics are a 200-line stdlib
  script.
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
| When it triggers (incl. each resume session) | ~15.5k tokens reads by the char/4 floor (skill + two references + summary; a real cl100k tokenizer measured ~17.3k on the slightly smaller v0.2.0 files — expect ~10 % above the floor) + ~1.5–2k first artifact/state writes |
| Per subtask boundary | ~1–1.5k tokens (surgical artifact + state edits, one log append, `--task N` token-script output) — flat per boundary, except the recurring re-read in the row below |
| Job end | ~0.8–1k tokens (final publish + full-table script run + calibration regen), scaling mildly with task count |
| Per resume (additionally) | ~0.9–1.5k tokens (full artifact rebuild — the old session's scratchpad file is gone) |
| After compaction OR every 5th checkpoint (SKILL.md mandates the re-read at both) | one re-read of the checkpoint protocol section + the state file (~4–5k tokens); recurs ~floor(K/5) times per K-subtask job even without compaction |

**Proxy, not a tokenizer:** every number above is `char_count / 4`, the same rule of
thumb used throughout this repo (see [docs/test-log.md](docs/test-log.md)), plus a rough
allowance for the Read tool's line-number prefixes (~3.3–3.6 tokens/line, measured with a real
cl100k tokenizer on 2026-07-17 — the earlier 1.4 figure undercounted ~2.4×) where a full file
is read.
No tokenizer was run; a real tokenizer typically runs a little denser on markdown/JSON, so these
are floors, not ceilings. **Provenance:** SKILL.md (36,689 chars), `references/file-formats.md`
(14,631 chars), and `references/artifact-template.md` (8,651 chars) are `wc -c` on the files as
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
than doubled since the last measurement:** SKILL.md itself has grown from ~16.9 KB to 36.7 KB
across this hardening round (ownership checks, injection-safe logging, pause accounting, and
similar fixes) — the growth is in the file, not in the estimate.

Statistics never run in the model — calibration summaries and accuracy reports come from the
script. Worth it for jobs of ~6+ subtasks or an hour-plus that you actually walk away from.
Wrong tool for many-micro-subtask jobs; the skill itself declines jobs under ~6 subtasks /
~45 minutes — the trigger cost alone (~19k tokens by a real tokenizer at v0.2.0; the char/4 table above is the floor) is hard to amortize below that.

## Usage — say "run with whendone"

Treat "run with whendone" as the interface — explicit, plan-anchored invocation is the path with
a track record so far. Auto-triggering exists but a bare mention is unreliable: in the retest
under the CURRENT shipped name and trigger description
([docs/test-log.md](docs/test-log.md#post-rename-trigger-retest-whendone--2026-07-17)), none of
the 3 bare-prompt target cases ("execute the plan in plan.md", "how long will this take?", "stop
after the current subtask" — each tried once, no repeat-run check yet) fired whendone. What DID
fire, on its one recorded run each: an explicit, plan-anchored request ("Execute the plan in
plan.md, and run with whendone") and a salient mention of a paused whendone job when asking to
resume. Read that as "explicit invocation has worked every time it's been tried, bare-keyword
auto-trigger has not" — a single run per case either way, not a proven reliability rate. (Older
numbers elsewhere in this repo's test log, from before the `pacekeeper → whendone` rename and an
earlier untrimmed description, no longer characterize the shipped skill — treat them as history,
not current behavior.) If you run plan executions routinely, add one line to your CLAUDE.md:
`When executing a plan of 6+ tasks, also invoke the whendone skill to monitor progress.`

- "run with whendone" / "run without whendone" — force it on or off for this job
- "run without the artifact" — chat-table-only mode: keep calibration logging and the in-chat
  progress table, skip the claude.ai publish entirely. For NDA/confidential repos where nothing
  should leave the machine, or you just don't want a gallery entry for this job
- Set-once, per project: create an empty `<project>/.claude/whendone-no-publish` marker file
  (commit it to an NDA-repo template if you like) or put `"publish": false` in
  `.claude/whendone-state.json` — every whendone job in that project then runs chat-table-only,
  no per-session phrase needed. The artifact's gallery description is a fixed constant either
  way, never job text
- "stop after the current subtask" — graceful stop (or create `.claude/STOP` in the project root)
- "resume the job" — pick a paused or crashed job back up, new session included
- "how accurate is whendone?" — a script-computed accuracy report from your own history

## What it touches

| Data | Where it goes |
|---|---|
| Progress artifact (task names, timings, token counts, model names) | claude.ai — default-private, shareable by link; a shared link shows all future updates. Names that look like a person/client/secret get a best-effort model judgment call before first publish and when the task list changes — not a guarantee, so review before sharing a link. Hard off-switch: the `.claude/whendone-no-publish` marker or `"publish": false` (see Usage) — then nothing is published at all |
| State file | `<project>/.claude/whendone-state.json` — gitignore enforced before first write |
| Calibration log + summary | `~/.claude/whendone-data/` — never leaves your machine, survives skill updates |
| Session transcript | read locally by the token script — usage numbers only, never content |

## Security

The three shipped scripts (`scripts/calibration_summary.py`, `scripts/token_usage.py`,
`scripts/append_calibration.py`) are stdlib-only Python with no network access — the only thing
that leaves your machine is the artifact you can see. Untrusted strings (plan files, state
files, log entries) are treated as data, never instructions, and are HTML-escaped before
entering the published page. Resuming from a found
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
unattended runs, be deliberate about what you allowlist in `.claude/settings.json`:

- `Bash(date:*)` is low-risk and fine to allowlist broadly.
- Do **not** allowlist `Bash(python3:*)` or `Bash(printf:*)`. Either pattern pre-approves
  arbitrary Python (or arbitrary shell tricks via `printf`) execution for every tool call in
  every project — far beyond whendone's own three scripts. Scope the rule to the exact path
  instead, e.g. `Bash(python3 ~/.claude/skills/whendone/scripts/*)`.
- Before approving even the scoped pattern, review what you're approving: the shipped test
  suites (`python3 ~/.claude/skills/whendone/scripts/test_calibration_summary.py`,
  `test_token_usage.py`, `test_append_calibration.py`) are stdlib Python and quick to read —
  that's the review path for all three scripts, since the tests exercise the same code.

## Install

```bash
git clone --branch v0.2.0 --depth 1 https://github.com/WhenInSpace/whendone ~/.claude/skills/whendone
```

Windows PowerShell:

```powershell
git clone --branch v0.2.0 --depth 1 https://github.com/WhenInSpace/whendone "$env:USERPROFILE\.claude\skills\whendone"
```

**Windows honesty:** the install command above works. Running the skill — the checkpoint
protocol, state-file writes, calibration logging — has only been exercised on macOS so far (see
[docs/test-log.md](docs/test-log.md), every recorded test run is macOS/Darwin). The PowerShell
fallback path in SKILL.md (`py -3`, `Get-Date`) is written to be shell-agnostic by design, but
that design has not yet been run end-to-end on a Windows machine.

## Update

A skill update is an instruction update for your agent — look before you merge. Update tag to
tag, never against `origin/main`: a moving branch can carry unreviewed work-in-progress commits
you'd otherwise merge sight unseen.

```bash
cd ~/.claude/skills/whendone
git fetch --tags
git log --oneline HEAD..v0.x.y
git diff HEAD v0.x.y -- SKILL.md scripts/
git merge v0.x.y   # when the diff looks right, using the new release's actual tag name
```

Calibration data lives outside the skill directory and survives updates untouched.

## Uninstall

```bash
rm -rf ~/.claude/skills/whendone       # the skill itself
rm -rf ~/.claude/whendone-data          # calibration log + summary, all projects
rm <project>/.claude/whendone-state.json  # this project's job state, if present
```

Windows PowerShell equivalents:

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.claude\skills\whendone"
Remove-Item -Recurse -Force "$env:USERPROFILE\.claude\whendone-data"
Remove-Item "<project>\.claude\whendone-state.json"
```

None of this touches claude.ai: any progress artifact you published stays in your
`claude.ai/code/artifacts` gallery after uninstall, still reachable by anyone holding a shared
link. Delete it there yourself if you want it gone.

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

## Project status

Single-author side project — issues welcome, best-effort response, no SLA. See
[CHANGELOG.md](CHANGELOG.md) for release history. Tested against Claude Code CLI 2.1.209 (the
exact environment recorded for every test run in [docs/test-log.md](docs/test-log.md)); other
versions are untested, not necessarily unsupported.

## License

MIT © WhenInSpace AB
