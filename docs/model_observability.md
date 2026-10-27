# 模型耗时、用量与缓存观测

本观测仅位于单进程内存，重启清零；不接外部监控、不上传遥测、不新增持久库，也不计算实际费用。通过现有 `GET /reviewer/metrics` 读取，需要审核员或开发者凭据，客户无权读取；没有新增页面或客户接口。

## 统计入口与维度

原 `requests`、`model_calls`、阶段耗时等字段及计数口径保留。新指标独立放在 `llm_observations`；最近100请求的每条记录也增加同名子项。旧 `model_calls` 不是新 SDK 尝试数，旧流式耗时仍是建流返回时的旧口径，不改成消费全程。

```json
{
  "llm_observations": {
    "totals": {
      "logical_calls": 1,
      "sdk_attempts": 2,
      "logical_finished": 1,
      "sdk_finished": 2,
      "logical_statuses": {"success": 1, "timeout": 0},
      "sdk_statuses": {"success": 1, "timeout": 1}
    },
    "by_scope_tier_stage": [],
    "recent_background": [],
    "retention": {"request_detail_limit": 64, "recent_requests": 100, "recent_background": 100}
  }
}
```

示例省略了零值状态及其他字段，不是完整响应。实际状态为 `success`、`error`、`timeout`、`cancelled`、`closed`、`validation_error`，运行中的明细为 `active`。

- `logical_calls`：每次统一 `chat_completion` 入口一次，包括发送前校验失败或取消。这两类可能有0次 SDK 尝试，以终态区分。
- `sdk_attempts`：实际进入 `client.chat.completions.create` 的次数。原超时重试和发送前连接重置重试分别增加尝试数。**它不是已确认计费次数**；发送、供应商接收和收费无法由本地计数证明。
- `sdk_late_attempts` / `sdk_late_finished`：逻辑调用已经终结之后才实际进入SDK的尝试及其完成数，是前述SDK计数的子集。仅记全局及固定维度桶，不回写已终结的逻辑明细或冻结前台。因而某前台快照可以是logical timeout、SDK0，稍后全局出现晚到SDK1，二者采样范围不同。
- `logical_finished` / `sdk_finished`：已经终结的数量，活动调用不进入用量覆盖数。同一调用／尝试只结算一次，重复close、晚到结果不会重复结算。
- `by_scope_tier_stage`：仅固定维度。tier为fast、expert或other；stage来自现有声明的有限集合，缺省为unknown，其他值为other，不把自由文本变成标签。
- trace关联只在有界最近请求／后台记录中保留；全局不建立trace历史索引或去重集合。

## 耗时

使用私有单调时钟，单位毫秒。`timings` 每项含 `samples` 和 `total_ms`，没有样本时二者为0，不等同实际测得零耗时。

| 字段 | 范围 |
|---|---|
| logical_ms | 统一调用入口到逻辑终态，含原重试与准备 |
| sdk_attempt_ms | 每次SDK create开始到返回／异常；流式到消费或关闭终态 |
| nonstream_completed_ms | 成功的非流式调用，从逻辑入口到完成 |
| stream_open_ms | SDK create开始到成功建流，不代表流完成 |
| stream_total_ms | 逻辑入口到流耗尽、异常、取消或提前关闭 |

非流式墙钟工作线程返回后，尝试计时使用已保存的SDK返回耗时；前台墙钟超时先行结算时，已登记的未完成尝试随逻辑调用标timeout，晚到结果不覆写该状态。若工作线程此前暂停在方法查找等窗口、实际SDK进入发生在逻辑终结之后，则独立登记晚到尝试并按其实际返回或异常终结，逻辑timeout及前台快照不变。所有时限、原重试数量、模型及取消规则不变，不阻止原路径仍会发生的SDK调用。

并行调用的耗时可以重叠；`total_ms`相加不是请求墙钟耗时。请求墙钟仍读既有 `total_elapsed_ms`，不以各模型调用之和替代。

## 用量：未知不等于0

`usage`统计逻辑终态；`attempt_usage`统计SDK尝试终态，两者不能相加充当总消耗。失败尝试通常没有完整用量，因此重试后的成功可能表现为：逻辑用量有效1份，尝试用量有效1份、缺失1份。

字段均严格只接受非负整数，bool不是整数：

`prompt_tokens`、`completion_tokens`、`total_tokens`、`prompt_cache_hit_tokens`、`prompt_cache_miss_tokens`。

SDK属性对象和字典均支持；缺失、None、错类型、负数为null（未知），明确0保留为已知0。既有评测辅助函数 `extract_cache_usage` 仍保留原缺省0接口；运行时使用独立严格解析器，不能拿评测缺省0代替运行时未知。

每个字段聚合为：

```json
{"known_sum": 12, "valid_samples": 2, "missing_samples": 1}
```

这只表示有效2份的和为12，不能声称覆盖全部3份。缺字段不补总量，不用prompt+completion凭空生成total。total与已知分项明显矛盾时，total变为未知；缓存计数与prompt矛盾时，缓存计数变为未知，并增加 `anomaly_samples`，不影响回答。

非流式按完整响应结算，响应成功但未提供usage时字段仍全未知。流式`complete_samples`表示已确认最终usage；没有确认时计incomplete_samples，包括中间累计usage后自然EOF。流式业务终态与用量确认独立：最终usage确认后再close、取消或抛错，保留最终用量及逐字段缺失，状态仍为closed、cancelled或error。complete不表示五个字段都有效，具体完整程度必须看各字段覆盖数。

## 缓存

`usage.cache`和`attempt_usage.cache`分别报告：

- `valid_samples`：hit/miss均有效、分母>0的终结样本数。
- `missing_samples`：hit或miss未知的样本数，包括未确认最终usage的流。
- `zero_denominator_samples`：明确hit=miss=0，已知但不能计算比例。
- `hit_tokens`、`miss_tokens`：只在有效正分母样本范围相加。
- `hit_ratio`：上述hit之和除以hit+miss之和，按token加权，不平均各调用百分比。没有有效分母为null。

流式在正文过滤前确认最终usage：usage是字典或带已知用量属性的对象，且choices=[]（usage-only），或非空choices的每项finish_reason均是当前SDK声明的stop、length、tool_calls、content_filter、function_call之一。缺usage、choices形状不明、无明确结束标志都不确认；StopIteration不充当终态证明。

首个确认的最终usage固定保留，不逐块累加；重复最终块不重复记账，后续中间块也不覆盖它。未确认时，EOF、异常、关闭或取消均不把中间累计值升级为最终值；确认后即使随后关闭、取消或异常也不清空。不会补发请求、继续读取取消流或查询其他接口。

## scope、后台与生命周期

- foreground：请求绑定上下文内的前台调用。
- background：既有后台任务，或memory_importance阶段；有明确来源时记录来源trace，无来源时trace_id=null（unattributed background），不伪造关联。
- unattributed：没有请求／后台绑定的独立调用。

前台结束时冻结最近请求快照；后台计数、用量及耗时独立进入全局background维度与 `recent_background`，不改变已完成前台记录。`main.py`仅给原有六处后台任务登记传递观测trace，没有改变创建条件、函数数量或业务参数。线程池／墙钟线程使用原有上下文传播，流持有其创建时的观测句柄，不使用消费线程当前trace重新归账。

请求活动状态释放后，晚到阶段日志不重新创建 `_active_requests`。观测自身解析或记录异常采用best effort，不替换业务结果／原异常、不触发重试；BaseException类取消不吞掉。观测不可用时新增容器可为null，不要把它当成实际零调用。

观测自身故障增加固定 `recording_failures` 计数，并打印 `code=llm_observation_failed`，不打印异常原文或输入。日志处理器本身失败时不覆盖业务结果，BaseException取消仍不吞掉。

## 有界保留与隐私

- 前台最近请求100条，每请求最多64条逻辑调用明细；超过仍计数、标 `details_truncated=true`。
- 后台最近任务窗口100条，每窗口最多64条明细；与前台分账，背景窗口不形成无限trace索引。
- 全局桶的最大数量为3个scope × 3个tier × 23个stage = 207。只有计数、耗时、严格整数用量，不留历次SDK尝试列表。
- 活动调用句柄只保存固定字段与至多一个当前尝试；SDK响应与完整流仅由原业务消费链临时持有，不进入指标或历史观测容器。
- 不保存请求／回答正文、推理内容、工具参数、用户标识、Key或派生指纹、URL、异常原文、SDK响应或完整流。trace_id是内部请求编号，不是账号标识。

统计是单进程视图，重启清零，不支持跨进程持久汇总；没有实际费用计算或缓存优化策略。观察结果仍需结合覆盖率和缺失数解释。
