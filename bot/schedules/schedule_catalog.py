"""Persistent schedule entities for the current academic year."""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

from schedules.group_schedule import is_group_name
from schedules.schedule_dates import current_college_date
from schedules.teacher_schedule import is_likely_teacher_name, normalize_teacher_name


CATALOG_VERSION = 1
ACADEMIC_YEAR_START_MONTH = 8
_PDF_GROUP_COLS = (2, 6, 10, 14)
_PDF_AUDIENCE_COLS = (3, 5, 7, 9, 11, 13, 15, 17)
_SPECIAL_AUDIENCE_RE = re.compile(
    r"^(?:"
    r"тир|вц|иц|с/з(?:_\d+)?|спорт(?:ивный)?\.?\s*зал|"
    r"акт(?:овый)?\.?\s*зал|библиотека|мастерская|полигон"
    r")$",
    re.IGNORECASE,
)
_NUMBERED_AUDIENCE_RE = re.compile(
    r"^(?:\d{1,3}(?:\s*[-\u2013]\s*[a-zа-я])?|[a-zа-я]\s*[-\u2013]?\s*\d{1,3})$",
    re.IGNORECASE,
)


def academic_year_for_date(value: str | dt.date) -> str:
    if isinstance(value, str):
        parsed = dt.datetime.strptime(value, "%d.%m.%Y").date()
    else:
        parsed = value
    start_year = parsed.year if parsed.month >= ACADEMIC_YEAR_START_MONTH else parsed.year - 1
    return f"{start_year}-{start_year + 1}"


def current_academic_year(today: dt.date | None = None) -> str:
    return academic_year_for_date(today or current_college_date())


def get_catalog_path(data_dir: str | os.PathLike[str] | None = None) -> Path:
    if data_dir is None:
        import config

        data_dir = config.DATA_DIR
    return Path(data_dir) / "schedule_catalog.json"


def _empty_catalog(academic_year: str) -> dict[str, Any]:
    return {
        "version": CATALOG_VERSION,
        "academic_year": academic_year,
        "updated_at": None,
        "last_schedule_date": None,
        "groups": [],
        "teachers": [],
        "audiences": [],
        "sources": {},
    }


def _natural_key(value: str) -> list[tuple[int, Any]]:
    return [
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", value)
        if part
    ]


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted(
        {item.strip() for item in value if isinstance(item, str) and item.strip()},
        key=_natural_key,
    )


def _read_catalog(path: Path, academic_year: str) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as catalog_file:
            data = json.load(catalog_file)
    except (OSError, ValueError, TypeError):
        return _empty_catalog(academic_year)

    if (
        not isinstance(data, dict)
        or data.get("version") != CATALOG_VERSION
        or data.get("academic_year") != academic_year
    ):
        return _empty_catalog(academic_year)

    catalog = _empty_catalog(academic_year)
    catalog.update(data)
    catalog["groups"] = _clean_list(catalog.get("groups"))
    catalog["teachers"] = _clean_list(catalog.get("teachers"))
    catalog["audiences"] = _clean_list(catalog.get("audiences"))
    if not isinstance(catalog.get("sources"), dict):
        catalog["sources"] = {}
    return catalog


def load_catalog(
    *,
    today: dt.date | None = None,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    academic_year = current_academic_year(today)
    catalog_path = Path(path) if path is not None else get_catalog_path()
    return _read_catalog(catalog_path, academic_year)


def get_catalog_values(
    kind: str,
    *,
    today: dt.date | None = None,
    path: str | os.PathLike[str] | None = None,
) -> list[str]:
    if kind not in {"groups", "teachers", "audiences"}:
        raise ValueError(f"Unsupported catalog kind: {kind}")
    return list(load_catalog(today=today, path=path)[kind])


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return re.sub(r"\s+", " ", str(value)).strip()


def _canonical_teacher(value: str) -> str:
    value = re.sub(r"\s+", " ", value.replace("_", " ")).strip()
    parts = value.split(" ")
    if len(parts) < 2:
        return value
    initials = re.sub(r"[^A-Za-zА-Яа-яЁё]", "", "".join(parts[1:]))
    if len(initials) != 2:
        return value
    return f"{parts[0]} {initials[0].upper()}.{initials[1].upper()}."


def _deduplicate_teachers(values: list[str]) -> list[str]:
    teachers: dict[str, str] = {}
    for value in values:
        key = normalize_teacher_name(value)
        if key and key not in teachers:
            teachers[key] = _canonical_teacher(value)
    return sorted(teachers.values(), key=_natural_key)


def _canonical_audience(value: str) -> str | None:
    value = re.sub(r"\s+", " ", value).strip().strip(".,")
    if not value or value == "0" or is_group_name(value):
        return None

    try:
        number = float(value.replace(",", "."))
        if number.is_integer() and 0 < number < 1000:
            return str(int(number))
    except ValueError:
        pass

    if _NUMBERED_AUDIENCE_RE.fullmatch(value):
        return re.sub(r"\s*[-\u2013]\s*", "-", value).lower()
    if _SPECIAL_AUDIENCE_RE.fullmatch(value):
        return value.upper() if len(value) <= 5 else value
    return None


def _audiences_from_cell(value: Any) -> list[str]:
    text = _cell_text(value)
    if not text:
        return []

    direct = _canonical_audience(text)
    if direct:
        return [direct]

    if re.fullmatch(r"\d{1,3}(?:\s*[/,;]\s*\d{1,3})+", text):
        result = []
        for part in re.split(r"[/,;]", text):
            canonical = _canonical_audience(part)
            if canonical:
                result.append(canonical)
        return result
    return []


def _looks_like_pdf_groups(df: pd.DataFrame) -> bool:
    if df.shape[1] < 15:
        return False
    for row in range(df.shape[0]):
        for col in _PDF_GROUP_COLS:
            if col < df.shape[1] and is_group_name(_cell_text(df.iat[row, col])):
                return True
    return False


def _extract_audiences(df: pd.DataFrame) -> list[str]:
    if _looks_like_pdf_groups(df):
        columns = [col for col in _PDF_AUDIENCE_COLS if col < df.shape[1]]
    else:
        from schedules.audience_schedule import find_audience_columns

        columns = [col for col in find_audience_columns(df) if col < df.shape[1]]

    audiences = set()
    for col in columns:
        for row in range(df.shape[0]):
            audiences.update(_audiences_from_cell(df.iat[row, col]))
    return sorted(audiences, key=_natural_key)


def extract_catalog_entries(df: pd.DataFrame) -> dict[str, list[str]]:
    groups = set()
    teacher_values = []
    for row in range(df.shape[0]):
        for col in range(df.shape[1]):
            text = _cell_text(df.iat[row, col])
            if not text:
                continue
            if is_group_name(text):
                groups.add(text.upper())
            elif is_likely_teacher_name(text):
                teacher_values.append(text)

    return {
        "groups": sorted(groups, key=_natural_key),
        "teachers": _deduplicate_teachers(teacher_values),
        "audiences": _extract_audiences(df),
    }


def _rebuild_aggregates(catalog: dict[str, Any]) -> None:
    aggregate = {"groups": set(), "teachers": {}, "audiences": set()}
    for source in catalog["sources"].values():
        if not isinstance(source, dict):
            continue
        aggregate["groups"].update(_clean_list(source.get("groups")))
        aggregate["audiences"].update(_clean_list(source.get("audiences")))
        for teacher in _clean_list(source.get("teachers")):
            aggregate["teachers"].setdefault(normalize_teacher_name(teacher), teacher)

    catalog["groups"] = sorted(aggregate["groups"], key=_natural_key)
    catalog["teachers"] = sorted(aggregate["teachers"].values(), key=_natural_key)
    catalog["audiences"] = sorted(aggregate["audiences"], key=_natural_key)


def _write_catalog(path: Path, catalog: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_name = temp_file.name
            json.dump(catalog, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def update_schedule_catalog(
    df: pd.DataFrame,
    schedule_date: str,
    schedule_type: str,
    *,
    source_name: str,
    today: dt.date | None = None,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    if schedule_type not in {"groups", "teachers"}:
        raise ValueError(f"Unsupported schedule type: {schedule_type}")

    current_year = current_academic_year(today)
    source_year = academic_year_for_date(schedule_date)
    if source_year != current_year:
        return {
            "updated": False,
            "reason": "stale_academic_year",
            "academic_year": current_year,
        }

    entries = extract_catalog_entries(df)
    catalog_path = Path(path) if path is not None else get_catalog_path()
    lock_path = Path(f"{catalog_path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        catalog = _read_catalog(catalog_path, current_year)
        source_key = f"{schedule_date}:{schedule_type}"
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        catalog["sources"][source_key] = {
            "schedule_date": schedule_date,
            "schedule_type": schedule_type,
            "source_name": os.path.basename(source_name),
            "updated_at": now,
            **entries,
        }
        catalog["updated_at"] = now
        source_dates = [
            source.get("schedule_date", "")
            for source in catalog["sources"].values()
            if isinstance(source, dict) and source.get("schedule_date")
        ]
        catalog["last_schedule_date"] = max(
            source_dates,
            key=lambda value: dt.datetime.strptime(value, "%d.%m.%Y"),
        )
        _rebuild_aggregates(catalog)
        _write_catalog(catalog_path, catalog)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    return {
        "updated": True,
        "academic_year": current_year,
        "groups": len(catalog["groups"]),
        "teachers": len(catalog["teachers"]),
        "audiences": len(catalog["audiences"]),
    }
