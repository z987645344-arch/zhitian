"""Small deterministic PDFs built with the already pinned pypdf (no rendering library)."""
from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, NumberObject, StreamObject


def pdf_bytes(text="PDF fixture", pages=1, page_size=(595, 842), table=False,
              image_size=None, password=None, owner_only=False):
    writer = PdfWriter()
    try:
        font = writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }))
        for _ in range(pages):
            page = writer.add_blank_page(*page_size)
            resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
            commands = []
            if table:
                for x in (50, 200, 350):
                    commands.append(f"{x} {page_size[1]-50} m {x} {page_size[1]-150} l S")
                for y in (50, 100, 150):
                    commands.append(f"50 {page_size[1]-y} m 350 {page_size[1]-y} l S")
                labels = ((70, 80, "Item"), (220, 80, "Value"),
                          (70, 130, "Model"), (220, 130, "42"))
            else:
                labels = [(72, 72, text)] if text else []
            for x, y, label in labels:
                escaped = label.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                commands.append(f"BT /F1 11 Tf {x} {page_size[1]-y} Td ({escaped}) Tj ET")
            if image_size:
                width, height = image_size
                image = StreamObject()
                image.set_data(b"\xff\xff\xff" * width * height)
                image.update({NameObject("/Type"): NameObject("/XObject"),
                              NameObject("/Subtype"): NameObject("/Image"),
                              NameObject("/Width"): NumberObject(width),
                              NameObject("/Height"): NumberObject(height),
                              NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                              NameObject("/BitsPerComponent"): NumberObject(8)})
                resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Im1"): writer._add_object(image)})
                commands.append("q 20 0 0 20 0 0 cm /Im1 Do Q")
            page[NameObject("/Resources")] = resources
            content = StreamObject()
            content.set_data("\n".join(commands).encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(content)
        if password or owner_only:
            writer.encrypt(password or "", owner_password="synthetic-owner", algorithm="AES-256")
        output = BytesIO()
        writer.write(output)
        return output.getvalue()
    finally:
        writer.close()
