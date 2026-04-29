# Task Annotations

Per spec §6.4, task difficulty annotations must be frozen before experiment execution.

## Method

For MuSiQue tasks, difficulty_bucket is deterministically mapped from hop count:
- 2-hop → local_readable
- 3-hop → multi_file
- 4-hop → long_context

Note: multi_hypothesis bucket is not represented in current MuSiQue selection.
Will be added via HotpotQA comparison tasks if needed.

## Dual Annotation Status

Stage 1A: Single annotator (deterministic mapping). 
Formal dual-annotator protocol (§6.4) with Cohen's κ measurement will be performed before Stage 2.

## Freeze

Date: 2026-04-29
Commit: (see git history)
