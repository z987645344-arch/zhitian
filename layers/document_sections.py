# -*- coding: utf-8 -*-
# 文档小节映射：只增加可选元数据，不改变原切片正文和编号

import json
import os
import re
from bisect import bisect_right

from docx import Document
from utils.logger import get_logger

logger = get_logger("document_sections")


def read_section_paths(value: str) -> set[tuple[str, ...]]:
    """缺失/损坏的可选字段失败即不补取，绝不推测所属小节。"""
    if not value:
        return set()
    try:
        paths = json.loads(value)
        if not isinstance(paths, list) or any(
            not isinstance(path, list) or not path
            or any(not isinstance(part, str) or not part.strip() for part in path)
            for path in paths
        ):
            raise ValueError("invalid section paths")
        return {tuple(path) for path in paths}
    except (ValueError, TypeError):
        logger.warning("小节元数据不可用：error_type=InvalidSectionPaths")
        return set()


def chunk_section_paths(text: str, chunks: list[str], *, source_path: str = "",
                        source_name: str = "", markdown: bool = False) -> list[str]:
    """返回与原切片一一对应的JSON标量；跨小节记录所有叶子路径。

    Markdown支持# / ##并忽略代码围栏；DOCX支持Title及Heading 1/2，
    读取同一批段落的样式，不向提取正文插入标题或前缀。
    """
    suffix = os.path.splitext(source_path or source_name)[1].lower()
    events = []
    title = os.path.splitext(os.path.basename(source_name or source_path))[0]
    if suffix == ".md" or markdown:
        offset = 0
        fence = None
        for line in text.splitlines(keepends=True):
            stripped = line.strip()
            marker = re.match(r"^(`{3,}|~{3,})", stripped)
            if marker:
                kind = marker.group(1)[0]
                if fence is None:
                    fence = (kind, len(marker.group(1)))
                elif kind == fence[0] and len(marker.group(1)) >= fence[1]:
                    fence = None
            elif fence is None:
                hit = re.match(r"^ {0,3}(#{1,2})\s+(.+?)\s*#*\s*$", line.rstrip("\r\n"))
                if hit:
                    events.append((offset, len(hit.group(1)), hit.group(2)))
            offset += len(line)
        if events and events[0][1] == 1:
            title = events[0][2]
    elif suffix == ".docx" and source_path:
        from layers.document_loader import iter_docx_text_blocks
        document = Document(source_path)
        offset = 0
        for block_text, style_id in iter_docx_text_blocks(document):
            if style_id == "Title":
                title = block_text.strip()
            hit = re.fullmatch(r"Heading([12])", style_id)
            if hit:
                events.append((offset, int(hit.group(1)), block_text.strip()))
            offset += len(block_text) + 1
    if not events or not title:
        return [""] * len(chunks)

    # 与chunk_text去空行、句子拼接后的正文对应，仅忽略排版空白；不改原文。
    normalize = lambda value: re.sub(r"\s+", "", value)
    normalized = normalize(text)
    boundaries = []
    headings = {}
    last_offset = 0
    normalized_offset = 0
    for offset, level, heading in events:
        if suffix == ".docx":
            # DOCX文档标题独立于Heading 1/2层级。
            headings[level] = heading
            headings = {key: val for key, val in headings.items() if key <= level}
            path = [title] + [headings[key] for key in sorted(headings)]
        else:
            if level == 1:
                headings = {1: heading}
            else:
                headings[2] = heading
            parent = headings.get(1, title)
            path = [title] + ([parent] if parent != title else []) + ([headings[2]] if 2 in headings else [])
        # 累加区间长度，避免标题很多时反复扫描整份文档前缀。
        normalized_offset += len(normalize(text[last_offset:offset]))
        last_offset = offset
        boundaries.append((normalized_offset, path))
    positions = [position for position, _path in boundaries]
    result = []
    cursor = 0
    for index, chunk in enumerate(chunks):
        needle = normalize(getattr(chunk, "source_text", chunk) if suffix == ".docx" else chunk)
        start = normalized.find(needle, cursor) if needle else -1
        if start < 0:
            logger.warning("切片小节映射不可用：chunk_index=%s error_type=ChunkPositionNotFound", index)
            result.append("")
            continue
        end = start + len(needle)
        paths = []
        first = max(0, bisect_right(positions, start) - 1)
        for n in range(first, len(boundaries)):
            position, path = boundaries[n]
            if position >= end:
                break
            stop = boundaries[n + 1][0] if n + 1 < len(boundaries) else len(normalized)
            if position < end and stop > start and path not in paths:
                paths.append(path)
        result.append(json.dumps(paths, ensure_ascii=False) if paths else "")
        cursor = end
    return result
