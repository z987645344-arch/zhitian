# -*- coding: utf-8 -*-
"""构建期从MIT原始仓库导出bge-small-zh-v1.5的ONNX权重。

只在Docker多阶段构建的导出阶段运行，运行镜像不包含torch/transformers。
从BAAI原仓库自行导出而非取用第三方ONNX镜像仓库，是为了保持许可链干净
（BAAI原仓库为MIT并明示可商用，第三方再导出仓库多未声明license）。

用法：python scripts/export_embedding_onnx.py <输出目录>
"""
import os
import sys

import torch
from transformers import AutoModel, AutoTokenizer

MODEL_ID = "BAAI/bge-small-zh-v1.5"
# 与模型自带的1_Pooling/config.json一致：BGE用CLS池化而非mean池化。
# 这里只导出BertModel本体，池化与归一化在推理侧实现，便于与sentence-transformers逐位对齐。
OPSET = 14


class _Wrapper(torch.nn.Module):
    """显式按关键字调用并只返回last_hidden_state。

    直接把三个张量按位置传给BertModel会与其签名错位（transformers新版在forward里
    注入use_cache），且原始返回是dataclass不便导出，因此加这层薄包装。
    """

    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, input_ids, attention_mask, token_type_ids):
        out = self.model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
        )
        return out.last_hidden_state


def main(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = _Wrapper(AutoModel.from_pretrained(MODEL_ID))
    model.eval()

    # 导出时给一个两条样本、不等长的批，促使dynamic_axes真正生效
    sample = tokenizer(
        ["知天平台的文档检索", "第二条更长一些的中文样本用于导出"],
        padding=True,
        truncation=True,
        max_length=512,
        return_tensors="pt",
    )
    onnx_path = os.path.join(out_dir, "model.onnx")
    with torch.no_grad():
        torch.onnx.export(
            model,
            (sample["input_ids"], sample["attention_mask"], sample["token_type_ids"]),
            onnx_path,
            input_names=["input_ids", "attention_mask", "token_type_ids"],
            output_names=["last_hidden_state"],
            dynamic_axes={
                "input_ids": {0: "batch", 1: "sequence"},
                "attention_mask": {0: "batch", 1: "sequence"},
                "token_type_ids": {0: "batch", 1: "sequence"},
                "last_hidden_state": {0: "batch", 1: "sequence"},
            },
            opset_version=OPSET,
            do_constant_folding=True,
            # 走传统TorchScript导出器：新的dynamo导出器需额外的onnxscript依赖，
            # 而本脚本只在构建期跑一次，没必要为此再拉一个包。
            dynamo=False,
        )
    # 推理侧只需tokenizer.json，一并落到同一目录
    tokenizer.save_pretrained(out_dir)
    size_mb = os.path.getsize(onnx_path) / 1024 / 1024
    print("已导出 %s（%.1fMB）" % (onnx_path, size_mb))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "onnx_model")
