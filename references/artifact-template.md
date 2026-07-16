# Pacekeeper artifact template

Write the artifact HTML to a file in the session scratchpad (e.g. `pacekeeper-<job-name>.html`)
and publish with the Artifact tool. Reuse the SAME file path at every checkpoint during the
session (→ same URL). On resume in a NEW session: pass `url` from pacekeeper-state.json to the
Artifact tool to update the existing artifact. Favicon: `⏱️` — keep it identical across all
updates. `<title>`: the job's name.

**Escaping (hard rule):** HTML-escape every interpolated string — job name, project name,
subtask names, plan-file path (`&` → `&amp;`, `<` → `&lt;`, `>` → `&gt;`, `"` → `&quot;`).
Subtask names may originate from untrusted plan files; they must never be able to inject markup
into the published page.

**Republishing:** at checkpoints, copy the previous artifact file and substitute only the
variable parts (banner, "last updated", ETA block, table rows) — do not regenerate the whole
page freehand from this template each time (token cost and drift risk).

Content requirements (top to bottom):

1. **Status banner:** RUNNING (blue) / PAUSED (yellow) / DONE (green) + **"Last updated HH:MM"**
   in large type — the reader must see the view updates per checkpoint, not live.
2. **ETA block:** "Done ~HH:MM ± N min" (NEVER a point time without an interval), start time,
   elapsed time, N of M subtasks done.
3. **Task table:** one row per subtask: status icon (✅/🔄/⬜), name, category, estimate, actual,
   deviation baked into the actual column: "11 m (+38 %)". **The actual column is always a
   computed time, never a status word** — correct: `"4 m (−33 %)"` for a finished subtask and
   `"—"` for an unfinished one; wrong: `"done"` (the icon already shows status). Wrap the
   `time (±%)` figure in `<span class="dev">…</span>` so it never breaks mid-figure; put any
   explanatory note (e.g. "mostly dashboard time; not counted") as plain text AFTER the span so
   it is free to wrap onto its own lines. The table is fixed-layout: the Subtask column absorbs
   the remaining width, so long subtask names stay readable and never collapse to one word per
   line.
4. **When PAUSED:** a box with the exact resume instruction (project, plan file, next subtask,
   and that a new session finds the state via `.claude/pacekeeper-state.json`).
5. **Footer:** how to stop: "Type 'stop after the current subtask' in the chat, or create the
   file `.claude/STOP` in the project root." Plus honest notification status ("Push
   notifications: via Remote Control" when RC is active, otherwise "Push notifications:
   uncertain delivery — requires Remote Control", or "unavailable in this environment" if the
   tool is missing).

Skeleton (adapt content, keep structure and theme handling):

```html
<title>Pacekeeper: JOB NAME</title>
<style>
  :root { --bg:#fff; --fg:#1a1a1a; --dim:#666; --card:#f5f5f5; --link:#1257b0;
          --running:#1e6fd9; --paused:#a66800; --done:#1e8a3c; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#1a1a1a; --fg:#eee; --dim:#999; --card:#2a2a2a; --link:#6db3ff; } }
  :root[data-theme="dark"] { --bg:#1a1a1a; --fg:#eee; --dim:#999; --card:#2a2a2a; --link:#6db3ff; }
  :root[data-theme="light"] { --bg:#fff; --fg:#1a1a1a; --dim:#666; --card:#f5f5f5; --link:#1257b0; }
  body { background:var(--bg); color:var(--fg); font:16px/1.5 system-ui,sans-serif;
         max-width:640px; margin:0 auto; padding:16px; }
  a { color:var(--link); }
  .banner { padding:12px 16px; border-radius:8px; color:#fff; font-weight:600; }
  .banner.running{background:var(--running)} .banner.paused{background:var(--paused)}
  .banner.done{background:var(--done)}
  .banner a { color:#fff; }                                    /* solid banner bg only */
  .eta { background:var(--card); border-radius:8px; padding:12px 16px; margin:12px 0; }
  .eta strong { font-size:1.3em; }
  table { width:100%; border-collapse:collapse; table-layout:fixed; }
  td,th { padding:6px 8px; text-align:left; border-bottom:1px solid var(--card);
          overflow-wrap:anywhere; }
  th:nth-child(1),td:nth-child(1) { width:2em; }               /* status icon */
  th:nth-child(2),td:nth-child(2) { width:auto; }              /* subtask — takes the slack */
  th:nth-child(3),td:nth-child(3) { width:18%; }               /* category */
  th:nth-child(4),td:nth-child(4) { width:4em; white-space:nowrap; } /* est. */
  th:nth-child(5),td:nth-child(5) { width:26%; }               /* actual (+ optional note) */
  td:nth-child(5) .dev { white-space:nowrap; }                 /* keep "4 m (−33 %)" intact */
  .dim { color:var(--dim); font-size:.9em; }
  .pause-box { border:2px solid var(--paused); border-radius:8px; padding:12px 16px; margin:12px 0; }
</style>
<div class="banner running">🔄 RUNNING — last updated 14:35</div>
<div class="eta"><strong>Done ~16:40 ± 35 min</strong><br>
  <span class="dim">Started 14:02 · 33 min elapsed · 2 of 6 subtasks done</span></div>
<table>
  <tr><th></th><th>Subtask</th><th class="dim">Category</th><th>Est.</th><th>Actual</th></tr>
  <tr><td>✅</td><td>Failing test read_skill</td><td class="dim">testing</td><td>8 m</td><td><span class="dev">11 m (+38 %)</span></td></tr>
  <tr><td>🔄</td><td>Implement read_skill</td><td class="dim">judgment-coding</td><td>15 m</td><td>—</td></tr>
  <tr><td>⬜</td><td>Cost cap per turn</td><td class="dim">judgment-coding</td><td>20 m</td><td>—</td></tr>
</table>
<p class="dim">Stop: type "stop after the current subtask" in the chat or create the file
<code>.claude/STOP</code> in the project root. Push notifications: on.</p>
```

Keep the artifact to ONE compact page — no growing per-checkpoint history (token overhead at
every republish).
