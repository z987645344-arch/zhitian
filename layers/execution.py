# -*- coding: utf-8 -*-
# 执行层：工具调用统一入口

import json
import os
import re
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from collections.abc import Iterator
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field
from tavily import TavilyClient
import config
from layers import attachments, auth, converter, files_store, llm_provider, memory
from utils.logger import get_logger
from utils import observability
from utils.time_context import cache_friendly_messages, current_date_prompt

logger = get_logger("execution")


class Citation(BaseModel):
    source: str
    doc_id: str
    chunk_index: int
    score: float


class ToolResult(BaseModel):
    tool: str
    status: str
    data: str
    error_msg: str = ""
    citations: list[Citation] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class DocumentListItem(BaseModel):
    source: str
    doc_id: str
    trust_level: str = "verified"


class GenerateFileResult(BaseModel):
    success: bool
    file_id: str = ""
    download_filename: str = ""
    char_count: int = 0
    error_type: str = ""
    requested_format: str = "md"
    delivered_format: str = ""
    conversion_error_type: Optional[str] = None


class ConvertDocumentResult(BaseModel):
    success: bool
    file_id: str = ""
    download_filename: str = ""
    error_type: str = ""

# 执行层错误处理规则
MAX_RETRIES = 1
RETRY_DELAY = 1.0
TIMEOUT = 10.0

# 工具注册表：新增工具在此注册
TOOL_REGISTRY = {
    "search_web": "_search_web",
    "llm_chat": "_llm_chat",
    "search_documents": "_search_documents",
    "list_documents": "_list_documents",
    "convert_document": "_convert_document",
    "generate_file": "generate_file",
}


def run(tool: str, params: dict) -> ToolResult:
    """统一工具调用入口"""
    if tool not in TOOL_REGISTRY:
        return ToolResult(tool=tool, status="error", data="", error_msg=f"未知工具：{tool}")

    func = globals()[TOOL_REGISTRY[tool]]
    last_error = ""
    max_attempts = 1 if tool in {
        "search_web", "llm_chat", "convert_document", "generate_file"
    } else MAX_RETRIES + 1
    for attempt in range(max_attempts):
        try:
            result = func(**params)
            if isinstance(result, ToolResult):
                return result
            if isinstance(result, GenerateFileResult):
                return ToolResult(
                    tool=tool,
                    status="success" if result.success else "error",
                    data=result.model_dump_json(),
                    error_msg=result.error_type if not result.success else "",
                    metadata=result.model_dump(),
                )
            if isinstance(result, ConvertDocumentResult):
                return ToolResult(
                    tool=tool,
                    status="success" if result.success else "error",
                    data=result.model_dump_json(),
                    error_msg=result.error_type if not result.success else "",
                    metadata=result.model_dump(),
                )
            return ToolResult(tool=tool, status="success", data=result, error_msg="")
        except Exception as e:
            last_error = str(e)
            logger.warning("工具调用失败：tool=%s attempt=%s error_type=%s", tool, attempt + 1, type(e).__name__)
            if attempt + 1 < max_attempts:
                time.sleep(RETRY_DELAY)

    return ToolResult(tool=tool, status="error", data="", error_msg=last_error)


def _search_web(
    query: str,
    context: list[str] = None,
    session_id: str = None,
    tier: str = "fast",
    total_budget: Optional[float] = None,
) -> str:
    """联网搜索：先优化搜索query，再调用Tavily并整理成自然语言回复"""
    if not _has_valid_key(config.TAVILY_API_KEY, "TAVILY"):
        raise ValueError("TAVILY_API_KEY未配置")

    started_at = time.perf_counter()
    search_budget = min(
        config.SEARCH_TOTAL_TIMEOUT,
        float(total_budget or config.SEARCH_TOTAL_TIMEOUT),
    )
    deadline = started_at + max(0.001, search_budget)
    original_question = query
    optimized_query = _rewrite_search_query(
        original_question,
        context,
        timeout=min(config.SEARCH_QUERY_REWRITE_TIMEOUT, _remaining_budget(deadline)),
        tier=tier
    )
    client = TavilyClient(api_key=config.TAVILY_API_KEY)
    try:
        result = _search_tavily_with_retry(client, optimized_query, deadline=deadline)
    except Exception as e:
        logger.warning("Tavily调用失败，降级为模型知识回答：error_type=%s", type(e).__name__)
        return _fallback_llm_answer(
            original_question,
            session_id=session_id,
            context=context,
            prefix="（搜索服务暂时不可用，以下为模型知识回答）",
            timeout=_remaining_budget(deadline),
            tier=tier
        )

    results = result.get("results", []) if isinstance(result, dict) else []
    _log_tavily_success(results)
    if not results:
        logger.warning("Tavily返回空结果，降级为模型知识回答：query_len=%s", len(optimized_query or ""))
        return _fallback_llm_answer(
            original_question,
            session_id=session_id,
            context=context,
            prefix="（网络搜索无结果，以下为模型知识回答）",
            timeout=_remaining_budget(deadline),
            tier=tier
        )

    if _has_low_search_relevance(results):
        logger.warning("Tavily搜索结果相关性不足，降级为模型知识回答：query_len=%s", len(optimized_query or ""))
        return _fallback_llm_answer(
            original_question,
            session_id=session_id,
            context=context,
            prefix="（搜索结果相关性不足，以下为模型知识回答）",
            timeout=_remaining_budget(deadline),
            tier=tier
        )

    remaining = _remaining_budget(deadline)
    if remaining <= 0:
        observability.record_search_fallback()
        return _format_raw_search_results(
            results,
            prefix="（搜索链路已达到时间预算，以下为原始搜索结果摘要）"
        )

    search_results = json.dumps(result, ensure_ascii=False)
    try:
        return _llm_chat(
            message=original_question,
            search_results=search_results,
            original_question=original_question,
            tier=tier,
            timeout=remaining
        )
    except Exception as e:
        logger.warning(
            "搜索结果整理失败：query_len=%s error_type=%s",
            len(optimized_query or ""),
            type(e).__name__
        )
        observability.record_search_fallback()
        return _format_raw_search_results(
            results,
            prefix="（搜索结果整理失败，以下为原始搜索结果摘要）"
        )


def stream_search_result(
    query: str,
    context: list[str] = None,
    session_id: str = None,
    tier: str = "fast"
) -> Iterator[str]:
    """流式联网搜索：Tavily完成后用所选模型逐chunk整理搜索结果。"""
    if not _has_valid_key(config.TAVILY_API_KEY, "TAVILY"):
        raise ValueError("TAVILY_API_KEY未配置")

    deadline = time.perf_counter() + config.SEARCH_TOTAL_TIMEOUT
    original_question = query
    optimized_query = _rewrite_search_query(
        original_question,
        context,
        timeout=min(config.SEARCH_QUERY_REWRITE_TIMEOUT, _remaining_budget(deadline)),
        tier=tier
    )
    client = TavilyClient(api_key=config.TAVILY_API_KEY)
    try:
        result = _search_tavily_with_retry(client, optimized_query, deadline=deadline)
    except Exception as e:
        logger.warning("Tavily调用失败，流式搜索降级为模型知识回答：error_type=%s", type(e).__name__)
        yield _fallback_llm_answer(
            original_question,
            session_id=session_id,
            context=context,
            prefix="（搜索服务暂时不可用，以下为模型知识回答）",
            timeout=_remaining_budget(deadline),
            tier=tier
        )
        return

    results = result.get("results", []) if isinstance(result, dict) else []
    _log_tavily_success(results)
    if not results:
        logger.warning("Tavily返回空结果，流式搜索降级为模型知识回答：query_len=%s", len(optimized_query or ""))
        yield _fallback_llm_answer(
            original_question,
            session_id=session_id,
            context=context,
            prefix="（网络搜索无结果，以下为模型知识回答）",
            timeout=_remaining_budget(deadline),
            tier=tier
        )
        return

    if _has_low_search_relevance(results):
        logger.warning("Tavily搜索结果相关性不足，流式搜索降级为模型知识回答：query_len=%s", len(optimized_query or ""))
        yield _fallback_llm_answer(
            original_question,
            session_id=session_id,
            context=context,
            prefix="（搜索结果相关性不足，以下为模型知识回答）",
            timeout=_remaining_budget(deadline),
            tier=tier
        )
        return

    remaining = _remaining_budget(deadline)
    if remaining <= 0:
        yield _format_raw_search_results(
            results,
            prefix="（搜索链路已达到时间预算，以下为原始搜索结果摘要）"
        )
        return

    search_results = json.dumps(result, ensure_ascii=False)
    emitted = False
    try:
        stream = _llm_chat(
            message=original_question,
            search_results=search_results,
            original_question=original_question,
            stream=True,
            tier=tier,
            timeout=remaining
        )
        for chunk in stream:
            emitted = True
            yield chunk
    except Exception as e:
        logger.warning(
            "流式搜索结果整理失败：query_len=%s emitted=%s error_type=%s",
            len(optimized_query or ""),
            emitted,
            type(e).__name__
        )
        if emitted:
            return
        observability.record_search_fallback()
        yield _format_raw_search_results(
            results,
            prefix="（搜索结果整理失败，以下为原始搜索结果摘要）"
        )


def _search_documents(
    query: str,
    tier: str = "fast",
    generate_answer: bool = True,
    rerank_enabled: bool = True,
    context: list[str] = None,
    timeout: Optional[float] = None,
) -> ToolResult:
    """检索已上传的本地文档并整理为自然语言。"""
    verified_doc_ids = auth.get_verified_doc_ids()
    results = memory.search_documents(
        query,
        top_k=5,
        verified_doc_ids=verified_doc_ids,
        tier=tier,
        enable_rerank=rerank_enabled,
        timeout=timeout,
    )
    if not results and context and generate_answer:
        return _answer_from_supplied_context(query, context, tier, timeout=timeout)
    if not results:
        return ToolResult(
            tool="search_documents",
            status="success",
            data="未找到可靠依据，无法确认答案",
            citations=[]
        )

    best_score = max(float(item.get("score", 0.0)) for item in results)
    trusted_results = [
        item for item in results
        if float(item.get("score", 0.0)) >= config.RAG_SCORE_THRESHOLD
    ]
    if best_score < config.RAG_SCORE_THRESHOLD or not trusted_results:
        logger.info(
            "文档检索低置信度拒答：query_len=%s best_score=%.4f threshold=%.4f",
            len(query or ""),
            best_score,
            config.RAG_SCORE_THRESHOLD
        )
        if context and generate_answer:
            return _answer_from_supplied_context(query, context, tier, timeout=timeout)
        return ToolResult(
            tool="search_documents",
            status="success",
            data="未找到可靠依据，无法确认答案",
            citations=[]
        )

    citations = [
        Citation(
            source=str(item.get("source", "")),
            doc_id=str(item.get("doc_id", "")),
            chunk_index=int(item.get("chunk_index", 0)),
            score=float(item.get("score", 0.0))
        )
        for item in trusted_results
    ]
    if generate_answer and context:
        context_result = _answer_from_supplied_context(query, context, tier, timeout=timeout)
        answer = context_result.data
    elif generate_answer:
        answer = _answer_from_documents(query, trusted_results, tier=tier, timeout=timeout)
    else:
        answer = _format_document_tool_context(trusted_results)
    title_source_match = any(
        item.get("title_source_match") and float(item.get("score", 0.0)) >= config.RAG_SCORE_THRESHOLD
        for item in trusted_results
    )
    return ToolResult(
        tool="search_documents",
        status="success",
        data=answer,
        citations=citations,
        metadata={
            "title_source_match": title_source_match,
            "candidate_count": len(results),
            "trusted_count": len(trusted_results),
            "supplied_context_answer": bool(context and generate_answer),
        }
    )


def _answer_from_supplied_context(
    query: str,
    context: list[str],
    tier: str,
    timeout: Optional[float] = None,
) -> ToolResult:
    system_prompt = (
        "请只根据本轮提供的附件或上下文回答用户问题。不得编造上下文中没有的信息；"
        "如果无法回答，明确说明依据不足。"
        "\n\n本轮附件或上下文：\n" + "\n\n".join(context)
    )
    answer = str(
        _llm_chat(
            message=query,
            system_prompt=system_prompt,
            tier=tier,
            timeout=timeout,
        )
    ).strip()
    return ToolResult(
        tool="search_documents",
        status="success",
        data=answer,
        citations=[],
        metadata={
            "supplied_context_answer": True,
            "candidate_count": 0,
            "trusted_count": 0,
        },
    )


def _format_document_tool_context(results: list[dict]) -> str:
    """Format local document evidence for a later model call without generating an answer."""
    snippets = []
    for index, item in enumerate(results or [], start=1):
        content = str(item.get("content", "")).strip()
        if content:
            snippets.append("[%s] %s" % (index, content[:1200]))
    return "\n\n".join(snippets) or "未找到可靠依据，无法确认答案"


def _list_documents() -> ToolResult:
    """列出已审核通过的企业知识库文档，不返回chunk内容。"""
    documents = [
        DocumentListItem(
            source=str(item.get("source", "")),
            doc_id=str(item.get("doc_id", "")),
            trust_level=str(item.get("trust_level", "verified") or "verified")
        )
        for item in auth.list_verified_documents()
        if item.get("source")
    ]
    logger.info("企业文档清单读取完成：verified_count=%s", len(documents))
    if not documents:
        return ToolResult(
            tool="list_documents",
            status="success",
            data="当前企业信息库暂无已审核通过的文档。",
            citations=[]
        )

    unique_sources = []
    seen = set()
    for item in documents:
        if item.source in seen:
            continue
        seen.add(item.source)
        unique_sources.append(item.source)

    lines = ["当前企业信息库包含以下文件："]
    lines.extend("%s. %s" % (index, source) for index, source in enumerate(unique_sources, start=1))
    return ToolResult(
        tool="list_documents",
        status="success",
        data="\n".join(lines),
        citations=[]
    )


def _convert_document(
    attachment_id: str,
    target_format: Literal["pdf", "docx", "xlsx", "pptx"],
    session_id: str,
    owner_user_id: str,
) -> ConvertDocumentResult:
    """转换当前会话附件，并将新产物写入owner的统一文件库。"""
    target = str(target_format or "").lower()
    record = attachments.get_attachment(session_id, attachment_id)
    if record is None or not record.file_id:
        return ConvertDocumentResult(success=False, error_type="attachment_not_found")
    source = files_store.get_file(record.file_id)
    if source is None or source.source_type != "attachment":
        return ConvertDocumentResult(success=False, error_type="file_not_found")
    if source.owner_user_id != owner_user_id:
        return ConvertDocumentResult(success=False, error_type="forbidden")
    if source.session_id != session_id:
        return ConvertDocumentResult(success=False, error_type="session_mismatch")
    allowed_targets = {
        "pdf": {"docx", "xlsx", "pptx"},
        "doc": {"pdf", "docx"},
        "docx": {"pdf"},
        "xls": {"pdf"},
        "xlsx": {"pdf"},
        "ppt": {"pdf"},
        "pptx": {"pdf"},
    }.get(source.format, set())
    if target not in allowed_targets:
        return ConvertDocumentResult(
            success=False,
            error_type="unsupported_conversion",
        )
    source_path = files_store.get_file_path(source)
    if source_path is None:
        return ConvertDocumentResult(success=False, error_type="file_not_found")

    conversion = None
    converted_path = ""
    conversion_fn = (
        converter.convert_pdf_to_office
        if source.format == "pdf"
        else converter.convert_file
    )
    for attempt in range(2):
        conversion = conversion_fn(source_path, target)
        converted_path = conversion.output_path or ""
        if conversion.success and converted_path:
            break
        logger.warning(
            "附件转换失败：attachment_id=%s target_format=%s attempt=%s error_type=%s",
            attachment_id,
            target,
            attempt + 1,
            conversion.error_type or "conversion_failed",
        )
        if attempt == 0:
            time.sleep(RETRY_DELAY)
    if conversion is None or not conversion.success or not converted_path:
        return ConvertDocumentResult(
            success=False,
            error_type=(conversion.error_type if conversion else "conversion_failed")
            or "conversion_failed",
        )

    try:
        stem = os.path.splitext(source.original_filename)[0] or "converted_file"
        download_filename = "%s.%s" % (stem, target)
        file_id = files_store.save_file(
            owner_user_id,
            "converted",
            download_filename,
            converted_path,
            target,
        )
        logger.info(
            "附件转换完成：attachment_id=%s target_format=%s file_id=%s",
            attachment_id,
            target,
            file_id,
        )
        return ConvertDocumentResult(
            success=True,
            file_id=file_id,
            download_filename=download_filename,
        )
    except Exception as exc:
        logger.warning(
            "附件转换产物保存失败：attachment_id=%s target_format=%s error_type=%s",
            attachment_id,
            target,
            type(exc).__name__,
        )
        return ConvertDocumentResult(
            success=False,
            error_type=type(exc).__name__,
        )
    finally:
        converter.cleanup_conversion_output(converted_path)


def generate_file(
    content: str,
    session_id: str,
    filename_hint: Optional[str] = None,
    output_format: Literal["md", "txt", "pdf", "docx"] = "md",
    owner_user_id: str = "",
) -> GenerateFileResult:
    """将Agent正文写入当前session的可下载文件，不提供任意文件读取能力。"""
    text = content if isinstance(content, str) else str(content or "")
    if len(text) > 200000:
        return GenerateFileResult(
            success=False,
            char_count=len(text),
            error_type="content_too_large",
        )
    requested_format = str(output_format or "md").lower()
    if requested_format not in {"md", "txt", "pdf", "docx"}:
        return GenerateFileResult(
            success=False,
            error_type="invalid_output_format",
            requested_format=requested_format,
        )
    if not _is_safe_generated_session_id(session_id):
        return GenerateFileResult(
            success=False,
            error_type="invalid_session_id",
            requested_format=requested_format,
        )
    if not owner_user_id:
        return GenerateFileResult(
            success=False,
            error_type="invalid_owner_user_id",
            requested_format=requested_format,
        )

    clean_hint = _sanitize_filename_hint(filename_hint, requested_format)
    if not clean_hint:
        return GenerateFileResult(
            success=False,
            error_type="invalid_filename",
            requested_format=requested_format,
        )

    work_root = os.path.join(config.BASE_DIR, "data", "tmp_generated")
    os.makedirs(work_root, exist_ok=True)
    output_dir = tempfile.mkdtemp(prefix="generate_", dir=work_root)
    initial_format = requested_format if requested_format in {"md", "txt"} else "md"
    initial_filename = "%s.%s" % (clean_hint, initial_format)
    initial_path = os.path.join(output_dir, initial_filename)
    file_id = ""
    write_error = _write_generated_text(
        initial_path,
        text,
        session_id,
        requested_format,
    )
    if write_error:
        shutil.rmtree(output_dir, ignore_errors=True)
        return GenerateFileResult(
            success=False,
            file_id=file_id,
            download_filename=initial_filename,
            char_count=len(text),
            error_type=write_error,
            requested_format=requested_format,
        )

    if requested_format in {"md", "txt"}:
        try:
            file_id = files_store.save_file(
                owner_user_id,
                "generated",
                initial_filename,
                initial_path,
                requested_format,
            )
        except Exception as exc:
            shutil.rmtree(output_dir, ignore_errors=True)
            return GenerateFileResult(
                success=False,
                char_count=len(text),
                error_type=type(exc).__name__,
                requested_format=requested_format,
            )
        shutil.rmtree(output_dir, ignore_errors=True)
        _log_generated_file(
            session_id,
            file_id,
            requested_format,
            requested_format,
            len(text),
        )
        return GenerateFileResult(
            success=True,
            file_id=file_id,
            download_filename=initial_filename,
            char_count=len(text),
            requested_format=requested_format,
            delivered_format=requested_format,
        )

    conversion_error = ""
    converted_path = ""
    try:
        conversion = converter.convert_file(initial_path, requested_format)
        converted_path = conversion.output_path or ""
        if not conversion.success or not converted_path:
            conversion_error = conversion.error_type or "conversion_failed"
        else:
            final_filename = "%s.%s" % (clean_hint, requested_format)
            file_id = files_store.save_file(
                owner_user_id,
                "generated",
                final_filename,
                converted_path,
                requested_format,
            )
            _log_generated_file(
                session_id,
                file_id,
                requested_format,
                requested_format,
                len(text),
            )
            shutil.rmtree(output_dir, ignore_errors=True)
            return GenerateFileResult(
                success=True,
                file_id=file_id,
                download_filename=final_filename,
                char_count=len(text),
                requested_format=requested_format,
                delivered_format=requested_format,
            )
    except Exception as exc:
        conversion_error = type(exc).__name__
    finally:
        if converted_path:
            converter.cleanup_conversion_output(converted_path)

    try:
        file_id = files_store.save_file(
            owner_user_id,
            "generated",
            initial_filename,
            initial_path,
            "md",
        )
    except Exception as exc:
        shutil.rmtree(output_dir, ignore_errors=True)
        return GenerateFileResult(
            success=False,
            char_count=len(text),
            error_type=type(exc).__name__,
            requested_format=requested_format,
        )
    shutil.rmtree(output_dir, ignore_errors=True)
    logger.warning(
        "生成文件格式转换降级：session_id_len=%s file_id=%s requested_format=%s delivered_format=md error_type=%s",
        len(session_id),
        file_id,
        requested_format,
        conversion_error,
    )
    return GenerateFileResult(
        success=True,
        file_id=file_id,
        download_filename=initial_filename,
        char_count=len(text),
        requested_format=requested_format,
        delivered_format="md",
        conversion_error_type=conversion_error or "conversion_failed",
    )


def _write_generated_text(
    output_path: str,
    text: str,
    session_id: str,
    requested_format: str,
) -> str:
    temp_path = output_path + ".tmp"
    for attempt in range(2):
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(temp_path, "w", encoding="utf-8", newline="") as output:
                output.write(text)
            os.replace(temp_path, output_path)
            return ""
        except Exception as exc:
            _remove_generated_temp_file(temp_path)
            logger.warning(
                "生成文件失败：session_id_len=%s requested_format=%s attempt=%s error_type=%s",
                len(session_id),
                requested_format,
                attempt + 1,
                type(exc).__name__,
            )
            if attempt == 0:
                time.sleep(RETRY_DELAY)
                continue
            return type(exc).__name__
    return "file_write_failed"


def _log_generated_file(
    session_id: str,
    file_id: str,
    requested_format: str,
    delivered_format: str,
    char_count: int,
) -> None:
    logger.info(
        "生成文件完成：session_id_len=%s file_id=%s requested_format=%s delivered_format=%s char_count=%s",
        len(session_id),
        file_id,
        requested_format,
        delivered_format,
        char_count,
    )


def _sanitize_filename_hint(filename_hint: Optional[str], output_format: str) -> str:
    hint = (filename_hint or "generated_file").strip()
    if not hint:
        hint = "generated_file"
    if len(hint) > 100 or "/" in hint or "\\" in hint or ".." in hint or "\x00" in hint:
        return ""
    suffix = ".%s" % output_format
    if hint.lower().endswith(suffix):
        hint = hint[:-len(suffix)]
    hint = re.sub(r'[<>:"|?*\x00-\x1f]', "_", hint)
    hint = hint.strip(" .")
    return hint or "generated_file"


def _is_safe_generated_session_id(session_id: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(session_id or "")))


def _remove_generated_temp_file(path: str) -> None:
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError as exc:
        logger.warning("生成文件临时产物清理失败：error_type=%s", type(exc).__name__)


def _answer_from_documents(
    query: str,
    results: list[dict],
    tier: str = "fast",
    timeout: Optional[float] = None,
) -> str:
    """基于可信文档chunk生成回答，来源信息只通过citations返回。"""
    snippets = []
    for index, item in enumerate(results, start=1):
        content = str(item.get("content", "")).strip()
        if content:
            snippets.append(f"[{index}]\n{content}")
    if not snippets:
        return "未找到可靠依据，无法确认答案"

    fixed_prompt = (
        "你是企业知识库问答助手。请只根据给定文档片段回答用户问题。"
        "不要编造文档片段之外的信息，不要在正文里写来源、doc_id、chunk_index或score。"
        "如果片段不足以回答，直接回答：未找到可靠依据，无法确认答案。"
    )
    dynamic_prompt = (
        f"用户问题：{query}\n\n"
        "文档片段：\n"
        + "\n\n".join(snippets)
    )
    try:
        if tier == "expert":
            response = llm_provider.chat_completion(
                cache_friendly_messages(
                    fixed_prompt,
                    [{"role": "user", "content": dynamic_prompt}],
                    include_date=True,
                ),
                tier=tier,
                timeout=timeout,
            )
            return llm_provider.extract_text(response).strip()
        prompt = current_date_prompt() + "\n\n" + fixed_prompt + "\n\n" + dynamic_prompt
        return str(_llm_chat(message=prompt, tier=tier, timeout=timeout)).strip()
    except Exception as e:
        logger.warning("文档回答生成失败，返回保守摘要：query_len=%s error_type=%s", len(query or ""), type(e).__name__)
        contents = [str(item.get("content", "")).strip() for item in results if item.get("content")]
        summary = "\n\n".join(contents)
        return summary[:800] if summary else "未找到可靠依据，无法确认答案"


def _search_tavily_with_retry(
    client: TavilyClient,
    query: str,
    deadline: Optional[float] = None
) -> dict:
    """按Level1规则调用Tavily，失败重试1次"""
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            timeout = min(TIMEOUT, _remaining_budget(deadline)) if deadline else TIMEOUT
            if timeout <= 0:
                raise TimeoutError("搜索链路已达到时间预算")
            return _run_with_timeout(
                client.search,
                timeout=timeout,
                query=query,
                search_depth="basic",
                max_results=5
            )
        except Exception as e:
            last_error = e
            logger.warning("Tavily调用失败：attempt=%s error_type=%s", attempt + 1, type(e).__name__)
            if attempt < MAX_RETRIES:
                if deadline and _remaining_budget(deadline) <= RETRY_DELAY:
                    break
                time.sleep(RETRY_DELAY)
    raise last_error


def _log_tavily_success(results: list[dict]) -> None:
    scores = [
        float(item.get("score", 0.0))
        for item in results or []
        if isinstance(item, dict) and item.get("score") is not None
    ]
    max_score = max(scores) if scores else 0.0
    logger.info(
        "Tavily搜索成功：result_count=%s max_score=%.4f",
        len(results or []),
        max_score
    )


def _format_raw_search_results(results: list[dict], prefix: str) -> str:
    lines = [prefix]
    for index, item in enumerate((results or [])[:5], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        content = str(item.get("content", "")).strip()
        if not title and not content:
            continue
        line = "%s. %s" % (index, title or content[:60])
        if url:
            line += "\n   %s" % url
        if content:
            line += "\n   %s" % content[:160]
        lines.append(line)
    if len(lines) == 1:
        lines.append("搜索结果中没有足够可展示的信息。")
    return "\n".join(lines)


def _fallback_llm_answer(
    message: str,
    session_id: str = None,
    context: list[str] = None,
    prefix: str = "",
    timeout: Optional[float] = None,
    tier: str = "fast"
) -> str:
    """搜索质量不足或不可用时降级为模型知识回答"""
    if timeout is not None and timeout <= 0:
        return prefix or "搜索链路已达到时间预算，请稍后重试"
    system_prompt = _build_context_system_prompt(context)
    answer = _llm_chat(
        message=message,
        session_id=session_id or "",
        system_prompt=system_prompt,
        tier=tier,
        timeout=timeout
    )
    return f"{prefix}\n{answer}" if prefix else str(answer)


def _build_context_system_prompt(context: list[str] = None) -> str:
    """将检索上下文转为LLM系统提示"""
    date_prompt = current_date_prompt()
    if not context:
        return date_prompt
    context_text = "\n".join(context)
    return (
        f"{date_prompt}\n\n"
        f"以下是与当前问题相关的历史记录，供参考：\n{context_text}\n\n"
        "如果历史记录与当前问题不相关，请忽略，不要主动引入无关信息。"
    )


def _has_low_search_relevance(results: list[dict]) -> bool:
    """Tavily score越高越相关，所有可用score低于0.3时视为相关性不足"""
    scores = [
        float(item["score"])
        for item in results
        if isinstance(item, dict) and item.get("score") is not None
    ]
    return bool(scores) and all(score < 0.3 for score in scores)


def _llm_chat(
    message: str,
    session_id: str = "",
    stream: bool = False,
    search_results: str = "",
    original_question: str = "",
    system_prompt: str = "",
    tier: str = "fast",
    timeout: Optional[float] = None
) -> Union[str, Iterator[str]]:
    """通过统一适配层调用指定tier，每次只发送一次模型请求。"""

    if search_results:
        messages = _build_search_answer_messages(
            original_question or message,
            search_results,
            tier=tier,
        )
    else:
        messages = _build_model_messages(session_id, message, system_prompt)

    if stream:
        response = llm_provider.chat_completion(
            messages,
            tier=tier,
            timeout=timeout,
            stream=True
        )
        return llm_provider.iter_text(response)

    response = llm_provider.chat_completion(messages, tier=tier, timeout=timeout)
    return llm_provider.extract_text(response)


def _build_model_messages(session_id: str, message: str, system_prompt: str = "") -> list[dict]:
    """读取会话历史并追加本轮用户消息"""
    history = memory.get_history(session_id, limit=10) if session_id else []
    system_parts = [current_date_prompt()]
    if system_prompt:
        system_parts.append(system_prompt)
    messages = [{"role": "system", "content": "\n\n".join(system_parts)}]
    messages.extend([
        {"role": item["role"], "content": item["content"]}
        for item in history
        if item.get("role") in {"user", "assistant"} and item.get("content")
    ])
    messages.append({"role": "user", "content": message})
    return messages


def _build_search_answer_messages(
    original_question: str,
    search_results: str,
    tier: str = "fast",
) -> list[dict]:
    """将搜索结果和原始问题拼成模型自然语言回答上下文。"""
    fixed_prompt = (
        "你是搜索结果整理助手。请只基于搜索结果回答用户原始问题，优先使用与问题最相关的信息，输出自然语言，不要返回JSON。"
        "不得编造搜索结果中没有出现的事件、发布时间、模型名称、公司动态或数据。"
        "如果搜索结果不足以支持明确结论，请直接说明“搜索结果中没有足够可靠的信息确认”。"
    )
    dynamic_messages = [
        {
            "role": "user",
            "content": f"原始用户问题：{original_question}\n\n搜索结果：{search_results}"
        }
    ]
    if tier == "expert":
        return cache_friendly_messages(fixed_prompt, dynamic_messages, include_date=True)
    return [{"role": "system", "content": current_date_prompt() + "\n\n" + fixed_prompt}] + dynamic_messages


def _rewrite_search_query(
    message: str,
    context: list[str] = None,
    timeout: Optional[float] = None,
    tier: str = "fast"
) -> str:
    """调用所选模型将用户原话改写成更适合搜索引擎的query。"""
    context_text = "\n".join(context or [])
    fixed_prompt = (
        "你是搜索引擎query优化专家。"
        "将用户的问题改写为适合搜索引擎检索的精准关键词。"
        "规则："
        "- 只返回关键词，不要解释，不要标点"
        "- 控制在15字以内"
        "- 问天气类问题时，加上城市+天气+日期；未提供城市时不要编造城市"
        "- 问是否适合出门时，按天气类问题处理"
        "- 问比较类问题时，保留对比关键词"
        "- 问时事新闻时，加上最新/今日等时间词"
        "- 如果上下文提供了用户城市，天气/出行类问题必须带上该城市"
    )
    dynamic_prompt = f"上下文：{context_text or '无'}用户问题：{message}"
    if tier == "expert":
        messages = cache_friendly_messages(
            fixed_prompt,
            [{"role": "user", "content": dynamic_prompt}],
            include_date=True,
        )
    else:
        messages = [{"role": "user", "content": current_date_prompt() + "\n" + fixed_prompt + dynamic_prompt}]

    if timeout is not None and timeout <= 0:
        return message
    try:
        response = llm_provider.chat_completion(
            messages,
            tier=tier,
            timeout=timeout or config.SEARCH_QUERY_REWRITE_TIMEOUT
        )
        rewritten = llm_provider.extract_text(response)
    except Exception as exc:
        logger.warning("搜索query改写失败，使用原始query：error_type=%s", type(exc).__name__)
        return message

    rewritten = _clean_search_query(rewritten)
    return rewritten or message


def _clean_search_query(query: str) -> str:
    """清理模型改写结果，避免解释性文字或标点进入搜索。"""
    first_line = str(query).strip().splitlines()[0].strip()
    for char in "，。！？；：,.!?;:\"'`“”‘’（）()[]【】{}":
        first_line = first_line.replace(char, " ")
    return " ".join(first_line.split())[:15]


def _extract_model_delta(chunk) -> str:
    """从流式chunk中提取增量文本。"""
    choices = getattr(chunk, "choices", None)
    if not choices:
        return ""

    delta = getattr(choices[0], "delta", None)
    content = getattr(delta, "content", None)
    if content is None and isinstance(delta, dict):
        content = delta.get("content")
    return str(content or "")


def _run_with_timeout(func, timeout: Optional[float] = None, **kwargs):
    """为不暴露timeout参数的SDK调用补充动态超时控制。"""
    wait_timeout = float(timeout or TIMEOUT)
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(func, **kwargs)
    try:
        return future.result(timeout=wait_timeout)
    except TimeoutError as e:
        future.cancel()
        raise TimeoutError(f"工具调用超时：{wait_timeout}秒") from e
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _has_valid_key(value: str, provider: str) -> bool:
    """过滤空Key和初始化占位Key"""
    if not value:
        return False
    placeholders = [f"你的{provider}_API_KEY", "your_api_key", "YOUR_API_KEY"]
    return value not in placeholders


def _remaining_budget(deadline: Optional[float]) -> float:
    if deadline is None:
        return config.SEARCH_TOTAL_TIMEOUT
    return max(0.0, deadline - time.perf_counter())
