import csv
import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(REPO_ROOT, "examples")

sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from claude_conversation_index import build_index as bi  # noqa: E402


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="session")
def traps():
    with open(os.path.join(EXAMPLES, "TRAPS.json"), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def mapping_path():
    return os.path.join(EXAMPLES, "mapping.csv")


@pytest.fixture(scope="session")
def previous_index_path():
    return os.path.join(EXAMPLES, "previous-index.csv")


@pytest.fixture(scope="session")
def delta_path():
    return os.path.join(EXAMPLES, "2026-01-30_delta_sync.csv")


@pytest.fixture(scope="session")
def full_export_dir():
    return os.path.join(EXAMPLES, "export-full")


@pytest.fixture(scope="session")
def partial_export_dir():
    return os.path.join(EXAMPLES, "export-30days")


def _build(tmp_path_factory, export_dir, mapping_path, previous_index_path,
          delta_path, prefix, with_excerpts):
    out_dir = tmp_path_factory.mktemp(prefix)
    bi.main([
        "--export-json", os.path.join(export_dir, "conversations.json"),
        "--mapping", mapping_path,
        "--previous-index", previous_index_path,
        "--projects-dir", os.path.join(export_dir, "projects"),
        "--export-date", "2026-01-30",
        "--output-dir", str(out_dir),
        "--deltas", delta_path,
        "--prefix", prefix,
    ] + (["--with-excerpts"] if with_excerpts else []))
    return out_dir


@pytest.fixture(scope="session")
def built_full(tmp_path_factory, full_export_dir, mapping_path, previous_index_path, delta_path):
    out_dir = _build(tmp_path_factory, full_export_dir, mapping_path, previous_index_path,
                     delta_path, "full", with_excerpts=True)
    rows = read_csv(os.path.join(out_dir, "full_2026-01-30.csv"))
    return {"dir": out_dir, "rows": rows, "by_uuid": {r["uuid"]: r for r in rows}}


@pytest.fixture(scope="session")
def built_partial(tmp_path_factory, partial_export_dir, mapping_path, previous_index_path, delta_path):
    out_dir = _build(tmp_path_factory, partial_export_dir, mapping_path, previous_index_path,
                     delta_path, "partial", with_excerpts=False)
    rows = read_csv(os.path.join(out_dir, "partial_2026-01-30.csv"))
    return {"dir": out_dir, "rows": rows, "by_uuid": {r["uuid"]: r for r in rows}}
