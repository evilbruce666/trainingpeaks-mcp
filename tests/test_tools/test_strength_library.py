"""Strength Builder templates in library folders, and the account's own exercises."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tp_mcp.client.http import APIResponse
from tp_mcp.tools.strength import tp_search_exercises
from tp_mcp.tools.strength_exercises import (
    tp_create_exercise,
    tp_delete_exercise,
    tp_get_exercise,
    tp_get_own_exercises,
    tp_update_exercise,
)
from tp_mcp.tools.strength_library import (
    tp_copy_strength_library_item,
    tp_create_strength_library_item,
    tp_delete_strength_library_item,
    tp_get_strength_library_item,
    tp_get_strength_library_items,
    tp_save_strength_workout_to_library,
    tp_schedule_strength_library_item,
    tp_update_strength_library_item,
)

BLOCKS = [{"type": "SingleExercise", "exercises": [{"id": "1", "sets": [{"Reps": "10"}, {"Reps": "10"}]}]}]


def _client(athlete_id=123456):
    c = AsyncMock()
    c.ensure_athlete_id = AsyncMock(return_value=athlete_id)
    c._ensure_access_token = AsyncMock(return_value=APIResponse(success=True))
    cache = MagicMock()
    cache.access_token = "tok"
    c._token_cache = cache
    return c


def resp(status, body):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    r.text = str(body)
    return r


def http(*responses, get=None):
    h = AsyncMock()
    h.request.side_effect = list(responses)
    if get is not None:
        h.get.return_value = get
    return h


async def run(h, fn, athlete_id=123456):
    with patch("tp_mcp.tools.strength_api.TPClient") as mtp:
        mtp.return_value.__aenter__.return_value = _client(athlete_id)
        with patch("tp_mcp.tools.strength_api.httpx.AsyncClient") as mh:
            mh.return_value.__aenter__.return_value = h
            return await fn()


def calls(h):
    return [(c.args[0], c.args[1].split("/rx/activity/v1/")[1], c.kwargs.get("json"))
            for c in h.request.call_args_list]


def draft():
    return {"data": {"id": "draft-uuid", "libraryId": 77, "title": "T",
                     "workoutLibraryItemType": "ActivityWorkoutTemplate",
                     "workout": {"id": "w-uuid", "workoutType": "StructuredStrength", "blocks": [],
                                 "snapshot": {"totalBlocks": 0}, "calendarId": None,
                                 "prescribedDate": None}}}


def stored(item_id=348054, blocks=None):
    return {"data": {"id": item_id, "libraryId": 77, "title": "База A",
                     "workoutLibraryItemType": "ActivityWorkoutTemplate",
                     "workout": {"id": "1", "title": "База A", "instructions": "старое",
                                 "workoutType": "StructuredStrength", "snapshot": {"totalSets": 1},
                                 "blocks": blocks if blocks is not None else [
                                     {"blockType": "SingleExercise", "title": "Присед",
                                      "prescriptions": [{"exercise": {"title": "Air Squat"},
                                                         "sets": [{"parameterValues": [
                                                             {"parameter": "Reps", "prescribedValue": "10"}]}]}]}]}}}


class TestCreate:
    @pytest.mark.asyncio
    async def test_fills_the_server_draft_and_saves_it(self):
        h = http(resp(200, draft()), resp(200, {"data": {"id": 348054}}))
        r = await run(h, lambda: tp_create_strength_library_item("77", " База A ", BLOCKS, "описание"))
        assert r == {"item_id": "348054", "library_id": "77", "title": "База A",
                     "total_blocks": 1, "total_sets": 2}
        (m1, u1, b1), (m2, u2, b2) = calls(h)
        assert (m1, u1) == ("POST", "libraryContent/workoutLibrary/create")
        assert b1 == {"libraryId": 77, "title": "База A", "workoutLibraryItemType": "ActivityWorkoutTemplate"}
        assert (m2, u2) == ("POST", "libraryContent/workoutLibrary/save")
        assert b2["id"] == "draft-uuid" and b2["libraryId"] == 77
        wk = b2["workout"]
        assert wk["id"] == "w-uuid" and wk["calendarId"] is None and wk["prescribedDate"] is None
        assert len(wk["blocks"][0]["prescriptions"][0]["sets"]) == 2
        assert wk["snapshot"]["totalSets"] == 2 and wk["instructions"] == "описание"

    @pytest.mark.asyncio
    async def test_validates_before_any_call(self):
        bad = [{"type": "SingleExercise", "exercises": [{"id": "1", "sets": []}]}]
        assert (await tp_create_strength_library_item("77", "T", bad))["error_code"] == "VALIDATION_ERROR"
        assert (await tp_create_strength_library_item("x", "T", BLOCKS))["error_code"] == "VALIDATION_ERROR"
        assert (await tp_create_strength_library_item("77", " ", BLOCKS))["error_code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_unknown_exercise_stops_before_the_draft(self):
        blocks = [{"type": "SingleExercise", "exercises": [{"id": "0", "sets": [{"Reps": "8"}]}]}]
        h = http(get=resp(404, "Could not find exercise"))
        r = await run(h, lambda: tp_create_strength_library_item("77", "T", blocks))
        assert r["error_code"] == "VALIDATION_ERROR"
        assert h.request.call_count == 0

    @pytest.mark.asyncio
    async def test_save_rejection_surfaces_server_errors(self):
        h = http(resp(200, draft()), resp(400, {"errors": {"workout.blocks": ["bad"]}}))
        r = await run(h, lambda: tp_create_strength_library_item("77", "T", BLOCKS))
        assert r["error_code"] == "API_ERROR" and "bad" in r["message"]

    @pytest.mark.asyncio
    async def test_missing_draft_is_an_error_not_a_blind_save(self):
        h = http(resp(200, {"data": {}}))
        r = await run(h, lambda: tp_create_strength_library_item("77", "T", BLOCKS))
        assert r["error_code"] == "API_ERROR" and h.request.call_count == 1

    @pytest.mark.asyncio
    async def test_auth_failure(self):
        r = await run(http(), lambda: tp_create_strength_library_item("77", "T", BLOCKS), athlete_id=None)
        assert r["error_code"] == "AUTH_INVALID"


class TestReadAndList:
    @pytest.mark.asyncio
    async def test_get_projects_the_template_without_execution_fields(self):
        r = await run(http(resp(200, stored())), lambda: tp_get_strength_library_item("348054"))
        assert r["item_id"] == "348054" and r["library_id"] == "77" and r["title"] == "База A"
        assert r["instructions"] == "старое" and r["blocks"][0]["title"] == "Присед"
        ex = r["blocks"][0]["exercises"][0]
        assert ex["sets"] == [{"prescribed": {"Reps": "10"}}]
        assert "date" not in r and "compliance_state" not in r and "compliance_percent" not in ex

    @pytest.mark.asyncio
    async def test_get_not_found(self):
        r = await run(http(resp(404, "nope")), lambda: tp_get_strength_library_item("1"))
        assert r["error_code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_list_keeps_strength_templates_only_sorted_by_title(self):
        def item(i, title, kind="ActivityWorkoutTemplate"):
            return {"id": i, "libraryId": 77, "title": title, "workoutLibraryItemType": kind,
                    "workout": {"blocks": [{"prescriptions": [
                        {"exercise": {"title": "Sled Push"}, "sets": [{}, {}]},
                        {"exercise": {"title": "Sled Push"}, "sets": [{}]}]}]}}
        raw = [item(2, "2.1 Б"), item(1, "1.1 А"), item(3, "x", kind="Other")]
        h = http(resp(200, raw))
        r = await run(h, lambda: tp_get_strength_library_items("77"))
        assert calls(h)[0][:2] == ("GET", "libraryContent/workoutLibrary/77")
        assert [i["item_id"] for i in r["items"]] == ["1", "2"] and r["count"] == 2
        assert r["items"][0]["exercises"] == ["Sled Push"] and r["items"][0]["total_sets"] == 3


    @pytest.mark.asyncio
    async def test_list_carries_block_titles(self):
        raw = [{"id": 1, "title": "1.1", "workoutLibraryItemType": "ActivityWorkoutTemplate",
                "workout": {"blocks": [{"title": "Разминка", "prescriptions": []},
                                       {"title": " ", "prescriptions": []},
                                       {"title": "Толкание саней", "prescriptions": []}]}}]
        r = await run(http(resp(200, raw)), lambda: tp_get_strength_library_items("77"))
        assert r["items"][0]["blocks"] == ["Разминка", "Толкание саней"]


class TestUpdate:
    @pytest.mark.asyncio
    async def test_replace_blocks_and_title_saves_under_the_same_id(self):
        h = http(resp(200, stored()), resp(200, {"data": {"id": 348054}}))
        r = await run(h, lambda: tp_update_strength_library_item("348054", blocks=BLOCKS, title="Новое"))
        assert r["item_id"] == "348054" and "warning" not in r
        assert r["changed"] == ["blocks (replace)", "title"] and r["total_sets"] == 2
        (_, u1, _), (_, u2, body) = calls(h)
        assert u1 == "libraryContent/workoutLibrary/items/348054"
        assert u2 == "libraryContent/workoutLibrary/save"
        assert body["id"] == 348054 and body["libraryId"] == 77 and body["title"] == "Новое"
        assert body["workout"]["title"] == "Новое" and body["workout"]["instructions"] == "старое"

    @pytest.mark.asyncio
    async def test_append_keeps_existing_blocks(self):
        h = http(resp(200, stored()), resp(200, {"data": {"id": 348054}}))
        r = await run(h, lambda: tp_update_strength_library_item("348054", blocks=BLOCKS, mode="append"))
        body = calls(h)[1][2]
        assert len(body["workout"]["blocks"]) == 2 and r["total_sets"] == 3

    @pytest.mark.asyncio
    async def test_empty_instructions_clear_the_description(self):
        h = http(resp(200, stored()), resp(200, {"data": {"id": 348054}}))
        await run(h, lambda: tp_update_strength_library_item("348054", instructions=""))
        assert calls(h)[1][2]["workout"]["instructions"] is None

    @pytest.mark.asyncio
    async def test_different_returned_id_warns_about_a_copy(self):
        h = http(resp(200, stored()), resp(200, {"data": {"id": 999}}))
        r = await run(h, lambda: tp_update_strength_library_item("348054", title="Новое"))
        assert "warning" in r

    @pytest.mark.asyncio
    async def test_nothing_to_update_and_bad_mode(self):
        assert (await tp_update_strength_library_item("1"))["error_code"] == "VALIDATION_ERROR"
        assert (await tp_update_strength_library_item("1", title="x", mode="merge"))["error_code"] == "VALIDATION_ERROR"
        assert (await tp_update_strength_library_item("1", title="  "))["error_code"] == "VALIDATION_ERROR"


class TestCopyAndSaveFromCalendar:
    @pytest.mark.asyncio
    async def test_copy_uses_the_original_title_by_default(self):
        h = http(resp(200, stored()), resp(200, {"data": {"id": 500, "title": "База A"}}))
        r = await run(h, lambda: tp_copy_strength_library_item("348054", "88"))
        assert r == {"item_id": "500", "library_id": "88", "title": "База A"}
        assert calls(h)[1] == ("POST", "LibraryContent/workoutLibrary/items/copy",
                               {"libraryId": 88, "workoutLibraryItemId": 348054, "title": "База A"})

    @pytest.mark.asyncio
    async def test_copy_with_title_skips_the_read(self):
        h = http(resp(200, {"data": {"id": 501}}))
        r = await run(h, lambda: tp_copy_strength_library_item("348054", "88", "Копия"))
        assert r["title"] == "Копия" and h.request.call_count == 1

    @pytest.mark.asyncio
    async def test_save_calendar_workout_into_a_folder(self):
        h = http(resp(200, {"data": {"id": 38406987, "title": "Ноги"}}), resp(200, {"data": {"id": 600}}))
        r = await run(h, lambda: tp_save_strength_workout_to_library("38406987", "77"))
        assert r == {"item_id": "600", "library_id": "77", "title": "Ноги"}
        assert calls(h)[0][:2] == ("GET", "workouts/38406987")
        assert calls(h)[1] == ("POST", "LibraryContent/workoutLibrary/createFromWorkout",
                               {"libraryId": 77, "title": "Ноги",
                                "workoutLibraryItemType": "ActivityWorkoutTemplate",
                                "workoutId": 38406987})


class TestScheduleAndDelete:
    @pytest.mark.asyncio
    async def test_schedule_applies_to_the_target_calendar(self):
        h = http(resp(200, {"data": {"id": 38406987, "prescribedDate": "2026-11-08", "title": "База A",
                                     "snapshot": {"totalBlocks": 1, "totalSets": 2}}}))
        r = await run(h, lambda: tp_schedule_strength_library_item("348054", "2026-11-08"), athlete_id=999)
        assert r["workout_id"] == "38406987" and r["date"] == "2026-11-08" and r["total_sets"] == 2
        assert calls(h)[0] == ("POST", "libraryContent/workoutLibrary/applyToCalendar",
                               {"calendarId": 999, "workoutLibraryItemId": 348054,
                                "prescribedDate": "2026-11-08", "prescribedStartTime": None,
                                "orderOnDay": None})

    @pytest.mark.asyncio
    async def test_schedule_validates_date_and_id(self):
        assert (await tp_schedule_strength_library_item("1", "8 Nov"))["error_code"] == "VALIDATION_ERROR"
        assert (await tp_schedule_strength_library_item("abc", "2026-11-08"))["error_code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_delete(self):
        h = http(resp(200, {"data": True}))
        assert await run(h, lambda: tp_delete_strength_library_item("348054")) == {
            "deleted": True, "item_id": "348054"}
        assert calls(h)[0][:2] == ("DELETE", "libraryContent/workoutLibrary/items/348054")


def own_ex(can_edit=True, **kw):
    d = {"id": "686317", "ownerId": 1, "title": "Тяга саней канатом", "videoUrl": "",
         "instructions": "", "parameters": [{"parameter": "DistanceMeters"}],
         "primaryMuscleGroups": ["Upper Back"], "secondaryMuscleGroups": [], "canEdit": can_edit}
    d.update(kw)
    return {"data": d}


class TestOwnExercises:
    @pytest.mark.asyncio
    async def test_create_fills_the_draft_and_puts_it(self):
        h = http(resp(200, {"data": {"id": "uuid", "ownerId": 1, "title": "", "parameters": [],
                                     "canEdit": True}}), resp(200, own_ex()))
        r = await run(h, lambda: tp_create_exercise("Тяга саней канатом", ["DistanceMeters"],
                                                    primary_muscle_groups=["Upper Back"]))
        assert r["id"] == "686317" and r["own"] is True and r["parameters"] == ["DistanceMeters"]
        (m1, u1, _), (m2, u2, body) = calls(h)
        assert (m1, u1, m2, u2) == ("POST", "exercises", "PUT", "exercises")
        assert body["id"] == "uuid" and body["title"] == "Тяга саней канатом"
        assert body["parameters"] == [{"parameter": "DistanceMeters"}]
        # The display name is sent as the server's enum, which is all it accepts.
        assert body["primaryMuscleGroups"] == ["UpperBack"]

    @pytest.mark.asyncio
    async def test_unknown_muscle_group_is_refused_offline(self):
        r = await tp_create_exercise("X", ["Reps"], primary_muscle_groups=["Back"])
        assert r["error_code"] == "VALIDATION_ERROR" and "UpperBack" in r["message"]
        r = await tp_update_exercise("1", secondary_muscle_groups=["Neck"])
        assert r["error_code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_create_validates_parameters_offline(self):
        assert (await tp_create_exercise("X", ["Repz"]))["error_code"] == "VALIDATION_ERROR"
        assert (await tp_create_exercise("X", []))["error_code"] == "VALIDATION_ERROR"
        assert (await tp_create_exercise(" ", ["Reps"]))["error_code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_update_changes_only_given_fields(self):
        h = http(resp(200, own_ex()), resp(200, own_ex(title="Новое")))
        r = await run(h, lambda: tp_update_exercise("686317", title="Новое"))
        assert r["changed"] == ["title"] and r["title"] == "Новое"
        body = calls(h)[1][2]
        assert body["title"] == "Новое" and body["parameters"] == [{"parameter": "DistanceMeters"}]

    @pytest.mark.asyncio
    async def test_built_in_exercises_are_refused_for_edit_and_delete(self):
        h = http(resp(200, own_ex(can_edit=False)))
        r = await run(h, lambda: tp_update_exercise("131", title="Присед"))
        assert r["error_code"] == "VALIDATION_ERROR" and h.request.call_count == 1
        h = http(resp(200, own_ex(can_edit=False)))
        r = await run(h, lambda: tp_delete_exercise("131"))
        assert r["error_code"] == "VALIDATION_ERROR" and h.request.call_count == 1

    @pytest.mark.asyncio
    async def test_delete_own(self):
        h = http(resp(200, own_ex()), resp(200, {}))
        r = await run(h, lambda: tp_delete_exercise("686317"))
        assert r["deleted"] is True and calls(h)[1][:2] == ("DELETE", "exercises/686317")

    @pytest.mark.asyncio
    async def test_get_and_list_own(self):
        r = await run(http(resp(200, own_ex())), lambda: tp_get_exercise("686317"))
        assert r["title"] == "Тяга саней канатом" and r["own"] is True and r["video_url"] is None
        lc = {"data": {"exercises": [
            {"exerciseId": "1", "title": "Air Squat", "canEdit": False},
            {"exerciseId": "686317", "title": "Тяга саней канатом", "canEdit": True,
             "searchAttributes": {"primaryMuscleGroups": ["Upper Back"]}}]}}
        r = await run(http(resp(200, lc)), tp_get_own_exercises)
        assert r == {"count": 1, "exercises": [{"id": "686317", "title": "Тяга саней канатом",
                                                "muscle_groups": ["Upper Back"]}]}

    @pytest.mark.asyncio
    async def test_search_merges_own_exercises_when_asked(self):
        lc = {"data": {"exercises": [{"exerciseId": "686317", "title": "Тяга саней канатом", "canEdit": True}]}}
        r = await run(http(resp(200, lc)), lambda: tp_search_exercises("тяга", include_own=True))
        assert r["exercises"] == [{"id": "686317", "title": "Тяга саней канатом", "video_url": None,
                                   "muscle_groups": [], "parameters": [], "own": True}]

    @pytest.mark.asyncio
    async def test_search_keeps_built_in_results_when_own_lookup_fails(self):
        r = await run(http(), lambda: tp_search_exercises("air squat", include_own=True), athlete_id=None)
        assert r["exercises"][0]["title"] == "Air Squat" and r["own_exercises_error"]


# ── Review findings (2026-11-07): each case below failed before its fix ──────


class TestReviewFindings:
    @pytest.mark.asyncio
    async def test_own_exercise_survives_the_limit_behind_many_built_in_matches(self):
        # «squat» has far more than 20 built-in matches; own ones were appended
        # last and cut by `limit`.
        lc = {"data": {"exercises": [{"exerciseId": "7001", "title": "My Squat Press", "canEdit": True}]}}
        r = await run(http(resp(200, lc)), lambda: tp_search_exercises("squat", include_own=True))
        assert r["count"] == 20 and r["exercises"][0]["id"] == "7001"
        r = await run(http(resp(200, {"data": {"exercises": [
            {"exerciseId": "7001", "title": "My Squat Press", "canEdit": True,
             "searchAttributes": {"primaryMuscleGroups": ["Quads"]}}]}})),
            lambda: tp_search_exercises("", muscle_group="quads", include_own=True))
        assert r["exercises"][0]["id"] == "7001"

    @pytest.mark.asyncio
    async def test_non_json_2xx_is_an_error_envelope_not_an_exception(self):
        bad = MagicMock(status_code=200, text="<html>")
        bad.json.side_effect = ValueError("not json")
        r = await run(http(bad), lambda: tp_get_strength_library_item("1"))
        assert r["error_code"] == "API_ERROR"
        r = await run(http(resp(200, {"data": ["unexpected"]})), lambda: tp_get_strength_library_item("1"))
        assert r["error_code"] == "API_ERROR"

    @pytest.mark.asyncio
    async def test_list_endpoint_wrapped_in_data_is_read_not_crashed(self):
        r = await run(http(resp(200, {"data": {"items": [
            {"id": 1, "title": "A", "workoutLibraryItemType": "ActivityWorkoutTemplate", "workout": {}}]}})),
            lambda: tp_get_strength_library_items("77"))
        assert r["count"] == 1

    @pytest.mark.asyncio
    async def test_search_survives_an_own_lookup_that_raises(self):
        with patch("tp_mcp.tools.strength_exercises.tp_get_own_exercises",
                   AsyncMock(side_effect=RuntimeError("boom"))):
            r = await tp_search_exercises("air squat", include_own=True)
        assert r["exercises"][0]["title"] == "Air Squat" and r["own_exercises_error"]

    @pytest.mark.asyncio
    async def test_exercise_lookup_with_empty_data_is_not_accepted_as_own(self):
        blocks = [{"type": "SingleExercise", "exercises": [{"id": "99999999", "sets": [{"Reps": "8"}]}]}]
        h = http(get=resp(200, {"data": None}))
        r = await run(h, lambda: tp_create_strength_library_item("77", "T", blocks))
        assert r["error_code"] == "VALIDATION_ERROR" and h.request.call_count == 0

    @pytest.mark.asyncio
    async def test_exercise_lookup_with_a_list_body_is_an_error_in_calendar_create(self):
        from tp_mcp.tools.strength import tp_create_strength_workout
        blocks = [{"type": "SingleExercise", "exercises": [{"id": "99999999", "sets": [{"Reps": "8"}]}]}]
        h = AsyncMock()
        h.get.return_value = resp(200, [1, 2])
        with patch("tp_mcp.tools.strength.TPClient") as mtp:
            mtp.return_value.__aenter__.return_value = _client()
            with patch("tp_mcp.tools.strength.httpx.AsyncClient") as mh:
                mh.return_value.__aenter__.return_value = h
                r = await tp_create_strength_workout(date="2027-01-06", title="x", blocks=blocks)
        assert r["error_code"] == "API_ERROR"
        h.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_auth_and_not_found_statuses_win_over_an_errors_body(self):
        r = await run(http(resp(401, {"errors": ["x"]})), lambda: tp_get_strength_library_item("1"))
        assert r["error_code"] == "AUTH_EXPIRED"
        r = await run(http(resp(404, {"errors": ["x"]})), lambda: tp_get_strength_library_item("1"))
        assert r["error_code"] == "NOT_FOUND"
        r = await run(http(resp(400, {"errors": {"title": ["bad"]}})), lambda: tp_delete_strength_library_item("1"))
        assert r["error_code"] == "API_ERROR" and "bad" in r["message"]

    def test_exercise_tools_take_no_athlete_parameter(self):
        from tp_mcp.server import TOOLS
        by = {t.name: t for t in TOOLS}
        for name in ("tp_get_exercise", "tp_get_own_exercises", "tp_create_exercise",
                     "tp_update_exercise", "tp_delete_exercise", "tp_search_exercises"):
            assert "athlete" not in by[name].input_schema["properties"], name
        assert "athlete" in by["tp_schedule_strength_library_item"].input_schema["properties"]

    @pytest.mark.asyncio
    async def test_inputs_of_the_wrong_shape_are_coerced_or_refused(self):
        h = http(resp(200, own_ex()), resp(200, own_ex(title="5")))
        r = await run(h, lambda: tp_update_exercise("686317", title=5))  # type: ignore[arg-type]
        assert r["title"] == "5"
        h = http(resp(200, {"data": {"id": "uuid", "parameters": [], "canEdit": True}}), resp(200, own_ex()))
        await run(h, lambda: tp_create_exercise("X", ["Reps"], primary_muscle_groups="Quads"))  # type: ignore[arg-type]
        assert calls(h)[1][2]["primaryMuscleGroups"] == ["Quads"]
        untitled = http(resp(200, {"data": {"id": 1, "title": ""}}))
        r = await run(untitled, lambda: tp_copy_strength_library_item("1", "2"))
        assert r["error_code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_exercise_id_with_spaces_resolves_and_builds(self):
        blocks = [{"type": "SingleExercise", "exercises": [{"id": " 686317 ", "sets": [{"Reps": "8"}]}]}]
        h = http(resp(200, draft()), resp(200, {"data": {"id": 5}}),
                 get=resp(200, {"data": {"id": "686317", "title": "Своё"}}))
        r = await run(h, lambda: tp_create_strength_library_item("77", "T", blocks))
        assert r["item_id"] == "5"
        presc = calls(h)[1][2]["workout"]["blocks"][0]["prescriptions"][0]
        assert presc["exercise"]["id"] == "686317" and presc["exercise"]["title"] == "Своё"


class TestReviewRound2:
    @pytest.mark.asyncio
    async def test_schedule_takes_only_a_plain_calendar_date(self):
        for bad in ("20261108", "2026-W45-7", "2026-11-8"):
            assert (await tp_schedule_strength_library_item("1", bad))["error_code"] == "VALIDATION_ERROR", bad

    @pytest.mark.asyncio
    async def test_stray_athlete_is_ignored_by_account_level_tools(self):
        from tp_mcp.client.context import athlete_override
        from tp_mcp.server import call_tool

        seen = []

        async def fake():
            seen.append(athlete_override.get())
            return {"count": 0, "exercises": []}

        with patch("tp_mcp.server.tp_get_own_exercises", fake):
            await call_tool("tp_get_own_exercises", {"athlete": "Nobody"})
        assert seen == [None]

    @pytest.mark.asyncio
    async def test_include_own_false_as_a_string_makes_no_live_call(self):
        from tp_mcp.server import call_tool
        with patch("tp_mcp.tools.strength_exercises.tp_get_own_exercises",
                   AsyncMock(side_effect=AssertionError("live call"))):
            await call_tool("tp_search_exercises", {"query": "air squat", "include_own": "false"})

    def test_malformed_shapes_are_refused_not_raised(self):
        from tp_mcp.tools.strength import _validate_blocks
        from tp_mcp.tools.strength_exercises import _check_params, _view
        assert _validate_blocks(["x"]) and _validate_blocks([{"exercises": ["1"]}])
        assert _validate_blocks([{"exercises": [{"id": None, "sets": [{"Reps": 1}]}]}])
        assert _check_params([["Reps"]])
        assert _view({"id": 1, "parameters": ["Reps"]})["parameters"] == ["Reps"]

    @pytest.mark.asyncio
    async def test_rows_without_ids_are_skipped(self):
        r = await run(http(resp(200, [{"title": "x", "workoutLibraryItemType": "ActivityWorkoutTemplate"}])),
                      lambda: tp_get_strength_library_items("77"))
        assert r["count"] == 0
        lc = {"data": {"exercises": [{"title": "no id", "canEdit": True}]}}
        assert (await run(http(resp(200, lc)), tp_get_own_exercises))["count"] == 0

    @pytest.mark.asyncio
    async def test_copy_of_a_missing_template_is_not_found(self):
        r = await run(http(resp(200, {"data": None})), lambda: tp_copy_strength_library_item("1", "2"))
        assert r["error_code"] == "NOT_FOUND"
