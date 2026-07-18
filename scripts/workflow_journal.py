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
