# -*- coding: utf-8 -*-
"""进程内Chroma访问同步原语。

业务读写与备份脚本必须复用同一个RLock，不能各自创建锁。
该锁不跨进程；独立备份/恢复命令仍要求先停止后端或暂停所有写入。
"""

import threading


CHROMA_LOCK = threading.RLock()
