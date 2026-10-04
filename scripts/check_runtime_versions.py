# -*- coding: utf-8 -*-
"""核验运行环境中关键二进制依赖的最终锁定版本，不导入应用或数据层。"""

from importlib import metadata
import sys


EXPECTED_VERSIONS = {"numpy": "1.26.4", "chroma-hnswlib": "0.7.3"}


def verify_runtime_versions() -> dict[str, str]:
    installed = {}
    for name, expected in EXPECTED_VERSIONS.items():
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError as exc:
            raise RuntimeError(f"运行依赖缺失: {name}; 必须为 {expected}") from exc
        if actual != expected:
            raise RuntimeError(f"运行依赖版本不匹配: {name}={actual}; 必须为 {expected}")
        installed[name] = actual
    return installed


def main() -> int:
    try:
        installed = verify_runtime_versions()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for name, version in installed.items():
        print(f"{name}=={version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
