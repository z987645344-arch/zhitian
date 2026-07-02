# -*- coding: utf-8 -*-
# 执行层：工具调用统一入口

import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from collections.abc import Iterator

from pydantic import BaseModel
from tavily import TavilyClient
from zhipuai import ZhipuAI
import config
from layers import auth, memory
from utils.logger import get_logger

logger = get_logger("execution")


class ToolResult(BaseModel):
    tool: str
    status: str
    data: str
    error_msg: str = ""

# 执行层错误处理规则
MAX_RETRIES = 1
RETRY_DELAY = 1.0
TIMEOUT = 10.0

# 工具注册表：新增工具在此注册
TOOL_REGISTRY = {
    "search_web": "_search_web",
    "llm_chat": "_llm_chat",
    "search_documents": "_search_documents",
}


def run(tool: str, params: dict) -> ToolResult:
    """统一工具调用入口"""
    if tool not in TOOL_REGISTRY:
        return ToolResult(tool=tool, status="error", data="", error_msg=f"未知工具：{tool}")

    func = globals()[TOOL_REGISTRY[tool]]
    last_error = ""
    for attempt in range(MAX_RETRIES + 1):
        try:
            result = func(**params)
            return ToolResult(tool=tool, status="success", data=result, error_msg="")
        except Exception as e:
            last_error = str(e)
            logger.warning("工具调用失败：tool=%s attempt=%s error_type=%s", tool, attempt + 1, type(e).__name__)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    return ToolResult(tool=tool, status="error", data="", error_msg=last_error)


def _search_web(query: str, context: list[str] = None, session_id: str = None) -> str:
    """联网搜索：先优化搜索query，再调用Tavily并整理成自然语言回复"""
    if not _has_valid_key(config.TAVILY_API_KEY, "TAVILY"):
        raise ValueError("TAVILY_API_KEY未配置")

    original_question = query
    optimized_query = _rewrite_search_query(original_question, context)
    client = TavilyClient(api_key=config.TAVILY_API_KEY)
    try:
        result = _search_tavily_with_retry(client, optimized_query)
    except Exception as e:
        logger.warning("Tavily调用失败，降级为模型知识回答：error_type=%s", type(e).__name__)
        return _fallback_llm_answer(
            original_question,
            session_id=session_id,
            context=context,
            prefix="（搜索服务暂时不可用，以下为模型知识回答）"
        )

    results = result.get("results", []) if isinstance(result, dict) else []
    if not results:
        logger.warning("Tavily返回空结果，降级为模型知识回答：query_len=%s", len(optimized_query or ""))
        return _fallback_llm_answer(
            original_question,
            session_id=session_id,
            context=context,
            prefix="（网络搜索无结果，以下为模型知识回答）"
        )

    if _has_low_search_relevance(results):
        logger.warning("Tavily搜索结果相关性不足，降级为模型知识回答：query_len=%s", len(optimized_query or ""))
        return _fallback_llm_answer(
            original_question,
            session_id=session_id,
            context=context,
            prefix="（搜索结果相关性不足，以下为模型知识回答）"
        )

    search_results = json.dumps(result, ensure_ascii=False)
    try:
        return _llm_chat(
            message=original_question,
            search_results=search_results,
            original_question=original_question
        )
    except Exception as e:
        logger.warning(
            "搜索结果整理失败：query_len=%s error_type=%s",
            len(optimized_query or ""),
            type(e).__name__
        )
        raise RuntimeError("搜索结果整理失败") from e


def _search_documents(query: str) -> str:
    """检索已上传的本地文档并整理为自然语言。"""
    verified_doc_ids = auth.get_verified_doc_ids()
    results = memory.search_documents(query, top_k=5, verified_doc_ids=verified_doc_ids)
    if not results:
        return "未在已上传文档中找到相关内容"

    lines = ["根据已上传文档，找到以下相关内容："]
    for index, item in enumerate(results, start=1):
        source = item.get("source", "")
        filename = source.replace("\\", "/").split("/")[-1] or source or "未知来源"
        chunk_index = item.get("chunk_index", 0)
        content = str(item.get("content", "")).strip()
        if len(content) > 280:
            content = f"{content[:280]}..."
        lines.append(f"{index}. 来源：{filename}（片段{chunk_index}）\n{content}")
    return "\n\n".join(lines)


def _search_tavily_with_retry(client: TavilyClient, query: str) -> dict:
    """按Level1规则调用Tavily，失败重试1次"""
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return _run_with_timeout(
                client.search,
                query=query,
                search_depth="basic",
                max_results=5
            )
        except Exception as e:
            last_error = e
            logger.warning("Tavily调用失败：attempt=%s error_type=%s", attempt + 1, type(e).__name__)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
    raise last_error


def _fallback_llm_answer(
    message: str,
    session_id: str = None,
    context: list[str] = None,
    prefix: str = ""
) -> str:
    """搜索质量不足或不可用时降级为模型知识回答"""
    system_prompt = _build_context_system_prompt(context)
    answer = _llm_chat(
        message=message,
        session_id=session_id or "",
        system_prompt=system_prompt
    )
    return f"{prefix}\n{answer}" if prefix else str(answer)


def _build_context_system_prompt(context: list[str] = None) -> str:
    """将检索上下文转为LLM系统提示"""
    if not context:
        return ""
    context_text = "\n".join(context)
    return (
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
    system_prompt: str = ""
) -> str | Iterator[str]:
    """LLM对话：优先主模型，失败后自动降级到fallback模型"""
    if not _has_valid_key(config.GLM_API_KEY, "GLM"):
        raise ValueError("GLM_API_KEY未配置")

    if search_results:
        messages = _build_search_answer_messages(
            original_question or message,
            search_results
        )
    else:
        messages = _build_glm_messages(session_id, message, system_prompt)

    if stream:
        return _stream_chat_with_fallback(messages)

    primary_error = ""
    try:
        return _chat_with_model(config.LLM_MODEL, messages)
    except Exception as e:
        primary_error = str(e)
        logger.warning("GLM主模型调用失败，准备fallback：error_type=%s", type(e).__name__)

    try:
        return _chat_with_model(config.FALLBACK_MODEL, messages)
    except Exception as e:
        logger.warning("GLM fallback调用失败：error_type=%s", type(e).__name__)
        raise RuntimeError(f"主模型失败：{primary_error}；fallback失败：{e}") from e


def _stream_chat_with_fallback(messages: list[dict]) -> Iterator[str]:
    """流式调用GLM，主模型失败且未输出内容时降级fallback"""
    emitted = False
    primary_error = ""
    try:
        for chunk in _stream_chat_with_model(config.LLM_MODEL, messages):
            emitted = True
            yield chunk
        return
    except Exception as e:
        primary_error = str(e)
        logger.warning("GLM流式主模型调用失败，准备fallback：error_type=%s", type(e).__name__)
        if emitted:
            raise

    try:
        yield from _stream_chat_with_model(config.FALLBACK_MODEL, messages)
    except Exception as e:
        logger.warning("GLM流式fallback调用失败：error_type=%s", type(e).__name__)
        raise RuntimeError(f"主模型失败：{primary_error}；fallback失败：{e}") from e


def _stream_chat_with_model(model: str, messages: list[dict]) -> Iterator[str]:
    """调用指定GLM模型并逐chunk返回文本片段"""
    client = ZhipuAI(
        api_key=config.GLM_API_KEY,
        timeout=TIMEOUT,
        max_retries=0
    )
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        timeout=TIMEOUT
    )
    for chunk in response:
        text = _extract_glm_delta(chunk)
        if text:
            yield text


def _chat_with_model(model: str, messages: list[dict]) -> str:
    """调用指定GLM模型，按Level1规则重试1次"""
    client = ZhipuAI(
        api_key=config.GLM_API_KEY,
        timeout=TIMEOUT,
        max_retries=0
    )
    last_error = ""
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                timeout=TIMEOUT
            )
            return _extract_glm_text(response)
        except Exception as e:
            last_error = str(e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    raise RuntimeError(last_error)


def _build_glm_messages(session_id: str, message: str, system_prompt: str = "") -> list[dict]:
    """读取会话历史并追加本轮用户消息"""
    history = memory.get_history(session_id, limit=10) if session_id else []
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
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
            "content": "你是搜索结果整理助手。请基于搜索结果回答用户原始问题，优先使用与问题最相关的信息，输出自然语言，不要返回JSON。"
        },
        {
            "role": "user",
            "content": f"原始用户问题：{original_question}\n\n搜索结果：{search_results}"
        }
    ]


def _rewrite_search_query(message: str, context: list[str] = None) -> str:
    """调用GLM将用户原话改写成更适合搜索引擎的query"""
    context_text = "\n".join(context or [])
    prompt = (
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

    try:
        rewritten = _chat_with_model(config.LLM_MODEL, messages)
    except Exception:
        try:
            rewritten = _chat_with_model(config.FALLBACK_MODEL, messages)
        except Exception:
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


def _run_with_timeout(func, **kwargs):
    """为不暴露timeout参数的SDK调用补充10秒超时控制"""
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(func, **kwargs)
    try:
        return future.result(timeout=TIMEOUT)
    except TimeoutError as e:
        future.cancel()
        raise TimeoutError(f"工具调用超时：{TIMEOUT}秒") from e
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _has_valid_key(value: str, provider: str) -> bool:
    """过滤空Key和初始化占位Key"""
    if not value:
        return False
    placeholders = [f"你的{provider}_API_KEY", "your_api_key", "YOUR_API_KEY"]
    return value not in placeholders
