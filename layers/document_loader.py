# -*- coding: utf-8 -*-
# 文档解析层：负责将本地文档提取为纯文本，不写入记忆层

import os
import re

from docx import Document
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

from layers.file_processing.models import FileProcessingRequest, FileTaskType
from layers.file_processing.pdf import pdf_processor as _pdf_processor_registration
from layers.file_processing.runtime import get_file_processor_registry


LONG_PARAGRAPH_RATIO = 1.5
SENTENCE_END_PATTERN = re.compile(r"[^。！？.!?]+[。！？.!?]*")


class _DocxText(str):
    """Plain text with transient table boundaries; nothing extra is persisted."""
    def __new__(cls, text, tables):
        value = super().__new__(cls, text)
        value.tables = tables
        return value


class _DocxChunk(str):
    """Keep original text for section mapping when a table header is repeated."""
    def __new__(cls, text, source_text):
        value = super().__new__(cls, text)
        value.source_text = source_text
        return value


def load_document(file_path: str) -> str:
    """读取本地文档并返回纯文本。"""
    if not file_path:
        return "错误：文件路径不能为空"
    if not os.path.isfile(file_path):
        return f"错误：文件不存在：{file_path}"

    suffix = os.path.splitext(file_path)[1].lower()
    try:
        if suffix in {".txt", ".md"}:
            return _read_text_file(file_path)
        if suffix == ".pdf":
            return _read_pdf(file_path)
        if suffix == ".docx":
            return _read_docx(file_path)
        return f"错误：不支持的文档格式：{suffix or '无扩展名'}"
    except Exception as e:
        return f"错误：文档解析失败：{e}"


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """按段落优先、句子兜底切片；overlap参数仅保留兼容旧调用。"""
    if not text:
        return []

    safe_chunk_size = max(1, int(chunk_size))
    long_paragraph_limit = int(safe_chunk_size * LONG_PARAGRAPH_RATIO)
    chunks = []
    current = ""

    for paragraph in _split_paragraphs(text):
        if len(paragraph) > long_paragraph_limit:
            if current.strip():
                chunks.append(current.strip())
                current = ""
            chunks.extend(_split_long_paragraph(paragraph, safe_chunk_size))
            continue

        if not current:
            current = paragraph
            continue

        candidate = f"{current}\n{paragraph}"
        if len(candidate) > safe_chunk_size:
            chunks.append(current.strip())
            current = paragraph
        else:
            current = candidate

    if current.strip():
        chunks.append(current.strip())

    chunks = [chunk for chunk in chunks if chunk.strip()]
    return _repeat_docx_headers(text, chunks, safe_chunk_size) if isinstance(text, _DocxText) else chunks


def _repeat_docx_headers(text, chunks, chunk_size):
    # 表格跨块后不能只剩值而丢掉列名。只在块从表体开始时补完整表头，
    # 不改正文的其他格式；超长表头不反复复制，也不截断其原始内容。
    normalize = lambda value: re.sub(r"\s+", "", value)
    normalized = normalize(text)
    cursor = 0
    result = []
    for chunk in chunks:
        needle = normalize(chunk)
        start = normalized.find(needle, cursor)
        headers = []
        if start >= 0:
            for body_start, end, header in text.tables:
                if body_start <= start < end and len(header) < chunk_size:
                    headers.append(header)
            cursor = start + len(needle)
        result.append(_DocxChunk("\n".join(headers + [chunk]), chunk) if headers else chunk)
    return result


def _split_paragraphs(text: str) -> list[str]:
    """兼容单换行和连续换行形成的段落边界。"""
    return [
        paragraph.strip()
        for paragraph in re.split(r"\r?\n+", text)
        if paragraph.strip()
    ]


def _split_long_paragraph(paragraph: str, chunk_size: int) -> list[str]:
    """长段落降级为句子边界切分，极端长句再硬切。"""
    chunks = []
    current = ""
    sentences = _split_sentences(paragraph)

    for sentence in sentences:
        if len(sentence) > chunk_size:
            if current.strip():
                chunks.append(current.strip())
                current = ""
            chunks.extend(_hard_split(sentence, chunk_size))
            continue

        candidate = f"{current}{sentence}" if current else sentence
        if len(candidate) > chunk_size:
            if current.strip():
                chunks.append(current.strip())
            current = sentence
        else:
            current = candidate

    if current.strip():
        chunks.append(current.strip())
    return [chunk for chunk in chunks if chunk.strip()]


def _split_sentences(paragraph: str) -> list[str]:
    """按常见中英文句末标点拆句，并保留句末标点。"""
    sentences = [
        match.group(0).strip()
        for match in SENTENCE_END_PATTERN.finditer(paragraph)
        if match.group(0).strip()
    ]
    return sentences or [paragraph.strip()]


def _hard_split(text: str, chunk_size: int) -> list[str]:
    """无可用语义边界时的最后兜底硬切。"""
    chunks = []
    start = 0
    while start < len(text):
        chunk = text[start:start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        start += chunk_size
    return chunks


def _read_text_file(file_path: str) -> str:
    encodings = ["utf-8", "utf-8-sig", "gbk"]
    last_error = None
    for encoding in encodings:
        try:
            with open(file_path, "r", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError as e:
            last_error = e
    raise last_error or UnicodeDecodeError("utf-8", b"", 0, 1, "decode failed")


def _read_pdf(file_path: str) -> str:
    request = FileProcessingRequest(
        task_type=FileTaskType.EXTRACT_TEXT,
        source_paths=[file_path],
        source_format="pdf",
    )
    processor, _ = get_file_processor_registry().resolve(request)
    result = processor.execute(request)
    if not result.success:
        raise ValueError(result.error_message or result.error_type or "PDF文件解析失败")
    return result.text


def _read_docx(file_path: str) -> str:
    document = Document(file_path)
    pieces, tables = [], []
    normalized_offset = 0
    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            if block.text.strip():
                pieces.append(block.text)
                normalized_offset += len(re.sub(r"\s+", "", block.text))
        else:
            rows = list(_docx_table_rows(block))
            if not rows:
                continue
            header_rows = []
            for row, is_header in rows:
                if not is_header:
                    break
                header_rows.append(row)
            # 未显式标注重复表头时，按常见表格约定保留首个非空行作列名。
            header_rows = header_rows or [rows[0][0]]
            header = "\n".join(header_rows)
            start = normalized_offset + len(re.sub(r"\s+", "", header))
            for row, _ in rows:
                pieces.append(row)
                normalized_offset += len(re.sub(r"\s+", "", row))
            tables.append((start, normalized_offset, header))
    return _DocxText("\n".join(pieces), tables)


def iter_docx_text_blocks(document):
    """Yield (text, paragraph style) in the same body order as extraction.

    Table content belongs to the surrounding body section, never promotes cell
    formatting to document headings. Shared by the section metadata mapper.
    """
    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            if block.text.strip():
                yield block.text, str(block.style.style_id if block.style else "")
        else:
            for text, _ in _docx_table_rows(block):
                yield text, ""


def _docx_table_rows(table):
    for row in table.rows:
        cells = []
        # row.cells repeats merged-cell aliases. Walk physical cells instead;
        # vertical continuations retain an empty column, not the restart text.
        for tc in row._tr.tc_lst:
            if tc.vMerge == "continue":
                cells.append("")
                continue
            cell = _Cell(tc, table)
            parts = []
            for block in cell.iter_inner_content():
                if isinstance(block, Paragraph):
                    content = " ".join(block.text.split())
                    if content:
                        parts.append(content)
                elif isinstance(block, Table):
                    parts.extend(text for text, _ in _docx_table_rows(block))
            cells.append(" ; ".join(parts))
        if any(cells):
            marker = row._tr.trPr.find(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tblHeader"
            ) if row._tr.trPr is not None else None
            header = marker is not None and marker.get(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val", "true"
            ) not in {"0", "false", "off"}
            yield " | ".join(cells).strip(), header
