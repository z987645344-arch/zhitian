# -*- coding: utf-8 -*-
# 文档解析层：负责将本地文档提取为纯文本，不写入记忆层

import os
import re

import pdfplumber
from docx import Document

from layers.pdf_text import extract_pdf_page_text


LONG_PARAGRAPH_RATIO = 1.5
SENTENCE_END_PATTERN = re.compile(r"[^。！？.!?]+[。！？.!?]*")


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

    return [chunk for chunk in chunks if chunk.strip()]


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
    texts = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text = extract_pdf_page_text(page)
            if text.strip():
                texts.append(text)
    return "\n\n".join(texts)


def _read_docx(file_path: str) -> str:
    document = Document(file_path)
    return "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
