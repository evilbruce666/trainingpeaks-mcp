"""Tests for tp_recalc_tss (upstream issue #70)."""

from unittest.mock import AsyncMock, patch

import pytest

from tp_mcp.client.http import APIResponse
from tp_mcp.tools.workouts import tp_recalc_tss


def _client(get_responses, post_response):
    inst = AsyncMock()
    inst.ensure_athlete_id = AsyncMock(return_value=123)
    inst.get = AsyncMock(side_effect=get_responses)
    inst.post = AsyncMock(return_value=post_response)
    return inst


@pytest.mark.asyncio
async def test_recalc_tss_posts_command_and_reports_before_after():
    before = APIResponse(success=True, data={"tssActual": 180.0, "tssSource": 1})
    after = APIResponse(success=True, data={"tssActual": 62.0, "tssSource": 4})
    inst = _client([before, after], APIResponse(success=True, data=None))
    with patch("tp_mcp.tools.workouts.TPClient") as mc:
        mc.return_value.__aenter__.return_value = inst
        result = await tp_recalc_tss(workout_id="555")
    assert result["success"] is True
    assert (result["tss_before"], result["tss_source_before"]) == (180.0, 1)
    assert (result["tss_after"], result["tss_source_after"]) == (62.0, 4)
    endpoint = inst.post.call_args[0][0]
    assert endpoint == "/fitness/v6/athletes/123/commands/workouts/555/recalctss"
    assert inst.post.call_args.kwargs["json"] == {"value": "heartRate"}


@pytest.mark.asyncio
async def test_recalc_tss_rejects_unconfirmed_source():
    result = await tp_recalc_tss(workout_id="555", source="power")
    assert result["isError"] is True
    assert result["error_code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_recalc_tss_api_error_is_reported():
    before = APIResponse(success=True, data={"tssActual": 180.0, "tssSource": 1})
    inst = _client([before], APIResponse(success=False, message="boom"))
    with patch("tp_mcp.tools.workouts.TPClient") as mc:
        mc.return_value.__aenter__.return_value = inst
        result = await tp_recalc_tss(workout_id="555")
    assert result["isError"] is True
