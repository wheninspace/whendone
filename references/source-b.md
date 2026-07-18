# Source B — Workflow-engine runs (stage 4, not yet shipped)

Declared per-phase estimates + categories at Workflow launch; a journal tailer maps the
engine's `journal.jsonl` (undocumented format — parsed defensively, version-detected) onto
the same state model, giving measured per-agent parallelism. Until stage 4 ships, a
Workflow-engine run gets no whendone tailing: say so and offer the chat table at phase
boundaries the lead observes itself.
