# Pacekeeper skill test log

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
