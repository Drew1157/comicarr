#  Copyright (C) 2026 Comicarr contributors
#
#  This file is part of Comicarr.
#
#  Comicarr is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.

"""Runtime integration for the custom Franchise/MainHero Comicarr fork.

Keeping the customization in a small extension package makes rebasing on
upstream Comicarr much easier.  The fork only needs the Docker entrypoint to
launch through ``comicarr_custom.runner``.
"""

from __future__ import annotations

import json
import re

from sqlalchemy import Column, Text

from comicarr import helpers, logger
from comicarr.tables import comics

from comicarr_custom import organization

_INSTALLED = False


def _install_table_columns():
    """Teach SQLAlchemy's reflected application table about custom columns."""
    if "Franchise" not in comics.c:
        comics.append_column(Column("Franchise", Text))
    if "MainHero" not in comics.c:
        comics.append_column(Column("MainHero", Text))


def _install_folder_tokens():
    """Inject custom values whenever Comicarr renders folder-format tokens."""
    original = helpers.replace_all
    if getattr(original, "_comicarr_custom_org", False):
        return

    def replace_all_with_organization(text, values):
        if not isinstance(values, dict):
            return original(text, values)

        if "$Franchise" in str(text) or "$MainHero" in str(text):
            merged = dict(values)
            if not merged.get("$Franchise") or not merged.get("$MainHero"):
                try:
                    extras = organization.lookup_format_organization(merged)
                    merged.setdefault("$Franchise", extras.get("$Franchise", ""))
                    merged.setdefault("$MainHero", extras.get("$MainHero", ""))
                    if not merged.get("$Franchise"):
                        merged["$Franchise"] = extras.get("$Franchise", "")
                    if not merged.get("$MainHero"):
                        merged["$MainHero"] = extras.get("$MainHero", "")
                except Exception as e:
                    logger.warn("[CUSTOM-ORGANIZATION] Unable to resolve folder tokens: %s" % e)
            return original(text, merged)
        return original(text, values)

    replace_all_with_organization._comicarr_custom_org = True
    replace_all_with_organization._original = original
    helpers.replace_all = replace_all_with_organization


def _last_user_text(messages):
    for message in reversed(messages or []):
        if message.get("role") == "user":
            content = message.get("content", "")
            return content if isinstance(content, str) else ""
    return ""


def _parse_action(text):
    if not text:
        return None, text
    lines = text.strip().split("\n", 1)
    candidate = lines[0].strip()
    try:
        payload = json.loads(candidate)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, text
    if not isinstance(payload, dict) or payload.get("action") != "organize_series":
        return None, text
    remainder = lines[1].strip() if len(lines) > 1 else ""
    return payload, remainder


def _confirmation_is_explicit(user_text):
    normalized = re.sub(r"[^a-z0-9 ]+", " ", (user_text or "").lower())
    phrases = ("confirm", "apply", "do it", "go ahead", "yes move", "yes apply")
    return any(phrase in normalized for phrase in phrases)


def _preview_message(result):
    if not result.get("success"):
        matches = result.get("matches") or []
        if matches:
            options = ", ".join(
                "%s (%s)" % (item.get("ComicName"), item.get("ComicYear")) for item in matches[:6]
            )
            return "%s. Matches: %s" % (result.get("error", "Unable to organize series"), options)
        return result.get("error", "Unable to organize series")

    return (
        "Organization preview for {series} ({year}):\n"
        "{current}\n→ {target}\n"
        "Franchise: {franchise}\nMain Hero: {main_hero}\n\n"
        "No files have been moved yet. Reply **confirm** or **apply** to make this change."
    ).format(
        series=result.get("series") or "Series",
        year=result.get("year") or "",
        current=result.get("current_path") or "(no current folder)",
        target=result.get("target_path") or "(no target folder)",
        franchise=result.get("franchise") or "(blank)",
        main_hero=result.get("main_hero") or "(blank)",
    )


def _applied_message(result):
    if not result.get("success"):
        return "I couldn't apply that organization change: %s" % result.get("error", "unknown error")
    verb = "Moved and updated" if result.get("moved") else "Updated"
    return "%s %s (%s) to:\n%s" % (
        verb,
        result.get("series") or "series",
        result.get("year") or "",
        result.get("target_path") or result.get("current_path") or "",
    )


def _install_ai_organization_action():
    """Add a confirmation-gated organization action to Ask Comicarr."""
    from comicarr.app.ai import chat

    if "organize_series" not in chat._SYSTEM_PROMPT:
        chat._SYSTEM_PROMPT += """

Custom organization action:
When the user asks to classify, organize, place, move, or file a comic series under a
Franchise and Main Hero hierarchy, do NOT use a query pattern. On the first line output
EXACTLY one JSON object with this shape:
{"action":"organize_series","series":"Batgirl","year":"2011","franchise":"Batman","main_hero":"Batgirl","move_files":true,"confirm":false}
Then add a short conversational explanation. Use the exact series title/year when known.
For a follow-up where the user explicitly says confirm/apply/go ahead, repeat the same
JSON with confirm=true. Never set confirm=true unless the latest user message explicitly
confirms the previously previewed move. The application will enforce confirmation again.
"""

    original = chat.stream_chat_response
    if getattr(original, "_comicarr_custom_org", False):
        return

    async def stream_chat_response_with_organization(messages, ctx, current_turn_images=None):
        pending_text = []
        passthrough = []
        async for event in original(messages, ctx, current_turn_images=current_turn_images):
            if event.get("type") == "text":
                pending_text.append(event.get("content", ""))
            else:
                passthrough.append(event)

        combined = "".join(pending_text).strip()
        action, remainder = _parse_action(combined)
        if not action:
            for event in passthrough:
                yield event
            if combined:
                yield {"type": "text", "content": combined}
            return

        # Query-style result events are irrelevant for a write action, but usage is retained.
        for event in passthrough:
            if event.get("type") in {"usage", "done"}:
                if event.get("type") == "usage":
                    yield event

        series_name = action.get("series")
        year = action.get("year")
        franchise = action.get("franchise")
        main_hero = action.get("main_hero") or action.get("mainHero")
        move_files = bool(action.get("move_files", True))
        explicit = _confirmation_is_explicit(_last_user_text(messages))

        if action.get("confirm") and explicit:
            result = organization.apply_by_name(
                series_name,
                franchise,
                main_hero,
                year=year,
                move_files=move_files,
            )
            yield {"type": "text", "content": _applied_message(result)}
        else:
            result = organization.preview_by_name(series_name, franchise, main_hero, year=year)
            message = _preview_message(result)
            if remainder:
                message += "\n\n" + remainder
            yield {"type": "text", "content": message}
        yield {"type": "done"}

    stream_chat_response_with_organization._comicarr_custom_org = True
    stream_chat_response_with_organization._original = original
    chat.stream_chat_response = stream_chat_response_with_organization

    # These modules import the function directly, so replace their bound reference too.
    try:
        from comicarr.app.ai import chat_service

        chat_service.stream_chat_response = stream_chat_response_with_organization
    except Exception as e:
        logger.warn("[CUSTOM-ORGANIZATION] Could not patch chat_service: %s" % e)
    try:
        from comicarr.app.ai import router as ai_router

        ai_router.stream_chat_response = stream_chat_response_with_organization
    except Exception as e:
        logger.warn("[CUSTOM-ORGANIZATION] Could not patch AI router: %s" % e)


def _install_router():
    from comicarr.app.main import app
    from comicarr_custom.router import router

    marker = "/api/custom/organization/{comic_id}"
    if not any(getattr(route, "path", None) == marker for route in app.routes):
        app.include_router(router)


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _install_table_columns()
    _install_folder_tokens()
    _install_ai_organization_action()
    _install_router()
    _INSTALLED = True
