# -*- coding: utf-8 -*-
# 规划层：LangGraph状态机调度意图分类、记忆检索、执行和响应生成

import json
import re
import time
from typing import Literal, Optional, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field
import config
from layers import execution, llm_provider, memory, system_modules
from layers.execution import Citation, ToolResult
from layers.mcp_client import mcp_client
from utils.logger import get_logger
from utils import observability
from utils.time_context import cache_friendly_messages, current_date_prompt

logger = get_logger("planning")


class Task(BaseModel):
    tool: str
    params: dict
    order: int
    task_index: int = 0
    status: Literal["pending", "success", "error"] = "pending"
    adjusted: bool = False


class ComplexTaskResult(BaseModel):
    task_index: int
    tool: str
    status: Literal["success", "error"]
    result_summary: str = ""
    citations: list[Citation] = Field(default_factory=list)


class FastEvidenceSelection(BaseModel):
    evidence_sufficient: bool = False
    used_candidate_ids: list[int] = Field(default_factory=list)
    reason: str = ""


FAST_EVIDENCE_PROMPT = """你是知天智能问答系统的证据筛选环节。你会收到用户问题，以及从企业知识库检索到的若干候选片段（每个候选带编号和原文内容）。

你的任务：判断这些候选片段中，哪些足以支撑对用户问题的可靠回答。

判断原则：
1. 依据语义相关性判断，不要求候选片段与问题使用完全相同的措辞。
2. 只要存在至少一个候选片段的内容能够支撑对用户问题的可靠回答，即判定证据充分（evidence_sufficient=true），并列出所有真正相关候选的编号。
3. 如果全部候选片段都与问题的实际询问内容不符（仅字面相似、或主题无关），判定证据不充分（evidence_sufficient=false），候选编号列表为空。
4. 不要求候选片段覆盖问题的全部细节才算充分——只要核心问题能被候选内容回答，即视为充分，避免因"不完整"而误判为不充分。
5. 只输出以下JSON，不要输出任何其他文字：
{"evidence_sufficient": true/false, "used_candidate_ids": [编号], "reason": "一句话说明判断依据"}"""


FAST_DOCUMENT_GENERATION_PROMPT = """你是知天智能问答系统的回答生成环节，服务于企业员工。你会收到用户问题，以及已经过筛选、确认为真正相关的知识库片段（如果为空，说明上一环节判定证据不充分）。

生成原则：
1. 如果提供了知识库片段，仅基于这些片段内容组织回答，不得引入片段之外的自身知识来补充、替换或"完善"片段内容；片段信息不完整时，如实说明"资料未详细说明"，不要编造。
2. 如果没有提供任何知识库片段，直接回复"未找到可靠依据，无法确认答案"，可视情况建议咨询专业人士或查阅相关法规名称，不展开具体条款内容。
3. 回答简洁准确，不堆砌免责声明。
4. 不需要重新评估证据是否充分——这一步已在上一环节完成。"""


class AgentState(TypedDict):
    session_id: str
    owner_user_id: str
    message: str
    mode: str
    intent: str
    context: list[str]
    attachment_context: list[str]
    attachment_ids: list[str]
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
    filename_hint: str
    output_format: str
    conversion_target_format: str
    decision_reasoning: Optional[str]
    is_complex_task: bool
    complex_task_list: list[Task]
    complex_task_results: list[ComplexTaskResult]
    full_replan_used: bool
    current_task_pointer: int
    complex_task_created_count: int
    complex_action: str
    complex_deadline: float
    stream_prepared: bool
    layer_trace: list[str]


INTENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "declare_complex_task",
            "description": (
                "仅当用户目标必须拆成多个有顺序的独立步骤才能完成时调用。"
                "典型场景包括多个独立信息源或对象的检索与对比、先检索再分析汇总、"
                "或单一工具调用无法覆盖完整目标。简单单问、单次搜索、单份文档查询、"
                "文件清单和普通对话不得调用。用户要求‘分别搜索A和B并对比’、‘先查A再结合B给建议’"
                "时必须调用本工具，不能用一次search_web或direct_answer代替。"
                "该工具只声明需要任务分解，不执行实际工作。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "需要多步骤完成的简短原因，不包含任务清单"
                    }
                },
                "required": ["reason"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "当用户问题完整清晰，且需要实时信息、联网搜索、天气、新闻、价格、最新状态或外部事实核验时调用。"
                "本工具只表示一个单一搜索目标；如果用户要求分别检索多个对象后比较或汇总，应调用declare_complex_task。"
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
                "当当前知识库已有某个专业或业务领域的verified资料时，用户提出该领域内的事实性、规范性或依据性问题，即使没有显式提到文档或复述资料原词，也应优先调用本工具检索核验，不要仅凭模型训练知识直接回答。"
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
            "name": "generate_file",
            "description": (
                "仅当用户明确要求把内容整理、导出或生成为一份可下载的文件、文档、清单或报告时调用。"
                "本工具表示需要先生成完整正文，再保存为可交付文件；普通问答、只需在聊天中展示内容、"
                "读取已有文件或转换已有文件格式时不要调用。支持md、txt、pdf、docx四种输出格式；"
                "md适合结构化文本，txt适合纯文本，用户明确要求正式文档、报告或可打印材料时可选择pdf或docx。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename_hint": {
                        "type": "string",
                        "description": "简短、用户可读的建议文件名，不包含目录路径"
                    },
                    "output_format": {
                        "type": "string",
                        "enum": ["md", "txt", "pdf", "docx"],
                        "description": "输出格式，默认md；正式文档、报告或可打印材料可选pdf/docx"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "convert_document",
            "description": (
                "仅当用户明确要求转换本轮对话已经上传的一个附件时调用。"
                "这是格式转换，不是读取、总结附件，也不是生成新内容。"
                "支持PDF转Word(DOCX)、Excel(XLSX)或PPT(PPTX)，以及Word(DOC/DOCX)、"
                "Excel(XLS/XLSX)、PPT(PPT/PPTX)转PDF；另外保留DOC转DOCX兼容能力。"
                "没有附件或同时存在多个附件时仍选择本工具，由系统提示用户上传或明确目标，禁止猜测附件。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "attachment_id": {
                        "type": "string",
                        "description": "本轮请求中唯一附件的attachment_id"
                    },
                    "target_format": {
                        "type": "string",
                        "enum": ["pdf", "docx", "xlsx", "pptx"],
                        "description": "用户明确要求的目标格式"
                    }
                },
                "required": ["attachment_id", "target_format"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "direct_answer",
            "description": (
                "当用户问题完整清晰，可以直接根据已有上下文或通用知识回答，不需要联网搜索时调用。"
                "如果问题属于可能被当前知识库已有verified资料覆盖的专业或业务领域，并且涉及事实、规范、依据或结论核验，不得调用本工具，应调用search_documents；是否属于该范围由语义和知识库领域背景综合判断，不要求用户显式提到文档。"
                "当本轮attachment_ids非空时，附件正文已经作为本轮上下文提供；读取、概括、分析当前附件应调用本工具，"
                "不要为读取当前附件调用search_documents或list_documents。只有格式转换请求才调用convert_document。"
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

DECISION_REASONING_FALLBACK = "已根据问题内容选择处理路径"
for _intent_tool in INTENT_TOOLS:
    _intent_tool["function"]["parameters"]["properties"]["reasoning"] = {
        "type": "string",
        "description": "用一句话说明选择该工具的依据，控制在60字以内",
    }


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

COMPLEX_TOOL_NAMES = {"search_web", "search_documents", "list_documents", "llm_chat"}


def classify_node(state: AgentState) -> AgentState:
    """classify节点：调用所选模型的 Function Call 判断意图。"""
    if state.get("stream_prepared") and state.get("intent"):
        return state
    started_at = time.perf_counter()
    state["context"] = _merge_context(
        state["context"],
        _load_classify_context(state["session_id"], state["message"]),
    )
    observability.log_stage("classify_context", int((time.perf_counter() - started_at) * 1000))
    started_at = time.perf_counter()
    decision = _classify_with_model(
        state["message"],
        state["context"],
        tier=state["mode"],
        attachment_ids=state.get("attachment_ids", []),
    )
    observability.log_stage("classify_model", int((time.perf_counter() - started_at) * 1000))
    state["intent"] = decision["intent"]
    state["is_complex_task"] = state["intent"] == "complex_task"
    state["clarification"] = decision.get("clarification", "")
    city = decision.get("city", "")
    state["city"] = city
    state["filename_hint"] = str(decision.get("filename_hint", "") or "")
    state["output_format"] = str(decision.get("output_format", "md") or "md")
    state["conversion_target_format"] = str(
        decision.get("conversion_target_format", "") or ""
    )
    if state["intent"] == "convert_document":
        current_attachment_ids = state.get("attachment_ids", [])
        if not current_attachment_ids:
            state["clarification"] = "请先上传需要转换的文件。"
        elif len(current_attachment_ids) != 1:
            state["clarification"] = "当前有多个附件，请明确指出要转换哪一个。"
    state["decision_reasoning"] = _normalize_decision_reasoning(
        decision.get("decision_reasoning")
    )
    if city:
        _save_city_memory(state["session_id"], city)
    logger.info(
        "意图分类结果：session_id=%s intent=%s reasoning_present=%s reasoning_len=%s",
        state["session_id"],
        state["intent"],
        bool(state["decision_reasoning"]),
        len(state["decision_reasoning"] or ""),
    )
    return state


def retrieve_node(state: AgentState) -> AgentState:
    """retrieve节点：从Chroma检索语义相关的长期记忆"""
    if state.get("stream_prepared"):
        return state
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
    if state["intent"] == "generate_file" and result.status == "success":
        state["results"].append(result)
        result = _save_generated_content(state, result.data)
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
    observability.log_stage("reflect_model", int((time.perf_counter() - started_at) * 1000))
    state["react_action"] = decision["action"]
    state["react_limit_reached"] = bool(decision.get("limit_reached", False))
    next_task = decision.get("task")
    if state["react_action"] == "continue" and next_task:
        state["tasks"].append(next_task)
    return state


def complex_plan_node(state: AgentState) -> AgentState:
    """Generate the initial bounded linear task list for an expert request."""
    if _complex_budget_exhausted(state):
        return _mark_complex_timeout(state)
    started_at = time.perf_counter()
    try:
        tasks = _generate_complex_tasks(state, config.MAX_COMPLEX_TASKS)
    except TimeoutError:
        observability.log_stage("complex_plan_model", int((time.perf_counter() - started_at) * 1000))
        return _mark_complex_timeout(state)
    observability.log_stage("complex_plan_model", int((time.perf_counter() - started_at) * 1000))
    state["complex_task_list"] = tasks
    state["complex_task_created_count"] = len(tasks)
    state["current_task_pointer"] = 0
    state["complex_action"] = "execute" if tasks else "respond"
    _append_layer_trace(state, "complex_plan")
    if not tasks:
        state["error"] = "complex_plan_failed"
    logger.info("复杂任务规划完成：session_id=%s task_count=%s", state["session_id"], len(tasks))
    return state


def execute_complex_node(state: AgentState) -> AgentState:
    """Execute exactly one task from the expert linear plan."""
    if _complex_budget_exhausted(state):
        return _mark_complex_timeout(state)
    pointer = state["current_task_pointer"]
    if pointer >= len(state["complex_task_list"]):
        state["complex_action"] = "respond"
        return state

    task = state["complex_task_list"][pointer]
    started_at = time.perf_counter()
    params = dict(task.params)
    remaining = _remaining_complex_budget(state)
    if task.tool == "search_web":
        params["total_budget"] = remaining
    elif task.tool in {"search_documents", "llm_chat"}:
        params["timeout"] = min(config.EXPERT_LLM_TIMEOUT, remaining)
    result = mcp_client.call_tool(task.tool, params)
    observability.log_stage(
        "complex_execute_%s" % task.tool,
        int((time.perf_counter() - started_at) * 1000),
    )
    task.status = "success" if result.status == "success" else "error"
    state["complex_task_list"][pointer] = task
    state["complex_task_results"].append(
        ComplexTaskResult(
            task_index=task.task_index,
            tool=task.tool,
            status=task.status,
            result_summary=_summarize_complex_result(result),
            citations=_dedupe_citations(result.citations or []),
        )
    )
    state["results"].append(result)
    state["tool_call_history"].append(_tool_history_item(task))
    state["citations"] = _dedupe_citations(state["citations"] + (result.citations or []))
    state["current_task_pointer"] += 1
    state["round_count"] += 1
    state["complex_action"] = "checkpoint"
    _append_layer_trace(state, "execute_complex")
    return state


def checkpoint_node(state: AgentState) -> AgentState:
    """Apply one global replan opportunity and one local adjustment per task position."""
    _append_layer_trace(state, "checkpoint")
    if _complex_budget_exhausted(state):
        return _mark_complex_timeout(state)
    if state["current_task_pointer"] >= len(state["complex_task_list"]):
        state["complex_action"] = "respond"
        return state
    if _consecutive_complex_failures(state["complex_task_results"]) >= 2:
        state["error"] = "complex_task_multiple_failures"
        state["complex_action"] = "respond"
        return state

    if not state["full_replan_used"]:
        started_at = time.perf_counter()
        try:
            route = _check_complex_route_with_model(state)
        except Exception as e:
            logger.warning("复杂任务路线判断失败：session_id=%s error_type=%s", state["session_id"], type(e).__name__)
            route = "keep"
        observability.log_stage("complex_checkpoint_route_model", int((time.perf_counter() - started_at) * 1000))
        if route == "replan":
            state["full_replan_used"] = True
            remaining_budget = max(0, config.MAX_COMPLEX_TASKS - state["complex_task_created_count"])
            if remaining_budget:
                started_at = time.perf_counter()
                try:
                    replacement = _generate_complex_tasks(state, remaining_budget, remaining_only=True)
                except Exception as e:
                    logger.warning("复杂任务重规划失败：session_id=%s error_type=%s", state["session_id"], type(e).__name__)
                    replacement = []
                observability.log_stage("complex_replan_model", int((time.perf_counter() - started_at) * 1000))
                if replacement:
                    completed = state["complex_task_list"][:state["current_task_pointer"]]
                    state["complex_task_list"] = completed + replacement
                    state["complex_task_created_count"] += len(replacement)
            state["complex_action"] = (
                "checkpoint"
                if state["current_task_pointer"] < len(state["complex_task_list"])
                else "respond"
            )
            return state

    next_task = state["complex_task_list"][state["current_task_pointer"]]
    remaining_budget = max(0, config.MAX_COMPLEX_TASKS - state["complex_task_created_count"])
    if not next_task.adjusted and remaining_budget:
        started_at = time.perf_counter()
        try:
            adjusted_task = _adjust_complex_task_with_model(state, next_task)
            next_task.adjusted = True
            state["complex_task_list"][state["current_task_pointer"]] = next_task
        except Exception as e:
            logger.warning("复杂任务局部调整失败：session_id=%s error_type=%s", state["session_id"], type(e).__name__)
            adjusted_task = None
        observability.log_stage("complex_checkpoint_adjust_model", int((time.perf_counter() - started_at) * 1000))
        if adjusted_task is not None:
            adjusted_task.adjusted = True
            state["complex_task_list"][state["current_task_pointer"]] = adjusted_task
            state["complex_task_created_count"] += 1
    state["complex_action"] = "execute"
    return state


def complex_respond_node(state: AgentState) -> AgentState:
    """Synthesize all expert subtask results into one response."""
    started_at = time.perf_counter()
    state["citations"] = _dedupe_citations(state["citations"])
    if state["error"] == "complex_task_timeout" or _complex_budget_exhausted(state):
        state["error"] = "complex_task_timeout"
        state["response"] = _complex_timeout_response(state["complex_task_results"])
        observability.log_stage("complex_respond_model", 0)
        _append_layer_trace(state, "complex_respond")
        return state
    try:
        response = llm_provider.chat_completion(
            cache_friendly_messages(
                system_modules.prompt_prefix(
                    "你负责汇总一个线性多步骤任务的执行结果。严格基于给出的结果回答原始目标，"
                    "明确说明失败或证据不足的部分，不得编造未提供的信息。"
                ),
                [
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "goal": state["message"],
                            "task_results": _complex_results_payload(state["complex_task_results"]),
                        },
                        ensure_ascii=False,
                    ),
                }],
                include_date=True,
            ),
            tier="expert",
            timeout=min(config.EXPERT_LLM_TIMEOUT, _remaining_complex_budget(state)),
            total_budget=_remaining_complex_budget(state),
        )
        state["response"] = llm_provider.extract_text(response)
        if not state["response"]:
            raise ValueError("empty complex response")
    except TimeoutError:
        state["error"] = "complex_task_timeout"
        state["response"] = _complex_timeout_response(state["complex_task_results"])
    except Exception as e:
        logger.error("复杂任务汇总失败：session_id=%s error_type=%s", state["session_id"], type(e).__name__)
        state["error"] = state["error"] or "complex_respond_failed"
        state["response"] = _fallback_complex_response(state["complex_task_results"])
    observability.log_stage("complex_respond_model", int((time.perf_counter() - started_at) * 1000))
    _append_layer_trace(state, "complex_respond")
    return state

def respond_node(state: AgentState) -> AgentState:
    """respond节点：读取执行结果并生成最终响应"""
    started_at = time.perf_counter()
    if state["intent"] == "clarify":
        state["response"] = state["clarification"]
        state["citations"] = []
        observability.log_stage("respond_total", int((time.perf_counter() - started_at) * 1000))
        return state

    if state["intent"] == "generate_file":
        _respond_with_generated_file(state)
        observability.log_stage("respond_total", int((time.perf_counter() - started_at) * 1000))
        return state

    if state["intent"] == "convert_document":
        _respond_with_converted_file(state)
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


def run_graph(
    session_id: str,
    message: str,
    mode: str = "fast",
    extra_context: Optional[list[str]] = None,
    owner_user_id: str = "",
    attachment_ids: Optional[list[str]] = None,
) -> str:
    """运行规划层状态机并返回最终响应"""
    return run_graph_state(
        session_id,
        message,
        mode=mode,
        extra_context=extra_context,
        owner_user_id=owner_user_id,
        attachment_ids=attachment_ids,
    )["response"]


def run_graph_state(
    session_id: str,
    message: str,
    mode: str = "fast",
    extra_context: Optional[list[str]] = None,
    owner_user_id: str = "",
    attachment_ids: Optional[list[str]] = None,
    prepared_state: Optional[AgentState] = None,
) -> AgentState:
    """运行规划层状态机并返回完整状态，供接口层判断降级和记忆写入。"""
    state = prepared_state or _new_agent_state(
        session_id,
        message,
        mode,
        extra_context=extra_context,
        owner_user_id=owner_user_id,
        attachment_ids=attachment_ids,
    )
    if prepared_state is not None:
        state["stream_prepared"] = True
    if mode == "fast":
        return _run_fast_state(state)
    if mode != "expert":
        raise ValueError("mode只支持fast或expert")

    state["complex_deadline"] = time.perf_counter() + config.EXPERT_COMPLEX_TIMEOUT
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


def _new_agent_state(
    session_id: str,
    message: str,
    mode: str,
    extra_context: Optional[list[str]] = None,
    owner_user_id: str = "",
    attachment_ids: Optional[list[str]] = None,
) -> AgentState:
    return AgentState(
        session_id=session_id,
        owner_user_id=owner_user_id,
        message=message,
        mode=mode,
        intent="",
        context=list(extra_context or []),
        attachment_context=list(extra_context or []),
        attachment_ids=list(attachment_ids or []),
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
        city="",
        filename_hint="",
        output_format="md",
        conversion_target_format="",
        decision_reasoning=None,
        is_complex_task=False,
        complex_task_list=[],
        complex_task_results=[],
        full_replan_used=False,
        current_task_pointer=0,
        complex_task_created_count=0,
        complex_action="",
        complex_deadline=0.0,
        stream_prepared=False,
        layer_trace=[]
    )


def _run_fast_state(state: AgentState) -> AgentState:
    """Run fast with one call for chat or up to three for tool selection, evidence, and answer."""
    deadline = time.perf_counter() + config.FAST_REQUEST_TIMEOUT
    try:
        state = retrieve_node(state)
        selection_started_at = time.perf_counter()
        first_response = llm_provider.chat_completion(
            _build_fast_messages(state),
            tier="fast",
            tools=FAST_TOOLS,
            tool_choice="auto",
            timeout=min(config.FAST_LLM_TIMEOUT, _remaining_fast_budget(deadline)),
            total_budget=_remaining_fast_budget(deadline),
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
        state["citations"] = []
        if result.status == "error":
            state["error"] = result.error_msg or "工具调用失败"
            state["response"] = "抱歉，知识库处理失败，请稍后重试"
            return state

        selected_evidence = ""
        if task.tool == "search_documents":
            evidence_started_at = time.perf_counter()
            try:
                evidence_response = llm_provider.chat_completion(
                    _build_fast_evidence_messages(state, result),
                    tier="fast",
                    response_format={"type": "json_object"},
                    timeout=min(config.FAST_LLM_TIMEOUT, _remaining_fast_budget(deadline)),
                    total_budget=_remaining_fast_budget(deadline),
                )
                selection = _parse_fast_evidence_selection(evidence_response)
                selected_evidence, selected_citations = _select_fast_evidence(
                    result,
                    selection.used_candidate_ids if selection.evidence_sufficient else [],
                )
            except Exception as exc:
                selection = FastEvidenceSelection()
                selected_citations = []
                logger.warning(
                    "fast证据筛选失败并按证据不足处理：session_id=%s error_type=%s",
                    state["session_id"],
                    type(exc).__name__,
                )
            observability.log_stage(
                "fast_evidence_filter",
                int((time.perf_counter() - evidence_started_at) * 1000),
            )
            if not selection.evidence_sufficient or not selected_evidence or not selected_citations:
                state["response"] = "未找到可靠依据，无法确认答案"
                state["citations"] = []
                logger.info(
                    "fast路径完成：session_id=%s model_calls=2 tool=%s evidence_sufficient=false",
                    state["session_id"],
                    task.tool,
                )
                return state
            state["citations"] = selected_citations

        response_started_at = time.perf_counter()
        try:
            final_response = llm_provider.chat_completion(
                _build_fast_result_messages(state, result, selected_evidence),
                tier="fast",
                timeout=min(config.FAST_LLM_TIMEOUT, _remaining_fast_budget(deadline)),
                total_budget=_remaining_fast_budget(deadline),
            )
            state["response"] = llm_provider.extract_text(final_response) or result.data
        except Exception as exc:
            state["error"] = "fast_final_generation_failed"
            state["citations"] = []
            state["response"] = (
                "（模型生成失败，以下为本地检索结果摘要）\n" + str(result.data or "")
            ).strip()
            logger.warning(
                "fast最终生成降级：session_id=%s tool=%s error_type=%s",
                state["session_id"],
                task.tool,
                type(exc).__name__,
            )
        observability.log_stage("fast_respond", int((time.perf_counter() - response_started_at) * 1000))
        logger.info(
            "fast路径完成：session_id=%s model_calls=%s tool=%s",
            state["session_id"],
            3 if task.tool == "search_documents" else 2,
            task.tool
        )
        return state
    except Exception as e:
        logger.error("fast路径失败：session_id=%s error_type=%s", state["session_id"], type(e).__name__)
        state["error"] = "fast_path_failed"
        state["response"] = "抱歉，快速模式暂时不可用，请稍后重试"
        state["citations"] = []
        return state


def _remaining_fast_budget(deadline: float) -> float:
    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        raise TimeoutError("fast request budget exhausted")
    return remaining


def _build_fast_messages(state: AgentState) -> list[dict]:
    fixed_prompt = system_modules.prompt_prefix(
        "你处于快速模式，只能基于对话上下文、长期记忆和本地企业知识库回答。"
        "需要查询知识库正文时调用search_documents；需要列出文件清单时调用list_documents。"
        "如果本轮提供了聊天附件，附件正文已经直接包含在上下文中，应优先阅读并回答附件内容，"
        "不要仅为读取当前附件调用search_documents或list_documents。用户没有附加文字时，直接概括附件的主要内容。"
        "其他问题直接回答，不调用工具。你没有联网搜索工具，不得声称已经查询互联网或获得实时结果。"
    )
    messages = cache_friendly_messages(fixed_prompt, [], include_date=True)
    messages.extend(_fast_history_messages(state["session_id"]))
    if state["attachment_context"]:
        messages.append({
            "role": "system",
            "content": "本轮聊天附件正文：\n" + "\n\n".join(state["attachment_context"])
        })
    memory_context = [
        item for item in state["context"]
        if item not in state["attachment_context"]
    ]
    if memory_context:
        messages.append({
            "role": "system",
            "content": "相关长期记忆：\n" + "\n".join(memory_context)
        })
    messages.append({"role": "user", "content": state["message"]})
    return messages


def _build_fast_evidence_messages(state: AgentState, result: ToolResult) -> list[dict]:
    fixed_prompt = system_modules.prompt_prefix(FAST_EVIDENCE_PROMPT)
    messages = cache_friendly_messages(fixed_prompt, [], include_date=True)
    messages.append({
        "role": "user",
        "content": "用户问题：%s\n\n候选片段：\n%s" % (state["message"], result.data),
    })
    return messages


def _build_fast_result_messages(
    state: AgentState,
    result: ToolResult,
    selected_evidence: str = "",
) -> list[dict]:
    instruction = FAST_DOCUMENT_GENERATION_PROMPT if result.tool == "search_documents" else (
        "你处于快速模式。请只根据提供的本地工具结果和对话上下文回答，"
        "不要编造工具结果中不存在的信息，不要声称使用了联网搜索。"
    )
    fixed_prompt = system_modules.prompt_prefix(instruction)
    messages = cache_friendly_messages(fixed_prompt, [], include_date=True)
    if result.tool == "search_documents":
        messages.append({
            "role": "user",
            "content": "用户问题：%s\n\n知识库片段：\n%s" % (
                state["message"],
                selected_evidence,
            ),
        })
        return messages

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


def _parse_fast_evidence_selection(response: object) -> FastEvidenceSelection:
    """Parse evidence selection; malformed output is treated as insufficient evidence."""
    try:
        payload = _parse_json_object(llm_provider.extract_text(response))
        return FastEvidenceSelection(**payload)
    except Exception as exc:
        logger.warning("fast文档证据选择解析失败：error_type=%s", type(exc).__name__)
        return FastEvidenceSelection()


def _select_fast_evidence(result: ToolResult, candidate_ids: list[int]) -> tuple[str, list[Citation]]:
    """Select numbered candidate blocks and matching citations without semantic hard-coding."""
    blocks = {
        int(match.group(1)): match.group(2).strip()
        for match in re.finditer(
            r"(?ms)^\[(\d+)\]\s*(.*?)(?=^\[\d+\]\s*|\Z)",
            str(result.data or ""),
        )
        if match.group(2).strip()
    }
    source_citations = _dedupe_citations(result.citations or [])
    selected_blocks = []
    selected = []
    seen = set()
    for candidate_id in candidate_ids:
        candidate_id = int(candidate_id)
        index = int(candidate_id) - 1
        if (
            candidate_id not in blocks
            or index < 0
            or index >= len(source_citations)
            or candidate_id in seen
        ):
            continue
        seen.add(candidate_id)
        selected_blocks.append("[%s] %s" % (candidate_id, blocks[candidate_id]))
        selected.append(source_citations[index])
    return "\n\n".join(selected_blocks), selected


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


def _generate_complex_tasks(
    state: AgentState,
    max_new_tasks: int,
    remaining_only: bool = False,
) -> list[Task]:
    if max_new_tasks <= 0:
        return []
    scope = "只规划尚未完成的剩余步骤" if remaining_only else "规划完成目标所需的全部步骤"
    response = llm_provider.chat_completion(
        cache_friendly_messages(
            "你是复杂任务规划器，负责生成线性、有顺序、可逐项执行的任务清单。"
            "只能使用search_web、search_documents、list_documents、llm_chat。"
            "每项格式为{\"tool\":工具名,\"params\":{...}}。"
            "search_web/search_documents使用query参数，llm_chat使用message参数，list_documents参数为空。"
            "任务必须最小、非冗余，通常2到4项足够：比较两个对象时通常每个对象各检索一次，"
            "不要按价值、局限、场景等比较维度重复搜索同一对象。最终比较和综合回答由后续汇总节点完成，"
            "不要为最终汇总额外生成llm_chat。只有用户明确要求查询本地文件清单时才使用list_documents。"
            "返回严格JSON：{\"tasks\":[...]}，不要解释。",
            [
            {
                "role": "system",
                "content": "%s，最多生成%d项。" % (scope, max_new_tasks),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "goal": state["message"],
                        "completed_results": _complex_results_payload(state["complex_task_results"]),
                        "remaining_tasks": _complex_tasks_payload(
                            state["complex_task_list"][state["current_task_pointer"]:]
                        ),
                    },
                    ensure_ascii=False,
                ),
            }],
            include_date=True,
        ),
        tier="expert",
        response_format={"type": "json_object"},
        timeout=min(config.EXPERT_LLM_TIMEOUT, _remaining_complex_budget(state)),
        total_budget=_remaining_complex_budget(state),
    )
    data = _parse_json_object(llm_provider.extract_text(response))
    raw_tasks = data.get("tasks") if isinstance(data.get("tasks"), list) else []
    if len(raw_tasks) > max_new_tasks:
        logger.warning(
            "复杂任务清单超限已截断：session_id=%s generated=%s limit=%s",
            state["session_id"],
            len(raw_tasks),
            max_new_tasks,
        )
    start_index = state["current_task_pointer"] if remaining_only else 0
    tasks = []
    for raw_task in raw_tasks[:max_new_tasks]:
        task = _normalize_complex_task(state, raw_task, start_index + len(tasks))
        if task is not None:
            tasks.append(task)
    return tasks


def _check_complex_route_with_model(state: AgentState) -> str:
    response = llm_provider.chat_completion(
        cache_friendly_messages(
            "判断剩余线性任务清单是否仍能达成原始目标。"
            "返回严格JSON：{\"action\":\"keep\"}或{\"action\":\"replan\"}。"
            "只有已完成结果（包括失败）使原路线明显不再成立时才replan，不要解释。",
            [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "goal": state["message"],
                        "completed_results": _complex_results_payload(state["complex_task_results"]),
                        "remaining_tasks": _complex_tasks_payload(
                            state["complex_task_list"][state["current_task_pointer"]:]
                        ),
                    },
                    ensure_ascii=False,
                ),
            }],
        ),
        tier="expert",
        response_format={"type": "json_object"},
        timeout=min(config.EXPERT_LLM_TIMEOUT, _remaining_complex_budget(state)),
        total_budget=_remaining_complex_budget(state),
    )
    data = _parse_json_object(llm_provider.extract_text(response))
    return "replan" if data.get("action") == "replan" else "keep"


def _adjust_complex_task_with_model(state: AgentState, task: Task) -> Optional[Task]:
    response = llm_provider.chat_completion(
        cache_friendly_messages(
            "判断下一个任务是否应根据已完成结果调整工具或参数。"
            "只能使用search_web、search_documents、list_documents、llm_chat。"
            "无需调整返回{\"action\":\"keep\"}；需要调整返回"
            "{\"action\":\"adjust\",\"task\":{\"tool\":...,\"params\":{...}}}。"
            "只返回严格JSON。",
            [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "goal": state["message"],
                        "completed_results": _complex_results_payload(state["complex_task_results"]),
                        "next_task": task.model_dump(),
                    },
                    ensure_ascii=False,
                ),
            }],
        ),
        tier="expert",
        response_format={"type": "json_object"},
        timeout=min(config.EXPERT_LLM_TIMEOUT, _remaining_complex_budget(state)),
        total_budget=_remaining_complex_budget(state),
    )
    data = _parse_json_object(llm_provider.extract_text(response))
    if data.get("action") != "adjust" or not isinstance(data.get("task"), dict):
        return None
    return _normalize_complex_task(state, data["task"], task.task_index)


def _normalize_complex_task(state: AgentState, raw_task: dict, task_index: int) -> Optional[Task]:
    if not isinstance(raw_task, dict):
        return None
    tool = str(raw_task.get("tool") or "").strip()
    if tool not in COMPLEX_TOOL_NAMES:
        return None
    raw_params = raw_task.get("params") if isinstance(raw_task.get("params"), dict) else {}
    query = str(raw_params.get("query") or raw_params.get("message") or state["message"]).strip()
    if tool == "search_web":
        params = {
            "query": query,
            "context": state["context"],
            "session_id": state["session_id"],
            "tier": "expert",
        }
    elif tool == "search_documents":
        params = {"query": query, "tier": "expert"}
    elif tool == "llm_chat":
        params = {"message": query, "session_id": state["session_id"], "tier": "expert"}
    else:
        params = {}
    return Task(tool=tool, params=params, order=task_index, task_index=task_index)


def _parse_json_object(raw: str) -> dict:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _summarize_complex_result(result: ToolResult) -> str:
    if result.status == "success":
        return str(result.data or "")[:2000]
    return ("执行失败：" + str(result.error_msg or "工具调用失败"))[:500]


def _complex_results_payload(results: list[ComplexTaskResult]) -> list[dict]:
    return [item.model_dump() for item in results]


def _complex_tasks_payload(tasks: list[Task]) -> list[dict]:
    return [
        {
            "task_index": item.task_index,
            "tool": item.tool,
            "status": item.status,
            "adjusted": item.adjusted,
        }
        for item in tasks
    ]


def _fallback_complex_response(results: list[ComplexTaskResult]) -> str:
    if not results:
        return "复杂任务未能生成可执行步骤，请稍后重试。"
    lines = ["复杂任务未能完成最终汇总，以下为已执行步骤摘要："]
    for item in results:
        lines.append("%s. [%s] %s" % (item.task_index + 1, item.status, item.result_summary))
    return "\n".join(lines)


def _remaining_complex_budget(state: AgentState) -> float:
    deadline = float(state.get("complex_deadline") or 0.0)
    if deadline <= 0:
        return config.EXPERT_COMPLEX_TIMEOUT
    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        raise TimeoutError("expert complex task budget exhausted")
    return remaining


def _complex_budget_exhausted(state: AgentState) -> bool:
    deadline = float(state.get("complex_deadline") or 0.0)
    return deadline > 0 and time.perf_counter() >= deadline


def _mark_complex_timeout(state: AgentState) -> AgentState:
    state["error"] = "complex_task_timeout"
    state["complex_action"] = "respond"
    return state


def _complex_timeout_response(results: list[ComplexTaskResult]) -> str:
    prefix = "复杂任务已达到全局时间上限，以下为超时前已完成的步骤摘要："
    if not results:
        return prefix + "\n尚无已完成步骤。"
    lines = [prefix]
    for item in results:
        lines.append("%s. [%s] %s" % (item.task_index + 1, item.status, item.result_summary))
    return "\n".join(lines)


def _consecutive_complex_failures(results: list[ComplexTaskResult]) -> int:
    count = 0
    for item in reversed(results):
        if item.status != "error":
            break
        count += 1
    return count


def _append_layer_trace(state: AgentState, node_name: str) -> None:
    if node_name not in state["layer_trace"]:
        state["layer_trace"].append(node_name)


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

    reflection = _reflect_with_model(state)
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
    if state["intent"] in {
        "chat", "search", "document_list", "generate_file", "convert_document"
    }:
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
    if metadata.get("supplied_context_answer"):
        return True
    if not metadata.get("title_source_match"):
        return False
    candidate_count = int(metadata.get("candidate_count", 0) or 0)
    trusted_count = int(metadata.get("trusted_count", 0) or 0)
    return candidate_count <= 3 and trusted_count <= 3


def _reflect_with_model(state: AgentState) -> dict:
    """Ask the selected model whether current tool results are enough."""
    messages = cache_friendly_messages(
        "你是轻量ReAct反思调度器，只判断当前工具结果是否足够回答用户问题。"
        "如果足够，返回JSON：{\"action\":\"respond\"}。"
        "如果不够，且需要再调用一次工具，返回JSON："
        "{\"action\":\"continue\",\"tool\":\"search_web|search_documents|llm_chat\",\"query\":\"下一轮查询或消息\"}。"
        "只能选择search_web、search_documents、llm_chat三个工具。"
        "判断时可以参考：文档citations是否为空或分数不足、搜索结果是否与问题相关、是否需要用另一类信息交叉验证。"
        "不要重复调用历史里已经用过的同一工具和同一参数。只返回JSON，不要解释。",
        [
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
        }],
        include_date=True,
    )
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
    if state["intent"] == "convert_document":
        return Task(
            tool="convert_document",
            params={
                "attachment_id": (
                    state["attachment_ids"][0]
                    if len(state["attachment_ids"]) == 1
                    else ""
                ),
                "target_format": state["conversion_target_format"],
                "session_id": state["session_id"],
                "owner_user_id": state["owner_user_id"],
            },
            order=order,
        )
    if state["intent"] == "generate_file":
        context_text = "\n".join(state["context"] or [])
        system_prompt = (
            "你负责生成可直接保存为文件的完整Markdown正文。只输出正文，不要解释生成过程，"
            "不要添加下载链接或本地路径。根据用户要求组织清晰结构；如果提供了历史或检索上下文，"
            "只使用相关内容，不得编造。可使用Markdown标题、加粗和列表组织内容，即使目标格式是PDF或DOCX也先输出Markdown。"
        )
        if context_text:
            system_prompt += "\n\n可用上下文：\n" + context_text
        return Task(
            tool="llm_chat",
            params={
                "message": state["message"],
                "session_id": state["session_id"],
                "tier": "expert",
                "system_prompt": system_prompt,
            },
            order=order,
        )
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
                "tier": state["mode"],
                "context": state["attachment_context"],
            },
            order=order
        )
    if state["attachment_context"]:
        return Task(
            tool="llm_chat",
            params={
                "message": state["message"],
                "session_id": state["session_id"],
                "tier": state["mode"],
                "system_prompt": (
                    "请优先根据本轮聊天附件正文回答。用户没有附加文字时，概括附件主要内容；"
                    "不得把当前附件误当成企业知识库文件清单，也不得编造附件中没有的信息。"
                    "\n\n本轮聊天附件正文：\n"
                    + "\n\n".join(state["attachment_context"])
                ),
            },
            order=order,
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


def _save_generated_content(state: AgentState, content: str) -> ToolResult:
    """Persist generated body through the registered execution tool."""
    return mcp_client.call_tool(
        "generate_file",
        {
            "content": str(content or ""),
            "session_id": state["session_id"],
            "owner_user_id": state["owner_user_id"],
            "filename_hint": state["filename_hint"],
            "output_format": state["output_format"],
        },
    )


def _respond_with_generated_file(state: AgentState) -> None:
    """Build a relative download response without exposing server filesystem paths."""
    if not state["results"]:
        state["error"] = state["error"] or "generate_file_failed"
        state["response"] = "文件生成失败，请稍后重试。"
        state["citations"] = []
        return
    result = state["results"][-1]
    metadata = result.metadata or {}
    if result.tool != "generate_file" or result.status != "success":
        state["error"] = state["error"] or result.error_msg or "generate_file_failed"
        state["response"] = "文件生成失败，请稍后重试。"
        state["citations"] = []
        return
    file_id = str(metadata.get("file_id", ""))
    download_filename = str(metadata.get("download_filename", ""))
    if not file_id or not download_filename:
        state["error"] = "generate_file_result_invalid"
        state["response"] = "文件生成失败，请稍后重试。"
        state["citations"] = []
        return
    relative_path = "/files/%s" % file_id
    requested_format = str(metadata.get("requested_format", "") or "")
    delivered_format = str(metadata.get("delivered_format", "") or "")
    prefix = ""
    if requested_format and delivered_format and requested_format != delivered_format:
        prefix = "目标格式转换失败，已降级交付Markdown文件。\n"
    state["response"] = "%s文件已生成：%s\n下载地址：%s" % (
        prefix,
        download_filename,
        relative_path,
    )
    state["citations"] = []


def _respond_with_converted_file(state: AgentState) -> None:
    """将附件转换结果映射为明确、可操作的用户响应。"""
    if state["clarification"]:
        state["response"] = state["clarification"]
        state["citations"] = []
        return
    result = state["results"][-1] if state["results"] else None
    metadata = result.metadata if result and result.metadata else {}
    if result and result.status == "success":
        file_id = str(metadata.get("file_id", "") or "")
        download_filename = str(metadata.get("download_filename", "") or "")
        if file_id and download_filename:
            state["response"] = "已生成 %s，可通过 /files/%s 下载" % (
                download_filename,
                file_id,
            )
            state["error"] = ""
            state["citations"] = []
            return
    error_type = str(
        metadata.get("error_type", "")
        or (result.error_msg if result else "")
        or "conversion_failed"
    )
    messages = {
        "unsupported_conversion": "不支持将该附件转换为所选格式。",
        "timeout": "附件转换超时，请稍后重试。",
        "attachment_not_found": "附件已过期或不存在，请重新上传。",
        "file_not_found": "附件原始文件不存在，请重新上传。",
        "forbidden": "无权转换该附件。",
        "session_mismatch": "该附件不属于当前会话，无法转换。",
    }
    state["error"] = error_type
    state["response"] = messages.get(error_type, "附件转换失败，请稍后重试。")
    state["citations"] = []


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
                "tier": state["mode"],
                "context": state["attachment_context"],
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

def _classify_with_model(
    message: str,
    context: list[str] = None,
    tier: str = "fast",
    attachment_ids: Optional[list[str]] = None,
) -> dict:
    """使用所选模型的 Function Call 选择搜索或直接回答。"""
    context_text = "\n".join(context or [])
    fixed_system_prompt = (
                    "你只负责一次性完成工具选择、澄清判断和城市提取。"
                    "选择工具时必须在该工具的reasoning参数中用一句话说明依据，控制在60字以内。"
                    "每次必须调用一个且仅一个主意图工具：declare_complex_task、search_web、search_documents、list_documents、generate_file、convert_document、direct_answer、ask_clarification。"
                    "如果请求必须顺序完成多个独立检索、比较、分析或操作，且单一工具无法覆盖完整目标，调用declare_complex_task；"
                    "例如分别检索两个主题后比较、先查企业文档再查外部资料并汇总。简单单问、单次搜索、单份文档查询或普通对话不要声明复杂任务。"
                    "强制few-shot：‘分别搜索A和B两个话题并对比’=>declare_complex_task；"
                    "‘先查A的最新情况，再结合B给出建议’=>declare_complex_task；"
                    "‘搜索A的最新消息’=>search_web。复杂请求禁止选择direct_answer或单次search_web。"
                    "当多个检索对象和比较/汇总目标已经明确时，问题就是完整的；不要因为‘近期’‘最新’"
                    "没有指定精确日期范围而ask_clarification，应结合当前日期直接declare_complex_task。"
                    "如果用户明确提供自己的当前城市或所在地，优先写入主意图工具的city参数；模型能力支持多个工具时，可额外同时调用save_city。"
                    "save_city是附加工具，禁止单独调用；如果需要保存城市，也必须同时选择一个主意图工具，或将city写入主意图工具参数。"
                    "如果模型能力限制导致一次只能调用一个工具，禁止调用save_city，必须优先调用主意图工具并在city参数中带出城市。"
                    "需要实时信息或外部事实时选search_web；无需联网时选direct_answer；"
                    "用户明确要求把内容整理、导出或生成为可下载文件、文档、清单或报告时选generate_file；"
                    "generate_file用于生成新的md、txt、pdf或docx交付物，不用于读取或转换用户已有文件；"
                    "用户明确要求把本轮已上传附件在PDF、Word、Excel、PPT之间转换时选convert_document；支持PDF转DOCX/XLSX/PPTX以及DOC/DOCX/XLS/XLSX/PPT/PPTX转PDF；附件缺失或数量不唯一时仍选convert_document，由系统负责提示，不要改选ask_clarification或猜测附件；"
                    "本轮attachment_ids非空表示用户已提供当前聊天附件，附件正文会由系统直接注入后续回答上下文；"
                    "读取、概括、总结、分析当前附件时必须选direct_answer，不要选search_documents或list_documents；"
                    "用户消息为空但attachment_ids非空时也选direct_answer，由后续节点直接概括附件；只有明确要求转换格式时选convert_document；"
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
    )
    fixed_system_prompt = system_modules.prompt_prefix(fixed_system_prompt)
    response = llm_provider.chat_completion(
        messages=cache_friendly_messages(
            fixed_system_prompt,
            [
            {
                "role": "system",
                "content": f"可用历史上下文：\n{context_text or '无'}",
            },
            {
                "role": "system",
                "content": "本轮attachment_ids：%s" % json.dumps(
                    list(attachment_ids or []),
                    ensure_ascii=False,
                ),
            },
            {
                "role": "user",
                "content": message
            }],
            include_date=True,
        ),
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
    """在有长期记忆上下文时，让所选模型生成最终回复。"""
    context_text = "\n".join(state["context"])
    messages = cache_friendly_messages(
        system_modules.prompt_prefix(
            "如果历史记录与当前问题不相关，请忽略，不要主动引入无关信息。"
        ),
        [
        {
            "role": "system",
            "content": f"以下是与当前问题相关的历史记录，供参考：\n{context_text}",
        },
        {
            "role": "user",
            "content": (
                f"用户当前问题：{state['message']}\n\n"
                f"执行层初步回复：{base_response}\n\n"
                "请结合历史记录和初步回复，生成自然、准确的最终回答。"
            )
        }],
        include_date=True,
    )
    try:
        started_at = time.perf_counter()
        response = llm_provider.extract_text(
            llm_provider.chat_completion(messages, tier=state["mode"])
        )
        observability.log_stage("respond_context_model", int((time.perf_counter() - started_at) * 1000))
        return response
    except Exception:
        return base_response


def _extract_tool_calls(response) -> list[dict]:
    """从 OpenAI 兼容 Function Call 响应中提取工具名和参数。"""
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
    decision = {
        "intent": "chat",
        "clarification": "",
        "city": "",
        "filename_hint": "",
        "output_format": "md",
        "conversion_target_format": "",
        "decision_reasoning": DECISION_REASONING_FALLBACK,
    }
    complex_call = next(
        (item for item in tool_calls if item.get("name") == "declare_complex_task"),
        None,
    )
    if complex_call:
        decision["intent"] = "complex_task"
        decision["decision_reasoning"] = _normalize_decision_reasoning(
            complex_call.get("arguments", {}).get("reasoning")
        )
        return decision
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
            decision["decision_reasoning"] = _normalize_decision_reasoning(
                arguments.get("reasoning")
            )
            continue
        if name == "search_web" and decision["intent"] != "clarify":
            decision["intent"] = "search"
            decision["clarification"] = ""
            decision["decision_reasoning"] = _normalize_decision_reasoning(
                arguments.get("reasoning")
            )
            continue
        if name == "search_documents" and decision["intent"] != "clarify":
            decision["intent"] = "document"
            decision["clarification"] = ""
            decision["decision_reasoning"] = _normalize_decision_reasoning(
                arguments.get("reasoning")
            )
            continue
        if name == "list_documents" and decision["intent"] != "clarify":
            decision["intent"] = "document_list"
            decision["clarification"] = ""
            decision["decision_reasoning"] = _normalize_decision_reasoning(
                arguments.get("reasoning")
            )
            continue
        if name == "generate_file" and decision["intent"] != "clarify":
            decision["intent"] = "generate_file"
            decision["filename_hint"] = str(arguments.get("filename_hint", "") or "")
            requested_format = str(arguments.get("output_format", "md") or "md").lower()
            decision["output_format"] = (
                requested_format
                if requested_format in {"md", "txt", "pdf", "docx"}
                else "md"
            )
            decision["clarification"] = ""
            decision["decision_reasoning"] = _normalize_decision_reasoning(
                arguments.get("reasoning")
            )
            continue
        if name == "convert_document" and decision["intent"] != "clarify":
            decision["intent"] = "convert_document"
            requested_target = str(
                arguments.get("target_format", "") or ""
            ).lower()
            decision["conversion_target_format"] = (
                requested_target
                if requested_target in {"pdf", "docx", "xlsx", "pptx"}
                else ""
            )
            decision["clarification"] = ""
            decision["decision_reasoning"] = _normalize_decision_reasoning(
                arguments.get("reasoning")
            )
            continue
        if name == "direct_answer" and decision["intent"] not in {
            "search", "document", "document_list", "generate_file",
            "convert_document", "clarify"
        }:
            decision["intent"] = "chat"
            decision["decision_reasoning"] = _normalize_decision_reasoning(
                arguments.get("reasoning")
            )
    if has_save_city and decision["intent"] == "chat" and len(tool_calls) == 1:
        decision["intent"] = "search"
    return decision


def _normalize_decision_reasoning(value) -> str:
    reasoning = str(value or "").strip()
    if not reasoning:
        return DECISION_REASONING_FALLBACK
    return reasoning[:60]


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


builder = StateGraph(AgentState)
builder.add_node("classify", classify_node)
builder.add_node("retrieve", retrieve_node)
builder.add_node("plan", plan_node)
builder.add_node("execute", execute_node)
builder.add_node("reflect", reflect_node)
builder.add_node("respond", respond_node)
builder.add_node("complex_plan", complex_plan_node)
builder.add_node("execute_complex", execute_complex_node)
builder.add_node("checkpoint", checkpoint_node)
builder.add_node("complex_respond", complex_respond_node)
builder.set_entry_point("classify")
builder.add_conditional_edges(
    "classify",
    lambda state: (
        "clarify"
        if state["intent"] == "clarify"
        or (state["intent"] == "convert_document" and state["clarification"])
        else "complex"
        if state["intent"] == "complex_task"
        else "continue"
    ),
    {
        "clarify": "respond",
        "complex": "complex_plan",
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
builder.add_conditional_edges(
    "complex_plan",
    lambda state: "execute" if state["complex_action"] == "execute" else "respond",
    {"execute": "execute_complex", "respond": "complex_respond"},
)
builder.add_edge("execute_complex", "checkpoint")
builder.add_conditional_edges(
    "checkpoint",
    lambda state: state["complex_action"],
    {
        "checkpoint": "checkpoint",
        "execute": "execute_complex",
        "respond": "complex_respond",
    },
)
builder.add_edge("complex_respond", END)
graph = builder.compile()



