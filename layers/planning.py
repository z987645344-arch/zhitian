# -*- coding: utf-8 -*-
# 规划层：LangGraph状态机调度意图分类、记忆检索、执行和响应生成

import json
import time
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel
import config
from layers import execution, llm_provider, memory
from layers.execution import Citation, ToolResult
from layers.mcp_client import mcp_client
from utils.logger import get_logger
from utils import observability
from utils.time_context import current_date_prompt

logger = get_logger("planning")


class Task(BaseModel):
    tool: str
    params: dict
    order: int


class AgentState(TypedDict):
    session_id: str
    message: str
    mode: str
    intent: str
    context: list[str]
    tasks: list[Task]
    results: list[ToolResult]
    citations: list[Citation]
    round_count: int
    tool_call_history: list[dict]
    react_action: str
    react_limit_reached: bool
    response: str
    error: str
    clarification: str
    city: str


INTENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "当用户问题完整清晰，且需要实时信息、联网搜索、天气、新闻、价格、最新状态或外部事实核验时调用。"
                "query_hint是搜索方向提示，不必重写完整query。"
                "如果问题是天气/出行且消息中有城市或上下文已有用户城市，直接调用本工具。"
                "如果用户同时明确提供了自己的城市，优先在city参数中带出城市。"
                "如果模型能力支持多个工具，可额外调用save_city；如果一次只能调用一个工具，必须优先调用search_web，不要只调用save_city。"
                "示例：“北京今天天气”“我在北京，今天天气怎么样”都应调用search_web。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query_hint": {
                        "type": "string",
                        "description": "搜索方向提示，例如天气、新闻、价格、对比评测等"
                    },
                    "city": {
                        "type": "string",
                        "description": "用户明确提供的当前城市或所在地；没有则不填"
                    }
                },
                "required": ["query_hint"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "查询用户已上传的本地文档/资料内容，与search_web（查询互联网实时信息）严格区分。"
                "用于回答文档内容、术语、产品说明、编号、段落信息、内部定义或企业知识库中的专有名词解释。"
                "用户提到“文档”“资料”“上传的文件”“刚才的PDF”“这份文件”“这份文档”等明确指代本地文档时使用。"
                "用户问“这份文档说了什么”“文档主要内容是什么”“ERR-8842是什么意思”“知了是什么”“蓝鲸项目有哪些能力”这类内容问题，必须调用本工具。"
                "当用户提出简短定义型问题（例如“XX是什么”“XX介绍一下”），且XX可能是企业内部术语、产品名、项目名、编号或知识库标题时，应优先调用本工具检索验证，不要直接用通用常识回答。"
                "如果用户问“有哪些文件/文档/资料”“上传了哪些文档”“企业信息库有哪些文件”这类清单问题，不要调用本工具，应调用list_documents。"
                "不要用于天气、新闻、价格、互联网实时信息、通用知识问答。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query_hint": {
                        "type": "string",
                        "description": "本地文档检索方向，例如主要内容、某个术语、某段资料"
                    }
                },
                "required": ["query_hint"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": (
                "列出当前企业信息库/知识库中已审核通过的文件、文档或资料清单。"
                "当用户问“企业信息库有哪些文件”“目前上传了哪些文档”“知识库里有哪些资料”“已上传的企业信息库文档有哪些”“刚才上传的文档里有什么文件”时调用。"
                "本工具只返回文件名/来源列表，不检索文档内容，不回答文档片段问题。"
                "如果用户要看某份文档的内容、摘要、说明、编号含义或具体资料，调用search_documents。"
            ),
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "direct_answer",
            "description": (
                "当用户问题完整清晰，可以直接根据已有上下文或通用知识回答，不需要联网搜索时调用。"
                "天气、下雨、出行、附近推荐这类依赖城市或位置的问题，不要调用本工具。"
                "如果用户同时明确提供了自己的城市，优先在city参数中带出城市；如果模型能力支持多个工具，可额外调用save_city。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "用户明确提供的当前城市或所在地；没有则不填"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ask_clarification",
            "description": (
                "当用户问题缺少关键信息无法准确回答时调用，返回一个澄清问题。"
                "判断规则：问天气/出行但没有城市且上下文没有用户城市时，询问用户所在城市；"
                "例如“今天天气怎么样”“明天会下雨吗”“今天适合出门吗”这类问题没有城市时，必须询问城市；"
                "问“附近”但没有位置且上下文没有位置时，说明无法获取位置并询问用户提供位置；"
                "问题完整清晰时不要调用本工具，正常选择search_web或direct_answer；"
                "能从消息推断出城市（如“北京今天天气”）或上下文已有用户城市时，直接search_web不问。"
                "如果用户同时明确提供了自己的城市，优先在city参数中带出城市；如果模型能力支持多个工具，可额外调用save_city。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "需要向用户询问的具体问题"
                    },
                    "city": {
                        "type": "string",
                        "description": "用户明确提供的当前城市或所在地；没有则不填"
                    }
                },
                "required": ["question"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_city",
            "description": (
                "辅助工具，不能单独调用。每次必须同时调用一个主意图工具：search_web、direct_answer或ask_clarification。"
                "当且仅当用户明确提供自己的当前城市或所在地时调用，可与search_web/direct_answer/ask_clarification同时调用。"
                "如果一次只能调用一个工具，不要调用本工具，请把城市写入主意图工具的city参数。"
                "例如“我在北京”“我住在上海”“我的城市是广州”“北京，帮我查天气”可调用。"
                "例如“我在北京，今天天气怎么样”必须同时调用search_web和save_city。"
                "如果用户只是评价、喜欢/不喜欢、询问或提到某城市，例如“我不喜欢北京的天气”，不要调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "用户明确提供的当前城市或所在地"
                    }
                },
                "required": ["city"]
            }
        }
    }
]


FAST_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "仅检索本地已审核企业知识库内容。用户询问内部文档内容、产品、项目、编号、"
                "术语定义或可能属于企业资料的事实时调用。不要用于互联网新闻、天气、价格或最新消息。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "用于本地企业知识库检索的完整查询"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": (
                "列出本地企业知识库中已审核通过的文件清单。用户询问有哪些文件、文档、资料或已上传内容时调用。"
                "只用于清单，不用于回答文档正文。"
            ),
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    }
]


def classify_node(state: AgentState) -> AgentState:
    """classify节点：调用GLM Function Call判断意图"""
    started_at = time.perf_counter()
    state["context"] = _load_classify_context(state["session_id"], state["message"])
    observability.log_stage("classify_context", int((time.perf_counter() - started_at) * 1000))
    started_at = time.perf_counter()
    decision = _classify_with_glm(state["message"], state["context"], tier=state["mode"])
    observability.log_stage("classify_glm", int((time.perf_counter() - started_at) * 1000))
    state["intent"] = decision["intent"]
    state["clarification"] = decision.get("clarification", "")
    city = decision.get("city", "")
    state["city"] = city
    if city:
        _save_city_memory(state["session_id"], city)
    logger.info("意图分类结果：session_id=%s intent=%s", state["session_id"], state["intent"])
    return state


def retrieve_node(state: AgentState) -> AgentState:
    """retrieve节点：从Chroma检索语义相关的长期记忆"""
    started_at = time.perf_counter()
    try:
        retrieved_context = memory.search_memory(
            state["message"],
            session_id=state["session_id"],
            top_k=3,
            strict_session=True
        )
        state["context"] = _merge_context(state["context"], retrieved_context)
    except Exception:
        state["context"] = state["context"] or []
    observability.log_stage("retrieve_chroma", int((time.perf_counter() - started_at) * 1000))
    return state


def plan_node(state: AgentState) -> AgentState:
    """plan node: ensure there is one pending task for the current round."""
    if len(state["tasks"]) > state["round_count"]:
        return state
    task = _task_from_intent(state, order=len(state["tasks"]) + 1)
    state["tasks"].append(task)
    return state


def execute_node(state: AgentState) -> AgentState:
    """execute node: run the next unexecuted task."""
    if state["round_count"] >= len(state["tasks"]):
        state["error"] = "没有可执行的任务"
        return state

    task = state["tasks"][state["round_count"]]
    started_at = time.perf_counter()
    result = mcp_client.call_tool(task.tool, task.params)
    observability.log_stage(
        "execute_%s" % task.tool,
        int((time.perf_counter() - started_at) * 1000)
    )
    state["results"].append(result)
    state["round_count"] += 1
    state["tool_call_history"].append(_tool_history_item(task))
    state["citations"] = _dedupe_citations(state["citations"] + (result.citations or []))
    if result.status == "error":
        state["error"] = result.error_msg
    return state


def reflect_node(state: AgentState) -> AgentState:
    """reflect node: decide whether another bounded tool round is needed."""
    started_at = time.perf_counter()
    decision = should_continue_react(state)
    observability.log_stage("reflect_glm", int((time.perf_counter() - started_at) * 1000))
    state["react_action"] = decision["action"]
    state["react_limit_reached"] = bool(decision.get("limit_reached", False))
    next_task = decision.get("task")
    if state["react_action"] == "continue" and next_task:
        state["tasks"].append(next_task)
    return state

def respond_node(state: AgentState) -> AgentState:
    """respond节点：读取执行结果并生成最终响应"""
    started_at = time.perf_counter()
    if state["intent"] == "clarify":
        state["response"] = state["clarification"]
        state["citations"] = []
        observability.log_stage("respond_total", int((time.perf_counter() - started_at) * 1000))
        return state

    failed_results = [result for result in state["results"] if result.status == "error"]
    if failed_results:
        state["error"] = failed_results[0].error_msg or "工具调用失败"
        state["response"] = "抱歉，搜索结果处理失败，请稍后重试"
        state["citations"] = []
        observability.log_stage("respond_total", int((time.perf_counter() - started_at) * 1000))
        return state

    if state["error"]:
        state["response"] = "抱歉，搜索结果处理失败，请稍后重试"
        state["citations"] = []
        observability.log_stage("respond_total", int((time.perf_counter() - started_at) * 1000))
        return state

    if not state["results"]:
        state["response"] = ""
        state["citations"] = []
        observability.log_stage("respond_total", int((time.perf_counter() - started_at) * 1000))
        return state

    latest_result = state["results"][-1]
    base_response = latest_result.data
    state["citations"] = _dedupe_citations(state["citations"])
    if latest_result.tool == "search_documents":
        state["response"] = _with_react_limit_notice(state, base_response)
        observability.log_stage("respond_total", int((time.perf_counter() - started_at) * 1000))
        return state
    if state["context"]:
        state["response"] = _with_react_limit_notice(state, _respond_with_context(state, base_response))
    else:
        state["response"] = _with_react_limit_notice(state, base_response)
    observability.log_stage("respond_total", int((time.perf_counter() - started_at) * 1000))
    return state


def run_graph(session_id: str, message: str, mode: str = "fast") -> str:
    """运行规划层状态机并返回最终响应"""
    return run_graph_state(session_id, message, mode=mode)["response"]


def run_graph_state(session_id: str, message: str, mode: str = "fast") -> AgentState:
    """运行规划层状态机并返回完整状态，供接口层判断降级和记忆写入。"""
    state = _new_agent_state(session_id, message, mode)
    if mode == "fast":
        return _run_fast_state(state)
    if mode != "expert":
        raise ValueError("mode只支持fast或expert")

    try:
        return graph.invoke(state)
    except Exception as e:
        logger.error("规划层异常，降级为普通chat：session_id=%s error_type=%s", session_id, type(e).__name__)
        # Level2：expert规划层出错时仅使用同tier普通chat降级。
        fallback = execution.run(
            "llm_chat",
            {
                "message": message,
                "session_id": session_id,
                "tier": mode
            }
        )
        if fallback.status == "success":
            state["results"] = [fallback]
            state["citations"] = fallback.citations or []
            state["error"] = "planning_degraded"
            state["response"] = fallback.data
            return state
        state["results"] = [fallback]
        state["citations"] = []
        state["error"] = fallback.error_msg or "规划层降级失败"
        state["response"] = "抱歉，搜索结果处理失败，请稍后重试"
        return state


def _new_agent_state(session_id: str, message: str, mode: str) -> AgentState:
    return AgentState(
        session_id=session_id,
        message=message,
        mode=mode,
        intent="",
        context=[],
        tasks=[],
        results=[],
        citations=[],
        round_count=0,
        tool_call_history=[],
        react_action="",
        react_limit_reached=False,
        response="",
        error="",
        clarification="",
        city=""
    )


def _run_fast_state(state: AgentState) -> AgentState:
    """Run the independent fast path: retrieve, select an optional local tool, then answer."""
    try:
        state = retrieve_node(state)
        selection_started_at = time.perf_counter()
        first_response = llm_provider.chat_completion(
            _build_fast_messages(state),
            tier="fast",
            tools=FAST_TOOLS,
            tool_choice="auto"
        )
        selection_elapsed_ms = int((time.perf_counter() - selection_started_at) * 1000)
        tool_call = _select_fast_tool_call(_extract_tool_calls(first_response))
        if tool_call is None:
            observability.log_stage("fast_respond", selection_elapsed_ms)
            state["intent"] = "chat"
            state["response"] = llm_provider.extract_text(first_response)
            logger.info("fast路径完成：session_id=%s model_calls=1 tool=none", state["session_id"])
            return state

        task = _fast_task_from_tool_call(state, tool_call)
        observability.log_stage("fast_select_tool", selection_elapsed_ms)
        state["intent"] = "document" if task.tool == "search_documents" else "document_list"
        state["tasks"] = [task]
        tool_started_at = time.perf_counter()
        result = mcp_client.call_tool(task.tool, task.params)
        observability.log_stage(
            "execute_%s" % task.tool,
            int((time.perf_counter() - tool_started_at) * 1000),
        )
        state["results"] = [result]
        state["round_count"] = 1
        state["tool_call_history"] = [_tool_history_item(task)]
        state["citations"] = _dedupe_citations(result.citations or [])
        if result.status == "error":
            state["error"] = result.error_msg or "工具调用失败"
            state["response"] = "抱歉，知识库处理失败，请稍后重试"
            return state

        response_started_at = time.perf_counter()
        final_response = llm_provider.chat_completion(
            _build_fast_result_messages(state, result),
            tier="fast"
        )
        observability.log_stage("fast_respond", int((time.perf_counter() - response_started_at) * 1000))
        state["response"] = llm_provider.extract_text(final_response) or result.data
        logger.info(
            "fast路径完成：session_id=%s model_calls=2 tool=%s",
            state["session_id"],
            task.tool
        )
        return state
    except Exception as e:
        logger.error("fast路径失败：session_id=%s error_type=%s", state["session_id"], type(e).__name__)
        state["error"] = "fast_path_failed"
        state["response"] = "抱歉，快速模式暂时不可用，请稍后重试"
        state["citations"] = []
        return state


def _build_fast_messages(state: AgentState) -> list[dict]:
    system_content = (
        current_date_prompt()
        + "\n\n你处于快速模式，只能基于对话上下文、长期记忆和本地企业知识库回答。"
        "需要查询知识库正文时调用search_documents；需要列出文件清单时调用list_documents。"
        "其他问题直接回答，不调用工具。你没有联网搜索工具，不得声称已经查询互联网或获得实时结果。"
    )
    messages = [{"role": "system", "content": system_content}]
    messages.extend(_fast_history_messages(state["session_id"]))
    if state["context"]:
        messages.append({
            "role": "system",
            "content": "相关长期记忆：\n" + "\n".join(state["context"])
        })
    messages.append({"role": "user", "content": state["message"]})
    return messages


def _build_fast_result_messages(state: AgentState, result: ToolResult) -> list[dict]:
    messages = [{
        "role": "system",
        "content": (
            current_date_prompt()
            + "\n\n你处于快速模式。请只根据提供的本地工具结果和对话上下文回答，"
            "不要编造工具结果中不存在的信息，不要声称使用了联网搜索。"
        )
    }]
    messages.extend(_fast_history_messages(state["session_id"]))
    context_text = "\n".join(state["context"]) if state["context"] else "无"
    messages.append({
        "role": "user",
        "content": (
            "当前问题：%s\n\n长期记忆：%s\n\n本地工具：%s\n工具结果：%s"
            % (state["message"], context_text, result.tool, result.data)
        )
    })
    return messages


def _fast_history_messages(session_id: str) -> list[dict]:
    history = memory.get_history(session_id, limit=10) if session_id else []
    return [
        {"role": item["role"], "content": item["content"]}
        for item in history
        if item.get("role") in {"user", "assistant"} and item.get("content")
    ]


def _select_fast_tool_call(tool_calls: list[dict]) -> Optional[dict]:
    for tool_call in tool_calls:
        if tool_call.get("name") in {"search_documents", "list_documents"}:
            return tool_call
    return None


def _fast_task_from_tool_call(state: AgentState, tool_call: dict) -> Task:
    tool = tool_call["name"]
    if tool == "list_documents":
        return Task(tool=tool, params={}, order=1)
    arguments = tool_call.get("arguments") or {}
    query = str(arguments.get("query") or state["message"]).strip()
    return Task(
        tool="search_documents",
        params={
            "query": query,
            "tier": "fast",
            "generate_answer": False,
            "rerank_enabled": False
        },
        order=1
    )



def should_continue_react(state: AgentState) -> dict:
    """Use LLM reflection to decide whether another bounded tool round is useful."""
    max_total_rounds = 1 + int(config.MAX_REACT_ROUNDS)
    if state["error"] or not state["results"]:
        return {"action": "respond"}

    reflection = _reflect_with_glm(state)
    if state["round_count"] >= max_total_rounds:
        return {
            "action": "respond",
            "limit_reached": reflection.get("action") == "continue"
        }
    if reflection.get("action") != "continue":
        return {"action": "respond"}

    task = _task_from_reflection(state, reflection)
    if task and task.tool == "search_web" and _has_called_tool(state["tool_call_history"], "search_web"):
        logger.info("ReAct追加搜索已阻止：session_id=%s tool=search_web", state["session_id"])
        return {"action": "respond"}
    if not task or _has_called_task(state["tool_call_history"], task):
        return {"action": "respond"}
    return {"action": "continue", "task": task}


def next_after_execute(state: AgentState) -> str:
    if state["intent"] in {"chat", "search", "document_list"}:
        return "respond"
    if _should_skip_reflect_for_title_match(state):
        return "respond"
    return "reflect"


def _should_skip_reflect_for_title_match(state: AgentState) -> bool:
    if state["intent"] != "document" or not state["results"]:
        return False
    latest_result = state["results"][-1]
    if latest_result.tool != "search_documents" or latest_result.status != "success":
        return False
    metadata = latest_result.metadata or {}
    if not metadata.get("title_source_match"):
        return False
    candidate_count = int(metadata.get("candidate_count", 0) or 0)
    trusted_count = int(metadata.get("trusted_count", 0) or 0)
    return candidate_count <= 3 and trusted_count <= 3


def _reflect_with_glm(state: AgentState) -> dict:
    """Ask GLM whether current tool results are enough, without hard-coded semantic branching."""
    messages = [
        {
            "role": "system",
            "content": (
                current_date_prompt()
                + "\n\n"
                "你是轻量ReAct反思调度器，只判断当前工具结果是否足够回答用户问题。"
                "如果足够，返回JSON：{\"action\":\"respond\"}。"
                "如果不够，且需要再调用一次工具，返回JSON："
                "{\"action\":\"continue\",\"tool\":\"search_web|search_documents|llm_chat\",\"query\":\"下一轮查询或消息\"}。"
                "只能选择search_web、search_documents、llm_chat三个工具。"
                "判断时可以参考：文档citations是否为空或分数不足、搜索结果是否与问题相关、是否需要用另一类信息交叉验证。"
                "不要重复调用历史里已经用过的同一工具和同一参数。只返回JSON，不要解释。"
            )
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "question": state["message"],
                    "round_count": state["round_count"],
                    "max_additional_rounds": config.MAX_REACT_ROUNDS,
                    "tool_call_history": state["tool_call_history"],
                    "results": _summarize_results_for_reflection(state["results"]),
                    "citations": [citation.model_dump() for citation in _dedupe_citations(state["citations"])]
                },
                ensure_ascii=False
            )
        }
    ]
    try:
        response = llm_provider.chat_completion(messages, tier=state["mode"])
        raw = llm_provider.extract_text(response)
        return _parse_reflection(raw)
    except Exception as e:
        logger.error("ReAct反思判断失败：session_id=%s error_type=%s", state["session_id"], type(e).__name__)
        return {"action": "respond"}


def _parse_reflection(raw: str) -> dict:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except Exception:
        return {"action": "respond"}
    action = data.get("action")
    if action != "continue":
        return {"action": "respond"}
    tool = str(data.get("tool", "")).strip()
    if tool not in {"search_web", "search_documents", "llm_chat"}:
        return {"action": "respond"}
    query = str(data.get("query", "")).strip()
    return {"action": "continue", "tool": tool, "query": query}


def _task_from_intent(state: AgentState, order: int) -> Task:
    if state["intent"] == "document_list":
        return Task(
            tool="list_documents",
            params={},
            order=order
        )
    if state["intent"] == "search":
        return Task(
            tool="search_web",
            params={
                "query": state["message"],
                "context": state["context"],
                "session_id": state["session_id"],
                "tier": state["mode"]
            },
            order=order
        )
    if state["intent"] == "document":
        return Task(
            tool="search_documents",
            params={
                "query": state["message"],
                "tier": state["mode"]
            },
            order=order
        )
    return Task(
        tool="llm_chat",
        params={
            "message": state["message"],
            "session_id": state["session_id"],
            "tier": state["mode"]
        },
        order=order
    )


def _task_from_reflection(state: AgentState, reflection: dict) -> Optional[Task]:
    tool = str(reflection.get("tool", "")).strip()
    query = str(reflection.get("query", "")).strip() or state["message"]
    order = len(state["tasks"]) + 1
    if tool == "search_web":
        return Task(
            tool="search_web",
            params={
                "query": query,
                "context": state["context"],
                "session_id": state["session_id"],
                "tier": state["mode"]
            },
            order=order
        )
    if tool == "search_documents":
        return Task(
            tool="search_documents",
            params={
                "query": query,
                "tier": state["mode"]
            },
            order=order
        )
    if tool == "llm_chat":
        return Task(
            tool="llm_chat",
            params={
                "message": query,
                "session_id": state["session_id"],
                "tier": state["mode"]
            },
            order=order
        )
    return None


def _tool_history_item(task: Task) -> dict:
    return {
        "tool": task.tool,
        "params_summary": _task_params_summary(task)
    }


def _task_params_summary(task: Task) -> str:
    if task.tool in {"search_web", "search_documents"}:
        value = task.params.get("query", "")
    else:
        value = task.params.get("message", "")
    return str(value or "").strip()[:80]


def _has_called_task(history: list[dict], task: Task) -> bool:
    candidate = _tool_history_item(task)
    return any(
        item.get("tool") == candidate["tool"]
        and item.get("params_summary") == candidate["params_summary"]
        for item in history or []
    )


def _has_called_tool(history: list[dict], tool: str) -> bool:
    return any(item.get("tool") == tool for item in history or [])


def _summarize_results_for_reflection(results: list[ToolResult]) -> list[dict]:
    summary = []
    for result in results or []:
        citations = result.citations or []
        summary.append({
            "tool": result.tool,
            "status": result.status,
            "data_preview": str(result.data or "")[:500],
            "error_type": "tool_error" if result.status == "error" else "",
            "citation_count": len(citations),
            "citation_scores": [round(float(item.score), 6) for item in citations[:5]]
        })
    return summary


def _dedupe_citations(citations: list[Citation]) -> list[Citation]:
    deduped = []
    seen = set()
    for citation in citations or []:
        if isinstance(citation, dict):
            citation = Citation(**citation)
        key = (citation.doc_id, citation.chunk_index)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(citation)
    return deduped


def _with_react_limit_notice(state: AgentState, response: str) -> str:
    if not state.get("react_limit_reached"):
        return response
    notice = "基于目前检索到的信息回答，可能不够全面。"
    if str(response or "").startswith(notice):
        return response
    return f"{notice}\n\n{response or ''}".strip()

def _classify_with_glm(
    message: str,
    context: list[str] = None,
    tier: str = "fast"
) -> dict:
    """使用GLM Function Call选择搜索或直接回答"""
    context_text = "\n".join(context or [])
    response = llm_provider.chat_completion(
        messages=[
            {
                "role": "system",
                "content": (
                    "你只负责一次性完成工具选择、澄清判断和城市提取。"
                    "每次必须调用一个且仅一个主意图工具：search_web、search_documents、list_documents、direct_answer、ask_clarification。"
                    "如果用户明确提供自己的当前城市或所在地，优先写入主意图工具的city参数；模型能力支持多个工具时，可额外同时调用save_city。"
                    "save_city是附加工具，禁止单独调用；如果需要保存城市，也必须同时选择一个主意图工具，或将city写入主意图工具参数。"
                    "如果模型能力限制导致一次只能调用一个工具，禁止调用save_city，必须优先调用主意图工具并在city参数中带出城市。"
                    "需要实时信息或外部事实时选search_web；无需联网时选direct_answer；"
                    "当用户想知道企业信息库/知识库/已上传资料里“有哪些文件、哪些文档、哪些资料、上传了什么”时，必须选list_documents；"
                    "list_documents只列清单，不回答内容；"
                    "用户明确提到文档、资料、上传的文件、刚才的PDF、这份文件、这份文档等本地文档指代时选search_documents；"
                    "“这份文档说了什么”“文档主要内容是什么”“ERR-8842是什么意思”这类内容检索必须选search_documents，禁止选ask_clarification；"
                    "简短定义型问题也要判断是否可能属于企业知识库内容：例如“知了是什么”“蓝鲸项目是什么”“ERR-8842是什么”这类产品名、项目名、编号或非明显日常概念，应优先选search_documents检索验证，而不是直接用模型常识回答；"
                    "“企业信息库有哪些文件”“目前上传了哪些文档”“知识库里有哪些资料”“已上传的企业信息库文档有哪些”必须选list_documents，禁止选direct_answer或ask_clarification；"
                    "few-shot示例：用户问“企业信息库有哪些文件”=> list_documents；用户问“目前上传了哪些文档”=> list_documents；用户问“刚才上传的文档里有什么文件”=> list_documents；"
                    "用户问“知了是什么”=> search_documents，query_hint填“知了”；用户问“ERR-8842是什么意思”=> search_documents，query_hint填“ERR-8842”；用户问“这份文档说了什么”=> search_documents；"
                    "search_documents只用于本地已上传文档，不能用于互联网新闻、天气、价格或实时信息；"
                    "缺少城市、位置等关键信息导致无法准确回答时选ask_clarification。"
                    "天气/下雨/出行问题没有城市且上下文也没有用户城市时，必须选ask_clarification，不能选direct_answer；"
                    "天气/下雨/出行问题只要消息里有城市或上下文有用户城市，必须选search_web。"
                    "明确示例：“北京今天天气”必须选search_web；“我在北京，今天天气怎么样”必须选search_web，并在search_web.city中填写北京。"
                    "“北京”“上海”“广州”等城市名出现在天气/出行问题中时，视为城市已提供，不要澄清。"
                    "自我介绍、姓名、偏好、常住地、来自哪里等个人信息陈述不需要联网，必须选direct_answer；"
                    "这类消息即使包含城市，也只在direct_answer.city中带出城市，不要选search_web，除非用户同时询问天气、出行、新闻、价格或实时信息。"
                    "附近推荐问题没有位置且上下文也没有位置时，必须选ask_clarification。"
                    "用户只是评价、喜欢/不喜欢、询问或提到某城市时，不要调用save_city。"
                    f"\n\n可用历史上下文：\n{context_text or '无'}"
                )
            },
            {
                "role": "user",
                "content": message
            }
        ],
        tier=tier,
        tools=INTENT_TOOLS,
        tool_choice="auto",
        timeout=(
            config.EXPERT_LLM_TIMEOUT
            if tier == "expert"
            else config.FAST_LLM_TIMEOUT
        )
    )
    tool_calls = _extract_tool_calls(response)
    return _build_classify_decision(tool_calls)


def _respond_with_context(state: AgentState, base_response: str) -> str:
    """在有长期记忆上下文时，让GLM结合上下文生成最终回复"""
    context_text = "\n".join(state["context"])
    system_prompt = (
        f"{current_date_prompt()}\n\n"
        f"以下是与当前问题相关的历史记录，供参考：\n{context_text}\n\n"
        "如果历史记录与当前问题不相关，请忽略，不要主动引入无关信息。"
    )
    messages = [
        {
            "role": "system",
            "content": system_prompt
        },
        {
            "role": "user",
            "content": (
                f"用户当前问题：{state['message']}\n\n"
                f"执行层初步回复：{base_response}\n\n"
                "请结合历史记录和初步回复，生成自然、准确的最终回答。"
            )
        }
    ]
    try:
        started_at = time.perf_counter()
        response = llm_provider.extract_text(
            llm_provider.chat_completion(messages, tier=state["mode"])
        )
        observability.log_stage("respond_context_glm", int((time.perf_counter() - started_at) * 1000))
        return response
    except Exception:
        return base_response


def _extract_tool_calls(response) -> list[dict]:
    """从GLM Function Call响应中提取所有工具名和参数"""
    choices = getattr(response, "choices", None)
    if not choices and isinstance(response, dict):
        choices = response.get("choices") or []
    if not choices:
        return [{"name": "direct_answer", "arguments": {}}]

    first_choice = choices[0]
    message = getattr(first_choice, "message", None)
    if message is None and isinstance(first_choice, dict):
        message = first_choice.get("message") or {}
    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls and isinstance(message, dict):
        tool_calls = message.get("tool_calls")
    if not tool_calls:
        return [{"name": "direct_answer", "arguments": {}}]

    parsed_calls = []
    for tool_call in tool_calls:
        function = getattr(tool_call, "function", None)
        if function is None and isinstance(tool_call, dict):
            function = tool_call.get("function", {})

        name = getattr(function, "name", None)
        if name is None and isinstance(function, dict):
            name = function.get("name")
        raw_arguments = getattr(function, "arguments", None)
        if raw_arguments is None and isinstance(function, dict):
            raw_arguments = function.get("arguments")
        parsed_calls.append({
            "name": name or "direct_answer",
            "arguments": _parse_tool_arguments(raw_arguments)
        })
    return parsed_calls


def _build_classify_decision(tool_calls: list[dict]) -> dict:
    """根据一次Function Call返回的多个工具调用合成规划决策"""
    decision = {"intent": "chat", "clarification": "", "city": ""}
    has_save_city = False
    for tool_call in tool_calls:
        name = tool_call["name"]
        arguments = tool_call["arguments"]
        if name == "save_city" and arguments.get("city"):
            has_save_city = True
            decision["city"] = str(arguments["city"]).strip()
            continue
        if name == "ask_clarification":
            decision["intent"] = "clarify"
            decision["clarification"] = arguments.get("question", "请补充关键信息。")
            continue
        if name == "search_web" and decision["intent"] != "clarify":
            decision["intent"] = "search"
            decision["clarification"] = ""
            continue
        if name == "search_documents" and decision["intent"] != "clarify":
            decision["intent"] = "document"
            decision["clarification"] = ""
            continue
        if name == "list_documents" and decision["intent"] != "clarify":
            decision["intent"] = "document_list"
            decision["clarification"] = ""
            continue
        if name == "direct_answer" and decision["intent"] not in {"search", "document", "document_list", "clarify"}:
            decision["intent"] = "chat"
    if has_save_city and decision["intent"] == "chat" and len(tool_calls) == 1:
        decision["intent"] = "search"
    return decision


def _parse_tool_arguments(raw_arguments) -> dict:
    """解析Function Call参数"""
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if not raw_arguments:
        return {}
    try:
        return json.loads(raw_arguments)
    except Exception:
        return {}


def _load_classify_context(session_id: str, message: str) -> list[str]:
    """读取少量长期记忆，辅助判断是否需要澄清"""
    if not session_id:
        return []
    context = []
    for query in [message, "用户城市 用户位置"]:
        try:
            for item in memory.search_session_memory(query, session_id=session_id, top_k=3):
                if item not in context:
                    context.append(item)
        except Exception as e:
            logger.error("规划层上下文检索失败：session_id=%s error_type=%s", session_id, type(e).__name__)
            pass
    return context[:3]


def _merge_context(primary: list[str], secondary: list[str]) -> list[str]:
    """合并上下文并去重，保留classify阶段已有城市信息"""
    merged = []
    for item in (primary or []) + (secondary or []):
        if item and item not in merged:
            merged.append(item)
    return merged[:3]


def _save_city_memory(session_id: str, city: str) -> None:
    """写入用户城市长期记忆"""
    if not session_id or not city:
        return
    try:
        memory.save_to_vector(
            session_id,
            f"用户城市：{city}",
            role="user",
            importance_level=memory.IMPORTANCE_LEVEL_HIGH
        )
    except Exception as e:
        logger.error("城市信息写入失败：session_id=%s error_type=%s", session_id, type(e).__name__)
        pass


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


builder = StateGraph(AgentState)
builder.add_node("classify", classify_node)
builder.add_node("retrieve", retrieve_node)
builder.add_node("plan", plan_node)
builder.add_node("execute", execute_node)
builder.add_node("reflect", reflect_node)
builder.add_node("respond", respond_node)
builder.set_entry_point("classify")
builder.add_conditional_edges(
    "classify",
    lambda state: "clarify" if state["intent"] == "clarify" else "continue",
    {
        "clarify": "respond",
        "continue": "retrieve"
    }
)
builder.add_edge("retrieve", "plan")
builder.add_edge("plan", "execute")
builder.add_conditional_edges(
    "execute",
    next_after_execute,
    {
        "respond": "respond",
        "reflect": "reflect"
    }
)
builder.add_conditional_edges(
    "reflect",
    lambda state: "continue" if state["react_action"] == "continue" else "respond",
    {
        "continue": "plan",
        "respond": "respond"
    }
)
builder.add_edge("respond", END)
graph = builder.compile()



