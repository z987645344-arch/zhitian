"""Renderer replacement keeps limits, encryption semantics and artifact shape."""
from io import BytesIO
from pathlib import Path
import zipfile

from PIL import Image
from pypdf import PdfReader
import pypdfium2 as pdfium
import pytest

from layers.file_processing.input_guard import is_encrypted
from layers.file_processing.models import FileProcessingRequest, FileTaskType
from layers.file_processing.pdf import _SMOKE_PDF, pdf_processor
from tests.pdf_fixtures import pdf_bytes


def test_builtin_smoke_pdf_is_strict_valid_and_contains_the_smoke_marker():
    reader = PdfReader(BytesIO(_SMOKE_PDF), strict=True)
    assert not reader.is_encrypted
    assert len(reader.pages) == 1
    assert reader.pages[0].extract_text().strip() == "File engine smoke test"
    assert pdf_processor.probe_ready().success


@pytest.mark.parametrize("kind,expected", [
    ("open", "encrypted_pdf"), ("owner", None), ("broken", "invalid_pdf"),
])
def test_open_password_owner_permissions_and_corruption_are_distinct(tmp_path, kind, expected):
    data = (b"%PDF-broken" if kind == "broken" else
            pdf_bytes(password="test-open" if kind == "open" else None, owner_only=kind == "owner"))
    path = tmp_path / "input.pdf"
    path.write_bytes(data)
    request = FileProcessingRequest(task_type=FileTaskType.RENDER_PAGES, source_format="pdf",
                                    source_paths=[str(path)], target_format="png",
                                    output_dir=str(tmp_path / "images"))
    assert is_encrypted(data, "pdf") == (kind == "open")
    validation = pdf_processor._validate_sources(request)
    assert (validation.error_type if validation else None) == expected
    if kind == "owner":
        result = pdf_processor._render_pages(str(path), str(tmp_path / "images"))
        assert result.success and result.page_count == 1
        assert Path(result.artifacts[0].output_path).is_file()


def test_render_and_pptx_use_15_scale_without_alpha_and_keep_page_order(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(pdf_bytes("Rendering fixture", pages=3, page_size=(200, 300)))
    result = pdf_processor._render_pages(str(source), str(tmp_path / "png"))
    assert result.success and result.page_count == 3
    assert [Path(item.output_path).name for item in result.artifacts] == ["page_1.png", "page_2.png", "page_3.png"]
    for artifact in result.artifacts:
        with Image.open(artifact.output_path) as image:
            assert image.size == (300, 450)
            assert image.mode == "RGB"
    pptx = tmp_path / "slides.pptx"
    pdf_processor._to_pptx(str(source), str(pptx))
    with zipfile.ZipFile(pptx) as archive:
        slides = [name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
        assert len(slides) == 3
        for name in archive.namelist():
            if name.startswith("ppt/media/"):
                with Image.open(BytesIO(archive.read(name))) as image:
                    assert image.size == (300, 450) and image.mode == "RGB"
    assert not list(tmp_path.glob("page_*.png"))


def test_page_pixel_check_runs_before_bitmap_allocation(tmp_path, monkeypatch):
    source = tmp_path / "large.pdf"
    source.write_bytes(pdf_bytes(page_size=(200, 300)))
    monkeypatch.setattr(pdfium.PdfPage, "render", lambda *a, **kw: pytest.fail("rendered before limit check"))
    with pdfium.PdfDocument(source) as document:
        page = document[0]
        try:
            # ceil(200*1.5) * ceil(300*1.5) == 135000.
            pdf_processor._validate_page_pixels(page, 1.5, 135000)
            with pytest.raises(ValueError, match="too_many_pixels"):
                pdf_processor._validate_page_pixels(page, 1.5, 134999)
        finally:
            page.close()
