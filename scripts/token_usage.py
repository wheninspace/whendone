#!/usr/bin/env python3
"""Report token usage per whendone subtask from Claude Code session transcripts.

Usage: python3 token_usage.py <whendone-state.json>

Reads sessionIds + task startedAt/finishedAt windows from the state file, locates
~/.claude/projects/*/<sessionId>.jsonl (plus <dir>/<sessionId>/subagents/agent-*.jsonl),
dedups assistant entries by message.id (keep LAST — streaming snapshots repeat ids with
lower counts), and buckets usage into task windows, listing the distinct models observed
per window (ordered by output tokens). Prints one JSON line to stdout.

Honesty notes baked into the output: "freshInput" = input_tokens + cache_creation (the
expensive kind); cacheRead is reported separately (≈10x cheaper — never sum them into one
number). No USD: subscribers don't pay per token, and price tables go stale.
The transcript format is undocumented and version-drifty: parse defensively, and on ANY
problem return {"available": false} — token display must never block a job.
Privacy: only message.usage numbers and the message.model string are read; conversation
content is never extracted.
"""
import glob, json, os, re, sys
from datetime import datetime, timezone

BUCKETS = ("output", "freshInput", "cacheRead")


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
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


def read_usage(path):
    """Dedup by message.id keeping the last entry; yield (ts, output, fresh, cacheread, model)."""
    seen = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    e = json.loads(line)
                    if e.get("type") != "assistant":
                        continue
                    m = e["message"]; u = m["usage"]
                    mdl = m.get("model")
                    seen[m.get("id") or e.get("requestId") or len(seen)] = (
                        parse_ts(e.get("timestamp")),
                        int(u.get("output_tokens") or 0),
                        int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0),
                        int(u.get("cache_read_input_tokens") or 0),
                        mdl if isinstance(mdl, str) else None,
                    )
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
    except OSError:
        return []
    return [v for v in seen.values() if v[0] is not None]


def summarize(state_path, projects_dir=None):
    try:
        state = json.load(open(state_path, encoding="utf-8"))
        sids = [s for s in state.get("sessionIds", []) if isinstance(s, str) and re.fullmatch(r"[A-Za-z0-9_-]+", s)]
        tasks = state.get("tasks", [])
        if not isinstance(tasks, list):
            tasks = []
    except (OSError, json.JSONDecodeError, AttributeError):
        return {"available": False, "reason": "state file unreadable"}
    projects_dir = projects_dir or os.path.expanduser("~/.claude/projects")
    main_entries, sub_entries = [], []
    for sid in sids:
        for t in glob.glob(os.path.join(projects_dir, "*", sid + ".jsonl")):
            main_entries += read_usage(t)
            for sa in glob.glob(os.path.join(os.path.dirname(t), sid, "subagents", "agent-*.jsonl")):
                sub_entries += read_usage(sa)
    if not main_entries and not sub_entries:
        return {"available": False, "reason": "no transcript found for session ids"}

    def bucket(entries, start, end):
        tot = dict.fromkeys(BUCKETS, 0)
        by_model = {}
        for ts, out, fresh, cr, model in entries:
            if (start is None or ts >= start) and (end is None or ts < end):
                tot["output"] += out; tot["freshInput"] += fresh; tot["cacheRead"] += cr
                if model:
                    by_model[model] = by_model.get(model, 0) + out
        tot["models"] = [{"id": m, "display": display_name(m)}
                         for m, _ in sorted(by_model.items(), key=lambda kv: (-kv[1], kv[0]))]
        return tot

    result_tasks = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        s, e = parse_ts(t.get("startedAt")), parse_ts(t.get("finishedAt"))
        if s is None:
            continue
        result_tasks.append({"nr": t.get("nr"), **bucket(main_entries + sub_entries, s, e)})
    return {"available": True,
            "job": bucket(main_entries + sub_entries, None, None),
            "tasks": result_tasks,
            "subagents": bucket(sub_entries, None, None)}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr); sys.exit(1)
    res = summarize(sys.argv[1])
    print(json.dumps(res))
    sys.exit(0 if isinstance(res, dict) else 1)
