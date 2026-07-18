# Pacekeeper skill test log

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
