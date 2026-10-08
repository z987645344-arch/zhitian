"""Shared, read-only password-to-open preflight. No engine or resource slot is used.

CFB directory/FAT parsing uses only the standard library (MS-CFB); do not search
arbitrary bytes for stream names. Legacy markers: MS-DOC FibBase.fEncrypted,
MS-XLS FilePass, MS-PPT CurrentUserAtom.headerToken. This does not decrypt files.
"""

import struct
from pathlib import Path

ENCRYPTED_FILE_MESSAGE = "文件已加密，请去掉打开密码后再上传"
OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_END = 0xFFFFFFFE
_FREE = 0xFFFFFFFF


class EncryptedFileError(ValueError):
    def __init__(self):
        super().__init__(ENCRYPTED_FILE_MESSAGE)


class CompoundFile:
    """Bounded CFB v3/v4 reader for root-level streams, including mini streams."""

    def __init__(self, data):
        self.data = data
        if len(data) < 512 or data[:8] != OLE_MAGIC:
            raise ValueError("invalid_compound_file")
        major, order, shift, mini_shift = struct.unpack_from("<4H", data, 26)
        if order != 0xFFFE or (major, shift) not in {(3, 9), (4, 12)} or mini_shift != 6:
            raise ValueError("invalid_compound_file")
        self.sector_size = 1 << shift
        self.sector_count = len(data) // self.sector_size - 1
        if self.sector_count < 1 or len(data) % self.sector_size:
            raise ValueError("invalid_compound_file")
        count = self.u32(data, 44)
        fat_ids = [n for n in struct.unpack_from("<109I", data, 76) if n != _FREE]
        difat, difat_count = self.u32(data, 68), self.u32(data, 72)
        seen = set()
        if count > self.sector_count or difat_count > self.sector_count:
            raise ValueError("invalid_compound_file")
        for _ in range(difat_count):
            if difat in seen:
                raise ValueError("invalid_compound_file")
            seen.add(difat)
            block = self.sector(difat)
            values = struct.unpack("<%dI" % (self.sector_size // 4), block)
            fat_ids.extend(n for n in values[:-1] if n != _FREE)
            difat = values[-1]
        if len(fat_ids) != count or len(set(fat_ids)) != count:
            raise ValueError("invalid_compound_file")
        self.fat = []
        for n in fat_ids:
            self.fat.extend(struct.unpack("<%dI" % (self.sector_size // 4), self.sector(n)))
        directory = self.chain(self.u32(data, 48), self.fat, self.sector_size, self.sector)
        entries = [directory[i:i + 128] for i in range(0, len(directory), 128)]
        if not entries or entries[0][66] != 5:
            raise ValueError("invalid_compound_file")
        self.root = entries[0]
        self.streams = {}
        pending = [self.u32(self.root, 76)]
        seen = set()
        while pending:
            n = pending.pop()
            if n == _FREE:
                continue
            if n in seen or n >= len(entries):
                raise ValueError("invalid_compound_file")
            seen.add(n)
            entry = entries[n]
            length = struct.unpack_from("<H", entry, 64)[0]
            if length < 2 or length > 64 or length % 2:
                raise ValueError("invalid_compound_file")
            name = entry[:length - 2].decode("utf-16-le").casefold()
            pending.extend([self.u32(entry, 68), self.u32(entry, 72)])
            if entry[66] == 2:
                if name in self.streams:
                    raise ValueError("invalid_compound_file")
                self.streams[name] = entry

    @staticmethod
    def u32(data, offset):
        return struct.unpack_from("<I", data, offset)[0]

    def sector(self, n):
        if n >= self.sector_count:
            raise ValueError("invalid_compound_file")
        start = (n + 1) * self.sector_size
        return self.data[start:start + self.sector_size]

    def chain(self, start, fat, unit, read, limit=None):
        chunks, seen = [], set()
        n = start
        while n != _END:
            if n in seen or n >= len(fat) or len(seen) >= len(self.data) // unit:
                raise ValueError("invalid_compound_file")
            seen.add(n)
            chunks.append(read(n))
            if limit is not None and len(chunks) * unit >= limit:
                break
            n = fat[n]
        return b"".join(chunks)[:limit]

    def read_stream(self, name, max_bytes=None):
        entry = self.streams.get(name.casefold())
        if entry is None:
            return b""
        size = struct.unpack_from("<Q", entry, 120)[0]
        if size > len(self.data):
            raise ValueError("invalid_compound_file")
        limit = min(size, max_bytes) if max_bytes is not None else size
        if not limit:
            return b""
        start = self.u32(entry, 116)
        if size >= 4096:
            result = self.chain(start, self.fat, self.sector_size, self.sector, limit)
        else:
            mini_count = self.u32(self.data, 64)
            if not mini_count or mini_count > self.sector_count:
                raise ValueError("invalid_compound_file")
            mini_data = self.chain(self.u32(self.data, 60), self.fat, self.sector_size,
                                   self.sector, mini_count * self.sector_size)
            mini_fat = struct.unpack("<%dI" % (len(mini_data) // 4), mini_data)
            root_size = struct.unpack_from("<Q", self.root, 120)[0]
            if root_size > len(self.data):
                raise ValueError("invalid_compound_file")
            root_data = self.chain(self.u32(self.root, 116), self.fat, self.sector_size,
                                   self.sector, root_size)
            def mini_sector(n):
                block = root_data[n * 64:(n + 1) * 64]
                if len(block) != 64:
                    raise ValueError("invalid_compound_file")
                return block
            result = self.chain(start, mini_fat, 64, mini_sector, limit)
        if len(result) < limit:
            raise ValueError("invalid_compound_file")
        return result


def is_encrypted(data: bytes, source_format: str) -> bool:
    fmt = source_format.lower().lstrip(".")
    if fmt == "pdf" and data.startswith(b"%PDF-"):
        import fitz
        try:
            with fitz.open(stream=data, filetype="pdf") as document:
                # Permission/owner-password-only PDFs do not require an open password.
                return document.needs_pass
        except (RuntimeError, ValueError):
            return False  # Corruption is reported by the existing validation/parser.
    if fmt not in {"docx", "xlsx", "pptx", "doc", "xls", "ppt"} or data[:8] != OLE_MAGIC:
        return False
    try:
        cfb = CompoundFile(data)
        if {"encryptedpackage", "encryptioninfo"} <= cfb.streams.keys():
            return bool(cfb.read_stream("EncryptionInfo", 8) and cfb.read_stream("EncryptedPackage", 8))
        if fmt == "doc":
            fib = cfb.read_stream("WordDocument", 12)
            return len(fib) == 12 and fib[:2] == b"\xec\xa5" and bool(struct.unpack_from("<H", fib, 10)[0] & 0x100)
        if fmt == "xls":
            workbook = cfb.read_stream("Workbook") or cfb.read_stream("Book")
            offset = 0
            while offset + 4 <= len(workbook):
                record, length = struct.unpack_from("<HH", workbook, offset)
                if offset + 4 + length > len(workbook):
                    break
                if record == 0x2F:
                    return True
                offset += 4 + length
        if fmt == "ppt":
            atom = cfb.read_stream("Current User", 16)
            return (len(atom) == 16 and struct.unpack_from("<H", atom, 2)[0] == 0x0FF6
                    and cfb.u32(atom, 12) == 0xF3D1C4DF)
    except (ValueError, struct.error, UnicodeError):
        return False  # Not evidence of encryption; keep corrupted-file handling distinct.
    return False


def reject_encrypted(source, source_format):
    """Accept bytes, a seekable upload stream, or a path; preserve stream position."""
    if source_format.lower().lstrip(".") not in {"pdf", "docx", "xlsx", "pptx", "doc", "xls", "ppt"}:
        return
    if isinstance(source, bytes):
        data = source
    elif hasattr(source, "read"):
        position = source.tell()
        try:
            source.seek(0)
            header = source.read(8)
            if header != OLE_MAGIC and not (source_format.lower().lstrip(".") == "pdf" and header.startswith(b"%PDF-")):
                return
            source.seek(0)
            data = source.read()
        finally:
            source.seek(position)
    else:
        path = Path(source)
        if not path.is_file():
            return  # Existing missing-source handling is unchanged.
        with path.open("rb") as stream:
            reject_encrypted(stream, source_format)
        return
    if is_encrypted(data, source_format):
        raise EncryptedFileError()


def check_request_inputs(request):
    if request.encrypted:
        raise EncryptedFileError()
    for path in request.source_paths:
        reject_encrypted(path, request.source_format or Path(path).suffix)
