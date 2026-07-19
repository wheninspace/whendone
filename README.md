# WhenDone

WhenDone gives a long, unattended Claude Code job a calibrated finish-time ETA and a live
progress page you can open anywhere — your phone included. Declared once at job start, kept
current by a background watcher; every finished subtask feeds the calibration that sharpens
the next job's estimate. The statistics are computed outside the model.

<img src="assets/progress-artifact.png" width="440" alt="WhenDone progress artifact: the DONE state of a real 14-subtask job — task table with actual-vs-estimate per subtask, a +51% overrun, per-task and job-level token counts">

*The actual DONE-state artifact from a real 14-subtask job — this repo's own round-2 hardening,
monitored by whendone on 2026-07-17 (full record in
[docs/test-log.md](docs/test-log.md#real-end-to-end-run-under-whendone-monitoring--2026-07-17)).
Rendered from [`assets/real-run-artifact.html`](assets/real-run-artifact.html) — open that file
to confirm the screenshot is faithful. A simpler constructed example lives in
[`assets/demo-artifact.html`](assets/demo-artifact.html), generated (not hand-edited) from
[`assets/demo-state.json`](assets/demo-state.json); regenerate it with
`python3 scripts/render_artifact.py assets/demo-state.json - assets/demo-artifact.html --now 2026-07-18T14:35:00+02:00 --summary -`.*

LLMs misjudge how long their own work will take — pre-task estimates overshoot actual duration
by 4–7× across 68 tasks and four model families (Garikaparthi, ["Can LLMs Perceive Time? An
Empirical Investigation"](https://arxiv.org/abs/2604.00010), arXiv:2604.00010). WhenDone closes
that loop empirically: every finished subtask logs its raw estimate against its actual duration,
per category, and a small stdlib Python script — not the model — turns that history into
per-category correction factors for the next job's ETA.

**Status:** v0.3.1 (2026-07-19), single author. Three job sources, each dogfooded live and
recorded in [docs/test-log.md](docs/test-log.md):

- **Source A** — lead/subagent runs (calibrated). Stage-3 live tailer/watcher run,
  render/publish/calibration end-to-end
  ([test-log](docs/test-log.md#stage-3--source-a-tailerwatcher-dogfood--monitoring-run--2026-07-18)).
- **Source B** — Workflow-engine runs, declared at launch (calibrated). Stage-4 dogfood, a live
  Workflow run end-to-end
  ([test-log](docs/test-log.md#stage-4--source-b-workflow-engine-dogfood--live-monitor-monitoring-run--2026-07-18)).
- **Source C** — plain solo / TodoWrite work (pace-based, never calibrated). Stage-5 pace-only
  live dogfood
  ([test-log](docs/test-log.md#stage-5--source-c-pace-only-live-dogfood--2026-07-19)).

Honest limits: both cross-session resume drills were run live on 2026-07-19 — a session
killed mid-job and resumed fresh, once per calibrated source — and each found and fixed a
real bug (a local-time display bug; a killed Workflow run's leftover record falsely
finalizing a job) — see [docs/test-log.md](docs/test-log.md). What remains untested is a
resume from a fresh clone at the tag (both drills ran in the dev tree). The 14-subtask hardening run behind the hero
image used the dev working tree (the installed skill dir is a symlink to this repo) on the
release branch, **not** a fresh clone at the tag; that run is headless, so the hero image is a
screenshot of *its own* DONE artifact
([`assets/real-run-artifact.html`](assets/real-run-artifact.html)), captured manually rather
than produced by the run. Design rationale and threat model in [docs/design.md](docs/design.md).

## Do you need this?

If you live in the terminal, maybe not. A statusline (e.g. claude-hud) and `/workflows`
already show you what's running, in the same window you're already watching — that's real,
and for a lot of jobs it's enough. WhenDone earns its overhead in one case: you want to **walk
away**. Two things a statusline can't give you: a *calibrated* finish-time ETA (not the model's
own guess, which overshoots — see below), and a link that **leaves the machine** — open the
live page from a VSCode extension, a desktop app, or your phone. If you never leave the terminal
and don't care about a calibrated ETA, you probably don't need whendone.

## Three sources

WhenDone attaches to a job in one of three ways, and which one it is decides whether the ETA is
calibrated:

| Source | What it is | ETA |
|---|---|---|
| **A** | Lead/subagent runs — a plan execution, a fan-out of subtasks | **Calibrated** — every finished subtask logs estimate vs. actual and corrects the next job |
| **B** | Workflow-engine runs, declared at launch | **Calibrated** — the same estimate→actual→correction loop, task list known up front |
| **C** | Plain solo / TodoWrite work | **Pace-based only** — visibly uncalibrated, and *never* calibrated: there's no per-subtask estimate to correct against |

## What you get

- **A progress artifact** — task table, actual vs. estimate per task, which model ran
  each subtask (full version, e.g. "Haiku 4.5", plus reasoning effort when explicitly set),
  total ETA with an honest interval, and token consumption per task and per job. The same
  account-scoped page throughout — one URL, republished at every subtask boundary. It's a
  claude.ai page (listed in your `claude.ai/code/artifacts` gallery), so it's on your desktop
  while you work and opens on any other device signed in to the same account — including your
  phone (on iOS via the browser: the mobile app's Artifacts tab doesn't list Code artifacts
  yet, so use the URL whendone posts in chat, or bookmark the gallery in Safari).
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

The mechanism IS context spend, so here it is. Figures below are a mix: the rows that say
so are real `tiktoken` `cl100k_base` measurements; the rest use a **4-characters-per-token
proxy** applied to the actual current file sizes in this repo (the note below the table says
which is which, with provenance):

| When | Cost |
|---|---|
| Every session, used or not | ~140-token trigger description |
| When it triggers (incl. each resume session) | ≈11,849 cl100k tokens — a real `tiktoken` `cl100k_base` measurement (Task 7 re-measure, 2026-07-19) of the trigger-to-first-publish read path: full SKILL.md file, frontmatter included (loads as the skill, no Read prefixes) + `references/source-a.md` + `references/file-formats.md` + `references/artifact-template.md` + `calibration-summary.md` allowance — see the provenance note below — + ~1.5–2k first artifact/state writes. The Resume mechanics now live in `references/resume.md`, OFF this path (read only when resuming), which is what dropped the figure ≈0.9k from the prior 12,313/12,552 measurements — every non-resume trigger keeps that saving (a later same-day fix pass added back +74 of coherence fixes on the path, then Tasks 2/4/5/7's on-path growth added a further +136 → 11,849). Raw content sum 11,849; as READ at trigger time — the three reference files arrive through the Read tool with line-number prefixes (~3.3–3.6 tok/line, +≈1.55–1.7k over 470 lines) — ≈13,400–13,541 (≈13.4–13.5k), which is the like-for-like number against the 14,000 budget (defined and previously measured prefix-inclusive: stage 4 recorded 13,821/14,000): ≈459–600 tokens (≈0.5–0.6k) to spare, not the 2.3k a raw-only comparison would suggest |
| Source-B addition to the trigger path | ~0 tokens marginal — `references/source-b.md` (2,210 cl100k tokens, re-measured 2026-07-19 — the file grew further with Task 5's on-path edits after the prior re-measurement of 2,067, itself after the B12 resume-drill fixes and the original stage-4 measurement of 1,810; method: `tiktoken` `cl100k_base`) is OFF the Source-A trigger path entirely; it's read only once Source B is detected, never as part of the trigger read above. Only a ~149-token pointer/event-row was added to SKILL.md + `file-formats.md` combined to describe it — already folded into the trigger-row figure above |
| Source-C addition to the trigger path | ~0 tokens marginal — `references/source-c.md` (921 cl100k tokens, re-measured 2026-07-19 after the review fix pass; method: `tiktoken` `cl100k_base`) is OFF the Source-A trigger path; it's read only once Source C is detected, never as part of the trigger read above |
| Per watcher wake (Sources A, B, and C — identical watcher path) | Two components, both MEASURED (`tiktoken cl100k_base` on the raw session transcripts of six real runs, stages 2–5 + both resume drills, re-verified 2026-07-19): one compact watcher event line (**~54–341 tokens, median ~120**), plus — when the harness re-injection fires — an echo of the rendered artifact HTML at **~0.6–0.8k tokens on small jobs, ~1.7–2.0k on a 13-task job** (scales with the task table; the debounce bounds it to at most one per wake, well under the 5k/wake threshold that would trigger D11's debounce-raising demotion). Mechanism, pinned by controlled experiment (2026-07-19, [docs/test-log.md](docs/test-log.md#per-wake-re-injection-mechanism--forensics--controlled-experiment-2026-07-19)): the harness registers a file when the model Writes/Edits it **or the Artifact tool publishes it** — script/Bash writes never register anything, and the render *location* is irrelevant (an earlier "renders to a non-watched path avoids it" note here was wrong about the mechanism — moving the file does not help). In the dogfood runs (VSCode extension, artifact page open and watched during the job) the echo fired at essentially every wake — those are the figures above. In a controlled run with **no artifact panel open**, watcher/background rewrites of the same registered file produced **zero** wake-turn echoes — leaving only the event line + one Artifact publish per wake. Practical reading: the walk-away scenario whendone is built for (phone/browser viewing, nothing open locally) sits at the low end; "actively watching the artifact inside the IDE buys the full echo" is the best-fit explanation for the dogfood-run echoes, not directly provable from inside a session (see the test-log entry) — the measured low-end/high-end split itself is real-run data on both sides either way. State edits, calibration append, and rendering happen in `tail_progress.py`/`render_artifact.py`, never through model context |
| Job end | ~0.8–1k tokens (final publish + full-table script run + calibration regen), scaling mildly with task count |
| Per resume (additionally) | + `references/resume.md` (1,281 cl100k tokens, re-measured 2026-07-19 after the resume token-sidecar note, up from 1,211 after the Source-B/C resume-routing fix — read ONLY when resuming) + ~0.9–1.5k for the full artifact rebuild (the old session's scratchpad file is gone). Accepted trade-off: a resume loads SKILL.md core *plus* resume.md — a small net rise on the rare resume path so that every non-resume trigger (fresh job, ETA question, accuracy report, stop) drops ~0.9k |
| After a compaction notice | one re-read of SKILL.md's Invariants section (≈930 chars ≈ 0.23k tokens) + the state file (size scales with task count — ~3–6 KB ≈ 0.75–1.55k tokens across this repo's recent runs) ≈ ~1–1.8k tokens — char/4 proxy. Recurs only when the harness issues a compaction notice, not on any fixed checkpoint schedule |

**Proxy vs. measurement:** the rows NOT marked as measured are `char_count / 4`, the same rule of
thumb used throughout this repo (see [docs/test-log.md](docs/test-log.md)), plus a rough
allowance for the Read tool's line-number prefixes (~3.3–3.6 tokens/line, measured with a real
cl100k tokenizer on 2026-07-17 — the earlier 1.4 figure undercounted ~2.4×) where a full file
is read.
No tokenizer was run for the job-end/resume/compaction rows below; a real tokenizer typically
runs a little denser on markdown/JSON, so those are floors, not ceilings. The per-watcher-wake
row above IS a real measurement (see its own provenance in the table), not a proxy. **Provenance
(trigger row):** a real `tiktoken` `cl100k_base` pass (consolidated-review fix pass, 2026-07-19) over
SKILL.md, `references/source-a.md`, `references/file-formats.md`, and
`references/artifact-template.md` as they ship in this repo (`references/resume.md` is NOT in
this set — it is read only when resuming), plus the calibration-summary figure — the actual
output size of `scripts/calibration_summary.py` (unmodified) run against a deterministic
synthetic 60-row, 8-category, 3-project fixture (3,377 chars — regeneration snippet in
[docs/design.md](docs/design.md#reproducing-the-readmes-synthetic-calibration-fixture)), not a
real user's calibration history. **Provenance (Source-B row):** `references/source-b.md`
measured standalone with the same `tiktoken` `cl100k_base` method, re-measured 2026-07-19
(originally 1,810 at stage 4, 2026-07-18; the B12 resume-drill fixes grew the file) —
2,067 tokens, under its own 2,500-token budget with 433 tokens to spare; the ~149-token
combined addition to SKILL.md + `file-formats.md` is the exact before/after delta measured for
those two files across the same edit. **Provenance (Source-C row):** `references/source-c.md`
measured standalone with the same `tiktoken` `cl100k_base` method on 2026-07-19 (re-measured
after the review fix pass; 920 at stage-5) —
921 tokens; it stays off the Source-A trigger path, read only once Source C is detected.
**Provenance (per-wake row):** the live figure comes from
a real L1 Monitor `--follow` watcher running for the whole stage-4 dogfood session — the first
stage with genuine live-Monitor-wake evidence (stage 3 only had a component estimate, no live
wake occurred that session); the 54-token event-line figure inside it is the stage-3 `cl100k`
measurement of a representative `tail_progress.py` `progress` line, still accurate since the
event schema didn't change. **Provenance (other rows):** the job-end token-script figures come
from calling `token_usage.py`'s `summarize()` against a synthetic state file and transcript —
confirming `--task N` (landed for C13) now returns a flat ~0.1–0.25k tokens per checkpoint
instead of growing with task count (pre-fix, this script's own output alone measured up to
~2.5–3k+ tokens by checkpoint 18–20 of a 20-task job). The resume-rebuild figure is grounded in
this repo's own `assets/demo-artifact.html` (3,232 bytes ≈ 808 tokens for the 3-task demo page,
+~65 tokens per additional task row). The compaction figure is `wc -c` on SKILL.md's Invariants
section (≈930 chars) plus a recent-run state file (~3–6 KB, scaling with task count), each divided by 4 — the only
re-read the declare-once/tail-thereafter watcher model still mandates on a compaction notice
(the pre-stage-3 per-checkpoint hand-editing that used to drive this row is gone). **What's
excluded:** the model's own task-execution reasoning tokens — only whendone's
own bookkeeping calls are counted. Current Claude Code deployments may also load the host's OWN
artifact-design skill before the first Artifact publish — outside whendone's control and not
counted here. **Why the trigger-path figure moved since the last measurement:** two movements.
(1) Between the stage-5 release (12,313) and this pass, on-path commits (local-time policy,
renderer display overhaul) grew `file-formats.md` and `artifact-template.md`, taking the trigger
path to 12,552 under an identical raw `tiktoken cl100k_base` sum. (2) This pass then extracted
the Resume mechanics from SKILL.md into `references/resume.md` (off the trigger path), dropping
the path to 11,639 — a 913-token saving every non-resume trigger now gets. (3) The same day's
consolidated pre-flip review then fixed stale/contradictory lines on the path (source
shipped-status, step-8 Source-C carve-out, pointer repairs), adding +74 → 11,713. (4) This task
(Task 7, honest raw-vs-as-read headroom) re-measured after Tasks 2/4/5 grew `source-a.md`,
`file-formats.md`, and `source-b.md` on- and off-path, plus this task's own SKILL.md "When not
to use" edit, moving the raw total to 11,849. **Method note (for
reproduction):** the figures here are a raw token sum of the file contents listed above (no
Read-tool line-number prefixes) plus the calibration-summary output; this reconstruction reads
the stage-5 commit at 12,385 vs the 12,313 recorded there — a ~0.6% additive reconstruction
variance that cancels in the 913-token delta. The path stays under the 14,000 budget as-read,
with ≈0.5–0.6k to spare (11,849 raw); see the provenance above for the exact breakdown.

Statistics never run in the model — calibration summaries and accuracy reports come from the
script. Worth it for jobs of ~6+ subtasks or an hour-plus that you actually walk away from.
Wrong tool for many-micro-subtask jobs; the skill itself declines jobs under ~6 subtasks /
~45 minutes — the trigger cost alone (≈11,849 tokens raw by a real cl100k tokenizer, 2026-07-19 measurement — see the Overhead table's trigger row; ≈13.4–13.5k as read) is hard to amortize below that.

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
| Progress artifact (task names, timings, token counts, model names) | claude.ai — default-private, shareable by link; a shared link shows all future updates. Names that look like a person/client/secret get a best-effort model judgment call before first publish and when the task list changes — not a guarantee, so review before sharing a link. Hard off-switch: the `.claude/whendone-no-publish` marker or `"publish": false` (see Usage) — then nothing is published at all — the tailer still renders locally (0600, for the in-chat table); nothing is published. HTML-escaping applied in code by the render script. Housekeeping: each job adds one page to your gallery (there is no delete API, so whendone can't clean up for you); old WhenDone pages — findable by their fixed "WhenDone progress monitor" subtitle — are safe to delete from the artifact's own menu |
| State file | `<project>/.claude/whendone-state.json` — gitignore enforced before first write |
| Calibration log + summary | `~/.claude/whendone-data/` — never leaves your machine, survives skill updates |
| Session transcript | read locally by the token script — usage numbers only, never content |

## Security

The six shipped scripts (`scripts/calibration_summary.py`, `scripts/token_usage.py`,
`scripts/append_calibration.py`, `scripts/render_artifact.py`, `scripts/tail_progress.py`,
`scripts/workflow_journal.py`) are stdlib-only Python with no
network access — the only thing that leaves your machine is the artifact you can see. Untrusted
strings (plan files, state files, log entries) are treated as data, never instructions, and are
HTML-escaped before entering the published page. Resuming from a found
state file requires your confirmation — a cloned repo can't silently start attacker-authored
work. The statistics script whitelists categories and sanitizes every string it re-emits, so a
poisoned log line can't plant instructions in the summary future sessions read. Install is
pinned to a release tag; updating a skill means updating instructions your agent will follow —
review the diff (see Update below). Full threat model: [docs/design.md](docs/design.md).

## Requirements

- Claude Code (CLI or desktop) signed in to claude.ai — the live artifact needs the Artifact
  tool. API-key-only / Bedrock / Vertex setups: whendone degrades to a progress table in
  chat. Cowork is expected to work but untested.
- Python 3 (`python3`, `python`, or `py`) for calibration statistics, token display, and
  artifact rendering. Without it these degrade off — whendone never does statistics in the model.
- It does not work in claude.ai chat — there's no file system there.

## First run — what it will ask you

Expect these prompts the first time: creating `~/.claude/whendone-data/` (outside the
project), adding the state file to your `.gitignore`, Bash `date` calls, a log append at each
checkpoint, and the artifact publish to claude.ai. To pre-approve the recurring ones for
unattended runs, be deliberate about what you allowlist in `.claude/settings.json`:

- `Bash(date:*)` is low-risk and fine to allowlist broadly.
- Do **not** allowlist `Bash(python3:*)` or `Bash(printf:*)`. Either pattern pre-approves
  arbitrary Python (or arbitrary shell tricks via `printf`) execution for every tool call in
  every project — far beyond whendone's own six scripts. Scope the rule to the exact path
  instead, e.g. `Bash(python3 ~/.claude/skills/whendone/scripts/*)`.
- Before approving even the scoped pattern, review what you're approving: the shipped test
  suites (`python3 ~/.claude/skills/whendone/scripts/test_calibration_summary.py`,
  `test_token_usage.py`, `test_append_calibration.py`, `test_render_artifact.py`,
  `test_tail_progress.py`, `test_workflow_journal.py`) are stdlib
  Python and quick to read — that's the review path for all six scripts, since the tests
  exercise the same code.

## Install

```bash
git clone --branch v0.3.1 --depth 1 https://github.com/WhenInSpace/whendone ~/.claude/skills/whendone
```

Windows PowerShell:

```powershell
git clone --branch v0.3.1 --depth 1 https://github.com/WhenInSpace/whendone "$env:USERPROFILE\.claude\skills\whendone"
```

**Windows honesty:** the install command above works. Running the skill — the declare-once
watcher, state-file writes, calibration logging — has only been exercised on macOS so far (see
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
rm <project>/.claude/whendone-tail.lock   # the watcher's single-instance lock, if present
```

Windows PowerShell equivalents:

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.claude\skills\whendone"
Remove-Item -Recurse -Force "$env:USERPROFILE\.claude\whendone-data"
Remove-Item "<project>\.claude\whendone-state.json"
Remove-Item "<project>\.claude\whendone-tail.lock"
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
