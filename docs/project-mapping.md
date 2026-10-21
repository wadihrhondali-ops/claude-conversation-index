# The conversation-to-project mapping file

`build_index` needs a CSV you produce yourself, mapping each conversation
`uuid` to the project it belongs to (see [export-format.md](export-format.md)
for why the official export alone does not give you this). This tool does
**not** fetch, scrape, or otherwise read Claude.ai's own project list for
you — there is no code here that does that, no internal endpoint name, no
embedded JavaScript snippet. You are expected to produce this file by your
own means (manually, from your own notes, with a browser automation tool
you run and maintain yourself, etc.) and point `--mapping` at it.

## Contract

A CSV with a header row and these six columns:

| Column | Meaning |
|---|---|
| `uuid` | The conversation's uuid, matching `conversations.json`. |
| `title` | The conversation's title, as you know it. Only used as a fallback when the export doesn't have this conversation. |
| `created_at` | ISO-8601 timestamp. Only used as a fallback. |
| `updated_at` | ISO-8601 timestamp. Used in the "most recent of the three sources" rule — see [method.md](method.md). |
| `project_uuid` | The project's uuid (can be empty if you only track names). |
| `project_name` | The project's display name. This is what wins over everything else when present (see method.md). |

A row with an empty `project_name` is treated as "this conversation has no
project", not as "unknown" — if you don't yet know a conversation's
project, omit the row entirely instead (it will then fall back to the
previous index's project, if any).

## A note on Anthropic's Terms of Service

Anthropic's consumer Terms of Use (version dated 2025-10-08, read
2026-10-21: <https://www.anthropic.com/legal/consumer-terms>), in section
3, prohibit scraping or accessing the Services by bots or scripts other
than through the API with a valid key, or with Anthropic's explicit
permission. The author of this tool has not independently verified what
is and is not permitted for retrieving your own project list, and this
repository deliberately ships no code that does so. This is not legal
advice; read the current Terms yourself and make your own call about how
you produce `mapping.csv`.
