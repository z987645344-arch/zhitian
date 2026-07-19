# -*- coding: utf-8 -*-
"""Shared PDF text extraction helpers with conservative reading-order repair."""

import statistics
import unicodedata
from typing import Any, Dict, List, Optional


_PRESERVED_CJK_PUNCTUATION = set("，。；：！？（）【】《》“”‘’、")


def normalize_pdf_text(text: str) -> str:
    """Normalize compatibility glyphs while retaining Chinese punctuation style."""
    normalized_parts = []
    for character in text or "":
        if character in _PRESERVED_CJK_PUNCTUATION:
            normalized_parts.append(character)
        else:
            normalized_parts.append(unicodedata.normalize("NFKC", character))
    return "".join(normalized_parts)


def extract_pdf_page_text(page: Any) -> str:
    """Extract one page, reading clear columns independently when confidently detected."""
    fallback = page.extract_text() or ""
    try:
        words = page.extract_words(use_text_flow=False, keep_blank_chars=False) or []
        gutter = _find_clear_gutter(words, float(page.width))
        if gutter is None:
            return normalize_pdf_text(fallback)

        left_words = [word for word in words if _word_center(word) < gutter]
        right_words = [word for word in words if _word_center(word) >= gutter]
        ordered = "%s\n%s" % (
            _words_to_text(left_words),
            _words_to_text(right_words),
        )
        return normalize_pdf_text(ordered.strip())
    except (KeyError, TypeError, ValueError, statistics.StatisticsError):
        return normalize_pdf_text(fallback)


def _find_clear_gutter(words: List[Dict[str, Any]], page_width: float) -> Optional[float]:
    """Return a page-wide two-column gutter only when the evidence is strong."""
    usable = [word for word in words if str(word.get("text", "")).strip()]
    if len(usable) < 10 or page_width <= 0:
        return None

    minimum_side_words = max(4, int(len(usable) * 0.18))
    maximum_crossing_words = max(1, int(len(usable) * 0.04))
    best_gutter = None
    best_score = None
    for index in range(25, 76):
        gutter = page_width * index / 100.0
        left = [word for word in usable if float(word["x1"]) <= gutter]
        right = [word for word in usable if float(word["x0"]) >= gutter]
        crossing = [
            word
            for word in usable
            if float(word["x0"]) < gutter < float(word["x1"])
        ]
        if len(left) < minimum_side_words or len(right) < minimum_side_words:
            continue
        if len(crossing) > maximum_crossing_words:
            continue
        if _vertical_overlap_ratio(left, right) < 0.55:
            continue

        nearest_left = max(float(word["x1"]) for word in left)
        nearest_right = min(float(word["x0"]) for word in right)
        gap_width = nearest_right - nearest_left
        if gap_width < page_width * 0.035:
            continue
        score = gap_width - len(crossing) * page_width * 0.02
        if best_score is None or score > best_score:
            best_score = score
            best_gutter = (nearest_left + nearest_right) / 2.0
    return best_gutter


def _vertical_overlap_ratio(
    left_words: List[Dict[str, Any]],
    right_words: List[Dict[str, Any]],
) -> float:
    left_top = min(float(word["top"]) for word in left_words)
    left_bottom = max(float(word["bottom"]) for word in left_words)
    right_top = min(float(word["top"]) for word in right_words)
    right_bottom = max(float(word["bottom"]) for word in right_words)
    overlap = max(0.0, min(left_bottom, right_bottom) - max(left_top, right_top))
    shorter_span = min(left_bottom - left_top, right_bottom - right_top)
    return overlap / shorter_span if shorter_span > 0 else 0.0


def _words_to_text(words: List[Dict[str, Any]]) -> str:
    if not words:
        return ""
    sorted_words = sorted(words, key=lambda word: (float(word["top"]), float(word["x0"])))
    heights = [max(1.0, float(word["bottom"]) - float(word["top"])) for word in sorted_words]
    line_tolerance = max(2.0, statistics.median(heights) * 0.45)
    lines: List[List[Dict[str, Any]]] = []
    line_tops: List[float] = []
    for word in sorted_words:
        top = float(word["top"])
        target_index = next(
            (index for index, line_top in enumerate(line_tops) if abs(top - line_top) <= line_tolerance),
            None,
        )
        if target_index is None:
            lines.append([word])
            line_tops.append(top)
        else:
            lines[target_index].append(word)

    output_lines = []
    for line in lines:
        line.sort(key=lambda word: float(word["x0"]))
        output = str(line[0]["text"])
        previous = line[0]
        for word in line[1:]:
            gap = float(word["x0"]) - float(previous["x1"])
            character_width = _average_character_width(previous)
            separator = " " if gap > max(2.0, character_width * 0.75) else ""
            output += separator + str(word["text"])
            previous = word
        output_lines.append(output)
    return "\n".join(output_lines)


def _word_center(word: Dict[str, Any]) -> float:
    return (float(word["x0"]) + float(word["x1"])) / 2.0


def _average_character_width(word: Dict[str, Any]) -> float:
    text = str(word.get("text", ""))
    return (float(word["x1"]) - float(word["x0"])) / max(1, len(text))
