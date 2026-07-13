"""Discover TrainingPeaks zone calculation methods (int -> labels).

TP exposes NO endpoint listing zone-calculation METHOD NAMES, and the athlete
settings carry only the opaque `calculationMethod` int (no name). BUT the zone
calculator endpoint
    POST /trainingzones/v1/users/{userId}/{metric}/calculate/{method}
returns, for each method int, the zone bands WITH a `label` per zone and the
`calculationMethod` echoed back. Zone count + labels are method-intrinsic
(stable across athletes; only the boundaries depend on the input threshold), so
they fingerprint the method without any names endpoint.

This script enumerates the method ints per metric and prints count + labels +
boundary fractions + whether the threshold is DERIVED (returned != input =
test/field-test based → a direct threshold can't be set).

Run:  .venv/bin/python3.13 scripts/probe_zone_methods.py
Read-only (POST to the calculator only computes; nothing is saved).
"""
import asyncio
import json

from tp_mcp.client.http import TPClient

BODIES = {
    "power": {"LTPower": 250},
    "heartrate": {"LTHR": 160, "maxHR": 190, "restingHR": 50},
    "speed": {"speed": 4.0, "distance": 3000},
}
THR = {"power": 250, "heartrate": 160, "speed": 4.0}
RANGE = {"power": range(0, 9), "heartrate": list(range(0, 6)) + [31],
         "speed": list(range(0, 7)) + [14]}


async def probe(c, uid, metric, method):
    r = await c.post(f"/trainingzones/v1/users/{uid}/{metric}/calculate/{method}",
                     json={**BODIES[metric], "zoneType": method})
    if not r.success:
        return None
    d = r.data if isinstance(r.data, dict) else {}
    zones = d.get("zones") or []
    derived = d.get("lactateThreshold") or d.get("thresholdSpeed")
    thr = THR[metric]
    return {
        "method": method,
        "zone_count": len(zones),
        "labels": [z.get("label") for z in zones],
        "max_fracs": [round(z.get("maximumAsDouble", z.get("maximum", 0)) / thr, 3)
                      for z in zones],
        "derives_threshold": bool(derived) and abs(float(derived) - thr) / thr > 0.02,
    }


async def main():
    async with TPClient() as c:
        uid = (await c._get_user_data() or {}).get("userId")
        out = {}
        for metric in ("power", "heartrate", "speed"):
            out[metric] = {}
            for m in RANGE[metric]:
                res = await probe(c, uid, metric, m)
                if res:
                    out[metric][m] = res
        print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
