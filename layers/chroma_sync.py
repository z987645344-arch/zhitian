# -*- coding: utf-8 -*-
"""进程内Chroma访问同步原语。

业务读写与备份脚本必须复用同一个RLock，不能各自创建锁。
该锁不跨进程；独立备份/恢复命令仍要求先停止后端或暂停所有写入。
"""

import threading


CHROMA_LOCK = threading.RLock()


def close_chroma_client(client) -> None:
    """Chroma 0.5.0无close：只关停本实例，不能清空其他仍在运行的库。"""
    from chromadb.api.client import SharedSystemClient

    with CHROMA_LOCK:
        system = client._system
        # stop失败必须传给调用方，保留引用和缓存供诊断/重试，不掩盖真实故障。
        system.stop()
        identifier = client._identifier
        cache = SharedSystemClient._identifer_to_system
        if cache.get(identifier) is system:
            del cache[identifier]
