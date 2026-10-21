#!/usr/bin/env python3
"""
Generate a fully fictional fixture for claude-conversation-index: a complete
export, a "last 30 days" partial export, a conversation-to-project mapping,
a previous index, and a delta file — plus the project JSON files.

Everything here is synthetic: fixed seed, invented uuids, generic titles and
filler message text with no connection to any real person, organization or
conversation. It exists so that the test suite (tests/) and the examples in
examples/ never need real data.

Usage:
    python -I -B tools/make_fixture.py [--out-dir examples]

Design
------
A fixed "reference date" (the date the fictional export was taken) anchors
everything, so re-running this script always produces byte-identical
output. The 30-day partial export keeps only conversations whose
`created_at` falls within the last 30 days of the reference date — this
mirrors a real, once-observed behaviour of Claude's own data export
(see docs/method.md): the window is applied to `created_at`, not to
`updated_at`. A handful of named "trap" conversations exercise every edge
case the index builder has to get right (see docs/method.md for the list).
"""

import argparse
import csv
import json
import os
import random
import uuid as uuidlib
from datetime import datetime, timedelta, timezone


REFERENCE_DATE = datetime(2026, 1, 30, 12, 0, 0, tzinfo=timezone.utc)
WINDOW_DAYS = 30
SEED = 20260130

PROJECTS = [
    {"uuid": "p-alpha",     "name": "Project Alpha"},
    {"uuid": "p-bravo",     "name": "Project Bravo"},
    {"uuid": "p-comet",     "name": "Project Comet"},
    {"uuid": "p-driftwood", "name": "Project Driftwood"},
    {"uuid": "p-ember",     "name": "Project Ember"},
    {"uuid": "p-flux",      "name": "Project Flux"},
]

TOPICS = [
    "a weeknight pasta recipe", "a fictional planet's weather patterns",
    "a short story about a lighthouse keeper", "garden layout ideas for a balcony",
    "a workout routine for beginners", "notes for a book club discussion",
    "debugging a sorting function", "a travel itinerary for a long weekend",
    "budget spreadsheet formulas", "a lesson plan about photosynthesis",
    "naming ideas for a pet turtle", "a packing list for a camping trip",
    "a crossword puzzle theme", "tips for a sourdough starter",
    "a board game house rules draft", "a poem about autumn leaves",
    "a checklist for a garage sale", "notes on an imaginary constellation",
    "a practice quiz about fractions", "ideas for a neighborhood picnic",
    "a playlist for a road trip", "a template for a weekly chore chart",
    "a comparison of two fictional cities", "a bedtime story about a kind dragon",
    "a outline for a woodworking project", "a plan for a herb garden",
    "notes on an imaginary board game", "a recipe for vegetable soup",
    "a draft toast for a birthday party", "a study plan for a trivia night",
    "an outline for a nature walk", "a list of riddles for a scavenger hunt",
]

FICTIONAL_TERM_PRIMARY = "widget workshop"
FICTIONAL_TERM_SECONDARY = "gizmo parts"


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def make_uuid(rng, label):
    """Deterministic, obviously-fictional uuid derived from a label."""
    n = rng.getrandbits(122)
    return str(uuidlib.UUID(int=n, version=4))


def make_messages(rng, topic, n, extra_primary=0, extra_secondary=0):
    msgs = []
    first_text = f"Can you help me think through {topic}? This is a fictional message."
    if extra_primary:
        first_text += f" Let's call this the {FICTIONAL_TERM_PRIMARY} for short."
    msgs.append({"uuid": f"m-{rng.getrandbits(32):08x}", "sender": "human",
                 "text": first_text, "content": [], "attachments": [], "files": [],
                 "created_at": "", "updated_at": "", "parent_message_uuid": ""})
    for i in range(n - 1):
        sender = "assistant" if i % 2 == 0 else "human"
        text = f"Fictional reply #{i+1} about {topic}."
        if extra_primary and i < extra_primary - 1:
            text += f" More on the {FICTIONAL_TERM_PRIMARY}."
        if extra_secondary and i < extra_secondary:
            text += f" Also some {FICTIONAL_TERM_SECONDARY} notes."
        msgs.append({"uuid": f"m-{rng.getrandbits(32):08x}", "sender": sender,
                     "text": text, "content": [], "attachments": [], "files": [],
                     "created_at": "", "updated_at": "", "parent_message_uuid": ""})
    return msgs


def build_dataset():
    rng = random.Random(SEED)
    conversations = []   # full fictional export (list of dicts)
    mapping_rows = []    # conversation-to-project mapping rows
    prev_rows = []       # previous index rows (new column schema)
    delta_rows = []      # delta rows

    def add_conversation(uuid, name, created_at, updated_at, topic,
                         n_messages=4, extra_primary=0, extra_secondary=0,
                         summary=None):
        conv = {
            "uuid": uuid,
            "name": name,
            "summary": summary if summary is not None else f"A fictional summary about {topic}.",
            "created_at": iso(created_at),
            "updated_at": iso(updated_at),
            "account": {"uuid": "fictional-account"},
            "chat_messages": make_messages(rng, topic, n_messages, extra_primary, extra_secondary),
        }
        conversations.append(conv)
        return conv

    def add_mapping(uuid, title, created_at, updated_at, project):
        mapping_rows.append({
            "uuid": uuid, "title": title,
            "created_at": iso(created_at), "updated_at": iso(updated_at),
            "project_uuid": project["uuid"] if project else "",
            "project_name": project["name"] if project else "",
        })

    def add_prev_row(uuid, title, created_at, updated_at, project_name,
                     message_count="", summary_excerpt="", first_message_excerpt="",
                     inferred_title="", inferred_title_source=""):
        prev_rows.append({
            "uuid": uuid, "title": title,
            "inferred_title": inferred_title, "inferred_title_source": inferred_title_source,
            "created_at": iso(created_at), "updated_at": iso(updated_at),
            "message_count": message_count,
            "summary_excerpt": summary_excerpt, "first_message_excerpt": first_message_excerpt,
            "project_name": project_name, "project_uuid": "",
            "project_source": "mapping" if project_name else "not in mapping",
            "project_previous_index": "", "project_changed": "",
            "source_line": "export+mapping", "link": "", "content_source": "export",
        })

    def add_delta(uuid, title="", created_at="", updated_at="", project=None):
        delta_rows.append({
            "uuid": uuid, "title": title,
            "created_at": iso(created_at) if created_at else "",
            "updated_at": iso(updated_at) if updated_at else "",
            "project_uuid": project["uuid"] if project else "",
            "project_name": project["name"] if project else "",
        })

    # ------------------------------------------------------------------
    # Named trap conversations (fixed uuids, fixed behaviour)
    # ------------------------------------------------------------------

    # 1. Delta older than the row's own updated_at -> must be ignored.
    u = make_uuid(rng, "trap-delta-older")
    created = REFERENCE_DATE - timedelta(days=10)
    updated = REFERENCE_DATE - timedelta(days=1)
    add_conversation(u, "Delta-older trap", created, updated, "a fictional delta-older case")
    add_mapping(u, "Delta-older trap", created, updated, PROJECTS[0])
    add_delta(u, title="SHOULD NOT APPEAR (older delta)",
              updated_at=REFERENCE_DATE - timedelta(days=5), project=PROJECTS[1])
    TRAP_DELTA_OLDER = u

    # 2. Delta newer than the row's own updated_at -> must be applied.
    u = make_uuid(rng, "trap-delta-newer")
    created = REFERENCE_DATE - timedelta(days=12)
    updated = REFERENCE_DATE - timedelta(days=3)
    add_conversation(u, "Delta-newer trap (stale title)", created, updated,
                     "a fictional delta-newer case")
    add_mapping(u, "Delta-newer trap (stale title)", created, updated, PROJECTS[0])
    add_delta(u, title="Delta-newer trap (fresh title applied)",
              updated_at=REFERENCE_DATE - timedelta(hours=1), project=PROJECTS[1])
    TRAP_DELTA_NEWER = u

    # 3. Only in the previous index -> must be carried over, never dropped.
    u = make_uuid(rng, "trap-prev-only")
    prev_created = REFERENCE_DATE - timedelta(days=200)
    prev_updated = REFERENCE_DATE - timedelta(days=190)
    add_prev_row(u, "Previous-index-only trap", prev_created, prev_updated,
                PROJECTS[2]["name"], message_count="8",
                summary_excerpt="", first_message_excerpt="")
    TRAP_PREV_ONLY = u

    # 4. No title in the export -> inferred_title must be derived from the summary.
    u = make_uuid(rng, "trap-no-title")
    created = REFERENCE_DATE - timedelta(days=4)
    updated = REFERENCE_DATE - timedelta(days=2)
    add_conversation(u, "", created, updated, "a fictional untitled case",
                     summary="A fictional summary used to infer a title for this untitled case.")
    add_mapping(u, "", created, updated, PROJECTS[2])
    TRAP_NO_TITLE = u

    # 5. No project anywhere -> project_name must stay empty.
    u = make_uuid(rng, "trap-no-project")
    created = REFERENCE_DATE - timedelta(days=6)
    updated = REFERENCE_DATE - timedelta(days=5)
    add_conversation(u, "No-project trap", created, updated, "a fictional unassigned case")
    TRAP_NO_PROJECT = u

    # 6. Created well before the 30-day window but updated recently -> must be
    #    excluded from the fictional "30 days" partial export (created_at
    #    governs the window), while still present in the full export.
    u = make_uuid(rng, "trap-old-created-recent-update")
    created = REFERENCE_DATE - timedelta(days=200)
    updated = REFERENCE_DATE - timedelta(days=5)
    add_conversation(u, "Old-created-recent-update trap", created, updated,
                     "a fictional case created long ago and updated recently",
                     n_messages=6)
    add_mapping(u, "Old-created-recent-update trap", created, updated, PROJECTS[3])
    add_prev_row(u, "Old-created-recent-update trap (previous title)", created,
                REFERENCE_DATE - timedelta(days=60), PROJECTS[3]["name"],
                message_count="4")
    TRAP_OLD_CREATED_RECENT_UPDATE = u

    # 7. Only in a delta -> must be added with source_line = 'delta only'.
    u = make_uuid(rng, "trap-delta-only")
    add_delta(u, title="Delta-only trap", created_at=REFERENCE_DATE - timedelta(days=2),
              updated_at=REFERENCE_DATE - timedelta(hours=2), project=PROJECTS[4])
    TRAP_DELTA_ONLY = u

    # 8. Delta without updated_at -> must never be applied to an existing row.
    u = make_uuid(rng, "trap-delta-no-updated-at")
    created = REFERENCE_DATE - timedelta(days=8)
    updated = REFERENCE_DATE - timedelta(days=4)
    add_conversation(u, "Delta-without-updated_at trap (unchanged)", created, updated,
                     "a fictional delta-without-updated_at case")
    add_mapping(u, "Delta-without-updated_at trap (unchanged)", created, updated, PROJECTS[0])
    add_delta(u, title="SHOULD NOT APPEAR (no updated_at in delta)", project=PROJECTS[1])
    TRAP_DELTA_NO_UPDATED_AT = u

    # 9. In the mapping AND targeted by a fresh delta -> the delta's project
    #    must be ignored (the mapping always wins), but a fresh title may
    #    still apply.
    u = make_uuid(rng, "trap-mapping-protects-project")
    created = REFERENCE_DATE - timedelta(days=9)
    updated = REFERENCE_DATE - timedelta(days=3)
    add_conversation(u, "Mapping-protects-project trap (stale title)", created, updated,
                     "a fictional mapping-protects-project case")
    add_mapping(u, "Mapping-protects-project trap (stale title)", created, updated, PROJECTS[2])
    add_delta(u, title="Mapping-protects-project trap (fresh title applied)",
              updated_at=REFERENCE_DATE - timedelta(hours=1), project=PROJECTS[5])
    TRAP_MAPPING_PROTECTS_PROJECT = u

    # 10. In the export but NOT in the mapping -> a fresh delta's project
    #     MUST apply (the mapping-protects-project rule only protects rows
    #     that are actually in the mapping).
    u = make_uuid(rng, "trap-delta-project-no-mapping")
    created = REFERENCE_DATE - timedelta(days=11)
    updated = REFERENCE_DATE - timedelta(days=4)
    add_conversation(u, "Delta-project-without-mapping trap", created, updated,
                     "a fictional case with no mapping row")
    add_delta(u, title="Delta-project-without-mapping trap (delta applied)",
              updated_at=REFERENCE_DATE - timedelta(hours=1), project=PROJECTS[5])
    TRAP_DELTA_PROJECT_NO_MAPPING = u

    # 11. The content-search probe: a conversation with a controlled number
    #     of occurrences of two fictional terms, for deterministic
    #     --content search assertions.
    u = make_uuid(rng, "trap-search-probe")
    created = REFERENCE_DATE - timedelta(days=7)
    updated = REFERENCE_DATE - timedelta(days=1)
    add_conversation(u, "Search-probe trap", created, updated,
                     "a fictional search probe case", n_messages=6,
                     extra_primary=5, extra_secondary=3)
    add_mapping(u, "Search-probe trap", created, updated, PROJECTS[1])
    TRAP_SEARCH_PROBE = u

    # ------------------------------------------------------------------
    # Filler conversations (random but deterministic: fixed seed)
    # ------------------------------------------------------------------
    n_filler = 32
    for i in range(n_filler):
        u = make_uuid(rng, f"filler-{i}")
        topic = TOPICS[i % len(TOPICS)]
        days_ago = rng.randint(0, 59)
        created = REFERENCE_DATE - timedelta(days=days_ago, hours=rng.randint(0, 23))
        updated = created + timedelta(hours=rng.randint(0, 48))
        if updated > REFERENCE_DATE:
            updated = REFERENCE_DATE
        name = f"Fictional thread about {topic}"
        add_conversation(u, name, created, updated, topic, n_messages=rng.randint(2, 10))
        project = rng.choice(PROJECTS + [None, None])  # ~1/4 unassigned
        add_mapping(u, name, created, updated, project)

    return {
        "conversations": conversations,
        "mapping_rows": mapping_rows,
        "prev_rows": prev_rows,
        "delta_rows": delta_rows,
        "traps": {
            "delta_older": TRAP_DELTA_OLDER,
            "delta_newer": TRAP_DELTA_NEWER,
            "prev_only": TRAP_PREV_ONLY,
            "no_title": TRAP_NO_TITLE,
            "no_project": TRAP_NO_PROJECT,
            "old_created_recent_update": TRAP_OLD_CREATED_RECENT_UPDATE,
            "delta_only": TRAP_DELTA_ONLY,
            "delta_no_updated_at": TRAP_DELTA_NO_UPDATED_AT,
            "mapping_protects_project": TRAP_MAPPING_PROTECTS_PROJECT,
            "delta_project_no_mapping": TRAP_DELTA_PROJECT_NO_MAPPING,
            "search_probe": TRAP_SEARCH_PROBE,
        },
    }


INDEX_COLS = [
    "uuid", "title", "inferred_title", "inferred_title_source",
    "created_at", "updated_at", "message_count",
    "summary_excerpt", "first_message_excerpt",
    "project_name", "project_uuid", "project_source", "project_previous_index",
    "project_changed", "source_line", "link", "content_source",
]

MAPPING_COLS = ["uuid", "title", "created_at", "updated_at", "project_uuid", "project_name"]
DELTA_COLS   = ["uuid", "title", "created_at", "updated_at", "project_uuid", "project_name"]


def write_csv(path, cols, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="examples",
                        help="Output directory (default: examples)")
    args = parser.parse_args()

    out = args.out_dir
    full_dir = os.path.join(out, "export-full")
    partial_dir = os.path.join(out, "export-30days")
    os.makedirs(os.path.join(full_dir, "projects"), exist_ok=True)
    os.makedirs(os.path.join(partial_dir, "projects"), exist_ok=True)

    data = build_dataset()
    conversations = data["conversations"]

    # Full fictional export.
    with open(os.path.join(full_dir, "conversations.json"), "w", encoding="utf-8") as f:
        json.dump(conversations, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # Partial ("last 30 days") fictional export: filtered on created_at, as
    # documented in docs/method.md.
    cutoff = REFERENCE_DATE - timedelta(days=WINDOW_DAYS)
    partial = [c for c in conversations if c["created_at"] >= iso(cutoff)]
    with open(os.path.join(partial_dir, "conversations.json"), "w", encoding="utf-8") as f:
        json.dump(partial, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # Project JSON files (same set for both exports).
    for pj in PROJECTS:
        proj_obj = {
            "uuid": pj["uuid"], "name": pj["name"],
            "description": f"A fictional project: {pj['name']}.",
            "is_private": True, "is_starter_project": False,
            "creator": {"uuid": "fictional-account"},
            "created_at": iso(REFERENCE_DATE - timedelta(days=300)),
            "updated_at": iso(REFERENCE_DATE - timedelta(days=1)),
            "docs": [],
            "prompt_template": "",
        }
        for d in (full_dir, partial_dir):
            with open(os.path.join(d, "projects", f"{pj['uuid']}.json"), "w", encoding="utf-8") as f:
                json.dump(proj_obj, f, ensure_ascii=False, indent=2)
                f.write("\n")

    write_csv(os.path.join(out, "mapping.csv"), MAPPING_COLS, data["mapping_rows"])
    write_csv(os.path.join(out, "previous-index.csv"), INDEX_COLS, data["prev_rows"])
    # The leading date must be >= the export date for build_index's and
    # consult's own delta-file freshness filter (by file name) to retain
    # it; "_delta_" must appear in the name for consult's auto-discovery.
    write_csv(os.path.join(out, "2026-01-30_delta_sync.csv"), DELTA_COLS, data["delta_rows"])

    with open(os.path.join(out, "TRAPS.json"), "w", encoding="utf-8") as f:
        json.dump({
            "reference_date": iso(REFERENCE_DATE),
            "window_days": WINDOW_DAYS,
            "cutoff": iso(cutoff),
            "traps": data["traps"],
            "fictional_terms": {
                "primary": FICTIONAL_TERM_PRIMARY,
                "secondary": FICTIONAL_TERM_SECONDARY,
                "primary_occurrences_in_probe": 5,
                "secondary_occurrences_in_probe": 3,
            },
        }, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Full export        : {len(conversations)} conversations -> {full_dir}")
    print(f"30-day export      : {len(partial)} conversations -> {partial_dir}")
    print(f"Mapping rows       : {len(data['mapping_rows'])}")
    print(f"Previous index rows: {len(data['prev_rows'])}")
    print(f"Delta rows         : {len(data['delta_rows'])}")
    print(f"Trap/probe index   : {os.path.join(out, 'TRAPS.json')}")


if __name__ == "__main__":
    main()
