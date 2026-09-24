# -*- coding: utf-8 -*-
"""F39：关闭时释放 Chroma 系统，并允许下一次检索重新初始化。"""

from chromadb.api.client import SharedSystemClient

from layers import memory


def test_close_resources_releases_shared_system_and_reopens_search():
    memory.save_document("restart.txt", ["合同履行义务与违约责任"], doc_id="f39-reopen")
    first_client = memory._chroma_client
    identifier = first_client._identifier
    system = first_client._system
    assert identifier in SharedSystemClient._identifer_to_system
    assert system._running

    memory.close_resources()

    assert identifier not in SharedSystemClient._identifer_to_system
    assert not system._running
    assert memory._chroma_client is None
    assert memory._document_collection is None

    results = memory.search_documents(
        "合同违约责任", verified_doc_ids=["f39-reopen"], enable_rerank=False
    )
    assert results and results[0]["doc_id"] == "f39-reopen"
    assert memory._chroma_client is not first_client
    assert identifier in SharedSystemClient._identifer_to_system
