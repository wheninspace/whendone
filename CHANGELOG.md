# Changelog

## [0.7.0] — 2026-08-22

**v0.5.0 and v0.6.0 above were merged (2026-08-15 and 2026-08-16) but never tagged — the last
real git tag is v0.4.0, so v0.7.0 is the first tag candidate since then.**

Close-authority rework, third bug in the same family v0.5.0 and v0.6.0 fixed: a live run on
2026-08-22 hit a harness that shipped NO todo tool at all (TodoWrite, TaskCreate, and
TaskUpdate were all absent), which silently disabled Source A's entire confirmed-close
path — no calibration rows, no alias upgrade, all-done never fired — and the executor
hand-forced task status/`finishedAt` in the state file to get the job to end, tearing the
row's `actualMin` and dumping its real time into orchestration. Schema changes are additive;
pre-v0.7 state files stay valid.

- **Lead-written close-marker channel (todo-equivalent close authority):** a new
  `.claude/whendone-closes.jsonl`, sibling of the state file, gives the lead a way to confirm
  a close without any todo tool. The lead announces the protocol once, creates the file fresh
  at job start (gitignored, same as the state file), and appends one `{"task","status","ts"}`
  line — `status: "in_progress"` or `"completed"` — at each todo-transition-equivalent moment.
  `completed` lines carry the same authority as a todo `completed` transition; `in_progress`
  carries start authority, including reopening a task whose close is still unconfirmed
  (a confirmed close stays done). `tail_progress.py`'s
  `read_close_markers` reads the file fail-soft (capped at 1 MB / 10,000 lines, a stale guard
  against markers older than the job's own `startedAt`) — a malformed or missing file degrades
  to no marker evidence, never a crash or a wrong close.
- **Totals block restructure:** the artifact's summary now shows `Sum of subtasks` /
  `Between-subtask orchestration` (only rendered when it's ≥ 1 minute; replaces the old dim
  orchestration `<p>` line) / `Total` — and `Total` is defined to equal `elapsed_min`'s own
  endpoint, the same value the header's `took` and `Ended` line already show, so the three
  numbers reconcile by construction instead of by hoped-for arithmetic. Source C's totals
  block keeps only the sum row (no estimate, no orchestration split).
- **Clearer unconfirmed label:** the display-closed-but-not-yet-confirmed marker now reads
  "unconfirmed — closed on agent completion" (was "unconfirmed — closed on subagent result",
  which misattributed the close to the subagent rather than the lead's own completion evidence).
- **Skill hard rule:** `references/source-a.md` now states explicitly that the model never
  sets a task's `status`/`finishedAt` in the state file by hand, at any point including job
  end — a task closes only on todo/marker evidence; hand-forcing it is the exact failure mode
  this release fixes.
- **Docs:** `references/formulas.md` states the marker-authority rules (`in_progress` = start
  authority, `completed` = confirmed close) normatively; `references/file-formats.md` gains
  the closes-file mechanics entry; `references/source-a.md` gains the full no-todo-tool
  protocol (announce once, create the file fresh, append at todo-transition-equivalent
  moments). Trigger-path budget re-measured after the doc additions: raw 11,047 (+372 over the
  prior 10,675 measurement), ≈12,509–12,642 as read with Read-tool line prefixes, still under
  the 14,000 prefix-inclusive budget with 1,358–1,491 to spare — stamped in the README Overhead
  table and `docs/design.md`'s appendix (movement 12).

Suite: 408 tests, warning-clean. **Live-verified on macOS by a 2026-08-22 dogfood run** — a
real 10-subtask Source-A job on the exact no-todo-tool harness build this release's fix
reproduces in: confirmed marker-based closes mid-run, per-task actuals, and calibration rows
logged from it. The Windows re-run is still open; see
`docs/reviews/2026-08-22-v0.7.0-verification-checklist.md` and the README's Maturity section.

## [0.6.0] — 2026-08-16

Background-dispatch evidence rework: a live macOS job on 2026-08-16 exposed a second
close-authority bug in the same family v0.5.0 fixed. A background subagent dispatch's
`tool_result` is only a launch acknowledgement — it fires the instant the dispatch is
accepted, long before the subagent has done any work — but the tailer was treating it as
that task's finish, exactly the mistake v0.5.0 fixed for foreground dispatches. On the
observed run this closed a task's display two seconds after dispatch and ended the whole
job 85 minutes early, with a `delegatedMin` value that measured nothing but the launch
handshake. Schema changes are additive; pre-v0.6 state files stay valid.

- **Ack vs. completion, for background dispatches:** a background dispatch is now detected by
  a truthy `run_in_background` argument OR ack-shaped `tool_result` text, and its `tool_result`
  never ends the delegated span. The span now ends only on the subagent's `agent-done`
  completion notification, correlated back to its dispatch by `tool_use` id. A task that
  receives repeat completion notifications keeps extending its span — last notification wins.
- **Main-transcript-only event authority:** only the MAIN session's transcript file produces
  progress events; a subagent's own transcript file is read only to feed the staleness clock
  and per-task token counts, never to decide that anything closed.
- **`in_progress` reopens an unconfirmed close:** todo/TaskUpdate evidence of `in_progress` on
  a task already display-closed (but not yet todo-confirmed) reopens it, the same way a late
  dispatch already could.
- **All-done quiet-transcript grace:** the job's all-done state is now held while ANY task's
  close is still unconfirmed and the transcript has gone quiet, rather than requiring the
  confirming transition to land in the same watcher cycle as the last task's completion.
- **Alias→versioned model upgrade searches the whole window:** a bare dispatch alias now
  resolves against every model id observed in the task's token-usage window for a same-family
  match, not only the busiest one — previously the lead's own (usually busiest) usage on a
  delegated task could mask the subagent's actual model.
- **Hours rollover:** both duration formatters now roll a displayed 60+ minutes over to
  `Hh Mm` instead of showing three-digit minutes.
- **`Ended HH:MM` line** now prints at job end, alongside the existing "took" duration.
- **Delegated/lead split moved into the Actual column** of the task table, next to the actual
  duration it annotates, instead of a separate line.

**Calibration-data caveat (no reset needed):** calibration rows logged before this release
carry `delegatedMin` values that measured the background-dispatch launch handshake (0.0–0.2
min), not real delegated work — **ignore that field on pre-v0.6 rows.** This does not affect
accuracy: `delegatedMin` is display metadata only and is never read by any factor, ETA, or
interval computation, so no `factor`/`q1`/`q3` value was ever wrong because of it, and no
calibration file needs to be reset for this release (contrast v0.5.0, whose bug WAS in the
data the factors learn from).

## [0.5.0] — 2026-08-15

Time-attribution rework: a live job on 2026-08-14 lost 77 % of its wall-clock time and logged
9 calibration rows with −26…−77 % false deviations, because the tailer treated a subagent's
*result* as its task's finish — closing each task the moment its first implementer subagent
returned, before the lead's own review pass, fix rounds, and commit. Schema changes are
additive; pre-v0.5 state files stay valid.

- **Todo-close authority:** the todo/TaskUpdate `completed` transition is now the ONLY thing
  that closes a task. A subagent result never closes a task on its own; matching dispatches
  now accrue to the task's *delegated span* (display metadata only) instead.
- **Delegated-span display split:** the artifact now shows `delegated Xm · lead/review Ym`
  per task, computed from a new optional numeric `delegatedMin` calibration-row field
  (agent-minutes summed over matched dispatch→result spans; may exceed the task's own wall
  span under parallel dispatch).
- **Forgetful-model fallback:** a task whose todos are never touched still display-closes — on
  its last matched subagent result, marked `unconfirmed` — and logs NO calibration row (no
  data beats biased data). It upgrades to a confirmed close, and logs the row once, if todo
  evidence arrives later.
- **Between-subtask orchestration line:** the artifact now shows
  `max(0, elapsed − union of task spans)`, so minutes belonging to no task are visible instead
  of silently vanishing.
- **Publish-lag backstop (Source A only):** the tailer now watches the model's own `Artifact`
  publishes in the transcript and flags `publishLag: true` on a changed event emitted while
  the previous one is still unpublished.
- **`idle` event (Source A only):** job-level event, fires when no task is in flight while the
  job is running; anchored on the last task boundary rather than transcript activity, so
  active-but-off-plan drift fires too.
- **Worktree-pinned state location (docs only):** "project root" is now defined as the main
  working tree (parent of `git rev-parse --path-format=absolute --git-common-dir`), so
  deleting a linked worktree can no longer kill the watcher. Trade-off, documented: one
  WhenDone job per repo across all its worktrees.
- **Dispatch-model capture:** calibration rows no longer log `model: "unknown"` — the model
  field is now read from the dispatch tool's own input.

**Calibration-data epoch:** every Source-A row logged before this release measured delegated
spans only and is systematically fast against real wall-clock time. Recommended per-machine
step — you run this yourself; it touches data outside the repo, on your own machines, and the
release can't do it for you:

```bash
mv ~/.claude/whendone-data/calibration.jsonl ~/.claude/whendone-data/calibration.pre-v0.5.jsonl
```

then regenerate the summary so per-category factors restart at their 1.0 defaults.

Final pre-merge review additions:

- **Parallel-group rows wait for confirmation.** A `parallel-group` calibration row is now
  written only once EVERY member has a todo-evidenced close. Previously a member that upgraded
  from `unconfirmed` to confirmed made the group log a SECOND row for the same work, and an
  unconfirmed member's dispatch-derived span could set the row's `finishedAt` — with the
  outcome depending on whether the watcher happened to see the evidence in one cycle or two.
- **`idle` no longer counts pause time.** `resumedAt` is a co-equal anchor candidate rather
  than a fallback, so a resumed job no longer fires `idle` immediately with the whole pause
  reported as idle minutes (which also burned the L2 watcher ladder's single relaunch).
- **Docs:** the D6 project-root pinning is now applied to every runtime state/STOP path
  (SKILL.md job start + stop, source-a.md's job-end token refresh, resume.md);
  `lastChangedEventAt` is documented as written on every source; formulas.md gains the honest
  D0 caveat (a pause taken inside a task's span is counted twice) and the real dispatch-matching
  tolerance (case, whitespace, one leading ordinal).

**Known limitation:** if a job's tasks ALL close on the forgetful (dispatch-only) path, the
tailer still emits `all-done` and exits — a later `completed` todo is never observed, so that
job logs zero calibration rows. Deliberate: the alternative (withholding `all-done`) would hang
the job-end protocol, and the failure direction here is the safe one — no rows, never biased
rows.

Suite: 366 tests, warning-clean. Verified live on Windows 11 / Python 3.13 on 2026-08-15 — the
machine where the original bug was observed — covering the regression itself (a subagent result
no longer closes its task), full-lifecycle `actualMin` with `delegatedMin` alongside it, watcher
survival across deletion of the linked worktree it was started from, and the artifact's
delegated/lead split and orchestration line. Sources B and C were not re-dogfooded against this
release; their live evidence predates it (docs/test-log.md).

## [0.4.0] — 2026-08-14

Post-v0.3.1 polish pass (Stage-2 findings + doc backlog; flip deferred). Script behavior
changed only in hardening details — no schema breaks, no formula changes.

- **README: per-subtask model/token ledger surfaced early (docs only):** the "Do you need
  this?" section gains a "Delegation transparency" bullet (which model ran each subtask +
  per-subtask token consumption — the receipt for model-tiering strategies), and the
  statusline paragraph now names it as the third differentiator alongside the calibrated ETA
  and the off-machine link — previously this capability first appeared in "What you get",
  after the section where at-the-desk readers decide. Also: `docs/test-log.md` retitled from
  the pre-rename "Pacekeeper skill test log" to "WhenDone skill test log" (its naming note
  and verbatim historical `pacekeeper` run records stay — they document what was actually
  run). README/test-log are off the trigger path — no re-measure.
- **Trigger-path dedup pass (docs only, −734 raw tokens → 9,785 raw / ≈11.0–11.2k as-read):**
  removed third copies of rules whose normative homes already existed — file-formats.md's
  restated preconditions and concurrency/STOP/malformed-JSON rules now point to SKILL.md
  job-start steps 1-4/7 and design.md's Safety decisions; the calibration-row schema and
  field rules moved to maintainer-only `references/formulas.md` (the model never constructs
  rows — the tailer does); SKILL.md no longer carries the trigger-cost figures (README's
  Overhead table is their single home, one fewer re-stamp site). No behavior, schema, or
  script changes; every removal left a pointer. Measurement movement 8 in design.md's
  appendix.
- **Windows verified live; README claims re-scoped to evidence:** a full verification pass
  on real Windows 11 hardware (fresh-clone install, 8-subtask Source-A dogfood with
  TaskCreate/TaskUpdate, per-task token accounting confirmed working, stale-lock hard-kill
  recovery drill, full job-end sequence — recorded in docs/test-log.md). README updated
  accordingly: "every claim dogfooded live" re-scoped to "every feature claim is backed by
  a recorded live run" with the untested list pointed to explicitly; the "Windows honesty"
  install caveat replaced with the verified result and the one remaining gap (symlink
  containment tests never executed on Windows — need Developer Mode); Maturity section now
  names the CI matrix and the Windows evidence. README/test-log are off the trigger path —
  no token-budget re-measure needed.
- **Windows lock-liveness fix (found by the new CI on its first run):** `_pid_alive` used
  `os.kill(pid, 0)`, which on Windows is not a probe — signal 0 is `CTRL_C_EVENT`, so a
  tailer recovering from a crashed watcher's stale lock would send a console Ctrl-C
  (in CI it interrupted the test runner itself; live it could interrupt the monitored
  session). Windows now probes via `OpenProcess`/`GetExitCodeProcess` (ctypes, stdlib);
  the POSIX path is unchanged. Known edge: a Windows process that exited with code 259
  (STILL_ACTIVE) reads as alive — safe direction for a lock check.
- **Windows test portability** (found by an external Windows install test 2026-08-13; all
  five failures were test-environment issues, not script bugs): the three symlink
  containment tests now skip with a clear reason when symlink creation needs privilege
  (WinError 1314 — Windows without Developer Mode/admin) instead of erroring;
  `test_cli_task_flag` sets `USERPROFILE` alongside `HOME` (Windows Python ≥3.8
  `expanduser` ignores `HOME`, so the fake-home fixture never took effect there);
  `test_append_failure_still_leaves_task_done` injects append failure via a path beneath
  a regular file instead of `/dev/null/impossible`, which is a creatable path on Windows —
  there the append silently succeeded and the test asserted against the success path.
  Script behavior unchanged on all platforms.
- **CI:** GitHub Actions workflow (`.github/workflows/tests.yml`) runs the full suite with
  `-W error::ResourceWarning` on ubuntu/macos/windows-latest on every push to main and
  every PR — the matrix that would have caught the five Windows failures pre-publication.
- **Trigger-path slimming (−1.6k raw / −1.8k as-read):** the script-implemented formula spec
  (ETA/interval/slip rules, calibration-row derivation, renderer formatting guarantees) moved
  off the trigger path into the new maintainer-only `references/formulas.md`; pause accounting
  moved into `references/resume.md` (its only consumer); the calibration-summary footer now
  points at the single normative statement instead of restating the ~250-token interval rule
  (its machine-readable q1/q3 lines are unchanged — the renderer parses those itself, so no
  behavior changes; the footer's stale "default table in SKILL.md" pointer was also fixed to
  source-a.md). Trigger path 12,077 → 10,519 raw (≈13.7–13.8k → ≈11.9–12.0k as read); the
  synthetic fixture summary 3,401 → 2,417 bytes. No formula changes; the two footer-pinning
  tests now pin the pointer instead of the restated rule.
- **Trigger threshold lowered ~45 → ~30 min:** with the slimmer trigger path, whendone now
  accepts jobs down to ~30 min expected total (still ~6+ subtasks) — SKILL.md "When not to
  use" and the README's "worth it" line updated together.
- **README restructured for first-time readers** (fresh-eyes review findings): "Do you need
  this?" moved to the top and now leads with the strongest use case — the machine must shut
  down (internal-network work, end of day): does the job fit the time left, and stop cleanly
  at a subtask boundary if not — plus job-sizing and time-boxing ("don't start a new subtask
  after 16:45") as named secondary cases. Overhead compressed to a one-line-per-row table
  with per-row provenance and measurement history moved to docs/design.md's appendix;
  internal tracking IDs removed from user-facing text; Usage leads with the commands;
  Status/"Honest limits" consolidated into a Maturity section; hero caption cut to one line
  (asset provenance now a docs/design.md section).
- **TaskCreate/TaskUpdate support:** newer Claude Code harnesses replace TodoWrite with a
  TaskCreate/TaskUpdate/TaskList tool family (no list snapshots — incremental creates and
  status updates, with the assigned task id only visible in the TaskCreate tool_result
  text). `tail_progress.py` now synthesizes TodoWrite-equivalent snapshot events from those
  calls, so Sources A and C track such sessions unchanged; previously they sat at 0 %
  forever (found live 2026-08-13 when a whendone job ran in a TodoWrite-less harness).
  Binding rides the `Task #<id> created` result line with a creation-order fallback;
  unparseable or unknown updates degrade to fewer events, never wrong ones. Docs
  generalized "TodoWrite list" to "todo list" with the tool families named once per file.
- **Quieter honesty marker:** the ETA line's band marker ("widened to measured spread" /
  "default band — little history") now renders as dim small text after the bold headline
  instead of inside it — same words, less visual weight; the machine-readable etaText is
  unchanged.
- **Terminal-injection hardening (M7):** `calibration_summary.py`'s `sanitize()` now strips
  all C0/C1 control bytes incl. ESC — project/job strings from the jsonl can no longer carry
  ANSI/OSC sequences into summary or report output.
- **Private calibration data (M8):** `~/.claude/whendone-data/` is created 0700; the
  calibration log, archives, and generated summary are opened 0600 with the permission
  tightened on the file descriptor BEFORE any content is written (pre-existing looser files
  are tightened on first touch).
- **Private render output (M9):** the tailer's rendered HTML and its `.tokens.json` sidecar
  are 0600 — including under `publish: false`, where the page previously landed
  world-readable at a predictable temp path every wake. Rendering itself still never blocks.
- **Resume publishes the right banner (M6):** references/resume.md now flips the state to
  `running` BEFORE the rebuilt page is rendered and published — a resumed job's public page
  no longer shows PAUSED until the next task boundary.
- **Honest ETA-band marker (M11):** the artifact's nominal-band marker now reads
  "(default band — little history)" instead of the jargon "(nominal)"; all doc/test
  consumers updated; demo artifact regenerated (3,256 bytes).
- **Cold-start candor (M10):** README states plainly that a first-run point estimate can be
  off severalfold and the ±50 % band is a labeling convention, not a coverage guarantee;
  docs/design.md now records the default minutes' provenance honestly (hand-set priors from
  early development, no fitted data).
- **Overhead-section honesty (M1/M2/M5):** wake turns' own inference cost named as the
  dominant per-wake dollar cost for API-key users; all figures labeled as `cl100k_base`
  counts (~10–25 % below Claude's tokenizer on this kind of text); char/4 rows labeled
  approximate, not floors.
- **Public-prose hygiene (M12–M15 + cold-read pass):** internal finding-codes glossed at
  first use; Android status stated (untested); Cowork and the Workflow engine and the old
  `pacekeeper` working name each glossed; no markdown links into unshipped directories;
  unshipped internal docs labeled as such in the test log.
- **Protocol doc gaps (M16/M17):** resume no longer claims Source B re-runs a unique-NAME
  check (B keys on `wdTag`); canonical render CLI documented once; artifactFile/artifactUrl
  state write-backs made explicit in all three source protocols.
- **Doc backlog (Stage-3 items 2/7/8):** winsorize+clamp's −4…−17 % asymptotic undershoot
  acknowledged in design.md; the trigger-figure movement history moved out of the README
  into a design.md appendix; M19 trivia sweep (rotation wording matched to code, matcher
  tolerance note, checkpoint→wake vocabulary, fixture figures re-measured live).
- Trigger path re-measured after the on-path edits: **raw 12,029 / as-read ≈13.6–13.8k**
  vs the prefix-inclusive 14,000 budget (≈0.2–0.4k spare). Suite: **300 tests** (296 + 4
  new: sanitize, calibration perms ×2, render-output perms), warning-clean.

## v0.3.1 — 2026-07-19

Pre-flip adversarial-review fixes (8-persona audit, findings in the internal review record).
Script behavior changed — patch release, no schema breaks (all new state fields additive).

- **STOP-file control re-homed (C1):** v0.3.0's pivot deleted the checkpoint loop that
  polled `.claude/STOP` without re-homing the check — the file was advertised (artifact
  footer, README) but dead. The tailer now emits a `stop-requested` event when the file
  exists; the model runs the Stop procedure on that wake. The tailer never deletes the file.
- **Source-B status guard (I1):** `sync_cycle_b` now no-ops when `status` isn't `running` —
  a paused Source-B job could previously keep processing and append calibration rows.
- **Finalize idempotency (I2):** per-task `bFinalized` flag (additive), written atomically
  with the done-marker — closes a crash window that could append duplicate calibration rows
  (v0.3.0's "never re-appended" held only for phases with `actualMin` set).
- **One-shot lock respect (I3):** an L3 one-shot now yields (`already-running`, rc 4) to a
  LIVE lock holder instead of double-writing state and calibration beside it.
- **Source-B resume made executable from state (I4):** new additive `workflowScriptPath`
  state field written at declare; resume.md/source-b.md route through it.
- README truth pass: honest-limits resume-drill claim corrected (both drills ran
  2026-07-19); demo artifact regenerated to the current renderer; trigger-path headroom
  restated like-for-like (raw 11,849 / as-read ≈13.4–13.5k vs the prefix-inclusive 14k budget).
- **Per-task token lines persist mid-run:** the per-wake token sidecar is now merged by
  task instead of overwritten — previously only the latest completed task showed its
  token count until the job-end full refresh.

## v0.3.0 — 2026-07-19

The pivot release (stages 2-5 of docs/specs/2026-07-18-whendone-pivot-design.md): whendone
moves from per-checkpoint hand-editing to declare-once/tail-thereafter, with three progress
sources behind one tailer, one renderer, and one artifact URL. First tagged release since
v0.2.1.

### Stage 2 — output layer

- `scripts/render_artifact.py` (+ tests): renders the FULL artifact page from
  `whendone-state.json` + `token_usage.py` output. ETA, interval (incl. the envelope
  widening and nominal/widened markers), deviation, and the 150 %-slip check are now
  computed in code from the fixed rules in `references/file-formats.md` — no longer
  model-executed prose (F6; makes the F1 bug class structurally impossible).
  `html.escape` on every interpolated field, atomic write, fail-soft (non-zero exit,
  no partial HTML → in-chat table fallback). Reads `calibration-summary.md` itself,
  so the category factor no longer enters model context at checkpoints.
- State-model v2 (additive): top-level `source` (`"a"`/`"b"`/`"c"`) and per-task
  `group` (parallel groups, MAX-aggregated everywhere). Pre-v2 state files stay valid.
- `assets/demo-state.json` — the demo artifact is now generated by the render script.
- SKILL.md job start/checkpoint/stop/resume/job-end publish via `render_artifact.py`;
  the model consumes the script's JSON status line (`etaText`, `slipAlert`,
  `estimateTotalMin`) instead of computing formulas. `references/artifact-template.md`
  collapsed to publish mechanics + guarantees (skeleton lives in the script).
  Interval/slip formulas are now stated once in `references/file-formats.md` (still
  pinned in the `calibration_summary.py` footer) and implemented in code.

### Stage 3 — watcher + Source A conversion

- **Declare-once, tail-thereafter:** new `scripts/tail_progress.py` (stdlib-only) observes
  TodoWrite transitions and subagent completions from the session transcript, owns state
  transitions, calibration appends (incl. synthetic parallel-group rows), token refresh, and
  rendering; the model's per-boundary work is one artifact publish on wake. The manual
  checkpoint protocol (1a–1d) and its alias-upgrade guard moved into the script; `checkpoint.py`
  (round-3 F7) confirmed superseded, never built.
- **Watcher fallback ladder:** Monitor (`--follow`) → background Bash (`--follow
  --exit-on-event`) → boundary-driven one-shot; pid lockfile against duplicate tailers;
  per-cycle jobId ownership check; debounce coalescing.
- **Liveness alerting (F13):** `stale` event + one push per hung task (default 10 min,
  `staleAfterMin`).
- **SKILL.md split (F16 absorbed):** thin core + `references/source-a.md` (+ B/C stubs);
  measured sizes in README. Resume rebaseline documented (F9); checkpoint-gap question moot
  under tailing (F14, see docs/design.md).
- **State-model v3 (additive):** `staleAfterMin`, `pushStatus`, `client`, per-task
  `staleNotifiedAt`; tailer event-line schema in `references/file-formats.md`.
- New/changed script interfaces: `tail_progress.py` (new), `token_usage.transcript_paths`,
  `append_calibration.append_obj` (extractions, behavior unchanged).

### Stage 4 — Source-B (Workflow-engine) ingestion

- **Source-B ingester:** `scripts/workflow_journal.py` (new) plus a tail seam in
  `tail_progress.py` reads a Workflow run's `journal.jsonl` (started/result pairs),
  per-agent transcripts, and `meta.json` — timestamps and model ids always come from the
  agent transcript, never the journal — and maps them onto the same state model Source A
  uses. Defensive v2 journal-format version-detect (the `v2:` key prefix) plus fixtures and
  tests guard against undocumented, unstable upstream schema drift; a drift condition
  degrades to job-wide agent counting with phase attribution stopped, notified once per job
  (persisted `wfDriftNotified` flag, survives watcher restarts).
- **`[wd:<slug>]` prompt-tag attribution:** the lead tags every `agent()` prompt at
  authoring time; the tag is the only phase signal that reaches disk. An untagged agent is
  still counted job-wide but attributed to no phase.
- **Per-phase calibration at the completion record:** one calibration row per phase,
  appended only when the run's completion record lands on disk — span =
  min(start)/max(end) over that phase's attributed agents, `model` from the agent's
  `meta.json` when uniform across the phase (else `"unknown"`). A phase whose span was
  never observed gets no row, never an invented one; phases already `done` are never
  re-finalized or re-appended, including across a resume.
- **Agent-counter rendering (additive):** per-phase `agentsStarted`/`agentsDone` and
  job-level `wfAgentsStarted`/`wfAgentsDone` render alongside the existing task table;
  ignored entirely by sources A and C, pre-existing state files stay valid without them.
- **Workflow-transcript token counting:** `token_usage.py` now counts workflow agent
  transcripts into the job's token totals.
- **`_render_out_path` hardening:** `artifactFile` is now validated, not trusted, before
  the render script writes to it — defense-in-depth, fails soft on a bad path.
- **Measured per-wake figure:** the README's per-watcher-wake overhead row is now a
  MEASURED figure (live L1 Monitor `--follow` observation during the stage-4 dogfood,
  2026-07-18) rather than the earlier component estimate — see the Overhead table.

### Stage 5 — Source C (pace-only) + release

- **Source-C ingester:** `tail_progress.py` now mirrors the session's TodoWrite list into
  the state file (`source: "c"`) — no declared plan, no estimates; the artifact shows a
  pace-based ETA always labeled "(uncalibrated)". Statuses are revertible mirror semantics;
  items added/removed/renamed mid-job flow through. **No calibration rows are ever written
  from Source-C jobs** — guarded in code, pinned by tests.
- **Defense-in-depth containment:** `workflow_journal.py` leaf readers now realpath-contain
  reads to the run directory (second layer behind the existing upstream validation).
- **README positioning rewrite:** work-time estimation leads; honest "terminal users may not
  need this" section; three-source table; stale pre-pivot claims removed (the per-checkpoint
  re-read row, the four-script count); single dated per-wake figure.
- `references/source-c.md` stub replaced by the full Source-C protocol; SKILL.md source
  detection flips Source C to shipped.

### Pre-flip consolidated review — fix pass (2026-07-19)

An independent whole-picture audit (protocol coherence, cross-references, docs-vs-code,
re-measured numbers, security posture) found no code or measurement defects; all findings
were doc coherence/staleness, fixed in one pass:

- SKILL.md no longer calls Source A "the only source shipped today"; the
  `unsupported-source` event row no longer says "Source C ships later" (both leftovers
  from before the stage-5 flip).
- README's per-wake row now labels the "IDE panel open → full echo" causation as the
  best-fit explanation (matching the test-log and forensics wording), not proven fact;
  the measured walk-away/watching split is unchanged.
- `references/resume.md` now routes Source-B resumes through source-b.md's Workflow
  relaunch (`resumeFromRunId`, runId comparison) and exempts Source C from
  classify/estimate — previously the resume flow only cited source-a.md.
- SKILL.md job-start step 8 no longer tells the model to invent a task list for
  Source-C jobs; error table and notifications rows de-Source-A'd; stale pointers
  repaired (estimate-table location, Watcher-ladder attribution, token temp file spec,
  artifact `<title>` prefix, redundant gitignore "extend" phrasing).
- Overhead table re-stamped from fresh `tiktoken cl100k_base` measurements: trigger path
  11,713 (fix pass added +74 over the 11,639 slimming measurement), source-b.md 2,067
  (was 1,810 pre-B12-fixes), source-c.md 921, resume.md 1,211; proxy-vs-measurement
  framing corrected; stale state-file/fixture sizes updated (fixture now 3,377 chars,
  deterministic across two runs).

## v0.2.1 — 2026-07-18

Doc/protocol correctness fixes plus one additive script feature, closed after a third
(round-3) adversarial review. No new scripts, no jsonl schema change.

- **Slip-formula symmetry (F1, headline correctness fix):** the slip check is now
  symmetric — running early no longer masks the same magnitude of running late, so the
  headline notification can't false-negative on the fan-out case where early and late
  subtasks offset each other.
- **No-publish control (F3):** a `<project>/.claude/whendone-no-publish` marker file and
  an optional `"publish": false` state-file flag both suppress the Artifact publish and
  fall back to the in-chat table — for NDA/confidential-repo use. The state-file field is
  optional and additive; old state files without it remain valid and publish as before.
- **Constant artifact description (F8):** the Artifact tool's `description` parameter is
  now pinned to a fixed, non-identifying string; job/project/subtask text is never
  interpolated into it.
- **ETA interval envelope-widening (F2):** the flat low/medium-confidence band is now
  clamped so it's never tighter than the observed IQR once one is available (n≥5); the
  rendered artifact carries a visible "nominal" / widened-band marker so the viewer is
  never shown an unqualified interval narrower than the tool's own data. README adds a
  ramp note on how the interval matures from flat-nominal to empirical.
- **Project-mix caveat (F10, advisory):** the calibration summary now surfaces a caveat
  when a category's factor is pooled across projects of noticeably different size/shape.
- **Token-cost doc corrections and install-tag reconcile (F4, F5):** corrected
  token-usage figures in the docs and reconciled the install-tag references.

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
