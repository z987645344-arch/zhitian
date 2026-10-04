# -*- coding: utf-8 -*-
"""周扫描只读检查官方Python补丁标签；digest变化或暂不可核实时仅提示，不自动更新。"""

import json
from pathlib import Path
import re
import subprocess


def pinned_base(text):
    match = re.search(r"^ARG PYTHON_BASE=(python:3\.12\.\d+-slim-trixie)@(sha256:[0-9a-f]{64})$", text, re.M)
    if not match:
        raise ValueError("Dockerfile缺少精确补丁版本与digest")
    return match.groups()


def main():
    tag, pinned = pinned_base(Path("Dockerfile").read_text(encoding="utf-8"))
    try:
        result = subprocess.run(["docker", "buildx", "imagetools", "inspect", tag, "--format", "{{json .Manifest.Digest}}"],
                                check=True, capture_output=True, text=True, timeout=60)
        current = json.loads(result.stdout)
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", current):
            raise ValueError("官方digest输出格式无效")
    except (subprocess.SubprocessError, OSError, ValueError, TypeError) as error:
        print(f"::notice::本周暂未核实官方Python digest，须人工重试；error_type={type(error).__name__}")
        return
    if current != pinned:
        print(f"::notice::有新 digest 可更新：{tag}@{current}（当前钉定 {pinned}）；请审核并验证后手工更新。")
    else:
        print(f"Python官方标签digest未变：{tag}@{pinned}")


if __name__ == "__main__":
    main()
