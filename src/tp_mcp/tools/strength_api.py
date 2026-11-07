"""Shared plumbing for the Peaksware strength host (`api.peakswaresb.com`).

Strength workouts, Strength Builder templates and the account's own exercises
all live there, behind the same Bearer token as `tp_analyze_workout`. The
calendar tools in strength.py predate this module and keep their own inline
calls; the library and exercise tools go through here.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from tp_mcp.client import TPClient

logger = logging.getLogger("tp-mcp")

STRENGTH_API_BASE = "https://api.peakswaresb.com"
STRENGTH_TIMEOUT = 30.0
RX_BASE = f"{STRENGTH_API_BASE}/rx/activity/v1"


def err(code: str, message: str) -> dict[str, Any]:
    return {"isError": True, "error_code": code, "message": message}


class BadResponseError(Exception):
    """A 2xx whose body is not the JSON shape the endpoint documents."""


def body(r: httpx.Response) -> Any:
    try:
        return r.json()
    except ValueError as exc:
        raise BadResponseError(f"non-JSON body (HTTP {r.status_code})") from exc


def data(r: httpx.Response) -> dict[str, Any]:
    """The `data` object of a `{data, errors}` envelope; {} when absent/null."""
    b = body(r)
    if not isinstance(b, dict):
        raise BadResponseError(f"expected an object, got {type(b).__name__}")
    d = b.get("data")
    if d is None:
        return {}
    if not isinstance(d, dict):
        raise BadResponseError(f"expected `data` object, got {type(d).__name__}")
    return d


def headers(access: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://app.trainingpeaks.com",
        "Referer": "https://app.trainingpeaks.com/",
    }


def map_status(status: int, text: str, not_found: str) -> dict[str, Any]:
    if status == 401:
        return err("AUTH_EXPIRED", "Session expired. Run 'tp-mcp auth' to re-authenticate.")
    if status == 403:
        return err("AUTH_INVALID", "Access denied. Check permissions or re-authenticate.")
    if status == 404:
        return err("NOT_FOUND", not_found)
    if status == 429:
        return err("RATE_LIMITED", "Rate limited. Please wait before retrying.")
    return err("API_ERROR", f"Strength API error: {status} {text[:200]}")


def api_error(r: httpx.Response, not_found: str) -> dict[str, Any]:
    """Map a non-2xx response. Auth / not-found / rate-limit statuses win; only
    a validation rejection (400/422) surfaces the server's field errors."""
    if r.status_code in (400, 422):
        try:
            b = r.json()
            errs = b.get("errors") if isinstance(b, dict) else None
            if errs:
                return err("API_ERROR", f"Strength API rejected the request: {errs}")
        except ValueError:
            pass
    return map_status(r.status_code, r.text, not_found)


async def access_token(client: TPClient) -> tuple[int | None, str | None, dict[str, Any] | None]:
    athlete_id = await client.ensure_athlete_id()
    if not athlete_id:
        return None, None, err("AUTH_INVALID", "Could not get athlete ID. Re-authenticate.")
    token = await client._ensure_access_token()
    if not token.success:
        return None, None, err("AUTH_INVALID", token.message or "Failed to obtain access token.")
    access = client._token_cache.access_token
    if not access:
        return None, None, err("AUTH_INVALID", "No access token available. Re-authenticate.")
    return athlete_id, access, None


@dataclass
class RxSession:
    http: httpx.AsyncClient
    headers: dict[str, str]
    calendar_id: int  # the athlete chosen via the `athlete` parameter, else self

    async def call(self, method: str, url: str, payload: Any = None) -> httpx.Response:
        return await self.http.request(method, url, headers=self.headers, json=payload)


@asynccontextmanager
async def rx_session() -> AsyncIterator[tuple[RxSession | None, dict[str, Any] | None]]:
    """Yield (session, None), or (None, error envelope) when auth fails."""
    async with TPClient() as client:
        athlete_id, access, e = await access_token(client)
        if e or not athlete_id or not access:
            yield None, e or err("AUTH_INVALID", "No access token available.")
            return
        async with httpx.AsyncClient(timeout=STRENGTH_TIMEOUT) as h:
            yield RxSession(h, headers(access), athlete_id), None


async def run_rx(what: str, fn: Callable[[RxSession], Any]) -> dict[str, Any]:
    """Run `fn(session)` with auth, network and malformed-body failures mapped
    to error envelopes — never an exception out of a tool."""
    try:
        async with rx_session() as (rx, e):
            if e or rx is None:
                return e or err("AUTH_INVALID", "Authentication failed.")
            result: dict[str, Any] = await fn(rx)
            return result
    except httpx.TimeoutException:
        return err("NETWORK_ERROR", f"Strength {what} timed out.")
    except httpx.RequestError:
        logger.exception("Network error: strength %s", what)
        return err("NETWORK_ERROR", "A network error occurred.")
    except BadResponseError as exc:
        logger.warning("Strength %s: unexpected response: %s", what, exc)
        return err("API_ERROR", f"Strength API returned an unexpected response ({exc}).")
