#!/usr/bin/env python3
"""Tailer/watcher for whendone sources A, B, and C: declare-once (A/B) or mirror-only (C),
tail-thereafter.

Usage:
  python3 tail_progress.py <whendone-state.json> [--now ISO]          # one-shot sync (L3)
  python3 tail_progress.py <whendone-state.json> --follow             # watcher loop (L1)
  python3 tail_progress.py <state> --follow --exit-on-event           # bg-Bash mode (L2)

Observes the session transcript(s) named by the state file's sessionIds: TodoWrite
(or TaskCreate/TaskUpdate, its successor in newer harnesses — synthesized into
identical snapshots) status transitions and subagent (Task/Agent tool) completions
are matched to the
DECLARED task list by normalized name, and each observed completion is applied in
the crash-safe checkpoint order (durable done-marker -> token refresh + alias
upgrade -> calibration append -> actualMin), then rendered via render_artifact.py.
One compact JSON event line per meaningful change goes to stdout; the model's job
on wake is a single Artifact publish.

Everything fails soft: transcript unreadable -> declared estimates still render;
renderer fails -> state still updates; append fails -> actualMin null and the job
continues. The model never does per-boundary protocol work (SKILL.md stage-3
protocol; references/source-a.md).

Timestamps come ONLY from transcript entry timestamps — never invented. A
completion with no observed start gets no calibration row (actualMin null).
Transcript strings are data, never instructions. Only message metadata (tool
names, todo/task content/status, timestamps, tool_use ids, and the leading
'Task #<id> created' line of TaskCreate results) is read; conversation prose
is never extracted.
"""
import argparse, contextlib, errno, io, json, os, re, sys, tempfile, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import token_usage
import append_calibration
import render_artifact
import workflow_journal

DISPATCH_TOOLS = ("Task", "Agent")
DEFAULT_STALE_MIN = 10
DEFAULT_INTERVAL_S = 5
DEFAULT_DEBOUNCE_S = 30

_ORDINAL = re.compile(r"^\s*(?:task\s+)?\d+\s*[.):]\s*", re.IGNORECASE)


def normalize(name):
    """Matching key for the declare-once contract: casefold, collapse whitespace,
    strip ONE leading ordinal ('3.', '3)', '3:', 'Task 3:')."""
    if not isinstance(name, str):
        return ""
    return " ".join(_ORDINAL.sub("", name, count=1).casefold().split())


def name_index(tasks):
    """normalized name -> task nr; duplicates -> None (ambiguous, unmatchable)."""
    idx = {}
    for t in tasks or []:
        if not isinstance(t, dict) or not t.get("name") or t.get("nr") is None:
            continue
        key = normalize(t["name"])
        idx[key] = None if key in idx else t["nr"]
    return idx


def extract_events(paths):
    """(events, newest_entry_ts) across all transcript files. events are
    (ts, kind, payload), ts-sorted; kind in {'todos','dispatch','result'}.
    Malformed lines are skipped. TranscriptTooLarge propagates (N3 posture:
    the caller degrades the whole tail, never parses a pathological file)."""
    events, last_ts = [], None
    for path in paths:
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if size > token_usage.MAX_TRANSCRIPT_BYTES:
            raise token_usage.TranscriptTooLarge(path)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i >= token_usage.MAX_TRANSCRIPT_LINES:
                        raise token_usage.TranscriptTooLarge(path)
                    try:
                        e = json.loads(line)
                        ts = token_usage.parse_ts(e.get("timestamp"))
                        if ts is None:
                            continue
                        if last_ts is None or ts > last_ts:
                            last_ts = ts
                        etype = e.get("type")
                        content = (e.get("message") or {}).get("content")
                        if not isinstance(content, list):
                            continue
                        if etype == "assistant":
                            for b in content:
                                if not isinstance(b, dict) or b.get("type") != "tool_use":
                                    continue
                                if b.get("name") == "TodoWrite":
                                    todos = (b.get("input") or {}).get("todos")
                                    if isinstance(todos, list):
                                        events.append((ts, "todos", [
                                            {"content": t.get("content"), "status": t.get("status")}
                                            for t in todos if isinstance(t, dict)]))
                                elif b.get("name") == "TaskCreate":
                                    events.append((ts, "taskcreate", {
                                        "id": b.get("id"),
                                        "subject": (b.get("input") or {}).get("subject")}))
                                elif b.get("name") == "TaskUpdate":
                                    inp = b.get("input") or {}
                                    events.append((ts, "taskupdate", {
                                        "taskId": inp.get("taskId"),
                                        "status": inp.get("status")}))
                                elif b.get("name") in DISPATCH_TOOLS:
                                    events.append((ts, "dispatch", {
                                        "id": b.get("id"),
                                        "description": (b.get("input") or {}).get("description")}))
                        elif etype == "user":
                            for b in content:
                                if isinstance(b, dict) and b.get("type") == "tool_result":
                                    events.append((ts, "result",
                                                   {"tool_use_id": b.get("tool_use_id"),
                                                    "text": _result_text(b.get("content"))}))
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError):
                        continue
        except OSError:
            continue
    events.sort(key=lambda ev: ev[0])
    return synthesize_task_snapshots(events), last_ts


def _result_text(content):
    """tool_result content is a string or a list of blocks; anything else is ''.
    Truncated — only the leading 'Task #<id> created ...' line is ever consumed."""
    if isinstance(content, list):
        content = " ".join(p.get("text") for p in content
                           if isinstance(p, dict) and p.get("type") == "text"
                           and isinstance(p.get("text"), str))
    return content[:120] if isinstance(content, str) else ""


_TASK_ID_RE = re.compile(r"#(\d+)")
_TASK_STATUSES = ("pending", "in_progress", "completed")


def synthesize_task_snapshots(events):
    """TaskCreate/TaskUpdate (TodoWrite's successor in newer harnesses) carry no
    list snapshots — rebuild them so observe/observe_c consume identical 'todos'
    events from either tool family. The taskId binds from the TaskCreate result
    text ('Task #<id> created ...'); if that wording ever changes, ids fall back
    to creation order, and an update whose taskId never bound is dropped
    (fail-soft: fewer events, never wrong ones). Raw taskcreate/taskupdate
    events never leave this function."""
    pending, tasks, order, out = {}, {}, [], []
    for ts, kind, payload in events:
        if kind == "taskcreate":
            if payload.get("id") and isinstance(payload.get("subject"), str) \
                    and payload["subject"]:
                pending[payload["id"]] = payload["subject"]
            continue
        if kind == "taskupdate":
            tid = payload.get("taskId")
            tid = str(tid) if isinstance(tid, (str, int)) else None
            if tid in tasks and payload.get("status") in _TASK_STATUSES:
                tasks[tid]["status"] = payload["status"]
                out.append((ts, "todos", [dict(tasks[t]) for t in order]))
            continue
        if kind == "result" and payload.get("tool_use_id") in pending:
            subject = pending.pop(payload["tool_use_id"])
            m = _TASK_ID_RE.search(payload.get("text") or "")
            tid = m.group(1) if m else str(len(order) + 1)
            if tid not in tasks:
                tasks[tid] = {"content": subject, "status": "pending"}
                order.append(tid)
                out.append((ts, "todos", [dict(tasks[t]) for t in order]))
        out.append((ts, kind, payload))
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("state")
    p.add_argument("--now", help="ISO 8601 override for one-shot mode (tests, L3 callers)")
    p.add_argument("--follow", action="store_true")
    p.add_argument("--exit-on-event", action="store_true")
    p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    p.add_argument("--debounce", type=float, default=DEFAULT_DEBOUNCE_S)
    p.add_argument("--max-cycles", type=int, default=0, help="0 = unbounded (tests use >0)")
    p.add_argument("--projects-dir", default=None)
    p.add_argument("--stale-min", type=float, default=None,
                   help="override state staleAfterMin (tests)")
    p.add_argument("--job-id", default=None,
                   help="expected jobId; mismatch -> ownership-lost, no writes (L3 callers pass this)")
    a = p.parse_args(argv)
    if a.follow:
        return follow(a)          # Task 6
    return one_shot(a)            # Task 3


def emit(obj):
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def write_state(path, state):
    """Atomic same-directory replace — the mid-Edit truncation class the manual
    protocol could only fail closed on cannot occur here."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".whendone-state-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def observe(events, idx):
    """First-seen start/finish evidence per declared task nr, transcript
    timestamps only. dispatch/result pairs correlate via tool_use id."""
    obs, dispatch_nr = {}, {}

    def note(nr, key, ts):
        slot = obs.setdefault(nr, {})
        if key not in slot:
            slot[key] = ts.isoformat()

    for ts, kind, payload in events:
        if kind == "todos":
            for it in payload:
                nr = idx.get(normalize(it.get("content")))
                if nr is None:
                    continue
                if it.get("status") == "in_progress":
                    note(nr, "startedAt", ts)
                elif it.get("status") == "completed":
                    note(nr, "finishedAt", ts)
        elif kind == "dispatch":
            nr = idx.get(normalize(payload.get("description")))
            if nr is not None and payload.get("id"):
                dispatch_nr[payload["id"]] = nr
                note(nr, "startedAt", ts)
        elif kind == "result":
            nr = dispatch_nr.get(payload.get("tool_use_id"))
            if nr is not None:
                note(nr, "finishedAt", ts)
    return obs


C_STATUS = {"pending": "pending", "in_progress": "running", "completed": "done"}


def observe_c(events):
    """Source C (spec §4.3): newest TodoWrite snapshot + first-seen start/finish
    evidence per normalized item name, transcript timestamps only. Events are
    ts-sorted, so the last 'todos' payload seen is the newest."""
    snapshot, first_started, first_finished = None, {}, {}
    for ts, kind, payload in events:
        if kind != "todos":
            continue
        snapshot = payload
        for it in payload:
            key = normalize(it.get("content"))
            if not key:
                continue
            st = it.get("status")
            if st in ("in_progress", "completed") and key not in first_started:
                first_started[key] = ts.isoformat()
            if st == "completed" and key not in first_finished:
                first_finished[key] = ts.isoformat()
    return snapshot, first_started, first_finished


def mirror_c(snapshot, first_started, first_finished):
    """Materialize state tasks from the newest snapshot — no declared plan, the
    list IS the plan. Statuses are revertible (mirror semantics, same posture as
    Source B's display states); timestamps only ever come from transcript entries.
    No estimates, no categories: pace-based ETA only, never calibrated (§5.1)."""
    tasks = []
    for it in snapshot or []:
        content = it.get("content")
        if not isinstance(content, str) or not content:
            continue
        status = C_STATUS.get(it.get("status"), "pending")
        key = normalize(content)
        tasks.append({
            "nr": len(tasks) + 1, "name": content, "status": status,
            "startedAt": first_started.get(key) if status != "pending" else None,
            "finishedAt": first_finished.get(key) if status == "done" else None,
        })
    return tasks


def plan_transitions(state, obs):
    starts, completions = [], []
    for t in state.get("tasks", []):
        if not isinstance(t, dict) or t.get("status") == "done":
            continue
        o = obs.get(t.get("nr"))
        if not o:
            continue
        if o.get("finishedAt"):
            completions.append((t, o.get("startedAt"), o["finishedAt"]))
        elif o.get("startedAt") and t.get("status") == "pending":
            starts.append((t, o["startedAt"]))
    completions.sort(key=lambda c: c[2])
    return starts, completions


def transcript_files(state, projects_dir):
    flat = []
    for main_t, subs in token_usage.transcript_paths(
            state.get("sessionIds", []),
            projects_dir or os.path.expanduser("~/.claude/projects")):
        flat.append(main_t)
        flat.extend(subs)
    return flat


def sync_cycle(state_path, now, args):
    """One observation pass. Returns (state_or_None, [events emitted])."""
    out = []
    state = load_state(state_path)
    if state is None:
        out.append({"event": "error", "reason": "state file unreadable/unparseable"})
        emit(out[-1]); return None, out
    if args.job_id and state.get("jobId") != args.job_id:
        out.append({"event": "ownership-lost", "expected": args.job_id})
        emit(out[-1]); return state, out
    # C1: a user-created .claude/STOP is a stop REQUEST — surface it, never act on
    # or delete it (delete-only-by-model; lexists so a shipped symlink still counts)
    stop_flag = os.path.join(os.path.dirname(os.path.abspath(state_path)), "STOP")
    if state.get("status") == "running" and os.path.lexists(stop_flag):
        _emit_once(args, out, {"event": "stop-requested"})
    src = state.get("source", "a")
    if src == "b":
        return sync_cycle_b(state_path, state, now, args, out)
    if src == "c":
        return sync_cycle_c(state_path, state, now, args, out)
    if src != "a":
        out.append({"event": "unsupported-source", "source": state.get("source")})
        emit(out[-1]); return state, out
    if state.get("status") != "running":
        out.append({"event": "no-op", "reason": "status %s" % state.get("status")})
        emit(out[-1]); return state, out

    try:
        events, last_ts = extract_events(transcript_files(state, args.projects_dir))
    except token_usage.TranscriptTooLarge:
        events, last_ts = [], None
        out.append({"event": "tail-unavailable", "reason": "transcript exceeds size cap"})
        emit(out[-1])

    idx = name_index(state.get("tasks"))
    starts, completions = plan_transitions(state, observe(events, idx))
    for t, started in starts:
        t["status"] = "running"
        t["startedAt"] = started
        write_state(state_path, state)
    just_done = []
    for t, started, finished in completions:
        handle_completion(state_path, state, t, started, finished, args)
        just_done.append(t.get("name"))

    changed = bool(starts or completions)
    args._last_ts = last_ts                      # Task 6's staleness input
    # D11: all-done bypasses the debounce gate outright (and finish_cycle's own
    # render call catches a same-cycle slipAlert too, since etaAlertSent gates
    # on job-level state.status, not on task-level done==total) — only a plain
    # progress completion stays subject to _render_ok.
    all_done_now = bool(completions) and bool(state.get("tasks")) and all(
        isinstance(t, dict) and t.get("status") == "done" for t in state["tasks"])
    return _maybe_finish(state_path, state, now, args, out, changed, just_done, all_done_now)


def _maybe_finish(state_path, state, now, args, out, changed, just_done, all_done_now):
    """Shared debounce/render tail (D11). all_done bypasses the gate outright;
    plain progress respects _render_ok; a gated change is coalesced into
    _pending_names for the next allowed render."""
    just_done = (getattr(args, "_pending_names", None) or []) + just_done
    if (changed and getattr(args, "_render_ok", True)) \
            or getattr(args, "_force_render", False) or all_done_now:
        args._pending_names, args._pending = [], False
        out.extend(finish_cycle(state_path, state, now,
                                getattr(args, "_last_ts", None), changed,
                                just_done, args))
    elif changed:
        args._pending, args._pending_names = True, just_done
    return state, out


def _emit_once(args, out, ev):
    """B10: Monitor turns every stdout line into a wake — B setup failures are
    emitted once per (event, reason) per watcher run."""
    seen = getattr(args, "_b_emitted", None)
    if seen is None:
        seen = args._b_emitted = set()
    key = (ev.get("event"), ev.get("reason"))
    if key in seen:
        return
    seen.add(key)
    out.append(ev)
    emit(ev)


def observe_b(state, args):
    """One disk pass over the workflow run. Never raises; degrades via meta flags."""
    projects = args.projects_dir or os.path.expanduser("~/.claude/projects")
    meta = {"runDir": None, "tooLarge": False, "drift": False, "finished": False,
            "started": 0, "done": 0, "unattributed": 0, "lastActivity": None}
    per_tag = {}
    run_dir = workflow_journal.find_run_dir(
        state.get("sessionIds"), state.get("workflowRunId"), projects)
    if run_dir is None:
        return per_tag, meta
    meta["runDir"] = run_dir
    journal = os.path.join(run_dir, "journal.jsonl")
    try:
        agents, stats = workflow_journal.parse_journal(journal)
    except workflow_journal.JournalTooLarge:
        meta["tooLarge"] = True
        return per_tag, meta
    meta["drift"] = workflow_journal.drifted(stats)
    meta["finished"] = workflow_journal.run_finished(run_dir)
    mtimes = []
    with contextlib.suppress(OSError):
        mtimes.append(os.path.getmtime(journal))
    cache = getattr(args, "_agent_cache", None)
    if cache is None:
        cache = args._agent_cache = {}
    for aid, seen in agents.items():
        meta["started"] += 1
        done = seen["result"]
        if done:
            meta["done"] += 1
        path = os.path.join(run_dir, "agent-%s.jsonl" % aid)
        with contextlib.suppress(OSError):
            mtimes.append(os.path.getmtime(path))
        info = cache.get(aid)
        if info is None or not info["final"]:
            if info is None:
                info = {"start": None, "tag": None, "end": None,
                        "model": None, "final": False}
                cache[aid] = info
            if info["start"] is None or info["tag"] is None:
                s, tag = workflow_journal.agent_first(path, root=run_dir)
                info["start"] = info["start"] or s
                info["tag"] = info["tag"] or tag
            if done:
                info["end"] = workflow_journal.agent_last_ts(path, root=run_dir)
                info["model"] = workflow_journal.agent_model(run_dir, aid)
                info["final"] = True          # frozen: completed agents never re-read
        if info["tag"] is None:
            meta["unattributed"] += 1
            continue
        slot = per_tag.setdefault(info["tag"], {
            "started": 0, "done": 0, "inflight": 0,
            "minStart": None, "maxEnd": None, "models": set()})
        slot["started"] += 1
        if info["start"] and (slot["minStart"] is None
                              or info["start"] < slot["minStart"]):
            slot["minStart"] = info["start"]
        if done:
            slot["done"] += 1
            if info["end"] and (slot["maxEnd"] is None
                                or info["end"] > slot["maxEnd"]):
                slot["maxEnd"] = info["end"]
            if info["model"]:
                slot["models"].add(info["model"])
        else:
            slot["inflight"] += 1
    if mtimes:
        meta["lastActivity"] = datetime.fromtimestamp(max(mtimes)).astimezone()
    return per_tag, meta


def sync_cycle_b(state_path, state, now, args, out):
    """Source-B pass: observe -> display transitions (revertible, B4) ->
    [Task 5: finalize on completion record] -> shared debounce/render tail."""
    if state.get("status") != "running":
        out.append({"event": "no-op", "reason": "status %s" % state.get("status")})
        emit(out[-1]); return state, out
    per_tag, meta = observe_b(state, args)
    args._last_ts = meta["lastActivity"]
    if meta["tooLarge"] or meta["runDir"] is None:
        _emit_once(args, out, {"event": "tail-unavailable",
                               "reason": "journal exceeds size cap" if meta["tooLarge"]
                               else "workflow run dir not found"})
        # §6.1: declared estimates still render — fall through to the shared tail
        # (a forced render in one-shot mode; debounced no-op otherwise).
        return _maybe_finish(state_path, state, now, args, out, False, [], False)
    changed = False
    if (state.get("wfAgentsStarted"), state.get("wfAgentsDone")) != \
            (meta["started"], meta["done"]):
        state["wfAgentsStarted"], state["wfAgentsDone"] = meta["started"], meta["done"]
        changed = True
    if meta["drift"]:
        if not state.get("wfDriftNotified"):
            state["wfDriftNotified"] = True
            write_state(state_path, state)
            ev = {"event": "journal-format-drift"}
            out.append(ev); emit(ev)
        just_done = []
        all_done_now = False
        if meta["finished"]:
            # Drift means no per-phase attribution: bring the run to a clean
            # terminal state WITHOUT writing any calibration rows (agents
            # counted, phases unknown — no finalize_b, no append). Every
            # remaining task is marked done with actualMin left None; phases
            # already done stay done.
            for t in state.get("tasks", []):
                if isinstance(t, dict) and t.get("status") != "done":
                    t["status"] = "done"
                    just_done.append(t.get("name"))
                    changed = True
            all_done_now = True
        if changed:
            write_state(state_path, state)
        return _maybe_finish(state_path, state, now, args, out, changed,
                             just_done, all_done_now)
    just_done = []
    for t in state.get("tasks", []):
        if not isinstance(t, dict) or not t.get("wdTag"):
            continue
        slot = per_tag.get(t["wdTag"])
        if slot is None:
            continue
        if (t.get("agentsStarted"), t.get("agentsDone")) != \
                (slot["started"], slot["done"]):
            t["agentsStarted"], t["agentsDone"] = slot["started"], slot["done"]
            changed = True
        if t.get("status") == "pending" and slot["started"] > 0:
            t["status"] = "running"
            if slot["minStart"]:
                t["startedAt"] = slot["minStart"]
            changed = True
        expected = t.get("agentsExpected")
        display_done = (isinstance(expected, int) and expected > 0
                        and slot["done"] >= expected and slot["inflight"] == 0)
        if t.get("status") == "running" and display_done:
            t["status"] = "done"
            if slot["maxEnd"]:
                t["finishedAt"] = slot["maxEnd"]
            changed = True
            just_done.append(t.get("name"))
        elif (t.get("status") == "done" and t.get("actualMin") is None
              and not display_done):
            t["status"] = "running"          # late pipeline() agent (B4) — legal,
            t["finishedAt"] = None           # no calibration row exists yet
            changed = True
    if changed:
        write_state(state_path, state)
    all_done_now = False
    if meta["finished"]:
        finalized = finalize_b(state_path, state, per_tag, args)
        if finalized:
            changed = True
            just_done.extend(n for n in finalized if n not in just_done)
        tasks = [t for t in state.get("tasks", []) if isinstance(t, dict)]
        all_done_now = bool(tasks) and all(t.get("status") == "done" for t in tasks)
    return _maybe_finish(state_path, state, now, args, out, changed,
                         just_done, all_done_now)


def sync_cycle_c(state_path, state, now, args, out):
    """Source-C pass (spec §4.3/§5.1): mirror the newest TodoWrite snapshot into
    state.tasks — no declaration, no estimates, and NEVER a calibration append
    (handle_completion is not on this path, and guards besides). Shares Source A's
    transcript tailing and the _maybe_finish debounce/render tail."""
    if state.get("status") != "running":
        out.append({"event": "no-op", "reason": "status %s" % state.get("status")})
        emit(out[-1]); return state, out
    try:
        events, last_ts = extract_events(transcript_files(state, args.projects_dir))
    except token_usage.TranscriptTooLarge:
        events, last_ts = [], None
        out.append({"event": "tail-unavailable", "reason": "transcript exceeds size cap"})
        emit(out[-1])
    args._last_ts = last_ts
    snapshot, first_started, first_finished = observe_c(events)
    changed, just_done = False, []
    if snapshot is not None:
        new_tasks = mirror_c(snapshot, first_started, first_finished)
        prev = {t.get("name"): t for t in state.get("tasks", []) if isinstance(t, dict)}
        for t in new_tasks:                      # stale flags survive a rebuild
            p = prev.get(t["name"])
            if p and p.get("status") == "running" and t["status"] == "running" \
                    and p.get("staleNotifiedAt"):
                t["staleNotifiedAt"] = p["staleNotifiedAt"]
        old = [(t.get("name"), t.get("status")) for t in state.get("tasks", [])
               if isinstance(t, dict)]
        if [(t["name"], t["status"]) for t in new_tasks] != old:
            done_before = {n for n, s in old if s == "done"}
            just_done = [t["name"] for t in new_tasks
                         if t["status"] == "done" and t["name"] not in done_before]
            state["tasks"] = new_tasks
            write_state(state_path, state)
            changed = True
    all_done_now = changed and bool(state.get("tasks")) and all(
        t.get("status") == "done" for t in state["tasks"])
    return _maybe_finish(state_path, state, now, args, out, changed,
                         just_done, all_done_now)


def finalize_b(state_path, state, per_tag, args):
    """Completion record observed: the run ENDED, so spans are final and rows can
    no longer be contradicted by a late agent (B4). Same crash order as
    handle_completion: (b) durable done-marker -> (c) append -> (d) actualMin.
    Full-job token refresh once (B8). Every step past (b) is fail-soft.
    Phases already done with a non-null actualMin are never re-finalized."""
    finalized = []
    tokens = None
    try:
        tokens = token_usage.summarize(state_path, projects_dir=args.projects_dir)
    except Exception:
        tokens = None
    args._held_tokens = (None, tokens)
    for t in state.get("tasks", []):
        if not isinstance(t, dict):
            continue
        if t.get("status") == "done" and (t.get("bFinalized")
                                          or t.get("actualMin") is not None):
            continue                      # done-is-done (B12); flag closes the I2 window
        slot = per_tag.get(t.get("wdTag")) or {}
        started = slot.get("minStart") or t.get("startedAt")
        finished = slot.get("maxEnd") or t.get("finishedAt")
        # (b) durable done-marker FIRST — bFinalized in the SAME write closes the
        # append/actualMin crash window (a lost row beats a duplicated one)
        t["status"] = "done"
        t["bFinalized"] = True
        if started:
            t["startedAt"] = started
        if finished:
            t["finishedAt"] = finished
        write_state(state_path, state)
        finalized.append(t.get("name"))
        # (c) append — only with a full observed span, never invented
        appended = None
        if started and finished:
            models = slot.get("models") or set()
            row = dict(_base_row(state, state_path, started, finished),
                       category=t.get("category"),
                       rawEstimateMin=t.get("rawEstimateMin"),
                       model=next(iter(models)) if len(models) == 1 else "unknown")
            try:
                ok, res = append_calibration.append_obj(row)
                appended = res if ok else None
            except Exception:
                appended = None
        # (d) actualMin mirrors the logged value
        t["actualMin"] = appended["actualMin"] if appended else None
        write_state(state_path, state)
    return finalized


def _is_alias(model):
    """Bare dispatch alias ('haiku') vs full versioned id ('claude-haiku-...')."""
    return isinstance(model, str) and bool(model) and "claude" not in model


def _project_name(state_path):
    root = os.path.dirname(os.path.dirname(os.path.abspath(state_path)))
    return os.path.basename(os.path.realpath(root))


def _base_row(state, state_path, started, finished):
    return {
        "date": (token_usage.parse_ts(finished) or datetime.now().astimezone())
                .astimezone().date().isoformat(),
        "project": _project_name(state_path),
        "job": state.get("job", ""),
        "startedAt": started,
        "finishedAt": finished,
        "client": state.get("client", "unknown"),
    }


def group_row(state, group_id):
    members = [t for t in state.get("tasks", [])
               if isinstance(t, dict) and t.get("group") == group_id]
    if not members or any(t.get("status") != "done" for t in members):
        return None
    starts = [t.get("startedAt") for t in members if t.get("startedAt")]
    ends = [t.get("finishedAt") for t in members if t.get("finishedAt")]
    raws = [t.get("rawEstimateMin") for t in members
            if isinstance(t.get("rawEstimateMin"), (int, float))]
    ests = [t.get("estimateMin") for t in members
            if isinstance(t.get("estimateMin"), (int, float))]
    if not (starts and ends and raws and ests):
        return None                     # missing evidence -> no row, never invented
    return {"category": "parallel-group", "rawEstimateMin": max(raws),
            "maxAdjusted": max(ests), "sumAdjusted": sum(ests),
            "startedAt": min(starts), "finishedAt": max(ends)}


def handle_completion(state_path, state, t, started, finished, args):
    """Today's checkpoint order, in code: (b) done-marker -> (b2) tokens+alias ->
    (c) append -> (d) actualMin. Every step past (b) is fail-soft."""
    if state.get("source") == "c":
        return          # spec §5.1: Source C never logs calibration — hard guard

    # (b) durable done-marker FIRST
    t["status"] = "done"
    if started and not t.get("startedAt"):
        t["startedAt"] = started
    t["finishedAt"] = finished
    write_state(state_path, state)

    # (b2) token refresh; alias upgrade only same-family (substring), never blind
    tokens = None
    try:
        tokens = token_usage.summarize(state_path, projects_dir=args.projects_dir,
                                       task_nr=t.get("nr"))
    except Exception:
        tokens = None
    args._held_tokens = (t.get("nr"), tokens)          # Task 5's render reuses this
    if tokens and tokens.get("available") and _is_alias(t.get("model")):
        entries = tokens.get("tasks") or []
        models = (entries[0].get("models") or []) if entries else []
        top = models[0].get("id") if models else None
        if isinstance(top, str) and t["model"] in top:
            t["model"] = top
            write_state(state_path, state)

    # (c) append — individual row for sequential tasks; group members wait for
    # the synthetic row when the LAST member lands. Source-c jobs never log
    # (guarded before this function is ever called).
    row = None
    if t.get("group") is None:
        started_final = t.get("startedAt")
        if started_final and finished:
            row = dict(_base_row(state, state_path, started_final, finished),
                       category=t.get("category"),
                       rawEstimateMin=t.get("rawEstimateMin"),
                       model=t.get("model") or "unknown")
            if t.get("effort") is not None:
                row["effort"] = t["effort"]
    else:
        g = group_row(state, t.get("group"))
        if g is not None:
            row = dict(_base_row(state, state_path, g.pop("startedAt"), g.pop("finishedAt")),
                       **g, model="unknown")
    appended = None
    if row is not None:
        try:
            ok, res = append_calibration.append_obj(row)
            appended = res if ok else None
        except Exception:
            appended = None

    # (d) actualMin mirrors the logged value; group members stay null (renderer
    # derives their display time from timestamps)
    if t.get("group") is None:
        t["actualMin"] = appended["actualMin"] if appended else None
        write_state(state_path, state)


def _open_private(path, flags):
    """Open with 0600, then fchmod the fd BEFORE any content is written -- closes the
    write-before-chmod window a trailing os.chmod(path, ...) would leave open for a
    pre-existing 0644 file (e.g. a pre-fix sidecar): content must never be written while
    the file is still world/group-readable. (Mirrors append_calibration._open_private.)"""
    fd = os.open(path, flags, 0o600)
    if os.name == "posix":
        os.fchmod(fd, 0o600)
    return fd


def _render_out_path(state):
    """Validate-not-trust (stage-4 hardening): artifactFile comes from the state
    file, an untrusted source. The protocol layer already re-mints it in the
    session scratchpad; this is defense-in-depth, and any rejection falls back
    to the tempdir default rather than blocking the render (fail-soft)."""
    af = state.get("artifactFile")
    if isinstance(af, str) and os.path.isabs(af) and af.endswith(".html") \
            and not os.path.islink(af):
        parent = os.path.dirname(af)
        if os.path.isdir(parent) and not os.path.islink(parent):
            return af
    job = re.sub(r"[^A-Za-z0-9T-]", "", str(state.get("jobId") or "job"))[:32] or "job"
    return os.path.join(tempfile.gettempdir(), "whendone-render-%s.html" % job)


def render_now(state_path, tok_arg, out_path, now, state):
    """Invoke the renderer through its pinned CLI entry point; capture the one-line
    JSON status. Any failure -> None (fail-soft; the caller reports rendered:false)."""
    push = state.get("pushStatus")
    if push not in render_artifact.PUSH_STATUS:
        push = "uncertain"
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = render_artifact.main([state_path, tok_arg, out_path,
                                       "--now", now.isoformat(), "--push-status", push])
        if rc != 0:
            return None
        return json.loads(buf.getvalue().strip().splitlines()[-1])
    except Exception:
        return None


def finish_cycle(state_path, state, now, last_ts, changed, just_done, args):
    """Render the artifact, surface etaText/slipAlert, emit ONE progress/all-done
    event. The model's whole wake job is publishing the file this wrote."""
    tasks = [t for t in state.get("tasks", []) if isinstance(t, dict)]
    done = sum(1 for t in tasks if t.get("status") == "done")
    all_done = bool(tasks) and done == len(tasks)
    out_path = _render_out_path(state)

    held_nr, held = getattr(args, "_held_tokens", (None, None))
    tok_arg = "-"
    if held and held.get("available"):
        sidecar_path = out_path + ".tokens.json"
        try:
            with open(sidecar_path, encoding="utf-8") as f:
                existing = json.load(f)
        except (OSError, ValueError):
            existing = None
        by_nr = {}
        if isinstance(existing, dict):
            for e in existing.get("tasks") or []:
                if isinstance(e, dict) and e.get("nr") is not None:
                    by_nr[e["nr"]] = e
        for e in held.get("tasks") or []:                # new held entries override old
            if isinstance(e, dict) and e.get("nr") is not None:
                by_nr[e["nr"]] = e
        merged = dict(held)
        merged["tasks"] = sorted(by_nr.values(), key=lambda e: e.get("nr"))
        try:
            fd = _open_private(sidecar_path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(merged, f)
            tok_arg = sidecar_path
        except OSError:
            tok_arg = "-"
    elif os.path.exists(out_path + ".tokens.json"):
        tok_arg = out_path + ".tokens.json"              # reuse last refresh on drift renders

    status = render_now(state_path, tok_arg, out_path, now, state)
    if status is not None and os.name == "posix":
        # M9: render_artifact.py's atomic write (plain open() + os.replace) lands
        # 0644 by default umask; tighten unconditionally regardless of `publish` --
        # the no-publish path still renders every wake for the chat table's
        # etaText, and 0600 costs nothing when a publish does happen.
        for p in (out_path, out_path + ".tokens.json"):
            with contextlib.suppress(OSError):
                os.chmod(p, 0o600)
    ev = {"event": "all-done" if all_done else "progress", "done": done,
          "total": len(tasks), "changed": changed, "justDone": just_done,
          "rendered": status is not None}
    if state.get("source") == "b" and isinstance(state.get("wfAgentsStarted"), int):
        ev["agentsStarted"] = state["wfAgentsStarted"]
        ev["agentsDone"] = state.get("wfAgentsDone")
    if status is not None:
        ev["etaText"] = status.get("etaText")
        if status.get("slipAlert") and not state.get("etaAlertSent"):
            state["etaAlertSent"] = True
            write_state(state_path, state)
            ev["slipAlert"] = True
    emit(ev)
    return [ev]


def one_shot(a):
    a._force_render = True        # L3 boundary refresh always renders (ETA drifts with time)
    holder = _live_lock_holder(a.state)
    if holder:
        emit({"event": "already-running",
              "reason": "live tailer (pid %d) holds whendone-tail.lock" % holder})
        return 4
    now = token_usage.parse_ts(a.now) or datetime.now().astimezone()
    state, events = sync_cycle(a.state, now, a)
    if state is None:
        return 2
    if any(e.get("event") == "ownership-lost" for e in events):
        return 3
    return 0


def _pid_alive(pid):
    if os.name == "nt":
        # os.kill(pid, 0) is NOT a liveness probe on Windows -- signal 0 is
        # CTRL_C_EVENT, which sends a console Ctrl-C (can interrupt the very
        # session being monitored). Probe via OpenProcess instead.
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5   # ERROR_ACCESS_DENIED: exists, not ours
        code = ctypes.c_ulong()                   # DWORD
        alive = k32.GetExitCodeProcess(h, ctypes.byref(code)) and code.value == 259
        k32.CloseHandle(h)                        # 259 = STILL_ACTIVE
        return bool(alive)
    try:
        os.kill(pid, 0)
    except OSError as e:
        return e.errno == errno.EPERM      # exists, not ours
    return True


def _lock_path(state_path):
    return os.path.join(os.path.dirname(os.path.abspath(state_path)), "whendone-tail.lock")


def _live_lock_holder(state_path):
    """Pid of a LIVE tailer holding the lock, else None (absent/dead/garbled)."""
    try:
        with open(_lock_path(state_path), encoding="utf-8") as f:
            pid = int(f.read().strip() or "0")
    except (OSError, ValueError):
        return None
    return pid if pid and _pid_alive(pid) else None


def acquire_lock(state_path):
    """O_EXCL pid lockfile beside the state file. None -> a LIVE tailer owns it.
    A dead pid's lock is removed and re-acquired (crashed watcher)."""
    lock = _lock_path(state_path)
    for _ in range(3):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
            return lock
        except FileExistsError:
            try:
                with open(lock, encoding="utf-8") as f:
                    pid = int(f.read().strip() or "0")
            except (OSError, ValueError):
                pid = 0
            if pid and _pid_alive(pid):
                return None
            with contextlib.suppress(OSError):
                os.remove(lock)            # stale; retry O_EXCL
        except OSError:
            return None
    return None


def check_staleness(state_path, state, now, stale_min, args):
    """F13: no new transcript entry for stale_min minutes while a task is in
    flight -> ONE stale event per task, persisted as staleNotifiedAt."""
    out = []
    last_ts = getattr(args, "_last_ts", None)
    for t in state.get("tasks", []):
        if not isinstance(t, dict) or t.get("status") != "running" or t.get("staleNotifiedAt"):
            continue
        anchor = last_ts
        started = token_usage.parse_ts(t.get("startedAt"))
        if anchor is None or (started and started > anchor):
            anchor = started
        if anchor is None:
            continue
        stalled = (now - anchor).total_seconds() / 60.0
        if stalled >= stale_min:
            t["staleNotifiedAt"] = now.isoformat()
            write_state(state_path, state)
            ev = {"event": "stale", "task": t.get("nr"), "name": t.get("name"),
                  "stalledMin": round(stalled, 1)}
            emit(ev)
            out.append(ev)
    return out


TERMINAL_RC = {"all-done": 0, "unsupported-source": 0, "no-op": 0,
               "ownership-lost": 3, "error": 2}


def follow(a):
    first = load_state(a.state)
    if first is None:
        emit({"event": "error", "reason": "state file unreadable/unparseable"})
        return 2
    if not a.job_id:
        a.job_id = first.get("jobId")     # remembered; checked every cycle (D8)
    lock = acquire_lock(a.state)
    if lock is None:
        emit({"event": "already-running",
              "reason": "another tailer holds whendone-tail.lock"})
        return 4
    stale_min = a.stale_min
    if stale_min is None:
        v = first.get("staleAfterMin")
        stale_min = v if isinstance(v, (int, float)) and v > 0 else DEFAULT_STALE_MIN
    last_emit = float("-inf")
    cycles = 0
    try:
        while True:
            now = datetime.now().astimezone()
            mono = time.monotonic()
            a._render_ok = (mono - last_emit) >= a.debounce
            a._force_render = bool(getattr(a, "_pending", False) and a._render_ok)
            state, events = sync_cycle(a.state, now, a)
            woke = [e for e in events
                    if e.get("event") in ("progress", "all-done", "stop-requested")]
            if woke:
                last_emit = mono
            for e in events:
                if e.get("event") in TERMINAL_RC:
                    if e["event"] in ("progress",):
                        continue
                    return TERMINAL_RC[e["event"]]
            stale_evs = []
            if state is not None and state.get("status") == "running":
                stale_evs = check_staleness(a.state, state, now, stale_min, a)
            if (woke or stale_evs) and a.exit_on_event:
                return 0
            cycles += 1
            if a.max_cycles and cycles >= a.max_cycles:
                return 0
            time.sleep(a.interval)
    finally:
        with contextlib.suppress(OSError):
            os.remove(lock)


if __name__ == "__main__":
    sys.exit(main())
