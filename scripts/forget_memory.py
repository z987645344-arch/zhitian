# -*- coding: utf-8 -*-
"""物理删除超过保留期的长期对话记忆。

仅处理 zhitian_memory collection，不影响 zhitian_documents 企业文档向量库。
"""

import os
import sys
from collections import Counter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from layers import memory


def main() -> None:
    with memory._chroma_lock:
        collection = memory._get_chroma_collection()
        result = collection.get(include=["metadatas"])
        ids = result.get("ids", []) or []
        metadatas = result.get("metadatas", []) or []

        delete_ids = []
        stats = Counter()
        for item_id, metadata in zip(ids, metadatas):
            metadata = metadata or {}
            importance_level = memory._normalize_importance_level(metadata.get("importance_level"))
            age_days = memory._memory_age_days(metadata.get("timestamp"))
            if age_days > memory.hard_delete_days(importance_level):
                delete_ids.append(item_id)
                stats[importance_level] += 1

        print("待删除长期记忆条数:", len(delete_ids))
        print("待删除分级统计:", dict(stats))
        if delete_ids:
            collection.delete(ids=delete_ids)

        after = collection.get(include=["metadatas"])
        remaining_ids = after.get("ids", []) or []
        actual_deleted = len(ids) - len(remaining_ids)
        print("实际删除长期记忆条数:", actual_deleted)


if __name__ == "__main__":
    main()
