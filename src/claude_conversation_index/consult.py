#!/usr/bin/env python3
"""
Search a CSV index produced by build_index.py.

Modes
-----
  - Index-only search (default): looks in title, inferred_title,
    summary_excerpt, first_message_excerpt of the index (these are empty
    unless the index was built with --with-excerpts).
  - --content: also loads the full export (a ZIP of conversations or an
    extracted conversations.json) and searches the actual message text.
    This requires no network: the file is read from local disk.

In --content mode, prints the file name, number of conversations loaded,
and created_at min/max. It also prints an explicit "partial export"
warning when fewer than 90% of the index rows with a non-empty
message_count are covered by the loaded export. This is informational —
the search still runs over whatever is available.

Delta merge (when a delta CSV is found next to the index, or via
--delta-dir): title/project_uuid/project_name from a delta only replace
an index row if the delta's updated_at (parse_iso) is >= the row's own
updated_at. A delta without updated_at is never applied to an existing
row. updated_at final = max(row, delta). A uuid present only in a delta
is added with source_line='delta only'.

Usage
-----
    python -I -B -m claude_conversation_index.consult TERM [TERM ...] [options]

Options
-------
    --all             Require ALL terms to be present (AND); default: OR.
    --project NAME    Filter on project_name (substring, case-insensitive).
    --content         Also search message text (loads the full export).
    --limit N         Maximum number of results to display (default: 10).
    --delta-dir DIR   Extra directory to look for delta files in
                       (in addition to the index's own directory).
    --index CSV       Index CSV to use (default: the most recent one in
                       the current directory).
    --export PATH     ZIP or conversations.json to use for --content
                       (default: the most recent conversations-*.zip in
                       the current directory).

Columns displayed:
    project_name | title | created_at | updated_at | message_count | link
    (+ occurrences in --content mode)

Constraints:
    - No network.
    - No writes other than stdout/stderr.
    - No content excerpt is ever displayed.
"""

import argparse
import csv
import glob
import io
import json
import os
import re
import sys
import unicodedata
import zipfile
from datetime import datetime, timezone


BASE_URL = "https://claude.ai/chat/"

_DELTA_MERGE_FIELDS = ("title", "created_at", "updated_at", "project_uuid", "project_name")
_DELTA_FRESH_FIELDS = ("title", "project_uuid", "project_name")


# ── automatic file discovery ────────────────────────────────────────────────

def _find_index(directory: str) -> str:
    pattern = os.path.join(directory, "*.csv")
    # Prefer files that look like an index written by this tool: a bare
    # "<prefix>_<date>.csv" without "_comparison_" or "_summary_" in the name.
    candidates = [
        p for p in glob.glob(pattern)
        if "_comparison_" not in os.path.basename(p)
        and "_summary_" not in os.path.basename(p)
        and re.search(r'_\d{4}-\d{2}-\d{2}\.csv$', os.path.basename(p))
    ]
    if not candidates:
        raise FileNotFoundError(
            f"No index found in {directory}\n"
            f"Expected pattern: <prefix>_YYYY-MM-DD.csv"
        )

    def _sort_key(path):
        name = os.path.basename(path)
        m_date = re.search(r'_(\d{4}-\d{2}-\d{2})\.csv$', name)
        date_str = m_date.group(1) if m_date else "0000-00-00"
        return (date_str, os.path.getmtime(path))

    return max(candidates, key=_sort_key)


def _index_export_date(index_path: str) -> str:
    m = re.search(r'_(\d{4}-\d{2}-\d{2})\.csv$', os.path.basename(index_path))
    return m.group(1) if m else "0000-00-00"


def _delta_sort_key(path: str):
    name = os.path.basename(path)
    m_date = re.match(r'(\d{4}-\d{2}-\d{2})', name)
    date_str = m_date.group(1) if m_date else "0000-00-00"
    m_time = re.search(r'[T_](\d{2})[:-](\d{2})(?:\D|$)', name)
    time_str = (m_time.group(1) + m_time.group(2)) if m_time else "0000"
    return (date_str, time_str, os.path.getmtime(path))


def _find_deltas(directory: str, export_date: str) -> list:
    pattern = os.path.join(directory, "*_delta_*.csv")
    result = []
    for path in glob.glob(pattern):
        m = re.match(r'(\d{4}-\d{2}-\d{2})', os.path.basename(path))
        if m and m.group(1) >= export_date:
            result.append(path)
    return sorted(result, key=_delta_sort_key)


def _find_zip(directory: str) -> str:
    pattern = os.path.join(directory, "conversations-*.zip")
    candidates = glob.glob(pattern)
    if not candidates:
        raise FileNotFoundError(
            f"No export ZIP found in {directory}\n"
            f"Expected pattern: conversations-*.zip"
        )
    return max(candidates, key=os.path.getmtime)


# ── loading and merging deltas ─────────────────────────────────────────────

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


def _load_all_deltas(delta_paths: list) -> dict:
    accumulated: dict = {}
    for path in delta_paths:
        for uuid, row in _load_delta_file(path).items():
            if uuid not in accumulated:
                accumulated[uuid] = dict(row)
            else:
                for field in _DELTA_MERGE_FIELDS:
                    val = row.get(field, "")
                    if val:
                        accumulated[uuid][field] = val
    return accumulated


def _merge_index_with_deltas(idx_rows: list, delta_by_uuid: dict) -> list:
    """
    Merge the index with delta entries, by freshness:
      - common uuid: title/project_uuid/project_name from the delta only
        replace the row if the delta's updated_at (parse_iso) is >= the
        row's own updated_at. A delta without updated_at is never applied
        to an existing row. updated_at final = max(row, delta).
      - uuid only in a delta: added with source_line='delta only'.
      - uuid only in the index: kept as-is.
    """
    seen: set = set()
    result: list = []

    for row in idx_rows:
        uuid = row.get("uuid", "")
        if not uuid or uuid in seen:
            continue
        seen.add(uuid)
        r = dict(row)
        if uuid in delta_by_uuid:
            d = delta_by_uuid[uuid]
            delta_updated = d.get("updated_at", "")
            row_updated   = r.get("updated_at", "")
            fresh = bool(delta_updated) and _parse_iso(delta_updated) >= _parse_iso(row_updated)
            if fresh:
                for field in _DELTA_FRESH_FIELDS:
                    val = d.get(field, "")
                    if val:
                        r[field] = val
                r["updated_at"] = max(
                    [v for v in (row_updated, delta_updated) if v], key=_parse_iso
                ) if (row_updated or delta_updated) else row_updated
            val = d.get("created_at", "")
            if val:
                r["created_at"] = val
        result.append(r)

    for uuid, d in delta_by_uuid.items():
        if uuid in seen:
            continue
        seen.add(uuid)
        result.append({
            "uuid": uuid,
            "title": d.get("title", ""),
            "inferred_title": "",
            "project_name": d.get("project_name", ""),
            "project_uuid": d.get("project_uuid", ""),
            "created_at": d.get("created_at", ""),
            "updated_at": d.get("updated_at", ""),
            "message_count": "",
            "summary_excerpt": "",
            "first_message_excerpt": "",
            "source_line": "delta only",
            "link": BASE_URL + uuid,
        })

    return result


# ── normalization ───────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """
    Lowercase + strip diacritics on Latin characters only. Non-Latin
    scripts are preserved as-is.
    """
    result = []
    for char in text:
        nfd = unicodedata.normalize("NFD", char)
        ascii_attempt = nfd.encode("ascii", "ignore").decode("ascii")
        if ascii_attempt:
            result.append(ascii_attempt.lower())
        else:
            result.append(char.lower())
    return "".join(result)


def _compile_patterns(terms: list) -> list:
    patterns = []
    for t in terms:
        norm_t = _normalize(t)
        if not norm_t.strip():
            print(f"[ERROR] Term \"{t}\" becomes empty after normalization.", file=sys.stderr)
            sys.exit(1)
        patterns.append(re.compile(re.escape(norm_t)))
    return patterns


def _count_in(text: str, patterns: list) -> int:
    if not text:
        return 0
    norm = _normalize(text)
    return sum(len(p.findall(norm)) for p in patterns)


def _count_message(msg: dict, patterns: list) -> int:
    text_val = msg.get("text") or ""
    if text_val.strip():
        return _count_in(text_val, patterns)
    total = 0
    for block in (msg.get("content") or []):
        if isinstance(block, dict) and block.get("type") == "text":
            total += _count_in(block.get("text") or "", patterns)
    return total


# ── index-only search ───────────────────────────────────────────────────────

def _search_index(rows: list, patterns: list, mode_all: bool, project_filter: str) -> list:
    results = []
    project_norm = _normalize(project_filter) if project_filter else ""

    for row in rows:
        if project_norm:
            if project_norm not in _normalize(row.get("project_name", "") or ""):
                continue

        title   = row.get("title", "") or ""
        inferred = row.get("inferred_title", "") or ""
        summary  = row.get("summary_excerpt", "") or ""
        first    = row.get("first_message_excerpt", "") or ""

        occ = (_count_in(title, patterns) + _count_in(inferred, patterns) +
               _count_in(summary, patterns) + _count_in(first, patterns))

        if mode_all:
            full = " ".join([title, inferred, summary, first])
            matched = all(bool(p.search(_normalize(full))) for p in patterns)
        else:
            matched = occ > 0

        if matched:
            results.append({**row, "_score": occ, "_occ_msg": 0})

    return results


# ── content search (--content) ──────────────────────────────────────────────

def _load_zip(zip_path: str) -> list:
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            json_names = [n for n in zf.namelist() if n.lower().endswith(".json")]
            if not json_names:
                print(f"[ERROR] No .json in {zip_path}", file=sys.stderr)
                sys.exit(1)
            target = "conversations.json" if "conversations.json" in json_names else json_names[0]
            with zf.open(target) as jf:
                return json.load(io.TextIOWrapper(jf, encoding="utf-8"))
    except zipfile.BadZipFile as e:
        print(f"[ERROR] Invalid ZIP: {e}", file=sys.stderr)
        sys.exit(1)


def _load_export_any(path: str) -> list:
    if path.lower().endswith(".json"):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            print(f"[ERROR] {path}: root JSON is not a list.", file=sys.stderr)
            sys.exit(1)
        return data
    return _load_zip(path)


def _search_content(conv_list: list, patterns: list, mode_all: bool,
                    idx_by_uuid: dict, project_filter: str) -> list:
    project_norm = _normalize(project_filter) if project_filter else ""
    results = []

    for conv in conv_list:
        if not isinstance(conv, dict):
            continue
        uuid = conv.get("uuid", "")
        idx_row = idx_by_uuid.get(uuid, {})

        if project_norm:
            pn = idx_row.get("project_name", "") or ""
            if project_norm not in _normalize(pn):
                continue

        msgs    = conv.get("chat_messages") or []
        summary = conv.get("summary") or ""
        name    = conv.get("name") or ""

        occ_name    = _count_in(name, patterns)
        occ_summary = _count_in(summary, patterns)
        occ_msg     = sum(_count_message(m, patterns) for m in msgs if isinstance(m, dict))
        total       = occ_name + occ_summary + occ_msg

        if mode_all:
            full_parts = [name, summary]
            for m in msgs:
                if isinstance(m, dict):
                    text_val = (m.get("text") or "").strip()
                    if text_val:
                        full_parts.append(text_val)
                    else:
                        for blk in (m.get("content") or []):
                            if isinstance(blk, dict) and blk.get("type") == "text":
                                full_parts.append(blk.get("text") or "")
            full_text = _normalize(" ".join(full_parts))
            matched = all(bool(p.search(full_text)) for p in patterns)
        else:
            matched = total > 0

        if not matched:
            continue

        title       = idx_row.get("title") or name or ""
        inferred    = idx_row.get("inferred_title", "") or ""
        project     = idx_row.get("project_name", "") or ""
        created     = idx_row.get("created_at") or conv.get("created_at", "")
        updated     = idx_row.get("updated_at") or conv.get("updated_at", "")
        msg_count   = idx_row.get("message_count") or str(len(msgs))
        link        = BASE_URL + uuid if uuid else ""

        results.append({
            "uuid": uuid, "title": title, "inferred_title": inferred,
            "project_name": project, "created_at": created, "updated_at": updated,
            "message_count": msg_count, "link": link,
            "_score": total, "_occ_msg": occ_msg,
        })

    return results


# ── sorting and display ──────────────────────────────────────────────────

def _parse_iso(s: str) -> datetime:
    if not s:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _display(results: list, limit: int, show_occ: bool) -> None:
    shown = sorted(
        results, key=lambda r: (-r["_score"], -_parse_iso(r.get("updated_at", "")).timestamp())
    )[:limit]

    total_found = len(results)
    suffix = f" - {limit} shown" if total_found > limit else ""
    print(f"\n{total_found} result(s){suffix}.\n")

    if not shown:
        return

    W_PROJ, W_TITLE, W_DATE, W_NB, W_OCC = 28, 44, 10, 5, 5

    def _trunc(s, w):
        s = str(s or "")
        return s[:w - 1] + "…" if len(s) > w else s.ljust(w)

    if show_occ:
        header = (f"{'project_name':{W_PROJ}}  {'title':{W_TITLE}}  "
                  f"{'created':{W_DATE}}  {'updated':{W_DATE}}  {'msgs':>{W_NB}}  "
                  f"{'occ':>{W_OCC}}  link")
    else:
        header = (f"{'project_name':{W_PROJ}}  {'title':{W_TITLE}}  "
                  f"{'created':{W_DATE}}  {'updated':{W_DATE}}  {'msgs':>{W_NB}}  link")
    sep = "-" * len(header)

    print(header)
    print(sep)

    for r in shown:
        title_disp = r.get("title") or r.get("inferred_title") or "(no title)"
        proj_disp  = r.get("project_name") or ""
        created    = (r.get("created_at") or "")[:10]
        updated    = (r.get("updated_at") or "")[:10]
        nb         = str(r.get("message_count", "") or "")
        link       = r.get("link", "")

        line = (f"{_trunc(proj_disp, W_PROJ)}  {_trunc(title_disp, W_TITLE)}  "
                f"{created:{W_DATE}}  {updated:{W_DATE}}  {nb:>{W_NB}}  ")
        if show_occ:
            line += f"{r['_score']:>{W_OCC}}  "
        line += link
        print(line)

    print()


# ── entry point ──────────────────────────────────────────────────────────

def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="claude-index-consult",
        description="Search a CSV index produced by build_index.")
    parser.add_argument("terms", nargs="+", metavar="TERM", help="Term(s) to search for")
    parser.add_argument("--all", action="store_true",
                        help="Require ALL terms to be present (AND)")
    parser.add_argument("--project", metavar="NAME", default="",
                        help="Filter on project_name (substring)")
    parser.add_argument("--content", action="store_true",
                        help="Also search message text (loads the export)")
    parser.add_argument("--limit", type=int, default=10,
                        help="Maximum number of results (default: 10)")
    parser.add_argument("--delta-dir", metavar="DIR", default="",
                        help="Extra directory to look for delta files in")
    parser.add_argument("--index", metavar="CSV", default="",
                        help="Index CSV to use (default: most recent in the current directory)")
    parser.add_argument("--export", metavar="PATH", default="",
                        help="ZIP or conversations.json to use for --content "
                             "(default: most recent conversations-*.zip in the current directory)")
    return parser


def main(argv=None):
    here = os.getcwd()

    args = build_arg_parser().parse_args(argv)

    if args.index:
        index_path = args.index
        if not os.path.isfile(index_path):
            print(f"[ERROR] --index \"{index_path}\" not found.", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            index_path = _find_index(here)
        except FileNotFoundError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(1)

    export_date = _index_export_date(index_path)
    index_mtime = datetime.fromtimestamp(os.path.getmtime(index_path))
    print(f"Index   : {os.path.basename(index_path)}")
    print(f"          modified {index_mtime.strftime('%Y-%m-%d %H:%M')}  (export {export_date})")

    idx_rows = []
    with open(index_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            idx_rows.append(row)

    index_dir = os.path.dirname(os.path.abspath(index_path))
    delta_dirs = [index_dir] if args.index else [here]
    if args.delta_dir:
        extra = os.path.abspath(args.delta_dir)
        if os.path.isdir(extra):
            delta_dirs.append(extra)
        else:
            print(f"[WARN] --delta-dir \"{args.delta_dir}\" is not a valid directory.", file=sys.stderr)

    seen_abs: set = set()
    all_delta_paths: list = []
    for d in delta_dirs:
        for p in _find_deltas(d, export_date):
            ap = os.path.abspath(p)
            if ap not in seen_abs:
                seen_abs.add(ap)
                all_delta_paths.append(p)
    all_delta_paths.sort(key=_delta_sort_key)

    if all_delta_paths:
        for dp in all_delta_paths:
            print(f"Delta   : {os.path.basename(dp)}")
    else:
        print("Delta   : none found.")

    delta_by_uuid = _load_all_deltas(all_delta_paths)
    merged_rows   = _merge_index_with_deltas(idx_rows, delta_by_uuid)

    idx_uuids    = {r.get("uuid") for r in idx_rows if r.get("uuid")}
    n_delta_only = sum(1 for u in delta_by_uuid if u not in idx_uuids)
    print(f"Rows    : {len(merged_rows)} after merge "
          f"({len(idx_rows)} index + {n_delta_only} added from delta)")

    idx_by_uuid = {r["uuid"]: r for r in merged_rows if r.get("uuid")}

    if args.content:
        if args.export:
            export_path = args.export
            if not os.path.isfile(export_path):
                print(f"[ERROR] --export \"{export_path}\" not found.", file=sys.stderr)
                sys.exit(1)
        else:
            try:
                export_path = _find_zip(here)
            except FileNotFoundError as e:
                print(f"[ERROR] {e}", file=sys.stderr)
                sys.exit(1)
        export_mtime = datetime.fromtimestamp(os.path.getmtime(export_path))
        print(f"Export  : {os.path.basename(export_path)}")
        print(f"          modified {export_mtime.strftime('%Y-%m-%d %H:%M')}")

        print("          loading... this can take a few seconds.", file=sys.stderr)
        conv_list = _load_export_any(export_path)
        conv_uuids = {c.get("uuid") for c in conv_list if isinstance(c, dict) and c.get("uuid")}
        created_vals = sorted(
            (c.get("created_at") or "") for c in conv_list
            if isinstance(c, dict) and (c.get("created_at") or "")
        )
        print(f"          {len(conv_list)} conversations loaded.")
        if created_vals:
            print(f"          created_at: {created_vals[0]} -> {created_vals[-1]}")

        idx_with_msgs = {
            r["uuid"] for r in merged_rows
            if r.get("uuid") and (r.get("message_count") or "").strip()
        }
        if idx_with_msgs:
            covered = len(idx_with_msgs & conv_uuids)
            rate = covered / len(idx_with_msgs)
            if rate < 0.9:
                print(
                    f"[WARN] partial export: content search does not cover all "
                    f"conversations in the index ({covered}/{len(idx_with_msgs)} = "
                    f"{rate:.0%} of rows with a non-empty message_count)."
                )

    patterns = _compile_patterns(args.terms)
    mode_label = "AND" if args.all else "OR"
    mode_str   = "content+index" if args.content else "index only"
    print(f"Search  : {args.terms} | mode {mode_label} | {mode_str}")
    if args.project:
        print(f"Filter  : project contains \"{args.project}\"")

    if args.content:
        results = _search_content(conv_list, patterns, args.all, idx_by_uuid, args.project)
    else:
        results = _search_index(merged_rows, patterns, args.all, args.project)

    _display(results, args.limit, show_occ=args.content)


if __name__ == "__main__":
    main()
