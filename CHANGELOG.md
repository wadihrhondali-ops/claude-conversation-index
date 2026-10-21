# Changelog

## 1.0.0 — 2026-10-21

Initial public release.

- `build_index`: combine an official Claude.ai export, a user-supplied
  conversation-to-project mapping, a previous index, and optional delta
  files into one CSV index, keyed by conversation `uuid`.
- Union of rows is `previous index ∪ export ∪ mapping ∪ deltas`: a
  conversation missing from a fresh (e.g. partial) export is never dropped
  — its content fields are carried over from the previous index.
- Delta fields (`title`, `project_uuid`, `project_name`) are only applied
  when at least as recent as the row they would replace; the mapping
  always wins over a delta's project.
- Excerpts of conversation content (`first_message_excerpt`,
  `summary_excerpt`) are off by default; enable with `--with-excerpts`.
- `consult`: search the index by title/summary or, with `--content`, by
  full message text; warns explicitly when the loaded export only
  partially covers the index.
- `tools/make_fixture.py` generates a fully fictional example dataset used
  by the test suite and by `examples/`.
