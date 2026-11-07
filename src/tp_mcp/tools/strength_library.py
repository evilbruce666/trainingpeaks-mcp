"""Strength Builder templates inside workout-library folders.

A library folder (tp_get_libraries / tp_create_library) holds two kinds of item:
classic endurance templates (`WorkoutTemplate`, tp_create_library_item) and
Strength Builder templates (`ActivityWorkoutTemplate`). The second kind lives on
the strength host (`api.peakswaresb.com`), never in /exerciselibrary, so
tp_get_library_items does not list it. Endpoints are the ones the TP web app's
Strength Builder calls; probed live 2026-11-07 under `/rx/activity/v1/`:

  • list     → GET    libraryContent/workoutLibrary/{libraryId}  (bare array of
               items, each with its full `workout`)
  • create   → POST   libraryContent/workoutLibrary/create  {libraryId, title,
               workoutLibraryItemType} → an UNSAVED draft with UUID ids
  • save     → POST   libraryContent/workoutLibrary/save  {id, libraryId, title,
               workout, workoutLibraryItemType}; a draft UUID creates the item and
               returns its numeric id, an existing numeric id edits it in place
  • detail   → GET    libraryContent/workoutLibrary/items/{id}
  • delete   → DELETE libraryContent/workoutLibrary/items/{id}
  • copy     → POST   LibraryContent/workoutLibrary/items/copy  {libraryId,
               workoutLibraryItemId, title} → a new item in the target folder
  • from cal → POST   LibraryContent/workoutLibrary/createFromWorkout  {libraryId,
               title, workoutLibraryItemType, workoutId} → calendar workout → folder
  • schedule → POST   libraryContent/workoutLibrary/applyToCalendar  {calendarId,
               workoutLibraryItemId, prescribedDate, prescribedStartTime,
               orderOnDay} → a new calendar strength workout with the template's
               blocks
"""

from __future__ import annotations

import re
from datetime import date as _date
from typing import Any

from tp_mcp.tools.strength import (
    _build_blocks,
    _err,
    _fmt_workout_detail,
    _recount,
    _resolve_exercises,
    _validate_blocks,
)
from tp_mcp.tools.strength_api import RX_BASE, RxSession, api_error, run_rx
from tp_mcp.tools.strength_api import body as _body
from tp_mcp.tools.strength_api import data as _data

_LIBRARY = f"{RX_BASE}/libraryContent/workoutLibrary"
# The web app spells these two with a capital L; kept verbatim.
_LIBRARY_CAP = f"{RX_BASE}/LibraryContent/workoutLibrary"
ITEM_TYPE = "ActivityWorkoutTemplate"
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _num(value: Any, name: str) -> tuple[int | None, dict[str, Any] | None]:
    try:
        return int(str(value).strip()), None
    except (TypeError, ValueError):
        return None, _err("VALIDATION_ERROR", f"{name} must be a numeric id.")


_NOT_FOUND = "Strength template not found."


def _template_view(item: dict[str, Any]) -> dict[str, Any]:
    """Project a library item (with its workout) to the fields tools expose."""
    detail = _fmt_workout_detail(item.get("workout") or {})
    for k in ("workout_id", "date", "executed_duration_min", "compliance_state",
              "compliance_percent", "rpe", "feel", "completed_sets"):
        detail.pop(k, None)
    for b in detail["blocks"]:
        b.pop("compliance_percent", None)
        for e in b["exercises"]:
            e.pop("compliance_percent", None)
            for s in e["sets"]:
                s.pop("executed", None)
                s.pop("complete", None)
    lib = item.get("libraryId")
    return {
        "item_id": str(item.get("id")),
        "library_id": str(lib) if lib is not None else None,
        **detail,
        "title": item.get("title") or detail.get("title"),
    }


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    wk = item.get("workout") or {}
    names: list[str] = []
    blocks = [b for b in (wk.get("blocks") or []) if isinstance(b, dict)]
    # Block titles are the coach's own words (often the exercise name in the
    # coach's language); the server fills an untitled block with the exercise.
    block_titles = [str(b.get("title")).strip() for b in blocks if str(b.get("title") or "").strip()]
    for b in blocks:
        for p in b.get("prescriptions") or []:
            if not isinstance(p, dict):
                continue
            t = (p.get("exercise") or {}).get("title")
            if t and t not in names:
                names.append(t)
    snap = _recount(blocks)
    return {
        "item_id": str(item.get("id")),
        "title": item.get("title"),
        "total_blocks": snap["totalBlocks"],
        "total_sets": snap["totalSets"],
        "exercises": names,
        "blocks": block_titles,
    }


async def _save(rx: RxSession, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    r = await rx.call("POST", f"{_LIBRARY}/save", payload)
    if r.status_code != 200:
        return {}, api_error(r, _NOT_FOUND)
    data = _data(r)
    if not data.get("id"):
        return {}, _err("API_ERROR", "Strength library save returned no item id.")
    return data, None


# ── Tools ───────────────────────────────────────────────────────────────────


async def tp_get_strength_library_items(library_id: str) -> dict[str, Any]:
    """List the Strength Builder templates in a workout-library folder.

    Args:
        library_id: Folder id.

    Returns:
        Dict with `count` and `items` (item_id, title, block/set counts, the
        exercises used, the block titles), sorted by title.
    """
    lib, err = _num(library_id, "library_id")
    if err:
        return err

    async def go(rx: RxSession) -> dict[str, Any]:
        r = await rx.call("GET", f"{_LIBRARY}/{lib}")
        if r.status_code != 200:
            return api_error(r, "Library folder not found.")
        raw = _body(r)
        rows = raw if isinstance(raw, list) else (_data(r).get("items") or [])
        rows = [it for it in rows if isinstance(it, dict) and it.get("id") is not None]
        items = [_summary(it) for it in rows if it.get("workoutLibraryItemType") == ITEM_TYPE]
        items.sort(key=lambda i: (i["title"] or "").lower())
        return {"library_id": str(lib), "count": len(items), "items": items}

    return await run_rx("library list", go)


async def tp_get_strength_library_item(item_id: str) -> dict[str, Any]:
    """Read a Strength Builder template in full: blocks, exercises, prescribed sets.

    Args:
        item_id: Template id (tp_get_strength_library_items).
    """
    iid, err = _num(item_id, "item_id")
    if err:
        return err

    async def go(rx: RxSession) -> dict[str, Any]:
        r = await rx.call("GET", f"{_LIBRARY}/items/{iid}")
        if r.status_code != 200:
            return api_error(r, _NOT_FOUND)
        data = _data(r)
        if not isinstance(data.get("workout"), dict):
            return _err("NOT_FOUND", _NOT_FOUND)
        return _template_view(data)

    return await run_rx("library read", go)


async def tp_create_strength_library_item(
    library_id: str,
    title: str,
    blocks: list[dict[str, Any]],
    instructions: str | None = None,
) -> dict[str, Any]:
    """Save a new Strength Builder template into a workout-library folder.

    Args:
        library_id: Folder id (tp_get_libraries / tp_create_library).
        title: Template title.
        blocks: Same shape as tp_create_strength_workout; own exercises allowed.
        instructions: Optional session description.

    Returns:
        Dict with the new `item_id`, library_id, title and block/set counts.
    """
    lib, err = _num(library_id, "library_id")
    if err:
        return err
    name = str(title or "").strip()
    if not name:
        return _err("VALIDATION_ERROR", "title is required.")
    if not isinstance(blocks, list):
        return _err("VALIDATION_ERROR", "blocks must be a list.")
    invalid = _validate_blocks(blocks)
    if invalid:
        return _err("VALIDATION_ERROR", invalid)

    async def go(rx: RxSession) -> dict[str, Any]:
        known, err = await _resolve_exercises(rx.http, rx.headers, blocks)
        if err:
            return err
        r = await rx.call("POST", f"{_LIBRARY}/create",
                          {"libraryId": lib, "title": name, "workoutLibraryItemType": ITEM_TYPE})
        if r.status_code != 200:
            return api_error(r, "Library folder not found.")
        draft = _data(r)
        workout = draft.get("workout")
        if not draft.get("id") or not isinstance(workout, dict):
            return _err("API_ERROR", "Strength library returned no draft to fill.")
        workout["blocks"] = _build_blocks(blocks, known)
        workout["snapshot"] = {**(workout.get("snapshot") or {}), **_recount(workout["blocks"])}
        workout["instructions"] = instructions
        workout["title"] = name
        data, err = await _save(rx, {"id": draft["id"], "libraryId": lib, "title": name,
                                     "workout": workout, "workoutLibraryItemType": ITEM_TYPE})
        if err:
            return err
        return {"item_id": str(data["id"]), "library_id": str(lib), "title": name,
                "total_blocks": workout["snapshot"]["totalBlocks"],
                "total_sets": workout["snapshot"]["totalSets"]}

    return await run_rx("library create", go)


async def tp_update_strength_library_item(
    item_id: str,
    blocks: list[dict[str, Any]] | None = None,
    title: str | None = None,
    instructions: str | None = None,
    mode: str = "replace",
) -> dict[str, Any]:
    """Edit a Strength Builder template in place (same id, same folder).

    Args:
        item_id: Template id.
        blocks: Optional blocks (shape of tp_create_strength_workout).
        title: Optional new title.
        instructions: Optional new description ("" clears it).
        mode: "replace" (default) swaps the blocks; "append" adds after them.

    Returns:
        Dict with item_id, title, the fields changed and block/set counts.
    """
    iid, err = _num(item_id, "item_id")
    if err:
        return err
    if mode not in ("replace", "append"):
        return _err("VALIDATION_ERROR", f"mode must be 'replace' or 'append', got {mode!r}.")
    if blocks is not None:
        if not isinstance(blocks, list):
            return _err("VALIDATION_ERROR", "blocks must be a list.")
        invalid = _validate_blocks(blocks)
        if invalid:
            return _err("VALIDATION_ERROR", invalid)
    if title is not None and not str(title).strip():
        return _err("VALIDATION_ERROR", "title cannot be empty.")
    if blocks is None and title is None and instructions is None:
        return _err("VALIDATION_ERROR", "Nothing to update: provide blocks, title or instructions.")

    async def go(rx: RxSession) -> dict[str, Any]:
        r = await rx.call("GET", f"{_LIBRARY}/items/{iid}")
        if r.status_code != 200:
            return api_error(r, _NOT_FOUND)
        item = _data(r)
        workout = item.get("workout")
        if not isinstance(workout, dict):
            return _err("NOT_FOUND", _NOT_FOUND)
        changed: list[str] = []
        if blocks is not None:
            known, err = await _resolve_exercises(rx.http, rx.headers, blocks)
            if err:
                return err
            new_blocks = _build_blocks(blocks, known)
            workout["blocks"] = ((workout.get("blocks") or []) + new_blocks
                                 if mode == "append" else new_blocks)
            changed.append(f"blocks ({mode})")
        name = str(title).strip() if title is not None else item.get("title")
        if title is not None:
            workout["title"] = name
            changed.append("title")
        if instructions is not None:
            workout["instructions"] = instructions or None
            changed.append("instructions")
        workout["snapshot"] = {**(workout.get("snapshot") or {}), **_recount(workout.get("blocks") or [])}
        data, err = await _save(rx, {"id": iid, "libraryId": item.get("libraryId"), "title": name,
                                     "workout": workout,
                                     "workoutLibraryItemType": item.get("workoutLibraryItemType") or ITEM_TYPE})
        if err:
            return err
        result = {"item_id": str(data["id"]), "title": name, "changed": changed,
                  "total_blocks": workout["snapshot"]["totalBlocks"],
                  "total_sets": workout["snapshot"]["totalSets"]}
        # Save is an upsert keyed on the id: a different id means a copy appeared.
        if str(data["id"]) != str(iid):
            result["warning"] = (f"Server returned id {data['id']}, expected {iid} — a copy may "
                                 f"have been created in the folder.")
        return result

    return await run_rx("library update", go)


async def tp_copy_strength_library_item(
    item_id: str, library_id: str, title: str | None = None
) -> dict[str, Any]:
    """Copy a Strength Builder template into a folder (the same or another one).

    Args:
        item_id: Template to copy.
        library_id: Target folder id.
        title: Optional title for the copy (default: the original title).

    Returns:
        Dict with the new `item_id`, target library_id and title.
    """
    iid, err = _num(item_id, "item_id")
    if err:
        return err
    lib, err = _num(library_id, "library_id")
    if err:
        return err

    async def go(rx: RxSession) -> dict[str, Any]:
        name = str(title or "").strip()
        if not name:
            r = await rx.call("GET", f"{_LIBRARY}/items/{iid}")
            if r.status_code != 200:
                return api_error(r, _NOT_FOUND)
            original = _data(r)
            if not original:
                return _err("NOT_FOUND", _NOT_FOUND)
            name = str(original.get("title") or "").strip()
            if not name:
                return _err("VALIDATION_ERROR", "The template has no title; pass a title for the copy.")
        r = await rx.call("POST", f"{_LIBRARY_CAP}/items/copy",
                          {"libraryId": lib, "workoutLibraryItemId": iid, "title": name})
        if r.status_code != 200:
            return api_error(r, _NOT_FOUND)
        data = _data(r)
        if not data.get("id"):
            return _err("API_ERROR", "Strength library copy returned no item id.")
        return {"item_id": str(data["id"]), "library_id": str(lib), "title": data.get("title") or name}

    return await run_rx("library copy", go)


async def tp_save_strength_workout_to_library(
    workout_id: str, library_id: str, title: str | None = None
) -> dict[str, Any]:
    """Save a calendar strength workout into a folder as a template.

    Args:
        workout_id: Calendar strength workout id (tp_get_strength_workouts).
        library_id: Target folder id.
        title: Optional template title (default: the workout's title).

    Returns:
        Dict with the new `item_id`, library_id and title.
    """
    wid, err = _num(workout_id, "workout_id")
    if err:
        return err
    lib, err = _num(library_id, "library_id")
    if err:
        return err

    async def go(rx: RxSession) -> dict[str, Any]:
        name = str(title or "").strip()
        if not name:
            r = await rx.call("GET", f"{RX_BASE}/workouts/{wid}")
            if r.status_code != 200:
                return api_error(r, "Strength workout not found.")
            name = str(_data(r).get("title") or "").strip() or "Strength"
        r = await rx.call("POST", f"{_LIBRARY_CAP}/createFromWorkout",
                          {"libraryId": lib, "title": name, "workoutLibraryItemType": ITEM_TYPE,
                           "workoutId": wid})
        if r.status_code != 200:
            return api_error(r, "Strength workout not found.")
        data = _data(r)
        if not data.get("id"):
            return _err("API_ERROR", "Strength library save returned no item id.")
        return {"item_id": str(data["id"]), "library_id": str(lib), "title": data.get("title") or name}

    return await run_rx("library save from workout", go)


async def tp_schedule_strength_library_item(item_id: str, date: str) -> dict[str, Any]:
    """Put a Strength Builder template on the athlete's calendar (the athlete
    given via the `athlete` parameter).

    Args:
        item_id: Template id.
        date: Planned date, YYYY-MM-DD.

    Returns:
        Dict with the created calendar `workout_id`, date, title and counts.
    """
    iid, err = _num(item_id, "item_id")
    if err:
        return err
    day = str(date or "").strip()
    # Strict: fromisoformat alone also takes «20261108» / «2026-W45-7» on 3.11+.
    if not _ISO_DAY.fullmatch(day):
        return _err("VALIDATION_ERROR", "date must be YYYY-MM-DD.")
    try:
        _date.fromisoformat(day)
    except ValueError:
        return _err("VALIDATION_ERROR", "date must be YYYY-MM-DD.")

    async def go(rx: RxSession) -> dict[str, Any]:
        r = await rx.call("POST", f"{_LIBRARY}/applyToCalendar",
                          {"calendarId": rx.calendar_id, "workoutLibraryItemId": iid,
                           "prescribedDate": day, "prescribedStartTime": None, "orderOnDay": None})
        if r.status_code != 200:
            return api_error(r, _NOT_FOUND)
        data = _data(r)
        if not data.get("id"):
            return _err("API_ERROR", "Strength library schedule returned no workout id.")
        snap = data.get("snapshot") or {}
        return {"workout_id": str(data["id"]), "item_id": str(iid),
                "date": data.get("prescribedDate") or day, "title": data.get("title"),
                "total_blocks": snap.get("totalBlocks"), "total_sets": snap.get("totalSets")}

    return await run_rx("library schedule", go)


async def tp_delete_strength_library_item(item_id: str) -> dict[str, Any]:
    """Permanently delete a Strength Builder template from its folder.

    Args:
        item_id: Template id.
    """
    iid, err = _num(item_id, "item_id")
    if err:
        return err

    async def go(rx: RxSession) -> dict[str, Any]:
        r = await rx.call("DELETE", f"{_LIBRARY}/items/{iid}")
        if r.status_code not in (200, 204):
            return api_error(r, _NOT_FOUND)
        return {"deleted": True, "item_id": str(iid)}

    return await run_rx("library delete", go)
