# -*- coding: utf-8 -*-
"""零网络验证编译产物的hnsw读写、持久化与最近邻检索。"""

from importlib.metadata import version
from pathlib import Path
import tempfile

import hnswlib
import numpy as np


def main():
    assert version("chroma-hnswlib") == "0.7.3"
    vectors = np.asarray([[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float32)
    with tempfile.TemporaryDirectory(prefix="hnsw-smoke-") as temporary:
        path = Path(temporary) / "index.bin"
        index = hnswlib.Index(space="l2", dim=3)
        index.init_index(max_elements=8, random_seed=42)
        index.add_items(vectors, [11, 22, 33])
        index.save_index(str(path))
        restored = hnswlib.Index(space="l2", dim=3)
        restored.load_index(str(path))
        ids, distances = restored.knn_query(vectors, k=1)
        assert ids.tolist() == [[11], [22], [33]]
        assert np.array_equal(distances, np.zeros((3, 1), dtype=np.float32))
        assert np.array_equal(restored.get_items([11, 22, 33]), vectors)
        restored.mark_deleted(22)
        assert restored.knn_query(vectors[1:2], k=1)[0][0][0] != 22
    print("chroma-hnswlib=0.7.3 write/persist/read/query/delete=passed")


if __name__ == "__main__":
    main()
