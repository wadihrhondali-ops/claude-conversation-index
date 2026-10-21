# Method

## `uuid` is the stable key

Every row of the index is keyed by the conversation's `uuid`. Titles
change, projects change, exports get regenerated — the uuid is the one
thing that is expected to stay stable across all of that, so every join
in this tool (export, mapping, previous index, deltas) is done on it.

## Index + deltas, not a full rebuild every time

Rebuilding the index from a full export every day does not scale well
once you have hundreds of conversations and large export files. The
intended workflow is:

1. Request a **full** export every so often (e.g. monthly) and build a
   fresh index from it, your mapping file, and your previous index.
2. In between, apply small **delta** CSVs (same six columns as the
   mapping file: `uuid,title,created_at,updated_at,project_uuid,project_name`)
   whenever you learn about a change (new conversation, retitled
   conversation, moved project) without re-exporting everything.

## Union of rows

A fresh index's rows are the union of:

```
previous index ∪ export ∪ mapping ∪ deltas
```

A conversation that is no longer covered by the fresh export (most
commonly because you used a time-limited export — see
[export-format.md](export-format.md)) is **not** dropped. Its content
fields (`inferred_title`, `message_count`, `summary_excerpt`,
`first_message_excerpt`) are carried over as-is from the previous index,
and `content_source` records where they came from (`export`, `previous
index`, or empty if neither source had anything). `source_line` similarly
records why the row exists at all, with a dedicated value, `previous
index only`, for a conversation found in no other source.

## Freshness rules for deltas

A delta's `title`, `project_uuid` and `project_name` only replace a row's
value when the delta's `updated_at` (parsed as ISO-8601) is **at least as
recent** as the row's own `updated_at`. A delta without an `updated_at`
is never applied to an existing row (only a brand-new uuid found only in
a delta is still added, with `source_line = delta only`). After a
successful merge, the row's final `updated_at` is the more recent of the
row's and the delta's.

Separately, the project from a delta is **never** applied to a
conversation that is present in the mapping file — the mapping is treated
as a complete, authoritative snapshot, so a delta cannot silently
re-assign a conversation's project out from under it. This only affects
`project_uuid`/`project_name`; `title` can still be updated by a fresh
delta regardless of whether the conversation is in the mapping.

One known characteristic, inherited unchanged from the logic this tool is
built on: `project_source` is not recomputed after a delta is merged in.
If a delta legitimately updates `project_name`/`project_uuid` for a
conversation that was *not* in the mapping, the row's `project_source`
still reads whatever the build step originally set (typically `not in
mapping`), even though the project fields themselves did change. This is
not a bug to work around in your own tooling — just something to be aware
of if you read `project_source` as a provenance label.

## Test fixtures

`tools/make_fixture.py` generates a fully fictional dataset (`examples/`)
with named "trap" conversations exercising each rule above: a delta older
than the row it targets (ignored), a delta newer than it (applied), a
conversation only in the previous index (kept), an untitled conversation
(title inferred from its summary), a conversation with no project, a
conversation created well before the fictional 30-day window but updated
inside it (present in the full export, absent from the windowed one), a
uuid found only in a delta, a delta without `updated_at` (ignored), a
conversation in the mapping targeted by a fresher delta that tries to
change its project (ignored — mapping wins) and the mirror case of a
conversation *not* in the mapping targeted by a fresher delta (applied).
See `examples/TRAPS.json` for the exact uuids.
