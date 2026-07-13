# -*- coding: utf-8 -*-
# 执行层：工具调用统一入口

import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from collections.abc import Iterator
from typing import Optional, Union

from pydantic import BaseModel, Field
from tavily import TavilyClient
import config
from layers import auth, llm_provider, memory
from utils.logger import get_logger
from utils import observability
from utils.time_context import current_date_prompt

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
}


def run(tool: str, params: dict) -> ToolResult:
    """统一工具调用入口"""
    if tool not in TOOL_REGISTRY:
        return ToolResult(tool=tool, status="error", data="", error_msg=f"未知工具：{tool}")

    func = globals()[TOOL_REGISTRY[tool]]
    last_error = ""
    max_attempts = 1 if tool in {"search_web", "llm_chat"} else MAX_RETRIES + 1
    for attempt in range(max_attempts):
        try:
            result = func(**params)
            if isinstance(result, ToolResult):
                return result
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
    tier: str = "fast"
) -> str:
    """联网搜索：先优化搜索query，再调用Tavily并整理成自然语言回复"""
    if not _has_valid_key(config.TAVILY_API_KEY, "TAVILY"):
        raise ValueError("TAVILY_API_KEY未配置")

    started_at = time.perf_counter()
    deadline = started_at + config.SEARCH_TOTAL_TIMEOUT
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
    """流式联网搜索：Tavily完成后用GLM stream逐chunk整理搜索结果。"""
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
    rerank_enabled: bool = True
) -> str:
    """检索已上传的本地文档并整理为自然语言。"""
    verified_doc_ids = auth.get_verified_doc_ids()
    results = memory.search_documents(
        query,
        top_k=5,
        verified_doc_ids=verified_doc_ids,
        tier=tier,
        enable_rerank=rerank_enabled
    )
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
    if generate_answer:
        answer = _answer_from_documents(query, trusted_results, tier=tier)
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
            "trusted_count": len(trusted_results)
        }
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


def _answer_from_documents(
    query: str,
    results: list[dict],
    tier: str = "fast"
) -> str:
    """基于可信文档chunk生成回答，来源信息只通过citations返回。"""
    snippets = []
    for index, item in enumerate(results, start=1):
        content = str(item.get("content", "")).strip()
        if content:
            snippets.append(f"[{index}]\n{content}")
    if not snippets:
        return "未找到可靠依据，无法确认答案"

    prompt = (
        current_date_prompt()
        + "\n\n"
        "你是企业知识库问答助手。请只根据给定文档片段回答用户问题。"
        "不要编造文档片段之外的信息，不要在正文里写来源、doc_id、chunk_index或score。"
        "如果片段不足以回答，直接回答：未找到可靠依据，无法确认答案。\n\n"
        f"用户问题：{query}\n\n"
        "文档片段：\n"
        + "\n\n".join(snippets)
    )
    try:
        return str(_llm_chat(message=prompt, tier=tier)).strip()
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
            search_results
        )
    else:
        messages = _build_glm_messages(session_id, message, system_prompt)

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


def _build_glm_messages(session_id: str, message: str, system_prompt: str = "") -> list[dict]:
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


def _build_search_answer_messages(original_question: str, search_results: str) -> list[dict]:
    """将搜索结果和原始问题拼成GLM自然语言回答上下文"""
    return [
        {
            "role": "system",
            "content": (
                current_date_prompt()
                + "\n\n你是搜索结果整理助手。请只基于搜索结果回答用户原始问题，优先使用与问题最相关的信息，输出自然语言，不要返回JSON。"
                "不得编造搜索结果中没有出现的事件、发布时间、模型名称、公司动态或数据。"
                "如果搜索结果不足以支持明确结论，请直接说明“搜索结果中没有足够可靠的信息确认”。"
            )
        },
        {
            "role": "user",
            "content": f"原始用户问题：{original_question}\n\n搜索结果：{search_results}"
        }
    ]


def _rewrite_search_query(
    message: str,
    context: list[str] = None,
    timeout: Optional[float] = None,
    tier: str = "fast"
) -> str:
    """调用GLM将用户原话改写成更适合搜索引擎的query"""
    context_text = "\n".join(context or [])
    prompt = (
        current_date_prompt()
        + "\n"
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
        f"上下文：{context_text or '无'}"
        f"用户问题：{message}"
    )
    messages = [{"role": "user", "content": prompt}]

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
    """清理GLM改写结果，避免解释性文字或标点进入搜索"""
    first_line = str(query).strip().splitlines()[0].strip()
    for char in "，。！？；：,.!?;:\"'`“”‘’（）()[]【】{}":
        first_line = first_line.replace(char, " ")
    return " ".join(first_line.split())[:15]


def _extract_glm_text(response) -> str:
    """从GLM响应中提取文本内容"""
    choices = getattr(response, "choices", None)
    if not choices:
        return str(response)

    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return str(content or response)


def _extract_glm_delta(chunk) -> str:
    """从GLM流式chunk中提取增量文本"""
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
