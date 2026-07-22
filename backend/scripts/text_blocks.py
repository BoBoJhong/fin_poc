from __future__ import annotations

import re
from collections.abc import Iterable


BOUNDARY_CHARS = frozenset(".!?。！？;；:")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _boundary_near(text: str, lower: int, upper: int, target: int) -> int:
    candidates = [
        index
        for index in range(lower, min(upper, len(text) - 1) + 1)
        if text[index - 1] in BOUNDARY_CHARS or text[index].isspace()
    ]
    if not candidates:
        return target
    return min(
        candidates,
        key=lambda index: (
            abs(index - target),
            0 if text[index - 1] in BOUNDARY_CHARS else 1,
        ),
    )


def split_long_text(text: str, max_chars: int) -> list[str]:
    """Split normalized text at nearby semantic boundaries with a strict size cap."""
    if max_chars < 32:
        raise ValueError("max_chars must be at least 32")
    remaining = _normalize(text)
    parts: list[str] = []
    while len(remaining) > max_chars:
        lower = max_chars // 2
        boundary = _boundary_near(remaining, lower, max_chars, max_chars)
        part = remaining[:boundary].strip()
        if not part:
            boundary = max_chars
            part = remaining[:boundary].strip()
        parts.append(part)
        remaining = remaining[boundary:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def _rebalance_short_blocks(
    blocks: list[str], max_chars: int, min_chars: int, separator: str
) -> list[str]:
    if not min_chars or len(blocks) < 2:
        return blocks
    attempts = 0
    index = 0
    while index < len(blocks) and attempts < len(blocks) * 4:
        attempts += 1
        if len(blocks[index]) >= min_chars:
            index += 1
            continue
        neighbor = index + 1 if index + 1 < len(blocks) else index - 1
        left_index = min(index, neighbor)
        right_index = max(index, neighbor)
        combined = f"{blocks[left_index]}{separator}{blocks[right_index]}"
        if len(combined) <= max_chars:
            blocks[left_index : right_index + 1] = [combined]
            index = max(0, left_index - 1)
            continue

        lower = max(min_chars, len(combined) - max_chars)
        upper = min(max_chars, len(combined) - min_chars)
        if lower > upper:
            index += 1
            continue
        boundary = _boundary_near(combined, lower, upper, len(combined) // 2)
        left = combined[:boundary].strip()
        right = combined[boundary:].strip()
        if not left or not right:
            index += 1
            continue
        blocks[left_index : right_index + 1] = [left, right]
        index = max(0, left_index - 1)
    return blocks


def build_semantic_blocks(
    units: Iterable[str],
    *,
    max_chars: int,
    min_chars: int = 0,
    separator: str = "\n",
) -> list[str]:
    """Build ordered, bounded blocks without dropping any non-whitespace input unit."""
    if min_chars < 0 or min_chars > max_chars // 2:
        raise ValueError("min_chars must be between 0 and half of max_chars")
    pieces = [
        piece
        for unit in units
        if (normalized := _normalize(unit))
        for piece in split_long_text(normalized, max_chars)
    ]
    blocks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}{separator}{piece}" if current else piece
        if current and len(candidate) > max_chars:
            blocks.append(current)
            current = piece
        else:
            current = candidate
    if current:
        blocks.append(current)
    blocks = _rebalance_short_blocks(blocks, max_chars, min_chars, separator)
    if any(len(block) > max_chars for block in blocks):
        raise AssertionError("semantic block builder exceeded max_chars")
    return blocks
