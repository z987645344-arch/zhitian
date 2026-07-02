# -*- coding: utf-8 -*-
# 文档解析层：负责将本地文档提取为纯文本，不写入记忆层

import os

import pdfplumber
from docx import Document


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
    """按字符数切片，保留overlap避免上下文断裂。"""
    if not text:
        return []

    safe_chunk_size = max(1, int(chunk_size))
    safe_overlap = max(0, min(int(overlap), safe_chunk_size - 1))
    step = safe_chunk_size - safe_overlap
    chunks = []

    start = 0
    while start < len(text):
        chunk = text[start:start + safe_chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        start += step
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
            text = page.extract_text() or ""
            if text.strip():
                texts.append(text)
    return "\n\n".join(texts)


def _read_docx(file_path: str) -> str:
    document = Document(file_path)
    return "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
