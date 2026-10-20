"""One renderer for previews and personal schedule delivery."""
from html import escape, unescape
import re

from schedule_preferences import SchedulePreferences
from schedules.pair_times import get_pair_time
from schedules.group_schedule import is_group_name
from schedules.schedule_formatter import _split_lesson, get_group_course
from schedules.schedule_mood import get_mood_emoji


def _text(value):
    return escape(unescape(re.sub(r"</?(?:b|i|strong|em)>", "", str(value))), quote=False)


def personal_subject_aliases(role, group):
    import config
    if role == config.ROLE_STUDENT and group.upper() == "103-Д9-3ИНС":
        return {"Тестирование информационных систем": "Торнадо 🌪🌪🌪"}
    return None


def _split_teacher_lesson(line):
    """A group contains digits too; never let the room heuristic consume it."""
    match = re.match(r"^▪️\s*(\d+)\s*пара\s*[–—-]\s*(.*)$", line.strip())
    if not match:
        return None
    parts = re.split(r"\s+[–—]\s+", match[2])
    if parts[0].casefold() == "нет":
        return int(match[1]), "Нет", "", ""
    if not is_group_name(re.sub(r"</?b>", "", parts[0]).strip()):
        return _split_lesson(line)
    group = parts[0]
    subject = parts[1] if len(parts) > 1 else "Занятие"
    room = re.sub(r"^ауд\.?\s*", "", " – ".join(parts[2:]), flags=re.I) if len(parts) > 2 else ""
    return int(match[1]), subject, f"Группа {group}", room


def render_schedule_message(lines, entity, date, *, preferences=None, show_group=True,
                            subject_aliases=None, teacher=False):
    p = preferences or SchedulePreferences()
    if isinstance(p, dict):
        p = SchedulePreferences.model_validate(p)
    parse = _split_teacher_lesson if teacher else _split_lesson
    raw_lines = [line for item in lines for line in item.splitlines() if line.strip()]
    parsed = [parse(line) for line in raw_lines]
    active = [lesson for lesson in parsed if lesson and lesson[1].casefold() != "нет"]
    times = [get_pair_time(pair, date) for pair in sorted({lesson[0] for lesson in active})]
    start = times[0].split("–")[0] if times and times[0] else ""
    end = times[-1].split("–")[-1] if times and times[-1] else ""
    common = {"emoji": get_mood_emoji(lines) if p.show_emoji else "", "date": f"<b>{_text(date)}</b>",
              "group": _text(entity), "start": _text(start), "end": _text(end)}
    first_course = get_group_course(entity) == 1 and not teacher
    lessons = []
    for raw_line, lesson in zip(raw_lines, parsed):
        if lesson is None:
            lessons.append(_text(raw_line))
            continue
        pair, subject, instructor, room = lesson
        empty = subject.casefold() == "нет"
        if empty and not p.show_free_pairs:
            continue
        subject = (subject_aliases or {}).get(subject, subject)
        pair_text = f"{pair}." if p.pair_prefix == "number" else f"{pair} пара"
        if p.pair_prefix == "bullet":
            pair_text = "▪️" + pair_text
        time_text = get_pair_time(pair, date) if p.show_times else ""
        values = {**common, "pair": f"<b>{_text(pair_text)}</b>",
                  "time": (f"<b>{time_text}</b>" if p.emphasis == "time" else f"<i>{time_text}</i>") if time_text else "",
                  "subject": _text(subject), "teacher": _text(instructor) if p.show_teachers and not empty else "",
                  "room": _text(room) if p.show_rooms and not empty else ""}
        if not empty and (p.emphasis == "subject" or (p.emphasis == "auto" and first_course)):
            values["subject"] = f"<b>{values['subject']}</b>"
        if values["room"]:
            label = {"full": "аудитория ", "short": "ауд. ", "none": ""}[p.room_label]
            if p.emphasis == "auto" and first_course:
                values["room"] = label + f"<b>{values['room']}</b>"
            else:
                values["room"] = label + values["room"]
        if p.lesson_template:
            rendered = escape(p.lesson_template, quote=False).format_map(values).strip()
        else:
            rows = []
            for fields in p.lesson_rows:
                selected = [(field, values[field]) for field in fields if values[field]]
                if not selected:
                    continue
                row = selected[0][1]
                for field, value in selected[1:]:
                    row += (" – " if field == "subject" else " · ") + value
                if p.emphasis == "auto" and not first_course and all(field in ("teacher", "room") for field, _ in selected):
                    row = f"<b>{row}</b>"
                rows.append(row)
            rendered = "\n   ".join(rows)
        lessons.append(rendered)
    separator = {"spacious": "\n\n", "compact": "\n", "line": "\n────────────\n"}[p.spacing]
    group_visible = p.show_group == "always" or (p.show_group == "auto" and show_group)
    blocks = {
        "header": escape(p.header_template, quote=False).format_map(common).strip(),
        "group": f"{'Преподаватель' if teacher else 'Группа'} <b>{_text(entity)}</b>:" if group_visible else "",
        "bounds": f"Начало занятий: <b>{start}</b> · до <b>{end}</b>" if p.show_bounds and start else "",
        "lessons": separator.join(lessons) or "Занятий нет.",
        "footer": escape(p.footer_text, quote=False).strip(),
    }
    return "\n\n".join(blocks[block] for block in p.blocks if blocks[block])
