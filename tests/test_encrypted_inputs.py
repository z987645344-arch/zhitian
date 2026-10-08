"""Password-to-open preflight: deterministic fixtures, no decryption or network."""

import io
import struct
import uuid

import fitz
import pytest

import main
from layers import auth, converter, document_loader, execution, task_store
from layers.file_processing.input_guard import (
    CompoundFile, ENCRYPTED_FILE_MESSAGE, EncryptedFileError, is_encrypted, reject_encrypted,
)
from layers.file_processing.models import FileEntry, FileProcessingRequest, FileTaskType
from layers.file_processing.runtime import get_file_processor_registry
from tests.conftest import grant_work_organization
from tests.test_document_upload import _docx_bytes, _office_zip_bytes


def compound_fixture(streams):
    """Minimal valid v3 CFB, regular streams; markers are format fixtures, not ciphertext."""
    header = bytearray(512)
    header[:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<4H", header, 26, 3, 0xFFFE, 9, 6)
    struct.pack_into("<I", header, 44, 1)
    struct.pack_into("<I", header, 48, 0)  # Directory sector.
    struct.pack_into("<I", header, 56, 4096)
    struct.pack_into("<I", header, 60, 0xFFFFFFFE)
    struct.pack_into("<I", header, 68, 0xFFFFFFFE)
    directory = bytearray(512)
    def entry(index, name, kind, start, size, child=0xFFFFFFFF, right=0xFFFFFFFF):
        offset = index * 128
        encoded = (name + "\0").encode("utf-16-le")
        directory[offset:offset + len(encoded)] = encoded
        struct.pack_into("<HBBIII", directory, offset + 64, len(encoded), kind, 1,
                         0xFFFFFFFF, right, child)
        struct.pack_into("<IQ", directory, offset + 116, start, size)
    entry(0, "Root Entry", 5, 0xFFFFFFFE, 0, child=1)
    sectors = [directory]
    fat = [0xFFFFFFFE]
    for index, (name, payload) in enumerate(streams.items(), 1):
        start = len(sectors)
        entry(index, name, 2, start, 4096,
              right=index + 1 if index < len(streams) else 0xFFFFFFFF)
        padded = payload.ljust(4096, b"\0")
        assert len(padded) == 4096
        sectors.extend(padded[i:i + 512] for i in range(0, 4096, 512))
        fat.extend([start + i + 1 for i in range(7)] + [0xFFFFFFFE])
    fat_sector = len(sectors)
    fat.append(0xFFFFFFFD)
    struct.pack_into("<109I", header, 76, fat_sector, *([0xFFFFFFFF] * 108))
    sectors.append(struct.pack("<128I", *(fat + [0xFFFFFFFF] * (128 - len(fat)))))
    return bytes(header) + b"".join(sectors)


def encrypted_office():
    return compound_fixture({"EncryptionInfo": b"encryption header", "EncryptedPackage": b"ciphertext"})


def pdf_bytes(password=None, owner_only=False):
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), "Public synthetic test")
        if password or owner_only:
            return doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,
                owner_pw="synthetic-owner", user_pw=password or "")
        return doc.tobytes()


@pytest.mark.parametrize("fmt", ["docx", "xlsx", "pptx"])
def test_office_encryption_requires_real_directory_streams(fmt):
    assert is_encrypted(encrypted_office(), fmt)
    assert not is_encrypted(compound_fixture({"Data": b"EncryptionInfo\0EncryptedPackage"}), fmt)
    assert not is_encrypted(compound_fixture({"EncryptedPackage": b"alone"}), fmt)
    assert not is_encrypted(b"damaged" + encrypted_office(), fmt)
    cyclic = bytearray(encrypted_office())
    struct.pack_into("<I", cyclic, 512 + 128 + 72, 1)
    assert not is_encrypted(bytes(cyclic), fmt)


def test_legacy_open_password_markers_not_edit_protection():
    word = bytearray(32)
    struct.pack_into("<H", word, 0, 0xA5EC)
    struct.pack_into("<H", word, 10, 0x100)
    assert is_encrypted(compound_fixture({"WordDocument": bytes(word)}), "doc")
    struct.pack_into("<H", word, 10, 0x800)  # Write-reservation only.
    assert not is_encrypted(compound_fixture({"WordDocument": bytes(word)}), "doc")
    assert is_encrypted(compound_fixture({"Workbook": struct.pack("<HHH", 0x2F, 2, 1)}), "xls")
    assert not is_encrypted(compound_fixture({"Workbook": struct.pack("<HHH", 0x12, 2, 1)}), "xls")
    ppt = struct.pack("<HHIII", 0, 0x0FF6, 20, 20, 0xF3D1C4DF)
    assert is_encrypted(compound_fixture({"Current User": ppt}), "ppt")
    assert not is_encrypted(compound_fixture({"Current User": ppt[:-4] + struct.pack("<I", 0xE391C05F)}), "ppt")


def test_pdf_open_password_and_owner_only_distinction():
    assert is_encrypted(pdf_bytes("synthetic-open"), "pdf")
    assert not is_encrypted(pdf_bytes(owner_only=True), "pdf")
    assert not is_encrypted(pdf_bytes(), "pdf")
    assert not is_encrypted(b"%PDF-broken", "pdf")


def test_preflight_preserves_stream_position_and_real_mini_stream_fixture(tmp_path):
    stream = io.BytesIO(encrypted_office())
    stream.seek(17)
    with pytest.raises(EncryptedFileError, match=ENCRYPTED_FILE_MESSAGE):
        reject_encrypted(stream, "docx")
    assert stream.tell() == 17
    # Turn EncryptionInfo into a genuine mini stream. FAT: directory=0,
    # former stream sector=1 becomes root mini stream, MiniFAT=2.
    data = bytearray(encrypted_office())
    struct.pack_into("<IQ", data, 512 + 116, 1, 64)
    struct.pack_into("<IQ", data, 512 + 128 + 116, 0, 16)
    struct.pack_into("<II", data, 60, 2, 1)
    fat_sector = struct.unpack_from("<I", data, 76)[0]
    struct.pack_into("<I", data, (fat_sector + 1) * 512 + 4, 0xFFFFFFFE)
    struct.pack_into("<I", data, (fat_sector + 1) * 512 + 8, 0xFFFFFFFE)
    data[3 * 512:4 * 512] = struct.pack("<128I", 0xFFFFFFFE, *([0xFFFFFFFF] * 127))
    assert is_encrypted(bytes(data), "docx")
    path = tmp_path / "fixture.docx"
    path.write_bytes(data)
    assert CompoundFile(path.read_bytes()).read_stream("EncryptionInfo") == b"encryption heade"


@pytest.mark.parametrize("entry", list(FileEntry))
def test_registry_rejects_before_engine_readiness_and_execution(entry, tmp_path, monkeypatch):
    path = tmp_path / "encrypted.docx"
    path.write_bytes(encrypted_office())
    registry = get_file_processor_registry()
    monkeypatch.setattr(registry, "engine_state", lambda *a: pytest.fail("engine queried"))
    with pytest.raises(EncryptedFileError):
        registry.resolve(FileProcessingRequest(task_type=FileTaskType.CONVERT, entry=entry,
            source_paths=[str(path)], source_format="docx", target_format="pdf"), require_ready=True)
    assert document_loader.load_document(str(path)).endswith(ENCRYPTED_FILE_MESSAGE)
    assert converter.convert_file(str(path), "pdf").error_type == "encrypted_file"
    monkeypatch.setattr(main.heavy_task_limits, "acquire_slot", lambda: pytest.fail("slot consumed"))
    assert execution._run_conversion_with_agent_budget(lambda *a: pytest.fail("engine called"),
        str(path), "pdf", 1).error_type == "encrypted_file"


@pytest.mark.parametrize("fmt", ["docx", "xlsx", "pptx", "pdf", "doc", "xls", "ppt"])
@pytest.mark.parametrize("endpoint", ["/documents/upload", "/tools/convert", "/chat/attachments"])
def test_encrypted_inputs_rejected_at_all_three_entries(endpoint, fmt, client, auth_headers, monkeypatch):
    headers, user = auth_headers("employee" if endpoint == "/documents/upload" else "customer")
    data = {"organization_id": grant_work_organization(user["user_id"])} if endpoint == "/documents/upload" else (
        {"session_id": "encrypted-input-session"} if endpoint == "/chat/attachments" else {"target_format": "pdf" if fmt != "pdf" else "docx"})
    payload = pdf_bytes("synthetic-open") if fmt == "pdf" else encrypted_office()
    monkeypatch.setattr(main.heavy_task_limits, "acquire_slot", lambda: pytest.fail("slot consumed"))
    monkeypatch.setattr(main.converter, "convert_file", lambda *a: pytest.fail("engine called"))
    monkeypatch.setattr(main.converter, "convert_pdf_to_office", lambda *a: pytest.fail("engine called"))
    monkeypatch.setattr(main.document_loader, "load_document", lambda *a: pytest.fail("parser called"))
    monkeypatch.setattr(main.file_service, "conversion_availability", lambda *a: pytest.fail("engine queried"))
    response = client.post(endpoint, headers=headers, data=data, files={"file": ("synthetic." + fmt, payload)})
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == ENCRYPTED_FILE_MESSAGE
    if endpoint == "/documents/upload":
        with auth._connect() as conn:
            rows = conn.execute("SELECT * FROM upload_tasks WHERE created_by=?", (user["user_id"],)).fetchall()
        assert len(rows) == 1 and rows[0]["status"] == "failed"
        assert rows[0]["error_message"] == ENCRYPTED_FILE_MESSAGE
        assert task_store.count_unfinished_by_user(user["user_id"]) == 0
    else:
        assert response.json()["error_type"] == "encrypted_file"


@pytest.mark.parametrize("endpoint", ["/documents/upload", "/tools/convert", "/chat/attachments"])
def test_corrupted_office_is_not_reported_as_encrypted(endpoint, client, auth_headers, monkeypatch):
    headers, user = auth_headers("employee" if endpoint == "/documents/upload" else "customer")
    data = {"organization_id": grant_work_organization(user["user_id"])} if endpoint == "/documents/upload" else (
        {"session_id": "corrupted-input-session"} if endpoint == "/chat/attachments" else {"target_format": "pdf"})
    monkeypatch.setattr(main.file_service, "conversion_availability", lambda *a: ("", ""))
    response = client.post(endpoint, headers=headers, data=data, files={"file": ("broken.docx", b"broken-data")})
    assert response.status_code == 400
    assert ENCRYPTED_FILE_MESSAGE not in response.text


@pytest.mark.parametrize("fmt", ["docx", "xlsx", "pptx", "pdf"])
def test_normal_document_preflight_does_not_change_bytes_or_position(fmt):
    payload = {"docx": _docx_bytes, "xlsx": lambda: _office_zip_bytes("xl/workbook.xml"),
        "pptx": lambda: _office_zip_bytes("ppt/presentation.xml"), "pdf": pdf_bytes}[fmt]()
    stream = io.BytesIO(payload)
    stream.seek(7)
    reject_encrypted(stream, fmt)
    assert stream.tell() == 7 and stream.getvalue() == payload


def test_normal_ooxml_only_reads_signature_not_full_upload():
    class HeaderOnly(io.BytesIO):
        def read(self, count=-1):
            assert count == 8, "normal ZIP must not be copied in the preflight"
            return super().read(count)
    source = HeaderOnly(_docx_bytes())
    reject_encrypted(source, "docx")
    assert source.tell() == 0


@pytest.mark.parametrize("endpoint", ["/documents/upload", "/tools/convert", "/chat/attachments"])
@pytest.mark.parametrize("fmt", ["docx", "xlsx", "pptx", "pdf"])
def test_normal_files_still_reach_processing_at_three_entries(endpoint, fmt, client,
        auth_headers, monkeypatch, tmp_path):
    headers, user = auth_headers("employee" if endpoint == "/documents/upload" else "customer")
    data = {"organization_id": grant_work_organization(user["user_id"])} if endpoint == "/documents/upload" else (
        {"session_id": "normal-input-" + uuid.uuid4().hex} if endpoint == "/chat/attachments" else
        {"target_format": "pdf" if fmt != "pdf" else "docx"})
    payload = {"docx": _docx_bytes, "xlsx": lambda: _office_zip_bytes("xl/workbook.xml"),
        "pptx": lambda: _office_zip_bytes("ppt/presentation.xml"), "pdf": pdf_bytes}[fmt]()
    processed = []
    def convert(path, target):
        processed.append("convert")
        output = tmp_path / ("normal-result." + target)
        output.write_bytes(_docx_bytes() if target == "docx" else pdf_bytes())
        return converter.ConversionResult(success=True, status=converter.ConversionStatus.SUCCESS,
            output_path=str(output), converted_from_format=fmt, converted_to_format=target)
    def load(path):
        processed.append("extract")
        return "Synthetic ordinary document content."
    monkeypatch.setattr(main.converter, "convert_file", convert)
    monkeypatch.setattr(main.converter, "convert_pdf_to_office", convert)
    monkeypatch.setattr(main.document_loader, "load_document", load)
    response = client.post(endpoint, headers=headers, data=data,
                           files={"file": ("ordinary." + fmt, payload)})
    assert response.status_code == 200, response.text
    assert processed
    if endpoint == "/documents/upload":
        assert response.json()["status"] == "accepted"
        assert task_store.get_task(response.json()["task_id"]).status == "done"
    else:
        assert response.json()["success"] is True


def test_conversion_service_also_rejects_before_engine_execution():
    from tests.test_conversion_service import ready_client, wait_ready, KEY
    client, manager = ready_client()
    with client:
        wait_ready(manager)
        manager.convert = lambda *a: pytest.fail("encrypted input reached engine")
        response = client.post("/v1/tasks", headers={"X-Conversion-Key": KEY},
            data={"source_format": "docx", "target_format": "pdf", "remaining_budget": "1"},
            files={"file": ("synthetic.docx", encrypted_office())})
        assert response.status_code == 422 and response.json()["detail"] == ENCRYPTED_FILE_MESSAGE
        assert not manager.jobs
        assert manager.reserve()
        manager.capacity.release()
