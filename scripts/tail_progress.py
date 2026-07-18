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
    a = p.parse_args(argv)
    if a.follow:
        return follow(a)          # Task 6
    return one_shot(a)            # Task 3


def one_shot(a):    # replaced in Task 3
    print(json.dumps({"event": "error", "reason": "one_shot not implemented"}))
    return 1


def follow(a):      # replaced in Task 6
    print(json.dumps({"event": "error", "reason": "follow not implemented"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
