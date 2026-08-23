<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  fetchTicketDetail,
  fetchTickets,
  type TicketOrderContext,
  type TicketRecord,
} from '../lib/ticketsApi'

type LoadState = 'loading' | 'success' | 'empty' | 'failed'

const loadState = ref<LoadState>('loading')

interface ApprovalCandidate {
  ticket: TicketRecord
  order: TicketOrderContext | null
}

const candidates = ref<ApprovalCandidate[]>([])

const scenarioLabels: Record<string, string> = {
  low_risk: '低风险换货（演示场景）',
  approval_required: '需人工审批（演示场景）',
  rejected: '超出政策窗口（演示场景）',
}

function scenarioLabel(scenario: string | null): string {
  if (!scenario) {
    return '未标注演示场景'
  }
  return scenarioLabels[scenario] ?? scenario
}

/**
 * 候选识别条件：demo_scenario === 'approval_required'。
 * 这是 T005 seeded domain data 的明确场景标签，仅用于展示当前演示审批候选，
 * 不代表任何真实持久化的 ApprovalRequest（T020 尚未实现）。
 */
function isApprovalCandidate(ticket: TicketRecord): boolean {
  return ticket.demo_scenario === 'approval_required'
}

async function loadCandidates(): Promise<void> {
  loadState.value = 'loading'
  candidates.value = []
  try {
    const tickets = await fetchTickets()
    const approvalTickets = tickets.filter(isApprovalCandidate)

    if (approvalTickets.length === 0) {
      loadState.value = 'empty'
      return
    }

    // 逐个补全关联订单上下文；单个详情失败时不伪造订单数据，仅标记订单信息不可用
    const enriched = await Promise.all(
      approvalTickets.map(async (ticket): Promise<ApprovalCandidate> => {
        let order: TicketOrderContext | null = null
        try {
          const detail = await fetchTicketDetail(ticket.business_key)
          order = detail.order
        } catch {
          order = null
        }
        return { ticket, order }
      })
    )

    candidates.value = enriched
    loadState.value = 'success'
  } catch {
    candidates.value = []
    loadState.value = 'failed'
  }
}

onMounted(loadCandidates)
</script>

<template>
  <div class="approval-inbox">
    <a class="back-link" href="#/">← 返回 Service Operations</a>

    <header class="inbox-header">
      <div class="title-row">
        <h1>Approval Inbox</h1>
        <span class="shell-tag">UI Shell / 演示审批候选</span>
      </div>
      <p class="subtitle">人工审批收件箱（结构预览）</p>
    </header>

    <p class="honest-notice">
      本页面是 Approval Inbox 的 UI shell，展示的是<strong>演示审批候选</strong>，而非真实审批请求。
      当前不存在任何持久化的 ApprovalRequest（T020 尚未实现），因此这里展示的候选均来自
      seeded demo ticket（<code>demo_scenario=approval_required</code>），不会伪造 approval id、审批人、
      时间戳、审批结果或 AgentRun id。
    </p>

    <p
      v-if="loadState === 'loading'"
      class="state-message"
      data-testid="approval-inbox-state"
      data-state="loading"
    >
      正在加载审批候选...
    </p>

    <p
      v-else-if="loadState === 'failed'"
      class="state-message state-failed"
      data-testid="approval-inbox-state"
      data-state="failed"
    >
      审批候选加载失败，未能从后端获取工单数据，请稍后重试。
    </p>

    <p
      v-else-if="loadState === 'empty'"
      class="state-message state-empty"
      data-testid="approval-inbox-state"
      data-state="empty"
    >
      当前没有需要审批的演示候选（后端未返回任何 approval_required 工单）。
    </p>

    <ul
      v-else
      class="candidate-list"
      data-testid="approval-inbox-state"
      data-state="success"
    >
      <li v-for="candidate in candidates" :key="candidate.ticket.business_key" class="candidate-card">
        <div class="card-head">
          <div class="card-title">
            <h2>{{ candidate.ticket.subject }}</h2>
            <span class="candidate-tag">演示审批候选（Simulated）</span>
          </div>
          <span class="candidate-scenario">{{ scenarioLabel(candidate.ticket.demo_scenario) }}</span>
        </div>

        <section class="reason-block" aria-label="审批原因">
          <h3>审批原因（来自持久化业务事实）</h3>
          <p class="reason-text">{{ candidate.ticket.description }}</p>
        </section>

        <section class="objects-block" aria-label="受影响业务对象">
          <h3>受影响业务对象</h3>
          <dl class="facts">
            <div class="fact">
              <dt>Ticket 编号</dt>
              <dd><code>{{ candidate.ticket.business_key }}</code></dd>
            </div>
            <template v-if="candidate.order">
              <div class="fact">
                <dt>Order 编号</dt>
                <dd><code>{{ candidate.order.business_key }}</code></dd>
              </div>
              <div class="fact">
                <dt>商品</dt>
                <dd>{{ candidate.order.product_name }}</dd>
              </div>
              <div class="fact">
                <dt>订单金额</dt>
                <dd>¥{{ candidate.order.amount }}</dd>
              </div>
            </template>
            <div v-else class="fact">
              <dt>关联订单</dt>
              <dd class="fact-unavailable">订单信息不可用</dd>
            </div>
          </dl>
        </section>

        <section class="actions-block" aria-label="审批操作">
          <div class="actions-note">
            审批操作尚未实现（T021 未实现）。以下 Approve / Reject 控件为占位展示，
            <strong>点击不会改变任何业务状态</strong>，也不会创建或更新任何审批请求。
          </div>
          <div class="actions">
            <button class="action-button action-approve" type="button" disabled>Approve</button>
            <button class="action-button action-reject" type="button" disabled>Reject</button>
          </div>
        </section>

        <a class="detail-link" :href="`#/tickets/${candidate.ticket.business_key}`">
          查看工单详情 →
        </a>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.approval-inbox {
  font-family: system-ui, -apple-system, sans-serif;
  max-width: 960px;
  margin: 0 auto;
  padding: 2rem;
}

.back-link {
  display: inline-block;
  margin-bottom: 1.25rem;
  color: #3949ab;
  text-decoration: none;
}

.back-link:hover {
  text-decoration: underline;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.inbox-header h1 {
  margin: 0;
  font-size: 1.5rem;
}

.shell-tag {
  font-size: 0.7rem;
  font-weight: 600;
  color: #455a64;
  background: #eceff1;
  border: 1px solid #b0bec5;
  border-radius: 4px;
  padding: 0.15rem 0.5rem;
  white-space: nowrap;
}

.subtitle {
  margin: 0.4rem 0 0;
  color: #616161;
  font-size: 0.9rem;
}

.honest-notice {
  margin: 1rem 0 1.25rem;
  padding: 0.75rem 0.9rem;
  font-size: 0.88rem;
  line-height: 1.6;
  color: #455a64;
  background: #eceff1;
  border: 1px solid #b0bec5;
  border-radius: 6px;
}

.state-message {
  color: #616161;
  padding: 1rem 0;
}

.state-failed {
  color: #c62828;
}

.state-empty {
  color: #8d6e00;
}

.candidate-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.candidate-card {
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  padding: 1.25rem;
  background: #fafafa;
}

.card-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  margin-bottom: 1rem;
}

.card-title h2 {
  margin: 0 0 0.4rem;
  font-size: 1.1rem;
}

.candidate-tag {
  font-size: 0.65rem;
  font-weight: 600;
  color: #8d6e00;
  background: #fff3cd;
  border: 1px solid #e6c94f;
  border-radius: 4px;
  padding: 0.1rem 0.4rem;
  white-space: nowrap;
}

.candidate-scenario {
  font-size: 0.75rem;
  font-weight: 600;
  color: #455a64;
  background: #eceff1;
  border: 1px solid #b0bec5;
  border-radius: 4px;
  padding: 0.15rem 0.5rem;
  white-space: nowrap;
}

.reason-block,
.objects-block,
.actions-block {
  margin-bottom: 1rem;
}

.reason-block h3,
.objects-block h3 {
  margin: 0 0 0.5rem;
  font-size: 0.85rem;
  color: #455a64;
  font-weight: 600;
}

.reason-text {
  margin: 0;
  padding: 0.6rem 0.75rem;
  background: #ffffff;
  border: 1px solid #eeeeee;
  border-radius: 6px;
  font-size: 0.9rem;
  line-height: 1.6;
}

.facts {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.75rem 1rem;
  margin: 0;
}

.fact dt {
  margin: 0;
  color: #757575;
  font-size: 0.72rem;
}

.fact dd {
  margin: 0.2rem 0 0;
  font-size: 0.9rem;
}

.fact code {
  font-size: 0.82rem;
}

.fact-unavailable {
  color: #757575;
}

.actions-note {
  margin: 0 0 0.75rem;
  font-size: 0.85rem;
  line-height: 1.6;
  color: #616161;
}

.actions {
  display: flex;
  gap: 0.75rem;
}

.action-button {
  font: inherit;
  font-size: 0.9rem;
  padding: 0.5rem 1.25rem;
  border-radius: 6px;
  border: 1px solid #b0bec5;
  cursor: not-allowed;
  opacity: 0.55;
}

.action-approve {
  color: #1b5e20;
  background: #e8f5e9;
  border-color: #a5d6a7;
}

.action-reject {
  color: #b71c1c;
  background: #ffebee;
  border-color: #ef9a9a;
}

.detail-link {
  display: inline-block;
  font-size: 0.9rem;
  color: #3949ab;
  text-decoration: none;
}

.detail-link:hover {
  text-decoration: underline;
}
</style>
