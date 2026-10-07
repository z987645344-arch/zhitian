"""仅在隔离容器运行的真实回环反证；只用标准库，不接触业务数据。"""

import base64
import json
import subprocess
import sys
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from converter_service.engine import convert, write_smoke_docx
from converter_service.settings import Settings
from layers.file_processing.runner import TaskWorkspace, task_scope


def run_probe():
    sandbox = str(Path(__file__).resolve().parents[1] / "layers/soffice_sandbox.py")
    checks = {}
    for family in ("AF_INET", "AF_INET6", "AF_UNIX"):
        result = subprocess.run([sys.executable, sandbox, sys.executable, "-c",
            "import socket; socket.socket(socket.%s, socket.SOCK_STREAM)" % family],
            capture_output=True, timeout=10, close_fds=True)
        assert (result.returncode == 0) == (family == "AF_UNIX"), family
        checks[family] = result.returncode == 0
    image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lZkAAAAASUVORK5CYII=")
    hits = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(image)))
            self.end_headers()
            self.wfile.write(image)
        def log_message(self, *args):
            pass
    settings = Settings(shared_key="test-only-conversion-probe-key-32-bytes")
    with tempfile.TemporaryDirectory(prefix="conversion-loopback-") as temporary:
        root = Path(temporary)
        normal = root / "normal.docx"
        write_smoke_docx(normal)
        workspace = TaskWorkspace()
        try:
            with task_scope(30) as scope:
                output = convert(normal, "pdf", workspace, scope, settings)
                assert output.read_bytes().startswith(b"%PDF-")
        finally:
            workspace.cleanup()
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            address = "http://127.0.0.1:%s/probe.png" % server.server_port
            linked = root / "linked.docx"
            with zipfile.ZipFile(normal) as original, zipfile.ZipFile(linked, "w") as archive:
                for member in original.infolist():
                    payload = original.read(member.filename)
                    if member.filename == "word/document.xml":
                        payload = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
                            'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
                            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                            'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                            '<w:body><w:p><w:r><w:drawing><wp:inline><wp:extent cx="914400" cy="914400"/>'
                            '<wp:docPr id="1" name="probe"/><a:graphic><a:graphicData '
                            'uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>'
                            '<pic:nvPicPr><pic:cNvPr id="0" name="probe"/><pic:cNvPicPr/></pic:nvPicPr>'
                            '<pic:blipFill><a:blip r:link="rIdProbe"/><a:stretch><a:fillRect/></a:stretch>'
                            '</pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="914400" cy="914400"/>'
                            '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
                            '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p></w:body></w:document>').encode()
                    archive.writestr(member, payload)
                archive.writestr("word/_rels/document.xml.rels", '<Relationships '
                    'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rIdProbe" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                    'Target="%s" TargetMode="External"/></Relationships>' % address)
            baseline = root / "baseline"
            baseline.mkdir()
            result = subprocess.run([settings.soffice_path,
                "-env:UserInstallation=" + (root / "baseline-profile").as_uri(),
                "--headless", "--convert-to", "pdf", "--outdir", str(baseline), str(linked)],
                capture_output=True, timeout=30, close_fds=True)
            assert result.returncode == 0 and (baseline / "linked.pdf").is_file()
            before = len(hits)
            assert before > 0, "loopback probe did not exercise remote loading"
            workspace = TaskWorkspace()
            try:
                with task_scope(30) as scope:
                    convert(linked, "pdf", workspace, scope, settings)
                after = len(hits) - before
                assert after == 0, "sandbox allowed outbound requests"
            finally:
                workspace.cleanup()
                server.shutdown()
    return dict(socket_checks=checks, linked_before_requests=before,
        linked_after_requests=after, normal_docx_pdf=True)


if __name__ == "__main__":
    print(json.dumps(run_probe()))
