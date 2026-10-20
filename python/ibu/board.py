"""The dispatcher board: each of your routes, and the bridges on it.

A route is BLOCKED when one of its bridges is raised, CHECK when we can't
vouch for one of them, and CLEAR only when every bridge on it is lowered.
Raise durations are history ("past raises here"), never a countdown."""

from __future__ import annotations

RANK = {"CLEAR": 0, "UNKNOWN": 1, "UP": 2}
ROUTE_STATUS = {"CLEAR": "CLEAR", "UNKNOWN": "CHECK", "UP": "BLOCKED"}


def bridge_state(states, bridge_id: str) -> dict:
    # A bridge we have nothing for is unknown, not lowered.
    return states.bridges.get(bridge_id) or {"state": "UNKNOWN", "revision": None, "hold": True}


def route_status(states, bridge_ids) -> str:
    worst = "CLEAR"
    for bridge_id in bridge_ids:
        state = bridge_state(states, bridge_id)["state"]
        if RANK.get(state, RANK["UNKNOWN"]) > RANK[worst]:
            worst = state if state in RANK else "UNKNOWN"
    return ROUTE_STATUS[worst]


def bridge_line(name: str, b: dict, duration: dict | None, now_ms: int) -> str:
    if b["state"] not in ("UP", "CLEAR"):
        return f"{name}: unknown, we can't vouch for it right now"
    since = ""
    if b["revision"] is not None:
        since = f" for {max(0, (now_ms - b['revision']) // 60000)} min"
    if b["state"] == "CLEAR":
        return f"{name}: lowered{since}"
    typical = ""
    if duration and duration.get("median_minutes") is not None:
        typical = f" (past raises here: median {duration['median_minutes']} min"
        if duration.get("p90_minutes") is not None:
            typical += f", 9 in 10 within {duration['p90_minutes']} min"
        typical += ")"
    return f"{name}: raised{since}{typical}"


def render(routes, states, names: dict, durations: dict, now_ms: int) -> str:
    lines = []
    for route in routes:
        lines.append(f"{route_status(states, route['bridges']):<8} {route['name']}")
        for bridge_id in route["bridges"]:
            lines.append("  " + bridge_line(names.get(bridge_id, bridge_id), bridge_state(states, bridge_id),
                                            durations.get(bridge_id), now_ms))
    return "\n".join(lines)
