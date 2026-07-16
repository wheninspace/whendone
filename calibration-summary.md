# Calibration summary

Regenerated: never (initial version, 0 data points). Regenerated from calibration.jsonl by
scripts/calibration_summary.py at every job end. NOTE: this is the initial template that ships
with the skill — the live version lives in `~/.claude/pacekeeper-data/` and is created from
this template on first run.

## Per category

| Category | Default estimate | Factor (blended) | Data points | Confidence | Spread (IQR) |
|---|---|---|---|---|---|
| debugging | 20 min | — | 0 | — | — |
| deploy-infra | 15 min | — | 0 | — | — |
| documentation | 10 min | — | 0 | — | — |
| judgment-coding | 12 min | — | 0 | — | — |
| mechanical-implementation | 5 min | — | 0 | — | — |
| research | 15 min | — | 0 | — | — |
| review | 10 min | — | 0 | — | — |
| testing | 8 min | — | 0 | — | — |

## How to use when estimating

Produce the raw estimate from the default table FIRST (adjusted only for the subtask's scope),
then multiply by the category factor. Uncertainty on the total ETA: confidence low -> +/-50 %,
medium -> +/-30 %, high -> shrink toward the IQR. Never state a point time without an interval,
and never mention factor values in chat or artifact.

The Default estimate column holds frozen anchor values — learning lives in the factor, never in
changed defaults.
