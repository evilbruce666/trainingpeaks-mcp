"""The account's own strength exercises (Strength Builder "custom exercises").

Built-in exercises (owner 2000301, `canEdit: false`) are read-only. A coach can
add their own; they then work in strength workouts and templates exactly like
built-in ones, by id. Endpoints (the TP web app's, probed live 2026-11-07, under
`/rx/activity/v1/`):

  • draft  → POST   exercises          → unsaved model (UUID ids, ownerId = you)
  • save   → PUT    exercises          → creates from a draft, or edits in place
               when the body carries an existing numeric id
  • detail → GET    exercises/{id}     (built-in or own)
  • delete → DELETE exercises/{id}
  • list   → GET    libraryContent     → the whole catalogue; own ones carry
               `canEdit: true`

Parameters are sent as bare names (`[{"parameter": "Reps"}]`); the server fills
in titles, units and ranges.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from tp_mcp.tools.strength import _KNOWN_PARAMS, _err
from tp_mcp.tools.strength_api import RX_BASE, RxSession, api_error, run_rx
from tp_mcp.tools.strength_api import data as _data

_NOT_FOUND = "Exercise not found."
# The server accepts only these enum names (from libraryContent.muscleGroups);
# the display form "Upper Back" is rejected with a bare 400 "Invalid exercise".
MUSCLE_GROUPS = ("Abs", "Biceps", "Calves", "Chest", "Forearms", "Glute", "Hamstrings",
                 "LowerBack", "Quads", "Shoulders", "Triceps", "UpperBack")
_MUSCLE_BY_KEY = {m.lower(): m for m in MUSCLE_GROUPS}
_READ_ONLY = "Built-in TrainingPeaks exercises cannot be edited or deleted; only your own."


def _view(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(d.get("id")),
        "title": d.get("title"),
        "own": bool(d.get("canEdit")),
        "video_url": d.get("videoUrl") or None,
        "instructions": d.get("instructions") or None,
        "parameters": [p.get("parameter") if isinstance(p, dict) else str(p)
                       for p in d.get("parameters") or []],
        "primary_muscle_groups": d.get("primaryMuscleGroups") or [],
        "secondary_muscle_groups": d.get("secondaryMuscleGroups") or [],
    }


def _muscles(groups: list[str] | None) -> tuple[list[str] | None, str | None]:
    """Normalise "Upper Back" / "upperback" to the server enum; reject unknown."""
    if groups is None:
        return None, None
    if isinstance(groups, str):
        groups = [groups]
    out, bad = [], []
    for g in groups:
        m = _MUSCLE_BY_KEY.get(str(g).replace(" ", "").lower())
        if m is None:
            bad.append(g)
        elif m not in out:
            out.append(m)
    if bad:
        return None, f"Unknown muscle group(s) {bad}. Allowed: {list(MUSCLE_GROUPS)}."
    return out, None


def _check_params(parameters: list[str] | None) -> str | None:
    if parameters is None:
        return None
    if not isinstance(parameters, list) or not parameters:
        return "parameters must be a non-empty list of parameter names."
    bad = [p for p in parameters if not isinstance(p, str) or p not in _KNOWN_PARAMS]
    if bad:
        return f"Unknown parameter(s) {bad}. Allowed: {sorted(_KNOWN_PARAMS)}."
    return None


async def _get(rx: RxSession, eid: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    r = await rx.call("GET", f"{RX_BASE}/exercises/{quote(eid, safe='')}")
    if r.status_code != 200:
        return {}, api_error(r, _NOT_FOUND)
    data = _data(r)
    if not data:
        return {}, _err("NOT_FOUND", _NOT_FOUND)
    return data, None


def _apply(model: dict[str, Any], title: str | None, video_url: str | None,
           instructions: str | None, parameters: list[str] | None,
           primary: list[str] | None, secondary: list[str] | None) -> list[str]:
    changed = []
    if title is not None:
        model["title"] = str(title).strip()
        changed.append("title")
    if video_url is not None:
        model["videoUrl"] = str(video_url).strip()
        changed.append("video_url")
    if instructions is not None:
        model["instructions"] = instructions
        changed.append("instructions")
    if parameters is not None:
        model["parameters"] = [{"parameter": p} for p in parameters]
        changed.append("parameters")
    if primary is not None:
        model["primaryMuscleGroups"] = list(primary)
        changed.append("primary_muscle_groups")
    if secondary is not None:
        model["secondaryMuscleGroups"] = list(secondary)
        changed.append("secondary_muscle_groups")
    return changed


async def tp_get_exercise(exercise_id: str) -> dict[str, Any]:
    """Read one exercise (built-in or your own) live: title, video, instructions,
    parameters, muscle groups and whether it is yours (`own`)."""
    eid = str(exercise_id or "").strip()
    if not eid:
        return _err("VALIDATION_ERROR", "exercise_id is required.")

    async def go(rx: RxSession) -> dict[str, Any]:
        data, err = await _get(rx, eid)
        return err or _view(data)

    return await run_rx("exercise read", go)


async def tp_get_own_exercises() -> dict[str, Any]:
    """List the account's own exercises (id, title, muscle groups)."""

    async def go(rx: RxSession) -> dict[str, Any]:
        r = await rx.call("GET", f"{RX_BASE}/libraryContent")
        if r.status_code != 200:
            return api_error(r, "Exercise library not found.")
        rows = [e for e in (_data(r).get("exercises") or []) if isinstance(e, dict)]
        out = [
            {"id": str(e.get("exerciseId")), "title": e.get("title"),
             "muscle_groups": (e.get("searchAttributes") or {}).get("primaryMuscleGroups") or []}
            for e in rows if e.get("canEdit") and e.get("exerciseId") is not None
        ]
        out.sort(key=lambda e: (e["title"] or "").lower())
        return {"count": len(out), "exercises": out}

    return await run_rx("own exercise list", go)


async def tp_create_exercise(
    title: str,
    parameters: list[str],
    video_url: str | None = None,
    instructions: str | None = None,
    primary_muscle_groups: list[str] | None = None,
    secondary_muscle_groups: list[str] | None = None,
) -> dict[str, Any]:
    """Create your own exercise for strength workouts and templates.

    Args:
        title: Exercise name.
        parameters: Default columns, e.g. ["Reps", "WeightKg"] (same names as set
            parameters in tp_create_strength_workout).
        video_url: Optional demo video link.
        instructions: Optional technique description.
        primary_muscle_groups / secondary_muscle_groups: Optional, e.g. ["Quads"].

    Returns:
        The saved exercise (id, title, parameters, …).
    """
    name = str(title or "").strip()
    if not name:
        return _err("VALIDATION_ERROR", "title is required.")
    bad = _check_params(parameters if parameters is not None else [])
    primary, bad_p = _muscles(primary_muscle_groups or [])
    secondary, bad_s = _muscles(secondary_muscle_groups or [])
    if bad or bad_p or bad_s:
        return _err("VALIDATION_ERROR", bad or bad_p or bad_s or "")

    async def go(rx: RxSession) -> dict[str, Any]:
        r = await rx.call("POST", f"{RX_BASE}/exercises")
        if r.status_code != 200:
            return api_error(r, _NOT_FOUND)
        model = _data(r)
        if not model.get("id"):
            return _err("API_ERROR", "Exercise draft was not returned.")
        _apply(model, name, video_url or "", instructions or "", parameters, primary, secondary)
        r = await rx.call("PUT", f"{RX_BASE}/exercises", model)
        if r.status_code != 200:
            return api_error(r, _NOT_FOUND)
        saved = _data(r)
        if not saved.get("id"):
            return _err("API_ERROR", "Exercise save returned no id.")
        return _view(saved)

    return await run_rx("exercise create", go)


async def tp_update_exercise(
    exercise_id: str,
    title: str | None = None,
    parameters: list[str] | None = None,
    video_url: str | None = None,
    instructions: str | None = None,
    primary_muscle_groups: list[str] | None = None,
    secondary_muscle_groups: list[str] | None = None,
) -> dict[str, Any]:
    """Edit your own exercise in place; omitted fields stay as they are."""
    eid = str(exercise_id or "").strip()
    if not eid:
        return _err("VALIDATION_ERROR", "exercise_id is required.")
    if title is not None and not str(title).strip():
        return _err("VALIDATION_ERROR", "title cannot be empty.")
    bad = _check_params(parameters)
    primary, bad_p = _muscles(primary_muscle_groups)
    secondary, bad_s = _muscles(secondary_muscle_groups)
    if bad or bad_p or bad_s:
        return _err("VALIDATION_ERROR", bad or bad_p or bad_s or "")
    fields = (title, parameters, video_url, instructions, primary_muscle_groups, secondary_muscle_groups)
    if all(f is None for f in fields):
        return _err("VALIDATION_ERROR", "Nothing to update.")

    async def go(rx: RxSession) -> dict[str, Any]:
        model, err = await _get(rx, eid)
        if err:
            return err
        if not model.get("canEdit"):
            return _err("VALIDATION_ERROR", _READ_ONLY)
        changed = _apply(model, title, video_url, instructions, parameters, primary, secondary)
        r = await rx.call("PUT", f"{RX_BASE}/exercises", model)
        if r.status_code != 200:
            return api_error(r, _NOT_FOUND)
        saved = _data(r)
        out = {**_view(saved or model), "changed": changed}
        if saved.get("id") is not None and str(saved["id"]) != eid:
            out["warning"] = f"Server returned id {saved['id']}, expected {eid} — a copy may have been created."
        return out

    return await run_rx("exercise update", go)


async def tp_delete_exercise(exercise_id: str) -> dict[str, Any]:
    """Permanently delete your own exercise (built-in ones are refused)."""
    eid = str(exercise_id or "").strip()
    if not eid:
        return _err("VALIDATION_ERROR", "exercise_id is required.")

    async def go(rx: RxSession) -> dict[str, Any]:
        model, err = await _get(rx, eid)
        if err:
            return err
        if not model.get("canEdit"):
            return _err("VALIDATION_ERROR", _READ_ONLY)
        r = await rx.call("DELETE", f"{RX_BASE}/exercises/{quote(eid, safe='')}")
        if r.status_code not in (200, 204):
            return api_error(r, _NOT_FOUND)
        return {"deleted": True, "exercise_id": eid, "title": model.get("title")}

    return await run_rx("exercise delete", go)
