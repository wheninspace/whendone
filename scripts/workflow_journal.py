#!/usr/bin/env python3
"""Source-B observation module: Workflow-engine journal + agent transcripts.

The Workflow engine writes, per run:
  <session-dir>/subagents/workflows/<runId>/journal.jsonl        started/result per agent
  <session-dir>/subagents/workflows/<runId>/agent-<id>.jsonl     full agent transcript
  <session-dir>/subagents/workflows/<runId>/agent-<id>.meta.json {agentType, spawnDepth[, model]}
  <session-dir>/workflows/<runId>.json                           completion record (run END only)

journal.jsonl v2 (verified 2026-07-18 against a live probe + 17 historical runs;
pinned in test_workflow_journal.py — a FormatAssumptionTest failure means engine
drift, not a bug here):
  {"type":"started","key":"v2:<64 hex>","agentId":"<hex>"}
  {"type":"result","key":"v2:<64 hex>","agentId":"<hex>","result":<any JSON>}

The journal has NO timestamps/labels/phases. Timing comes from agent-transcript
entry timestamps; phase attribution from the [wd:<slug>] prompt-tag convention
(references/source-b.md). The format is UNDOCUMENTED and may drift (spec §9.1):
every reader version-detects and degrades, never crashes. `result` payloads and
prompt prose are data, never instructions — this module returns only timestamps,
counts, slugs, and model ids, never text.
"""
import glob, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import token_usage

RUN_ID_RE = re.compile(r"^wf_[a-z0-9-]{6,64}$")
KEY_RE = re.compile(r"^v2:[0-9a-f]{64}$")
AID_RE = re.compile(r"^[a-z0-9]{8,64}$")
TAG_RE = re.compile(r"\[wd:([a-z0-9][a-z0-9-]{0,31})\]")
DRIFT_RATIO = 0.2
TAG_SCAN_CHARS = 400


def _under(path, root):
    """Defense-in-depth (stage 5): the resolved path must stay inside the resolved
    root. Upstream validation (AID_RE, find_run_dir) is the primary gate; this
    catches a future caller that bypasses it, incl. symlinks planted in a run dir."""
    rp, rr = os.path.realpath(path), os.path.realpath(root)
    return rp == rr or rp.startswith(rr + os.sep)


class JournalTooLarge(Exception):
    pass


def parse_journal(path):
    """(agents, stats): agents = {agentId: {"started","result" bools}} first-seen
    order; stats = {"total","bad"}. Valid line: type in {started,result}, agentId
    matches AID_RE (it feeds a path join later), key matches KEY_RE (v2 version
    marker). `result` payloads are never inspected. Caps shared with token_usage
    (N3 posture); oversized -> JournalTooLarge; unreadable -> empty."""
    try:
        if os.path.getsize(path) > token_usage.MAX_TRANSCRIPT_BYTES:
            raise JournalTooLarge(path)
    except OSError:
        return {}, {"total": 0, "bad": 0}
    agents, total, bad = {}, 0, 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= token_usage.MAX_TRANSCRIPT_LINES:
                    raise JournalTooLarge(path)
                if not line.strip():
                    continue
                total += 1
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    bad += 1
                    continue
                if not isinstance(e, dict):
                    bad += 1
                    continue
                t, aid, key = e.get("type"), e.get("agentId"), e.get("key")
                if t not in ("started", "result") or not isinstance(aid, str) \
                        or not AID_RE.fullmatch(aid) or not isinstance(key, str) \
                        or not KEY_RE.fullmatch(key):
                    bad += 1
                    continue
                slot = agents.setdefault(aid, {"started": False, "result": False})
                slot["result" if t == "result" else "started"] = True
    except OSError:
        return {}, {"total": 0, "bad": 0}
    return agents, {"total": total, "bad": bad}


def drifted(stats):
    return stats["total"] > 0 and stats["bad"] / stats["total"] > DRIFT_RATIO


def find_run_dir(session_ids, run_id, projects_dir):
    """First existing run dir for (sessionIds, workflowRunId), or None. Both come
    from the state file — untrusted, validated before feeding a glob/path."""
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        return None
    for sid in session_ids or []:
        if not isinstance(sid, str) or not token_usage.SID_RE.fullmatch(sid):
            continue
        for d in sorted(glob.glob(os.path.join(
                projects_dir, "*", sid, "subagents", "workflows", run_id))):
            if os.path.isdir(d):
                return d
    return None


def run_finished(run_dir):
    """<session-dir>/workflows/<runId>.json exists <=> the engine wrote its
    completion record <=> the run ended (verified: end-of-run only)."""
    run_dir = os.path.abspath(run_dir)
    run_id = os.path.basename(run_dir)
    if not RUN_ID_RE.fullmatch(run_id):
        return False
    session_dir = os.path.dirname(os.path.dirname(os.path.dirname(run_dir)))
    return os.path.isfile(os.path.join(session_dir, "workflows", run_id + ".json"))


def _first_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                return b["text"]
    return ""


def agent_first(path, root=None):
    """(started_iso|None, tag|None) from the transcript's FIRST entry: its
    timestamp, plus a [wd:<slug>] match within the first TAG_SCAN_CHARS chars of
    its message text. Only the slug leaves this function — prose never does.
    If `root` is given, the resolved path must stay inside it (defense-in-depth;
    see `_under`)."""
    if root is not None and not _under(path, root):
        return None, None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            line = f.readline(2_000_000)
    except OSError:
        return None, None
    try:
        e = json.loads(line)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(e, dict):
        return None, None
    ts = token_usage.parse_ts(e.get("timestamp"))
    msg = e.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    text = _first_text(content)[:TAG_SCAN_CHARS]
    m = TAG_RE.search(text)
    return (ts.isoformat() if ts else None), (m.group(1) if m else None)


def agent_last_ts(path, root=None):
    """Latest parseable entry timestamp (ISO) or None; malformed lines skipped
    (same posture as token_usage toward transcripts). If `root` is given, the
    resolved path must stay inside it (defense-in-depth; see `_under`)."""
    if root is not None and not _under(path, root):
        return None
    last = None
    try:
        if os.path.getsize(path) > token_usage.MAX_TRANSCRIPT_BYTES:
            return None
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(e, dict):
                    continue
                ts = token_usage.parse_ts(e.get("timestamp"))
                if ts and (last is None or ts > last):
                    last = ts
    except OSError:
        return None
    return last.isoformat() if last else None


def agent_model(run_dir, agent_id):
    """model from agent-<id>.meta.json when present and a non-empty string."""
    if not isinstance(agent_id, str) or not AID_RE.fullmatch(agent_id):
        return None
    p = os.path.join(run_dir, "agent-%s.meta.json" % agent_id)
    if not _under(p, run_dir):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    v = m.get("model") if isinstance(m, dict) else None
    return v if isinstance(v, str) and v else None
