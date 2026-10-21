#!/usr/bin/env python3
"""
Build a CSV index of Claude.ai conversations from an official data export.

What it does
------------
Combines up to four sources into one CSV index, keyed by conversation
``uuid``:

  1. ``--export-json``      : the official ``conversations.json`` export.
  2. ``--mapping``          : a CSV you maintain yourself, mapping each
                               conversation uuid to a project (see
                               ``docs/project-mapping.md``). This tool does
                               NOT fetch or scrape this mapping for you.
  3. ``--previous-index``   : the index CSV this tool produced last time
                               (any version written by this tool).
  4. ``--deltas``            : optional incremental CSV files (globs or
                               direct paths), applied last, by freshness.

Union of rows = previous index ∪ export ∪ mapping ∪ deltas. A conversation
that is no longer present in a fresh export (e.g. because the export only
covers a recent window — see docs/method.md) is NOT dropped: its content
fields are carried over as-is from the previous index. Content fields are
never recomputed to blank for a uuid the export does not cover.

A delta's ``title``/``project_uuid``/``project_name`` only replace a row's
value when the delta's ``updated_at`` is at least as recent as the row's
own ``updated_at`` (ISO-8601 comparison). A delta without ``updated_at`` is
never applied to an existing row (a uuid found only in a delta is still
added). The conversation-to-project mapping always wins over a delta.

Privacy
-------
By default this tool copies NO excerpt of conversation content. Pass
``--with-excerpts`` to also copy the first sentence of the first user
message (``first_message_excerpt``, <= 200 chars) and of the summary
(``summary_excerpt``, <= 300 chars) — see docs/privacy.md before using it
on real data, and never commit the resulting index to a public repository.

Usage
-----
    python -I -B -m claude_conversation_index.build_index \\
        --export-json    /path/to/conversations.json \\
        --mapping        /path/to/conversation-project-mapping.csv \\
        --previous-index /path/to/previous_index.csv \\
        --projects-dir   /path/to/projects/ \\
        --export-date    YYYY-MM-DD \\
        --output-dir     /path/to/output-dir/ \\
        [--deltas "/path/to/*_delta_*.csv" ...] \\
        [--project-aliases /path/to/aliases.csv] \\
        [--with-excerpts] \\
        [--prefix claude-index]
"""

import argparse
import csv
import glob
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone


BASE_URL    = "https://claude.ai/chat/"
MAX_SUMMARY = 300
MAX_FIRST   = 200
MAX_INFERRED = 90

GENERIC_HEADER = re.compile(
    r'^\*{0,2}(?:conversation\s+overview|title\s*:|summary\s*:|this\s+conversation|overview\s*:)\*{0,2}',
    re.IGNORECASE)

# Index columns, in order. `content_source` is always last; nothing above it
# changes name or position across versions of this tool.
INDEX_COLS = [
    "uuid", "title", "inferred_title", "inferred_title_source",
    "created_at", "updated_at", "message_count",
    "summary_excerpt", "first_message_excerpt",
    "project_name", "project_uuid", "project_source", "project_previous_index",
    "project_changed", "source_line", "link", "content_source",
]

COMPARISON_COLS = [
    "uuid", "title", "source_line",
    "project_previous_index", "project_current", "project_source", "project_changed",
]

SUMMARY_COLS = [
    "project_uuid", "project_name", "total",
    "export_and_mapping", "export_only", "mapping_only",
    "with_previous_project", "changed_yes", "changed_no",
]

# Delta fields subject to the freshness rule (only applied if the delta's
# updated_at is >= the row's own updated_at).
_DELTA_FRESH_FIELDS = ("title", "project_uuid", "project_name")


# ── helpers ────────────────────────────────────────────────────────────────

def parse_iso(s):
    if not s:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def first_sentence(text, maxlen):
    if not text:
        return ""
    lines = [l.strip().strip("*").strip() for l in text.splitlines() if l.strip()]
    lines = [l for l in lines if not GENERIC_HEADER.match(l)]
    if not lines:
        return ""
    joined = " ".join(lines)
    sents  = re.split(r'(?<=[.!?])\s+', joined)
    first  = sents[0].strip() if sents else ""
    if len(first) <= maxlen:
        return first
    cut = first[:maxlen].rfind(" ")
    return (first[:cut] if cut > 0 else first[:maxlen]) + "…"


def load_project_aliases(path):
    """
    Load an optional CSV of equivalent project names (two columns, no
    header required: name_a,name_b). Returns a dict mapping each lowercased
    name to its lowercased equivalent. Empty / missing path -> no aliases.
    """
    aliases = {}
    if not path:
        return aliases
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue
            a, b = row[0].strip(), row[1].strip()
            if not a or not b:
                continue
            if a.lower() == "name_a" and b.lower() == "name_b":
                continue  # tolerate an optional header line
            aliases[a.lower()] = b.lower()
            aliases[b.lower()] = a.lower()
    return aliases


def are_equivalent(p1, p2, aliases):
    if not p1 and not p2:
        return True
    if not p1 or not p2:
        return False
    n1, n2 = p1.lower().strip(), p2.lower().strip()
    if n1 == n2:
        return True
    return aliases.get(n1) == n2 or aliases.get(n2) == n1


def md_esc(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


# ── loading sources ─────────────────────────────────────────────────────────

def load_export(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        print("[ERROR] The export JSON is not a list.", file=sys.stderr)
        sys.exit(1)
    return {c["uuid"]: c for c in data}


def load_mapping(path):
    rows = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return {r["uuid"]: r for r in rows}


def _get_prev_project(prev_row: dict) -> str:
    return (prev_row.get("project_name") or "").strip()


def load_previous_index(path):
    """
    Load a previous index CSV written by any version of this tool.
    Returns {uuid: {"project": project_name_str, "row": full_row}}.
    """
    result = {}
    with open(path, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            uuid = r.get("uuid", "")
            if uuid:
                result[uuid] = {"project": _get_prev_project(r), "row": r}
    return result


def load_projects(proj_dir):
    projs = []
    for pf in sorted(glob.glob(os.path.join(proj_dir, "*.json"))):
        with open(pf, "r", encoding="utf-8") as f:
            d = json.load(f)
        projs.append({"uuid": d.get("uuid", ""), "name": d.get("name", "")})
    return projs


# ── deltas ───────────────────────────────────────────────────────────────

def _delta_sort_key(path: str):
    name = os.path.basename(path)
    m_date = re.match(r'(\d{4}-\d{2}-\d{2})', name)
    date_str = m_date.group(1) if m_date else "0000-00-00"
    m_time = re.search(r'[T_](\d{2})[:-](\d{2})(?:\D|$)', name)
    time_str = (m_time.group(1) + m_time.group(2)) if m_time else "0000"
    return (date_str, time_str, os.path.getmtime(path))


def _load_delta_file(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            content = f.read()
    except Exception as e:
        print(f"[DELTA] {os.path.basename(path)}: could not read ({e}).", file=sys.stderr)
        return {}

    if not content.strip():
        print(f"[DELTA] {os.path.basename(path)}: empty, skipped.", file=sys.stderr)
        return {}

    try:
        reader = csv.DictReader(io.StringIO(content))
        rows = {}
        for row in reader:
            uuid = (row.get("uuid") or "").strip()
            if not uuid:
                continue
            rows[uuid] = {k: (v or "").strip() for k, v in row.items()}
        if not rows:
            print(f"[DELTA] {os.path.basename(path)}: no valid uuid row, skipped.", file=sys.stderr)
        return rows
    except Exception as e:
        print(f"[DELTA] {os.path.basename(path)}: malformed ({e}), skipped.", file=sys.stderr)
        return {}


def expand_and_load_deltas(delta_args: list, export_date: str) -> tuple:
    paths: list = []
    for arg in delta_args:
        if "*" in arg or "?" in arg:
            expanded = glob.glob(arg)
            if not expanded:
                print(f"[DELTA] Pattern \"{arg}\" matched no file.", file=sys.stderr)
            paths.extend(expanded)
        elif os.path.isfile(arg):
            paths.append(arg)
        else:
            print(f"[DELTA] \"{arg}\": file not found, skipped.", file=sys.stderr)

    seen: set = set()
    filtered: list = []
    for p in paths:
        ap = os.path.abspath(p)
        if ap in seen:
            continue
        seen.add(ap)
        m = re.match(r'(\d{4}-\d{2}-\d{2})', os.path.basename(p))
        delta_date = m.group(1) if m else "0000-00-00"
        if delta_date >= export_date:
            filtered.append(p)
        else:
            print(f"[DELTA] {os.path.basename(p)}: date {delta_date} "
                  f"< export {export_date}, skipped.", file=sys.stderr)

    filtered.sort(key=_delta_sort_key)

    accumulated: dict = {}
    for p in filtered:
        for uuid, row in _load_delta_file(p).items():
            if uuid not in accumulated:
                accumulated[uuid] = dict(row)
            else:
                for field in ("title", "created_at", "updated_at", "project_uuid", "project_name"):
                    val = row.get(field, "")
                    if val:
                        accumulated[uuid][field] = val

    return accumulated, filtered


# ── row construction ───────────────────────────────────────────────────────

def build_rows(export_by_uuid, mapping_by_uuid, prev_by_uuid, aliases,
               with_excerpts=False):
    """
    Union of uuid = previous index ∪ export ∪ mapping.
    """
    all_uuids = set(export_by_uuid) | set(mapping_by_uuid) | set(prev_by_uuid)
    rows = []

    for uuid in all_uuids:
        in_export  = uuid in export_by_uuid
        in_mapping = uuid in mapping_by_uuid
        in_prev    = uuid in prev_by_uuid
        prev_entry = prev_by_uuid.get(uuid)
        prev_row   = prev_entry["row"] if prev_entry else None

        # --- content: inferred_title*, message_count, summary_excerpt, first_message_excerpt ---
        if in_export:
            c = export_by_uuid[uuid]
            title_export   = (c.get("name") or "").strip()
            export_created = c.get("created_at", "")
            export_updated = c.get("updated_at", "")
            msgs    = c.get("chat_messages") or []
            summary = (c.get("summary") or "").strip()

            if title_export:
                inferred_title = inferred_title_src = ""
            else:
                if summary:
                    inferred_title     = first_sentence(summary, MAX_INFERRED)
                    inferred_title_src = "summary"
                else:
                    inferred_title     = ""
                    inferred_title_src = "no summary"

            summary_excerpt = first_sentence(summary, MAX_SUMMARY) if summary else ""
            first_msg = ""
            for m in msgs:
                if m.get("sender", "") in ("human", "user"):
                    t = (m.get("text") or "").strip()
                    if not t:
                        for blk in (m.get("content") or []):
                            if isinstance(blk, dict) and blk.get("type") == "text":
                                t = (blk.get("text") or "").strip(); break
                    if t:
                        first_msg = first_sentence(t, MAX_FIRST); break
            message_count = len(msgs)
            content_source = "export"
        elif in_prev:
            title_export = ""
            export_created = export_updated = ""
            inferred_title     = (prev_row.get("inferred_title", "") or "")
            inferred_title_src = (prev_row.get("inferred_title_source", "") or "")
            message_count      = (prev_row.get("message_count", "") or "")
            summary_excerpt    = (prev_row.get("summary_excerpt", "") or "")
            first_msg          = (prev_row.get("first_message_excerpt", "") or "")
            content_source = "previous index" if (inferred_title or message_count or summary_excerpt or first_msg) else ""
        else:
            title_export = ""
            export_created = export_updated = ""
            inferred_title = inferred_title_src = message_count = summary_excerpt = first_msg = ""
            content_source = ""

        if not with_excerpts:
            summary_excerpt = ""
            first_msg = ""

        # --- title: export if present, else mapping, else previous index, else blank ---
        if in_export:
            title = title_export
        elif in_mapping:
            title = (mapping_by_uuid[uuid].get("title", "") or "").strip()
        elif in_prev:
            title = (prev_row.get("title", "") or "").strip()
        else:
            title = ""

        # --- created_at: first non-empty (export, mapping, previous index) ---
        mapping_created = (mapping_by_uuid[uuid].get("created_at", "") or "") if in_mapping else ""
        prev_created    = (prev_row.get("created_at", "") or "") if in_prev else ""
        created = export_created or mapping_created or prev_created

        # --- updated_at: most recent of the three ---
        mapping_updated = (mapping_by_uuid[uuid].get("updated_at", "") or "") if in_mapping else ""
        prev_updated    = (prev_row.get("updated_at", "") or "") if in_prev else ""
        upd_candidates = [v for v in (export_updated, mapping_updated, prev_updated) if v]
        updated = max(upd_candidates, key=parse_iso) if upd_candidates else ""

        # --- project: mapping wins; else previous index; delta applied later ---
        if in_mapping:
            mp = mapping_by_uuid[uuid]
            project_name   = (mp.get("project_name", "") or "").strip()
            project_uuid   = (mp.get("project_uuid", "") or "").strip()
            project_source = "mapping"
        elif in_prev and prev_entry["project"]:
            project_name   = prev_entry["project"]
            project_uuid   = (prev_row.get("project_uuid", "") or "").strip()
            project_source = "previous index"
        else:
            project_name = project_uuid = ""
            project_source = "not in mapping"

        # --- source_line ---
        if in_export and in_mapping:
            source_line = "export+mapping"
        elif in_export:
            source_line = "export only"
        elif in_mapping:
            source_line = "mapping only"
        else:
            source_line = "previous index only"

        project_previous_index = prev_entry["project"] if prev_entry else ""

        if not project_previous_index:
            project_changed = ""
        elif are_equivalent(project_name, project_previous_index, aliases):
            project_changed = "no"
        else:
            project_changed = "yes"

        rows.append({
            "uuid": uuid, "title": title,
            "inferred_title": inferred_title, "inferred_title_source": inferred_title_src,
            "created_at": created, "updated_at": updated,
            "message_count": message_count,
            "summary_excerpt": summary_excerpt, "first_message_excerpt": first_msg,
            "project_name": project_name, "project_uuid": project_uuid,
            "project_source": project_source,
            "project_previous_index": project_previous_index,
            "project_changed": project_changed,
            "source_line": source_line,
            "link": BASE_URL + uuid if uuid else "",
            "content_source": content_source,
            "_upd_dt": parse_iso(updated),
        })

    rows.sort(key=lambda r: r["_upd_dt"], reverse=True)
    for r in rows:
        del r["_upd_dt"]
    return rows


def apply_deltas_to_rows(rows: list, delta_by_uuid: dict, mapping_by_uuid: dict) -> list:
    """
    Apply delta rows by freshness.
      - Common uuid: title/project_uuid/project_name from the delta only
        replace the row if the delta's updated_at (parse_iso) is >= the
        row's own updated_at. A delta without updated_at is never applied
        to an existing row.
      - The delta's project is never applied if the uuid is in the mapping
        (the mapping is a complete, authoritative snapshot).
      - updated_at final = max(row, delta).
      - uuid only in a delta: added with source_line='delta only'.
    """
    result_by_uuid = {r["uuid"]: r for r in rows if r.get("uuid")}

    for uuid, d in delta_by_uuid.items():
        delta_updated = d.get("updated_at", "")
        if uuid in result_by_uuid:
            r = result_by_uuid[uuid]
            row_updated = r.get("updated_at", "")
            fresh = bool(delta_updated) and parse_iso(delta_updated) >= parse_iso(row_updated)

            if fresh:
                val = d.get("title", "")
                if val:
                    r["title"] = val
                if uuid not in mapping_by_uuid:
                    val = d.get("project_uuid", "")
                    if val:
                        r["project_uuid"] = val
                    val = d.get("project_name", "")
                    if val:
                        r["project_name"] = val
                r["updated_at"] = max(
                    [v for v in (row_updated, delta_updated) if v], key=parse_iso
                ) if (row_updated or delta_updated) else row_updated

            val = d.get("created_at", "")
            if val:
                r["created_at"] = val
        else:
            result_by_uuid[uuid] = {
                "uuid": uuid,
                "title": d.get("title", ""),
                "inferred_title": "", "inferred_title_source": "",
                "created_at": d.get("created_at", ""),
                "updated_at": d.get("updated_at", ""),
                "message_count": "",
                "summary_excerpt": "", "first_message_excerpt": "",
                "project_name": d.get("project_name", ""),
                "project_uuid": d.get("project_uuid", ""),
                "project_source": "delta",
                "project_previous_index": "", "project_changed": "",
                "source_line": "delta only",
                "content_source": "",
                "link": BASE_URL + uuid,
            }

    result = list(result_by_uuid.values())
    result.sort(key=lambda r: parse_iso(r.get("updated_at", "")), reverse=True)
    return result


def build_comparison_rows(rows, prev_by_uuid):
    result = []
    for r in rows:
        uuid = r["uuid"]
        prev_entry = prev_by_uuid.get(uuid)
        result.append({
            "uuid": uuid,
            "title": r["title"] or r.get("inferred_title", ""),
            "source_line": r["source_line"],
            "project_previous_index": prev_entry["project"] if prev_entry else "",
            "project_current": r["project_name"],
            "project_source": r["project_source"],
            "project_changed": r["project_changed"],
        })
    return result


def build_summary_rows(rows, projects_json):
    name_rows = defaultdict(list)
    uuid_rows = defaultdict(list)
    for r in rows:
        if r["project_name"]: name_rows[r["project_name"]].append(r)
        if r["project_uuid"]: uuid_rows[r["project_uuid"]].append(r)

    result = []
    for pj in projects_json:
        pu, pn = pj["uuid"], pj["name"]
        matched = uuid_rows.get(pu, []) or name_rows.get(pn, [])
        result.append({
            "project_uuid": pu, "project_name": pn,
            "total": len(matched),
            "export_and_mapping": sum(1 for r in matched if r["source_line"] == "export+mapping"),
            "export_only":        sum(1 for r in matched if r["source_line"] == "export only"),
            "mapping_only":       sum(1 for r in matched if r["source_line"] == "mapping only"),
            "with_previous_project": sum(1 for r in matched if r["project_previous_index"]),
            "changed_yes": sum(1 for r in matched if r["project_changed"] == "yes"),
            "changed_no":  sum(1 for r in matched if r["project_changed"] == "no"),
        })

    unassigned = [r for r in rows if not r["project_name"]]
    result.append({
        "project_uuid": "", "project_name": "(no project)",
        "total": len(unassigned),
        "export_and_mapping": sum(1 for r in unassigned if r["source_line"] == "export+mapping"),
        "export_only":        sum(1 for r in unassigned if r["source_line"] == "export only"),
        "mapping_only":       sum(1 for r in unassigned if r["source_line"] == "mapping only"),
        "with_previous_project": 0, "changed_yes": 0, "changed_no": 0,
    })
    return result


# ── output naming ──────────────────────────────────────────────────────────

def deduce_output_names(output_dir: str, export_date: str, prefix: str) -> dict:
    return {
        "csv":        os.path.join(output_dir, f"{prefix}_{export_date}.csv"),
        "md":         os.path.join(output_dir, f"{prefix}_{export_date}.md"),
        "comparison": os.path.join(output_dir, f"{prefix}_comparison_{export_date}.csv"),
        "summary":    os.path.join(output_dir, f"{prefix}_summary_{export_date}.csv"),
    }


# ── writing ──────────────────────────────────────────────────────────────

def write_csv(rows, cols, path):
    if os.path.exists(path):
        print(f"[ERROR] Output file already exists: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_md(rows, path, export_date, with_excerpts):
    if os.path.exists(path):
        print(f"[ERROR] Output file already exists: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Claude conversation index\n\n")
        f.write(f"**Export source:** `conversations.json` ({export_date})  \n")
        f.write(f"**Project source:** your conversation-to-project mapping file  \n")
        f.write("**Previous-index carry-over:** a conversation missing from the fresh export "
                "keeps its content (`inferred_title`, `message_count`, `summary_excerpt`, "
                "`first_message_excerpt`) from the previous index — see `content_source`.  \n")
        if with_excerpts:
            f.write("**Excerpts enabled (--with-excerpts):** `first_message_excerpt` (<= 200 "
                    "chars, first sentence of the first user message) and `summary_excerpt` "
                    "(<= 300 chars, first sentence of the summary) are excerpts of conversation "
                    "content copied into this index — handle them with the same care as the "
                    "rest of the conversation. See docs/privacy.md.  \n")
        else:
            f.write("**Excerpts disabled (default):** `first_message_excerpt` and "
                    "`summary_excerpt` are empty. Pass --with-excerpts to fill them.  \n")
        f.write(f"**Row count:** {len(rows)} (union of previous index + export + mapping + deltas)  \n")
        f.write("**Sorted by:** updated_at, descending  \n\n")
        f.write("| " + " | ".join(INDEX_COLS) + " |\n")
        f.write("| " + " | ".join(["---"] * len(INDEX_COLS)) + " |\n")
        for r in rows:
            cells = []
            for c in INDEX_COLS:
                if c == "link":
                    cells.append(f"[chat]({r['link']})" if r.get("link") else "")
                else:
                    cells.append(md_esc(r.get(c, "") or ""))
            f.write("| " + " | ".join(cells) + " |\n")


# ── main ───────────────────────────────────────────────────────────────────

def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="claude-index-build",
        description="Build a CSV index of Claude.ai conversations from an official export.")
    parser.add_argument("--export-json", required=True,
                        help="Path to conversations.json (official export)")
    parser.add_argument("--mapping", required=True,
                        help="Path to your conversation-to-project mapping CSV "
                             "(see docs/project-mapping.md)")
    parser.add_argument("--previous-index", required=True,
                        help="Previous index CSV written by this tool (any version)")
    parser.add_argument("--projects-dir", required=True,
                        help="Directory containing the exported project JSON files")
    parser.add_argument("--export-date", required=True,
                        help="Export date (YYYY-MM-DD), used to derive output file names")
    parser.add_argument("--output-dir", default="",
                        help="Output directory (default: current directory)")
    parser.add_argument("--deltas", nargs="*", default=[], metavar="GLOB_OR_FILE",
                        help="Delta files to apply (globs or direct paths)")
    parser.add_argument("--project-aliases", default="",
                        help="Optional CSV of equivalent project name pairs "
                             "(name_a,name_b per line); default: none")
    parser.add_argument("--with-excerpts", action="store_true",
                        help="Copy short excerpts of conversation content into the index "
                             "(see docs/privacy.md). Off by default.")
    parser.add_argument("--prefix", default="claude-index",
                        help="Prefix for output file names (default: claude-index)")
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)

    output_dir = args.output_dir or "."
    os.makedirs(output_dir, exist_ok=True)

    out = deduce_output_names(output_dir, args.export_date, args.prefix)
    for label, path in out.items():
        if os.path.exists(path):
            print(f"[ERROR] Output file already exists ({label}): {path}", file=sys.stderr)
            sys.exit(1)

    print(f"Export JSON      : {args.export_json}")
    export_by_uuid = load_export(args.export_json)
    print(f"  {len(export_by_uuid)} conversations")

    print(f"Mapping          : {args.mapping}")
    mapping_by_uuid = load_mapping(args.mapping)
    print(f"  {len(mapping_by_uuid)} rows")

    print(f"Previous index   : {args.previous_index}")
    prev_by_uuid = load_previous_index(args.previous_index)
    print(f"  {len(prev_by_uuid)} rows")
    n_with_project = sum(1 for v in prev_by_uuid.values() if v["project"])
    print(f"  {n_with_project} uuid with a project in the previous index")

    print(f"Projects JSON    : {args.projects_dir}")
    projects_json = load_projects(args.projects_dir)
    print(f"  {len(projects_json)} projects")

    aliases = load_project_aliases(args.project_aliases)
    if args.project_aliases:
        print(f"Project aliases  : {args.project_aliases} ({len(aliases) // 2} pair(s))")

    delta_by_uuid, delta_paths_used = expand_and_load_deltas(args.deltas, args.export_date)
    if delta_paths_used:
        for dp in delta_paths_used:
            print(f"Delta used       : {os.path.basename(dp)}")
        print(f"  {len(delta_by_uuid)} uuid in the deltas")
    else:
        print("Delta            : none supplied or retained.")

    rows = build_rows(export_by_uuid, mapping_by_uuid, prev_by_uuid, aliases, args.with_excerpts)
    print(f"Rows after build : {len(rows)} (previous index + export + mapping)")

    if delta_by_uuid:
        rows = apply_deltas_to_rows(rows, delta_by_uuid, mapping_by_uuid)
        if not args.with_excerpts:
            for r in rows:
                r["summary_excerpt"] = ""
                r["first_message_excerpt"] = ""
        print(f"Rows after delta : {len(rows)}")

    comparison_rows = build_comparison_rows(rows, prev_by_uuid)
    summary_rows    = build_summary_rows(rows, projects_json)

    write_csv(rows, INDEX_COLS, out["csv"])
    print(f"CSV index written    : {out['csv']}")

    write_md(rows, out["md"], args.export_date, args.with_excerpts)
    print(f"Markdown index written: {out['md']}")

    write_csv(comparison_rows, COMPARISON_COLS, out["comparison"])
    print(f"Comparison written    : {out['comparison']}")

    write_csv(summary_rows, SUMMARY_COLS, out["summary"])
    print(f"Summary written       : {out['summary']}")

    sl = Counter(r["source_line"] for r in rows)
    cs = Counter(r["content_source"] for r in rows)
    pc = Counter(r["project_changed"] for r in rows)
    print(f"\n{'='*60}")
    print(f"Rows         : {len(rows)}")
    print("source_line  :")
    for k, v in sl.items():
        print(f"  {k:25s}: {v}")
    print("content_source :")
    for k, v in cs.items():
        print(f"  {repr(k):18s}: {v}")
    n_with = sum(1 for r in rows if r["project_name"])
    n_without = sum(1 for r in rows if not r["project_name"])
    print(f"With project : {n_with}")
    print(f"No project   : {n_without}")
    distinct_projects = {r["project_name"] for r in rows if r["project_name"]}
    print(f"Distinct projects : {len(distinct_projects)}")
    print("project_changed :")
    for k, v in pc.items():
        print(f"  {repr(k):10s}: {v}")
    print(f"Summary rows : {len(summary_rows)}")


if __name__ == "__main__":
    main()
