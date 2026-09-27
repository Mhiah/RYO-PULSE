"""Tiny field-path extractor, so the adapter reads only fields a human confirmed from a real response.

Syntax: dotted keys; `[]` fans out over a list. Examples: `candidates[].symbol`, `data.regime`.
"""

from __future__ import annotations

from typing import Any


class PathError(ValueError):
    pass


def extract(data: Any, path: str) -> list[Any]:
    if not path:
        raise PathError("empty path")
    current: list[Any] = [data]
    for part in path.split("."):
        fan = part.endswith("[]")
        key = part[:-2] if fan else part
        nxt: list[Any] = []
        for item in current:
            if key:
                if not isinstance(item, dict) or key not in item:
                    raise PathError(f"key '{key}' not found while reading '{path}'")
                item = item[key]
            if fan:
                if not isinstance(item, list):
                    raise PathError(f"'{key or '<root>'}' is not a list while reading '{path}'")
                nxt.extend(item)
            else:
                nxt.append(item)
        current = nxt
    return current


def suggest(data: Any, prefix: str = "", depth: int = 0) -> list[str]:
    """List candidate paths to string-ish values inside lists of objects (for `discover`)."""
    out: list[str] = []
    if depth > 6:
        return out
    if isinstance(data, dict):
        for k, v in data.items():
            out += suggest(v, f"{prefix}.{k}" if prefix else k, depth + 1)
    elif isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            for k, v in first.items():
                if isinstance(v, str):
                    out.append(f"{prefix}[].{k}")
            out += [p for p in suggest(first, f"{prefix}[]", depth + 1) if p not in out]
        elif isinstance(first, str):
            out.append(f"{prefix}[]")
    return out
