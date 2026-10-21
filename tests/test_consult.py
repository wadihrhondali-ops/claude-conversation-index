import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONSULT_SCRIPT = os.path.join(REPO_ROOT, "src", "claude_conversation_index", "consult.py")
EXAMPLES = os.path.join(REPO_ROOT, "examples")


def run_cli(args):
    return subprocess.run(
        [sys.executable, "-I", "-B", CONSULT_SCRIPT] + args,
        capture_output=True, text=True,
    )


def test_help_runs():
    result = run_cli(["--help"])
    assert result.returncode == 0
    assert "--content" in result.stdout


def _index_path(built):
    # The only csv in the build output matching <prefix>_<date>.csv.
    import glob
    candidates = [
        p for p in glob.glob(os.path.join(str(built["dir"]), "*.csv"))
        if "_comparison_" not in p and "_summary_" not in p
    ]
    assert len(candidates) == 1
    return candidates[0]


def test_index_search_by_title(built_full):
    result = run_cli([
        "--index", _index_path(built_full),
        "No-project",
    ])
    assert result.returncode == 0
    assert "1 result" in result.stdout


def test_content_search_primary_term(traps, built_full):
    with open(os.path.join(EXAMPLES, "TRAPS.json"), encoding="utf-8") as f:
        traps_data = json.load(f)
    terms = traps_data["fictional_terms"]
    result = run_cli([
        "--index", _index_path(built_full),
        "--export", os.path.join(EXAMPLES, "export-full", "conversations.json"),
        "--content", terms["primary"],
    ])
    assert result.returncode == 0
    probe_uuid = traps["traps"]["search_probe"]
    assert probe_uuid in result.stdout
    # one result line should show the expected occurrence count in the last
    # numeric column before the link
    lines = [l for l in result.stdout.splitlines() if probe_uuid in l]
    assert len(lines) == 1
    occ = int(lines[0].split()[-2])
    assert occ == terms["primary_occurrences_in_probe"]


def test_content_search_two_terms_or(traps, built_full):
    with open(os.path.join(EXAMPLES, "TRAPS.json"), encoding="utf-8") as f:
        traps_data = json.load(f)
    terms = traps_data["fictional_terms"]
    result = run_cli([
        "--index", _index_path(built_full),
        "--export", os.path.join(EXAMPLES, "export-full", "conversations.json"),
        "--content", terms["primary"], terms["secondary"],
    ])
    assert result.returncode == 0
    probe_uuid = traps["traps"]["search_probe"]
    lines = [l for l in result.stdout.splitlines() if probe_uuid in l]
    assert len(lines) == 1
    occ = int(lines[0].split()[-2])
    assert occ == (terms["primary_occurrences_in_probe"] + terms["secondary_occurrences_in_probe"])


def test_all_mode_requires_every_term(built_full):
    result = run_cli([
        "--index", _index_path(built_full),
        "--all",
        "Mapping-protects-project", "fresh title applied",
    ])
    assert result.returncode == 0
    assert "1 result" in result.stdout

    result2 = run_cli([
        "--index", _index_path(built_full),
        "--all",
        "Mapping-protects-project", "a term that cannot possibly match",
    ])
    assert result2.returncode == 0
    assert "0 result" in result2.stdout


def test_partial_export_warning(built_full):
    result = run_cli([
        "--index", _index_path(built_full),
        "--export", os.path.join(EXAMPLES, "export-30days", "conversations.json"),
        "--content", "a",
    ])
    assert result.returncode == 0
    assert "[WARN] partial export" in result.stdout


def test_project_filter(built_full):
    result = run_cli([
        "--index", _index_path(built_full),
        "--project", "Project Comet",
        "trap",
    ])
    assert result.returncode == 0
    # at least the untitled trap (mapped to Project Comet) should show up
    assert "result" in result.stdout
