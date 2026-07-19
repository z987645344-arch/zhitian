# -*- coding: utf-8 -*-

from layers.pdf_text import extract_pdf_page_text, normalize_pdf_text


class FakePage:
    width = 600

    def __init__(self, words, fallback="fallback"):
        self._words = words
        self._fallback = fallback

    def extract_text(self):
        return self._fallback

    def extract_words(self, **kwargs):
        return self._words


def _word(text, x0, x1, top):
    return {
        "text": text,
        "x0": x0,
        "x1": x1,
        "top": top,
        "bottom": top + 10,
    }


def test_normalize_pdf_text_converts_compatibility_ideographs():
    assert normalize_pdf_text("⾼级软件⼯程师") == "高级软件工程师"


def test_normalize_pdf_text_preserves_chinese_punctuation_style():
    source = "中文，English；数字１２３：完成！"
    assert normalize_pdf_text(source) == "中文，English；数字123：完成！"


def test_extract_pdf_page_text_reads_clear_columns_separately():
    words = []
    for index in range(5):
        top = 20 + index * 20
        words.append(_word("左%s" % index, 40, 100, top))
        words.append(_word("右%s" % index, 380, 440, top))
    page = FakePage(words, fallback="左0 右0\n左1 右1")

    assert extract_pdf_page_text(page).splitlines() == [
        "左0", "左1", "左2", "左3", "左4",
        "右0", "右1", "右2", "右3", "右4",
    ]


def test_extract_pdf_page_text_falls_back_for_single_column():
    words = [_word("第%s行" % index, 40, 500, 20 + index * 20) for index in range(10)]
    page = FakePage(words, fallback="第一行，正常。\n第二行，正常。")

    assert extract_pdf_page_text(page) == "第一行，正常。\n第二行，正常。"
