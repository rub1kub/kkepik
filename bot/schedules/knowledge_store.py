"""Public, source-backed schedule knowledge; never reads the users database.

Rows are extracted from source DataFrames, not from Telegram presentation text.
Each source file and each parsed revision is immutable. Missing dates are not
treated as holidays, and published lessons are not evidence of attendance.
"""
from __future__ import annotations

import datetime as dt
import copy
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd

from schedules.group_schedule import is_group_name, parse_two_columns
from schedules.pair_times import get_pair_time
from schedules.schedule_catalog import (
    _audiences_from_cell, _canonical_teacher, academic_year_for_date,
)
from schedules.schedule_dates import current_college_date
from schedules.teacher_schedule import normalize_teacher_name

# Reparse identical source bytes after recovering table continuations across
# PDF pages. Existing source/revision records remain immutable.
PARSER_VERSION = "structured-2"
TIMEZONE = "Europe/Moscow"
PUBLIC_BASE_URL = "https://kkepik.rub1kub.ru"
COLLEGE = {
    "id": "kkep", "name": "Краснодарский колледж электронного приборостроения",
    "city": "Краснодар", "aliases": ["ККЭП", "ККЭПик", "Кэпик"],
}
ENTITY_TYPES = {"group", "teacher", "audience"}
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_CATALOG_CACHE: dict[str, tuple[tuple, dict]] = {}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def normalize_date(value: str) -> str:
    """Resolve relative dates exclusively in the college timezone."""
    relative = {"today": 0, "tomorrow": 1, "yesterday": -1}
    if value in relative:
        return (current_college_date() + dt.timedelta(days=relative[value])).isoformat()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
        return dt.date.fromisoformat(value).isoformat()
    if re.fullmatch(r"\d{1,2}[._]\d{1,2}[._]\d{4}", value or ""):
        return dt.datetime.strptime(value.replace("_", "."), "%d.%m.%Y").date().isoformat()
    raise ValueError("Дата должна быть YYYY-MM-DD, today, tomorrow или yesterday")


def bounded_range(date: str | None = None, start: str | None = None,
                  end: str | None = None, *, maximum: int = 31) -> tuple[str, str]:
    if date is not None:
        if start is not None or end is not None:
            raise ValueError("Укажите либо date, либо start и end")
        return normalize_date(date), normalize_date(date)
    if start is None and end is None:
        today = normalize_date("today")
        return today, today
    if start is None or end is None:
        raise ValueError("Нужны оба параметра start и end")
    first, last = normalize_date(start), normalize_date(end)
    count = (dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days + 1
    if count < 1 or count > maximum:
        raise ValueError(f"Диапазон должен содержать от 1 до {maximum} дней")
    return first, last


def _cell(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _key(value: str, kind: str) -> str:
    value = re.sub(r"\s+", " ", value.strip().replace("_", " "))
    value = value.replace("–", "-").replace("—", "-").replace("‑", "-")
    if kind == "teacher":
        value = normalize_teacher_name(value)
    return value.casefold().replace("ё", "е")


def _natural_key(value: str) -> list:
    return [(0, int(p)) if p.isdigit() else (1, p.casefold())
            for p in re.split(r"(\d+)", value) if p]


def extract_lessons(df: pd.DataFrame, date: str) -> tuple[list[str], list[dict]]:
    """Preserve subgroup/teacher/room associations from source columns.

    Both the 18-column PDF layout and the legacy 23-column XLSX layout use
    discipline + room + discipline + room, with a following teacher row.
    A single common lesson is subgroup=None; distinct source subgroup columns
    remain two records even if their subjects happen to be equal.
    """
    if df is None or df.empty:
        raise ValueError("Таблица источника пуста")
    date = normalize_date(date)
    headers = []
    for row in range(df.shape[0]):
        for col in range(df.shape[1]):
            text = _cell(df.iat[row, col]).upper()
            if is_group_name(text):
                headers.append((row, col, text))
    if not headers:
        raise ValueError("В источнике не найдены группы")
    result: list[dict] = []
    pair_rows = 0
    for header_row, col, group in headers:
        # A source can repeat a group header; use its final section like the bot.
        if any(g == group and r > header_row for r, _, g in headers):
            continue
        following = [r for r, c, _ in headers if c == col and r > header_row]
        end_row = min(following, default=df.shape[0])
        row = header_row + 1
        while row < end_row:
            raw_pair = _cell(df.iat[row, 0])
            try:
                pair_value = float(raw_pair)
                if not pair_value.is_integer():
                    row += 1
                    continue
                pair = int(pair_value)
            except (ValueError, OverflowError):
                row += 1
                continue
            if pair < 1 or pair > 12:
                row += 1
                continue
            pair_rows += 1
            subject1, room1 = parse_two_columns(df, row, col)
            teacher1, teacher_room1 = (parse_two_columns(df, row + 1, col)
                                      if row + 1 < end_row else ("", ""))
            subject2, room2 = (parse_two_columns(df, row, col + 2)
                               if col + 3 < df.shape[1] else ("", ""))
            teacher2, teacher_room2 = (parse_two_columns(df, row + 1, col + 2)
                                      if row + 1 < end_row and col + 3 < df.shape[1]
                                      else ("", ""))
            # A shared room is often printed in the second subgroup's column.
            if subject2 and _audiences_from_cell(subject2) and not teacher2:
                room2 = room2 or subject2
                subject2 = ""
            subgroups = [(subject1, teacher1, [room1, teacher_room1], 1),
                         (subject2, teacher2, [room2, teacher_room2], 2)]
            if not subject2 and not teacher2:
                subgroups[0] = (subject1, teacher1,
                                [room1, teacher_room1, room2, teacher_room2], None)
            elif not subject1 and not teacher1:
                subgroups[1] = (subject2, teacher2,
                                [room2, teacher_room2, room1, teacher_room1], 2)
            for subject, teacher, rooms, subgroup in subgroups:
                if not subject.strip() or subject.strip().casefold() in {"нет", "-", "—"}:
                    continue
                audience_values = list(dict.fromkeys(
                    a for room in rooms for a in _audiences_from_cell(room)))
                time_value = get_pair_time(pair, dt.date.fromisoformat(date).strftime("%d.%m.%Y"))
                start_time, end_time = (time_value.split("–") if "–" in time_value else (None, None))
                # Honor explicit source times (including exceptional opening days).
                explicit = _cell(df.iat[row, 2]) if col >= 4 else ""
                match = re.fullmatch(r"(\d{1,2})[.:](\d{2})\s*[-–—]\s*(\d{1,2})[.:](\d{2})", explicit)
                if match and int(match[1]) < 24 and int(match[3]) < 24 and int(match[2]) < 60 and int(match[4]) < 60:
                    start_time, end_time = f"{int(match[1]):02d}:{match[2]}", f"{int(match[3]):02d}:{match[4]}"
                if start_time:
                    start_time = start_time.zfill(5)
                result.append({
                    "group": group, "pair_number": pair, "subject": subject.strip(),
                    "teacher": _canonical_teacher(teacher) if teacher.strip() else None,
                    "audience": ", ".join(audience_values) or None,
                    "audiences": audience_values, "subgroup": subgroup,
                    "start_time": start_time, "end_time": end_time,
                    "association_status": "source_columns", "lesson_status": "scheduled",
                })
            row += 2
    if not pair_rows:
        raise ValueError("В источнике нет строк с номерами пар")
    return sorted({g for _, _, g in headers}, key=_natural_key), result


class KnowledgeStore:
    def __init__(self, data_dir: str | os.PathLike | None = None):
        if data_dir is None:
            import config
            data_dir = config.DATA_DIR
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "knowledge.db"
        self.sources_dir = self.data_dir / "knowledge_sources"

    @contextmanager
    def _connect(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=20000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS sources (
                sha TEXT PRIMARY KEY, extension TEXT NOT NULL, filename TEXT NOT NULL,
                size INTEGER NOT NULL, received_at TEXT NOT NULL, archived_import INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS revisions (
                id INTEGER PRIMARY KEY, date TEXT NOT NULL, source_sha TEXT NOT NULL REFERENCES sources(sha),
                revision INTEGER NOT NULL, parser_version TEXT NOT NULL, status TEXT NOT NULL,
                parsed_at TEXT NOT NULL, academic_year TEXT NOT NULL, error TEXT,
                groups_json TEXT NOT NULL DEFAULT '[]', received_at TEXT, archived_import INTEGER,
                UNIQUE(date, revision)
            );
            CREATE TABLE IF NOT EXISTS lessons (
                id INTEGER PRIMARY KEY, revision_id INTEGER NOT NULL REFERENCES revisions(id),
                group_name TEXT NOT NULL, teacher_key TEXT, audiences_json TEXT NOT NULL,
                pair_number INTEGER NOT NULL, payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS knowledge_revision_date ON revisions(date, revision);
            CREATE INDEX IF NOT EXISTS knowledge_lesson_revision ON lessons(revision_id);
            CREATE INDEX IF NOT EXISTS knowledge_lesson_group ON lessons(group_name, revision_id);
        """)
        # Additive migration stays compatible with an already-running initial
        # archive importer, whose INSERT omits these nullable observation fields.
        revision_columns = {r[1] for r in connection.execute("PRAGMA table_info(revisions)")}
        if not {"received_at", "archived_import"}.issubset(revision_columns):
            connection.execute("BEGIN IMMEDIATE")
            # Recheck after acquiring the write lock: several API workers may
            # discover a legacy schema at the same time.
            revision_columns = {r[1] for r in connection.execute("PRAGMA table_info(revisions)")}
            if "received_at" not in revision_columns:
                connection.execute("ALTER TABLE revisions ADD COLUMN received_at TEXT")
            if "archived_import" not in revision_columns:
                connection.execute("ALTER TABLE revisions ADD COLUMN archived_import INTEGER")
            connection.commit()
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        if schema_version == 0:
            connection.execute("PRAGMA user_version=1")
        elif schema_version != 1:
            connection.close()
            raise RuntimeError("Unsupported knowledge database schema")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def ingest(self, file_path: str | os.PathLike, date: str, df: pd.DataFrame | None,
               *, error: str | None = None, received_at: str | None = None,
               archived_import: bool = False) -> dict:
        """Idempotently snapshot a source and commit its structured revision."""
        date = normalize_date(date)
        source_path = Path(file_path)
        extension = source_path.suffix.lower()
        if extension not in {".pdf", ".xlsx"}:
            raise ValueError("Поддерживаются только PDF и XLSX")
        if source_path.stat().st_size > 100 * 1024 * 1024:
            raise ValueError("Источник превышает 100 МБ")
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        # Hash exactly the bytes being snapshotted, then publish atomically. A
        # crash cannot leave a partially written publicly addressable hash file.
        try:
            with tempfile.NamedTemporaryFile(dir=self.sources_dir, prefix=".snapshot-", delete=False) as target, source_path.open("rb") as original:
                temporary_path = Path(target.name)
                digest = hashlib.sha256()
                for chunk in iter(lambda: original.read(1024 * 1024), b""):
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            sha = digest.hexdigest()
            snapshot = self.sources_dir / f"{sha}{extension}"
            try:
                os.link(temporary_path, snapshot)
            except FileExistsError:
                valid = not snapshot.is_symlink() and snapshot.stat().st_size == temporary_path.stat().st_size
                if valid:
                    existing_digest = hashlib.sha256()
                    with snapshot.open("rb") as existing_file:
                        for chunk in iter(lambda: existing_file.read(1024 * 1024), b""):
                            existing_digest.update(chunk)
                    valid = existing_digest.hexdigest() == sha
                if not valid:
                    # Recover a damaged legacy/partial snapshot using verified
                    # bytes for this exact hash, never an arbitrary file path.
                    os.replace(temporary_path, snapshot)
                    temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        groups, lessons = [], []
        if error is None:
            try:
                groups, lessons = extract_lessons(df, date)
            except Exception as exc:
                error = f"Не удалось разобрать структуру источника: {type(exc).__name__}"
        status = "failed" if error else "parsed"
        observation_time = received_at or utc_now()
        # Error strings are internal only, never reflected in the public API.
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO sources VALUES (?, ?, ?, ?, ?, ?)",
                (sha, extension, source_path.name, snapshot.stat().st_size,
                 observation_time, int(archived_import)))
            if archived_import:
                existing = connection.execute(
                    "SELECT id, revision, status FROM revisions WHERE date=? AND source_sha=? AND parser_version=? ORDER BY revision DESC LIMIT 1",
                    (date, sha, PARSER_VERSION)).fetchone()
            else:
                latest = connection.execute(
                    "SELECT id,source_sha,parser_version,revision,status FROM revisions WHERE date=? ORDER BY revision DESC LIMIT 1",
                    (date,)).fetchone()
                existing = latest if latest and latest["source_sha"] == sha and latest["parser_version"] == PARSER_VERSION else None
            # A transient parse failure must not permanently poison this hash.
            # Recovery is another immutable revision; repeated equal outcomes
            # are idempotent and a later failed attempt never downgrades parsed.
            if existing and not (existing["status"] == "failed" and status == "parsed"):
                return {"created": False, "sha": sha, "revision": existing["revision"], "status": existing["status"]}
            revision = connection.execute(
                "SELECT COALESCE(MAX(revision), 0)+1 FROM revisions WHERE date=?", (date,)).fetchone()[0]
            cursor = connection.execute(
                "INSERT INTO revisions(date, source_sha, revision, parser_version, status, parsed_at, academic_year, error, groups_json, received_at, archived_import) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (date, sha, revision, PARSER_VERSION, status, utc_now(),
                 academic_year_for_date(dt.date.fromisoformat(date)), error,
                 json.dumps(groups, ensure_ascii=False), observation_time, int(archived_import)))
            for lesson in lessons:
                connection.execute(
                    "INSERT INTO lessons(revision_id,group_name,teacher_key,audiences_json,pair_number,payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (cursor.lastrowid, lesson["group"], _key(lesson["teacher"] or "", "teacher"),
                     json.dumps(lesson["audiences"], ensure_ascii=False), lesson["pair_number"],
                     json.dumps(lesson, ensure_ascii=False)))
        return {"created": True, "sha": sha, "revision": revision, "status": status,
                "groups": len(groups), "lessons": len(lessons)}

    def _source(self, row: sqlite3.Row) -> dict:
        archived = bool(row["observation_archived_import"])
        return {"id": row["source_sha"], "revision": row["revision"],
                "received_at": row["observation_received_at"], "published_at": None,
                "parsed_at": row["parsed_at"], "academic_year": row["academic_year"],
                "archived_import": archived,
                "received_at_semantics": "local_file_mtime" if archived else "ingested_at",
                "revision_order": "archive_import_order" if archived else "observation_order",
                "url": f"{PUBLIC_BASE_URL}/api/v2/sources/{row['source_sha']}"}

    def _revisions(self, connection: sqlite3.Connection, date: str) -> list:
        return connection.execute(
            "SELECT r.*,COALESCE(r.received_at,s.received_at) AS observation_received_at,COALESCE(r.archived_import,s.archived_import) AS observation_archived_import FROM revisions r JOIN sources s ON s.sha=r.source_sha WHERE r.date=? ORDER BY r.revision",
            (date,)).fetchall()

    def _lessons(self, connection: sqlite3.Connection, revision_id: int,
                 entity_type: str, entity: str) -> list[dict]:
        rows = connection.execute("SELECT payload_json FROM lessons WHERE revision_id=? ORDER BY pair_number,id", (revision_id,)).fetchall()
        result = []
        for row in rows:
            item = json.loads(row[0])
            if (entity_type == "group" and _key(item["group"], "group") == _key(entity, "group")
                    or entity_type == "teacher" and _key(item["teacher"] or "", "teacher") == _key(entity, "teacher")
                    or entity_type == "audience" and any(_key(a, "audience") == _key(entity, "audience") for a in item["audiences"])):
                result.append(item)
        return result

    def _catalog(self, connection: sqlite3.Connection) -> dict:
        latest = connection.execute("""
            SELECT r.* FROM revisions r JOIN
            (SELECT date,MAX(revision) AS revision FROM revisions WHERE status='parsed' GROUP BY date) p
            ON p.date=r.date AND p.revision=r.revision
        """).fetchall()
        groups, teachers, audiences, group_dates = set(), {}, set(), {}
        for row in latest:
            day_groups = json.loads(row["groups_json"])
            groups.update(day_groups)
            for group in day_groups:
                group_dates.setdefault(group, []).append(row["date"])
            for item in connection.execute("SELECT payload_json FROM lessons WHERE revision_id=?", (row["id"],)):
                lesson = json.loads(item[0])
                if lesson["teacher"]:
                    teachers.setdefault(_key(lesson["teacher"], "teacher"), lesson["teacher"])
                audiences.update(lesson["audiences"])
        all_dates = [r[0] for r in connection.execute("SELECT DISTINCT date FROM revisions ORDER BY date")]
        updated = connection.execute("SELECT MAX(parsed_at) FROM revisions").fetchone()[0]
        return {"college": COLLEGE.copy(), "timezone": TIMEZONE,
                "groups": sorted(groups, key=_natural_key),
                "teachers": sorted(teachers.values(), key=_natural_key),
                "audiences": sorted(audiences, key=_natural_key),
                "dates": all_dates, "updated_at": updated,
                "available_dates": sorted(r["date"] for r in latest),
                "group_dates": {g: sorted(values) for g, values in group_dates.items()},
                "homework": {"supported": False, "reason": "Личный учёт ДЗ не публикуется"}}

    def catalog(self) -> dict:
        def generation():
            stamps = []
            for path in (self.path, Path(str(self.path) + "-wal")):
                try:
                    stat = path.stat()
                    stamps.extend((stat.st_mtime_ns, stat.st_size))
                except FileNotFoundError:
                    stamps.extend((0, 0))
            return tuple(stamps)
        cache_key = str(self.path.resolve())
        stamp = generation()
        cached = _CATALOG_CACHE.get(cache_key)
        if cached and cached[0] == stamp:
            return copy.deepcopy(cached[1])
        with self._connect() as connection:
            value = self._catalog(connection)
        if len(_CATALOG_CACHE) > 20:
            _CATALOG_CACHE.clear()
        # A concurrent importer may commit during this read. Cache against the
        # initial generation so such a commit invalidates this result next time.
        _CATALOG_CACHE[cache_key] = (stamp, value)
        return copy.deepcopy(value)

    def resolve(self, entity_type: str, query: str) -> dict:
        self._validate_entity(entity_type, query)
        values = self.catalog()[{"group": "groups", "teacher": "teachers", "audience": "audiences"}[entity_type]]
        normalized = _key(query, entity_type)
        exact = [v for v in values if _key(v, entity_type) == normalized]
        matches = exact or [v for v in values if normalized in _key(v, entity_type)]
        return {"entity_type": entity_type, "query": query, "matches": matches[:100],
                "ambiguous": len(matches) > 1, "truncated": len(matches) > 100}

    @staticmethod
    def _validate_entity(entity_type: str, entity: str) -> None:
        if entity_type not in ENTITY_TYPES:
            raise ValueError("entity_type должен быть group, teacher или audience")
        if not isinstance(entity, str) or not entity.strip() or len(entity) > 150:
            raise ValueError("entity должен содержать от 1 до 150 символов")

    def _day(self, connection: sqlite3.Connection, date: str,
             entity_type: str | None, entity: str | None) -> dict:
        revisions = self._revisions(connection, date)
        day = {"date": date, "status": "missing", "lessons": [], "source": None}
        if not revisions:
            return day
        parsed = [r for r in revisions if r["status"] == "parsed"]
        if not parsed:
            day.update(status="invalid_source", source=self._source(revisions[-1]))
            return day
        row = parsed[-1]
        day["source"] = self._source(row)
        if revisions[-1]["status"] != "parsed":
            day["warning"] = "Новая редакция не разобрана; показана предыдущая корректная редакция"
        if entity_type is None:
            day["status"] = "available"
            return day
        groups = json.loads(row["groups_json"])
        day["lessons"] = self._lessons(connection, row["id"], entity_type, entity)
        if day["lessons"]:
            day["status"] = "available"
        elif entity_type == "group":
            day["status"] = "no_lessons" if any(_key(g, "group") == _key(entity, "group") for g in groups) else "entity_not_found"
        else:
            day["status"] = "entity_not_found"
            day["note"] = "В расписании групп не найдено занятий для этого преподавателя/аудитории; это не подтверждение свободного дня"
        return day

    def schedule(self, entity_type: str, entity: str, *, date: str | None = None,
                 start: str | None = None, end: str | None = None) -> dict:
        self._validate_entity(entity_type, entity)
        first, last = bounded_range(date, start, end)
        dates = [dt.date.fromisoformat(first) + dt.timedelta(days=i)
                 for i in range((dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days + 1)]
        with self._connect() as connection:
            days = [self._day(connection, d.isoformat(), entity_type, entity) for d in dates]
        return {"college": COLLEGE.copy(), "timezone": TIMEZONE, "entity_type": entity_type,
                "entity": entity, "range": {"start": first, "end": last}, "days": days,
                "coverage": self._summary(days), "lesson_status": "scheduled",
                "note": "Опубликованное расписание, не подтверждение фактически проведённых занятий"}

    @staticmethod
    def _summary(days: list[dict]) -> dict:
        counts = Counter(day["status"] for day in days)
        return {"total_days": len(days), "available": counts["available"],
                "no_lessons": counts["no_lessons"], "missing": counts["missing"],
                "invalid_source": counts["invalid_source"], "entity_not_found": counts["entity_not_found"],
                "complete": not any(counts[s] for s in ("missing", "invalid_source", "entity_not_found"))}

    def coverage(self, *, entity_type: str | None = None, entity: str | None = None,
                 start: str | None = None, end: str | None = None) -> dict:
        if (entity_type is None) != (entity is None):
            raise ValueError("Нужны оба параметра entity_type и entity")
        if entity_type is not None:
            self._validate_entity(entity_type, entity)
        with self._connect() as connection:
            dates = [r[0] for r in connection.execute("SELECT DISTINCT date FROM revisions ORDER BY date")]
            if start is not None or end is not None:
                first, last = bounded_range(start=start, end=end, maximum=366)
                selection = [d for d in dates if first <= d <= last]
                calendar = [dt.date.fromisoformat(first) + dt.timedelta(days=i)
                            for i in range((dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days + 1)]
                days = [self._day(connection, d.isoformat(), entity_type, entity) for d in calendar]
            else:
                first, last = (dates[0], dates[-1]) if dates else (None, None)
                selection = dates
                days = [self._day(connection, d, entity_type, entity) for d in dates]
        return {"college": COLLEGE.copy(), "timezone": TIMEZONE,
                "entity_type": entity_type, "entity": entity,
                "range": {"start": first, "end": last}, "dates": selection,
                "days": [{"date": d["date"], "status": d["status"]} for d in days],
                "available_dates": [d["date"] for d in days if d["status"] in {"available", "no_lessons"}],
                "missing_dates": [d["date"] for d in days if d["status"] == "missing"],
                "summary": self._summary(days), "range_is_calendar": start is not None,
                "history_complete": False,
                "note": "Архив содержит только сохранившиеся источники; прежние редакции могли быть утрачены"}

    def changes(self, entity_type: str, entity: str, date: str) -> dict:
        self._validate_entity(entity_type, entity)
        date = normalize_date(date)
        with self._connect() as connection:
            revisions = [{"revision": r["revision"], "status": r["status"],
                          "source": self._source(r),
                          "lessons": self._lessons(connection, r["id"], entity_type, entity)}
                         for r in self._revisions(connection, date)]
        parsed = [r for r in revisions if r["status"] == "parsed"]
        changes = []
        for before, after in zip(parsed, parsed[1:]):
            old = {json.dumps(x, ensure_ascii=False, sort_keys=True): x for x in before["lessons"]}
            new = {json.dumps(x, ensure_ascii=False, sort_keys=True): x for x in after["lessons"]}
            changes.append({"from_revision": before["revision"], "to_revision": after["revision"],
                            "added": [new[k] for k in new.keys() - old.keys()],
                            "removed": [old[k] for k in old.keys() - new.keys()]})
        return {"college": COLLEGE.copy(), "timezone": TIMEZONE, "date": date,
                "entity_type": entity_type, "entity": entity, "revisions": revisions,
                "changes": changes, "history_complete": False,
                "order_basis": "observation_order_not_publication",
                "note": "Показаны только сохранённые редакции в порядке наблюдения/импорта, не подтверждённого опубликования; отсутствие изменений не доказывает, что их не было"}

    def source_path(self, sha: str) -> tuple[Path, str] | None:
        if not HASH_RE.fullmatch(sha):
            return None
        with self._connect() as connection:
            row = connection.execute("SELECT extension FROM sources WHERE sha=?", (sha,)).fetchone()
        if not row or row[0] not in {".pdf", ".xlsx"}:
            return None
        path = self.sources_dir / f"{sha}{row[0]}"
        if not path.is_file() or path.is_symlink():
            return None
        mime = "application/pdf" if row[0] == ".pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return path, mime
