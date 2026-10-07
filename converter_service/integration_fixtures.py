"""CI-only synthetic fixture generator; not an HTTP capability or business path."""

import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

from converter_service.engine import write_smoke_docx
from layers.file_processing.runner import TaskWorkspace, task_scope, run_process


@contextmanager
def managed_workspace():
    workspace = TaskWorkspace()
    try:
        yield workspace
    finally:
        workspace.cleanup()


def main(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    with task_scope(90) as scope, managed_workspace() as workspace:
        docx = workspace.path / "sample.docx"
        write_smoke_docx(docx)
        shutil.copyfile(docx, destination / docx.name)
        csv = workspace.path / "sample.csv"
        csv.write_text("name,value\nknowledge,42\n", encoding="utf-8")
        fodp = workspace.path / "sample.fodp"
        fodp.write_text('''<?xml version="1.0" encoding="UTF-8"?>
<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
 xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
 office:mimetype="application/vnd.oasis.opendocument.presentation" office:version="1.2">
<office:body><office:presentation><draw:page draw:name="page1">
<draw:frame svg:width="20cm" svg:height="3cm" svg:x="1cm" svg:y="1cm">
<draw:text-box><text:p>Remote presentation fixture</text:p></draw:text-box>
</draw:frame></draw:page></office:presentation></office:body></office:document>''', encoding="utf-8")
        sandbox = Path(__file__).resolve().parents[1] / "layers/soffice_sandbox.py"
        for source, target in ((docx, "doc"), (csv, "xls"), (csv, "xlsx"),
                               (fodp, "ppt"), (fodp, "pptx")):
            output = workspace.path / target
            output.mkdir()
            code = run_process([sys.executable, str(sandbox), "/usr/bin/soffice",
                "-env:UserInstallation=" + (workspace.path / "profile").as_uri(),
                "--headless", "--convert-to", target, "--outdir", str(output), str(source)], workspace, scope)
            assert code == 0, target
            artifact = output / (source.stem + "." + target)
            assert artifact.is_file() and artifact.stat().st_size, target
            shutil.copyfile(artifact, destination / ("sample." + target))


if __name__ == "__main__":
    main(sys.argv[1])
