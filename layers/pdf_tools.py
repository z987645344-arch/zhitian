# -*- coding: utf-8 -*-
"""PDF合并/拆分核心；仅处理本地临时文件，不负责认证或持久化。"""

import os
from typing import List, Optional

from pydantic import BaseModel, Field
from pypdf import PdfReader, PdfWriter


class PdfOperationResult(BaseModel):
    success: bool
    output_paths: List[str] = Field(default_factory=list)
    page_count: int = 0
    error_type: Optional[str] = None


def _reader(path: str) -> PdfReader:
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ValueError("encrypted_pdf")
    return reader


def merge_pdfs(source_paths: List[str], output_path: str) -> PdfOperationResult:
    writer = PdfWriter()
    try:
        for source_path in source_paths:
            reader = _reader(source_path)
            for page in reader.pages:
                writer.add_page(page)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as output_file:
            writer.write(output_file)
        return PdfOperationResult(
            success=True,
            output_paths=[output_path],
            page_count=len(writer.pages),
        )
    except ValueError as exc:
        return PdfOperationResult(success=False, error_type=str(exc))
    except Exception:
        return PdfOperationResult(success=False, error_type="invalid_pdf")
    finally:
        writer.close()


def split_pdf(
    source_path: str,
    output_dir: str,
    max_pages: int,
) -> PdfOperationResult:
    try:
        reader = _reader(source_path)
        page_count = len(reader.pages)
        if page_count > max_pages:
            return PdfOperationResult(
                success=False,
                page_count=page_count,
                error_type="too_many_pages",
            )
        os.makedirs(output_dir, exist_ok=True)
        output_paths = []
        for index, page in enumerate(reader.pages, start=1):
            output_path = os.path.join(output_dir, "page_%s.pdf" % index)
            writer = PdfWriter()
            try:
                writer.add_page(page)
                with open(output_path, "wb") as output_file:
                    writer.write(output_file)
            finally:
                writer.close()
            output_paths.append(output_path)
        return PdfOperationResult(
            success=True,
            output_paths=output_paths,
            page_count=page_count,
        )
    except ValueError as exc:
        return PdfOperationResult(success=False, error_type=str(exc))
    except Exception:
        return PdfOperationResult(success=False, error_type="invalid_pdf")
