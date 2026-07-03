# -*- coding: utf-8 -*-
# 规划层：LangGraph状态机调度意图分类、记忆检索、执行和响应生成

import json
from typing import TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel

import config
from layers import execution, llm_client, memory
from layers.execution import ToolResult
from layers.mcp_client import mcp_client
from utils.logger import get_logger

logger = get_logger("planning")


class Task(BaseModel):
    tool: str
    params: dict
    order: int


class AgentState(TypedDict):
    session_id: str
    message: str
    intent: str
    context: list[str]
    tasks: list[Task]
    results: list[ToolResult]
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
                "仅在用户提到“文档”“资料”“上传的文件”“刚才的PDF”“这份文件”“这份文档”等明确指代本地文档时使用。"
                "只要用户问“这份文档说了什么”“刚才上传的文档里有什么”“文档主要内容是什么”，必须调用本工具。"
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


def classify_node(state: AgentState) -> AgentState:
    """classify节点：调用GLM Function Call判断意图"""
    state["context"] = _load_classify_context(state["session_id"], state["message"])
    decision = _classify_with_glm(state["message"], state["context"])
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
    return state


def execute_node(state: AgentState) -> AgentState:
    """execute节点：按意图调度执行层工具"""
    if state["intent"] == "search":
        task = Task(
            tool="search_web",
            params={
                "query": state["message"],
                "context": state["context"],
                "session_id": state["session_id"]
            },
            order=1
        )
    elif state["intent"] == "document":
        task = Task(
            tool="search_documents",
            params={
                "query": state["message"]
            },
            order=1
        )
    else:
        task = Task(
            tool="llm_chat",
            params={
                "message": state["message"],
                "session_id": state["session_id"]
            },
            order=1
        )
    state["tasks"] = [task]
    result = mcp_client.call_tool(task.tool, task.params)
    state["results"] = [result]
    if result.status == "error":
        state["error"] = result.error_msg
    return state


def respond_node(state: AgentState) -> AgentState:
    """respond节点：读取执行结果并生成最终响应"""
    if state["intent"] == "clarify":
        state["response"] = state["clarification"]
        return state

    failed_results = [result for result in state["results"] if result.status == "error"]
    if failed_results:
        state["error"] = failed_results[0].error_msg or "工具调用失败"
        state["response"] = "抱歉，搜索结果处理失败，请稍后重试"
        return state

    if state["error"]:
        state["response"] = "抱歉，搜索结果处理失败，请稍后重试"
        return state

    if not state["results"]:
        state["response"] = ""
        return state

    base_response = state["results"][-1].data
    if state["results"][-1].tool == "search_documents":
        state["response"] = base_response
        return state
    if state["context"]:
        state["response"] = _respond_with_context(state, base_response)
    else:
        state["response"] = base_response
    return state


def run_graph(session_id: str, message: str) -> str:
    """运行规划层状态机并返回最终响应"""
    return run_graph_state(session_id, message)["response"]


def run_graph_state(session_id: str, message: str) -> AgentState:
    """运行规划层状态机并返回完整状态，供接口层判断降级和记忆写入。"""
    state = AgentState(
        session_id=session_id,
        message=message,
        intent="",
        context=[],
        tasks=[],
        results=[],
        response="",
        error="",
        clarification="",
        city=""
    )
    try:
        return graph.invoke(state)
    except Exception as e:
        logger.error("规划层异常，降级为普通chat：session_id=%s error_type=%s", session_id, type(e).__name__)
        # Level2：规划层出错时降级为普通chat模式
        fallback = execution.run(
            "llm_chat",
            {
                "message": message,
                "session_id": session_id
            }
        )
        if fallback.status == "success":
            state["response"] = fallback.data
            return state
        state["error"] = fallback.error_msg or "规划层降级失败"
        state["response"] = "抱歉，搜索结果处理失败，请稍后重试"
        return state


def _classify_with_glm(message: str, context: list[str] = None) -> dict:
    """使用当前LLM Function Call选择搜索或直接回答"""
    if not llm_client.has_valid_key():
        raise ValueError(f"{llm_client.provider_name().upper()} API_KEY未配置")

    context_text = "\n".join(context or [])
    response = llm_client.chat(
        messages=[
            {
                "role": "system",
                "content": (
                    "你只负责一次性完成工具选择、澄清判断和城市提取。"
                    "每次必须调用一个且仅一个主意图工具：search_web、search_documents、direct_answer、ask_clarification。"
                    "如果用户明确提供自己的当前城市或所在地，优先写入主意图工具的city参数；模型能力支持多个工具时，可额外同时调用save_city。"
                    "save_city是附加工具，禁止单独调用；如果需要保存城市，也必须同时选择一个主意图工具，或将city写入主意图工具参数。"
                    "如果模型能力限制导致一次只能调用一个工具，禁止调用save_city，必须优先调用主意图工具并在city参数中带出城市。"
                    "需要实时信息或外部事实时选search_web；无需联网时选direct_answer；"
                    "用户明确提到文档、资料、上传的文件、刚才的PDF、这份文件、这份文档等本地文档指代时选search_documents；"
                    "“这份文档说了什么”“刚才上传的文档里有什么”“文档主要内容是什么”必须选search_documents，禁止选ask_clarification；"
                    "search_documents只用于本地已上传文档，不能用于互联网新闻、天气、价格或实时信息；"
                    "缺少城市、位置等关键信息导致无法准确回答时选ask_clarification。"
                    "天气/下雨/出行问题没有城市且上下文也没有用户城市时，必须选ask_clarification，不能选direct_answer；"
                    "天气/下雨/出行问题只要消息里有城市或上下文有用户城市，必须选search_web。"
                    "明确示例：“北京今天天气”必须选search_web；“我在北京，今天天气怎么样”必须选search_web，并在search_web.city中填写北京。"
                    "“北京”“上海”“广州”等城市名出现在天气/出行问题中时，视为城市已提供，不要澄清。"
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
        model=llm_client.fallback_model(),
        tools=INTENT_TOOLS,
        tool_choice="auto"
    )
    tool_calls = _extract_tool_calls(response)
    return _build_classify_decision(tool_calls)


def _respond_with_context(state: AgentState, base_response: str) -> str:
    """在有长期记忆上下文时，让GLM结合上下文生成最终回复"""
    context_text = "\n".join(state["context"])
    system_prompt = (
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
        return _chat_with_fallback(messages)
    except Exception:
        return base_response


def _chat_with_fallback(messages: list[dict]) -> str:
    """调用当前LLM生成规划层最终回复，主模型失败后降级fallback"""
    primary_error = ""
    try:
        return _chat_with_model(llm_client.primary_model(), messages)
    except Exception as e:
        primary_error = str(e)

    try:
        return _chat_with_model(llm_client.fallback_model(), messages)
    except Exception as e:
        raise RuntimeError(f"主模型失败：{primary_error}；fallback失败：{e}") from e


def _chat_with_model(model: str, messages: list[dict]) -> str:
    """调用指定LLM模型"""
    if not llm_client.has_valid_key():
        raise ValueError(f"{llm_client.provider_name().upper()} API_KEY未配置")
    return str(llm_client.chat(messages, model=model))


def _extract_tool_calls(response) -> list[dict]:
    """从GLM Function Call响应中提取所有工具名和参数"""
    choices = getattr(response, "choices", None)
    if not choices:
        return [{"name": "direct_answer", "arguments": {}}]

    message = getattr(choices[0], "message", None)
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
        if name == "direct_answer" and decision["intent"] not in {"search", "document", "clarify"}:
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
        memory.save_to_vector(session_id, f"用户城市：{city}", importance="high")
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
builder.add_node("execute", execute_node)
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
builder.add_edge("retrieve", "execute")
builder.add_edge("execute", "respond")
builder.add_edge("respond", END)
graph = builder.compile()
