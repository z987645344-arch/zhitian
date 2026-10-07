"""历史探针入口；独立转换容器运行，标准库实现位于converter_service。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from converter_service.probe import run_probe

if __name__ == "__main__":
    print(json.dumps(run_probe()))
