# claude-conversation-index

Build and search a CSV index of your Claude.ai conversations, enriched
with your own conversation-to-project mapping. Standard library only, no
external dependencies, no network calls.

([Français](README.fr.md))

## What it's for

Claude.ai's own data export does not reliably tell you which project each
conversation belongs to (see [docs/export-format.md](docs/export-format.md)),
and a time-limited export only contains conversations *created* in that
window, not updated in it. This tool combines your export, a mapping file
you maintain, your previous index, and small incremental "delta" files
into one CSV you can keep, diff, and search — without ever losing track of
a conversation just because one export didn't happen to cover it. See
[docs/method.md](docs/method.md) for the full rules.

By default no excerpt of your conversations is ever copied into the
index — see [docs/privacy.md](docs/privacy.md).

## Quickstart (fictional data)

Everything below runs on `examples/`, a fully synthetic dataset — no real
conversation, title, or project name. Regenerate it any time with
`tools/make_fixture.py`.

```bash
git clone https://github.com/wadihrhondali-ops/claude-conversation-index.git && cd claude-conversation-index
python -I -B tools/make_fixture.py --out-dir examples   # (already committed; optional)

python -I -B src/claude_conversation_index/build_index.py \
    --export-json    examples/export-full/conversations.json \
    --mapping        examples/mapping.csv \
    --previous-index examples/previous-index.csv \
    --projects-dir   examples/export-full/projects \
    --export-date    2026-01-30 \
    --output-dir     /tmp/my-index \
    --deltas         "examples/*_delta_*.csv"

python -I -B src/claude_conversation_index/consult.py \
    --index  /tmp/my-index/claude-index_2026-01-30.csv \
    --export examples/export-full/conversations.json \
    --content "widget workshop"
```

That's it: `/tmp/my-index/` now has a CSV index, a Markdown rendering, a
comparison-to-previous-index CSV, and a per-project summary CSV, built
entirely from fictional data.

## Using it on your own data

1. Request a full export from Claude.ai: Settings > Privacy > Export data
   (see [docs/export-format.md](docs/export-format.md)).
2. Produce (by your own means — see [docs/project-mapping.md](docs/project-mapping.md))
   a `mapping.csv` with columns `uuid,title,created_at,updated_at,project_uuid,project_name`.
3. Run `build_index` with `--export-json`, `--mapping`, `--previous-index`
   (your last index, or an empty one with just a header the first time),
   `--projects-dir`, and `--export-date`.
4. Between full exports, record small changes in a dated
   `YYYY-MM-DD_delta_*.csv` file (same six columns) and pass it via
   `--deltas`.
5. Search with `consult.py` — add `--content` to also search message text
   (loads the full export ZIP or `conversations.json`; no network).

Install it properly (optional) with `pip install -e .` to get the
`claude-index-build` / `claude-index-consult` console scripts and to run
`python -m claude_conversation_index.build_index` / `...consult`;
otherwise just run the two scripts in `src/claude_conversation_index/`
directly, as in the quickstart above.

## Limits

- You must supply `--mapping` yourself; this tool does not fetch or
  scrape your project list (see [docs/project-mapping.md](docs/project-mapping.md)).
- The "created_at governs the export window, not updated_at" behaviour
  (docs/export-format.md) was observed once, on one account; it may not
  generalize to every account or every export.
- `project_source` is not recomputed after a delta merge — see
  [docs/method.md](docs/method.md).
- No encryption, no secure storage: see [docs/privacy.md](docs/privacy.md)
  for what never to commit anywhere public.

## Tested with

Python 3.9.6 and 3.14.4, standard library only. Tests use `pytest` (see
`pyproject.toml`'s `dev` extra); nothing else is required to run
`build_index.py` / `consult.py` themselves.

## License

MIT — see [LICENSE](LICENSE).
