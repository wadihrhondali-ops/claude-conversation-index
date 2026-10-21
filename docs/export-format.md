# The Claude.ai data export

## How to request an export

Settings > Privacy > Export data, on the web app or Claude Desktop — not
available on mobile. You get a link by e-mail; it requires being logged in
and expires after 24 hours.

Source: <https://support.claude.com/en/articles/9450526-how-can-i-export-my-claude-data>
(read 2026-10-21).

## What's in `conversations.json`

The export page does not document whether each conversation is linked to
its project. In one export observed on one account on 2026-10-21, the
conversation objects did **not** have a project field. The keys observed
on that account were:

```
account, chat_messages, created_at, name, summary, updated_at, uuid
```

This is "observed on one account", not a claim about every account or
every export. A public issue report notes the same gap (mirror:
<https://claudeissues.com/issue/40411-claude-ai-data-export-missing-conversation-to-project-associations>,
reported as closed "not planned" according to that mirror).

Because of this, `build_index` needs a separate conversation-to-project
mapping that *you* produce — see [project-mapping.md](project-mapping.md).
This tool does not read, scrape, or otherwise reconstruct that mapping for
you.

## Export window

An export requested with a limited window (observed: 30 days) contained
exactly the conversations *created* within that window — not the ones
*updated* within it. This was observed once, on one account, on
2026-10-21; it has not been independently re-verified and may not hold for
every account or every export. If you rely on a time-limited export, a
conversation created outside the window but updated recently will be
missing from it even though it is "fresh" by `updated_at`. `build_index`
handles this by carrying over a conversation's content fields from the
previous index when the fresh export does not cover it (see
[method.md](method.md)) — but it cannot recover anything your previous
index itself did not already have.

If in doubt, request a full (unwindowed) export at least for your
periodic/monthly refresh, and use a time-limited export only for the
in-between incremental deltas.
