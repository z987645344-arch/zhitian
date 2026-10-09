from io import BytesIO
from pathlib import Path

from PIL import Image
from pypdf import PdfReader
import pytest
import yaml

from scripts import check_pdf_chinese_rendering as probe


def test_chinese_smoke_fixture_has_no_embedded_fonts_or_latin_text():
    reader = PdfReader(BytesIO(probe.chinese_pdf_bytes()), strict=True)
    assert len(reader.pages) == 1
    font = reader.pages[0]['/Resources']['/Font']['/F1'].get_object()
    assert font['/BaseFont'] == '/STSong-Light'
    descendant = font['/DescendantFonts'][0].get_object()
    assert '/FontDescriptor' not in descendant
    assert reader.pages[0].extract_text() == '中文渲染测试资料文件字体'


def test_nonwhite_measurement_detects_blank_without_alpha_or_latin_false_positive():
    with Image.new('RGB', (100, 100), 'white') as image:
        assert probe.nonwhite_ratio(image) == 0
        image.paste('black', (0, 0, 10, 10))
        assert probe.nonwhite_ratio(image) == 0.01


def test_probe_rejects_missing_chinese_rendering(monkeypatch):
    monkeypatch.setattr(probe, 'nonwhite_ratio', lambda image: 0)
    with pytest.raises(RuntimeError, match='中文PDF渲染缺失'):
        probe.verify_chinese_rendering()


def test_container_gate_runs_real_probe_and_installs_only_font_package():
    root = Path(__file__).resolve().parents[1]
    runtime = (root / 'Dockerfile').read_text(encoding='utf-8').split('AS runtime', 1)[1]
    assert 'fonts-noto-cjk' in runtime
    assert 'fontconfig \\' not in runtime and 'libreoffice-' not in runtime
    workflow = yaml.safe_load((root / '.github/workflows/container-ci.yml').read_text(encoding='utf-8'))
    step = next(s for s in workflow['jobs']['build-and-scan']['steps']
                if s['name'] == 'Verify unembedded Chinese PDF rendering')
    assert '--network none' in step['run']
    assert 'scripts/check_pdf_chinese_rendering.py' in step['run']
