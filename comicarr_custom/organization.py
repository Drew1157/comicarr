#  Copyright (C) 2026 Comicarr contributors
#
#  This file is part of Comicarr.
#
#  Comicarr is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.

"""Custom series organization for franchise/main-hero library layouts."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import comicarr
from comicarr import db, helpers, logger, updater


def _text(value):
    if value is None:
        return ""
    value = str(value).strip()
    return "" if value.lower() == "none" else value


def _safe(value):
    value = _text(value)
    if not value:
        return ""
    return _text(helpers.filesafe(value))


def get_series(comic_id):
    return db.raw_select_one(
        "SELECT ComicID, ComicName, ComicYear, ComicPublisher, PublisherImprint, "
        "ComicLocation, Type, Corrected_Type, ComicVersion, Franchise, MainHero "
        "FROM comics WHERE ComicID=?",
        [str(comic_id)],
    )


def find_series(series_name, year=None, limit=20):
    name = _text(series_name)
    if not name:
        return []

    args = ["%%%s%%" % name]
    sql = (
        "SELECT ComicID, ComicName, ComicYear, ComicPublisher, ComicLocation, Franchise, MainHero "
        "FROM comics WHERE LOWER(ComicName) LIKE LOWER(?)"
    )
    if year:
        sql += " AND ComicYear=?"
        args.append(str(year))
    sql += " ORDER BY CASE WHEN LOWER(ComicName)=LOWER(?) THEN 0 ELSE 1 END, ComicYear DESC LIMIT ?"
    args.extend([name, int(limit)])
    return db.raw_select_all(sql, args) or []


def resolve_unique_series(series_name, year=None):
    matches = find_series(series_name, year=year)
    if len(matches) == 1:
        return matches[0], None

    exact = [row for row in matches if _text(row.get("ComicName")).lower() == _text(series_name).lower()]
    if year:
        exact = [row for row in exact if _text(row.get("ComicYear")) == str(year)]
    if len(exact) == 1:
        return exact[0], None

    return None, {
        "success": False,
        "error": "Series name is ambiguous" if matches else "Series not found",
        "matches": matches,
    }


def _format_values(row, franchise=None, main_hero=None):
    year = _text(row.get("ComicYear"))
    publisher = _safe(row.get("ComicPublisher"))
    series = _safe(row.get("ComicName"))
    imprint = _safe(row.get("PublisherImprint"))
    franchise_value = _safe(franchise if franchise is not None else row.get("Franchise"))
    main_hero_value = _safe(main_hero if main_hero is not None else row.get("MainHero"))
    booktype = _safe(row.get("Corrected_Type") or row.get("Type"))
    volume = _safe(row.get("ComicVersion"))

    return {
        "$Series": series,
        "$series": series.lower(),
        "$Publisher": publisher,
        "$publisher": publisher.lower(),
        "$Imprint": imprint,
        "$Year": year,
        "$VolumeY": "V%s" % year if year else "",
        "$VolumeN": volume.upper() if volume else "",
        "$Type": booktype,
        "$Franchise": franchise_value,
        "$MainHero": main_hero_value,
    }


def lookup_format_organization(values):
    """Resolve organization values from the naming-engine token dictionary."""
    series = _text(values.get("$Series") or values.get("$series"))
    publisher = _text(values.get("$Publisher") or values.get("$publisher"))
    if not series:
        return {"$Franchise": "", "$MainHero": ""}

    year = _text(values.get("$VolumeY"))
    if year.upper().startswith("V"):
        year = year[1:]
    if not re.fullmatch(r"\d{4}", year or ""):
        year = ""

    sql = "SELECT Franchise, MainHero FROM comics WHERE LOWER(ComicName)=LOWER(?)"
    args = [series]
    if publisher:
        sql += " AND LOWER(ComicPublisher)=LOWER(?)"
        args.append(publisher)
    if year:
        sql += " AND ComicYear=?"
        args.append(year)
    sql += " ORDER BY DateAdded DESC LIMIT 1"
    row = db.raw_select_one(sql, args) or {}
    return {
        "$Franchise": _safe(row.get("Franchise")),
        "$MainHero": _safe(row.get("MainHero")),
    }


def render_folder(row, franchise=None, main_hero=None):
    folder_format = _text(getattr(comicarr.CONFIG, "FOLDER_FORMAT", None)) or "$Series ($Year)"
    values = _format_values(row, franchise=franchise, main_hero=main_hero)
    rendered = folder_format
    for token, value in values.items():
        rendered = rendered.replace(token, value)

    # Strip unresolved custom tokens and empty decoration left by optional values.
    rendered = rendered.replace("$Franchise", "").replace("$MainHero", "")
    rendered = re.sub(r"\(\s*\)|\[\s*\]", "", rendered)
    rendered = rendered.replace("\\", "/")

    parts = []
    for part in rendered.split("/"):
        part = part.strip()
        if not part or part in {".", ".."}:
            continue
        parts.append(part)

    root = _text(getattr(comicarr.CONFIG, "DESTINATION_DIR", None)) or "/comics"
    return str(Path(root).joinpath(*parts))


def preview_organization(comic_id, franchise, main_hero):
    row = get_series(comic_id)
    if not row:
        return {"success": False, "error": "Series not found", "comic_id": str(comic_id)}

    target = render_folder(row, franchise=franchise, main_hero=main_hero)
    return {
        "success": True,
        "comic_id": str(comic_id),
        "series": row.get("ComicName"),
        "year": row.get("ComicYear"),
        "publisher": row.get("ComicPublisher"),
        "current_franchise": _text(row.get("Franchise")),
        "current_main_hero": _text(row.get("MainHero")),
        "franchise": _text(franchise),
        "main_hero": _text(main_hero),
        "current_path": _text(row.get("ComicLocation")),
        "target_path": target,
    }


def apply_organization(comic_id, franchise, main_hero, move_files=False):
    preview = preview_organization(comic_id, franchise, main_hero)
    if not preview.get("success"):
        return preview

    old_path = _text(preview.get("current_path"))
    target_path = _text(preview.get("target_path"))
    moved = False
    created_target = False

    if move_files and target_path and os.path.normpath(old_path or target_path) != os.path.normpath(target_path):
        if os.path.exists(target_path):
            return {
                **preview,
                "success": False,
                "error": "Target folder already exists; refusing to merge automatically",
            }

        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        if old_path and os.path.exists(old_path):
            shutil.move(old_path, target_path)
            moved = True
        else:
            os.makedirs(target_path, exist_ok=True)
            created_target = True

    try:
        if move_files:
            db.raw_execute(
                "UPDATE comics SET Franchise=?, MainHero=?, ComicLocation=? WHERE ComicID=?",
                [_text(franchise), _text(main_hero), target_path, str(comic_id)],
            )
        else:
            db.raw_execute(
                "UPDATE comics SET Franchise=?, MainHero=? WHERE ComicID=?",
                [_text(franchise), _text(main_hero), str(comic_id)],
            )
    except Exception:
        if moved and os.path.exists(target_path) and old_path and not os.path.exists(old_path):
            shutil.move(target_path, old_path)
        elif created_target and os.path.isdir(target_path):
            try:
                os.rmdir(target_path)
            except OSError:
                pass
        raise

    if move_files:
        try:
            updater.forceRescan(str(comic_id), module="[CUSTOM-ORGANIZATION]")
        except Exception as e:
            logger.warn("[CUSTOM-ORGANIZATION] Rescan failed for %s: %s" % (comic_id, e))

    return {
        **preview,
        "success": True,
        "moved": moved,
        "location_updated": bool(move_files),
    }


def preview_by_name(series_name, franchise, main_hero, year=None):
    row, error = resolve_unique_series(series_name, year=year)
    if error:
        return error
    return preview_organization(row["ComicID"], franchise, main_hero)


def apply_by_name(series_name, franchise, main_hero, year=None, move_files=False):
    row, error = resolve_unique_series(series_name, year=year)
    if error:
        return error
    return apply_organization(row["ComicID"], franchise, main_hero, move_files=move_files)
