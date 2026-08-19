# Flyweave MVP Roadmap

## Delivery Strategy

目标：用最短路径做出一个**能演示、能讲清、能被企业理解**的垂直业务闭环。

不按“先把所有技术学会”推进，而按“每周增加一个可展示证据”推进。

## Phase 0 — Scope Lock

- [x] Repository created
- [x] README v1
- [x] MVP scope locked
- [x] Architecture boundaries defined
- [ ] Initial issues created

Exit criteria:

- 所有人看 README 都能知道项目不是普通 AI 客服；
- MVP 只有一条 Golden Path；
- 明确哪些能力不做。

## Phase 1 — Product Shell

目标：先让产品“长出来”。

- Vue 3 + TypeScript app shell
- Service Operations dashboard
- Ticket list / detail
- Agent Runs list / timeline placeholder
- Approval Inbox placeholder
- seeded demo data

Exit criteria:

- 不接模型也能完整演示业务对象与页面结构；
- Demo 数据明确标注 simulation；
- UI 已具备企业工作台观感。

## Phase 2 — End-to-End Happy Path

目标：完成第一条真实 Agent Run。

- FastAPI service
- Run state model
- intent classification
- policy retrieval baseline
- `get_order`
- `check_inventory`
- constrained decision
- `create_replacement`
- `update_ticket`

Exit criteria:

- 一张工单可以从创建一路走到“换货单创建 + 工单写回”；
- 至少两个 Tool 是真实代码路径，不是模型伪装执行；
- Run timeline 能看到每一步。

## Phase 3 — Human Control & Reliability

目标：从 Demo Agent 变成企业可讨论的 Agent。

- risk gate
- human approval
- pause / resume
- structured outputs
- timeout / retry
- recoverable tool errors
- audit events

Exit criteria:

- 风险案例能够暂停；
- 人工批准后从原 Run 恢复，而不是重新跑；
- 拒绝动作可被记录并终止；
- Tool 失败存在可观察状态。

## Phase 4 — RAG / Context Quality

目标：让判断有依据，而不是靠模型记忆。

- policy documents
- chunking strategy
- retrieval
- citations / source metadata
- minimal context assembly
- retrieval eval cases

Exit criteria:

- 处理建议能指出依据的政策；
- 错误或缺失政策能被测试出来；
- Context 不依赖手工硬编码整篇文档。

## Phase 5 — Eval / Tracing / Cost

目标：证明不仅能跑，还知道跑得怎么样。

- Agent Run tracing
- eval dataset
- intent eval
- tool selection eval
- policy-grounding eval
- unsafe-action-blocked eval
- latency / token / cost metadata

Exit criteria:

- 至少一组可重复运行的 eval cases；
- Dashboard 可展示 demo 指标；
- 能在面试中解释一次失败 Run 为什么失败。

## Phase 6 — Demo Polish

目标：成为求职作品，而不是个人实验仓库。

- polished README
- architecture diagram
- product screenshots / GIF
- one-click or documented local setup
- seeded demo scenario
- case study
- technical decisions / trade-offs
- limitations

Exit criteria:

- HR 30 秒能理解；
- 技术面 5 分钟能展开；
- 架构面能继续追问 reliability / permission / eval；
- 不夸大 demo 数据为生产效果。

## Non-goals Before MVP Complete

以下事项全部排在 MVP 之后：

- 多 Agent；
- 通用 workflow designer；
- MCP gateway；
- model router platform；
- multi-tenant SaaS；
- Kubernetes；
- 自研 eval / observability framework；
- 重型权限中心；
- 更多行业场景。

> **MVP 完成之前，增加抽象不是进度，跑通闭环才是进度。**
