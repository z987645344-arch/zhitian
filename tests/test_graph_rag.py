# -*- coding: utf-8 -*-
"""GraphRAG：默认关闭的回归安全网、实体去重、建图失败降级、图扩展上限与重排序接入。"""

import pytest

import config
from layers import auth, graph_store, memory


@pytest.fixture(autouse=True)
def isolated_graph_database(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "USERS_DB_PATH", str(tmp_path / "users.db"))
    auth.init_db()
    graph_store.init_db()


def _payload(entities, relationships=()):
    return {
        "entities": [
            {"name": name, "type": kind, "description": desc}
            for name, kind, desc in entities
        ],
        "relationships": [
            {"source": s, "target": t, "description": d} for s, t, d in relationships
        ],
    }


def _count(table):
    with auth._connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0])


# ---------------------------------------------------------------- 回归安全网


def test_disabled_by_default_and_save_search_unchanged(
    isolated_chroma, monkeypatch
):
    """默认关闭：save_document与search_documents完全不触碰图谱逻辑。"""
    assert config.GRAPH_RAG_ENABLED is False

    called = []
    monkeypatch.setattr(
        graph_store, "build_chunk_graph",
        lambda *a, **k: called.append("build") or True,
    )
    monkeypatch.setattr(
        graph_store, "expand_chunk_keys",
        lambda *a, **k: called.append("expand") or [],
    )

    count = memory.save_document("plain.txt", ["劳动合同的解除条件"], doc_id="plain-1")
    assert count == 1
    results = memory.search_documents(
        "劳动合同", top_k=5, verified_doc_ids=["plain-1"], enable_rerank=False
    )
    assert results and results[0]["doc_id"] == "plain-1"
    # 关闭状态下两个图谱入口都没有被调用，也没有产生任何图谱数据
    assert called == []
    assert _count("chunk_entities") == 0
    assert not any(item.get("graph_expanded") for item in results)


# ---------------------------------------------------------------- 建图


def test_entity_dedupe_by_name(monkeypatch):
    graph_store.store_chunk_graph(
        "doc-a:0", _payload([("劳动合同法", "法条", "第一版描述")])
    )
    graph_store.store_chunk_graph(
        "doc-b:0", _payload([("劳动合同法", "法律", "另一段描述")])
    )
    # 同名实体复用同一条记录，两个chunk各自建立关联
    assert _count("graph_entities") == 1
    assert _count("chunk_entities") == 2
    with auth._connect() as conn:
        row = conn.execute("SELECT entity_type FROM graph_entities").fetchone()
    assert row["entity_type"] == "法条"  # 首次写入的类型保留，不被后续覆盖


def test_relationship_written_and_deduped():
    payload = _payload(
        [("甲公司", "机构", None), ("乙员工", "人物", None)],
        [("甲公司", "乙员工", "劳动关系")],
    )
    graph_store.store_chunk_graph("doc-a:0", payload)
    graph_store.store_chunk_graph("doc-a:1", payload)
    assert _count("graph_relationships") == 1  # 相同三元组不重复写入


def test_extraction_failure_does_not_block_document_save(
    isolated_chroma, monkeypatch
):
    """建图失败只记录日志并跳过，文档本身仍正常保存且可被检索。"""
    monkeypatch.setattr(config, "GRAPH_RAG_ENABLED", True)
    monkeypatch.setattr(
        graph_store, "extract_chunk_graph",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("provider down")),
    )

    count = memory.save_document("fail.txt", ["经济补偿金的计算方式"], doc_id="fail-1")
    assert count == 1
    assert _count("chunk_entities") == 0
    results = memory.search_documents(
        "经济补偿金", top_k=5, verified_doc_ids=["fail-1"], enable_rerank=False
    )
    assert results and results[0]["doc_id"] == "fail-1"


def test_build_chunk_graph_uses_stable_chunk_key(monkeypatch):
    monkeypatch.setattr(
        graph_store, "extract_chunk_graph",
        lambda *a, **k: _payload([("经济补偿金", "概念", None)]),
    )
    assert graph_store.build_chunk_graph(graph_store.chunk_key("doc-x", 2), "文本") is True
    with auth._connect() as conn:
        row = conn.execute("SELECT chunk_id FROM chunk_entities").fetchone()
    assert row["chunk_id"] == "doc-x:2"
    assert graph_store.doc_id_from_chunk_key("doc-x:2") == "doc-x"


# ---------------------------------------------------------------- 图扩展


def test_expansion_finds_related_chunks_and_respects_limit():
    # 种子chunk与另外两个chunk共享实体，第三个chunk经关系相连
    graph_store.store_chunk_graph("seed:0", _payload([("劳动合同", "概念", None)]))
    graph_store.store_chunk_graph("other:0", _payload([("劳动合同", "概念", None)]))
    graph_store.store_chunk_graph("other:1", _payload([("劳动合同", "概念", None)]))
    graph_store.store_chunk_graph(
        "linked:0",
        _payload(
            [("经济补偿金", "概念", None), ("劳动合同", "概念", None)],
            [("劳动合同", "经济补偿金", "解除后需支付")],
        ),
    )

    expanded = graph_store.expand_chunk_keys(["seed:0"], limit=10)
    assert "seed:0" not in expanded  # 种子自身被排除
    assert {"other:0", "other:1", "linked:0"} <= set(expanded)

    # 上限生效
    assert len(graph_store.expand_chunk_keys(["seed:0"], limit=2)) == 2
    assert graph_store.expand_chunk_keys(["seed:0"], limit=0) == []
    assert graph_store.expand_chunk_keys([], limit=10) == []


def test_expansion_limit_follows_multiplier(monkeypatch):
    monkeypatch.setattr(config, "GRAPH_EXPANSION_MAX_MULTIPLIER", 2.0)
    assert graph_store.expansion_limit(3) == 6
    monkeypatch.setattr(config, "GRAPH_EXPANSION_MAX_MULTIPLIER", 0.0)
    assert graph_store.expansion_limit(3) == 0


def test_expanded_candidates_enter_pipeline_and_respect_verified_scope(
    isolated_chroma, monkeypatch
):
    """图扩展候选并入候选池、带传播分、受verified白名单约束，且不新增模型调用。"""
    monkeypatch.setattr(config, "GRAPH_RAG_ENABLED", True)
    monkeypatch.setattr(graph_store, "extract_chunk_graph", lambda *a, **k: None)

    memory.save_document("seed.txt", ["劳动合同解除的法定情形"], doc_id="seed-doc")
    memory.save_document("far.txt", ["经济补偿金按工作年限计算"], doc_id="far-doc")
    memory.save_document("hidden.txt", ["未审核的关联材料"], doc_id="hidden-doc")

    shared = _payload([("劳动合同", "概念", None)])
    graph_store.store_chunk_graph("seed-doc:0", shared)
    graph_store.store_chunk_graph("far-doc:0", shared)
    graph_store.store_chunk_graph("hidden-doc:0", shared)

    rerank_calls = []
    monkeypatch.setattr(
        memory, "_rerank_candidates",
        lambda query, candidates, **k: rerank_calls.append(len(candidates)) or candidates,
    )
    # 小语料下向量召回会把全部文档都返回，far-doc 本身就成了种子、无从体现扩展。
    # 这里把向量召回收窄为只命中 seed-doc，模拟真实大语料中关联文档未被召回的情形。
    monkeypatch.setattr(
        memory, "_query_document_memory",
        lambda collection, query, top_k, allowed: {
            "documents": [["劳动合同解除的法定情形"]],
            "metadatas": [[{
                "source": "seed.txt", "doc_id": "seed-doc",
                "chunk_index": 0, "total_chunks": 1,
            }]],
            "distances": [[0.15]],
        },
    )
    monkeypatch.setattr(memory, "_search_bm25_candidates", lambda *a, **k: [])

    results = memory.search_documents(
        "劳动合同解除",
        top_k=10,
        verified_doc_ids=["seed-doc", "far-doc"],
        enable_rerank=True,
    )
    doc_ids = {item["doc_id"] for item in results}
    assert "far-doc" in doc_ids  # 靠图关系带进来的候选
    assert "hidden-doc" not in doc_ids  # 不在verified白名单内，必须被排除

    expanded = [item for item in results if item.get("graph_expanded")]
    assert expanded, "应存在图扩展候选"
    assert all(item["score"] > 0 for item in expanded)  # 传播分让其能过阈值判断
    assert len(rerank_calls) == 1  # 仍然只有一次重排序调用，不新增模型调用
