# -*- coding: utf-8 -*-
"""F31升级验证：checkpoint节点自指向边在新版调度器下的运行时行为。

分两层验证：
1. 项目状态机语义——直接驱动checkpoint_node两轮，确认"全局重规划只用一次、
   第二轮转入执行"这一约束在升级后不变；
2. 库调度语义——用最小可复现图还原planning.py里那条自指向条件边
   （add_conditional_edges(节点, path, {..., "自身": "自身"})），确认新版
   调度器接受该结构且循环次数精确。

为什么第2层不直接patch项目的图：planning.py在模块级执行
`graph = builder.compile()`，节点函数在import时就绑定进已编译图，之后
monkeypatch模块属性不会影响它（这与langgraph版本无关，0.1.1同理）。
因此改用等价的最小图隔离验证库行为本身。
"""

from typing import TypedDict

from langgraph.graph import END, StateGraph

from layers import planning


def _prepared_state():
    state = planning._new_agent_state("selfloop-session", "分别搜索A和B并对比", "expert")
    state["intent"] = "complex_task"
    state["is_complex_task"] = True
    return state


def _task(index, tool="search_web", query="topic"):
    return planning._normalize_complex_task(
        _prepared_state(), {"tool": tool, "params": {"query": query}}, index
    )


def test_checkpoint_state_machine_allows_one_global_replan(monkeypatch):
    """第一轮replan后请求自环，第二轮不得再次replan，须转入执行。"""
    calls = {"route": 0}

    def fake_route(state):
        calls["route"] += 1
        return "replan"

    monkeypatch.setattr(planning, "_check_complex_route_with_model", fake_route)
    monkeypatch.setattr(
        planning,
        "_generate_complex_tasks",
        lambda state, limit, remaining_only=False: [_task(1, query="new")],
    )
    monkeypatch.setattr(
        planning, "_adjust_complex_task_with_model", lambda state, task: task
    )

    state = _prepared_state()
    state["complex_task_list"] = [_task(0, query="done"), _task(1, query="old")]
    state["current_task_pointer"] = 1
    state["complex_task_created_count"] = 2

    planning.checkpoint_node(state)
    assert state["complex_action"] == "checkpoint", "第一轮应请求自环"
    assert state["full_replan_used"] is True

    planning.checkpoint_node(state)
    assert state["complex_action"] == "execute", "第二轮应转入执行而非再次replan"
    assert calls["route"] == 1, "全局重规划机会只应使用一次"


class _LoopState(TypedDict):
    action: str
    visits: int


def test_self_referencing_conditional_edge_loops_exactly(monkeypatch):
    """最小可复现图：条件边把节点映射回自身，验证调度器精确循环。

    结构与planning.py的checkpoint边一致：
      add_conditional_edges("checkpoint", lambda s: s["action"],
                            {"checkpoint": "checkpoint", "respond": "respond"})
    """
    def checkpoint(state: _LoopState) -> _LoopState:
        state["visits"] += 1
        # 前两次要求自环，第三次收敛
        state["action"] = "checkpoint" if state["visits"] < 3 else "respond"
        return state

    def respond(state: _LoopState) -> _LoopState:
        return state

    builder = StateGraph(_LoopState)
    builder.add_node("checkpoint", checkpoint)
    builder.add_node("respond", respond)
    builder.set_entry_point("checkpoint")
    builder.add_conditional_edges(
        "checkpoint",
        lambda state: state["action"],
        {"checkpoint": "checkpoint", "respond": "respond"},
    )
    builder.add_edge("respond", END)
    # 与项目一致：compile()不传checkpointer
    compiled = builder.compile()

    result = compiled.invoke({"action": "checkpoint", "visits": 0})

    assert result["visits"] == 3, "自环应恰好执行3次，实际=%s" % result["visits"]
    assert result["action"] == "respond"


def test_compile_without_checkpointer_still_works():
    """确认新版compile()不强制要求checkpointer（评估阶段的静态结论需运行时确认）。"""
    def only(state: _LoopState) -> _LoopState:
        state["visits"] += 1
        return state

    builder = StateGraph(_LoopState)
    builder.add_node("only", only)
    builder.set_entry_point("only")
    builder.add_edge("only", END)

    compiled = builder.compile()
    result = compiled.invoke({"action": "", "visits": 0})
    assert result["visits"] == 1
