"""Health metrics tools: log, get, nutrition."""

import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from tp_mcp.client import TPClient
from tp_mcp.tools._validation import DateRangeInput, format_validation_error

logger = logging.getLogger("tp-mcp")

# Metric type IDs (confirmed from network captures)
METRIC_TYPES: dict[str, dict[str, Any]] = {
    "pulse": {"type": 5, "label": "Pulse", "units": "bpm", "formatedUnits": "bpm", "min": 10, "max": 200},
    "weight_kg": {"type": 9, "label": "Weight", "units": "kg", "formatedUnits": "kg", "min": 0, "max": 1000},
    "rmr": {"type": 15, "label": "RMR", "units": "kcal", "formatedUnits": "kcal", "min": 500, "max": 5000},
    "injury": {"type": 23, "label": "Injury", "units": "", "formatedUnits": "", "min": 1, "max": 10},
    "sleep_hours": {"type": 6, "label": "Sleep", "units": "hours", "formatedUnits": "hours", "min": 0, "max": 72},
    "hrv": {"type": 60, "label": "HRV", "units": "", "formatedUnits": "", "min": 0, "max": 200},
    "spo2": {"type": 53, "label": "SPO2", "units": "%", "formatedUnits": "%", "min": 0, "max": 100},
    "steps": {"type": 58, "label": "Steps", "units": "steps", "formatedUnits": "steps", "min": 0, "max": 1000000000},
}


class LogMetricsInput(BaseModel):
    """Validates input for logging metrics."""

    date: str
    weight_kg: float | None = Field(default=None, ge=0, le=1000)
    pulse: int | None = Field(default=None, ge=10, le=200)
    hrv: float | None = Field(default=None, ge=0, le=200)
    sleep_hours: float | None = Field(default=None, ge=0, le=72)
    spo2: float | None = Field(default=None, ge=0, le=100)
    steps: int | None = Field(default=None, ge=0, le=1000000000)
    rmr: int | None = Field(default=None, ge=500, le=5000)
    injury: int | None = Field(default=None, ge=1, le=10)
    upload_client: str | None = None
    record_id: str | None = None

    @field_validator("date")
    @classmethod
    def check_date(cls, v: str) -> str:
        from datetime import date

        date.fromisoformat(v)
        return v


async def tp_log_metrics(
    date: str,
    weight_kg: float | None = None,
    pulse: int | None = None,
    hrv: float | None = None,
    sleep_hours: float | None = None,
    spo2: float | None = None,
    steps: int | None = None,
    rmr: int | None = None,
    injury: int | None = None,
    upload_client: str | None = None,
    record_id: str | None = None,
) -> dict[str, Any]:
    """Log health metrics for a date.

    Args:
        date: Date in ISO format (YYYY-MM-DD).
        weight_kg: Body weight in kg.
        pulse: Resting pulse in bpm.
        hrv: Heart rate variability.
        sleep_hours: Hours of sleep.
        spo2: Blood oxygen saturation (%).
        steps: Step count.
        rmr: Resting metabolic rate (kcal).
        injury: Injury level (1-10).
        upload_client: Free-text source label attached to each detail (e.g.
            "WHOOP") — confirmed live (2026-07-15) that the API accepts and
            persists an arbitrary string here even though TP's own web UI
            only ever sets this for recognised partner integrations (shows
            as "Garmin Health" etc there). Omit to leave it null, matching
            the previous behaviour.
        record_id: The day's own consolidatedtimedmetric `id` (as returned by
            tp_get_metrics) — pass it to EDIT that day's record in place
            instead of creating a new one. Confirmed live: TP matches
            existing details by (time, type) within the record and updates
            matching ones (bumps modifiedTime, keeps the SAME group/
            parentId) rather than appending a duplicate — this only works
            when `date` matches the target record's day and this call's
            fields overlap one you already wrote (repeat calls use the
            same hardcoded T12:00:00 time, so same-day re-writes naturally
            land on the same slot). Omit (default) to always create a new
            record — the historical behaviour, safe for the normal
            one-write-per-day case.

    Returns:
        Dict with confirmation or error.
    """
    try:
        params = LogMetricsInput(
            date=date,
            weight_kg=weight_kg,
            pulse=pulse,
            hrv=hrv,
            sleep_hours=sleep_hours,
            spo2=spo2,
            steps=steps,
            rmr=rmr,
            injury=injury,
            upload_client=upload_client,
            record_id=record_id,
        )
    except (ValidationError, ValueError) as e:
        msg = format_validation_error(e) if isinstance(e, ValidationError) else str(e)
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": msg,
        }

    # Build details list from provided metrics
    metric_values: dict[str, float] = {}
    if params.weight_kg is not None:
        metric_values["weight_kg"] = params.weight_kg
    if params.pulse is not None:
        metric_values["pulse"] = float(params.pulse)
    if params.hrv is not None:
        metric_values["hrv"] = params.hrv
    if params.sleep_hours is not None:
        metric_values["sleep_hours"] = params.sleep_hours
    if params.spo2 is not None:
        metric_values["spo2"] = params.spo2
    if params.steps is not None:
        metric_values["steps"] = float(params.steps)
    if params.rmr is not None:
        metric_values["rmr"] = float(params.rmr)
    if params.injury is not None:
        metric_values["injury"] = float(params.injury)

    if not metric_values:
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": "At least one metric must be provided.",
        }

    async with TPClient() as client:
        athlete_id = await client.ensure_athlete_id()
        if not athlete_id:
            return {
                "isError": True,
                "error_code": "AUTH_INVALID",
                "message": "Could not get athlete ID. Re-authenticate.",
            }

        details = []
        for key, value in metric_values.items():
            meta = METRIC_TYPES[key]
            detail: dict[str, Any] = {
                "type": meta["type"],
                "label": meta["label"],
                "value": value,
                "time": f"{params.date}T12:00:00",
                "temporaryId": 0,
                "units": meta["units"],
                "formatedUnits": meta["formatedUnits"],
                "min": meta["min"],
                "max": meta["max"],
            }
            if params.upload_client is not None:
                detail["uploadClient"] = params.upload_client
            details.append(detail)

        payload = {
            "athleteId": athlete_id,
            "timeStamp": f"{params.date}T00:00:00",
            "id": params.record_id,
            "details": details,
        }

        endpoint = f"/metrics/v3/athletes/{athlete_id}/consolidatedtimedmetric"
        response = await client.post(endpoint, json=payload)

        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }

        return {
            "success": True,
            "date": params.date,
            "logged": list(metric_values.keys()),
        }


async def tp_get_metrics(start_date: str, end_date: str) -> dict[str, Any]:
    """Get health metrics for a date range.

    Args:
        start_date: Start date in ISO format (YYYY-MM-DD).
        end_date: End date in ISO format (YYYY-MM-DD).

    Returns:
        Dict with per-day metric values.
    """
    try:
        params = DateRangeInput(start_date=start_date, end_date=end_date)
    except (ValidationError, ValueError) as e:
        msg = format_validation_error(e) if isinstance(e, ValidationError) else str(e)
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": msg,
        }

    async with TPClient() as client:
        athlete_id = await client.ensure_athlete_id()
        if not athlete_id:
            return {
                "isError": True,
                "error_code": "AUTH_INVALID",
                "message": "Could not get athlete ID. Re-authenticate.",
            }

        start_str = params.start_date.isoformat()
        end_str = params.end_date.isoformat()
        endpoint = f"/metrics/v3/athletes/{athlete_id}/consolidatedtimedmetrics/{start_str}/{end_str}"
        response = await client.get(endpoint)

        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }

        if not response.data:
            return {
                "metrics": [],
                "count": 0,
                "message": "No metrics data for this date range.",
            }

        return {
            "metrics": response.data if isinstance(response.data, list) else [response.data],
            "count": len(response.data) if isinstance(response.data, list) else 1,
            "date_range": {"start": start_date, "end": end_date},
        }


async def tp_get_nutrition(start_date: str, end_date: str) -> dict[str, Any]:
    """Get nutrition data for a date range.

    Args:
        start_date: Start date in ISO format (YYYY-MM-DD).
        end_date: End date in ISO format (YYYY-MM-DD).

    Returns:
        Dict with nutrition data.
    """
    try:
        params = DateRangeInput(start_date=start_date, end_date=end_date)
    except (ValidationError, ValueError) as e:
        msg = format_validation_error(e) if isinstance(e, ValidationError) else str(e)
        return {
            "isError": True,
            "error_code": "VALIDATION_ERROR",
            "message": msg,
        }

    async with TPClient() as client:
        athlete_id = await client.ensure_athlete_id()
        if not athlete_id:
            return {
                "isError": True,
                "error_code": "AUTH_INVALID",
                "message": "Could not get athlete ID. Re-authenticate.",
            }

        start_str = params.start_date.isoformat()
        end_str = params.end_date.isoformat()
        endpoint = f"/fitness/v1/athletes/{athlete_id}/nutrition/{start_str}/{end_str}"
        response = await client.get(endpoint)

        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }

        if not response.data:
            return {
                "nutrition": [],
                "count": 0,
                "message": "No nutrition data for this date range.",
            }

        return {
            "nutrition": response.data if isinstance(response.data, list) else [response.data],
            "count": len(response.data) if isinstance(response.data, list) else 1,
            "date_range": {"start": start_date, "end": end_date},
        }
