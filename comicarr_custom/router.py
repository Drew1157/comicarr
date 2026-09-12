#  Copyright (C) 2026 Comicarr contributors
#
#  This file is part of Comicarr.
#
#  Comicarr is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.

"""Authenticated API for custom Franchise/MainHero organization."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from comicarr.app.core.security import require_session

from comicarr_custom import organization

router = APIRouter(prefix="/api/custom", tags=["custom-organization"])


def _body_value(body, snake, camel=None):
    if not isinstance(body, dict):
        return None
    value = body.get(snake)
    if value is None and camel:
        value = body.get(camel)
    return value


@router.get("/organization/series", dependencies=[Depends(require_session)])
def find_series(name: str, year: str | None = None):
    return {"success": True, "matches": organization.find_series(name, year=year)}


@router.get("/organization/{comic_id}", dependencies=[Depends(require_session)])
def get_organization(comic_id: str):
    row = organization.get_series(comic_id)
    if not row:
        return JSONResponse(status_code=404, content={"detail": "Series not found"})
    return {
        "success": True,
        "comic_id": str(comic_id),
        "franchise": row.get("Franchise") or "",
        "main_hero": row.get("MainHero") or "",
        "series": row.get("ComicName"),
        "year": row.get("ComicYear"),
        "publisher": row.get("ComicPublisher"),
        "location": row.get("ComicLocation"),
    }


@router.post("/organization/{comic_id}/preview", dependencies=[Depends(require_session)])
def preview_organization(comic_id: str, request_body: dict | None = None):
    franchise = _body_value(request_body, "franchise")
    main_hero = _body_value(request_body, "main_hero", "mainHero")
    result = organization.preview_organization(comic_id, franchise, main_hero)
    if not result.get("success"):
        return JSONResponse(status_code=404, content=result)
    return result


@router.post("/organization/{comic_id}/apply", dependencies=[Depends(require_session)])
def apply_organization(comic_id: str, request_body: dict | None = None):
    franchise = _body_value(request_body, "franchise")
    main_hero = _body_value(request_body, "main_hero", "mainHero")
    move_files = bool(_body_value(request_body, "move_files", "moveFiles"))
    confirm = bool(_body_value(request_body, "confirm"))
    if not confirm:
        return JSONResponse(status_code=400, content={"success": False, "error": "Explicit confirmation is required"})
    result = organization.apply_organization(comic_id, franchise, main_hero, move_files=move_files)
    if not result.get("success"):
        return JSONResponse(status_code=409, content=result)
    return result
