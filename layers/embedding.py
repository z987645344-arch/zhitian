# -*- coding: utf-8 -*-
"""F37：bge-small-zh-v1.5的ONNX嵌入函数，接入chromadb自定义embedding function。

为什么自写而不用chromadb内置的SentenceTransformerEmbeddingFunction：后者要求安装
sentence-transformers，实测会连带引入torch、scipy、transformers、sympy、sklearn等
约910MB依赖，把镜像从529.9MB推到1.5GB以上。而本项目运行环境已有onnxruntime与
tokenizers（chromadb的传递依赖），走ONNX路径不新增任何Python依赖，只多一个模型文件。

池化方式按模型自带的1_Pooling/config.json：BGE用**CLS池化**（pooling_mode_cls_token
为true、pooling_mode_mean_tokens为false），不是mean池化；用错会得不到选型时实测的
区分度。归一化对应模型的2_Normalize模块。
"""

import logging
import os
import threading

import numpy as np
import onnxruntime
from tokenizers import Tokenizer

import config

logger = logging.getLogger(__name__)

# 模型自身的上限，也是选型时确认"500字符切片不被截断"的依据
MAX_SEQ_LENGTH = 512
# 单次前向的最大条数。本机实测16/32/64分别为22.4/21.6/21.1切片每秒，
# 差异很小且16略优；批越大内存峰值越高，故取16。
BATCH_SIZE = 16


class BgeSmallZhEmbeddingFunction:
    """chromadb embedding function：分词 → ONNX前向 → CLS池化 → L2归一化。"""

    def __init__(self, model_dir: str = ""):
        self._model_dir = model_dir or config.EMBEDDING_MODEL_DIR
        self._lock = threading.Lock()
        self._session = None
        self._tokenizer = None

    def _ensure_loaded(self):
        """首次调用时加载，避免导入期就占用内存与文件句柄。"""
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            onnx_path = os.path.join(self._model_dir, "model.onnx")
            tokenizer_path = os.path.join(self._model_dir, "tokenizer.json")
            for path in (onnx_path, tokenizer_path):
                if not os.path.exists(path):
                    raise FileNotFoundError(
                        f"嵌入模型文件缺失：{path}。镜像应在构建期由"
                        f"scripts/export_embedding_onnx.py导出。"
                    )
            tokenizer = Tokenizer.from_file(tokenizer_path)
            tokenizer.enable_truncation(max_length=MAX_SEQ_LENGTH)
            tokenizer.enable_padding()
            options = onnxruntime.SessionOptions()
            # 与既有部署一致按单进程CPU推理，不额外抢占核数
            options.graph_optimization_level = (
                onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
            )
            self._session = onnxruntime.InferenceSession(
                onnx_path, options, providers=["CPUExecutionProvider"]
            )
            self._tokenizer = tokenizer
            logger.info("嵌入模型已加载：dir=%s", self._model_dir)

    def _embed_batch(self, texts):
        encodings = self._tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        token_type_ids = np.array([e.type_ids for e in encodings], dtype=np.int64)
        last_hidden = self._session.run(
            ["last_hidden_state"],
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )[0]
        # CLS池化：取每条序列的第0个token
        cls = last_hidden[:, 0]
        norms = np.linalg.norm(cls, axis=1, keepdims=True)
        # 全零向量不可能出现在正常前向结果里，兜底避免除零
        norms = np.where(norms == 0, 1e-12, norms)
        return (cls / norms).astype(np.float32)

    def __call__(self, input):
        self._ensure_loaded()
        texts = [t if isinstance(t, str) else str(t) for t in list(input)]
        if not texts:
            return []
        out = []
        for start in range(0, len(texts), BATCH_SIZE):
            out.append(self._embed_batch(texts[start:start + BATCH_SIZE]))
        return np.concatenate(out, axis=0).tolist()

    def name(self) -> str:
        """chromadb在部分路径下会读取该标识用于持久化元数据。"""
        return "bge-small-zh-v1.5-onnx"


_default_function = None
_default_lock = threading.Lock()


def get_embedding_function() -> BgeSmallZhEmbeddingFunction:
    """进程内共享单例：模型加载昂贵，两个collection复用同一实例。"""
    global _default_function
    if _default_function is None:
        with _default_lock:
            if _default_function is None:
                _default_function = BgeSmallZhEmbeddingFunction()
    return _default_function
