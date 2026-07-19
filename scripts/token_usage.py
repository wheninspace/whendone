#!/usr/bin/env python3
"""Report token usage per whendone subtask from Claude Code session transcripts.

Usage: python3 token_usage.py <whendone-state.json> [--task N]

Reads sessionIds + task startedAt/finishedAt windows from the state file, locates
~/.claude/projects/*/<sessionId>.jsonl (plus <dir>/<sessionId>/subagents/agent-*.jsonl),
dedups assistant entries by message.id (keep LAST — streaming snapshots repeat ids with
lower counts), and buckets usage into task windows, listing the distinct models observed
per window (ordered by output tokens). Entries whose model is "<synthetic>" (Claude
Code's error-placeholder model) or that carry zero output AND zero input tokens are
excluded from by_model/models but their token counts still count toward the window's
totals. Prints one JSON line to stdout.

Job/subagents windowing (C2): the "job" and "subagents" buckets are bounded below by the
state file's own `startedAt` (open-ended above), NOT the whole session — a session that did
unrelated work before the job started no longer inflates the headline. Accepted limitation:
this is a single lower bound, so time the session spent PAUSED mid-job (between a stop and a
resume, same session) is not subtracted back out and still counts toward the total. If the
state file has no parseable `startedAt`, this degrades gracefully to the old whole-session
behavior rather than failing.

--task N (C13): closed task windows are immutable (their entries' timestamps are fixed once
finishedAt is set), so re-emitting every prior task's frozen numbers at each checkpoint is
pure O(n^2) waste. With --task N, "tasks" contains only the entry for task N (or is empty if
N is not a known task nr); "job" and "subagents" are always included. Overlap detection (see
below) still runs over ALL tasks in the state file regardless of this filter. Without
--task, "tasks" contains every task with a parseable startedAt (unchanged default behavior,
used at job end and by any caller that wants the full table).

Cross-file dedup (M18): message.id dedup is applied across ALL transcript files feeding a
bucket (every sessionId a resumed job accumulates), not just within one file — later files
win ties. This matters only if a future Claude Code build ever replays prior turns into a
resumed session's transcript; today it is a no-op safety net.

Overlap detection (M1): a task's [startedAt, finishedAt) window intersecting any OTHER
task's window (parallel dispatch groups) means both windows' entry buckets double-count the
shared usage. Rather than attempt per-entry attribution (not reliably possible from usage
totals alone), affected task entries get `"overlap": true` so the artifact can show one
combined figure for the group instead of misleadingly-precise per-task numbers. A
still-running task (finishedAt is null) uses "now" as its window's end for this check only.

Input-size bound (N3): a single transcript file over MAX_TRANSCRIPT_BYTES (50 MB) or
MAX_TRANSCRIPT_LINES (200,000) is treated as pathological/hostile and the whole result
degrades to {"available": false} rather than parsing it into memory — consistent with every
other failure mode below; token display must never block the job.

Honesty notes baked into the output: "freshInput" = input_tokens + cache_creation (the
expensive kind); cacheRead is reported separately (≈10x cheaper — never sum them into one
number). No USD: subscribers don't pay per token, and price tables go stale.
The transcript format is undocumented and version-drifty: parse defensively, and on ANY
problem return {"available": false} — token display must never block a job.
Privacy: only message.usage numbers and the message.model string are read; conversation
content is never extracted.
"""
import glob, json, os, re, sys
from datetime import datetime

BUCKETS = ("output", "freshInput", "cacheRead")

# N3: per-file input-size bound. Either cap being exceeded degrades the whole result to
# {"available": false} — see docstring above.
MAX_TRANSCRIPT_BYTES = 50_000_000
MAX_TRANSCRIPT_LINES = 200_000


class TranscriptTooLarge(Exception):
    """Raised internally when a single transcript file exceeds the N3 size/line cap."""


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone()
    except (ValueError, TypeError, AttributeError):
        return None


def display_name(model_id):
    """'claude-haiku-4-5-20251001' -> 'Haiku 4.5'; alias 'haiku' -> 'Haiku'.

    Best-effort: drop any '[...]' annotation (e.g. the '[1m]' 1M-context marker)
    and a leading vendor/region prefix (e.g. 'us.anthropic.'), strip the 'claude-'
    prefix and a trailing deployment suffix ('-v1:0'), take the first alphabetic
    token as the name and the numeric tokens (excluding 8-digit dates) as the
    version. Unknown shapes pass through unchanged rather than erroring."""
    if not isinstance(model_id, str) or not model_id:
        return model_id
    s = re.sub(r"\[[^\]]*\]", "", model_id)      # drop '[1m]'-style annotations
    idx = s.find("claude")
    if idx != -1:
        s = s[idx:]                              # drop 'us.anthropic.'-style prefixes
    s = re.sub(r"-v\d+(?::\d+)?$", "", s)        # drop '-v1:0' deployment suffix
    parts = s.split("-")
    if parts and parts[0] == "claude":
        parts = parts[1:]
    name = next((p for p in parts if p.isalpha()), None)
    if not name:
        return model_id
    version = ".".join(p for p in parts if p.isdigit() and not re.fullmatch(r"\d{8}", p))
    return name.capitalize() + ((" " + version) if version else "")


def parse_transcript(path):
    """Yield (dedup_key, (ts, output, fresh, cacheread, model)) for assistant entries in one
    transcript file. No dedup is done here — callers merge across files (M18) by dedup_key,
    keeping the last value seen. Raises TranscriptTooLarge (N3) if this file alone exceeds
    MAX_TRANSCRIPT_BYTES or MAX_TRANSCRIPT_LINES; callers must let that propagate to an
    {"available": false} result rather than swallow it."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size > MAX_TRANSCRIPT_BYTES:
        raise TranscriptTooLarge(path)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= MAX_TRANSCRIPT_LINES:
                    raise TranscriptTooLarge(path)
                try:
                    e = json.loads(line)
                    if e.get("type") != "assistant":
                        continue
                    m = e["message"]; u = m["usage"]
                    mdl = m.get("model")
                    key = m.get("id") or e.get("requestId") or (path, i)
                    yield key, (
                        parse_ts(e.get("timestamp")),
                        int(u.get("output_tokens") or 0),
                        int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0),
                        int(u.get("cache_read_input_tokens") or 0),
                        mdl if isinstance(mdl, str) else None,
                    )
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
    except OSError:
        return


def compute_overlaps(valid_tasks, now):
    """valid_tasks: list of (nr, start, finishedAt_or_None). Returns the set of nrs whose
    [start, end) window intersects at least one OTHER task's window (M1) — a null
    finishedAt (still running) uses `now` as that window's end. O(n^2) over tasks, which is
    fine: task counts per job are tiny."""
    windows = [(nr, s, e if e is not None else now) for nr, s, e in valid_tasks]
    overlapping = set()
    for i in range(len(windows)):
        nr_i, s_i, e_i = windows[i]
        for j in range(i + 1, len(windows)):
            nr_j, s_j, e_j = windows[j]
            if s_i < e_j and s_j < e_i:
                overlapping.add(nr_i)
                overlapping.add(nr_j)
    return overlapping


SID_RE = re.compile(r"[A-Za-z0-9_-]+")


def transcript_paths(session_ids, projects_dir):
    """[(main_transcript, [subagent_transcripts])] for each valid session id.
    Invalid ids (anything not matching SID_RE fully) are dropped — they came
    from a state file, an untrusted source, and feed a glob."""
    out = []
    for sid in session_ids or []:
        if not isinstance(sid, str) or not SID_RE.fullmatch(sid):
            continue
        for t in glob.glob(os.path.join(projects_dir, "*", sid + ".jsonl")):
            subs = glob.glob(os.path.join(os.path.dirname(t), sid, "subagents", "agent-*.jsonl"))
            subs += glob.glob(os.path.join(os.path.dirname(t), sid, "subagents", "workflows", "wf_*", "agent-*.jsonl"))
            out.append((t, sorted(subs)))
    return out


def summarize(state_path, projects_dir=None, task_nr=None):
    try:
        with open(state_path, encoding="utf-8") as _sf:
            state = json.load(_sf)
        tasks = state.get("tasks", [])
        if not isinstance(tasks, list):
            tasks = []
    except (OSError, json.JSONDecodeError, AttributeError):
        return {"available": False, "reason": "state file unreadable"}
    projects_dir = projects_dir or os.path.expanduser("~/.claude/projects")
    # M18: dedup by message.id across ALL files feeding each bucket, not just within one
    # file — keep-last (a later file's value for the same id wins).
    main_seen, sub_seen = {}, {}
    try:
        for t, subs in transcript_paths(state.get("sessionIds", []), projects_dir):
            for key, val in parse_transcript(t):
                main_seen[key] = val
            for sa in subs:
                for key, val in parse_transcript(sa):
                    sub_seen[key] = val
    except TranscriptTooLarge:
        return {"available": False, "reason": "transcript exceeds size cap"}
    main_entries = [v for v in main_seen.values() if v[0] is not None]
    sub_entries = [v for v in sub_seen.values() if v[0] is not None]
    if not main_entries and not sub_entries:
        return {"available": False, "reason": "no transcript found for session ids"}

    def bucket(entries, start, end):
        tot = dict.fromkeys(BUCKETS, 0)
        by_model = {}
        for ts, out, fresh, cr, model in entries:
            if (start is None or ts >= start) and (end is None or ts < end):
                tot["output"] += out; tot["freshInput"] += fresh; tot["cacheRead"] += cr
                # Synthetic error-placeholder entries ("<synthetic>", or any model with
                # zero output AND zero input) are excluded from by_model/models — their
                # token counts (usually zero anyway) are still folded into tot above.
                if model and model != "<synthetic>" and not (out == 0 and fresh == 0):
                    by_model[model] = by_model.get(model, 0) + out
        tot["models"] = [{"id": m, "display": display_name(m)}
                         for m, _ in sorted(by_model.items(), key=lambda kv: (-kv[1], kv[0]))]
        return tot

    # C2: job/subagents buckets are lower-bounded by the job's own startedAt (falls back to
    # the old whole-session behavior — start=None — if the state file has none/unparseable).
    job_start = parse_ts(state.get("startedAt"))

    valid_tasks = []  # (nr, start, finishedAt_or_None)
    for t in tasks:
        if not isinstance(t, dict):
            continue
        s = parse_ts(t.get("startedAt"))
        if s is None:
            continue
        valid_tasks.append((t.get("nr"), s, parse_ts(t.get("finishedAt"))))

    # M1: overlap detection always runs over ALL tasks, independent of --task filtering.
    overlap_nrs = compute_overlaps(valid_tasks, datetime.now().astimezone())

    result_tasks = []
    for nr, s, e in valid_tasks:
        if task_nr is not None and nr != task_nr:
            continue
        entry = {"nr": nr, **bucket(main_entries + sub_entries, s, e)}
        if nr in overlap_nrs:
            entry["overlap"] = True
        result_tasks.append(entry)

    return {"available": True,
            "job": bucket(main_entries + sub_entries, job_start, None),
            "tasks": result_tasks,
            "subagents": bucket(sub_entries, job_start, None)}


if __name__ == "__main__":
    argv = sys.argv[1:]
    task_arg = None
    if len(argv) == 3 and argv[1] == "--task":
        try:
            task_arg = int(argv[2])
        except ValueError:
            print(__doc__, file=sys.stderr); sys.exit(1)
    elif len(argv) != 1:
        print(__doc__, file=sys.stderr); sys.exit(1)
    res = summarize(argv[0], task_nr=task_arg)
    print(json.dumps(res))
    sys.exit(0 if isinstance(res, dict) else 1)
