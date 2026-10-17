"""Helpers for safely consuming structured model output.

Local models are unreliable JSON producers. Every Phase 4 AI call must be able
to fall back to deterministic behaviour, so the shared parsing helpers here are
defensive by design: they never raise on malformed input, they bound the size of
everything they return, and they strip control characters so that model output
cannot smuggle formatting or invisible instructions into the UI.

Nothing in this module talks to a network or a model; it is pure text handling
so it can be unit-tested without any dependencies.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

MAX_TEXT_CHARS = 800
MAX_LIST_ITEMS = 12
MAX_LIST_ITEM_CHARS = 240

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def strip_control(text: str) -> str:
    """Remove control characters except tab/newline/carriage-return."""

    return _CONTROL_RE.sub("", text)


def clean_text(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    """Coerce ``value`` to a trimmed, control-free string of bounded length."""

    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " ".join(str(v) for v in value)
    if isinstance(value, dict):
        value = " ".join(f"{k}: {v}" for k, v in value.items())
    text = strip_control(str(value)).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def clean_list(
    value: Any,
    limit: int = MAX_LIST_ITEMS,
    item_max: int = MAX_LIST_ITEM_CHARS,
) -> List[str]:
    """Coerce ``value`` to a bounded list of bounded, non-empty strings."""

    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        value = [value]
    result: List[str] = []
    for item in value:
        text = clean_text(item, limit=item_max)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def extract_json_object(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """Best-effort extraction of a single JSON object from model output.

    Handles three shapes: a raw object, an object wrapped in a fenced code
    block, and an object embedded in surrounding prose. Returns ``None`` when no
    usable object can be recovered.
    """

    if not text:
        return None
    candidate = text.strip()

    parsed = _try_load(candidate)
    if isinstance(parsed, dict):
        return parsed

    fence = _FENCE_RE.search(candidate)
    if fence:
        parsed = _try_load(fence.group(1))
        if isinstance(parsed, dict):
            return parsed

    balanced = _first_balanced_object(candidate)
    if balanced is not None:
        parsed = _try_load(balanced)
        if isinstance(parsed, dict):
            return parsed

    return None


def _try_load(text: str) -> Optional[Any]:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _first_balanced_object(text: str) -> Optional[str]:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
