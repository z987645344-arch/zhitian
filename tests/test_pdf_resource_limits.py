"""PDF页数及图像像素上限必须在产生部分结果前拒绝。"""

from PIL import Image
from tests.pdf_fixtures import pdf_bytes
import pytest

import config
from layers.file_processing.models import (
    FileArtifact,
    FileProcessingRequest,
    FileTaskType,
    QualityProfile,
)
from layers.file_processing.pdf import pdf_processor
from layers.file_processing.quality import FileQualityChecker


def _write_pdf(path, pages=1, page_size=(612, 792), image_size=None):
    path.write_bytes(pdf_bytes(text="", pages=pages, page_size=page_size, image_size=image_size))


def test_pdf_page_cap_rejects_before_extraction_or_partial_render(tmp_path, monkeypatch):
    source = tmp_path / "too-many-pages.pdf"
    output = tmp_path / "rendered"
    _write_pdf(source, pages=2)
    monkeypatch.setattr(config, "MAX_PDF_PROCESSING_PAGES", 1)

    result = pdf_processor.execute(
        FileProcessingRequest(
            task_type=FileTaskType.RENDER_PAGES,
            source_paths=[str(source)],
            source_format="pdf",
            target_format="png",
            output_dir=str(output),
        )
    )

    assert result.success is False
    assert result.error_type == "too_many_pages"
    assert not output.exists()


def test_oversized_embedded_image_is_rejected_without_partial_render(tmp_path, monkeypatch):
    source = tmp_path / "too-large-image.pdf"
    output = tmp_path / "rendered"
    _write_pdf(source, page_size=(20, 20), image_size=(40, 40))
    monkeypatch.setattr(config, "MAX_PDF_PROCESSING_PAGES", 200)
    monkeypatch.setattr(config, "MAX_IMAGE_PIXELS", 1000)

    result = pdf_processor.execute(
        FileProcessingRequest(
            task_type=FileTaskType.RENDER_PAGES,
            source_paths=[str(source)],
            source_format="pdf",
            target_format="png",
            output_dir=str(output),
        )
    )

    assert result.success is False
    assert result.error_type == "too_many_pixels"
    assert not output.exists()


def test_png_quality_check_rejects_pixels_over_limit(tmp_path, monkeypatch):
    image_path = tmp_path / "oversized.png"
    with Image.new("RGB", (40, 40)) as image:
        image.save(image_path)
    monkeypatch.setattr(config, "MAX_IMAGE_PIXELS", 1000)
    artifact = FileArtifact(
        output_path=str(image_path),
        file_format="png",
        mime_type="image/png",
    )

    result = FileQualityChecker().validate(artifact, QualityProfile.PNG)

    assert result.passed is False
    assert [issue.code for issue in result.issues] == ["too_many_pixels"]


def test_pdf_quality_check_rejects_page_count_over_limit(tmp_path, monkeypatch):
    pdf_path = tmp_path / "oversized.pdf"
    _write_pdf(pdf_path, pages=2)
    monkeypatch.setattr(config, "MAX_PDF_PROCESSING_PAGES", 1)
    artifact = FileArtifact(
        output_path=str(pdf_path),
        file_format="pdf",
        mime_type="application/pdf",
    )

    result = FileQualityChecker().validate(artifact, QualityProfile.PDF)

    assert result.passed is False
    assert [issue.code for issue in result.issues] == ["too_many_pages"]
