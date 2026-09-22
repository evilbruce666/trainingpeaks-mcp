"""Athlete group tools — read + write (issue #69).

TrainingPeaks exposes a coach's "Athlete Groups" as TAGS in the API:

    GET /coaches/v2/coaches/{coachId}/tags

where ``coachId`` is the personId of the token owner. These are *coach-scoped*
endpoints — they describe how the coach has grouped their roster, so they do
NOT take an ``athlete`` target. Each tag looks like::

    {"id": 123, "coachId": 1135463, "name": "Group A",
     "athleteIds": [201, 202], "isDefault": false}

Write operations were verified against the live API and use a DIFFERENT version
than the read endpoint (a TP quirk, like the library endpoints needing
``libraryName`` not ``name``):

  POST   /coaches/v1/coaches/{coachId}/tags        body {"Value": "<name>"}   (create)
  PUT    /coaches/v1/coaches/{coachId}/tags/{id}   body {"Value": "<name>"}   (rename)
  DELETE /coaches/v1/coaches/{coachId}/tags/{id}                              (delete)

Membership (add/remove athletes) is a separate sub-resource — one athlete per
call (verified live):

  POST   /coaches/v1/coaches/{coachId}/tags/{tagId}/athletes      body {"Value": <athleteId>}
  DELETE /coaches/v1/coaches/{coachId}/tags/{tagId}/athletes/{athleteId}

Note: reads are **v2**, writes are **v1**, and the body key is ``Value`` (not
``name``); ``athleteIds`` is NOT settable through the tag PUT — use the
membership endpoints above. The default group's membership is managed by TP and
is not edited here.
"""

import logging
from typing import Any

from tp_mcp.client import TPClient

logger = logging.getLogger("tp-mcp")

_TAGS_ENDPOINT = "/coaches/v2/coaches/{coach_id}/tags"        # read
_TAGS_WRITE = "/coaches/v1/coaches/{coach_id}/tags"          # create
_TAG_WRITE = "/coaches/v1/coaches/{coach_id}/tags/{tag_id}"  # rename / delete
# Membership: add = POST .../athletes {"Value": athleteId}; remove = DELETE
# .../athletes/{athleteId}. One athlete per call (verified live).
_TAG_ATHLETES = "/coaches/v1/coaches/{coach_id}/tags/{tag_id}/athletes"
_TAG_ATHLETE = "/coaches/v1/coaches/{coach_id}/tags/{tag_id}/athletes/{athlete_id}"


async def _coach_id(client: TPClient) -> int | None:
    """personId of the token owner — the ``coachId`` for group endpoints.

    Groups are coach-scoped, so this deliberately ignores any athlete override
    (unlike ``ensure_athlete_id``): the grouping belongs to the coach, not to a
    targeted athlete.
    """
    user_data = await client._get_user_data()
    if not user_data:
        return None
    return user_data.get("personId")


def _slim_group(tag: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": tag.get("id"),
        "name": tag.get("name", ""),
        "athlete_count": len(tag.get("athleteIds") or []),
        "is_default": tag.get("isDefault", False),
    }


async def tp_list_groups() -> dict[str, Any]:
    """List the coach's athlete groups (TP tags).

    Returns:
        Dict with a ``groups`` list of ``{id, name, athlete_count, is_default}``.
    """
    async with TPClient() as client:
        coach_id = await _coach_id(client)
        if not coach_id:
            return {
                "isError": True,
                "error_code": "AUTH_INVALID",
                "message": "Could not resolve the coach account. Re-authenticate.",
            }

        response = await client.get(_TAGS_ENDPOINT.format(coach_id=coach_id))
        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }

        data = response.data if isinstance(response.data, list) else []
        groups = [_slim_group(t) for t in data if isinstance(t, dict)]
        return {"groups": groups, "count": len(groups)}


async def tp_list_athletes_in_group(group_id: str) -> dict[str, Any]:
    """List the athletes in one group, resolving athleteIds to names.

    Args:
        group_id: The group (tag) ID from tp_list_groups.

    Returns:
        Dict with the group name and an ``athletes`` list of
        ``{athlete_id, name}`` (names joined from the coach's roster).
    """
    try:
        gid = int(group_id)
    except (TypeError, ValueError):
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": f"group_id must be a numeric ID, got {group_id!r}.",
        }

    async with TPClient() as client:
        user_data = await client._get_user_data()
        if not user_data or not user_data.get("personId"):
            return {
                "isError": True,
                "error_code": "AUTH_INVALID",
                "message": "Could not resolve the coach account. Re-authenticate.",
            }
        coach_id = user_data.get("personId")

        response = await client.get(_TAGS_ENDPOINT.format(coach_id=coach_id))
        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }

        data = response.data if isinstance(response.data, list) else []
        tag = next((t for t in data
                    if isinstance(t, dict) and t.get("id") == gid), None)
        if tag is None:
            return {
                "isError": True,
                "error_code": "NOT_FOUND",
                "message": f"No athlete group with id {gid}. Use tp_list_groups.",
            }

        # Join athleteIds against the coach's roster (from /users/v3/user, the
        # same source tp_list_athletes uses) to attach names.
        roster = {a.get("athleteId"): a for a in user_data.get("athletes", [])}
        athletes = []
        for aid in (tag.get("athleteIds") or []):
            a = roster.get(aid)
            # name is None when the athlete is in the group but not in this
            # account's roster
            name = f"{a.get('firstName', '')} {a.get('lastName', '')}".strip() if a is not None else None
            athletes.append({"athlete_id": aid, "name": name})
        athletes.sort(key=lambda x: (x["name"] or "").lower())

        return {
            "group_id": gid,
            "name": tag.get("name", ""),
            "athletes": athletes,
            "count": len(athletes),
        }


async def _get_tag(client: TPClient, coach_id: int, gid: int) -> dict[str, Any] | None:
    """Fetch one tag by id from the read endpoint (for guards / round-trip)."""
    resp = await client.get(_TAGS_ENDPOINT.format(coach_id=coach_id))
    if resp.is_error or not isinstance(resp.data, list):
        return None
    return next((t for t in resp.data
                 if isinstance(t, dict) and t.get("id") == gid), None)


def _auth_envelope() -> dict[str, Any]:
    return {
        "isError": True,
        "error_code": "AUTH_INVALID",
        "message": "Could not resolve the coach account. Re-authenticate.",
    }


async def tp_create_group(name: str) -> dict[str, Any]:
    """Create a new athlete group.

    Args:
        name: The group name.

    Returns:
        Dict with the created group's id and name.
    """
    name = (name or "").strip()
    if not name:
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": "Group name must not be empty.",
        }
    async with TPClient() as client:
        coach_id = await _coach_id(client)
        if not coach_id:
            return _auth_envelope()
        # Write endpoint is v1 and the body key is "Value" (verified live).
        response = await client.post(
            _TAGS_WRITE.format(coach_id=coach_id), json={"Value": name})
        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }
        data = response.data if isinstance(response.data, dict) else {}
        return {
            "id": data.get("id"),
            "name": data.get("name", name),
            "group": _slim_group(data) if data else None,
        }


async def tp_rename_group(group_id: str, name: str) -> dict[str, Any]:
    """Rename an athlete group.

    Args:
        group_id: The group (tag) ID.
        name: The new name.

    Returns:
        Dict with the group id and new name.
    """
    try:
        gid = int(group_id)
    except (TypeError, ValueError):
        return {"isError": True, "error_code": "VALIDATION_ERROR",
                "message": f"group_id must be a numeric ID, got {group_id!r}."}
    name = (name or "").strip()
    if not name:
        return {"isError": True, "error_code": "VALIDATION_ERROR",
                "message": "Group name must not be empty."}
    async with TPClient() as client:
        coach_id = await _coach_id(client)
        if not coach_id:
            return _auth_envelope()
        tag = await _get_tag(client, coach_id, gid)
        if tag is None:
            return {"isError": True, "error_code": "NOT_FOUND",
                    "message": f"No athlete group with id {gid}. Use tp_list_groups."}
        if tag.get("isDefault"):
            return {"isError": True, "error_code": "FORBIDDEN",
                    "message": "The default group cannot be renamed."}
        response = await client.put(
            _TAG_WRITE.format(coach_id=coach_id, tag_id=gid), json={"Value": name})
        if response.is_error:
            return {"isError": True,
                    "error_code": response.error_code.value if response.error_code else "API_ERROR",
                    "message": response.message}
        return {"id": gid, "name": name}


async def tp_delete_group(group_id: str) -> dict[str, Any]:
    """Delete an athlete group. Destructive — removes the grouping (athletes are
    not deleted, only the group). The default group cannot be deleted.

    Args:
        group_id: The group (tag) ID.

    Returns:
        Dict confirming the deletion.
    """
    try:
        gid = int(group_id)
    except (TypeError, ValueError):
        return {"isError": True, "error_code": "VALIDATION_ERROR",
                "message": f"group_id must be a numeric ID, got {group_id!r}."}
    async with TPClient() as client:
        coach_id = await _coach_id(client)
        if not coach_id:
            return _auth_envelope()
        tag = await _get_tag(client, coach_id, gid)
        if tag is None:
            return {"isError": True, "error_code": "NOT_FOUND",
                    "message": f"No athlete group with id {gid}. Use tp_list_groups."}
        if tag.get("isDefault"):
            return {"isError": True, "error_code": "FORBIDDEN",
                    "message": "The default group cannot be deleted."}
        response = await client.delete(
            _TAG_WRITE.format(coach_id=coach_id, tag_id=gid))
        if response.is_error:
            return {"isError": True,
                    "error_code": response.error_code.value if response.error_code else "API_ERROR",
                    "message": response.message}
        return {"deleted": True, "id": gid, "name": tag.get("name", "")}


def _parse_ids(athlete_ids: Any) -> tuple[list[int], str | None]:
    """Coerce an athlete_ids input to a list[int]; return (ids, error_message)."""
    if not isinstance(athlete_ids, (list, tuple)) or not athlete_ids:
        return [], "athlete_ids must be a non-empty list of athlete IDs."
    out: list[int] = []
    for a in athlete_ids:
        try:
            out.append(int(a))
        except (TypeError, ValueError):
            return [], f"athlete_ids must be numeric; got {a!r}."
    return out, None


def _membership_result(
    gid: int, key: str, ok: list, errors: list
) -> dict[str, Any]:
    """Shape a membership mutation result. Sets ``isError`` only when EVERY
    athlete failed (none succeeded but some were attempted) — a partial success
    is reported plainly so the caller can act on what went through."""
    result: dict[str, Any] = {"group_id": gid, key: ok, "errors": errors}
    if errors and not ok:
        result["isError"] = True
        result["error_code"] = "API_ERROR"
        result["message"] = (
            f"None of the {len(errors)} athlete(s) could be {key}; "
            "see errors for per-athlete detail."
        )
    return result


async def tp_add_athletes_to_group(group_id: str, athlete_ids: list) -> dict[str, Any]:
    """Add one or more athletes to a group.

    Args:
        group_id: The group (tag) ID.
        athlete_ids: Athlete IDs to add.

    Returns:
        Dict with ``added`` and ``errors`` lists. When EVERY athlete failed
        (``added`` empty and ``errors`` non-empty) the result also carries
        ``isError`` so the caller can tell a total failure from a partial one.
    """
    try:
        gid = int(group_id)
    except (TypeError, ValueError):
        return {"isError": True, "error_code": "VALIDATION_ERROR",
                "message": f"group_id must be a numeric ID, got {group_id!r}."}
    ids, err = _parse_ids(athlete_ids)
    if err:
        return {"isError": True, "error_code": "VALIDATION_ERROR", "message": err}

    async with TPClient() as client:
        coach_id = await _coach_id(client)
        if not coach_id:
            return _auth_envelope()
        tag = await _get_tag(client, coach_id, gid)
        if tag is None:
            return {"isError": True, "error_code": "NOT_FOUND",
                    "message": f"No athlete group with id {gid}. Use tp_list_groups."}
        if tag.get("isDefault"):
            return {"isError": True, "error_code": "FORBIDDEN",
                    "message": "Membership of the default group is managed by TP, "
                               "not editable here."}
        endpoint = _TAG_ATHLETES.format(coach_id=coach_id, tag_id=gid)
        added, errors = [], []
        for aid in ids:
            # One athlete per call: POST body {"Value": <athleteId>} (verified).
            r = await client.post(endpoint, json={"Value": aid})
            if r.is_error:
                errors.append({"athlete_id": aid, "message": r.message})
            else:
                added.append(aid)
        return _membership_result(gid, "added", added, errors)


async def tp_move_athletes_between_groups(
    from_group_id: str, to_group_id: str, athlete_ids: list
) -> dict[str, Any]:
    """Move one or more athletes from one athlete group to another.

    TP has no native "move" endpoint — group membership is a many-to-many
    tag relation, so this is add-to-destination-then-remove-from-source
    under the hood, same as composing tp_add_athletes_to_group +
    tp_remove_athletes_from_group. The add happens FIRST for each athlete:
    if the follow-up remove fails, they end up in BOTH groups rather than
    in neither — safer than silently dropping them from the roster view.

    Args:
        from_group_id: The source group (tag) ID.
        to_group_id: The destination group (tag) ID.
        athlete_ids: Athlete IDs to move.

    Returns:
        Dict with ``moved`` (removed from source, added to destination),
        ``added_only`` (added to destination but the remove from source
        failed — still a member of both) and ``errors`` (could not even be
        added to the destination) lists. ``isError`` is set only when
        NOTHING succeeded in the destination group.
    """
    try:
        fid = int(from_group_id)
    except (TypeError, ValueError):
        return {"isError": True, "error_code": "VALIDATION_ERROR",
                "message": f"from_group_id must be a numeric ID, got {from_group_id!r}."}
    try:
        tid = int(to_group_id)
    except (TypeError, ValueError):
        return {"isError": True, "error_code": "VALIDATION_ERROR",
                "message": f"to_group_id must be a numeric ID, got {to_group_id!r}."}
    if fid == tid:
        return {"isError": True, "error_code": "VALIDATION_ERROR",
                "message": "from_group_id and to_group_id must be different groups."}
    ids, err = _parse_ids(athlete_ids)
    if err:
        return {"isError": True, "error_code": "VALIDATION_ERROR", "message": err}

    async with TPClient() as client:
        coach_id = await _coach_id(client)
        if not coach_id:
            return _auth_envelope()

        # One GET covers both tags — cheaper than two _get_tag round-trips.
        resp = await client.get(_TAGS_ENDPOINT.format(coach_id=coach_id))
        if resp.is_error:
            return {"isError": True,
                    "error_code": resp.error_code.value if resp.error_code else "API_ERROR",
                    "message": resp.message}
        data = resp.data if isinstance(resp.data, list) else []
        by_id = {t.get("id"): t for t in data if isinstance(t, dict)}

        from_tag = by_id.get(fid)
        if from_tag is None:
            return {"isError": True, "error_code": "NOT_FOUND",
                    "message": f"No athlete group with id {fid} (from_group_id). "
                               "Use tp_list_groups."}
        to_tag = by_id.get(tid)
        if to_tag is None:
            return {"isError": True, "error_code": "NOT_FOUND",
                    "message": f"No athlete group with id {tid} (to_group_id). "
                               "Use tp_list_groups."}
        if from_tag.get("isDefault") or to_tag.get("isDefault"):
            return {"isError": True, "error_code": "FORBIDDEN",
                    "message": "Membership of the default group is managed by TP, "
                               "not editable here."}

        add_endpoint = _TAG_ATHLETES.format(coach_id=coach_id, tag_id=tid)
        moved: list[int] = []
        added_only: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for aid in ids:
            add_r = await client.post(add_endpoint, json={"Value": aid})
            if add_r.is_error:
                errors.append({"athlete_id": aid, "message": add_r.message})
                continue
            remove_r = await client.delete(
                _TAG_ATHLETE.format(coach_id=coach_id, tag_id=fid, athlete_id=aid))
            if remove_r.is_error:
                added_only.append({"athlete_id": aid, "message": remove_r.message})
            else:
                moved.append(aid)

        result: dict[str, Any] = {
            "from_group_id": fid, "to_group_id": tid,
            "moved": moved, "added_only": added_only, "errors": errors,
        }
        if errors and not moved and not added_only:
            result["isError"] = True
            result["error_code"] = "API_ERROR"
            result["message"] = (
                f"None of the {len(errors)} athlete(s) could be added to group {tid}; "
                "see errors for per-athlete detail."
            )
        return result


async def tp_remove_athletes_from_group(group_id: str, athlete_ids: list) -> dict[str, Any]:
    """Remove one or more athletes from a group.

    Args:
        group_id: The group (tag) ID.
        athlete_ids: Athlete IDs to remove.

    Returns:
        Dict with ``removed`` and ``errors`` lists. When EVERY athlete failed
        (``removed`` empty and ``errors`` non-empty) the result also carries
        ``isError`` so the caller can tell a total failure from a partial one.
    """
    try:
        gid = int(group_id)
    except (TypeError, ValueError):
        return {"isError": True, "error_code": "VALIDATION_ERROR",
                "message": f"group_id must be a numeric ID, got {group_id!r}."}
    ids, err = _parse_ids(athlete_ids)
    if err:
        return {"isError": True, "error_code": "VALIDATION_ERROR", "message": err}

    async with TPClient() as client:
        coach_id = await _coach_id(client)
        if not coach_id:
            return _auth_envelope()
        tag = await _get_tag(client, coach_id, gid)
        if tag is None:
            return {"isError": True, "error_code": "NOT_FOUND",
                    "message": f"No athlete group with id {gid}. Use tp_list_groups."}
        if tag.get("isDefault"):
            return {"isError": True, "error_code": "FORBIDDEN",
                    "message": "Membership of the default group is managed by TP, "
                               "not editable here."}
        removed, errors = [], []
        for aid in ids:
            r = await client.delete(
                _TAG_ATHLETE.format(coach_id=coach_id, tag_id=gid, athlete_id=aid))
            if r.is_error:
                errors.append({"athlete_id": aid, "message": r.message})
            else:
                removed.append(aid)
        return _membership_result(gid, "removed", removed, errors)
