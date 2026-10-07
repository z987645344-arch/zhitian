"""服务端唯一LO执行入口：固定命令、禁网子进程、第二阶段任务运行器。"""

import sys
import zipfile
from pathlib import Path

from layers.file_processing.lo_capabilities import LIBREOFFICE_SOURCES
from layers.file_processing.runner import run_process


def allowed(source, target):
    return source in LIBREOFFICE_SOURCES.get(target, ())


def validate_artifact(path, target, limit):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or not 0 < path.stat().st_size <= limit:
        raise ValueError("output_size_or_missing")
    if target == "pdf":
        with path.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise ValueError("output_type_mismatch")
    elif target == "docx":
        with zipfile.ZipFile(path) as archive:
            if not {"[Content_Types].xml", "word/document.xml"} <= set(archive.namelist()):
                raise ValueError("output_type_mismatch")
    else:
        raise ValueError("unsupported_conversion")


def convert(source, target, workspace, scope, settings):
    if not allowed(source.suffix.lstrip("."), target):
        raise ValueError("unsupported_conversion")
    output = workspace.path / "output"
    output.mkdir()
    sandbox = Path(__file__).resolve().parents[1] / "layers" / "soffice_sandbox.py"
    command = [sys.executable, str(sandbox), settings.soffice_path,
        "-env:UserInstallation=" + (workspace.path / "profile").as_uri(),
        "--headless", "--convert-to", target, "--outdir", str(output), str(source)]
    scope.emit("converting")
    code = run_process(command, workspace, scope)
    if code:
        raise ValueError("sandbox_unavailable" if code == 126 else "process_failed")
    scope.emit("validating")
    artifact = output / (source.stem + "." + target)
    validate_artifact(artifact, target, settings.output_limit)
    scope.check()
    return artifact


def write_smoke_docx(path):
    # 自生成最小OOXML，无外部文件、无需python-docx及其lxml依赖。
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Conversion smoke test</w:t></w:r></w:p><w:sectPr/></w:body></w:document>')
