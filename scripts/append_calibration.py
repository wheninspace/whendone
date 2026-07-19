#!/usr/bin/env python3
"""Validate and append one completed-subtask row to calibration.jsonl.

Usage: python3 append_calibration.py <tmpfile.json>

Reads ONE JSON object from <tmpfile.json> — the model writes that file with the
Write tool, which treats content as data, so no untrusted `project`/`job` string is
ever spliced into shell or Python source (the injection surface the printf/heredoc
append pattern had). Validates the object (single JSON object; `category` in the
fixed taxonomy; `rawEstimateMin` numeric/finite; `startedAt`/`finishedAt` ISO 8601
strings), computes `actualMin` itself from the two timestamps — never trusts model
arithmetic for the number that feeds the append-only calibration log — and appends
the canonical `json.dumps` line to calibration.jsonl in UTF-8.

On success, prints exactly two lines to stdout so the checkpoint's single call still
yields everything it needs: (1) the computed `actualMin` (or the literal `null` on
clock skew) for the state file's own `actualMin` field — kept identical to the
logged value instead of a second, possibly-divergent computation; (2) the current
timestamp (`date -Iseconds` equivalent) for the next subtask's `startedAt`.

Clock skew (`finishedAt` before `startedAt`, e.g. system clock moved back):
`actualMin` is logged as `null` — an excluded data point, never a wrong-but-finite
duration and never silently dropped.

On any validation failure: print one line to stderr, append nothing, exit 1. The
skill's error table already tolerates a lost calibration write.

The target directory is `~/.claude/whendone-data` by default, overridable via the
`WHENDONE_DATA_DIR` environment variable or the `data_dir` argument to `append()` —
tests must always override it so they never touch the real, shared calibration log.
"""
import json, math, os, sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from calibration_summary import CATEGORIES, PARALLEL

DEFAULT_DATA_DIR = os.path.expanduser("~/.claude/whendone-data")


def _parse_ts(s):
    """Parse an ISO 8601 timestamp (Z or numeric offset). None if not a valid string."""
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def build_row(obj):
    """Validate the parsed JSON object. Returns (row, None) or (None, error_message)."""
    if not isinstance(obj, dict):
        return None, "expected a single JSON object"

    category = obj.get("category")
    if not isinstance(category, str) or (category not in CATEGORIES and category != PARALLEL):
        return None, f"invalid category: {category!r}"

    raw_est = obj.get("rawEstimateMin")
    if (not isinstance(raw_est, (int, float)) or isinstance(raw_est, bool)
            or not math.isfinite(raw_est)):
        return None, f"invalid rawEstimateMin: {raw_est!r}"

    started_raw, finished_raw = obj.get("startedAt"), obj.get("finishedAt")
    started, finished = _parse_ts(started_raw), _parse_ts(finished_raw)
    if started is None or finished is None:
        return None, "startedAt/finishedAt must be ISO 8601 strings"

    delta_min = (finished - started).total_seconds() / 60.0
    # Clock skew (finishedAt before startedAt): null, never a floored-to-0.5 duration.
    actual_min = None if delta_min < 0 else round(max(delta_min, 0.5), 1)

    row = {
        "date": obj.get("date", ""),
        "project": obj.get("project", ""),
        "job": obj.get("job", ""),
        "category": category,
        "rawEstimateMin": raw_est,
        "startedAt": started_raw,
        "finishedAt": finished_raw,
        "actualMin": actual_min,
        "model": obj.get("model", "unknown"),
        "client": obj.get("client", "unknown"),
    }
    effort = obj.get("effort")
    if effort is not None:
        row["effort"] = effort
    # M22: parallel-group synthetic rows carry the ETA rule's actual operands
    # (max-of-adjusted, sum-of-adjusted estimates) so calibration_summary.py can report
    # wall/max-adjusted and wall/sum-adjusted medians. Optional (omitted for ordinary
    # category rows and pre-M22 callers) but must be numeric/finite when present —
    # rejected loudly, same as rawEstimateMin, rather than silently dropped or coerced.
    for key in ("maxAdjusted", "sumAdjusted"):
        value = obj.get(key)
        if value is not None:
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value)):
                return None, f"invalid {key}: {value!r}"
            row[key] = value
    return row, None


def append_obj(obj, data_dir=None):
    """Validate an already-parsed object and append it. Same return contract
    as append(): (ok, row_dict_or_error_message)."""
    resolved_dir = data_dir or os.environ.get("WHENDONE_DATA_DIR") or DEFAULT_DATA_DIR
    row, err = build_row(obj)
    if err:
        return False, err
    try:
        os.makedirs(resolved_dir, mode=0o700, exist_ok=True)
        if os.name == "posix":
            os.chmod(resolved_dir, 0o700)  # makedirs' mode arg skips already-existing dirs
        log_path = os.path.join(resolved_dir, "calibration.jsonl")
        fd = os.open(log_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        if os.name == "posix":
            os.chmod(log_path, 0o600)  # tighten a pre-existing 0644 log
    except OSError as e:
        return False, f"cannot append to calibration.jsonl: {e}"
    return True, row


def append(tmp_path, data_dir=None):
    """Read one JSON object from tmp_path and append it via append_obj()."""
    try:
        with open(tmp_path, encoding="utf-8") as f:
            obj = json.load(f, parse_constant=lambda _: None)  # NaN/Inf -> None -> rejected
    except (OSError, json.JSONDecodeError) as e:
        return False, f"cannot read {tmp_path}: {e}"
    return append_obj(obj, data_dir=data_dir)


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 1
    ok, result = append(argv[1])
    if not ok:
        print(result, file=sys.stderr)
        return 1
    actual_min = result["actualMin"]
    print("null" if actual_min is None else actual_min)
    print(datetime.now().astimezone().isoformat(timespec="seconds"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
