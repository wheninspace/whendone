# WhenDone Pivot — Design Spec

**Date:** 2026-07-18
**Status:** Approved (brainstorming pass, all sections user-approved)
**Supersedes:** the direction portions of [2026-07-17 pivot handoff](../plans/archive/2026-07-17-whendone-pivot-and-round3-handoff.md); partially supersedes [round-3 fixes plan](../plans/archive/2026-07-17-round3-fixes.md) (see §8)
**Next step:** stage plans via `superpowers:writing-plans`, one per stage in §7, executed in fresh sessions

---

## 1. The need (pinned)

**A calibrated finish-time ETA + shareable live progress for a long, unattended Claude Code job, viewable off the terminal / on another device.**

That single sentence governs scope. The ETA (calibrated finish time) leads; live progress visibility supports it; non-terminal surfaces (VSCode extension, Claude desktop app, a phone via the artifact link) are the target. Anything not serving this need is out of scope.

### Why this need is unserved (verified 2026-07-17/18)

- The Workflow `/workflows` live phase-tree is effectively terminal-only. The VSCode extension's progress UI is undocumented; the desktop app has only a coarse background-tasks pane.
- Statuslines are terminal-only, so statusline tools (e.g. claude-hud) cannot serve VSCode-extension or desktop-app users.
- Of the three ways a long job runs, Workflow-engine execution is the least visible on non-terminal surfaces — and it is the method heavy users should be using more.

## 2. Scope

### In scope (first public version)

Three progress **sources**, one shareable artifact:

| Source | Execution style | ETA quality |
|---|---|---|
| **A** | Lead-model / subagent-driven (today's mode, re-plumbed) | Calibrated (per-subtask estimates + categories) |
| **B** | Workflow-engine runs (new) | Calibrated (per-phase estimates + categories, declared at launch) |
| **C** | Plain solo / TodoWrite jobs (new) | Pace-based only, visibly labeled **uncalibrated** |

One consumption mode: the shareable claude.ai artifact serves both "glance at my own phone" and "share with a teammate". The in-chat table remains the documented fallback (and the whole-job mode under no-publish), not a separate product surface.

### Out of scope

- Terminal-first features (statusline rendering, terminal UI). Terminal users are addressed by honest README positioning only.
- Any consumption surface beyond the artifact + chat-table fallback.
- Building on gated artifact runtime capabilities (live-data pages). The artifact stays a static re-published page.

### Release gate (changed from v0.2.0 plan)

The public flip requires Sources **A and B both working**. Source C and the positioning rewrite also precede the flip per §7 staging. No git push until the gate.

## 3. Positioning (README)

- Lead with **work-time estimation** — the calibrated ETA is the headline; the shareable live page is the delivery vehicle.
- State plainly that terminal users have statuslines/claude-hud and `/workflows`, and may not need whendone — unless they want a calibrated ETA or a link that leaves the machine (the walk-away case).
- This absorbs round-3 F11 (break-even driver), F12 (statusline alternative), and F13's positioning half (crisp niche) as a rewrite, not patches to the old text.

## 4. Architecture

**Pluggable ingesters → one job-state model → one renderer → one artifact URL**, with an out-of-band watcher providing refresh and liveness.

### 4.1 Job-state model (v2)

`whendone-state.json` extended to represent all three sources: declared plan (subtasks or phases) with per-item estimate + category, per-item status/timestamps, parallel structure, source type (`a`/`b`/`c`), publish flag. Schema changes are **additive**: old state files without new fields remain valid.

### 4.2 `render_artifact.py` (round-3 F6)

`scripts/render_artifact.py <state.json> <token.json> <out.html> --now <ISO>` — stdlib only. Writes the **full** HTML page from a fixed skeleton:

- `html.escape()` on every interpolated field (retain "untrusted strings in text nodes only" in the skeleton spec as defense-in-depth).
- ETA, interval, deviation, and the **slip check** computed in code from the file-formats formulas — moving the slip formula out of model-executed prose kills the F1 bug class permanently.
- Reads `calibration-summary.md` itself for factor/q1/q3 (the factor never enters model context).
- Same output path → same claude.ai URL (verified: URL identity depends only on the path).
- On failure: non-zero exit, no partial HTML; the model falls back to the in-chat table.

### 4.3 Ingesters

- **Source A — transcript/TodoWrite tailer.** Marks declared subtasks done from the session transcript's TodoWrite status changes and subagent completions. The lead no longer hand-executes per-boundary state edits: the 1a–1d checkpoint mechanics — and the proposed `checkpoint.py` (round-3 F7) — are **superseded**.
- **Source B — Workflow journal tailer.** Tails the engine's `journal.jsonl` (started/result events per agent, phase structure), maps phases/agents → state. The format is **undocumented and unstable** (docs-verified silent); the ingester parses defensively, in the same posture `token_usage.py` takes toward transcripts.
- **Source C — same tailer as A**, with no declared plan; progress is item completion counts.

### 4.4 Watcher

Fallback ladder, each level failing soft to the next:

1. **Monitor tool** watches the journal/transcript for change and staleness.
2. **Background Bash poller** (`run_in_background`) where Monitor is unavailable (not on Bedrock / GCP Agent Platform / Foundry).
3. **Boundary-driven refresh only** (no watcher): artifact refreshes when the model naturally takes a turn.

On change → the model wakes → runs `render_artifact.py` + one Artifact republish. This is a **cheap-touch** loop, not zero-token: only the model can call the Artifact tool (docs-verified; the published page cannot fetch anything itself). No protocol re-read is involved in a wake.

On staleness past a threshold → liveness alert / push notification — closing **F13**, the hung-subtask gap (no progress event for N minutes on an in-flight item ⇒ alert, once).

Refresh frequency is capped by a staleness threshold (order of minutes, not seconds); the exact threshold and measured per-wake token cost are set in the stage-3 plan from measurements, not asserted here.

### 4.5 Skill loading (the slimming mechanism)

Verified: a skill's SKILL.md body loads atomically on invocation; there is **no** conditional sub-section loading. The only progressive-loading form is file splitting, which is what we do:

- **Thin SKILL.md core:** trigger, source detection (which of A/B/C is in play), the declare-once step, watcher setup, stop/pause/resume, invariants.
- **Per-source reference files** read on demand: `references/source-a.md`, `references/source-b.md`, `references/source-c.md`, plus the existing `file-formats.md` and a collapsed artifact skeleton spec.
- A session running one method never loads the other methods' protocol files.

Concrete size targets are set in the stage-3 plan against tiktoken measurements (per round-3 F4's method), not guessed here. Directionally: the checkpoint hot-path prose (~12.5k B) largely disappears with the protocol itself; rationale prose moves to `docs/design.md` (F16, absorbed by the rewrite).

## 5. Data flow & calibration (declare-once, tail-thereafter)

### 5.1 Declaration (once, at job start)

- **Source A:** the lead declares plan items with per-subtask estimate + category + parallel structure.
- **Source B:** the lead declares per-**phase** estimates + categories at Workflow launch — it authored the script, so it knows `meta.phases`, agent counts, and fan-out shape.
- **Source C:** no declaration. Pace-based ETA from item completion rate, visibly labeled uncalibrated in the artifact. **No calibration rows are written** from Source C jobs.

### 5.2 Tailing (thereafter)

The watcher/tailer updates state; the lead does **no per-boundary protocol work**. When the tailer script observes an item completing (during a watcher wake or a boundary refresh), the same script appends the calibration row from the state transition, using timestamps from the tailed source — the model neither computes nor writes rows. The invariant that the model never reads `calibration.jsonl` is preserved — scripts write and summarize; the model sees only `calibration-summary.md` (and after F6, mostly not even that: the renderer reads it in-process).

### 5.3 Calibration rules

- Factor / interval / confidence rules carry over unchanged, with the round-3 honesty fixes: **F2 clamp** (flat medium/low band never tighter than the shown IQR from n≥5; visible "nominal" qualifier reaching the artifact viewer; pure flat bounds only for n<5) and **F10** (project-mix caveat, advisory, sanitized project names, blank projects skipped).
- The slip alert uses the **F1-corrected symmetric aggregation** (sequential sum + MAX per parallel group on BOTH sides) — stated once in `file-formats.md` and implemented in `render_artifact.py`.
- Source B categories are the declared per-phase categories; per-agent journal events give real per-member timestamps, so Workflow parallelism is measured, not hand-tracked.
- Resume rebaseline (round-3 F9): on a resume that restructures the plan, `originalTotalMin` is recomputed over the new task set with the same aggregation, noted in chat (option (a) of the fixes plan).

### 5.4 Parallelism

For Source B, the under-specified hand-tracked parallel-group state machine (F15) largely dissolves — the journal provides per-agent started/result events. The declared-plan path (Source A) keeps a much thinner parallel rule: groups exist in the declared plan; member completion comes from the tailer; the synthetic group calibration row logic moves script-side with the rest of the append path.

## 6. Error handling, invariants, egress

### 6.1 Fail-soft chain (visibility never blocks work)

Watcher fails → boundary-driven refresh. Renderer fails → in-chat table. Journal schema drifts → Source B degrades to "agents counted, phases unknown" or marks itself unavailable. Tailer can't find the transcript → the declared plan still renders with declared estimates and no live updates, stated on the page. **No component may ever wedge the job.**

### 6.2 Invariants (all carried forward)

- Shipped scripts: Python 3 **stdlib only**, no network.
- The model never reads `calibration.jsonl` into context.
- **Only the published artifact leaves the machine.**
- Forbidden-string set (as defined in the round-3 fixes plan's Global Constraints) stays absent from tracked files. (Sweep at every stage's final task.)
- Existing tests stay green (91 today); every new/changed script ships with tests.
- No git push; public flip gated per §2.
- Commit trailer: `Co-Authored-By: Claude <noreply@anthropic.com>`.

### 6.3 Egress controls (round-3 F3 + F8, land in stage 1)

- Persistent per-project no-publish: marker file `<project>/.claude/whendone-no-publish` **or** state-file `"publish": false`, checked before the first publish; whole job routes through the chat-table-only path. Marker is the default-carrier (a fresh job has no state file). Read-only existence check; fail-safe (a stray marker only suppresses an artifact).
- Artifact `description` pinned to a fixed non-identifying constant; "description/subtitle" added to the sensitivity-check enumeration.

## 7. Staging

One design doc (this one) → sequential implementation plans, one per stage, each written with `superpowers:writing-plans` and executed in a fresh session. Each stage lands green (tests + forbidden-string sweep) and committed before the next begins.

| Stage | Contents | Round-3 items riding along |
|---|---|---|
| **1. Correctness & egress now** | Doc/small-script fixes independent of the pivot's architecture | F1 (slip symmetry, doc fix now; formula later moves into the renderer), F3 + F8 (egress), F2-partial (clamp + nominal marker), F4/F5 (token-cost doc corrections), F10 (project-mix caveat) |
| **2. Output layer** | `render_artifact.py` + tests, state-model v2 schema, artifact-template collapse | F6 |
| **3. Watcher + Source A conversion** | Monitor/poller fallback ladder, transcript/TodoWrite tailer, declare-once protocol rewrite, SKILL.md split into thin core + per-source refs, liveness alerting, measured size/token targets | F13 (liveness), F15 (thin declared-plan parallel rule), F16 (absorbed by the rewrite), F9 (resume rebaseline), F14 (moot or documented in the new protocol) |
| **4. Source B** | Workflow journal ingester + per-phase calibration + defensive-format tests | — |
| **5. Source C + release** | Pace-only ingester, README/positioning rewrite, CHANGELOG, tag, flip-gate check | F11, F12, F13-positioning |

Stage 1 is deliberately pivot-independent: if the pivot's pace slips, the repo is still correct and honest.

## 8. Superseded / archived

- **`checkpoint.py` (F7): not built.** Stage 3 removes the per-boundary mechanics it would have automated.
- **[2026-07-17-round3-fixes.md](../plans/archive/2026-07-17-round3-fixes.md):** its Tasks 1–3, 8, 9, 11 map to stage 1 (and parts of 3); Tasks 4, 6, 7, 12 are absorbed into stages 2–5 in redesigned form; Task 5 is dropped. Once the stage-1 plan is written, propose moving the old plan to `docs/plans/archive/` (never delete unasked).
- The **2026-07-17 pivot handoff** is fulfilled by this spec; it remains as the decision record.

## 9. Risks (named honestly)

1. **`journal.jsonl` is an undocumented internal format** — the top external risk. Source B version-detects and degrades; its tests encode the format assumptions so drift is caught loudly.
2. **Monitor tool availability varies** (absent on Bedrock/GCP/Foundry; older CC versions) — hence the fallback ladder.
3. **Watcher wake-ups cost real tokens** — each refresh is a model turn. Mitigated by the staleness cap and measured (not asserted) per-wake cost; if measurements are bad, the threshold rises or the watcher demotes to liveness-only.
4. **Identity dilution** ("progress layer for everything") — bounded by §1's pinned need; features are tested against that sentence.
5. **Transcript/TodoWrite tailing is also format-tailing** — same class of risk as (1), same defensive posture; already proven viable by `token_usage.py`.

## 10. Verified mechanism facts this design rests on (2026-07-18, official docs)

- Skill bodies load atomically; progressive loading = file splitting only; frontmatter descriptions are always in context.
- Only the model can update a published artifact (same path → same URL); the page cannot fetch data itself (CSP).
- Monitor delivers file/command output lines as notifications that re-engage the model; background Bash persists across turns within a session.
- Workflow journal format and VSCode-extension progress UI are undocumented; desktop app has a coarse tasks pane; statuslines are terminal-only.
