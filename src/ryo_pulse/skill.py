"""Skill entry point: a JSON-in / JSON-out function plus an MCP-style tool definition.

Once the official RYO tool specification is confirmed, align TOOL_DEFINITION with it; the
engine and contract underneath do not need to change.
"""

from __future__ import annotations

from typing import Any

from .engine import classify
from .models import PulseRequest, PulseResult

TOOL_NAME = "scan_persistence"

TOOL_DEFINITION: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Read-only. Given a token and a window of scan_market snapshots, report whether the token "
        "keeps showing up under the same scan profile: persistent, emerging, transient, absent or "
        "insufficient. Failed or drifted scans lower coverage and never count as absence. "
        "Deterministic, with a replay_hash. No trading, no price prediction."
    ),
    "inputSchema": PulseRequest.model_json_schema(),
    "outputSchema": PulseResult.model_json_schema(),
    "annotations": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
}


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate arguments, classify, return the result as plain JSON."""
    return classify(PulseRequest.model_validate(arguments)).model_dump(mode="json")
