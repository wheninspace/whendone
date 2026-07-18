#!/usr/bin/env python3
"""Source-A tailer/watcher for whendone: declare-once, tail-thereafter (stage 3).

Usage:
  python3 tail_progress.py <whendone-state.json> [--now ISO]          # one-shot sync (L3)
  python3 tail_progress.py <whendone-state.json> --follow             # watcher loop (L1)
  python3 tail_progress.py <state> --follow --exit-on-event           # bg-Bash mode (L2)

Observes the session transcript(s) named by the state file's sessionIds: TodoWrite
status transitions and subagent (Task/Agent tool) completions are matched to the
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
names, todo content/status, timestamps, tool_use ids) is read; conversation
prose is never extracted.
"""
import argparse, contextlib, errno, io, json, os, re, sys, tempfile, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import token_usage
import append_calibration
import render_artifact

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
                                elif b.get("name") in DISPATCH_TOOLS:
                                    events.append((ts, "dispatch", {
                                        "id": b.get("id"),
                                        "description": (b.get("input") or {}).get("description")}))
                        elif etype == "user":
                            for b in content:
                                if isinstance(b, dict) and b.get("type") == "tool_result":
                                    events.append((ts, "result",
                                                   {"tool_use_id": b.get("tool_use_id")}))
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError):
                        continue
        except OSError:
            continue
    events.sort(key=lambda ev: ev[0])
    return events, last_ts


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
    if state.get("source", "a") != "a":
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
    just_done = (getattr(args, "_pending_names", None) or []) + just_done
    if (changed and getattr(args, "_render_ok", True)) or getattr(args, "_force_render", False):
        args._pending_names, args._pending = [], False
        out.extend(finish_cycle(state_path, state, now, last_ts, changed, just_done, args))
    elif changed:
        args._pending, args._pending_names = True, just_done   # debounced; coalesced later
    return state, out


def handle_completion(state_path, state, t, started, finished, args):
    # Task 4 replaces this with the full crash-ordered pipeline.
    t["status"] = "done"
    if started and not t.get("startedAt"):
        t["startedAt"] = started
    t["finishedAt"] = finished
    write_state(state_path, state)


def finish_cycle(state_path, state, now, last_ts, changed, just_done, args):
    # Task 5 replaces this with render + slip + real event payloads.
    tasks = state.get("tasks", [])
    done = sum(1 for t in tasks if isinstance(t, dict) and t.get("status") == "done")
    name = "all-done" if tasks and done == len(tasks) else "progress"
    ev = {"event": name, "done": done, "total": len(tasks),
          "changed": changed, "justDone": just_done}
    emit(ev)
    return [ev]


def one_shot(a):
    a._force_render = True        # L3 boundary refresh always renders (ETA drifts with time)
    now = token_usage.parse_ts(a.now) or datetime.now(timezone.utc)
    state, events = sync_cycle(a.state, now, a)
    if state is None:
        return 2
    if any(e.get("event") == "ownership-lost" for e in events):
        return 3
    return 0


def follow(a):      # replaced in Task 6
    print(json.dumps({"event": "error", "reason": "follow not implemented"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
