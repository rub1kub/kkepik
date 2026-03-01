from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import sqlite3
import config
import pandas as pd
from schedules import group_schedule, teacher_schedule, parser_all, audience_schedule
import global_schedules
from typing import Optional, List, Dict
import os
from pydantic import BaseModel
import json
from datetime import datetime

app = FastAPI(title="KKEPIK Bot API")

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _load_cache_on_startup():
    """Загружает последние файлы расписания в кэш при старте API."""
    global_schedules.reload_cache()


# ──────────────────────────────────────────────────────────────────────────────
# Модели данных
# ──────────────────────────────────────────────────────────────────────────────

class ScheduleRequest(BaseModel):
    date: Optional[str] = None

class GroupScheduleRequest(ScheduleRequest):
    group: str

class TeacherScheduleRequest(ScheduleRequest):
    teacher: str

class AudienceScheduleRequest(ScheduleRequest):
    audience: str

class FullScheduleRequest(BaseModel):
    date: Optional[str] = None
    schedule_type: str = "groups"  # "groups" или "teachers"


# ──────────────────────────────────────────────────────────────────────────────
# Единая функция загрузки DataFrame из файла расписания
# Поддерживает .xlsx и .pdf без изменения вызывающего кода.
# ──────────────────────────────────────────────────────────────────────────────

def load_schedule_df(
    data_dir: str,
    date: str,
    schedule_type: str,  # "groups" или "teachers"
) -> Optional[pd.DataFrame]:
    """
    Ищет файл расписания в data_dir и возвращает pandas DataFrame.

    Порядок поиска: сначала .xlsx, затем .pdf (в порядке перебора директории).
    Поддерживаемые форматы имени файла:
        dd.mm.yyyy  или  dd_mm_yyyy
    плюс обязательное ключевое слово «ГРУППЫ» / «ПРЕПОДАВАТЕЛИ» в имени.

    Возвращает None если файл не найден или не читается.
    """
    file_date   = date.replace(".", "_")
    type_kw     = "ГРУППЫ" if schedule_type == "groups" else "ПРЕПОДАВАТЕЛИ"

    for filename in os.listdir(data_dir):
        if filename.startswith("~$"):
            continue

        ext = os.path.splitext(filename)[1].lower()
        if ext not in (".xlsx", ".pdf"):
            continue

        if (date not in filename and file_date not in filename):
            continue

        if type_kw not in filename.upper():
            continue

        file_path = os.path.join(data_dir, filename)
        try:
            if ext == ".pdf":
                from schedules.pdf_to_df import pdf_to_dataframe
                df = pdf_to_dataframe(file_path)
                # pdf_to_dataframe уже делает ffill внутри себя
            else:
                df = pd.read_excel(file_path, sheet_name=date, header=None)
                if df is not None and 0 in df.columns:
                    df[0] = df[0].ffill()

            if df is not None:
                return df

        except Exception as e:
            print(f"[load_schedule_df] Ошибка при чтении {filename}: {e}")
            continue

    return None


def get_today_schedule(schedule_type: str) -> tuple[Optional[pd.DataFrame], Optional[str]]:
    """Загружает расписание на сегодня (.xlsx или .pdf)."""
    today = datetime.now().strftime("%d.%m.%Y")
    df = load_schedule_df(config.DATA_DIR, today, schedule_type)
    return (df, today) if df is not None else (None, None)


def get_latest_schedule(schedule_type: str) -> tuple[Optional[pd.DataFrame], Optional[str]]:
    """Загружает последнее доступное расписание (по mtime файла)."""
    from schedules.parser_all import _find_latest_schedule_file, _load_df_from_file
    fpath = _find_latest_schedule_file(schedule_type)
    if not fpath:
        return None, None
    df = _load_df_from_file(fpath)
    if df is None:
        return None, None
    ext = os.path.splitext(fpath)[1].lower()
    if ext == ".pdf":
        from schedules.pdf_to_df import extract_date_from_pdf_content
        date_str = extract_date_from_pdf_content(fpath)
    else:
        import re as _re
        m = _re.search(r'(\d{1,2}[._]\d{1,2}[._]\d{4})', fpath)
        date_str = m.group(1).replace('_', '.') if m else None
    return df, date_str


# ──────────────────────────────────────────────────────────────────────────────
# Вспомогательная функция: найти путь к файлу расписания для скачивания
# ──────────────────────────────────────────────────────────────────────────────

def find_schedule_file(
    data_dir: str,
    date: str,
    schedule_type: str,
) -> Optional[tuple[str, str]]:
    """
    Возвращает (полный_путь, расширение) найденного файла или None.
    Порядок предпочтений: .xlsx, затем .pdf.
    """
    file_date = date.replace(".", "_")
    type_kw   = "ГРУППЫ" if schedule_type == "groups" else "ПРЕПОДАВАТЕЛИ"

    candidates = []
    for filename in os.listdir(data_dir):
        if filename.startswith("~$"):
            continue
        ext = os.path.splitext(filename)[1].lower()
        if ext not in (".xlsx", ".pdf"):
            continue
        if (date in filename or file_date in filename) and type_kw in filename.upper():
            candidates.append((filename, ext))

    # Предпочитаем .xlsx (для обратной совместимости), затем .pdf
    for ext_pref in (".xlsx", ".pdf"):
        for filename, ext in candidates:
            if ext == ext_pref:
                return os.path.join(data_dir, filename), ext

    return None


# ──────────────────────────────────────────────────────────────────────────────
# API-эндпоинты
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"message": "KKEPIK Bot API"}


@app.get("/user/{user_id}")
async def get_user(user_id: int):
    """Получение данных пользователя по ID"""
    conn = sqlite3.connect(config.get_db_path())
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT user_id, role, name_or_group, is_class_teacher, class_group
            FROM users WHERE user_id = ?
        """, (user_id,))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        return {
            "user_id":          user[0],
            "role":             user[1],
            "name_or_group":    user[2],
            "is_class_teacher": bool(user[3]),
            "class_group":      user[4],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.post("/schedule/group")
async def get_group_schedule(request: GroupScheduleRequest):
    """Получение расписания группы"""
    df = None
    schedule_date = None

    if request.date:
        # 1. Проверяем кэш в памяти
        if global_schedules.last_groups_date == request.date:
            df = global_schedules.last_groups_df
            schedule_date = request.date
        else:
            # 2. Ищем файл (.xlsx или .pdf)
            df = load_schedule_df(config.DATA_DIR, request.date, "groups")
            if df is not None:
                schedule_date = request.date

    if df is None:
        # Fallback: кэш → сегодняшнее → последнее
        if global_schedules.last_groups_df is not None:
            df = global_schedules.last_groups_df
            schedule_date = global_schedules.last_groups_date
        else:
            df, schedule_date = get_today_schedule("groups")
        if df is None:
            df, schedule_date = get_latest_schedule("groups")
        if df is None:
            raise HTTPException(status_code=404, detail="Расписание не найдено")

    lines = group_schedule.get_schedule_for_group(df, request.group.upper())
    if lines is None:
        raise HTTPException(
            status_code=404,
            detail=f"Группа {request.group} не найдена в расписании",
        )
    if not lines:
        lines = [f"▪️{i} пара – Нет" for i in range(1, 5)]

    return {"group": request.group, "date": schedule_date, "schedule": lines}


@app.post("/schedule/teacher")
async def get_teacher_schedule(request: TeacherScheduleRequest):
    """Получение расписания преподавателя"""
    df = None
    schedule_date = None

    if request.date:
        # 1. Кэш преподавателей
        if global_schedules.last_teachers_date == request.date:
            df = global_schedules.last_teachers_df
            schedule_date = request.date
        else:
            # 2. Файл ПРЕПОДАВАТЕЛИ
            df = load_schedule_df(config.DATA_DIR, request.date, "teachers")
            if df is not None:
                schedule_date = request.date

        if df is None:
            # 3. Кэш групп (PDF содержит имена преподавателей)
            if global_schedules.last_groups_date == request.date:
                df = global_schedules.last_groups_df
                schedule_date = request.date
            else:
                # 4. Файл ГРУППЫ
                df = load_schedule_df(config.DATA_DIR, request.date, "groups")
                if df is not None:
                    schedule_date = request.date
                else:
                    print(f"Расписание на дату {request.date} не найдено")

    if df is None:
        # Fallback: кэш → сегодняшнее → последнее (teachers → groups)
        if global_schedules.last_teachers_df is not None:
            df = global_schedules.last_teachers_df
            schedule_date = global_schedules.last_teachers_date
        elif global_schedules.last_groups_df is not None:
            df = global_schedules.last_groups_df
            schedule_date = global_schedules.last_groups_date
        else:
            df, schedule_date = get_today_schedule("teachers")
            if df is None:
                df, schedule_date = get_today_schedule("groups")
        if df is None:
            df, schedule_date = get_latest_schedule("teachers")
        if df is None:
            df, schedule_date = get_latest_schedule("groups")
        if df is None:
            raise HTTPException(status_code=404, detail="Расписание не найдено")

    print(f"Получено расписание на дату {schedule_date}")
    lines = teacher_schedule.get_schedule_for_teacher(df, request.teacher)
    if not lines:
        raise HTTPException(
            status_code=404,
            detail=f"Преподаватель {request.teacher} не найден в расписании",
        )

    return {"teacher": request.teacher, "date": schedule_date, "schedule": lines}


@app.post("/schedule/audience")
async def get_audience_schedule(request: AudienceScheduleRequest):
    """Получение расписания аудитории"""
    df = None
    schedule_date = None

    if request.date:
        if global_schedules.last_groups_date == request.date:
            df = global_schedules.last_groups_df
            schedule_date = request.date
        else:
            df = load_schedule_df(config.DATA_DIR, request.date, "groups")
            if df is not None:
                schedule_date = request.date
            else:
                print(f"Файлы с расписанием групп на дату {request.date} не найдены")

    if df is None:
        if request.date:
            raise HTTPException(
                status_code=404,
                detail=f"Расписание групп на дату {request.date} не найдено",
            )
        print("Попытка получить сегодняшнее расписание...")
        df, schedule_date = get_today_schedule("groups")
        if df is None:
            print("Сегодняшнее расписание также не найдено")
            raise HTTPException(status_code=404, detail="Расписание не найдено")

    print(f"Получено расписание на дату {schedule_date}")
    lines = audience_schedule.get_schedule_for_audience(df, request.audience)
    if not lines:
        raise HTTPException(
            status_code=404,
            detail=f"Аудитория {request.audience} не найдена в расписании",
        )

    return {"audience": request.audience, "date": schedule_date, "schedule": lines}


@app.post("/getUserSchedule/{user_id}")
async def get_user_schedule(user_id: int, request: ScheduleRequest):
    """Получение расписания пользователя по его ID"""
    conn = sqlite3.connect(config.get_db_path())
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT role, name_or_group, is_class_teacher, class_group
            FROM users WHERE user_id = ?
        """, (user_id,))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        role, name_or_group, is_class_teacher, class_group = user
        if role == config.ROLE_STUDENT:
            return await get_group_schedule(
                GroupScheduleRequest(group=name_or_group, date=request.date)
            )
        elif role == config.ROLE_TEACHER:
            return await get_teacher_schedule(
                TeacherScheduleRequest(teacher=name_or_group, date=request.date)
            )
        else:
            raise HTTPException(status_code=400, detail="Неизвестная роль пользователя")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.post("/schedule/full")
async def get_full_schedule(request: FullScheduleRequest):
    """Получение полного расписания в формате JSON"""
    df = None
    schedule_date = None

    if request.date:
        df = load_schedule_df(config.DATA_DIR, request.date, request.schedule_type)
        if df is not None:
            schedule_date = request.date

    if df is None:
        df, schedule_date = get_today_schedule(request.schedule_type)
        if df is None:
            raise HTTPException(status_code=404, detail="Расписание не найдено")

    try:
        df = df.where(pd.notnull(df), None)
        data = []
        for _, row in df.iterrows():
            data.append({str(col): row[col] for col in df.columns})
        return {"date": schedule_date, "type": request.schedule_type, "data": data}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при преобразовании данных: {str(e)}",
        )


@app.get("/schedule/download/{schedule_type}/{date}")
async def download_schedule(schedule_type: str, date: str):
    """Скачивание файла расписания (.xlsx или .pdf)"""
    if schedule_type not in ("groups", "teachers"):
        raise HTTPException(status_code=400, detail="Неверный тип расписания")

    try:
        data_dir = config.DATA_DIR
        print(f"Ищем файл: дата={date}, тип={schedule_type}")

        result = find_schedule_file(data_dir, date, schedule_type)
        if result is None:
            available = [
                f for f in os.listdir(data_dir)
                if not f.startswith("~$")
                and os.path.splitext(f)[1].lower() in (".xlsx", ".pdf")
            ]
            print(f"Доступные файлы: {available}")
            raise HTTPException(
                status_code=404,
                detail=f"Файл не найден. Доступные файлы: {', '.join(available)}",
            )

        file_path, ext = result
        filename = os.path.basename(file_path)
        media_type = (
            "application/pdf"
            if ext == ".pdf"
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        print(f"Отдаём файл: {filename} ({media_type})")
        return FileResponse(path=file_path, filename=filename, media_type=media_type)

    except HTTPException:
        raise
    except Exception as e:
        print(f"Ошибка при скачивании файла: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка при скачивании: {e}")


@app.get("/groups")
async def get_all_groups():
    """Получение списка всех групп (.xlsx и .pdf)"""
    try:
        # Сначала используем кэшированный DataFrame — быстрее и актуальнее
        df = global_schedules.last_groups_df
        groups = parser_all.get_all_groups(df)  # df=None → автопоиск в DATA_DIR
        if not groups:
            raise HTTPException(status_code=404, detail="Список групп не найден")
        return {"groups": groups}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/teachers")
async def get_all_teachers():
    """Получение списка всех преподавателей (.xlsx и .pdf)"""
    try:
        df = global_schedules.last_teachers_df
        teachers = parser_all.get_all_teachers(df)
        if not teachers:
            raise HTTPException(status_code=404, detail="Список преподавателей не найден")
        return {"teachers": teachers}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/groups/by-course")
async def get_groups_by_course():
    """Получение списка групп, сгруппированных по курсам"""
    try:
        df = global_schedules.last_groups_df
        groups = parser_all.get_all_groups(df)
        if not groups:
            raise HTTPException(status_code=404, detail="Список групп не найден")
        return {"groups_by_course": parser_all.get_groups_by_course(groups)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/teachers/by-department")
async def get_teachers_by_department():
    """Получение списка преподавателей, сгруппированных по кафедрам"""
    try:
        df = global_schedules.last_teachers_df
        teachers = parser_all.get_all_teachers(df)
        if not teachers:
            raise HTTPException(status_code=404, detail="Список преподавателей не найден")
        return {"teachers_by_department": parser_all.get_teachers_by_department(teachers)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/schedule/reload")
async def reload_schedule_cache():
    """Перезагружает кэш расписаний из DATA_DIR (вызывается ботом после загрузки файла)."""
    try:
        global_schedules.reload_cache()
        return {
            "ok": True,
            "groups_date": global_schedules.last_groups_date,
            "teachers_date": global_schedules.last_teachers_date,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=config.get_api_port())
