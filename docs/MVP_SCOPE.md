# Flyweave MVP Scope

## Goal

在一个足够薄、但足够真实的企业售后场景中，证明 Agent 可以在**知识、业务数据、工具、人工审批和审计约束**下完成从问题理解到业务执行的闭环。

MVP 的目标不是构建通用 Agent 平台，而是做出一条可演示、可解释、可追踪的企业级 vertical slice。

## Golden Path

首条业务链固定为：

```text
客户提交售后工单
→ Agent 理解问题
→ 检索售后政策
→ 查询订单
→ 查询库存
→ 形成处理建议
→ 风险判断
→ 必要时进入人工审批
→ 创建换货单
→ 写回工单
→ 记录 Audit / Eval
```

### 首个 Demo Case

客户：

> 我的耳机右耳没有声音，7 月 12 日购买，可以换货吗？

预期系统行为：

- 识别 `quality_issue / replacement`；
- 获取订单购买时间、商品、状态；
- 检索对应售后政策；
- 查询可换货库存；
- 依据结构化规则形成建议；
- 当金额、置信度或规则触发风险条件时，要求人工审批；
- 审批通过后执行 `create_replacement`；
- 将处理结果写回工单；
- 保存一次完整 Agent Run。

## In Scope

### Product

- Service Operations dashboard
- Ticket detail
- Agent Run timeline
- Approval Inbox
- Basic knowledge/policy view

### Agent

- Intent classification
- Knowledge retrieval
- Order lookup tool
- Inventory lookup tool
- Replacement decision
- Risk gate
- Human approval
- Replacement execution tool
- Ticket write-back

### Reliability

- Structured outputs
- Tool input validation
- Timeout / retry baseline
- Low-confidence escalation
- Audit trail
- Basic eval cases
- Run-level latency / token / cost metadata when available

### Data

全部使用 demo/simulated data：

- customers
- tickets
- orders
- order_items
- inventory
- policies
- approvals
- agent_runs
- tool_calls
- audit_events

## Explicitly Out of Scope

MVP 阶段不做：

- 通用 Agent Builder
- 多 Agent 自主协作平台
- 自研模型网关
- 自研 MCP Gateway
- 自研向量数据库
- 自研 tracing / eval 平台
- K8s / 微服务拆分
- 多租户计费系统
- 复杂组织权限中心
- 全渠道客服
- 语音客服
- 自动退款 / 自动打款等高风险真实支付动作
- Fine-tuning / RLHF / 模型训练

如果某项能力不是完成 Golden Path 的必要条件，默认不进入 MVP。

## Success Criteria

MVP 完成必须同时满足：

1. 用户可以从 UI 创建或选择一个 demo 售后工单；
2. Agent 能基于政策和订单数据给出有依据的处理路径；
3. Agent 至少真实调用两个业务 Tool；
4. 一个风险分支必须进入人工审批；
5. 审批通过后能继续恢复执行，而不是重新开始任务；
6. 一次 Run 的关键步骤、输入输出、Tool Call、审批和最终结果可查看；
7. 至少存在一组自动 eval case，用于验证规则判断或 Tool 选择；
8. README 中可以用 30 秒说明业务价值，用 5 分钟展开技术设计。

## Product Metrics (Demo)

仅用于 demo / simulation，并明确标识：

- automation rate
- human handoff rate
- average handling time
- success / failure rate
- average cost per case
- policy-grounded answer rate
- tool-call success rate

## Scope Rule

> **先把一件事办完，再讨论平台化。**

任何新增需求都先回答：

> 它是否直接提高 Golden Path 的完整性、可靠性或展示价值？

如果答案是否定的，放入 backlog，不进入 MVP。
