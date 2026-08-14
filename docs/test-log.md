# WhenDone skill test log

> **Naming note (2026-07-17):** entries dated 2026-07-16 and earlier were recorded when this
> skill was named `pacekeeper` (renamed to `whendone` on 2026-07-17). Their `pacekeeper` /
> "run with pacekeeper" wording is preserved as an accurate record of what was run. Trigger
> behaviour was re-validated under the new name — see "Post-rename trigger retest" below.

Date: 2026-07-16

## Environment

- Claude Code CLI 2.1.209 (`~/.local/bin/claude`), macOS (Darwin 25.5.0)
- Headless harness: `claude -p ... --output-format stream-json --verbose`, run from a scratch
  project directory outside the pacekeeper repo
- Models used: `claude-haiku-4-5-20251001` for trigger tests (cases a–e), `claude-sonnet-5` for
  the end-to-end dry run
- Skill under test: `pacekeeper`, installed at `~/.claude/skills/pacekeeper` (symlinked to
  the local development repo)
- Confound present for the whole session: the older `progress-monitor` skill remained installed
  at `~/.claude/skills/progress-monitor` (Swedish-flavored trigger description) — see caveats

## Installed-location smoke test

Already run by the controller prior to this session: `scripts/calibration_summary.py` invoked
against a fixture `calibration.jsonl` from its installed location — exit 0, output matched the
expected fixture summary. Not re-run in this session.

## Trigger tests (cases a–e)

Scratch project: `/private/tmp/.../scratchpad/pk-test-project`, git-initialized, `.gitignore`
containing `node_modules/`, `plan.md` with a 5-task checkbox plan (create `file1.txt`…
`file5.txt` with fixed contents).

First pass ran all five cases in parallel against the same scratch directory and was discarded:
one case (d) actually renamed `plan.md` → `PLAN.md` mid-run, which contaminated case a's read of
the plan file. All five cases were re-run sequentially, each preceded by `git reset --hard` +
`git clean -fd`, to get clean, independent results. Table below reflects the clean sequential
run (raw logs for both passes kept in the scratch dir: `case-*.jsonl` and
`old-parallel-run/case-*.jsonl`).

| Case | Prompt (summary) | Expected | Result | Evidence |
|---|---|---|---|---|
| a | "Execute the plan in plan.md" | pacekeeper triggers | FAIL | Model explicitly invoked `Skill` with `{"skill":"superpowers:executing-plans"}`, not pacekeeper and not progress-monitor. Proceeded to build a TodoWrite list and start executing directly. |
| b | "I need you to refactor five modules, run tests, and update docs. How long will this take?" | pacekeeper triggers | FAIL | No `Skill` tool call at all (num_turns 1). Model answered directly, asking clarifying questions about scope — never considered any skill. |
| c | "stop after the current subtask" | pacekeeper triggers (stop procedure) | FAIL | Model ignored the "stop" framing entirely (no prior job/state existed) and instead invoked `Skill` with `superpowers:executing-plans`, then started executing plan.md tasks. Neither pacekeeper's stop procedure nor progress-monitor triggered. |
| d | "Fix the typo in plan.md and rename it to PLAN.md" (small 2-step task) | no trigger | PASS | No `Skill` tool call. Model read plan.md, found no typo, asked for clarification. Correctly did not invoke any long-job skill for this small task. |
| e | "Execute the plan in plan.md, but run without pacekeeper" | skill not used / explicitly declined | PASS | No `Skill` tool call. Model went straight to `Write` calls for the five files (all denied by the harness's tool allowlist, since only `Skill` was permitted) — i.e., pacekeeper was not invoked, consistent with the instruction, though the model never explicitly acknowledged declining it in text. |

Overall: 2 PASS (d, e), 3 FAIL (a, b, c). No case triggered `progress-monitor` — the
Swedish-flavored old skill did not compete for any of these English prompts. The actual
competing skill in the plan-execution cases (a, c) was `superpowers:executing-plans`, not
progress-monitor, so these are recorded as FAIL rather than INCONCLUSIVE per the interpretation
rule (that rule was scoped specifically to a progress-monitor conflict). Case b is a genuine
miss: a direct "how long will this take" ETA question never invoked any skill under Haiku with
only `Skill` in the tool allowlist and a 4-turn budget.

## End-to-end dry run

Command: `claude -p "Execute the plan in plan.md using the pacekeeper skill." --model
claude-sonnet-5 --max-turns 60 --output-format stream-json --verbose --permission-mode
acceptEdits --allowedTools "Skill,Read,Write,Edit,Bash(python3:*),Bash(date:*),Bash(mkdir:*),
Bash(cp:*),Bash(cat:*),Bash(ls:*),Bash(echo:*),Bash(git:*)"` (note: `Artifact` was not in the
allowlist, per the original harness spec — artifact publishing was expected to be
unavailable/gracefully skipped in this run).

`~/.claude/pacekeeper-data/` did not exist before this run (confirmed).

The session did invoke pacekeeper this time (`Skill` call with `{"skill":"pacekeeper",
"args":"Execute the plan in plan.md"}`) and followed the SKILL.md protocol correctly in order:
checked the plan, started first-run setup (`mkdir ~/.claude/pacekeeper-data`, `cp
calibration-summary.md`), but every write under `~/.claude/` and every attempt to create a
project `.claude/` directory was auto-denied by the harness as a "sensitive path" — independent
of the `Bash(mkdir:*)` / `Bash(cp:*)` allowlist entries. The model recognized this, said in text
that it would "degrade gracefully per the skill's error-handling table," and chose to proceed
with the raw task (creating the five files) without pacekeeper's state/calibration machinery,
explicitly flagging in text that state file and calibration log could not be persisted in this
sandbox.

The transcript then ends abruptly: all five files were created and verified (content and byte
counts confirmed correct: `alpha`/`bravo`/`charlie`/`delta`/`echo`), but there is no final
`"type":"result"` event in the stream-json output, and no `claude -p` process for this job was
still running when checked afterward. `plan.md`'s checkboxes were never updated (still all
unchecked) and `.gitignore` is unchanged. This indicates the session was cut off (not a clean
job-end) before reaching the checkpoint/job-end steps, rather than the skill failing to run
them.

### Verification checklist (read from filesystem, not transcript)

1. `~/.claude/pacekeeper-data/` created with `calibration-summary.md` copied — **NOT achieved**.
   Directory still does not exist after the run; skill attempted the copy correctly but was
   blocked by the harness's sensitive-path auto-deny before it could complete.
2. `calibration.jsonl` with English fields (date/project/job/category/estimateMin/actualMin/
   model/client) — **NOT created**. Same blocker as (1); the job never logged a subtask.
3. Scratch project's `.claude/pacekeeper-state.json` with jobId, per-task raw/estimate minutes,
   statuses — **NOT created**. Directory creation for `.claude/` in the scratch project was also
   auto-denied ("sensitive file").
4. State file path added to scratch project's `.gitignore` — **N/A / not achieved** (no state
   file was ever created, and `.gitignore` is unchanged: still only `node_modules/`).
5. Artifact published or gracefully skipped — **gracefully skipped by design**: `Artifact` was
   deliberately excluded from `--allowedTools` for this run, and the transcript shows no attempt
   to call it, consistent with the tool being unavailable in this harness.
6. Calibration summary regenerated at job end (header no longer says "never") — **not
   verifiable**: the job never reached job-end; no regeneration call appears in the transcript,
   and `~/.claude/pacekeeper-data/calibration-summary.md` does not exist to check.

Net: pacekeeper triggered correctly for the explicit "using the pacekeeper skill" prompt and
followed its own error-handling principle ("visibility must never block the work") by continuing
the actual task instead of stalling on the blocked setup step — a good sign for the skill's
degrade-gracefully design. But none of the persistent-state/calibration/artifact machinery could
be verified working end-to-end in this environment, because the sandbox denies writes under
`~/.claude/` and project `.claude/` directories regardless of the Bash allowlist, and the session
terminated before a clean job-end regardless.

## Caveats

- **progress-monitor confound**: the older Swedish-flavored `progress-monitor` skill remained
  installed throughout (disabling it was not permitted). It did not trigger in any of the five
  cases tested — all prompts were in English and progress-monitor's trigger phrases are
  Swedish-specific. This caveat did not end up affecting the observed results, but the skill was
  present for the entire test.
- **Contamination in first parallel pass**: running all five trigger cases concurrently against
  one shared scratch directory caused a real race (case d's rename of `plan.md` mid-run affected
  case a). Fixed by re-running all cases sequentially with a git reset between each; the table
  above reflects only the clean sequential results.
- **Headless vs. interactive differences**: the trigger-test harness restricted `--allowedTools`
  to `Skill` only and used `--max-turns 4`, which may itself suppress skill invocation (a model
  that intends to do direct tool work first, gets denied, and never revisits the Skill tool). Real
  interactive sessions with full tool access may behave differently — these results characterize
  headless/Haiku behavior specifically, not necessarily interactive/Sonnet behavior.
- **Sandbox permission blocking**: this nested Claude Code session runs inside an outer
  sandboxed session that auto-denies writes to `~/.claude/` and project `.claude/` paths as
  "sensitive files," regardless of the inner session's `--allowedTools`. This blocked full
  verification of pacekeeper's state-file, calibration-log, and gitignore-append behavior. This
  is an artifact of the nested-testing environment, not a demonstrated defect in the skill
  running standalone.
- **E2E session ended prematurely**: the end-to-end run's transcript stops mid-task with no
  final `"type":"result"` event and no live process afterward — the job was not observed to
  reach a clean end within the ~10 minute window before this was checked. Job-end behavior
  (final artifact update, calibration summary regeneration, state file set to `"done"`) is
  therefore unverified, not merely "not applicable."
- **Push notifications**: not verifiable headless in this environment (no PushNotification tool
  available / no Remote Control session active); not tested.
- **Artifact publishing**: not tested in the E2E run because `Artifact` was excluded from the
  allowlist by design (per the harness spec used); whether pacekeeper's artifact
  publish/republish logic itself works was not exercised in this test pass.

## Retest after description fix

Commit `f237804` amended pacekeeper's SKILL.md trigger description to add: "Pacekeeper is a
companion, not an executor — when a plan-execution or orchestration skill runs the job, invoke
pacekeeper IN ADDITION to monitor it." Since `~/.claude/skills/pacekeeper` is a symlink to this
repo, the installed skill picked up the change immediately (confirmed via `grep` on the
installed SKILL.md before retesting).

Retested only the three original failures (a, b, c), sequentially, each preceded by
`git reset --hard` + `git clean -fd` in the scratch project. **Two harness variables changed at
once** for this retest, not just the description: model was switched from
`claude-haiku-4-5-20251001` to `claude-sonnet-5` (a more realistic interactive model per the
coordinator's request), and `--max-turns` was raised from 4 to 6. Attribution between "the
description fix worked" and "a stronger model with more turns worked" is therefore **partial,
not clean** — these results should not be read as isolated proof the description change alone
fixed triggering.

| Case | Prompt (summary) | New pass rule | Result | Evidence |
|---|---|---|---|---|
| a | "Execute the plan in plan.md" | pacekeeper alone, or pacekeeper + an execution skill, both PASS | FAIL | Model invoked `Skill` with only `{"skill":"superpowers:executing-plans"}` — pacekeeper was never invoked, alone or alongside it. |
| b | "...how long will this take?" | pacekeeper triggers | FAIL | No `Skill` tool call (num_turns=1). Model noticed the scope mismatch between the prompt and the ambient `plan.md` content, flagged it as data not instruction (good hygiene), and said it would "invoke [pacekeeper] once scope is confirmed" — but never actually called the Skill tool. |
| c | "stop after the current subtask" | pacekeeper alone, or pacekeeper + an execution skill, both PASS | PASS | Model invoked `Skill` with `{"skill":"pacekeeper","args":"stop after the current subtask"}` directly — no competing execution skill this time. Ran the first-run setup steps (checked `.claude/`, `~/.claude/pacekeeper-data/`) before hitting `--max-turns 6` (`error_max_turns`, 7 turns counted) mid-setup. |

Retest summary: 1/3 PASS (c), 2/3 still FAIL (a, b). Case c flipped from FAIL to PASS — plausibly
the description fix, since this case had no competing execution skill to explain a model/turn-
budget effect on its own. Case a still lost to `superpowers:executing-plans` alone, with no
pacekeeper call at all, even under Sonnet with more turns and the new "invoke in addition"
language — the competing skill still wins outright rather than pacekeeper being invoked
alongside it. Case b still never invoked any skill under this harness. Original round-1 results
(haiku, 4 turns, pre-fix) are left intact above for comparison; this section only adds new data,
it does not replace them.

## Post-trim retest (2026-07-16)

Task 12 trimmed the frontmatter `description:` (dropped the "these intents count in any language"
sentence and de-duplicated the stop/pause phrasing), keeping every clause prior evidence flagged
as load-bearing, including the "companion, not an executor — invoke IN ADDITION" sentence. Size:
682 → 556 chars (≈170 → ≈139 tokens at a 4-chars/token rule of thumb; the task brief's own
estimate of "~120 tokens" for the new string is in the same ballpark but not identically derived —
both are rough char-count proxies, not an actual tokenizer run).

Environment for this retest: `claude -p` CLI 2.1.209, macOS, run from a fresh scratch project
(`/private/tmp/.../scratchpad/pk-retest-project`, git-initialized) containing `plan.md` (5-task
checkbox plan) and `.claude/pacekeeper-state.json` with `"status":"paused"` (job `test-job-001`,
tasks 1–2 done, task 3 paused, tasks 4–5 pending) — both fixtures git-committed so
`git reset --hard && git clean -fd` restores clean state between cases. Model
`claude-sonnet-5`, `--max-turns 6`, `--allowedTools "Skill"`. Unlike the pre-trim retest, the
older Swedish-flavored `progress-monitor` skill is **no longer installed** in `~/.claude/skills/`
— confirmed by directory listing — so that confound is gone for this round. `superpowers:executing-plans`
remains installed and available as a competitor for case a.

The headless harness executed without any nested-sandbox auth/exec failure this time (a plain
`2+2` smoke test returned a normal result before the real cases were run). One run (case e,
first attempt) hit a transient `API Error: 500 Internal server error` unrelated to triggering;
it was simply retried.

**Important honest finding: cases a, b, and c were each run twice with byte-identical prompts,
model, turn budget, and allowlist (once via `--output-format json`, once via
`--output-format stream-json --verbose` for tool-call visibility) — and gave different
trigger outcomes both times.** This is run-to-run non-determinism, not an artifact of the two
output formats (the format flag does not change model input/sampling). It means the table below
is not a clean "the trim fixed it" result — it shows the trim did not make triggering
deterministic for a/b/c either way.

| Case | Prompt | Run 1 result | Run 2 result | Evidence |
|---|---|---|---|---|
| a | "Execute the plan in plan.md" | FAIL — no `Skill` call at all; went straight to `Bash`/`Write` (all denied by allowlist) | PASS — `Skill` called with `{"skill":"pacekeeper","args":"Executing plan.md (5 subtasks...)"}` *before* any `Write` calls, no competing skill invoked this run | `case-a.json` (run 1, denials only, no Skill), `case-a.jsonl` (run 2, stream-json, explicit `Skill` tool_use block) |
| b | "How long will this take?" | FAIL — no `Skill` call; investigated files/git status via `Bash` (denied), never called Skill | PASS — `Skill` called with `{"skill":"pacekeeper", "args":"...Provide an ETA / calibration."}`; model then read the skill's own "too small a job → skip" guidance and gave a direct sub-minute ETA instead of running the full protocol (arguably correct per the skill's own threshold logic, but the trigger call itself did happen) | `case-b.json` (run 1), `case-b.jsonl` (run 2) |
| c | "stop after the current subtask" | FAIL — no `Skill` call; reasoned in text about the paused state file (noticed a done/missing-file mismatch) but never invoked the Skill tool | PASS — `Skill` called with `{"skill":"pacekeeper","args":"stop after the current subtask"}`, followed by `Bash` reads of `.claude/pacekeeper-state.json` | `case-c.json` (run 1), `case-c.jsonl` (run 2) |
| d | "run with pacekeeper" | PASS — 0 permission denials, `num_turns=4`, consistent with a Skill-only call followed by stopping to ask for confirmation | PASS (stream-json rerun) — `Skill` called with `{"skill":"pacekeeper"}` explicitly, then `Bash` reads of state/plan files | `case-d.json`, `case-d.jsonl` |
| e | paused-state fixture present + "continue the work" | N/A — first attempt hit a transient `API Error: 500`, no real trial (discarded) | PASS — `Skill` called with `{"skill":"pacekeeper"}` after the model read the state file and said "this matches the pacekeeper skill's trigger condition exactly" | `case-e.jsonl` (retry) |

Acceptance per the plan: (d) and (e) must pass. **Both do, on the runs that actually completed
a real trial** (d passed on both of its runs; e's only real trial, after discarding the transient
500, passed). (a), (b), (c) are recorded honestly as inconsistent — each flipped from FAIL to
PASS across two identical-condition runs in this session, so no single verdict can be reported
for them without cherry-picking. This is a weaker result than a clean PASS: it suggests the
description trim did not break triggering for a/b/c (PASS was reachable), but it also did not
make triggering reliable — the model's decision to call `Skill` for an ambiguous "execute the
plan" / "how long will this take" / "stop after the subtask" prompt still depends on
sampling, not just on the description text.

**This headless, `--allowedTools Skill`-only, 6-turn, single-sample-per-case harness cannot
establish reliable pass/fail rates** — as shown directly by a/b/c flipping outcome on repeat with
nothing changed. A real verification of the trimmed description's trigger quality requires either
(a) many repeated samples per case to estimate a trigger rate, or (b) genuine interactive
sessions (full tool access, no artificial turn cap, a human operator) rather than this nested
headless harness. Flagging this explicitly per the task's honesty requirement: **the plan's
stated acceptance criterion (d, e must pass) is met on the data collected here, but a/b/c remain
unverified in any stable sense, and a non-nested interactive re-check is still recommended before
treating triggering as solved.**

## Post-rename trigger retest (whendone) — 2026-07-17

Re-run after the `pacekeeper → whendone` rename (frontmatter `name:` and `description:` both
changed). Same headless method as above.

### Environment (this retest)

- Claude Code CLI 2.1.209 (`~/.local/bin/claude`), macOS (Darwin 25.5.0)
- Harness: `claude -p "<prompt>" --model claude-haiku-4-5-20251001 --allowedTools Skill
  --max-turns 6 --output-format stream-json --verbose`, run from a fresh scratch project outside
  the repo (`.../scratchpad/wd-test-project`, git-initialized, `.gitignore` with `node_modules/`
  and `.claude/whendone-state.json`, `plan.md` with a 5-task checkbox plan). Each case preceded by
  `git reset --hard` + `git clean -fd`.
- Skill under test: `whendone`, installed at `~/.claude/skills/whendone` (symlink to the repo).
- **Confounds REDUCED vs. the 2026-07-16 session:** the old `progress-monitor` skill is no longer
  installed, and there is no stale `pacekeeper` skill — both absences confirmed by `ls`. The
  competing skills that remain are the `superpowers:*` plan skills (see cases a–c, e).

### Cases

| Case | Prompt | Expected | Result | Evidence |
|---|---|---|---|---|
| a | "Execute the plan in plan.md" | whendone triggers (in addition to any executor) | FAIL | `Skill` invoked `superpowers:executing-plans` only; whendone not called. num_turns 7 (hit max-turns). Same miss as the 2026-07-16 case a. |
| b | "…refactor five modules, run tests, update docs. How long will this take?" | whendone triggers (ETA question) | FAIL | `Skill` invoked `superpowers:writing-plans` (args: "effort estimation… realistic timeline"); whendone not called. num_turns 3. (2026-07-16 case b invoked no skill at all — still a miss either way.) |
| c | "stop after the current subtask" | whendone triggers (stop procedure) | FAIL | No `Skill` call at all (num_turns 1); no prior job/state existed. Same as 2026-07-16 case c framing. |
| d | "Execute the plan in plan.md, and run with whendone" | whendone triggers | **PASS** | `Skill` invoked `whendone` (args: "Execute … create 5 text files…"). Explicit "run with whendone" reliably triggers the renamed skill. num_turns 7 (hit max-turns). |
| e | paused `.claude/whendone-state.json` present + "continue the work" | whendone triggers (resume) | FAIL (harness artifact) | Deterministic across 3 identical Skill-only runs: `superpowers:executing-plans` every time, never whendone. A variant with `--allowedTools Skill Read` also missed (the model looked for a plan file, `PLAN.md`, and never inspected the state file). **Root cause: the headless harness never surfaces the paused-state signal to the trigger model** — there is no SessionStart hook or context injection announcing the paused job, and the model does not proactively read `.claude/whendone-state.json`. See e′ below. |
| e′ | same paused state, but salient prompt: "There's a paused whendone job in .claude/whendone-state.json… Resume it and continue the work." | whendone triggers (resume) | **PASS** | `Skill` invoked `whendone` (args: "resume"), num_turns 5. Confirms the renamed skill's *resume trigger wording is correct*; case e's failure is purely that the bare-prompt headless run gives the trigger model no signal that a paused job exists. |

### Interpretation

- The rename did not regress triggering: the one case that reliably fired under the old name via
  an explicit request ("run with …") fires identically under `whendone` (case d PASS), and the
  paused-resume path fires whenever the paused state is actually visible to the model (e′ PASS).
- Cases a–c behave as they did pre-rename: under Haiku with a 4–6-turn budget and only `Skill`
  permitted, plan-execution / ETA / stop prompts are captured by the `superpowers:*` skills or no
  skill, not by whendone. whendone is designed as an *in-addition* companion, so a–c are honest
  misses of the auto-trigger in this constrained harness — not name-specific breakage.
- Case e is a harness limitation, not a skill defect (e′ proves the wording works). In real
  Claude Code, a paused job is surfaced to the model via session context / hooks that this
  `claude -p` harness does not replicate.
- **Authoritative check still pending:** as with the 2026-07-16 session, a real non-nested
  interactive run (full tool access, no turn cap, a human operator) remains the true test of
  auto-triggering and end-to-end resume under the `whendone` name. Recorded honestly per the
  task's honesty requirement; run-to-run for e was deterministic here (no flip observed).

## Real end-to-end run under whendone monitoring — 2026-07-17

This section is the round-2 plan's "missing proof": a real, multi-subtask job run **with the
progress artifact, state file, calibration append, and summary regeneration all permitted** —
not sandbox-blocked as in the earlier headless runs above.

**The job:** this repo's own round-2 hardening plan (`docs/plans/2026-07-17-round2-fixes.md`),
14 subtasks, executed with superpowers:subagent-driven-development while whendone monitored it.
Unedited evidence:

- **Artifact:** one artifact published at job start and republished in place at every subtask
  boundary (15 publishes total — job start + 13 checkpoints + final DONE), same file, same URL,
  favicon `⏱️`. Per-task rows accreted their computed `actual (±%)` and per-task token sub-lines;
  the ETA block tracked elapsed / N-of-M / job tokens; the executor line showed `Sonnet 5` for
  delegated subtasks and `Opus 4.8` for the one run inline (Task 13).
- **State file:** `.claude/whendone-state.json` tracked all 14 tasks through
  pending→running→done, with `startedAt`/`finishedAt`/`actualMin`/`model` per task,
  `originalTotalMin` frozen at job start (149), and `etaAlertSent` flipping once.
- **Calibration log:** 14 rows appended via `scripts/append_calibration.py` (the new
  Task-1 helper) — the checkpoint wrote each row as a temp JSON file and the helper validated
  it, computed `actualMin` from the timestamps, and appended the canonical line. Three
  representative rows (job name is not sensitive; reproduced from the logged values, the log
  itself was never read back into context):

  ```json
  {"date":"2026-07-17","project":"pacekeeper","job":"Round-2 adversarial review fixes","category":"judgment-coding","rawEstimateMin":15,"startedAt":"2026-07-17T14:43:42+02:00","finishedAt":"2026-07-17T15:12:13+02:00","actualMin":28.5,"model":"sonnet","client":"cli"}
  {"date":"2026-07-17","project":"pacekeeper","job":"Round-2 adversarial review fixes","category":"documentation","rawEstimateMin":8,"startedAt":"2026-07-17T16:16:43+02:00","finishedAt":"2026-07-17T16:25:01+02:00","actualMin":8.3,"model":"sonnet","client":"cli"}
  {"date":"2026-07-17","project":"pacekeeper","job":"Round-2 adversarial review fixes","category":"testing","rawEstimateMin":18,"startedAt":"2026-07-17T18:26:08+02:00","finishedAt":"2026-07-17T18:29:16+02:00","actualMin":3.1,"model":"claude-opus-4-8","client":"cli"}
  ```

- **Slip alert:** at the 12-of-14 checkpoint the slip formula crossed threshold —
  `Σ(actual-or-estimate) = 229.4 min > 1.5 × originalTotalMin (223.5)` — and the one-per-job
  push fired. Delivery degraded honestly: **"Mobile push not sent (Remote Control inactive)"** —
  desktop only, matching the artifact's "uncertain delivery — requires Remote Control" status.
  (The slip is itself the expected signal: delegated subtasks measured dispatch→review-complete,
  plus two Critical-bug fix loops, ran longer than the frozen per-task estimates.)
- **token_usage.py** ran at every checkpoint (`--task N`) and at job end; job-level "spent"
  (output + fresh input) grew from ~0.77M to ~4.9M with ~128M cache reads by the final publish —
  numbers came from the script, never estimated.

**Regenerated calibration summary** (`scripts/calibration_summary.py`, at job end — the new
estimate-weighted M2 factor, M30 legacy-key count, M15 backtick-quoted models, and the Task-5
ETA rule + machine-usable q1/q3 footer all visible):

```
# Calibration summary

Regenerated: 2026-07-17 (29 data points, 4 legacy-key rows). ...

| Category | Factor (blended) | Data points | Confidence | Spread (IQR) |
|---|---|---|---|---|
| documentation | 1.21 | 9 | medium | 1.08–1.53 |
| judgment-coding | 1.15 | 9 | medium | 0.74–1.81 |
| mechanical-implementation | 0.87 | 7 | medium | 0.69–0.85 |
| testing | 0.68 | 4 | low | — |
```

Note the learning: `documentation` and `judgment-coding` moved *above* 1.0 (from 0.90/0.88
pre-run) because subagent-driven subtasks measured through their review loop genuinely take
longer than the raw estimate — exactly the correction whendone exists to make.

**Accuracy report** (`--report`, unedited):

```
# WhenDone accuracy report (29 data points)

| Category | n | Mean ratio (winsorized) | Lifetime factor | Last-10 mean ratio |
|---|---|---|---|---|
| documentation | 9 | 1.33 | 1.21 | 1.33 |
| judgment-coding | 9 | 1.23 | 1.15 | 1.23 |
| mechanical-implementation | 7 | 0.78 | 0.87 | 0.78 |
| testing | 4 | 0.28 | 0.68 | 0.28 |
```

**Honest limits of this run:**
- It ran from the **dev working tree** — the installed skill dir (`~/.claude/skills/whendone`)
  is a symlink to this repo — on the release branch, **not a fresh clone at the v0.2.0 tag**.
  A clean-clone install-and-run is still recommended as an author step.
- **Cross-session resume was not exercised** (the whole job ran in one session); resume remains
  the least-tested path.
- **No GUI screenshot** was captured (headless environment). The hero image is still rendered
  from `assets/demo-artifact.html`; capturing a real screenshot from an artifact like the one
  above remains an author step.

## Stage 3 — Source-A tailer/watcher dogfood + monitoring run — 2026-07-18

Stage 3 (declare-once, tail-thereafter watcher; `scripts/tail_progress.py`) was executed with
superpowers:subagent-driven-development while whendone monitored the execution. Two independent
bodies of evidence came out of it: a **CLI integration dogfood** of the new tailer (plan Task 10),
and the **monitoring run itself** as live proof of the render/publish/calibration loop.

### CLI integration dogfood (Task 10)

The new `tail_progress.py` was driven end-to-end through its **real CLI via subprocess** (real
argv, stdout, exit codes, process lifecycle — not the in-process unittest path), in a fully
isolated scratch project with an isolated `WHENDONE_DATA_DIR`, against realistic transcript
fixtures (TodoWrite `in_progress`/`completed` + subagent dispatch/result, the shapes verified live
per plan decision D13). **21/21 drills PASS:**

| Drill | What it exercised | Result |
|---|---|---|
| D1 | L3 one-shot: observe 2 TodoWrite completions → 2 tasks `done`, transcript timestamps stamped, render HTML, 2 isolated calibration rows, `progress` event w/ `etaText` | PASS |
| D2 | Idempotent re-scan: no new calibration rows, `done` tasks untouched | PASS |
| D3 | `--follow --exit-on-event` all-done via real subprocess + pid lock created & released + 3rd calibration row | PASS |
| D4 | Duplicate tailer: live foreign lock → `already-running`, exit 4 | PASS |
| D5 | `--job-id` mismatch → `ownership-lost`, exit 3, no writes | PASS |
| D6 | Staleness (F13): running task, old `startedAt`, empty transcript → exactly one `stale` event, `staleNotifiedAt` persisted | PASS |
| D7 | `status: paused` → `no-op`, exit 0 | PASS |
| D8 | `source: "b"` → `unsupported-source`, no writes | PASS |

**Isolation verified:** the real `~/.claude/whendone-data/calibration.jsonl` ended with **0
dogfood rows** — toy timings went only to the isolated dir and never polluted the real
per-category factors.

**Honest limit — no live Monitor-driven wake:** this session used the model-driven monitoring
protocol (the model does the checkpoint work), NOT a live Monitor watcher waking the model on a
`progress` event. So no real "wake turn" exists in any transcript to measure. The README's
per-wake figure (~1–3k tokens) is therefore a **component estimate** (one `progress` event line
read — measured at 54 cl100k tokens — + a short assistant turn + one Artifact publish), clearly
labelled as such, with a live-Monitor-wake measurement **deferred**. The underlying `--follow`
CLI those levels invoke is fully exercised (D3/D4/D6/D7) including exit codes and lock lifecycle;
what was not drilled is a *persistent* Monitor watcher / background-Bash relaunch cycle (skipped
to avoid a lingering background process).

### Monitoring run (this session) — live render/publish/calibration evidence

whendone monitored its own 12-subtask stage-3 execution:

- **Artifact:** one artifact published at job start and republished in place at each subtask
  boundary and at DONE (~13 publishes, same file, same URL, favicon `⏱️`), each render produced
  by `render_artifact.py` against the live multi-model state. This dogfoods the render/publish
  half of the loop end-to-end (the half stage 3 automates in the tailer).
- **State + calibration:** `.claude/whendone-state.json` tracked all 12 tasks
  pending→running→done; **12 calibration rows** appended via `append_calibration.py`. The
  same-family alias-upgrade guard was exercised — Task 4 and Task 11 windows were briefly
  opus-dominated (an opus *reviewer* outweighing the sonnet *implementer* in the token window),
  and the guard correctly refused to upgrade `sonnet`→opus for Task 4 (logged as the alias),
  while Task 11's larger sonnet implementer kept its window sonnet-dominated (upgraded to
  `claude-sonnet-5`).
- **Slip:** never fired — peak `slipTotalMin` ≈ 136.6 stayed under the 1.5×`originalTotalMin`
  (176.4) threshold, even with two review/fix loops (Tasks 6, 9) and the size-gate iteration
  (Task 11) running long.
- **Accuracy this run:** took **142 min vs 117.6 estimated (+21 %)** — the overrun concentrated
  in the three tasks with review/fix loops; the rest ran under estimate.
- **Tests:** 155 → **196 green**, warning-clean (`-W error::ResourceWarning`).

**Regenerated calibration summary** (`calibration_summary.py`, at job end — 57 data points):

```
| Category | Factor (blended) | Data points | Confidence | Spread (IQR) |
|---|---|---|---|---|
| documentation | 0.98 | 21 | high | 0.59–1.40 |
| judgment-coding | 0.81 | 22 | high | 0.49–1.02 |
| mechanical-implementation | 0.87 | 7 | medium | 0.69–0.85 |
| review | 0.86 | 2 | low | — |
| testing | 0.67 | 5 | medium | 0.21–0.61 |
```

`documentation` and `judgment-coding` both crossed into **high confidence** (n ≥ 20) this run.

**Honest limits (unchanged from stage 2):** ran from the dev working tree (installed skill dir is
a symlink to this repo), not a fresh clone at a tag; **cross-session resume was again not
exercised** (whole job ran in one session) and remains the least-tested path; no live
Monitor-driven wake (see above); no GUI screenshot.

## Stage 4 — Source-B (Workflow-engine) dogfood + live-Monitor monitoring run — 2026-07-18

Stage 4 (Source B: Workflow-journal ingester; `scripts/workflow_journal.py` + the `sync_cycle_b`
seam in `scripts/tail_progress.py`) was executed with superpowers:subagent-driven-development
while whendone monitored the execution under a **live L1 Monitor watcher**. Three bodies of
evidence: the **Source-B dogfood** (plan Task 11), the **live-Monitor monitoring run** itself,
and a **documented (not-yet-run) cross-session resume drill**.

### Source-B dogfood — live Workflow run, end-to-end

A real 3-phase Workflow was launched via the Workflow tool (runId `wf_0580e207-b1b`, 6 agents:
2 per phase, each `agent()` prompt tagged `[wd:understand]` / `[wd:review]` / `[wd:verify]` per
the B1 declare-at-authoring convention). Phases declared in a Source-B state file
(`source:"b"`, `workflowRunId`, per-phase `category`/`rawEstimateMin`/`wdTag`/`agentsExpected`).
The state file lived in an isolated scratchpad dir so its pid lock did not collide with the
concurrent meta-job's `.claude/whendone-tail.lock` (lock path is derived from the state file's
directory — verified). First render + publish produced a **separate artifact**
(`…/artifact/50e2ad95-…`) showing all three phases at `0/2 agents`.

The lightweight agents completed in ~42 s — faster than a live L1 follow loop could show
intermediate wakes — so the Source-B-specific finalize path was validated by driving one
`tail_progress.py` sync cycle over the **completed** run (the follow-loop wrapper itself is
unchanged shared Source-A machinery, already drilled in stage 3 D3/D4/D6/D7). That single cycle,
on the NEW Source-B code, produced (all from unedited script output):

- **Journal parsed** (v2 lines only, real agentIds/keys), completion record
  `workflows/wf_0580e207-b1b.json` detected as the all-done signal.
- **Per-phase attribution via `[wd:]` tags**, with spans read from the agent transcripts, not the
  journal: Understand `19:11:21→29`, Review `19:11:31→42`, Verify `19:11:44→57` (UTC) — sequential,
  correctly separated, proving tag attribution + transcript-timestamp timing.
- **Display transitions** pending→running→done for all 3 phases; per-task `2/2 agents` and
  job-level `Workflow agents: 6/6 finished` rendered (Task 6 counters).
- **One calibration row per phase** appended by `finalize_b` at the completion record:
  calibration.jsonl **67 → 70** (three rows), categories `research` / `review` / `testing`,
  `model:"unknown"` (dogfood meta.json carried no `model` key — matches the survey's
  "sometimes present"), `actualMin` computed from the spans. Verified by `wc -l` before/after and
  the `--report` view (never read into context per Constraint 3).
- **`all-done`** event emitted with `agentsStarted:6, agentsDone:6`; DONE banner rendered
  (`Done — took 1 m (estimated 7.6 m)`); calibration summary regenerated (70 points); **pid lock
  left clean** (one-shot released it).

**Honest limits of the dogfood:** (1) agents ran lightweight analysis prompts (no file reads) —
the target was validating the *monitoring pipeline*, not producing a substantive review; (2) the
calibration rows are tagged `project:"scratchpad"` because the state file was placed in the
scratchpad dir (to avoid the lock collision) — `append_calibration` derives `project` from the
state file's parent dir; a real Source-B job with state in the project's `.claude/` tags the
correct project name; (3) the run finished before a live L1 follow could show *incremental*
phase-by-phase wakes, so intermediate Source-B wake rendering was not separately captured (the
finalize + all-done + rows path was fully validated as above).

### Live-Monitor monitoring run (this session) — first real Monitor-driven wakes

Unlike stages 2–3 (model-driven checkpoints, no live watcher wake), the stage-4 execution ran
under a **real L1 Monitor watcher** (`tail_progress.py <state> --follow`, persistent) for the
whole session. It woke the lead on each subtask completion and emitted `progress` / `stale` /
`all-done` events; the lead republished the artifact in place on each `progress` wake (same file,
same URL `…/artifact/57ead8a9-…`, favicon ⏱️). This is the first stage with genuine
live-Monitor-wake evidence (the stage-3 limit "no live Monitor-driven wake" is now closed).

**Per-wake cost (observed, this environment):** each wake delivered one compact JSON event line
(~50–90 tokens) plus — because the watcher rewrites the artifact HTML each cycle — a harness
"file modified" re-injection of the ~2.7 KB rendered page (~800–1,000 tokens), plus the lead's
single Artifact publish call. So the observed marginal per-wake cost is ≈ **1 k tokens of
injected context + one publish turn**, consistent with stage 3's component estimate (one event
line measured at 54 cl100k tokens + a short turn + a publish). The HTML re-injection is
environment-specific (Claude Code echoing a changed tracked file back to the model); a headless
watcher that renders to a non-watched path would not incur it.
*[Correction 2026-07-19: re-measurement revised the echo cost UPWARD — median 1,738 tok/echo
this session, vs the ~800–1,000 estimated above — and the "non-watched path" explanation was
wrong — see "Per-wake re-injection
mechanism — forensics + controlled experiment" below. Render location is irrelevant; the file is
registered by the Artifact publish itself, and the echo is tied to the interactive session
surface, not the path.]*

### Cross-session resume drill (Source B, B12) — documented, NOT run this session

A Workflow run dies with its launching session, so a genuine cross-session Source-B resume cannot
be exercised within a single session; it is recorded here as the exact manual procedure for a
follow-up run (still the least-tested path, as in stages 2–3):

1. Declare + launch a Source-B job (as above); let ≥1 phase reach `done` (with its `actualMin`
   logged); note `wc -l ~/.claude/whendone-data/calibration.jsonl`.
2. Request stop → whendone runs the stop procedure (watcher stopped first, final one-shot sync,
   `status:"paused"` render, push, `.claude/STOP` deleted).
3. **Open a fresh Claude Code session.** whendone should summarize the paused state, confirm the
   `artifactUrl` is the user's, and re-mint `artifactFile` in the new session's scratchpad.
4. Relaunch via `Workflow({scriptPath, resumeFromRunId})` → a **new runId** → update
   `workflowRunId`, restart the watcher at L1 (B12). Cached-prefix replays may surface as
   near-instant started/result pairs.
5. **Verify:** phases already `done` keep their `actualMin` and are **not** re-appended
   (`wc -l` on calibration.jsonl unchanged for those phases across the resume); the artifact URL
   is the same. F9 rebaseline applies only if the phase list changed during the pause.

The done-is-done guard this drill checks is unit-tested (`test_finalize_never_reappends_done_phase`
in `scripts/test_tail_progress.py`); the drill is the live cross-session confirmation of it.

## Stage 5 — flip-gate check — 2026-07-19

The release gate (design spec §2): Sources A and B both working, plus Source C and the positioning
rewrite, all precede the public flip. This section RECORDS the gate; it does not push (the push is
the owner-run flip checklist, `docs/plans/2026-07-18-flip-checklist.md`).

**Suites (warning-clean).** `python3 -W error::ResourceWarning -m unittest discover -p 'test_*.py'`
across the six suites: **267 tests, OK** (243 baseline + 24 stage-5 additions — Source-C observer,
sync_cycle_c, calibration guard, follow-mode pins, renderer pins, and workflow_journal containment).

**Formula parity.** `widen that task's band to the envelope` → **1/1/0/0/0/0**
(`references/file-formats.md`:1, `scripts/calibration_summary.py`:1; SKILL.md + all three
`source-*.md` at 0). Unchanged from the stage-4 baseline — no formula prose was touched in stage 5.

**Forbidden-string + secrets sweep (tracked files).** `git grep` for the agreed private marker
strings (case-insensitive): **all absent.**
Secrets scan (`api[_-]?key|secret|BEGIN … PRIVATE KEY|Bearer …`): the only hits are two benign
security-context prose mentions in `README.md` (the sensitivity-flagging paragraph and the
"API-key-only setups" note) — no credentials, tokens, or keys. Manual skim: no Swedish prose in
tracked docs.

**Token budget (stage-5 re-measurement, `tiktoken cl100k_base`, 2026-07-19).** Source-A
trigger-to-first-publish path (same composition as stage-4's 12,182): **12,313 tokens** (+131 drift
from the five SKILL.md flips + the file-formats note), under the 14,000 gate with 1,687 to spare.
`references/source-c.md` standalone: **920 tokens** (gate <2,500) — off the Source-A trigger path,
read only when Source C is detected.

**Gate evidence (cited, not re-run).**
- Source A working → the Stage-3 Source-A dogfood entry above (this file, 2026-07-18).
- Source B working → the Stage-4 Source-B dogfood entry above (runId `wf_0580e207-b1b`, 2026-07-18).
- Source C working → the live Source-C dogfood entry below (this session, artifact
  `048ecc36-4bfb-407b-9d43-403c4a5429fb`, 2026-07-19).
- Docs consistency → `git grep "not shipped"` over SKILL.md/references/README.md: zero survivors;
  README install tag `v0.3.0`; CHANGELOG header order v0.3.0 → v0.2.1 → v0.2.0 → v0.1.0.

All gate conditions met. The two never-run-live paths (Source-A and Source-B cross-session resume
drills) remain owner tasks on the flip checklist — they gate the flip, not this branch's merge.

## Stage 5 — Source-C (pace-only) live dogfood — 2026-07-19

A real end-to-end Source-C run in the stage-5 execution session — no mocks. whendone monitored the
final stretch of its own build: the TodoWrite list mirrored was the five remaining work items of
this stage (the flip-gate check and the flip-checklist/ledger/final-review of Tasks 13–14), and the
watcher ran while that work was actually done.

**Environment.** macOS, `zsh`; real `.claude/whendone-state.json` with `source:"c"`,
`originalTotalMin:null`, this session's id in `sessionIds`; artifact
`048ecc36-4bfb-407b-9d43-403c4a5429fb`. Watcher level: **L1 Monitor** (`tail_progress.py --follow`,
5 s interval / 30 s debounce), the first stage-5 Source-C run under a live Monitor watcher.

**Job start.** State written per `references/source-c.md` (no classification, no estimate table, no
calibration-summary read; `tasks:[]`). The gitignore precondition was already satisfied
(`.claude/whendone-state.json` + `.claude/whendone-tail.lock`). A one-shot sync mirrored the five
TodoWrite items into `tasks` on the first pass — each carrying only
`nr`/`name`/`status`/`startedAt`/`finishedAt` (no estimate/category/model keys), with `startedAt`
taken from the transcript's TodoWrite timestamp. First render + publish emitted
`etaText:"ETA not yet known (uncalibrated)"` (empty→one running item), published to the URL above.

**Observed event lines (representative).** One `progress` line per mirrored transition, then the
terminal `all-done`:
```
{"event":"progress","done":0,"total":5,"etaText":"ETA not yet known (uncalibrated)"}
{"event":"progress","done":1,"total":5,"justDone":["Run full test suites (267) warning-clean"],"etaText":"Done ~22:51 (uncalibrated — pace-based)"}
{"event":"progress","done":2,"total":5,"justDone":["Formula-parity + forbidden-string + secrets sweeps"],"etaText":"Done ~22:41 (uncalibrated — pace-based)"}
{"event":"progress","done":3,"total":5,"justDone":["Record flip-gate evidence + write flip-gate test-log entry"],"etaText":"Done ~22:39 (uncalibrated — pace-based)"}
{"event":"progress","done":4,"total":5,"justDone":["Write the public-flip checklist + update SDD ledger"],"etaText":"Done ~22:36 (uncalibrated — pace-based)"}
{"event":"all-done","done":5,"total":5,"justDone":["Final whole-branch code review + apply fixes"],"etaText":"Done (uncalibrated)"}
```

**What the dogfood confirmed.**
- **Mirror transitions** on each wake: the newest TodoWrite snapshot drove `done` count, per-task
  status icons, and computed `Actual` times (e.g. 2.1 m, 1.4 m, 4.7 m) with no hand-editing.
- **Uncalibrated label everywhere:** `etaText` carried `(uncalibrated — pace-based)` while running
  and exactly `Done (uncalibrated)` at end; the page's ETA headline matched. The pace ETA adapted as
  items completed (~22:51 → ~22:36). **Category and Est. columns rendered `—`** (no estimates);
  `slipAlert` never fired; `estimateTotalMin` was `0`.
- **`justDone` naming** correctly named the item(s) newly completed at each wake.
- **Same artifact URL** (`048ecc36…`) across all ~six republishes (same scratchpad file path).
- **Zero calibration writes:** `wc -l ~/.claude/whendone-data/calibration.jsonl` = **72 before and
  72 after** — the §5.1 guard held live for a full Source-C completion (no `handle_completion`/append
  on the Source-C path).
- **Job end:** on `all-done` the watcher exited **rc 0**, removed `.claude/whendone-tail.lock`, and
  the Monitor stream ended. The model then set `status:"done"`, ran `token_usage.py` (no `--task`),
  and rendered + published the **DONE (uncalibrated)** banner; a best-effort "job done" push was sent
  (delivery uncertain — no Remote Control).

**Deviations from `references/source-c.md`:** none. The protocol was followed as written and the
document needed no correction — job-start (mirror-only, no declaration), wake handling (one publish
per `progress`/`all-done`, verbatim `etaText`), and job-end (no calibration-summary regeneration)
all matched observed behavior. Dogfood limit: because the run tracked the model's own multi-minute
work items, wakes were minutes apart (not sub-second), which exercised the debounce/render tail
naturally.

## Source-A cross-session resume drill (flip checklist A1) — 2026-07-19

The first of the two never-run-live paths, run against a real job: the flip checklist's own Part-B
pre-push verification, declared as a 4-subtask Source-A job. Session 1 declared and ran the job
under an L1 watcher; the session was ended with subtask 4 in flight (state `paused`, subtask 4
`running` with `startedAt` set and `finishedAt` null). Session 2 was a fresh session opened with
only "resume the whendone job".

**All five drill checkpoints verified.**

- **State summarized and confirmed before any action:** the fresh session read
  `.claude/whendone-state.json`, presented job name, plan-file path, the saved `artifactUrl` as a
  quoted literal, and the done/remaining task split, then waited for explicit confirmation
  (including URL ownership) before touching anything.
- **Artifact republished onto the SAME URL** (`57a29fda-62e0-49be-a7ce-78788436fec8`): a fresh
  `artifactFile` was minted in the new session's scratchpad (the state file's path was never
  written to), rendered with the RUNNING banner, and published with the `url` parameter — same
  link before and after the kill, DONE banner included.
- **Done subtasks kept `actualMin`, never re-appended:**
  `wc -l ~/.claude/whendone-data/calibration.jsonl` = **75 before resume, 76 after completion** —
  exactly one new row, for the redone subtask 4; subtasks 1–3 kept their logged times untouched.
- **Watcher restarted at L1** (Monitor, `tail_progress.py --follow`); it detected the final
  completion itself, emitted `all-done`, and exited rc 0 on its own.
- **DONE banner completion:** `"Done — took 21 m (estimated 17.4 m)"`, 4/4 subtasks; pause
  accounting folded 10.4 min into `pausedTotalMin`; the calibration summary was regenerated at
  job end.

The interrupted subtask 4 followed the crash-resume rule as written: read-only (documentation
category), so redone with a fresh `startedAt` and `actualMin: null` for the lost partial run —
its earlier partial time landed in pause accounting, not work time.

**Drill finding (fixed same day):** the tailer's default `now` was `datetime.now(timezone.utc)`,
and since the renderer displays every time in `now`'s tz, all tailer-driven renders painted the
artifact in UTC ("last updated 08:05" against a 10:05 local wall clock). Fixed as a local-time
policy across the scripts (`tail_progress.py` default `now`, `token_usage.parse_ts`, overlap
reference, `lastActivity`); pinned by three new tests (`LocalTimePolicyTest` in
`test_tail_progress.py`, `ParseTsTimezoneTest` in `test_token_usage.py`). Suites after the fix:
**270 tests, OK, warning-clean.**

## Source-B cross-session resume drill (flip checklist A2 / B12) — 2026-07-19

The second of the two never-run-live paths. Session 1 declared a 4-phase Source-B job
("B12 Source-B cross-session resume drill (attempt 3)": Scan / Cross-check / Audit / Report,
estimate 14.5 m) over a tagged Workflow run and was killed mid-Audit (71 s into the run, 1 of
2 audit agents in flight). Session 2 was a fresh session opened with only "resume the whendone
job".

**Resume procedure verified:** state summarized and confirmed — including `artifactUrl`
ownership — before any action; fresh `artifactFile` minted in the new session's scratchpad (the
state file's path never written to); artifact republished onto the SAME URL
(`c97b117b-9f0f-4e44-b8d1-a721ed8dfff0`) with the RUNNING banner; pause accounting folded
3.8 min into `pausedTotalMin` (crash-resume fallback, `now − last finishedAt`); the crashed
Audit phase (review, read-only) reset for a clean redo; workflow relaunched via
`Workflow({scriptPath, resumeFromRunId})`; the run completed end-to-end with the DONE banner
`"Done — took 8 m (estimated 14.5 m)"` on the same URL, push sent, calibration summary
regenerated.

**Procedure correction: the engine REUSES the runId on resume** — the stage-4 notes and
source-b.md's resume section assumed a new one. `workflowRunId` needs no update (compare and
update only if it ever differs); the resumed run's dir appears under the NEW session while the
dead session's dir persists under the old — same runId, two dirs.

**Drill finding (fixed same day): a killed run's record finalized the job.** The session kill
had written `<old-session>/workflows/<runId>.json` with `status: "killed"` and
`error: "Workflow aborted"` — `run_finished()` checked only that the file exists, and
`find_run_dir()` returned the first `sessionIds` match, i.e. the dead session's dir. First
watcher cycle after resume: a false `all-done` off the dead run — Audit/Report stamped done
(Audit with `finishedAt` null, Report with no timestamps at all), watcher exited while the
resumed run was still live. Scan/Cross-check were finalized in that same pass from their real
run-1 spans (two calibration rows, `actualMin` 0.5 each — early, but valid data). Recovery
workaround (state data only, watcher stopped): `sessionIds` reordered newest-first, false-done
phases reverted to pending. The resumed run then finished for real (7 engine agents: 4
cached-prefix replays + 3 live), and the final one-shot sync appended **exactly 2 new rows
(81 → 83)** for the newly finished Audit (7.0 m) and Report (0.5 m) — **done phases kept
`actualMin` and were never re-appended** (done-is-done, the drill's core assertion).

**Fixes (same day, TDD — all four new tests watched fail on the old behavior first):**

- `workflow_journal.run_finished()` parses the record and requires `status == "completed"`;
  killed / failed / statusless / unparseable / oversized (>1 MB) all fail closed to "not
  finished" — stale visibility over a false DONE. Predicate backed by a survey of 24 real run
  records across 8 projects: `status` always present (completed 22, killed 1, failed 1;
  killed/failed carry `error`).
- `workflow_journal.find_run_dir()` collects every matching run dir across `sessionIds` and
  returns the one with the newest `journal.jsonl` mtime (no journal sorts oldest; ties keep
  list order, preserving single-dir behavior).
- `references/source-b.md`: resume section rewritten for runId reuse; "completion record"
  defined as a status-`completed` record only.
- Tests: `test_run_finished_killed_or_failed_record_fails_closed`,
  `test_run_finished_statusless_or_unparseable_record_fails_closed`,
  `test_find_run_dir_prefers_newest_journal`,
  `test_find_run_dir_without_journals_falls_back_to_first` (unit), plus the integration
  regression `test_killed_record_does_not_finalize`; `WfEnv.finish_run` now writes a
  real-shaped record instead of `{}`. The fix was also replayed against the real on-disk run
  dirs from the drill: old/killed → not finished, new/completed → finished, and the original
  `sessionIds` order now picks the live dir.

Suites after the fix: **275 tests, OK, warning-clean.** Both never-run-live resume paths (A1,
B12) have now been exercised end-to-end against real jobs.

## Context-slimming Fix 1 — Resume extracted to references/resume.md — 2026-07-19

De-monolithized SKILL.md: the entire `## Resume` section (fail-closed preamble + steps 0-6) moved
verbatim into `references/resume.md`, read only on a resume trigger. SKILL.md keeps a 4-line
`## Resume` stub pointing at it; the fail-closed rule stays in job-start step 1 (where it is
relied on before any resume path is chosen). Two bare "step-5" references inside the moved text —
which pointed at SKILL.md job-start step 5 while Resume lived in the same file — were made
explicit ("SKILL.md job-start step 5") to survive the move; nothing else in the prose changed.
All cross-references repointed (SKILL.md job-start ×2, `source-a.md`, `artifact-template.md`,
`file-formats.md` ×3); `grep -rn "Resume step\|go to Resume"` over SKILL.md/references/README.md/
docs/design.md returns zero survivors.

**Measurement (`tiktoken cl100k_base`, method = raw token sum of file contents, no Read-tool
line-number prefixes, + `calibration_summary.py` output on the design.md synthetic fixture — the
same composition as the README trigger row).** Trigger-to-first-publish read set = SKILL.md +
`source-a.md` + `file-formats.md` + `artifact-template.md` + calibration-summary allowance;
`resume.md` is OFF this path.

| Point | Trigger read set | SKILL.md body |
|---|---|---|
| Stage-5 commit `e42b0f1` (recorded 12,313) | 12,385 (this reconstruction, +72 / 0.6%) | 4,262 |
| HEAD pre-edit (drift from post-stage-5 commits) | 12,552 | 4,262 |
| HEAD post-edit (Resume extracted) | **11,639** | 3,342 |

- **Saving on every non-resume trigger: 913 tokens** (12,552 → 11,639), matching the plan's
  expected ~0.9–1.1k. The absolute lands above the plan's 11.2–11.4k estimate only because
  current HEAD had drifted to 12,552 (local-time policy + renderer-overhaul commits grew
  `file-formats.md`/`artifact-template.md`) before this edit; the delta is on target.
- `references/resume.md` standalone: **1,092 cl100k tokens** (4,287 chars) — the added cost paid
  only on resume sessions (accepted trade-off; documented in the README resume row).
- The +72-token (0.6%) reconstruction variance vs the recorded 12,313 comes from an unrecovered
  formatting detail in the original run (the synthetic fixture output measures 3,302 chars here
  vs the 3,371 recorded in design.md); it is an additive constant that cancels in the 913-token
  delta. The prefixed-variant check (Read-tool line prefixes on the three ref files) gives 13,342
  — far from any documented figure — confirming raw-content summation is the faithful method.

README Overhead table updated: trigger row → 11,639 (2,361 to spare under the 14k budget), resume
row now lists the `resume.md` read, and the "why the figure moved" note documents both movements
(drift up to 12,552, then extraction down to 11,639) plus the method/variance note. Second
in-prose `12,313` mention (amortization line) updated to ≈11,639.

**Suite: `python3 -W error::ResourceWarning -m unittest discover -p 'test_*.py'` → 285 tests,
OK, warning-clean** (no script reads SKILL.md content; unchanged as expected).

## Per-wake re-injection mechanism — forensics + controlled experiment (2026-07-19)

Context: the context-slimming plan's Fix 2 asked what makes the harness re-inject the rendered
artifact HTML at watcher wakes. A first forensic pass concluded the re-injection didn't exist
(false negative — detector blind spot; see
`docs/reviews/2026-07-19-per-wake-reinjection-forensics.md` (internal review record, not
shipped in this repo)).
A second pass found it in `type:"attachment"` / `edited_text_file` entries and hypothesized
scratchpad-location watching. This session settled it with a controlled experiment (session
`3d35911d`, Claude Code 2.1.215, VSCode extension).

**Measured (tiktoken cl100k on the `snippet` payloads, all echo-positive real sessions):**

| Session | HTML echoes | median tok | max tok | state-file echoes (tok) |
|---|---|---|---|---|
| `fa4e497b` (stage-4, 13 tasks) | 14 | 1,738 | 1,952 | 2 (2,128 / 982) |
| `69f04d45` (stage-3, 12 tasks) | 12 | 1,852 | 2,033 | 0 |
| `7d1011ea` (stage-2) | 5 | 1,626 | 1,730 | 0 |
| `aa5f7a76` (B12 resume drill) | 5 | 791 | 1,016 | 2 (687 / 620) |
| `d87c6263` (stage-5c) | 5 | 600 | 621 | 1 (599) |
| `43fd86f0` (pre-push run) | 5 | 597 | 628 | 1 (238) |

Watcher event lines (queued_command prompts with a `progress` event): 54–341 tok, median ~120.

**Controlled experiment (six arms, model never touches the files after setup unless stated):**

| Arm | Registered how | Rewritten by | Echo? |
|---|---|---|---|
| 4 files in scratchpad / `/tmp` / project `.claude/` | Bash only | background task ×6 | no |
| Bash-only scratchpad file | Bash only | model's own Bash | **no** |
| Bash-created file, Artifact-published once | publish | background task ×4 | no |
| Same published file | publish | model's own Bash | **YES** (inline note, line-numbered) |
| Write-tool-created file | Write | model's own Bash | **YES** |
| Same published file | publish | Monitor-managed process ×3 | no |

**Conclusions (proven):**
- A file enters the harness's tracked set via model Write/Edit **or via an Artifact publish**
  (`file_path` registration proven: the published-only file echoed, the Bash-only file never did).
- Script/Bash writes never register a file. Render **location is irrelevant** — scratchpad,
  `/tmp`, and project `.claude/` behaved identically. Both earlier mechanistic stories ("renders
  to a non-watched path avoids it", "the scratchpad is watched") are wrong.
- A change made *during a model-issued Bash call* to a tracked file is echoed immediately
  (proven). This covers L3 one-shot wakes and any model-run render.

**Conclusion (best-fit, not directly proven):** the dogfood sessions' wake-turn echoes (12/13
stage-4 echoes sit immediately after a Monitor task-notification; 4/4 in stage-5c) did NOT
reproduce with a synthetic Monitor rewriting a published file in this session. The surviving
difference is the interactive surface: in the dogfoods the artifact page was open in the VSCode
extension while the job ran (also explains `test_render_artifact.py` — an open editor file —
being echoed 8.2k chars in `aa5f7a76`). Treat "IDE panel open on the artifact → echo per wake"
as the best-fit explanation; the operational split below holds regardless, since it is measured
on real runs on both sides.

**Operational reading (what the README row now says):** watching the artifact in the IDE during
the run costs the full echo per wake (~0.6–2.0k tok, scaling with the task table, ≤1/wake by
debounce). The walk-away scenario — nothing open locally, progress viewed on phone/browser —
showed zero wake-turn echoes in the controlled run: per-wake cost = event line + one publish
turn. State-file echoes are rare (0–2 per session, only where the model touched state at
declare/resume) — independent validation of the single-writer invariant, not a per-wake cost.

Artifacts of the experiment: disposable private artifact "EXPSIG-E cycle 0"
(`…/artifact/fdde1edd-…`, safe to delete from the gallery); experiment files removed after the run.

## Consolidated pre-flip review — fix pass + re-measure — 2026-07-19

An independent whole-picture audit ran before the public flip: protocol coherence traced
end-to-end across SKILL.md + all six reference files, cross-reference grep sweeps, docs-vs-code
verification of ~45 script claims, security/egress posture, and independent re-measurement.

**Verified before fixes:** the 285-test suite passed warning-clean
(`python3 -W error::ResourceWarning -m unittest discover`), and the README's 11,639 trigger-path
figure reproduced EXACTLY under an independent `tiktoken cl100k_base` run (3,342 + 1,965 +
4,358 + 878 + 1,096). No code, measurement, or security defects found.

**Findings (all doc coherence/staleness) and fixes:** see the CHANGELOG's "Pre-flip
consolidated review" block. Highest-severity: a stale "only source shipped today" /
"Source C ships later" pair left over from before the stage-5 flip; an unhedged IDE-echo
causation sentence in the README's per-wake row (now labeled best-fit, matching this log's
entry above); the resume flow never routing Source-B jobs to source-b.md's Workflow-relaunch
paragraph; step 8 instructing task-list invention for Source-C jobs; a stale source-b.md
token figure.

**Post-fix re-measure (same `tiktoken cl100k_base` raw-content-sum method):** trigger path
**11,713** = SKILL.md 3,375 + source-a.md 1,984 + file-formats.md 4,376 +
artifact-template.md 882 + calibration-summary fixture 1,096 (fixture output now 3,377 chars,
identical across two regeneration runs). Standalone: source-b.md **2,067** (was 1,810 before
the B12 resume-drill fixes), source-c.md **921**, resume.md **1,211** (grew with the
Source-B/C resume-routing fix). Spare vs the 14,000 budget: **2,287**. Suite re-run after all
edits: 285 tests OK, warning-clean (docs-only pass — expected, and verified anyway).

## Pre-flip adversarial-review fix run — 2026-07-19

An 8-persona adversarial review (internal record, not shipped in this repo) preceded the
public flip; every Stage-1 (P0) finding was fixed in this run, executed task-by-task with
per-task independent review.

**Script fixes (each TDD, red→green):**

- **C1 STOP-file re-home:** `stop-requested` event emitted by the tailer when `.claude/STOP`
  exists and status is `running`; non-terminal, source-independent, `_emit_once`-deduped,
  added to the follow wake set. Tests: `StopRequestedTest` (emit + ordering, no-file, paused
  suppression) + a Source-A twin in `OneShotTest`.
- **I1 Source-B status guard:** `sync_cycle_b` no-ops on non-running status (a paused
  Source-B job could append calibration rows). Test:
  `test_paused_b_state_noops_without_writes`.
- **I2 finalize idempotency:** per-task `bFinalized` flag written atomically with the
  done-marker before the append. Tests:
  `test_finalized_phase_with_null_actual_is_never_reappended`,
  `test_finalize_sets_bfinalized_with_done_marker`.
- **I3 one-shot lock respect:** `_live_lock_holder` + guard in `one_shot` — yields
  (`already-running`, rc 4) to a live lock holder; dead/absent/garbled lock proceeds. Tests:
  `test_one_shot_defers_to_live_lock_holder`, `test_one_shot_proceeds_past_dead_pid_lock`.

**Docs:** I4 `workflowScriptPath` persisted at Source-B declare (resume executable from
state); I5 honest-limits resume claim corrected; I6 demo artifact regenerated (3,232 bytes,
reproducible + idempotent); I7/M3/M4 trigger headroom restated like-for-like.

**Re-measured trigger path (tiktoken cl100k_base, 2026-07-19 post-fix):** raw **11,849**
(SKILL.md + source-a.md + file-formats.md + artifact-template.md + 1,096 fixture); as-read
(≈3.3–3.6 tok/line Read prefixes on the three reference files) **13,400–13,541** vs the
prefix-inclusive 14,000 budget — **459–600 to spare**. Standalone: source-b.md 2,210
(budget 2,500), source-c.md 921, resume.md 1,217.

**Suite after all fixes: 294 tests OK, warning-clean** (285 + 9 new).

**Post-review addendum (same day, pre-tag):** a live dogfood of this very fix run surfaced one
more bug — mid-run, per-task token lines in the artifact survived only for the latest completed
task (the per-wake `task_nr`-scoped token sidecar was overwritten, not merged; full list only
reappeared at the job-end refresh). Fixed in `finish_cycle` by merging the sidecar per task nr
(fail-soft on missing/corrupt sidecar). Tests:
`test_sidecar_merges_across_sequential_completions`,
`test_sidecar_merge_survives_corrupt_existing_file`. **Suite: 296 tests OK, warning-clean.**

## Stage-2 polish pass (post-v0.3.1, flip deferred) — 2026-07-19

Owner deferred the public flip (3-week absence) and asked for every known finding fixed,
including Minors. Executed task-by-task with per-task independent review; 10 tasks
(plan Tasks 10–17 + Stage-3 doc items 2/7/8 + this wrap-up).

**Script hardening (each TDD, red→green):** `sanitize()` strips C0/C1 controls incl. ESC
(`test_sanitize_strips_control_and_escape_chars`); calibration dir 0700 + log/archive/summary
0600 via fd-level tighten-before-write `_open_private`
(`test_log_and_dir_are_private`, `test_rotation_and_summary_outputs_are_private`); rendered
HTML + tokens sidecar 0600 incl. under no-publish
(`test_no_publish_render_output_is_0600`).

**Docs/prose:** resume publish-order fixed (RUNNING banner, both branches); nominal marker →
"(default band — little history)" across 9 consumer sites, demo regenerated (3,256 bytes,
idempotent); cold-start candor + defaults provenance (hand-set priors, confirmed via git
archaeology to ac86ba8); overhead honesty (wake-turn inference cost, cl100k caveat,
approximate-not-floors — the +40 % claim verified live: demo real 1,149 vs char/4 803 = +43 %);
public-prose hygiene (finding-codes glossed, Android untested stated, Workflow engine/Cowork/
pacekeeper glossed, no links into unshipped dirs); protocol gaps (B resumes key on wdTag,
canonical render CLI, artifactFile/Url write-backs); winsorize-bias note; trigger-figure
history moved to a design.md appendix; M19 trivia sweep with code-verified wording.

**Re-measured (tiktoken cl100k_base, post-pass):** trigger path raw **12,029**
(SKILL.md 3,402 + source-a 2,094 + file-formats 4,468 + artifact-template 966 + fixture 1,099);
as-read (479 ref-file lines × 3.3–3.6 prefix allowance) **13,610–13,753** vs the
prefix-inclusive 14,000 budget — **247–390 to spare**. Standalone: source-b 2,257,
source-c 980, resume.md 1,385. Fixture output 3,401 bytes / 3,330 chars, two-run identical.

**Suite after all fixes: 300 tests OK, warning-clean** (296 + 4 new).

## Windows verification pass — CI matrix + live dogfood — 2026-08-13

Two external evaluations arrived the same day: an independent reviewer's repo assessment, and
a Claude session's install test on the owner's Windows 11 machine reporting 5 test failures on
the v0.3.1 suite. All five were verified to be test-environment issues, not script bugs (three
symlink tests need a privilege Windows doesn't grant unelevated; one test set `HOME` where
Windows Python ≥3.8 `expanduser` reads `USERPROFILE`; one injected append-failure via
`/dev/null/impossible`, a creatable path on Windows, so the fault never injected). Fixed in
e0af8e9 together with a GitHub Actions CI matrix (ubuntu/macos/windows-latest, full suite with
`-W error::ResourceWarning`, push to main + PRs).

**CI's first run caught a real Windows-only product bug:** `_pid_alive` probed lock-holder
liveness with `os.kill(pid, 0)` — on Windows signal 0 is `CTRL_C_EVENT`, so stale-lock
recovery sent a console Ctrl-C instead of probing (in CI it interrupted the test runner
itself; live it could have interrupted the monitored session). Fixed in dafb75f with a
ctypes `OpenProcess`/`GetExitCodeProcess` probe (stdlib invariant holds); POSIX path
unchanged. **CI green on all three OSes since: 310 OK on Linux/macOS, 310 OK (skipped=6)
on Windows.**

**Live verification on real hardware (Windows 11 Pro build 26200, Python 3.13.15, repo at
dafb75f):** performed by a Claude session on the owner's Windows machine, running the
installed skill from a fresh clone (not a dev tree) — the first end-to-end run from a
clean install on any platform.

- **Suite:** 310 OK, skipped=6 — 3 symlink-containment tests (WinError 1314, no Developer
  Mode) + 3 POSIX-permission tests (`skipUnless(os.name == "posix")`, by design — octal
  modes don't map to NTFS ACLs).
- **Source-A dogfood (8 subtasks, TaskCreate/TaskUpdate harness):** artifact rendered and
  republished to the same URL at every subtask boundary (7 republishes, URL equality
  checked each time); per-task token column and running job total visible from the first
  completion onward; cold-start path clean (no calibration data existed — factor-1.0
  fallback, wide "default band — little history" interval that stayed honestly labeled
  while the ETA tightened ~19:46 → ~19:32 as trivially-fast tasks completed); full job-end
  sequence (final token table, DONE banner "took 7 m 49 s (estimated 43 m)", push
  notification, calibration regeneration) with zero degraded paths. This was also the
  first live-verified TaskCreate/TaskUpdate job on any platform (the e78471e support had
  only synthetic-fixture coverage before).
- **Stale-lock recovery drill:** the job's own live L1 watcher hard-killed
  (`Stop-Process -Force`), dead PID confirmed in the lock, identical relaunch took over
  cleanly — no `already-running` refusal, no delay, no Ctrl-C side effects anywhere
  (canary command in a separate console ran uninterrupted). The dafb75f fix verified on
  real hardware, not just CI.
- **Note:** the dogfood's 8 calibration rows are trivially fast verification steps
  (−70…−98 % deviations); the owner was advised to wipe that machine's
  `~/.claude/whendone-data/` before real use so the factors start unskewed.

**Still untested on Windows:** the three symlink-containment tests have never executed on
any Windows machine (need Developer Mode or elevation; the containment behavior they guard
is POSIX-verified). Sources B and C remain macOS-only verified. Suite at this entry:
**310 tests, warning-clean on all three CI OSes.**

## Time-attribution rework — protocol docs (Task 7) — 2026-08-14

Docs-only task following code Tasks 1–6 of the time-attribution rework (close authority moved
to the todo/TaskUpdate `completed` transition; delegated/lead-review split; `unconfirmed`
display closes; the `idle`/`publishLag` wake events; D6 project-root pinning). Updated
`references/source-a.md` (rawEstimateMin's full-lifecycle scope; the todo-transition close
authority replacing the old dispatch-naming-decides-closure text; the shipped reopen
contract — any in-flight matched dispatch reopens an `unconfirmed` task, not only the plan's
draft "no todo evidence yet" condition; the `idle`/`publishLag` wake-table rows; the D6
project-root definition at state-file-write time; one line on worktree removal ordering at
job end), `references/file-formats.md` (additive job fields `lastPublishedAt`/
`lastChangedEventAt`/`idleNotifiedAt` and per-task `delegatedMin`/`unconfirmed`, both pointed
at `references/formulas.md` for semantics; the `progress`/`all-done` event row gains
`publishLag?`/`sincePublishMin?`; new `idle` event row; a `delegatedMin` mention in the
calibration.jsonl section; the state-file Location line now points its "project root"
definition at SKILL.md/source-a.md instead of restating it), and `SKILL.md` (one pointer
sentence at the top of Job start resolving every `<project-root>` use in that section to the
D6 definition in source-a.md). `references/formulas.md` untouched — it already carries the
delegated-split/orchestration-line/unconfirmed semantics from the prior task; this pass only
points at it. The `idle`/`publishLag` events are Source A only (Source B never reads the
session transcript; Source C is mirror-only, never calibrated) — both new event-table rows
say so explicitly, matching the existing `journal-format-drift` (Source B only) precedent in
the same table.

**Suite: 361 tests OK, warning-clean** — unchanged (no scripts touched).

**Re-measured (tiktoken cl100k_base, throwaway venv, 2026-08-14):** trigger path raw
**10,558** (SKILL.md 3,318 + source-a.md 2,563 + file-formats.md 3,200 +
artifact-template.md 651 + calibration-summary allowance 826, fixture unchanged at 2,417
bytes); as-read (420 on-path reference lines × 3.3–3.6 prefix allowance) **11,944–12,070**
vs the prefix-inclusive 14,000 budget — **1,930–2,056 to spare** (was 9,785 raw /
≈11.0–11.2k as-read, ≈2.8–3.0k to spare, movement 8). Standalone reference files
(source-b.md, source-c.md, resume.md) untouched this pass, not re-measured.
