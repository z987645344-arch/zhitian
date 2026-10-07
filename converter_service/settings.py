"""仅服务自身配置；不读取.env文件，不导入业务config。"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    shared_key: str
    input_limit: int = 25 * 1024 * 1024
    output_limit: int = 25 * 1024 * 1024
    queue_limit: int = 2
    timeout_seconds: float = 30
    retention_seconds: float = 60
    soffice_path: str = "/usr/bin/soffice"

    @classmethod
    def environment(cls):
        return cls(shared_key=os.environ.get("CONVERSION_SERVICE_KEY", ""))

    def valid_key(self):
        return len(self.shared_key.encode("utf-8")) >= 32 and not self.shared_key.startswith("CHANGE_ME")
