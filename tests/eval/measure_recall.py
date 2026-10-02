# -*- coding: utf-8 -*-
"""零API召回测量；仅CLI建立隔离库，导入模块不加载应用/模型。

.venv/Scripts/python -B tests/eval/measure_recall.py
结果只写入Git忽略的backups/eval；真实ONNX+现有BM25，不修改线上代码或参数。
完整库排名用于诊断；每个top_k另跑search_documents，不能用大候选池排名代替。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
from tests.eval.run_eval import snapshot_data, source_revision

THRESHOLDS = (.40, .45, .50, .55, .60)
TOP_KS = (5, 8, 10)
# 原题组的quote描述末轮，下面两轮另问事实；只补测量映射，不改正式题库。
TURN_EVIDENCE = {
    "M02/3": [{"source": "05_lamps.md", "section": "D05-04",
               "quote": "L1标准保修12个月"}],
    "M04/2": [{"source": "08_service_plan.pdf", "section": "D08-01",
               "quote": "单价80元"}],
}


def normalize(text):
    """只忽略排版空白；不做同义/数字推断，quote仍须逐字支持。"""
    return re.sub(r"\s+", "", str(text))


def expand_rounds(questions):
    rounds = []
    for question in questions:
        turns = question["question"]
        if isinstance(turns, str):
            turns = [turns]
        for index, text in enumerate(turns, 1):
            key = question["id"] + ("/" + str(index) if len(turns) > 1 else "")
            evidence = list(question.get("supporting_evidence", []))
            if len(turns) > 1:
                check = next(item for item in question["turn_checks"] if item["turn"] == index)
                evidence = [item for item in evidence if item["source"] in check["expected_sources"]]
            if key in TURN_EVIDENCE:
                evidence = TURN_EVIDENCE[key]
            rounds.append({"id": key, "category": question["category"], "question": text,
                           "history_query": "\n".join(turns[:index]),
                           "evidence": evidence, "evidence_mapping":
                           "measurement_supplement" if key in TURN_EVIDENCE else "dataset",
                           "negative": question["id"].startswith(("R", "U")),
                           "known_issue": question.get("known_issue")})
    return rounds


def matching_blocks(evidence, blocks):
    quote = normalize(evidence["quote"])
    if not quote:
        raise ValueError("Empty evidence quote")
    return [block for block in blocks if block["source"] == evidence["source"]
            and quote in normalize(block["content"])]


def describe_evidence(evidence, blocks, ranking, by_k):
    matches = matching_blocks(evidence, blocks)
    keys = {(item["source"], item["chunk_index"]) for item in matches}
    ranked = [(index, item) for index, item in enumerate(ranking, 1)
              if (item["source"], item["chunk_index"]) in keys]
    selected = {}
    for k, candidates in by_k.items():
        found = [(index, item) for index, item in enumerate(candidates, 1)
                 if (item["source"], item["chunk_index"]) in keys]
        selected[str(k)] = {"recalled": bool(found),
                            "rank": found[0][0] if found else None,
                            "score": max((item["score"] for _, item in found), default=None),
                            "passes_055": any(item["score"] >= .55 for _, item in found)}
    block_ranks = []
    for match in matches:
        key = (match["source"], match["chunk_index"])
        full = next(((i, item) for i, item in enumerate(ranking, 1)
                     if (item["source"], item["chunk_index"]) == key), None)
        block_ranks.append({"chunk_index": match["chunk_index"],
                            "full_rank": full[0] if full else None,
                            "score": full[1]["score"] if full else None,
                            "by_top_k": {str(k): next((
                                {"rank": i, "score": item["score"]}
                                for i, item in enumerate(items, 1)
                                if (item["source"], item["chunk_index"]) == key), None)
                                for k, items in by_k.items()}})
    return {**evidence, "present_in_chunks": bool(matches), "blocks": block_ranks,
            "chunk_indices": [item["chunk_index"] for item in matches],
            "full_rank": ranked[0][0] if ranked else None,
            "full_score": max((item["score"] for _, item in ranked), default=None),
            "passes_055_anywhere": any(item["score"] >= .55 for _, item in ranked),
            "by_top_k": selected}


def candidate_chars(candidates):
    # 对齐execution._format_document_tool_context：编号、1200字符截断、双换行。
    # 不导入execution，避免测量脚本初始化不需要的应用模块。
    return len("\n\n".join("[%d] %s" % (index, item["content"].strip()[:1200])
                           for index, item in enumerate(candidates, 1)
                           if item["content"].strip()))


def summarize(rows, threshold, k):
    retained = expected = available = recalled = negative_with_candidates = 0
    all_evidence = all_available = evidence_rounds = 0
    counts, chars = [], []
    for row in rows:
        candidates = [item for item in row["by_top_k"][str(k)] if item["score"] >= threshold]
        counts.append(len(candidates))
        chars.append(candidate_chars(candidates))
        if row["negative"]:
            negative_with_candidates += bool(candidates)
        round_retained = 0
        for evidence in row["evidence"]:
            expected += 1
            available += evidence["present_in_chunks"]
            eligible = [item for item in candidates if item["source"] == evidence["source"]
                        and normalize(evidence["quote"]) in normalize(item["content"])]
            found = evidence["by_top_k"][str(k)]["recalled"]
            recalled += found
            retained += bool(eligible)
            round_retained += bool(eligible)
        if row["evidence"]:
            evidence_rounds += 1
            all_evidence += round_retained == len(row["evidence"])
            if all(item["present_in_chunks"] for item in row["evidence"]):
                all_available += round_retained == len(row["evidence"])
    negatives = sum(item["negative"] for item in rows)
    present_rounds = sum(bool(row["evidence"]) and all(item["present_in_chunks"]
                         for item in row["evidence"]) for row in rows)
    return {"threshold": threshold, "top_k": k, "rounds": len(rows),
            "expected_quotes": expected, "available_quotes": available,
            "retained_quotes": retained, "recalled_quotes_before_threshold": recalled,
            "evidence_retention": retained / expected if expected else None,
            "available_evidence_retention": retained / available if available else None,
            "all_evidence_rounds": all_evidence, "evidence_rounds": evidence_rounds,
            "all_available_rounds": all_available, "available_rounds": present_rounds,
            "negative_rounds": negatives, "negative_with_candidates": negative_with_candidates,
            "negative_contamination": negative_with_candidates / negatives if negatives else None,
            "average_candidates": statistics.mean(counts) if counts else 0,
            "average_candidate_chars": statistics.mean(chars) if chars else 0,
            # 中文字符~0.5-1.5 token仅做区间估计，不冒充供应商token实测。
            "estimated_candidate_tokens": [statistics.mean(chars) * .5,
                                           statistics.mean(chars) * 1.5] if chars else [0, 0],
            "nonempty_rounds": sum(value > 0 for value in counts)}


def transform_blocks(blocks, variant):
    result = []
    examples_started = set()
    for item in blocks:
        content = item["content"]
        if variant == "without_header" and item["chunk_index"] == 0:
            content = "\n".join(line for line in content.splitlines()
                                 if "虚构评估素材" not in line
                                 and not line.startswith("文档编号 "))
        elif variant == "without_examples":
            if item["source"] in examples_started:
                continue  # PDF长示例跨块时，后续块可能根本不再含“示例”字样。
            match = re.search(r"(?:^|\n)示例\s+D\d+-E\d+|这是制度应用示例", content)
            if match:
                content = content[:match.start()].strip()
                examples_started.add(item["source"])
        elif variant != "original":
            if variant not in {"without_header", "without_examples"}:
                raise ValueError("Unknown corpus variant")
        if content.strip():
            result.append({**item, "content": content.strip()})
    return result


class OfflineGuard:
    """Python网络和写入闸门；原生Chroma路径另外显式重定向并做data快照。"""
    def __init__(self, roots):
        self.roots = [Path(root).resolve() for root in roots]
        self.network_attempts = 0
        self.model_attempts = 0

    def check_write(self, filename):
        if not isinstance(filename, (str, bytes, os.PathLike)):
            return
        if os.fsdecode(filename).lower() == os.devnull.lower():
            return
        path = Path(os.fsdecode(filename)).resolve()
        if not any(path == root or root in path.parents for root in self.roots):
            raise RuntimeError("Write outside isolated runtime/results blocked")

    def audit(self, event, values):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
            self.network_attempts += 1
            raise RuntimeError("Network forbidden in zero-API measurement")
        if event == "open":
            filename, mode, flags = values
            writing = (isinstance(mode, str) and any(char in mode for char in "wax+")) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if writing:
                self.check_write(filename)
        elif event in {"os.mkdir", "os.remove", "os.rmdir"}:
            self.check_write(values[0])
        elif event == "os.rename":
            self.check_write(values[0])
            self.check_write(values[1])

    def forbid_model(self, *args, **kwargs):
        self.model_attempts += 1
        raise RuntimeError("LLM forbidden in zero-API measurement")


class CachedLocalEmbedding:
    """只缓存真实ONNX结果；不使用替身，不改变向量或检索打分。"""
    def __init__(self, original):
        self.original = original
        self.cache = {}

    def __call__(self, input):
        texts = list(input)
        missing = list(dict.fromkeys(text for text in texts if text not in self.cache))
        if missing:
            self.cache.update(zip(missing, self.original(missing)))
        return [self.cache[text] for text in texts]

    def name(self):
        return self.original.name()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def measure_variant(memory, blocks, rounds, path, include_full=True):
    """三种top_k各调原检索入口；quote定位依赖原始chunk_index。"""
    import config
    memory.close_resources()
    config.VECTORDB_PATH = str(path)
    collection = memory._get_document_collection()
    # 临时实验语料用实际原始块编号；保存原件时已走save_document校验。
    for start in range(0, len(blocks), config.INGEST_CHUNK_BATCH_SIZE):
        batch = blocks[start:start + config.INGEST_CHUNK_BATCH_SIZE]
        collection.add(ids=[item["doc_id"] + ":" + str(item["chunk_index"]) for item in batch],
                       documents=[item["content"] for item in batch],
                       metadatas=[{key: item[key] for key in ("source", "doc_id", "chunk_index")}
                                  for item in batch])
    memory.mark_document_bm25_dirty()
    doc_ids = list(dict.fromkeys(item["doc_id"] for item in blocks))
    rows = []
    for index, round_item in enumerate(rounds):
        for mode in ("raw", "history"):
            query = round_item["question"] if mode == "raw" else round_item["history_query"]
            by_k = {str(k): memory.search_documents(query, top_k=k, verified_doc_ids=doc_ids,
                                                   enable_rerank=False) for k in TOP_KS}
            ranking = memory.search_documents(query, top_k=len(blocks), verified_doc_ids=doc_ids,
                                              enable_rerank=False) if include_full else by_k["10"]
            rows.append({**round_item, "query_mode": mode, "query": query,
                         "full_ranking": ranking, "by_top_k": by_k,
                         "top_score": ranking[0]["score"] if ranking else None,
                         "evidence": [describe_evidence(item, blocks, ranking, by_k)
                                      for item in round_item["evidence"]]})
        if (index + 1) % 10 == 0 or index == len(rounds) - 1:
            print("RECALL rounds=%d/%d" % (index + 1, len(rounds)), flush=True)
    return rows


def make_summary(rows):
    result = {"grid": [], "categories": [], "negative_scores": []}
    for mode in ("raw", "history"):
        selected = [row for row in rows if row["query_mode"] == mode]
        for threshold in THRESHOLDS:
            for k in TOP_KS:
                result["grid"].append({"query_mode": mode, **summarize(selected, threshold, k)})
        for category in sorted({row["category"] for row in selected}):
            group = [row for row in selected if row["category"] == category]
            result["categories"].append({"query_mode": mode, "category": category,
                                         **summarize(group, .55, 5),
                                         "top10": summarize(group, .55, 10)})
        result["negative_scores"].extend({"id": row["id"], "query_mode": mode,
                                           "highest_score": row["top_score"]}
                                          for row in selected if row["negative"])
    return result


def write_report(output, variants, metadata):
    lines = ["# 本地召回测量（零API）", "", "## 口径", "",
             "- 57题逐轮，共69轮；每轮原话/确定性拼接全部之前的用户消息，两种查询。",
             "- quote允许忽略排版空白，不推断同义。多轮按turn_checks的expected_sources限定；"
             "M02/3和M04/2补充逐轮quote，不修改题库。仅对有quote的轮计算召回率。",
             "- 完整库排名用于定位；top_k=5/8/10各跑真实检索，候选池分别为20/32/40。",
             "- 保留率按quote计：多个块可支持同一quote，任一保留即命中。包含提取缺失的总分母，"
             "另列在库quote的保留率。R/U混入率仅指无答案题出现过阈值候选，不等于最终编造率。",
             "- token为候选字符×0.5–1.5的估计区间，不是API实测；不包含固定提示词、历史和输出。",
             "- 关闭精排，故不能实测强证据/反思走向。新增非空候选轮可能多走筛选或反思，"
             "不是已观测到的线上调用增量。", "", "## 原语料阈值组合", "",
             "|查询|阈值|top_k|quote保留/总数|在库保留率|R/U混入|平均候选|平均字符|",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in variants["original"]["summary"]["grid"]:
        rate = row["available_evidence_retention"]
        lines.append("|%s|%.2f|%d|%d/%d|%.1f%%|%d/%d|%.2f|%.1f|" % (
            row["query_mode"], row["threshold"], row["top_k"], row["retained_quotes"],
            row["expected_quotes"], (rate or 0) * 100, row["negative_with_candidates"],
            row["negative_rounds"], row["average_candidates"], row["average_candidate_chars"]))
    lines += ["", "## 类别（0.55/top5）", "",
              "|查询|类别|轮数|quote保留/总数|top10过阈值保留|全部证据轮/有证据轮|",
              "|---|---|---:|---:|---:|---:|"]
    for row in variants["original"]["summary"]["categories"]:
        lines.append("|%s|%s|%d|%d/%d|%d/%d|%d/%d|" % (
            row["query_mode"], row["category"], row["rounds"], row["retained_quotes"],
            row["expected_quotes"], row["top10"]["retained_quotes"], row["expected_quotes"],
            row["all_evidence_rounds"], row["evidence_rounds"]))
    lines += ["", "## 隔离和费用", "", "```json", json.dumps(metadata, ensure_ascii=False, indent=2), "```"]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args):
    repo = Path(__file__).resolve().parents[2]
    if Path(sys.executable).resolve() != (repo / ".venv/Scripts/python.exe").resolve():
        raise RuntimeError("Use project .venv Python")
    sys.dont_write_bytecode = True
    output = Path(args.output or repo / "backups/eval" / ("recall-" + time.strftime("%Y%m%d-%H%M%S"))).resolve()
    if not args.worker_runtime:
        # Windows HNSW句柄可能活到进程退出；父进程只在子进程退出之后清理自己创建的目录。
        work = Path(tempfile.mkdtemp(prefix="zhitian-recall-")).resolve()
        try:
            result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()),
                                     "--output", str(output), "--worker-runtime", str(work)], cwd=repo)
        finally:
            shutil.rmtree(work)
        metadata_path = output / "metadata.json"
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["runtime_removed"] = not work.exists()
            write_json(metadata_path, metadata)
            variants = {name: json.loads((output / (name + ".json")).read_text(encoding="utf-8"))
                        for name in ("original", "without_header", "without_examples")
                        if (output / (name + ".json")).exists()}
            if "original" in variants:
                write_report(output, variants, metadata)
        if result.returncode:
            raise RuntimeError("Measurement worker failed")
        print("RESULT " + str(output), flush=True)
        return
    output.mkdir(parents=True, exist_ok=False)
    probe = output / "ignore-check.txt"
    probe.write_text("ignore check", encoding="utf-8")
    ignored = subprocess.run(["git", "check-ignore", "-q", str(probe)], cwd=repo).returncode == 0
    probe.unlink()
    if not ignored:
        raise RuntimeError("Results must be Git ignored")
    before = snapshot_data(repo / "data")
    write_json(output / "default_data_before.json", before)
    revision = source_revision(repo)
    work = Path(args.worker_runtime).resolve()
    if (repo == work or repo in work.parents or not work.name.startswith("zhitian-recall-")
            or work.parent != Path(tempfile.gettempdir()).resolve() or any(work.iterdir())):
        raise RuntimeError("Runtime must be outside repository")
    os.environ["PERSONAL_DEEPSEEK_KEY_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    os.environ["DEEPSEEK_API_KEY"] = ""
    os.environ["TAVILY_API_KEY"] = ""
    os.environ["GRAPH_RAG_ENABLED"] = "false"
    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    guard = OfflineGuard((work, output))
    sys.addaudithook(guard.audit)
    memory = None
    variants = {}
    metadata = {"revision": revision, "runtime": str(work), "results_ignored": ignored,
                "embedding": "real local BGE-small-zh-v1.5 ONNX", "paid_calls": 0}
    try:
        import config
        config.BASE_DIR = str(work)
        config.HISTORY_DB_PATH = str(work / "data/history.db")
        config.VECTORDB_PATH = str(work / "data/vectordb")
        config.GRAPH_RAG_ENABLED = False
        config.RERANK_ENABLED = False
        from layers import document_loader, embedding, llm_provider, memory
        llm_provider.chat_completion = guard.forbid_model
        original_embedding = embedding.get_embedding_function()
        if not isinstance(original_embedding, embedding.BgeSmallZhEmbeddingFunction):
            raise RuntimeError("Real ONNX embedding required")
        cache = CachedLocalEmbedding(original_embedding)
        embedding.get_embedding_function = lambda: cache
        dataset = json.loads((repo / "tests/eval/questions.json").read_text(encoding="utf-8"))
        manifest = json.loads((repo / "tests/eval/manifest.json").read_text(encoding="utf-8"))
        rounds = expand_rounds(dataset["questions"])
        blocks, documents = [], []
        for entry in manifest["documents"]:
            source = entry["file"]
            text = document_loader.load_document(str(repo / "tests/eval/corpus" / source))
            if text.startswith("错误："):
                raise RuntimeError("Corpus extraction failed: " + source)
            chunks = document_loader.chunk_text(text)
            doc_id = "recall-" + Path(source).stem
            stored = memory.save_document(source, chunks, doc_id)
            if stored != len(chunks) or stored != entry["chunks"]:
                raise RuntimeError("Corpus chunk count changed: " + source)
            documents.append({"source": source, "chunks": stored})
            blocks.extend({"source": source, "doc_id": doc_id, "chunk_index": index,
                           "content": chunk} for index, chunk in enumerate(chunks))
        metadata.update(documents=documents, chunks=len(blocks), rounds=len(rounds),
                        queries=len(rounds) * 2, rag_threshold=config.RAG_SCORE_THRESHOLD,
                        bm25_scale=config.BM25_SCORE_SCALE,
                        embedding_model_sha256=hashlib.sha256(
                            Path(original_embedding._model_dir, "model.onnx").read_bytes()).hexdigest())
        write_json(output / "chunks.json", blocks)
        for variant in ("original", "without_header", "without_examples"):
            print("VARIANT " + variant, flush=True)
            current = transform_blocks(blocks, variant)
            rows = measure_variant(memory, current, rounds, work / "data" / variant)
            summary = make_summary(rows)
            variants[variant] = {"chunks": len(current), "rows": rows, "summary": summary}
            write_json(output / (variant + ".json"), variants[variant])
        metadata["success"] = True
    finally:
        if memory is not None:
            memory.close_resources()
        logging.shutdown()
        after = snapshot_data(repo / "data")
        write_json(output / "default_data_after.json", after)
        metadata.update(default_data_unchanged=before == after, default_data_files=len(before),
                        runtime_removed=not work.exists(), network_attempts=guard.network_attempts,
                        model_attempts=guard.model_attempts)
        write_json(output / "metadata.json", metadata)
        if "original" in variants:
            write_report(output, variants, metadata)
        if before != after or guard.network_attempts or guard.model_attempts:
            raise RuntimeError("Isolation/zero-API invariant failed")
    print("RESULT " + str(output), flush=True)


if __name__ == "__main__":
    # 直接运行时先补仓库路径，模块导入仍不初始化应用。
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--worker-runtime", help=argparse.SUPPRESS)
    run(parser.parse_args())
