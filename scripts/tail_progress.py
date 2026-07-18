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
    # D11: all-done bypasses the debounce gate outright (and finish_cycle's own
    # render call catches a same-cycle slipAlert too, since etaAlertSent gates
    # on job-level state.status, not on task-level done==total) — only a plain
    # progress completion stays subject to _render_ok.
    all_done_now = bool(completions) and bool(state.get("tasks")) and all(
        isinstance(t, dict) and t.get("status") == "done" for t in state["tasks"])
    if (changed and getattr(args, "_render_ok", True)) or getattr(args, "_force_render", False) \
            or all_done_now:
        args._pending_names, args._pending = [], False
        out.extend(finish_cycle(state_path, state, now, last_ts, changed, just_done, args))
    elif changed:
        args._pending, args._pending_names = True, just_done   # debounced; coalesced later
    return state, out


def _is_alias(model):
    """Bare dispatch alias ('haiku') vs full versioned id ('claude-haiku-...')."""
    return isinstance(model, str) and bool(model) and "claude" not in model


def _project_name(state_path):
    root = os.path.dirname(os.path.dirname(os.path.abspath(state_path)))
    return os.path.basename(os.path.realpath(root))


def _base_row(state, state_path, started, finished):
    return {
        "date": (token_usage.parse_ts(finished) or datetime.now(timezone.utc))
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


def _render_out_path(state):
    af = state.get("artifactFile")
    if isinstance(af, str) and os.path.isabs(af):
        return af
    return os.path.join(tempfile.gettempdir(),
                        "whendone-render-%s.html" % (state.get("jobId") or "job"))


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
        try:
            with open(out_path + ".tokens.json", "w", encoding="utf-8") as f:
                json.dump(held, f)
            tok_arg = out_path + ".tokens.json"
        except OSError:
            tok_arg = "-"
    elif os.path.exists(out_path + ".tokens.json"):
        tok_arg = out_path + ".tokens.json"              # reuse last refresh on drift renders

    status = render_now(state_path, tok_arg, out_path, now, state)
    ev = {"event": "all-done" if all_done else "progress", "done": done,
          "total": len(tasks), "changed": changed, "justDone": just_done,
          "rendered": status is not None}
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
    now = token_usage.parse_ts(a.now) or datetime.now(timezone.utc)
    state, events = sync_cycle(a.state, now, a)
    if state is None:
        return 2
    if any(e.get("event") == "ownership-lost" for e in events):
        return 3
    return 0


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except OSError as e:
        return e.errno == errno.EPERM      # exists, not ours
    return True


def acquire_lock(state_path):
    """O_EXCL pid lockfile beside the state file. None -> a LIVE tailer owns it.
    A dead pid's lock is removed and re-acquired (crashed watcher)."""
    lock = os.path.join(os.path.dirname(os.path.abspath(state_path)), "whendone-tail.lock")
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
            now = datetime.now(timezone.utc)
            mono = time.monotonic()
            a._render_ok = (mono - last_emit) >= a.debounce
            a._force_render = bool(getattr(a, "_pending", False) and a._render_ok)
            state, events = sync_cycle(a.state, now, a)
            woke = [e for e in events if e.get("event") in ("progress", "all-done")]
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
