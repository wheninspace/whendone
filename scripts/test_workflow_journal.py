#!/usr/bin/env python3
"""Tests for workflow_journal.py — Source-B journal/transcript readers.

FIXTURE PROVENANCE: the shapes below are copied from a live Workflow run's files
on 2026-07-18 (format-probe run wf_6d1be0bd-c7d), cross-checked against 17 runs
spanning 5 weeks / 7 projects. journal.jsonl schema v2. If FormatAssumptionTest
fails, the ENGINE FORMAT HAS DRIFTED — do not "fix" the fixture to match new
output without re-running the format survey and bumping the version detector.

The probe run's agent-*.meta.json carried only {agentType, spawnDepth} (no model
key this run); META_WITH_MODEL adds the model field observed in the same run's
transcript ("claude-opus-4-8") to exercise the model reader — the survey records
model as sometimes-present in meta.json.
"""
import contextlib, io, json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workflow_journal as wj
import token_usage

HEX64 = "5a2e809010783ddc1ef7e3509048dbe2ca8b8b6afa9ff6cb92d071d53a69048d"
AID1, AID2 = "af1d39ab276777fa9", "ac0cefc6ebe0852e1"
J_STARTED = {"type": "started", "key": "v2:" + HEX64, "agentId": AID1}
J_RESULT = {"type": "result", "key": "v2:" + HEX64, "agentId": AID1,
            "result": "ok"}
T0, T1 = "2026-07-18T17:01:07.616Z", "2026-07-18T17:01:10.781Z"
AGENT_FIRST_LINE = {"type": "user", "timestamp": T0, "agentId": AID1,
                    "sessionId": "fa4e497b-c0c9-4a0a-9bc3-0a8921441128",
                    "message": {"role": "user",
                                "content": "[wd:probe] Reply with exactly the word ok. Nothing else."}}
AGENT_LAST_LINE = {"type": "assistant", "timestamp": T1, "agentId": AID1,
                   "message": {"role": "assistant", "content": [
                       {"type": "text", "text": "ok"}]}}
META_WITH_MODEL = {"agentType": "workflow-subagent", "spawnDepth": 1,
                   "model": "claude-opus-4-8"}


def write_lines(path, objs):
    with open(path, "w", encoding="utf-8") as f:
        for o in objs:
            f.write(json.dumps(o) + "\n")


if __name__ == "__main__":
    unittest.main()
