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
_NOTIF_ID_RE = re.compile(r"<tool-use-id>\s*([^<\s]+)\s*</tool-use-id>")
_ACK_RE = re.compile(r"\bAsync agent launched\b")


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


def extract_events(paths, aux_paths=()):
    """(events, newest_entry_ts) across all transcript files. events are
    (ts, kind, payload), ts-sorted; kind in {'todos','dispatch','result','artifact',
    'agent-done'}. Malformed lines are skipped. TranscriptTooLarge propagates (N3
    posture: the caller degrades the whole tail, never parses a pathological file).

    N5: `aux_paths` (subagent transcripts) never produce events -- they only
    advance `last_ts`, the staleness/grace clock, and are fail-soft: an
    oversized or unreadable aux file is skipped silently rather than raising."""
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
                        if etype == "user" and isinstance(content, str):
                            if "<task-notification>" in content:
                                m = _NOTIF_ID_RE.search(content)
                                if m:
                                    events.append((ts, "agent-done",
                                                   {"tool_use_id": m.group(1)}))
                            continue
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
                                    inp = b.get("input") or {}
                                    events.append((ts, "dispatch", {
                                        "id": b.get("id"),
                                        "description": inp.get("description"),
                                        "model": inp.get("model") if isinstance(inp.get("model"), str) else None,
                                        "background": bool(inp.get("run_in_background"))}))
                                elif b.get("name") == "Artifact":
                                    # D4: the model's OWN publish action -- evidence for the
                                    # publishLag backstop, never acted on as an instruction.
                                    inp = b.get("input") or {}
                                    events.append((ts, "artifact", {
                                        "file_path": inp.get("file_path"),
                                        "description": inp.get("description")}))
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
    for path in aux_paths:
        try:
            if os.path.getsize(path) > token_usage.MAX_TRANSCRIPT_BYTES:
                continue
            with open(path, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i >= token_usage.MAX_TRANSCRIPT_LINES:
                        break
                    try:
                        ts = token_usage.parse_ts(json.loads(line).get("timestamp"))
                    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
                        continue
                    if ts is not None and (last_ts is None or ts > last_ts):
                        last_ts = ts
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
    """Tiered evidence per declared task nr, transcript timestamps only.
    Todo transitions are the close authority (v0.5 attribution rework);
    matched dispatch/result pairs are delegated-span display evidence and
    the start fallback. dispatch/result correlate via tool_use id.

    D1: startedAt authority is the todo `in_progress` transition; a
    name-matching dispatch is start-EVIDENCE FALLBACK only, and must lose to
    a todo transition regardless of which arrives first in ts order (a batch
    of parallel dispatches followed by one todo update is the common case
    that a first-write-wins guard would get backwards). So the two
    candidates are tracked separately per slot (`_todoStart`/`_dispatchStart`,
    scratch — never returned) and `startedAt` is resolved from them only
    after the full pass, once no earlier todo transition can still appear.

    N1/N2: a background dispatch's `result` is a launch acknowledgment, not
    the agent's finish — recognized either by the dispatch's own
    `run_in_background` flag or, flag-less, by the ack text itself
    (`_ACK_RE`). Such a result never closes the span; only a later
    `agent-done` event (parsed from a `<task-notification>` transcript
    entry, same tool_use id) does. `closed` remembers which slot/span index
    a tool_use id closed so a repeat notification (resume) can extend that
    span's end instead of opening a new one."""
    obs, dispatch_open, closed = {}, {}, {}

    def slot(nr):
        return obs.setdefault(nr, {"startedAt": None, "todoFinishedAt": None,
                                   "todoSeen": False, "spans": [], "open": 0,
                                   "model": None, "_todoStart": None,
                                   "_dispatchStart": None})

    for ts, kind, payload in events:
        if kind == "todos":
            for it in payload:
                nr = idx.get(normalize(it.get("content")))
                if nr is None:
                    continue
                st = it.get("status")
                if st not in ("in_progress", "completed"):
                    continue
                s = slot(nr)
                s["todoSeen"] = True
                if st == "in_progress" and s["_todoStart"] is None:
                    s["_todoStart"] = ts.isoformat()
                elif st == "completed" and s["todoFinishedAt"] is None:
                    s["todoFinishedAt"] = ts.isoformat()
        elif kind == "dispatch":
            nr = idx.get(normalize(payload.get("description")))
            if nr is not None and payload.get("id"):
                s = slot(nr)
                dispatch_open[payload["id"]] = (nr, ts, bool(payload.get("background")))
                s["open"] += 1
                if s["_dispatchStart"] is None:
                    s["_dispatchStart"] = ts.isoformat()
                if s["model"] is None and payload.get("model"):
                    s["model"] = payload["model"]
        elif kind == "result":
            tid = payload.get("tool_use_id")
            pair = dispatch_open.get(tid)
            if pair is not None:
                nr, dts, background = pair
                # N2: a background dispatch's tool_result is the launch ack, never
                # the agent's result -- the span stays open until agent-done.
                if not (background or _ACK_RE.search(payload.get("text") or "")):
                    del dispatch_open[tid]
                    s = slot(nr)
                    s["open"] -= 1
                    s["spans"].append((dts.isoformat(), ts.isoformat()))
                    closed[tid] = (nr, len(s["spans"]) - 1)
        elif kind == "agent-done":
            tid = payload.get("tool_use_id")
            pair = dispatch_open.pop(tid, None)
            if pair is not None:
                nr, dts, _bg = pair
                s = slot(nr)
                s["open"] -= 1
                s["spans"].append((dts.isoformat(), ts.isoformat()))
                closed[tid] = (nr, len(s["spans"]) - 1)
            elif tid in closed:                     # repeat notification: extend, last wins
                nr, i = closed[tid]
                start, end = obs[nr]["spans"][i]
                if ts > token_usage.parse_ts(end):
                    obs[nr]["spans"][i] = (start, ts.isoformat())

    for s in obs.values():
        todo_start, dispatch_start = s.pop("_todoStart"), s.pop("_dispatchStart")
        s["startedAt"] = todo_start if todo_start is not None else dispatch_start
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


def _delegated_min(spans):
    """D3: agent-minutes summed over matched dispatch->result spans — DISPLAY
    metadata only. Never the calibrated actual (that is the full task span);
    parallel spans deliberately double-count, since that is what 'agent
    minutes spent' means. None when there is nothing observed to report.

    Totally guarded: this runs INSIDE handle_completion's step (b), before the
    durable done-marker write — the one step the crash-safety order depends on
    and the only one that is not fail-soft. It cannot raise today (spans come
    from observe(), parse_ts is total), but visibility must never block the
    work, so a display-metadata computation degrades to None rather than
    propagating into the marker write."""
    try:
        total = 0.0
        for s, e in spans or []:
            a, b = token_usage.parse_ts(s), token_usage.parse_ts(e)
            if a and b and b >= a:
                total += (b - a).total_seconds() / 60.0
        return round(total, 1) if total > 0 else None
    except Exception:
        return None


def plan_transitions(state, obs):
    """D1: the todo/TaskUpdate `completed` transition is the ONLY close
    authority — a subagent result ends a delegated span, never the task (the
    2026-08-14 bug: every task closed on its first implementer's result, and
    the lead's own review/fix/verify/commit time vanished from the row).

    D2: a task with NO observed todo transition at all still has to LOOK done
    once its dispatches are all back, or a forgetful model's job never shows
    progress — so it gets a revertible display close (`displays`), which
    NEVER writes a calibration row. A later matching dispatch reverts it
    (`reopens`, same B4 posture as Source B); later todo evidence upgrades it
    to a confirmed close (`completions`), and the row is appended then, once.

    Returns (starts, completions, displays, reopens)."""
    starts, completions, displays, reopens = [], [], [], []
    for t in state.get("tasks", []):
        if not isinstance(t, dict):
            continue
        o = obs.get(t.get("nr"))
        if t.get("status") == "done":
            if t.get("unconfirmed") and o:          # display close: still provisional
                if o.get("todoFinishedAt"):
                    completions.append((t, o.get("startedAt"), o["todoFinishedAt"], o))
                elif o.get("open") or o.get("todoSeen"):
                    # N4: in_progress evidence (or a new matched dispatch) means the
                    # lead is still working this task — revert the provisional close.
                    reopens.append(t)
            continue                                # done-is-done for confirmed closes
        if not o:
            continue
        if o.get("todoFinishedAt"):
            completions.append((t, o.get("startedAt"), o["todoFinishedAt"], o))
        elif not o.get("todoSeen") and o.get("spans") and not o.get("open"):
            displays.append((t, o))
        elif o.get("startedAt") and t.get("status") == "pending":
            starts.append((t, o["startedAt"]))
    completions.sort(key=lambda c: c[2])
    return starts, completions, displays, reopens


def transcript_files(state, projects_dir):
    """(mains, subs): only mains carry event authority (N5); subs feed last_ts only."""
    mains, subs = [], []
    for main_t, sub_list in token_usage.transcript_paths(
            state.get("sessionIds", []),
            projects_dir or os.path.expanduser("~/.claude/projects")):
        mains.append(main_t)
        subs.extend(sub_list)
    return mains, subs


def _match_publish(state, payload):
    """D4: does this observed Artifact tool_use correspond to THIS job's
    published page? file_path is compared normcase/normpath-insensitive
    against the declared artifactFile (Windows-safe: separators/case can
    differ between what the model typed and what state recorded); when the
    path doesn't line up (or artifactFile isn't known yet), fall back to the
    fixed description sentinel every whendone publish call carries."""
    af = state.get("artifactFile")
    fp = payload.get("file_path")
    if isinstance(af, str) and isinstance(fp, str) and af and fp:
        if os.path.normcase(os.path.normpath(af)) == os.path.normcase(os.path.normpath(fp)):
            return True
    return payload.get("description") == "WhenDone progress monitor"


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
        mains, subs = transcript_files(state, args.projects_dir)
        events, last_ts = extract_events(mains, subs)
    except token_usage.TranscriptTooLarge:
        events, last_ts = [], None
        out.append({"event": "tail-unavailable", "reason": "transcript exceeds size cap"})
        emit(out[-1])

    # D4: observe the model's OWN Artifact publishes (Source A only -- see
    # finish_cycle's publishLag gate for why B/C never get this scan).
    pub = None
    for ts, kind, payload in events:
        if kind == "artifact" and _match_publish(state, payload):
            pub = ts.isoformat()                       # ts-sorted: last match wins
    if pub and pub != state.get("lastPublishedAt"):
        state["lastPublishedAt"] = pub
        write_state(state_path, state)

    idx = name_index(state.get("tasks"))
    obs = observe(events, idx)
    # D1: the dispatch's `model` alias is the only model evidence a subagent task
    # ever gives us. The tailer is its single writer (declared model wins, done
    # tasks are frozen), and it is never a wake by itself — it rides the next
    # real transition's render.
    model_changed = False
    for t in state.get("tasks", []):
        o = obs.get(t.get("nr")) if isinstance(t, dict) else None
        if o and o.get("model") and isinstance(t, dict) \
                and not t.get("model") and t.get("status") != "done":
            t["model"] = o["model"]
            model_changed = True
    if model_changed:
        write_state(state_path, state)

    starts, completions, displays, reopens = plan_transitions(state, obs)
    for t in reopens:                       # revertible display close (B4 posture)
        t["status"] = "running"
        t["finishedAt"] = None
        t.pop("unconfirmed", None)
        write_state(state_path, state)
    for t, started in starts:
        t["status"] = "running"
        t["startedAt"] = started
        write_state(state_path, state)
    just_done = []
    for t, started, finished, o in completions:
        handle_completion(state_path, state, t, started, finished, args,
                          spans=o.get("spans"))
        just_done.append(t.get("name"))
    for t, o in displays:                   # D2: display state, NEVER a row
        t["status"] = "done"
        t["unconfirmed"] = True
        if not t.get("startedAt") and o.get("startedAt"):
            t["startedAt"] = o["startedAt"]
        t["finishedAt"] = o["spans"][-1][1]
        dm = _delegated_min(o.get("spans"))
        if dm is not None:
            t["delegatedMin"] = dm
        write_state(state_path, state)
        just_done.append(t.get("name"))

    changed = bool(starts or completions or displays or reopens)
    args._last_ts = last_ts                      # Task 6's staleness input
    # N3/N10: all-done fires whenever the job is complete and not held — no longer
    # gated on a same-cycle transition (follow() exits on all-done, so no repeats).
    # An unconfirmed final close holds it until the transcript has been quiet for
    # staleAfterMin; no transcript timestamp at all never holds (fail-soft).
    tasks_now = [t for t in state.get("tasks", []) if isinstance(t, dict)]
    complete = bool(tasks_now) and all(t.get("status") == "done" for t in tasks_now)
    held = False
    if complete and any(t.get("unconfirmed") for t in tasks_now):
        if last_ts is not None:
            held = (now - last_ts).total_seconds() / 60.0 < _stale_threshold(args, state)
    args._hold_all_done = held
    all_done_now = complete and not held
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
        mains, subs = transcript_files(state, args.projects_dir)
        events, last_ts = extract_events(mains, subs)
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
    """The synthetic parallel-group row, or None while the group is not yet
    fully and CONFIRMEDLY closed.

    D2 at group level: an `unconfirmed` member is a display close — its span is
    dispatch-derived, it logged no individual row, and it stays revisitable. It
    must not contribute to the group row either, for two reasons:
      1. no double log. Individual tasks are protected implicitly (once
         status == "done" plan_transitions skips them forever), but this row
         keys on the GROUP, not the task: without this guard a member that
         later upgrades unconfirmed -> confirmed re-enters handle_completion,
         finds every member `done` again, and appends a SECOND parallel-group
         row for the same work (reproduced 2026-08-14: rows of 12.0 and 20.0
         actualMin for one 2-member group).
      2. no timing-dependent data. Displays are applied AFTER completions in
         sync_cycle, so identical transcript evidence seen in one cycle
         produced no row while the same evidence split across two produced
         one. The row now lands at exactly one point in the group's life --
         the moment its last member confirms -- whatever the watcher's cadence.
    "No data beats biased data": the group simply logs nothing unless every
    member's close is todo-evidenced."""
    members = [t for t in state.get("tasks", [])
               if isinstance(t, dict) and t.get("group") == group_id]
    if not members or any(t.get("status") != "done" for t in members):
        return None
    if any(t.get("unconfirmed") for t in members):
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


def handle_completion(state_path, state, t, started, finished, args, spans=None):
    """A CONFIRMED (todo-evidenced) close, D1 — the only path that ever logs a
    calibration row. `started`/`finished` are the full task span (D3: review
    passes, fix rounds and the lead's own edit/test/commit are the task's real
    cost); `spans` are the matched delegated spans, display metadata only.

    Today's checkpoint order, in code: (b) done-marker -> (b2) tokens+alias ->
    (c) append -> (d) actualMin. Every step past (b) is fail-soft."""
    if state.get("source") == "c":
        return          # spec §5.1: Source C never logs calibration — hard guard

    # (b) durable done-marker FIRST
    t["status"] = "done"
    t.pop("unconfirmed", None)              # a todo close is confirmed by definition
    dm = _delegated_min(spans)
    if dm is not None:
        t["delegatedMin"] = dm
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
    if tokens and tokens.get("available"):
        entries = tokens.get("tasks") or []
        models = (entries[0].get("models") or []) if entries else []
        ids = [m.get("id") for m in models if isinstance(m.get("id"), str)]
        if _is_alias(t.get("model")):
            # N9: same-family match anywhere in the window, not just the busiest
            # model — the lead's own usage is usually models[0] on delegated tasks.
            top = next((mid for mid in ids if t["model"] in mid), None)
            if top:
                t["model"] = top
                write_state(state_path, state)
        elif t.get("model") is None and len(ids) == 1:
            # no declared/dispatch model at all: a task window that saw exactly
            # ONE model is unambiguous evidence. Two or more -> stays null
            # ("unknown" in the row) rather than guessing the busiest one.
            t["model"] = ids[0]
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
            dmin = t.get("delegatedMin")                 # D3: alongside, never instead
            if isinstance(dmin, (int, float)) and not isinstance(dmin, bool):
                # bool is an int in Python: a corrupt/hand-edited state carrying
                # `"delegatedMin": true` would otherwise reach Task 2's validator
                # and sink the ENTIRE row (no row, actualMin null).
                row["delegatedMin"] = dmin
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
    all_done = bool(tasks) and done == len(tasks) \
        and not getattr(args, "_hold_all_done", False)
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
    # D4: the model's whole wake job on a changed event is publishing the
    # artifact this render just wrote -- but the 2026-08-14 run showed the
    # model can simply keep working instead. Flag it back when the PREVIOUS
    # changed event is still unpublished, riding an event the model already
    # reads (zero extra per-boundary protocol). Source A only: lastPublishedAt
    # is only ever written by sync_cycle's Source-A publish scan -- Source B
    # never reads the session transcript at all, and Source C's mirror path
    # doesn't run that scan either, so both would show a permanently-None
    # lastPublishedAt and false-positive on EVERY changed event after the
    # first if this weren't gated.
    if state.get("source", "a") == "a" and (changed or all_done) \
            and state.get("publish") is not False and state.get("artifactUrl"):
        prev = token_usage.parse_ts(state.get("lastChangedEventAt"))
        lastpub = token_usage.parse_ts(state.get("lastPublishedAt"))
        if prev is not None and (lastpub is None or lastpub < prev):
            ev["publishLag"] = True
            if lastpub is not None:
                ev["sincePublishMin"] = round((now - lastpub).total_seconds() / 60.0, 1)
    if changed or all_done:
        state["lastChangedEventAt"] = now.isoformat()
        write_state(state_path, state)
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


def _stale_threshold(args, state):
    """The staleAfterMin used both by check_staleness/check_idle (via follow's
    resolution) and N3's quiet-transcript grace: an explicit --stale-min wins,
    else the state's own staleAfterMin, else DEFAULT_STALE_MIN."""
    v = getattr(args, "stale_min", None)
    if v is None:
        sv = state.get("staleAfterMin")
        v = sv if isinstance(sv, (int, float)) and sv > 0 else DEFAULT_STALE_MIN
    return v


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


def check_idle(state_path, state, now, stale_min, args):
    """D5 (2026-08-14 rework): between-task dead zones -- transcript activity
    can be busy while NO task is in flight (model working off-plan, between
    subtasks) -- are invisible to check_staleness, which only watches running
    tasks. Anchored on the last task BOUNDARY, not transcript activity, so a
    session that's busy but drifting off-plan still fires. ONE event per gap,
    persisted as idleNotifiedAt (additive job-level field; re-arms only when
    the anchor moves past the last notification, i.e. a genuinely new gap).

    Source A only (my ruling, not the original brief): Source B's between-
    phase gaps are journal-driven and normal, with no todo to point the model
    at, and Source C is mirror-only/never-calibrated -- both would misfire.
    One-shot mode never calls this at all, matching its existing no-staleness
    posture (see follow(), the only call site)."""
    if state.get("source", "a") != "a":
        return []
    tasks = [t for t in state.get("tasks", []) if isinstance(t, dict)]
    if not tasks or any(t.get("status") == "running" for t in tasks) \
            or not any(t.get("status") == "pending" for t in tasks):
        return []
    # D0/D5: `resumedAt` is a CO-EQUAL anchor candidate, not a fallback. A
    # resumed job's last done task finished BEFORE the pause, so treating
    # resumedAt as an else-branch made a resume fire `idle` seconds later with
    # the whole pause counted as idle time (10:05 -> 14:00 pause reproduced as
    # idleMin 235.5 half a minute after resume) -- and under --exit-on-event
    # that burns the L2 ladder's single permitted relaunch on cycle 1. Pause
    # time is its own bucket (references/resume.md, and the artifact's own
    # pause-adjusted orchestration line); the event must not double-count it as
    # dead time. parse_ts is total and always returns aware datetimes, so the
    # max() is safe; startedAt still covers the never-resumed, nothing-done job.
    cands = [token_usage.parse_ts(t.get("finishedAt"))
             for t in tasks if t.get("status") == "done"]
    cands.append(token_usage.parse_ts(state.get("resumedAt")))
    cands = [c for c in cands if c is not None]
    anchor = max(cands) if cands else token_usage.parse_ts(state.get("startedAt"))
    if anchor is None:
        return []
    notified = token_usage.parse_ts(state.get("idleNotifiedAt"))
    if notified is not None and notified >= anchor:
        return []
    idle = (now - anchor).total_seconds() / 60.0
    if idle < stale_min:
        return []
    state["idleNotifiedAt"] = now.isoformat()
    write_state(state_path, state)
    ev = {"event": "idle", "idleMin": round(idle, 1),
          "nextTask": next((t.get("name") for t in tasks
                            if t.get("status") == "pending"), None)}
    emit(ev)
    return [ev]


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
    stale_min = _stale_threshold(a, first)
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
                stale_evs += check_idle(a.state, state, now, stale_min, a)  # D5 (source gate lives inside)
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
