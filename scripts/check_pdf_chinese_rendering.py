"""Container gate: unembedded CJK text must render, without data/model access."""
from io import BytesIO
import json

import pypdfium2 as pdfium
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, NumberObject, ArrayObject, StreamObject, TextStringObject

MIN_NONWHITE_RATIO = 0.003


def chinese_pdf_bytes() -> bytes:
    """Standard STSong CID font reference, deliberately no embedded font stream."""
    writer = PdfWriter()
    try:
        page = writer.add_blank_page(400, 200)
        system_info = DictionaryObject({NameObject('/Registry'): TextStringObject('Adobe'),
                                        NameObject('/Ordering'): TextStringObject('GB1'),
                                        NameObject('/Supplement'): NumberObject(0)})
        descendant = writer._add_object(DictionaryObject({
            NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/CIDFontType0'),
            NameObject('/BaseFont'): NameObject('/STSong-Light'),
            NameObject('/CIDSystemInfo'): system_info, NameObject('/DW'): NumberObject(1000),
        }))
        font = writer._add_object(DictionaryObject({
            NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type0'),
            NameObject('/BaseFont'): NameObject('/STSong-Light'), NameObject('/Encoding'): NameObject('/UniGB-UCS2-H'),
            NameObject('/DescendantFonts'): ArrayObject([descendant]),
        }))
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/Font'): DictionaryObject({NameObject('/F1'): font}),
        })
        text = '中文渲染测试资料文件字体'.encode('utf-16-be').hex()
        stream = StreamObject()
        stream.set_data(('BT /F1 24 Tf 32 120 Td <' + text + '> Tj ET').encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
        output = BytesIO()
        writer.write(output)
        return output.getvalue()
    finally:
        writer.close()


def nonwhite_ratio(image) -> float:
    # Pure Chinese fixture: Latin/numeric glyphs cannot disguise missing CJK text.
    rgb = image.convert('RGB')
    try:
        data = rgb.tobytes()
        dark = sum(max(data[i:i + 3]) < 245 for i in range(0, len(data), 3))
        return dark / (rgb.width * rgb.height)
    finally:
        rgb.close()


def verify_chinese_rendering() -> dict:
    with pdfium.PdfDocument(chinese_pdf_bytes()) as document:
        page = document[0]
        try:
            bitmap = page.render(scale=1.5, fill_color=(255, 255, 255, 255))
            try:
                image = bitmap.to_pil()
                try:
                    ratio = nonwhite_ratio(image)
                finally:
                    image.close()
            finally:
                bitmap.close()
        finally:
            page.close()
    if ratio <= MIN_NONWHITE_RATIO:
        raise RuntimeError(f'中文PDF渲染缺失: nonwhite_ratio={ratio:.6f}; 请检查API镜像的Noto CJK字体')
    return {'unembedded_cjk_rendered': True, 'nonwhite_ratio': ratio,
            'minimum_nonwhite_ratio': MIN_NONWHITE_RATIO}


if __name__ == '__main__':
    print(json.dumps(verify_chinese_rendering()))
