"""Verify the October 13 PDF page-continuation repair; no writes without --apply."""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCE = ROOT / "data/Расписание на 13.10.2026_ГРУППЫ.pdf"
SOURCE_SHA = "3f7970f6030289cac3c40d177aaf2aece6ebc48e6bfd9aa3110eac74455e1f30"
DATE = "2026-10-13"
EXPECTED_GROUPS = {
    "112-Д9-3РЭС", "113-КД9-3КСК", "114-КД9-3КСК", "116-КД9-3ССА",
    "146-Д9-1РУПО", "147-Д9-1БТС", "148-Д9-1ИНБ", "149-Д9-1РЭС",
}


def by_group(lessons):
    groups = defaultdict(list)
    for lesson in lessons:
        groups[lesson["group"]].append(json.dumps(lesson, sort_keys=True, ensure_ascii=False))
    return {group: sorted(values) for group, values in groups.items()}


def verify():
    from schedules.pdf_to_df import pdf_to_dataframe
    from schedules.knowledge_store import extract_lessons

    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != SOURCE_SHA:
        raise ValueError("Source changed; this incident repair no longer applies")
    with closing(sqlite3.connect(f"file:{ROOT / 'data/knowledge.db'}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        baseline = db.execute(
            "SELECT * FROM revisions WHERE date=? AND source_sha=? "
            "AND parser_version='structured-1' AND status='parsed' ORDER BY revision LIMIT 1",
            (DATE, SOURCE_SHA),
        ).fetchone()
        if baseline is None:
            raise ValueError("Incident baseline not found")
        previous = [json.loads(row[0]) for row in db.execute(
            "SELECT payload_json FROM lessons WHERE revision_id=?", (baseline["id"],))]
    frame = pdf_to_dataframe(str(SOURCE))
    groups, lessons = extract_lessons(frame, DATE)
    old, new = by_group(previous), by_group(lessons)
    changed = {group for group in old.keys() | new.keys() if old.get(group, []) != new.get(group, [])}
    if changed != EXPECTED_GROUPS:
        raise ValueError("Unexpected changed groups; manual review required")
    if len(groups) != 65 or not set(json.loads(baseline["groups_json"])).issubset(groups):
        raise ValueError("Unexpected group coverage")
    sample = [item for item in lessons if item["group"] == "116-КД9-3ССА"]
    if [item["pair_number"] for item in sample] != [1, 2, 3, 4]:
        raise ValueError("The reported group is still incomplete")
    for item in sample:
        teacher, room = ("Гринь И.В.", "59") if item["pair_number"] == 2 else ("Искандарян К.С.", "58")
        if item["teacher"] != teacher or item["audiences"] != [room]:
            raise ValueError("The reported group's teacher/room does not match the PDF")
    practice = [item["pair_number"] for item in lessons if item["group"] == "112-Д9-3РЭС"]
    if practice != [4, 5, 6]:
        raise ValueError("The page-two practice continuation is incomplete")
    report = {
        "date": DATE, "source_sha": SOURCE_SHA,
        "old_groups": len(json.loads(baseline["groups_json"])), "new_groups": len(groups),
        "old_lessons": len(previous), "new_lessons": len(lessons),
        "changed_groups": sorted(changed), "unaffected_groups_unchanged": True,
        "reported_group": sample,
    }
    return frame, dict(baseline), report


def main(apply=False):
    frame, baseline, report = verify()
    if apply:
        from schedules.knowledge_store import KnowledgeStore
        from schedules.schedule_catalog import update_schedule_catalog
        result = KnowledgeStore(ROOT / "data").ingest(
            SOURCE, DATE, frame, received_at=baseline["received_at"], archived_import=False)
        if result["status"] != "parsed":
            raise ValueError("The corrected revision was not indexed")
        report["ingestion"] = result
        report["catalog"] = update_schedule_catalog(
            frame, "13.10.2026", "groups", source_name=SOURCE.name)
    report["mode"] = "apply" if apply else "dry-run"
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    try:
        main(parser.parse_args().apply)
    except Exception as exc:
        print(json.dumps({"status": "stopped", "error_type": type(exc).__name__,
                          "reason": str(exc)[:250]}, ensure_ascii=False), flush=True)
        sys.exit(1)
