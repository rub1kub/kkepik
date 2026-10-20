"""Import source schedule archives without broadcasting or touching users.

Usage: venv/bin/python scripts/backfill_knowledge.py [--data-dir DIR] [--limit N]
Every run is idempotent. XLSX date sheets and PDF content dates are preserved.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from schedules.knowledge_store import KnowledgeStore, normalize_date
from schedules.pdf_to_df import extract_date_from_pdf_content, pdf_to_dataframe

DATE_RE = re.compile(r"\d{1,2}[._]\d{1,2}[._]\d{4}")


def _filename_date(path: Path) -> str | None:
    match = DATE_RE.search(path.name)
    if not match:
        return None
    try:
        return normalize_date(match[0])
    except ValueError:
        return None


def import_archive(store: KnowledgeStore, *, limit: int | None = None) -> dict:
    paths = [p for p in store.data_dir.iterdir() if p.is_file() and not p.name.startswith("~$")
             and p.suffix.lower() in {".pdf", ".xlsx"} and "ГРУПП" in p.name.upper()]
    paths.sort(key=lambda p: (_filename_date(p) or "", p.stat().st_mtime, p.name))
    if limit is not None:
        paths = paths[:limit]
    totals = {"files": len(paths), "created": 0, "unchanged": 0, "failed": 0, "skipped": 0, "lessons": 0}
    for path in paths:
        received_at = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat(timespec="seconds")
        fallback_date = _filename_date(path)
        try:
            if path.suffix.lower() == ".pdf":
                content_date = extract_date_from_pdf_content(str(path))
                date = normalize_date(content_date) if content_date else fallback_date
                if not date:
                    totals["skipped"] += 1
                    continue
                frames = [(date, pdf_to_dataframe(str(path)))]
            else:
                book = pd.ExcelFile(path)
                sheets = []
                for sheet in book.sheet_names:
                    match = DATE_RE.fullmatch(sheet.strip())
                    if match:
                        try:
                            sheets.append((normalize_date(match[0]), sheet))
                        except ValueError:
                            pass
                if not sheets and fallback_date:
                    sheets = [(fallback_date, book.sheet_names[0])]
                frames = [(date, pd.read_excel(book, sheet_name=sheet, header=None)) for date, sheet in sheets]
                book.close()
            if not frames:
                totals["skipped"] += 1
            for date, frame in frames:
                result = store.ingest(path, date, frame, received_at=received_at, archived_import=True)
                totals["created" if result["created"] else "unchanged"] += 1
                totals["failed"] += int(result["status"] == "failed")
                totals["lessons"] += result.get("lessons", 0)
                print(json.dumps({"file": path.name, "date": date, **result}, ensure_ascii=False), flush=True)
        except Exception as exc:
            if fallback_date:
                result = store.ingest(path, fallback_date, None, error=type(exc).__name__, received_at=received_at, archived_import=True)
                totals["created" if result["created"] else "unchanged"] += 1
                totals["failed"] += 1
                print(json.dumps({"file": path.name, "date": fallback_date, **result}, ensure_ascii=False), flush=True)
            else:
                totals["skipped"] += 1
                print(json.dumps({"file": path.name, "status": "skipped", "reason": type(exc).__name__}, ensure_ascii=False), flush=True)
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit должен быть положительным")
    totals = import_archive(KnowledgeStore(args.data_dir), limit=args.limit)
    print(json.dumps({"summary": totals}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
