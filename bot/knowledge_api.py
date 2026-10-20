"""Read-only structured schedule API, independent of user authentication/data."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from schedules.knowledge_store import KnowledgeStore

router = APIRouter(prefix="/knowledge", tags=["Public schedule knowledge"])


def get_store() -> KnowledgeStore:
    return KnowledgeStore()


def _call(method, **kwargs):
    try:
        return method(**kwargs)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/catalog")
def catalog():
    return get_store().catalog()


@router.get("/resolve")
def resolve(entity_type: str = Query(..., max_length=15), query: str = Query(..., min_length=1, max_length=150)):
    return _call(get_store().resolve, entity_type=entity_type, query=query)


@router.get("/schedule")
def schedule(entity_type: str = Query(..., max_length=15), entity: str = Query(..., min_length=1, max_length=150),
             date: str | None = Query(None, max_length=20), start: str | None = Query(None, max_length=20),
             end: str | None = Query(None, max_length=20)):
    return _call(get_store().schedule, entity_type=entity_type, entity=entity, date=date, start=start, end=end)


@router.get("/coverage")
def coverage(entity_type: str | None = Query(None, max_length=15), entity: str | None = Query(None, max_length=150),
             start: str | None = Query(None, max_length=20), end: str | None = Query(None, max_length=20)):
    return _call(get_store().coverage, entity_type=entity_type, entity=entity, start=start, end=end)


@router.get("/changes")
def changes(entity_type: str = Query(..., max_length=15), entity: str = Query(..., min_length=1, max_length=150),
            date: str = Query(..., max_length=20)):
    return _call(get_store().changes, entity_type=entity_type, entity=entity, date=date)


@router.get("/sources/{sha}")
def source(sha: str):
    result = get_store().source_path(sha)
    if result is None:
        raise HTTPException(status_code=404, detail="Источник не найден")
    path, mime = result
    return FileResponse(path, media_type=mime, filename=path.name,
                        headers={"Cache-Control": "public, max-age=31536000, immutable",
                                 "X-Content-Type-Options": "nosniff"})
