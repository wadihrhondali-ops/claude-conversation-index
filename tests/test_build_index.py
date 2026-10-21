import csv
import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_SCRIPT = os.path.join(REPO_ROOT, "src", "claude_conversation_index", "build_index.py")


def run_cli(args):
    return subprocess.run(
        [sys.executable, "-I", "-B", BUILD_SCRIPT] + args,
        capture_output=True, text=True,
    )


def test_help_runs():
    result = run_cli(["--help"])
    assert result.returncode == 0
    assert "--export-json" in result.stdout


def test_unique_uuids(built_full, built_partial):
    for built in (built_full, built_partial):
        uuids = [r["uuid"] for r in built["rows"]]
        assert len(uuids) == len(set(uuids))
        assert all(uuids)


def test_independent_union_count(full_export_dir, partial_export_dir, mapping_path,
                                 previous_index_path, delta_path, built_full, built_partial):
    """
    Recompute the expected row count independently of build_index's own
    union logic, straight from the raw files, and compare.
    """
    def union_count(export_dir):
        with open(os.path.join(export_dir, "conversations.json"), encoding="utf-8") as f:
            export_uuids = {c["uuid"] for c in json.load(f)}
        with open(mapping_path, newline="", encoding="utf-8") as f:
            mapping_uuids = {r["uuid"] for r in csv.DictReader(f)}
        with open(previous_index_path, newline="", encoding="utf-8") as f:
            prev_uuids = {r["uuid"] for r in csv.DictReader(f)}
        with open(delta_path, newline="", encoding="utf-8") as f:
            delta_uuids = {r["uuid"] for r in csv.DictReader(f) if r.get("uuid")}
        return export_uuids | mapping_uuids | prev_uuids | delta_uuids

    assert len(built_full["rows"]) == len(union_count(full_export_dir))
    assert len(built_partial["rows"]) == len(union_count(partial_export_dir))


def test_source_line_sums_to_row_count(built_full, built_partial):
    for built in (built_full, built_partial):
        from collections import Counter
        counts = Counter(r["source_line"] for r in built["rows"])
        assert sum(counts.values()) == len(built["rows"])


def test_previous_index_only_row_is_kept(traps, built_full, built_partial):
    uuid = traps["traps"]["prev_only"]
    for built in (built_full, built_partial):
        row = built["by_uuid"][uuid]
        assert row["source_line"] == "previous index only"
        assert row["content_source"] == "previous index"
        assert row["message_count"] == "8"
        assert row["project_name"] == "Project Comet"


def test_no_content_loss_vs_previous_index(previous_index_path, built_full, built_partial):
    with open(previous_index_path, newline="", encoding="utf-8") as f:
        prev_rows = {r["uuid"]: r for r in csv.DictReader(f)}
    for built in (built_full, built_partial):
        for uuid, prev in prev_rows.items():
            row = built["by_uuid"].get(uuid)
            assert row is not None, f"{uuid} must not disappear from the index"
            for field in ("message_count",):
                if (prev.get(field) or "").strip():
                    assert (row.get(field) or "").strip(), (
                        f"{field} was lost for {uuid} in {built['dir']}"
                    )


def test_delta_older_is_ignored(traps, built_full):
    uuid = traps["traps"]["delta_older"]
    row = built_full["by_uuid"][uuid]
    assert "SHOULD NOT APPEAR" not in row["title"]
    assert row["project_name"] == "Project Alpha"


def test_delta_newer_is_applied(traps, built_full):
    uuid = traps["traps"]["delta_newer"]
    row = built_full["by_uuid"][uuid]
    assert row["title"] == "Delta-newer trap (fresh title applied)"
    # This conversation is also in the mapping, so its project must stay
    # the mapping's value even though the fresher delta tried to change it
    # (the mapping always wins over a delta's project -- see
    # test_mapping_project_wins_over_delta for the dedicated trap).
    assert row["project_name"] == "Project Alpha"


def test_delta_without_updated_at_is_never_applied(traps, built_full):
    uuid = traps["traps"]["delta_no_updated_at"]
    row = built_full["by_uuid"][uuid]
    assert "SHOULD NOT APPEAR" not in row["title"]
    assert row["project_name"] == "Project Alpha"


def test_delta_only_uuid_is_added(traps, built_full):
    uuid = traps["traps"]["delta_only"]
    row = built_full["by_uuid"][uuid]
    assert row["source_line"] == "delta only"
    assert row["title"] == "Delta-only trap"
    assert row["project_name"] == "Project Ember"


def test_mapping_project_wins_over_delta(traps, built_full):
    uuid = traps["traps"]["mapping_protects_project"]
    row = built_full["by_uuid"][uuid]
    # The mapping's project must survive even though a fresher delta tried
    # to change it...
    assert row["project_name"] == "Project Comet"
    assert row["project_source"] == "mapping"
    # ...while the title, which has no such protection, does follow the
    # fresher delta.
    assert row["title"] == "Mapping-protects-project trap (fresh title applied)"


def test_delta_project_applies_when_not_in_mapping(traps, built_full):
    uuid = traps["traps"]["delta_project_no_mapping"]
    row = built_full["by_uuid"][uuid]
    assert row["title"] == "Delta-project-without-mapping trap (delta applied)"
    assert row["project_name"] == "Project Flux"
    # NOTE: project_source is NOT recomputed after a delta merge (it still
    # reads "not in mapping" from the build step) even though project_name
    # and project_uuid were updated -- a known characteristic inherited
    # unchanged from the validated upstream logic, documented in
    # docs/method.md.
    assert row["project_source"] == "not in mapping"


def test_untitled_conversation_gets_inferred_title(traps, built_full):
    uuid = traps["traps"]["no_title"]
    row = built_full["by_uuid"][uuid]
    assert row["title"] == ""
    assert row["inferred_title"] != ""
    assert row["inferred_title_source"] == "summary"


def test_conversation_without_project_stays_unassigned(traps, built_full):
    uuid = traps["traps"]["no_project"]
    row = built_full["by_uuid"][uuid]
    assert row["project_name"] == ""


def test_partial_export_window_is_on_created_at(traps, built_full, built_partial):
    """
    A conversation created well outside the 30-day window but updated
    recently must be absent from the fictional partial export, yet still
    present in the index (carried over from the previous index), with a
    stale message_count; the full-export build must show the fresh value.
    """
    uuid = traps["traps"]["old_created_recent_update"]
    partial_row = built_partial["by_uuid"][uuid]
    full_row = built_full["by_uuid"][uuid]

    assert partial_row["content_source"] == "previous index"
    assert partial_row["message_count"] == "4"  # stale value from previous-index.csv

    assert full_row["content_source"] == "export"
    assert full_row["message_count"] == "6"  # fresh value from the full export


def test_excerpts_empty_by_default(built_partial):
    assert all(not r["summary_excerpt"] for r in built_partial["rows"])
    assert all(not r["first_message_excerpt"] for r in built_partial["rows"])


def test_excerpts_filled_with_flag(built_full):
    with_content = [r for r in built_full["rows"] if r["content_source"] == "export"]
    assert any(r["first_message_excerpt"] for r in with_content)


def test_refuses_to_overwrite(tmp_path, full_export_dir, mapping_path, previous_index_path, delta_path):
    args = [
        "--export-json", os.path.join(full_export_dir, "conversations.json"),
        "--mapping", mapping_path,
        "--previous-index", previous_index_path,
        "--projects-dir", os.path.join(full_export_dir, "projects"),
        "--export-date", "2026-01-30",
        "--output-dir", str(tmp_path),
        "--deltas", delta_path,
        "--prefix", "once",
    ]
    r1 = run_cli(args)
    assert r1.returncode == 0
    r2 = run_cli(args)
    assert r2.returncode != 0
    assert "already exists" in r2.stderr
