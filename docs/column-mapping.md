# Column name correspondence

This tool's logic started as a private, French-language script. This
table records the renaming done when it was ported to English for this
public repository — useful if you are migrating notes, scripts, or a
previous private index that used the original French names.

| Original (French) | This repository (English) |
|---|---|
| `uuid` | `uuid` |
| `titre` | `title` |
| `titre_deduit` | `inferred_title` |
| `titre_deduit_source` | `inferred_title_source` |
| `created_at` | `created_at` |
| `updated_at` | `updated_at` |
| `nb_messages` | `message_count` |
| `resume_court` | `summary_excerpt` |
| `premier_message` | `first_message_excerpt` |
| `projet_nom` | `project_name` |
| `projet_uuid` | `project_uuid` |
| `projet_source` | `project_source` |
| `projet_ancien_index` | `project_previous_index` |
| `projet_change` | `project_changed` |
| `source_ligne` | `source_line` |
| `lien` | `link` |
| `contenu_source` | `content_source` |

`source_line` values:

| Original (French) | English |
|---|---|
| `export+application` | `export+mapping` |
| `export seulement` | `export only` |
| `application seulement` | `mapping only` |
| `index précédent seulement` | `previous index only` |
| `delta seulement` | `delta only` |

`project_source` values:

| Original (French) | English |
|---|---|
| `application` | `mapping` |
| `index précédent` | `previous index` |
| `absente de la liste application` | `not in mapping` |
| `delta` | `delta` |

`project_changed` / `content_source` values: `oui`/`non` -> `yes`/`no`;
`export` and `index précédent` -> `export` and `previous index`
(unchanged otherwise, empty stays empty).

The original French script also carried a hardcoded table of equivalent
project names (so that renaming a project wouldn't register as a
"change" on every row). That table was specific to one person's projects
and has been removed; this repository replaces it with an optional
`--project-aliases FILE` (a plain two-column CSV of equivalent name
pairs), empty by default.
