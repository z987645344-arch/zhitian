# -*- coding: utf-8 -*-
"""从校验过的官方源码构建可移植CP312 hnsw wheel，供Linux镜像和Windows CI共用。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

SOURCE_URL = "https://files.pythonhosted.org/packages/c0/59/1224cbae62c7b84c84088cdf6c106b9b2b893783c000d22c442a1672bc75/chroma-hnswlib-0.7.3.tar.gz"
SOURCE_SHA256 = "b6137bedde49fffda6af93b0297fe00429fc61e5a072b1ed9377f909ed95a932"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def download_source(archive):
    # 仅对传输失败重试固定官方URL；不换源、不关闭TLS校验，哈希失败不重试。
    for attempt in range(3):
        try:
            with urllib.request.urlopen(SOURCE_URL, timeout=30) as response, Path(archive).open("wb") as output:
                shutil.copyfileobj(response, output)
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            print(f"source_download attempt={attempt + 1} error_type={type(error).__name__}")
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def extract_source(archive, destination):
    """3.10/3.12共用的解包保护：只接受目录和普通文件，不接受链接或越界。"""
    destination = Path(destination).resolve()
    with tarfile.open(archive, "r:gz") as source:
        for member in source.getmembers():
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination) or not (member.isfile() or member.isdir()):
                raise ValueError("源码归档包含不安全的成员")
        source.extractall(destination)


def portable_environment():
    env = dict(os.environ)
    for name in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "CL", "_CL_"):
        env.pop(name, None)
    env["HNSWLIB_NO_NATIVE"] = "1"
    if sys.platform != "win32":
        env["CFLAGS"] = "-march=x86-64 -mtune=generic"
        env["CXXFLAGS"] = env["CFLAGS"]
    return env


def verify_artifact(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "build-manifest.json").read_text(encoding="utf-8"))
    if manifest["source_sha256"] != SOURCE_SHA256 or manifest["source_url"] != SOURCE_URL:
        raise ValueError("构件源码身份不匹配")
    if manifest.get("no_native") is not True:
        raise ValueError("构件没有可移植编译声明")
    for name, expected in manifest["files"].items():
        target = (directory / name).resolve()
        if target.parent != directory or digest(target) != expected:
            raise ValueError("构件SHA-256不匹配: " + name)
    wheels = list(directory.glob("*.whl"))
    if len(wheels) != 1 or wheels[0].name not in manifest["files"] or "LICENSE" not in manifest["files"]:
        raise ValueError("构件必须包含一份wheel及许可证")
    wheel = wheels[0]
    if not wheel.name.startswith("chroma_hnswlib-0.7.3-cp312-cp312-"):
        raise ValueError("构件不是预期的0.7.3 CP312 wheel")
    if not wheel.name.endswith(("-win_amd64.whl", "-linux_x86_64.whl")):
        raise ValueError("构件不是受支持的x86-64平台")
    with zipfile.ZipFile(wheel) as archive:
        metadata = archive.read("chroma_hnswlib-0.7.3.dist-info/METADATA").decode()
        if "Version: 0.7.3\n" not in metadata or "Name: chroma-hnswlib\n" not in metadata:
            raise ValueError("wheel元数据身份不匹配")
    print("verified", wheel.name, digest(wheel))
    return wheel


def build(output):
    if sys.version_info[:2] != (3, 12) or platform.machine().lower() not in ("amd64", "x86_64"):
        raise RuntimeError("必须使用x86-64 Python 3.12构建")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("构件目录必须为空，避免混用旧wheel")
    with tempfile.TemporaryDirectory(prefix="hnsw-source-") as temporary:
        temporary = Path(temporary)
        archive = temporary / "source.tar.gz"
        download_source(archive)
        if digest(archive) != SOURCE_SHA256:
            raise ValueError("官方源码SHA-256不匹配，停止编译")
        extract_source(archive, temporary / "source")
        source = temporary / "source" / "chroma-hnswlib-0.7.3"
        result = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--no-cache-dir", "--verbose", "--wheel-dir", str(output), str(source)],
            env=portable_environment(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        print(result.stdout, end="")
        result.check_returncode()
        if any(flag in result.stdout.lower() for flag in ("-march=native", "-mtune=native", "/arch:avx")):
            raise RuntimeError("编译日志出现宿主专属指令集，拒绝构件")
        (output / "build.log").write_text(result.stdout, encoding="utf-8")
        shutil.copyfile(source / "LICENSE", output / "LICENSE")
    shutil.copyfile(__file__, output / "build-verifier.py")
    files = {path.name: digest(path) for path in sorted(output.iterdir())}
    manifest = {"source_url": SOURCE_URL, "source_sha256": SOURCE_SHA256, "python": sys.version,
                "platform": platform.platform(), "no_native": True, "files": files}
    (output / "build-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (output / "SHA256SUMS").write_text("".join(f"{value}  {name}\n" for name, value in files.items()), encoding="utf-8")
    verify_artifact(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path)
    group.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        verify_artifact(args.verify)
    else:
        build(args.output)


if __name__ == "__main__":
    main()
