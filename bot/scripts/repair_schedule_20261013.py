"""Repair only proven 13 October deliveries, never send a new user message.

Preparation imports the PDF stack; application intentionally uses stdlib only.
Run --prepare once after the parser fix, inspect its summary, then --apply.
Without either flag this is a read-only dry run. Private plans and durable edit
journals allow safe resumption: editing one fixed message is idempotent.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
from html import unescape
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
USERS = ROOT / "data/database.db"
REGISTRY = ROOT / "data/message_registry.db"
SOURCE = ROOT / "data/Расписание на 13.10.2026_ГРУППЫ.pdf"
SOURCE_SHA = "3f7970f6030289cac3c40d177aaf2aece6ebc48e6bfd9aa3110eac74455e1f30"
DATE = "13.10.2026"
AUDIT = ROOT / "audits/schedule-repair-20261013"
PLAN = AUDIT / "plan.json"
JOURNAL = AUDIT / "edits.jsonl"
GROUPS = frozenset({"112-Д9-3РЭС", "113-КД9-3КСК", "114-КД9-3КСК",
                    "116-КД9-3ССА", "146-Д9-1РУПО", "147-Д9-1БТС",
                    "148-Д9-1ИНБ", "149-Д9-1РЭС"})
STUDENT = "Я студент"
TEACHER = "Я преподаватель"


def digest(data):
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def plain(html):
    return unescape(re.sub(r"<[^>]+>", "", html))


def visible_length(html):
    return len(plain(html).encode("utf-16-le")) // 2


def ro(path):
    conn = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def candidate_rows(registry=REGISTRY):
    with ro(registry) as conn:
        return [dict(row) for row in conn.execute("""
            SELECT * FROM message_registry WHERE platform='telegram'
              AND direction='outgoing' AND schedule_date=? AND schedule_type='groups'
              AND message_kind IN ('schedule','schedule_missing_group')
            ORDER BY id
        """, (DATE,))]


def user_snapshot(uid, database=USERS):
    with ro(database) as conn:
        row = conn.execute("SELECT role,name_or_group,is_class_teacher,class_group FROM users WHERE user_id=?", (uid,)).fetchone()
        if not row:
            return None
        tracked = [list(item) for item in conn.execute("SELECT group_name,slot FROM tracked_groups WHERE user_id=? ORDER BY slot", (uid,))]
        pref = conn.execute("SELECT preferences,updated_at FROM schedule_preferences WHERE user_id=?", (uid,)).fetchone()
    return {"user": dict(row), "tracked": tracked, "preferences": dict(pref) if pref else None}


def tracked_groups(snapshot):
    result = [group.upper() for group, _ in snapshot["tracked"]]
    if result:
        return result
    user = snapshot["user"]
    if user["role"] == STUDENT and user["name_or_group"]:
        return [user["name_or_group"].upper()]
    if user["role"] == TEACHER and user["is_class_teacher"] and user["class_group"]:
        return [user["class_group"].upper()]
    return []


def allowed_entity(row, snapshot):
    if not snapshot:
        return False
    if row["entity"] in GROUPS:
        return row["entity"] in tracked_groups(snapshot)
    return (row["message_kind"] == "schedule" and snapshot["user"]["role"] == TEACHER
            and row["entity"] == snapshot["user"]["name_or_group"])


def validate_row(row):
    if not (row["platform"] == "telegram" and row["direction"] == "outgoing"
            and row["schedule_date"] == DATE and row["schedule_type"] == "groups"
            and row["message_kind"] in {"schedule", "schedule_missing_group"}
            and row["telegram_method"] in {"SendPhoto", "SendMessage"}
            and str(row["chat_id"]).isdigit() and int(row["chat_id"]) > 0
            and str(row["message_id"]).isdigit() and int(row["message_id"]) > 0
            and isinstance(row["content_preview"], str)
            and digest(row["content_preview"]) == row["content_hash"]):
        raise ValueError("Unsafe or truncated registry row")


def check_source():
    if digest(SOURCE.read_bytes()) != SOURCE_SHA:
        raise ValueError("Source bytes differ from the verified incident source")


def durable_file(path, data):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    with os.fdopen(os.open(path, flags, 0o600), "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def durable_event(event):
    event = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **event}
    with os.fdopen(os.open(JOURNAL, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def prepare():
    """Heavy, explicit, local-only stage. Never connects to Telegram."""
    check_source()
    if PLAN.exists():
        raise ValueError("A stable plan already exists; inspect or apply it, do not overwrite")
    sys.path.insert(0, str(ROOT))
    from schedules.pdf_to_df import pdf_to_dataframe
    from schedules.pdf_crop import crop_group_screenshots
    from schedules.group_schedule import get_schedule_for_group
    from schedules.teacher_schedule import get_schedule_for_teacher
    from schedules.message_constructor import render_schedule_message
    from schedules.schedule_formatter import fit_photo_caption
    from schedule_preferences import SchedulePreferences

    frame = pdf_to_dataframe(str(SOURCE))
    crops = crop_group_screenshots(str(SOURCE))
    if frame is None or not GROUPS.issubset(crops):
        raise ValueError("Corrected parser/crop output does not contain every proven affected group")
    AUDIT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(AUDIT, 0o700)
    targets, skipped = [], Counter()
    for row in candidate_rows():
        snapshot = user_snapshot(int(row["chat_id"])) if str(row["chat_id"]).isdigit() else None
        if not allowed_entity(row, snapshot):
            skipped["unaffected_or_unsubscribed"] += 1
            continue
        validate_row(row)
        pref = snapshot["preferences"]
        if pref and pref["updated_at"] > row["created_at"]:
            skipped["preferences_changed_after_delivery"] += 1
            continue
        preferences = SchedulePreferences.model_validate(json.loads(pref["preferences"])) if pref else SchedulePreferences()
        is_group = row["entity"] in GROUPS
        if is_group:
            lines = get_schedule_for_group(frame, row["entity"])
            if lines is None:
                raise ValueError("Proven affected group still absent after parsing")
            lines = lines or [f"▪️{pair} пара – Нет" for pair in range(1, 5)]
            show_group = (row["message_kind"] == "schedule_missing_group"
                          or f"Группа {row['entity']}:" in row["content_preview"])
        else:
            lines = get_schedule_for_teacher(frame, row["entity"])
            if not lines:
                skipped["teacher_no_corrected_lessons"] += 1
                continue
            show_group = True
        html = render_schedule_message(lines, row["entity"], DATE, preferences=preferences,
                                       show_group=show_group, teacher=not is_group)
        if row["telegram_method"] == "SendPhoto":
            if not is_group:
                raise ValueError("Unexpected teacher photo")
            html = fit_photo_caption(html)
            if visible_length(html) > 1024:
                skipped["caption_exceeds_limit"] += 1
                continue
            method = "editMessageMedia"
            media = crops[row["entity"]]
            name = digest(row["entity"])[:16] + ".png"
            image = AUDIT / name
            if not image.exists():
                durable_file(image, media)
            elif digest(image.read_bytes()) != digest(media):
                raise ValueError("Prepared crop changed unexpectedly")
            image_info = {"file": name, "sha": digest(media)}
        else:
            method, image_info = "editMessageText", None
            if visible_length(html) > 4096:
                skipped["text_exceeds_limit"] += 1
                continue
            if row["message_kind"] == "schedule" and plain(html) == row["content_preview"]:
                skipped["already_correct_text"] += 1
                continue
        targets.append({"row": row, "snapshot": snapshot, "method": method,
                        "html": html, "plain": plain(html), "new_hash": digest(plain(html)),
                        "image": image_info, "new_kind": "schedule"})
    payload = {"date": DATE, "source_sha": SOURCE_SHA, "version": 1,
               "prepared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "targets": targets, "skipped": dict(skipped)}
    durable_file(PLAN, json.dumps(payload, ensure_ascii=False, indent=2).encode())
    summary(payload, "prepared")


def load_plan():
    payload = json.loads(PLAN.read_text())
    if payload.get("date") != DATE or payload.get("source_sha") != SOURCE_SHA or payload.get("version") != 1:
        raise ValueError("Invalid campaign plan")
    seen = set()
    for target in payload["targets"]:
        row = target["row"]
        validate_row(row)
        key = (row["chat_id"], row["message_id"])
        if key in seen:
            raise ValueError("Duplicate target message")
        seen.add(key)
        if not allowed_entity(row, target["snapshot"]):
            raise ValueError("Unsafe target entity")
        expected_method = "editMessageMedia" if row["telegram_method"] == "SendPhoto" else "editMessageText"
        if (target["method"] != expected_method or target["plain"] != plain(target["html"])
                or digest(target["plain"]) != target["new_hash"] or target["new_kind"] != "schedule"
                or visible_length(target["html"]) > (1024 if target["image"] else 4096)):
            raise ValueError("Unsafe prepared payload")
        if target["image"]:
            filename = target["image"]["file"]
            if not re.fullmatch(r"[a-f0-9]{16}\.png", filename) or digest((AUDIT / filename).read_bytes()) != target["image"]["sha"]:
                raise ValueError("Unsafe or changed prepared crop")
    return payload


def summary(payload, stage, **extra):
    targets = payload["targets"]
    print(json.dumps({"stage": stage, "date": DATE, "messages": len(targets),
                      "recipients": len({t["row"]["chat_id"] for t in targets}),
                      "methods": dict(Counter(t["method"] for t in targets)),
                      "false_missing_warnings": sum(t["row"]["message_kind"] == "schedule_missing_group" for t in targets),
                      "skipped": payload["skipped"], **extra}, ensure_ascii=False), flush=True)


def keyboard():
    base = "https://kkepik.rub1kub.ru/"
    return {"inline_keyboard": [[{"text": "Открыть", "style": "primary", "web_app": {"url": base}},
                                  {"text": "Скачать PDF", "url": base + "api/schedule/download/groups/" + DATE}]]}


class ApiFailure(Exception):
    def __init__(self, code, description="", retry_after=0):
        self.code, self.description, self.retry_after = code, description, retry_after


def api_call(token, method, values, image=None):
    if method not in {"getMe", "editMessageMedia", "editMessageText"}:
        raise ValueError("This repair cannot send new messages")
    if image is None:
        body = json.dumps(values, ensure_ascii=False).encode()
        content_type = "application/json"
    else:
        boundary = "kkepik-" + uuid.uuid4().hex
        pieces = []
        for key, value in values.items():
            data = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
            pieces.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{data}\r\n").encode())
        pieces.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"schedule.png\"\r\nContent-Type: image/png\r\n\r\n").encode() + image + b"\r\n")
        pieces.append(f"--{boundary}--\r\n".encode())
        body, content_type = b"".join(pieces), "multipart/form-data; boundary=" + boundary
    request = urllib.request.Request("https://api.telegram.org/bot" + token + "/" + method,
                                     data=body, headers={"Content-Type": content_type}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            raw = response.read(2 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        raw = exc.read(2 * 1024 * 1024)
        try:
            error = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ApiFailure(exc.code) from None
        raise ApiFailure(error.get("error_code", exc.code), error.get("description", ""),
                         error.get("parameters", {}).get("retry_after", 0)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ApiFailure(0) from None
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ApiFailure(0) from None
    if not result.get("ok"):
        raise ApiFailure(result.get("error_code", 0), result.get("description", ""),
                         result.get("parameters", {}).get("retry_after", 0))
    return result["result"]


def token_from_env():
    token = os.getenv("BOT_TOKEN", "")
    if not token:
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("BOT_TOKEN="):
                token = line.split("=", 1)[1].strip().strip("\"'")
                break
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
        raise ValueError("Missing or invalid bot credentials")
    return token


def current_registry_row(row, registry=REGISTRY):
    with ro(registry) as conn:
        result = conn.execute("SELECT * FROM message_registry WHERE id=?", (row["id"],)).fetchone()
    return dict(result) if result else None


def guard_target(target):
    row = target["row"]
    if user_snapshot(int(row["chat_id"])) != target["snapshot"]:
        return "recipient_changed"
    current = current_registry_row(row)
    if not current:
        return "registry_changed"
    keys = ("platform", "direction", "chat_id", "message_id", "schedule_date", "schedule_type", "entity", "telegram_method")
    if any(current[key] != row[key] for key in keys):
        return "registry_changed"
    if current["content_hash"] == target["new_hash"] and current["message_kind"] == "schedule":
        # Photos can have the same caption before and after: only a journaled
        # successful media edit proves their corrected image was delivered.
        if target["method"] == "editMessageText":
            return "already_updated"
    if current["content_hash"] != row["content_hash"] or current["message_kind"] != row["message_kind"]:
        return "registry_changed"
    return None


def update_registry(target, registry=REGISTRY):
    row = target["row"]
    with sqlite3.connect(registry, timeout=10) as conn:
        changed = conn.execute("""UPDATE message_registry SET content_preview=?,content_hash=?,
            message_kind='schedule',updated_at=CURRENT_TIMESTAMP WHERE id=? AND platform='telegram'
            AND direction='outgoing' AND chat_id=? AND message_id=? AND schedule_date=?
            AND schedule_type='groups' AND entity=? AND content_hash=? AND message_kind=?""",
            (target["plain"][:1000], target["new_hash"], row["id"], row["chat_id"], row["message_id"], DATE,
             row["entity"], row["content_hash"], row["message_kind"])).rowcount
    if changed != 1:
        current = current_registry_row(row, registry)
        if not current or current["content_hash"] != target["new_hash"] or current["message_kind"] != "schedule":
            raise ValueError("Registry compare-and-swap failed after editing")


def previous_events():
    if not JOURNAL.exists():
        return {}
    events = {}
    for line in JOURNAL.read_text().splitlines():
        event = json.loads(line)
        events[event["registry_id"]] = event
    return events


def completion_report(payload, counts, finished):
    failures = {name: counts[name] for name in ("recipient_changed", "registry_changed", "retry_exhausted", "unavailable")}
    preparation_blocks = {name: payload["skipped"].get(name, 0) for name in (
        "preferences_changed_after_delivery", "teacher_no_corrected_lessons", "caption_exceeds_limit", "text_exceeds_limit")}
    complete = finished and not any(failures.values()) and not any(preparation_blocks.values())
    return {"date": DATE, "source_sha": SOURCE_SHA, "finished": finished, "complete": complete,
            "messages": len(payload["targets"]), "results": dict(counts),
            "guard_report": failures, "preparation_blocks": preparation_blocks,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def persist_report(report):
    temporary = AUDIT / ("summary-" + uuid.uuid4().hex + ".tmp")
    durable_file(temporary, json.dumps(report, ensure_ascii=False, indent=2).encode())
    os.replace(temporary, AUDIT / "summary.json")
    directory_fd = os.open(AUDIT, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def apply(payload):
    check_source()
    lock_fd = os.open(AUDIT / "apply.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_fd)
        raise ValueError("Another repair worker is active") from None
    counts, finished = Counter(), False
    try:
        persist_report(completion_report(payload, counts, False))
        token = token_from_env()
        me = api_call(token, "getMe", {})
        if me.get("username") != "kkepik_bot" or not me.get("is_bot"):
            raise ValueError("Unexpected bot identity")
        previous, last_progress = previous_events(), time.monotonic()
        summary(payload, "applying")
        for target in payload["targets"]:
            row = target["row"]
            old_event = previous.get(row["id"], {})
            if old_event.get("status") in {"edited", "not_modified"}:
                update_registry(target)
                counts["previously_edited"] += 1
                continue
            if old_event.get("status") in {"unavailable", "recipient_changed", "registry_changed"}:
                counts[old_event["status"]] += 1
                continue
            failure = guard_target(target)
            if failure:
                durable_event({"registry_id": row["id"], "status": failure})
                counts[failure] += 1
                continue
            values = {"chat_id": int(row["chat_id"]), "message_id": int(row["message_id"]), "reply_markup": keyboard()}
            if target["method"] == "editMessageMedia":
                values["media"] = {"type": "photo", "media": "attach://image", "caption": target["html"], "parse_mode": "HTML"}
                image = (AUDIT / target["image"]["file"]).read_bytes()
            else:
                values.update(text=target["html"], parse_mode="HTML")
                image = None
            for attempt in range(5):
                failure = guard_target(target)
                if failure:
                    status = failure
                    break
                durable_event({"registry_id": row["id"], "status": "attempting", "attempt": attempt + 1})
                try:
                    result = api_call(token, target["method"], values, image)
                    if (not isinstance(result, dict) or result.get("message_id") != int(row["message_id"])
                            or result.get("chat", {}).get("id") != int(row["chat_id"])
                            or (result.get("caption") if image else result.get("text")) != target["plain"]):
                        durable_event({"registry_id": row["id"], "status": "response_mismatch"})
                        raise ValueError("Unexpected Telegram edit response")
                    status = "edited"
                    break
                except ApiFailure as exc:
                    lower = exc.description.lower()
                    if exc.code == 400 and "message is not modified" in lower:
                        status = "not_modified"
                        break
                    if exc.code in {400, 403, 404} and any(word in lower for word in
                            ("message to edit not found", "message can't be edited", "chat not found", "bot was blocked", "user is deactivated", "not enough rights", "forbidden")):
                        status = "unavailable"
                        break
                    if exc.code == 429 or exc.code == 0 or exc.code >= 500:
                        wait = max(1, int(exc.retry_after) + 1) if exc.code == 429 else 2 * (attempt + 1)
                        durable_event({"registry_id": row["id"], "status": "retryable", "http_status": exc.code})
                        while wait > 0:
                            chunk = min(wait, 20)
                            time.sleep(chunk)
                            wait -= chunk
                            if time.monotonic() - last_progress >= 20:
                                print(json.dumps({"stage": "progress", "counts": dict(counts), "waiting": True}), flush=True)
                                last_progress = time.monotonic()
                        continue
                    durable_event({"registry_id": row["id"], "status": "fatal_api_error", "http_status": exc.code})
                    raise ValueError("Telegram rejected the edit; repair halted safely") from None
            else:
                status = "retry_exhausted"
            durable_event({"registry_id": row["id"], "status": status})
            if status in {"edited", "not_modified"}:
                update_registry(target)
            counts[status] += 1
            time.sleep(0.1)
            if time.monotonic() - last_progress >= 20:
                persist_report(completion_report(payload, counts, False))
                print(json.dumps({"stage": "progress", "counts": dict(counts)}), flush=True)
                last_progress = time.monotonic()
        finished = True
        report = completion_report(payload, counts, finished)
        summary(payload, "complete", results=dict(counts), complete=report["complete"],
                guard_report=report["guard_report"], preparation_blocks=report["preparation_blocks"])
    finally:
        try:
            persist_report(completion_report(payload, counts, finished))
        finally:
            os.close(lock_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.prepare:
            prepare()
        elif PLAN.exists():
            payload = load_plan()
            apply(payload) if args.apply else summary(payload, "dry_run")
        elif args.apply:
            raise ValueError("Prepare and inspect the stable plan first")
        else:
            rows = candidate_rows()
            print(json.dumps({"stage": "dry_run", "date": DATE, "prepared": False,
                              "existing_deliveries": len(rows), "no_network_or_writes": True}), flush=True)
        return 0
    except (ValueError, OSError, sqlite3.Error, ApiFailure) as exc:
        # Never expose request URLs, bot tokens, user IDs or remote error text.
        print(json.dumps({"stage": "halted", "error_type": type(exc).__name__}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
