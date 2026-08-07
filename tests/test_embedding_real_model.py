# -*- coding: utf-8 -*-
"""F37：真实ONNX嵌入实现的覆盖。

conftest对整个测试会话替换了确定性桩，因此其余用例都不加载真实模型；
本文件是唯一直接验证`layers/embedding.py`的地方。模型文件按设计不入库
（`models/`在.gitignore内，90MB二进制），因此缺失时明确skip而不是失败——
skip会在CI输出里留下可见记录，不会伪装成通过。
"""
import os

import pytest

import config
from layers import embedding

_MODEL_DIR = config.EMBEDDING_MODEL_DIR
_HAS_MODEL = all(
    os.path.exists(os.path.join(_MODEL_DIR, name))
    for name in ("model.onnx", "tokenizer.json")
)

pytestmark = pytest.mark.skipif(
    not _HAS_MODEL,
    reason="缺少嵌入模型文件（%s），按设计不入库；容器内由构建期导出" % _MODEL_DIR,
)


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b))


def test_real_embedding_shape_and_normalization():
    func = embedding.BgeSmallZhEmbeddingFunction(_MODEL_DIR)
    vectors = func(["橙色标签档案的最短留存期限为七十三个月", "短句"])
    assert len(vectors) == 2
    assert len(vectors[0]) == 512
    for vector in vectors:
        assert abs(sum(v * v for v in vector) ** 0.5 - 1.0) < 1e-5


def test_real_embedding_discriminates_chinese():
    """中文区分度：相关句应明显高于无关句。

    这是F37换模型的核心目的——旧模型在这组语料上区分度为-0.0054（等同随机）。
    阈值取0.3，远低于实测的约0.67，只用于捕捉"模型换错或加载错"的回归。
    """
    func = embedding.BgeSmallZhEmbeddingFunction(_MODEL_DIR)
    vectors = func([
        "橙色标签档案的最短留存期限为七十三个月",
        "橙色标签档案要保存多久",
        "今天北京的天气怎么样",
    ])
    related = _cos(vectors[0], vectors[1])
    unrelated = _cos(vectors[0], vectors[2])
    assert related - unrelated > 0.3, (
        "中文区分度仅%.4f（相关%.4f/无关%.4f），疑似模型未按预期加载"
        % (related - unrelated, related, unrelated)
    )


def test_missing_model_dir_raises_clear_error(tmp_path):
    """模型缺失时应给出明确错误，而不是静默降级。"""
    func = embedding.BgeSmallZhEmbeddingFunction(str(tmp_path))
    with pytest.raises(FileNotFoundError) as excinfo:
        func(["任意文本"])
    assert "嵌入模型文件缺失" in str(excinfo.value)
