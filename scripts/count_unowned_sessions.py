"""只读统计无主人但有持久会话记录的数量；不导入应用，不输出标识/正文。"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from layers.session_records import count_unowned_sessions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args(argv)
    data = args.data_dir
    try:
        count = count_unowned_sessions(data / "users.db", data / "history.db",
                                       data / "files.db", data / "vectordb" / "chroma.sqlite3")
    except Exception as exc:
        # 不把sqlite异常里的路径或标识打印出来；失败不伪装成数量0。
        print("检查失败：" + type(exc).__name__, file=sys.stderr)
        return 1
    print(count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
