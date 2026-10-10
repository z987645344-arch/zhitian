# -*- coding: utf-8 -*-
"""F39：关闭时释放 Chroma 系统，并允许下一次检索重新初始化。"""

import asyncio
import logging
from pathlib import Path

import pytest
from chromadb.api.client import SharedSystemClient

import main
from layers import memory
from scripts import backup_data


def test_backup_snapshot_close_preserves_running_application_system(tmp_path):
    memory.save_document("说明.txt", ["用于生命周期测试的资料"], doc_id="close-test")
    client = memory._chroma_client
    identifier, system = client._identifier, client._system
    snapshot = tmp_path / "snapshot"
    try:
        backup_data._copy_chroma_snapshot(Path(identifier), snapshot)
        assert backup_data.chroma_collection_counts(snapshot) == {"zhitian_documents": 1}
        assert str(snapshot) not in SharedSystemClient._identifer_to_system
        assert SharedSystemClient._identifer_to_system[identifier] is system
        assert system._running
        assert client.get_collection("zhitian_documents").count() == 1
        memory.close_resources()
        assert not system._running
    finally:
        # 修复前故障复现也必须释放主库：全局缓存被清空不代表系统已停止。
        if memory._chroma_client is client and identifier not in SharedSystemClient._identifer_to_system:
            SharedSystemClient._identifer_to_system[identifier] = system


def _isolate_lifespan_background(monkeypatch):
    monkeypatch.setattr(main.backup_scheduler, "start_scheduler", lambda: None)
    monkeypatch.setattr(main.backup_scheduler, "stop_scheduler", lambda: None)
    monkeypatch.setattr(main.temporary_files, "start_cleanup", lambda: None)
    monkeypatch.setattr(main.temporary_files, "stop_cleanup", lambda: None)
    monkeypatch.setattr(main.get_file_processor_registry(), "start_probes", lambda: None)
    monkeypatch.setattr(main, "_active_http_requests", 0)
    monkeypatch.setattr(main, "_accepting_requests", True)


def test_lifespan_after_snapshot_stops_real_chroma_without_warning(tmp_path, monkeypatch, caplog):
    _isolate_lifespan_background(monkeypatch)

    async def run_case():
        async with main.lifespan(main.app):
            memory.save_document("说明.txt", ["生命周期资料"], doc_id="lifespan-test")
            system = memory._chroma_client._system
            snapshot = tmp_path / "lifespan-snapshot"
            backup_data._copy_chroma_snapshot(Path(memory._chroma_client._identifier), snapshot)
            backup_data.chroma_collection_counts(snapshot)
            assert system._running
        return system

    with caplog.at_level(logging.WARNING):
        system = asyncio.run(run_case())
    assert not system._running
    assert memory._chroma_client is None
    assert not any("关闭Chroma资源失败" in record.getMessage() for record in caplog.records)
    memory.close_resources()  # 重复关闭无副作用。


@pytest.mark.parametrize("failure", [RuntimeError, KeyError])
def test_real_chroma_stop_failure_still_warns_and_can_retry(monkeypatch, caplog, failure):
    _isolate_lifespan_background(monkeypatch)
    memory.save_document("说明.txt", ["关闭失败测试"], doc_id="stop-failure")
    client = memory._chroma_client

    async def run_case():
        async with main.lifespan(main.app):
            pass

    with monkeypatch.context() as patch:
        def fail_stop():
            raise failure("simulated stop failure")
        patch.setattr(client._system, "stop", fail_stop)
        with caplog.at_level(logging.WARNING):
            asyncio.run(run_case())
        warnings = [r for r in caplog.records if "关闭Chroma资源失败" in r.getMessage()]
        assert len(warnings) == 1
        assert warnings[0].levelno == logging.WARNING
        assert "error_type=" + failure.__name__ in warnings[0].getMessage()
        assert memory._chroma_client is client
        assert client._identifier in SharedSystemClient._identifer_to_system
    memory.close_resources()
    assert client._identifier not in SharedSystemClient._identifer_to_system


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
