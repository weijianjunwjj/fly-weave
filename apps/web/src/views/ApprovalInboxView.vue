<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  approveApproval,
  fetchApprovalInbox,
  fetchAuditEvents,
  rejectApproval,
  resumeAgentRun,
  type ApprovalInboxItem,
  type AuditEvent,
} from '../lib/approvalsApi'
import { startAgentRun } from '../lib/agentRunsApi'
import { fetchTickets } from '../lib/ticketsApi'

type LoadState = 'loading' | 'ready' | 'empty' | 'demo'
type ActionPhase = 'idle' | 'approving' | 'executing' | 'rejecting'

const loadState = ref<LoadState>('loading')
const approvals = ref<ApprovalInboxItem[]>([])
const selected = ref<ApprovalInboxItem | null>(null)
const auditEvents = ref<AuditEvent[]>([])
const auditLoading = ref(false)
const actionPhase = ref<ActionPhase>('idle')
const notice = ref<string | null>(null)
const rejectOpen = ref(false)
const rejectionNote = ref('')
const isCreatingScenario = ref(false)

const demoApproval: ApprovalInboxItem = {
  created_at: '2026-08-31T08:24:00',
  approval: {
    approval_key: 'demo-approval-preview',
    status: 'pending',
    protected_action: 'create_replacement',
    agent_run_key: 'demo-run-preview',
    agent_run_status: 'waiting_for_approval',
    resolved_at: null,
    decision_reason: null,
    risk: {
      action: 'create_replacement',
      level: 'high',
      rule_code: 'order_amount_above_approval_threshold',
      requires_approval: true,
      reason: '订单金额 ¥1,299 超过 AI 自动授权上限 ¥500，需要人工确认。',
      order_key: 'ORD-20260831-0824',
      order_amount: '1299.00',
      approval_threshold_amount: '500.00',
      policy_key: 'after-sales-policy-cn',
    },
  },
  ticket: {
    business_key: 'TK-20260831-1048',
    subject: '高价值订单配送损坏换货',
    issue_type: '商品损坏',
    description: '客户收到商品时外包装严重破损，商品无法正常使用，希望尽快安排同款换货。',
    status: 'open',
    demo_scenario: 'approval_required',
    is_demo_data: true,
    created_at: '2026-08-31T08:21:00',
    updated_at: '2026-08-31T08:21:00',
    customer: {
      business_key: 'customer-demo-preview',
      name: '王女士',
      email: 'wang@example.com',
      phone: null,
      is_demo_data: true,
    },
    order: {
      business_key: 'ORD-20260831-0824',
      product_sku: 'FW-AIR-07',
      product_name: 'FlyAir 智能净化器',
      purchased_at: '2026-08-23T10:30:00',
      status: 'delivered',
      amount: '1299.00',
      is_demo_data: true,
    },
  },
  agent_run: {
    business_key: 'demo-run-preview',
    ticket_key: 'TK-20260831-1048',
    status: 'waiting_for_approval',
    created_at: '2026-08-31T08:23:00',
    started_at: '2026-08-31T08:23:00',
    completed_at: null,
    error_message: null,
    steps: [
      { step_order: 1, name: 'intent_extraction', status: 'completed', started_at: '2026-08-31T08:23:00', completed_at: '2026-08-31T08:23:06', error_message: null },
      { step_order: 2, name: 'policy_retrieval', status: 'completed', started_at: '2026-08-31T08:23:06', completed_at: '2026-08-31T08:23:12', error_message: null },
      { step_order: 3, name: 'risk_evaluation', status: 'completed', started_at: '2026-08-31T08:23:12', completed_at: '2026-08-31T08:23:16', error_message: null },
    ],
    replacement: null,
    ticket_result: { status: 'open', resolution: null, resolution_summary: null, resolved_at: null, replacement_key: null },
    recommendation: {
      action: 'replacement',
      issue_summary: '客户收到商品时外包装严重破损，商品无法正常使用，希望尽快安排同款换货。',
      confidence: 1,
    },
    policy_basis: {
      status: 'success',
      query_summary: '商品到货损坏且在售后时限内，符合换货条件。',
      document_key: 'policy-after-sales-cn',
      document_title: '售后服务政策',
      source_reference: '§ 4.2 到货损坏处理',
      is_demo_data: true,
      failure_reason: null,
      passages: [{
        rank: 1,
        score: 0.94,
        chunk_key: 'policy-preview-4-2',
        chunk_order: 4,
        passage: '商品签收后 7 日内发现运输损坏，可免费安排同款换货；高价值订单需经人工授权。',
      }],
    },
    risk: {
      action: 'create_replacement',
      level: 'high',
      rule_code: 'order_amount_above_approval_threshold',
      requires_approval: true,
      reason: '订单金额 ¥1,299 超过 AI 自动授权上限 ¥500，需要人工确认。',
      order_key: 'ORD-20260831-0824',
      order_amount: '1299.00',
      approval_threshold_amount: '500.00',
      policy_key: 'after-sales-policy-cn',
    },
    approval_request: null,
  },
}

const demoAudit: AuditEvent[] = [
  { event_key: 'demo-1', event_type: 'ticket_received', actor_type: 'system', occurred_at: '2026-08-31T08:21:00', outcome: 'created', success: true, action: '接收客服工单', summary: '工单已进入 AI 服务工作流', affected_object_type: 'ticket', affected_object_key: 'TK-20260831-1048', reference_type: null, reference_key: null },
  { event_key: 'demo-2', event_type: 'policy_retrieved', actor_type: 'agent', occurred_at: '2026-08-31T08:23:12', outcome: 'success', success: true, action: '检索企业政策', summary: '已匹配售后服务政策 § 4.2', affected_object_type: 'agent_run', affected_object_key: 'demo-run-preview', reference_type: null, reference_key: null },
  { event_key: 'demo-3', event_type: 'risk_gate', actor_type: 'system', occurred_at: '2026-08-31T08:23:16', outcome: 'approval_required', success: false, action: '执行风险评估', summary: '金额超过自动授权上限，已阻止业务动作', affected_object_type: 'agent_run', affected_object_key: 'demo-run-preview', reference_type: null, reference_key: null },
  { event_key: 'demo-4', event_type: 'approval_request_created', actor_type: 'system', occurred_at: '2026-08-31T08:24:00', outcome: 'created', success: true, action: '创建人工审批', summary: '审批请求已进入 Approval Inbox', affected_object_type: 'approval_request', affected_object_key: 'demo-approval-preview', reference_type: null, reference_key: null },
]

const isDemoMode = computed(() => loadState.value === 'demo')
const pendingCount = computed(() => approvals.value.filter(item => item.approval.status === 'pending').length)
const highRiskCount = computed(() => approvals.value.filter(item => item.approval.risk.level.toLowerCase() === 'high').length)
const approvedToday = computed(() => approvals.value.filter(item => item.approval.status === 'approved' && isToday(item.approval.resolved_at)).length)
const rejectedToday = computed(() => approvals.value.filter(item => item.approval.status === 'rejected' && isToday(item.approval.resolved_at)).length)

function isToday(value: string | null): boolean {
  if (!value) return false
  const date = parseDate(value)
  const today = new Date()
  return date.toDateString() === today.toDateString()
}

function parseDate(value: string): Date {
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`)
}

function formatDate(value: string | null, includeDate = false): string {
  if (!value) return '—'
  const date = parseDate(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: includeDate ? 'short' : undefined,
    day: includeDate ? 'numeric' : undefined,
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function formatMoney(value: string | null): string {
  if (!value) return '—'
  const number = Number(value)
  return Number.isFinite(number)
    ? new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY', maximumFractionDigits: 0 }).format(number)
    : `¥${value}`
}

function actionLabel(item: ApprovalInboxItem): string {
  const amount = item.approval.risk.order_amount
  const labels: Record<string, string> = {
    create_replacement: '创建同款换货单',
    refund: '发起全额退款',
  }
  const label = labels[item.approval.protected_action] ?? item.approval.protected_action
  return amount ? `${label} · ${formatMoney(amount)}` : label
}

function statusLabel(item: ApprovalInboxItem): string {
  if (actionPhase.value === 'executing' && selected.value?.approval.approval_key === item.approval.approval_key) return '执行中'
  if (item.agent_run.status === 'completed') return '已完成'
  const labels: Record<string, string> = { pending: '待审批', approved: '已批准', rejected: '已拒绝' }
  return labels[item.approval.status] ?? item.approval.status
}

function statusTone(item: ApprovalInboxItem): string {
  if (actionPhase.value === 'executing' && selected.value?.approval.approval_key === item.approval.approval_key) return 'executing'
  if (item.agent_run.status === 'completed') return 'completed'
  return item.approval.status
}

function eventLabel(event: AuditEvent): string {
  const labels: Record<string, string> = {
    ticket_received: 'Ticket received',
    policy_retrieved: 'Policy retrieved',
    get_order: 'Order context loaded',
    check_inventory: 'Inventory checked',
    decision_produced: 'AI recommendation prepared',
    risk_gate: 'Risk Gate evaluated',
    approval_request_created: 'Approval requested',
    approval_approved: 'Approved by operator',
    approval_rejected: 'Rejected by operator',
    create_replacement: 'Action executed',
    update_ticket: 'Ticket updated',
    agent_run_outcome: 'Agent Run completed',
  }
  return labels[event.event_type] ?? event.action
}

async function loadInbox(): Promise<void> {
  loadState.value = 'loading'
  notice.value = null
  selected.value = null
  try {
    approvals.value = await fetchApprovalInbox()
    loadState.value = approvals.value.length ? 'ready' : 'empty'
  } catch {
    approvals.value = [demoApproval]
    loadState.value = 'demo'
    notice.value = '审批服务暂时不可用，当前展示示例服务场景。连接恢复后可执行真实审批。'
  }
}

async function openDetail(item: ApprovalInboxItem): Promise<void> {
  selected.value = item
  rejectOpen.value = false
  rejectionNote.value = ''
  notice.value = null
  auditLoading.value = true
  if (isDemoMode.value) {
    auditEvents.value = demoAudit
    auditLoading.value = false
    return
  }
  try {
    auditEvents.value = await fetchAuditEvents(item.approval.agent_run_key)
  } catch {
    auditEvents.value = []
    notice.value = '审计记录暂时无法加载，审批上下文仍可查看。'
  } finally {
    auditLoading.value = false
  }
}

function replaceSelected(item: ApprovalInboxItem): void {
  approvals.value = approvals.value.map(current =>
    current.approval.approval_key === item.approval.approval_key ? item : current
  )
  selected.value = item
}

async function handleApprove(): Promise<void> {
  if (!selected.value || isDemoMode.value || actionPhase.value !== 'idle') return
  actionPhase.value = 'approving'
  notice.value = null
  const current = selected.value
  try {
    const decision = await approveApproval(current.approval.approval_key)
    const approvedItem = { ...current, approval: decision }
    replaceSelected(approvedItem)
    actionPhase.value = 'executing'
    const resumed = await resumeAgentRun(decision.agent_run_key)
    const completedItem = { ...approvedItem, approval: resumed.approval ?? decision, agent_run: resumed.agent_run }
    replaceSelected(completedItem)
    auditEvents.value = await fetchAuditEvents(decision.agent_run_key)
    notice.value = resumed.agent_run.status === 'completed'
      ? '审批已批准，Agent 已恢复执行，业务动作与工单更新均已完成。'
      : '审批已批准，Agent 已恢复；请查看最新执行状态。'
  } catch {
    notice.value = selected.value?.approval.status === 'approved'
      ? '审批已真实批准，但 Agent 恢复未完成。可点击“重试恢复执行”。'
      : '审批提交失败，业务状态没有在界面中被假定更改，请重试。'
  } finally {
    actionPhase.value = 'idle'
  }
}

async function retryResume(): Promise<void> {
  if (!selected.value || isDemoMode.value || actionPhase.value !== 'idle') return
  actionPhase.value = 'executing'
  notice.value = null
  try {
    const resumed = await resumeAgentRun(selected.value.approval.agent_run_key)
    replaceSelected({ ...selected.value, approval: resumed.approval ?? selected.value.approval, agent_run: resumed.agent_run })
    auditEvents.value = await fetchAuditEvents(selected.value.approval.agent_run_key)
    notice.value = 'Agent 已恢复执行，最新业务结果已同步。'
  } catch {
    notice.value = 'Agent 恢复仍未完成；已批准状态保持不变，请稍后重试。'
  } finally {
    actionPhase.value = 'idle'
  }
}

async function handleReject(): Promise<void> {
  if (!selected.value || isDemoMode.value || actionPhase.value !== 'idle') return
  actionPhase.value = 'rejecting'
  notice.value = null
  try {
    const decision = await rejectApproval(selected.value.approval.approval_key, rejectionNote.value)
    replaceSelected({ ...selected.value, approval: decision, agent_run: { ...selected.value.agent_run, status: decision.agent_run_status } })
    auditEvents.value = await fetchAuditEvents(decision.agent_run_key)
    rejectOpen.value = false
    notice.value = '审批已拒绝，受保护动作不会继续执行。'
  } catch {
    notice.value = '拒绝提交失败，业务状态没有在界面中被假定更改，请重试。'
  } finally {
    actionPhase.value = 'idle'
  }
}

async function createDemoScenario(): Promise<void> {
  if (isCreatingScenario.value) return
  isCreatingScenario.value = true
  notice.value = null
  try {
    const tickets = await fetchTickets()
    const ticket = tickets.find(item => item.demo_scenario === 'approval_required')
    if (!ticket) {
      notice.value = '当前没有可启动的示例高风险工单。'
      return
    }
    await startAgentRun(ticket.business_key)
    await loadInbox()
  } catch {
    notice.value = '示例场景启动失败，请确认后端服务可用后重试。'
  } finally {
    isCreatingScenario.value = false
  }
}

onMounted(loadInbox)
</script>

<template>
  <div class="approval-workspace">
    <section class="page-heading">
      <div>
        <div class="eyebrow">HUMAN-IN-THE-LOOP OPERATIONS</div>
        <h1>Approval Inbox <span>人工审批中心</span></h1>
        <p>查看并处理需要人工确认的 AI 操作，在执行高风险业务动作前保留最终控制权。</p>
      </div>
      <div class="heading-actions">
        <span v-if="isDemoMode" class="demo-badge"><i></i> Demo Mode · Sample scenarios</span>
        <button class="refresh-button" type="button" :disabled="loadState === 'loading'" @click="loadInbox">↻ 刷新</button>
      </div>
    </section>

    <div v-if="notice" class="notice" :class="{ demo: isDemoMode }" role="status">
      <span>{{ notice }}</span>
      <button v-if="isDemoMode" type="button" @click="loadInbox">重新连接</button>
    </div>

    <section class="metrics" aria-label="审批统计">
      <article><span class="metric-icon pending">◷</span><div><small>待审批</small><strong>{{ pendingCount }}</strong></div></article>
      <article><span class="metric-icon high">!</span><div><small>高风险</small><strong>{{ highRiskCount }}</strong></div></article>
      <article><span class="metric-icon approved">✓</span><div><small>今日已批准</small><strong>{{ approvedToday }}</strong></div></article>
      <article><span class="metric-icon rejected">×</span><div><small>今日已拒绝</small><strong>{{ rejectedToday }}</strong></div></article>
    </section>

    <section class="inbox-panel">
      <header class="panel-heading">
        <div><h2>需要你确认的操作</h2><p>按创建时间排序 · 风险快照来自触发时刻</p></div>
        <span>{{ approvals.length }} items</span>
      </header>

      <div v-if="loadState === 'loading'" class="state-view" data-testid="approval-inbox-state" data-state="loading">
        <span class="spinner"></span><h3>正在加载审批工作台</h3><p>正在同步 ApprovalRequest 与执行上下文…</p>
      </div>

      <div v-else-if="loadState === 'empty'" class="state-view" data-testid="approval-inbox-state" data-state="empty">
        <span class="empty-icon">✓</span><h3>当前没有待审批操作</h3><p>所有高风险 AI 操作都已处理，新的请求会自动出现在这里。</p>
        <button class="primary-button" type="button" :disabled="isCreatingScenario" @click="createDemoScenario">
          {{ isCreatingScenario ? '正在运行真实场景…' : '运行示例高风险工单' }}
        </button>
      </div>

      <div v-else class="approval-table" data-testid="approval-inbox-state" :data-state="loadState">
        <div class="table-header">
          <span>客户 / 工单</span><span>AI proposed action</span><span>风险</span><span>创建时间</span><span>状态</span><span></span>
        </div>
        <article v-for="item in approvals" :key="item.approval.approval_key" class="approval-row" :class="{ active: selected?.approval.approval_key === item.approval.approval_key }">
          <div class="customer-cell">
            <span class="avatar">{{ item.ticket.customer?.name?.slice(0, 1) ?? '客' }}</span>
            <div><strong>{{ item.ticket.customer?.name ?? '客户信息未提供' }}</strong><small>{{ item.ticket.business_key }} · {{ item.ticket.subject }}</small></div>
          </div>
          <div class="action-cell"><strong>{{ actionLabel(item) }}</strong><small>{{ item.ticket.description }}</small></div>
          <div class="risk-cell"><span :data-level="item.approval.risk.level">{{ item.approval.risk.level.toUpperCase() }}</span><small>{{ item.approval.risk.reason }}</small></div>
          <time>{{ formatDate(item.created_at, true) }}</time>
          <span class="status-pill" :data-status="statusTone(item)"><i></i>{{ statusLabel(item) }}</span>
          <button class="detail-button" type="button" @click="openDetail(item)">查看详情 <span>→</span></button>
        </article>
      </div>
    </section>

    <div v-if="selected" class="drawer-layer" @click.self="selected = null">
      <aside class="detail-drawer" role="dialog" aria-modal="true" aria-labelledby="approval-detail-title">
        <header class="drawer-header">
          <div><span class="detail-kicker">APPROVAL REVIEW</span><h2 id="approval-detail-title">{{ selected.ticket.subject }}</h2><p>{{ selected.ticket.business_key }} · {{ selected.approval.approval_key }}</p></div>
          <button class="close-button" type="button" aria-label="关闭详情" @click="selected = null">×</button>
        </header>

        <div class="drawer-scroll">
          <section class="detail-section ticket-context">
            <div class="section-title"><span>01</span><div><h3>Ticket Context</h3><p>客户与业务对象</p></div><span class="ticket-status">{{ selected.ticket.status }}</span></div>
            <div class="context-grid">
              <div><small>客户</small><strong>{{ selected.ticket.customer?.name ?? '未提供' }}</strong><span>{{ selected.ticket.customer?.email ?? '—' }}</span></div>
              <div><small>关联订单</small><strong>{{ selected.ticket.order?.business_key ?? '未关联' }}</strong><span>{{ selected.ticket.order?.product_name ?? '—' }}</span></div>
              <div><small>订单金额</small><strong>{{ formatMoney(selected.ticket.order?.amount ?? null) }}</strong><span>{{ selected.ticket.order?.status ?? '—' }}</span></div>
            </div>
            <div class="problem-box"><small>问题描述</small><p>{{ selected.ticket.description }}</p></div>
          </section>

          <section class="detail-section recommendation">
            <div class="section-title"><span>02</span><div><h3>AI Recommendation</h3><p>面向业务人员的处理建议</p></div></div>
            <div class="recommendation-box">
              <div class="spark">✦</div><div><small>建议执行</small><h4>{{ actionLabel(selected) }}</h4><p>{{ selected.agent_run.policy_basis?.query_summary ?? 'AI 已根据工单上下文与企业规则完成处理建议。' }}</p></div>
            </div>
          </section>

          <section class="detail-section">
            <div class="section-title"><span>03</span><div><h3>Policy Basis</h3><p>本次建议使用的企业政策依据</p></div><span v-if="selected.agent_run.policy_basis?.status === 'success'" class="verified">✓ 已检索</span></div>
            <template v-if="selected.agent_run.policy_basis">
              <article class="policy-card">
                <header><div><strong>{{ selected.agent_run.policy_basis.document_title ?? '政策来源未命名' }}</strong><span>{{ selected.agent_run.policy_basis.source_reference ?? selected.agent_run.policy_basis.document_key }}</span></div><span>Policy match</span></header>
                <blockquote v-for="passage in selected.agent_run.policy_basis.passages" :key="passage.chunk_key">{{ passage.passage }}</blockquote>
                <p><b>为什么适用：</b>{{ selected.agent_run.policy_basis.query_summary }}</p>
              </article>
            </template>
            <p v-else class="data-unavailable">本次历史 Run 没有可展示的政策检索记录。</p>
          </section>

          <section class="detail-section">
            <div class="section-title"><span>04</span><div><h3>Risk Evaluation</h3><p>Risk Gate 触发时保存的快照</p></div></div>
            <div class="risk-evaluation">
              <div class="risk-level-block"><small>RISK</small><strong :data-level="selected.approval.risk.level">{{ selected.approval.risk.level.toUpperCase() }}</strong></div>
              <dl>
                <div><dt>Trigger</dt><dd><code>{{ selected.approval.risk.rule_code }}</code></dd></div>
                <div><dt>Protected action</dt><dd>{{ selected.approval.protected_action }}</dd></div>
                <div><dt>Explanation</dt><dd>{{ selected.approval.risk.reason }}</dd></div>
              </dl>
              <div class="threshold-visual">
                <div><span>自动授权上限</span><strong>{{ formatMoney(selected.approval.risk.approval_threshold_amount) }}</strong></div>
                <div class="bar"><i :style="{ width: Math.min(100, (Number(selected.approval.risk.approval_threshold_amount) / Number(selected.approval.risk.order_amount)) * 100) + '%' }"></i><b></b></div>
                <div><span>本次业务影响</span><strong>{{ formatMoney(selected.approval.risk.order_amount) }}</strong></div>
              </div>
            </div>
          </section>

          <section class="detail-section audit-section">
            <div class="section-title"><span>05</span><div><h3>Audit Timeline</h3><p>{{ isDemoMode ? '示例场景事件' : '来自持久化 AuditEvent' }}</p></div></div>
            <div v-if="auditLoading" class="audit-loading"><span class="spinner"></span>同步审计事件…</div>
            <ol v-else-if="auditEvents.length" class="audit-timeline">
              <li v-for="event in auditEvents" :key="event.event_key" :data-success="event.success">
                <time>{{ formatDate(event.occurred_at) }}</time><i></i>
                <div><strong>{{ eventLabel(event) }}</strong><p>{{ event.summary }}</p><span>{{ event.outcome }}</span></div>
              </li>
            </ol>
            <p v-else class="data-unavailable">暂时没有可显示的审计事件。</p>
          </section>
        </div>

        <footer class="decision-panel">
          <template v-if="isDemoMode">
            <div><strong>Demo Mode</strong><p>当前为示例上下文。连接审批服务后可执行真实决策。</p></div>
            <button class="secondary-button" type="button" @click="loadInbox">重新连接服务</button>
          </template>
          <template v-else-if="selected.agent_run.status === 'completed'">
            <div class="outcome-icon completed">✓</div><div><strong>Action executed · Ticket resolved</strong><p>{{ selected.agent_run.ticket_result.resolution_summary ?? 'Agent 已恢复并完成业务动作。' }}</p></div>
          </template>
          <template v-else-if="selected.approval.status === 'rejected'">
            <div class="outcome-icon rejected">×</div><div><strong>Approval rejected</strong><p>{{ selected.approval.decision_reason || '受保护动作已终止，Agent 不会继续执行。' }}</p></div>
          </template>
          <template v-else-if="selected.approval.status === 'approved'">
            <div><strong>Approval approved · Awaiting resume</strong><p>决策已持久化，业务动作尚未被标记为完成。</p></div>
            <button class="primary-button" type="button" :disabled="actionPhase !== 'idle'" @click="retryResume">{{ actionPhase === 'executing' ? 'Agent 正在执行…' : '重试恢复执行' }}</button>
          </template>
          <template v-else>
            <div class="decision-copy"><strong>Decision</strong><p>确认后将写入真实 ApprovalRequest。</p></div>
            <div v-if="rejectOpen" class="reject-form">
              <textarea v-model="rejectionNote" rows="2" maxlength="300" placeholder="拒绝原因（可选）"></textarea>
              <button type="button" @click="rejectOpen = false">取消</button>
              <button class="reject-confirm" type="button" :disabled="actionPhase !== 'idle'" @click="handleReject">{{ actionPhase === 'rejecting' ? '提交中…' : '确认拒绝' }}</button>
            </div>
            <div v-else class="decision-actions">
              <button class="reject-button" type="button" :disabled="actionPhase !== 'idle'" @click="rejectOpen = true">拒绝</button>
              <button class="approve-button" type="button" :disabled="actionPhase !== 'idle'" @click="handleApprove">{{ actionPhase === 'approving' ? '正在批准…' : actionPhase === 'executing' ? 'Agent 正在执行…' : '批准并执行' }}</button>
            </div>
          </template>
        </footer>
      </aside>
    </div>
  </div>
</template>

<style scoped>
* { box-sizing: border-box; }
.approval-workspace { max-width: 1600px; margin: 0 auto; padding: 38px 42px 56px; color: #172033; }
.page-heading { display: flex; justify-content: space-between; align-items: flex-end; gap: 30px; margin-bottom: 28px; }
.eyebrow,.detail-kicker { color: #6d5bd0; font-size: 11px; font-weight: 800; letter-spacing: .13em; }
.page-heading h1 { margin: 8px 0 8px; font-size: clamp(28px, 2.4vw, 38px); line-height: 1.15; letter-spacing: -.035em; }
.page-heading h1 span { margin-left: 10px; color: #737b8c; font-size: .5em; font-weight: 500; letter-spacing: 0; }
.page-heading p { margin: 0; color: #687184; font-size: 14px; }
.heading-actions { display: flex; align-items: center; gap: 12px; }
.demo-badge { display: flex; align-items: center; gap: 8px; padding: 8px 11px; border: 1px solid #dacb91; border-radius: 8px; background: #fffaf0; color: #755e16; font-size: 11px; font-weight: 700; }
.demo-badge i { width: 7px; height: 7px; border-radius: 50%; background: #d59b21; }
.refresh-button,.detail-button,.close-button,.secondary-button { border: 0; background: transparent; color: #556074; font: inherit; cursor: pointer; }
.refresh-button { padding: 9px 12px; border: 1px solid #dce1e9; border-radius: 8px; background: #fff; font-size: 12px; }
.notice { display: flex; justify-content: space-between; align-items: center; margin: -8px 0 18px; padding: 11px 14px; border: 1px solid #cbdcf4; border-radius: 9px; background: #f5f9ff; color: #31567f; font-size: 12px; }
.notice.demo { border-color: #e6d8a6; background: #fffcf4; color: #705c20; }
.notice button { border: 0; background: transparent; color: inherit; font-weight: 700; cursor: pointer; }
.metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border: 1px solid #e1e5ec; border-radius: 12px; background: #fff; box-shadow: 0 2px 6px rgba(31,42,68,.03); }
.metrics article { display: flex; align-items: center; gap: 13px; min-height: 88px; padding: 18px 22px; border-right: 1px solid #e7eaf0; }
.metrics article:last-child { border: 0; }
.metrics small { display: block; color: #7b8495; font-size: 11px; font-weight: 600; }
.metrics strong { display: block; margin-top: 3px; font-size: 24px; line-height: 1; }
.metric-icon { display: grid; place-items: center; width: 38px; height: 38px; border-radius: 10px; font-weight: 800; }
.metric-icon.pending { background: #fff6e9; color: #cb7919; }.metric-icon.high { background: #fff0f0; color: #d34040; }.metric-icon.approved { background: #edfaf4; color: #14855d; }.metric-icon.rejected { background: #f2f3f6; color: #6a7280; }
.inbox-panel { margin-top: 22px; overflow: hidden; border: 1px solid #e1e5ec; border-radius: 12px; background: #fff; box-shadow: 0 5px 18px rgba(31,42,68,.045); }
.panel-heading { display: flex; justify-content: space-between; align-items: center; padding: 21px 24px; border-bottom: 1px solid #e8ebf0; }
.panel-heading h2 { margin: 0; font-size: 15px; }.panel-heading p { margin: 4px 0 0; color: #8a92a1; font-size: 11px; }.panel-heading>span { padding: 5px 9px; border-radius: 6px; background: #f3f4f7; color: #777f8d; font-size: 10px; font-weight: 700; text-transform: uppercase; }
.table-header,.approval-row { display: grid; grid-template-columns: minmax(230px,1.2fr) minmax(220px,1.15fr) minmax(210px,1fr) 100px 90px 90px; column-gap: 20px; align-items: center; }
.table-header { padding: 10px 24px; background: #f8f9fb; color: #8a92a1; font-size: 10px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }
.approval-row { min-height: 96px; padding: 15px 24px; border-top: 1px solid #edf0f4; transition: background .15s; }
.approval-row:first-of-type { border-top: 0; }.approval-row:hover,.approval-row.active { background: #fafbff; }
.customer-cell { display: flex; min-width: 0; align-items: center; gap: 11px; }.avatar { display: grid; flex: 0 0 auto; place-items: center; width: 36px; height: 36px; border-radius: 10px; background: #eeebff; color: #6856c7; font-size: 13px; font-weight: 800; }
.customer-cell div,.action-cell,.risk-cell { min-width: 0; }.customer-cell strong,.action-cell strong { display: block; overflow: hidden; color: #252d3d; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }.customer-cell small,.action-cell small,.risk-cell small { display: -webkit-box; overflow: hidden; margin-top: 5px; color: #858d9c; font-size: 10.5px; line-height: 1.45; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.risk-cell>span { display: inline-block; padding: 3px 7px; border-radius: 5px; background: #fff0f0; color: #cc3434; font-size: 9px; font-weight: 800; letter-spacing: .08em; }.risk-cell>span[data-level="medium"] { background: #fff7e8; color: #b96d18; }
.approval-row time { color: #6f7786; font-size: 11px; }.status-pill { display: inline-flex; width: fit-content; align-items: center; gap: 6px; padding: 5px 8px; border-radius: 12px; background: #fff6e7; color: #ad681b; font-size: 10px; font-weight: 700; }.status-pill i { width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
.status-pill[data-status="approved"],.status-pill[data-status="completed"] { background: #ecf8f2; color: #147956; }.status-pill[data-status="rejected"] { background: #f2f3f5; color: #656d7a; }.status-pill[data-status="executing"] { background: #eef3ff; color: #426aca; }
.detail-button { color: #6354bd; font-size: 11px; font-weight: 700; white-space: nowrap; }.detail-button span { margin-left: 4px; }
.state-view { display: grid; min-height: 300px; place-items: center; align-content: center; padding: 50px; text-align: center; }.state-view h3 { margin: 14px 0 5px; font-size: 16px; }.state-view p { margin: 0 0 18px; color: #858d9c; font-size: 12px; }.empty-icon { display: grid; place-items: center; width: 46px; height: 46px; border-radius: 50%; background: #edf9f4; color: #13835e; font-size: 20px; }
.spinner { display: inline-block; width: 20px; height: 20px; border: 2px solid #dedbea; border-top-color: #6758c5; border-radius: 50%; animation: spin .8s linear infinite; }@keyframes spin { to { transform: rotate(360deg); } }
.primary-button,.approve-button { border: 0; border-radius: 8px; background: #6557c8; color: #fff; font: inherit; font-size: 12px; font-weight: 700; cursor: pointer; }.primary-button { padding: 10px 15px; }
.drawer-layer { position: fixed; z-index: 50; inset: 0; display: flex; justify-content: flex-end; background: rgba(22,29,44,.32); backdrop-filter: blur(2px); }
.detail-drawer { display: flex; width: min(720px, 92vw); height: 100vh; flex-direction: column; background: #f8f9fb; box-shadow: -18px 0 50px rgba(22,29,44,.16); }
.drawer-header { display: flex; justify-content: space-between; padding: 25px 30px 20px; border-bottom: 1px solid #e1e5eb; background: #fff; }.drawer-header h2 { margin: 6px 0 3px; font-size: 21px; letter-spacing: -.02em; }.drawer-header p { margin: 0; color: #8a92a0; font-size: 10px; }.close-button { align-self: flex-start; width: 30px; height: 30px; border-radius: 8px; background: #f2f3f6; font-size: 20px; }
.drawer-scroll { overflow-y: auto; flex: 1; padding: 22px 28px 40px; }.detail-section { margin-bottom: 18px; padding: 20px; border: 1px solid #e1e5eb; border-radius: 11px; background: #fff; }.section-title { display: flex; align-items: center; gap: 10px; margin-bottom: 17px; }.section-title>span:first-child { display: grid; place-items: center; width: 25px; height: 25px; border-radius: 7px; background: #f0eefb; color: #6858c2; font-size: 9px; font-weight: 800; }.section-title h3 { margin: 0; font-size: 13px; }.section-title p { margin: 2px 0 0; color: #9299a6; font-size: 9.5px; }.ticket-status,.verified { margin-left: auto; padding: 4px 7px; border-radius: 5px; background: #eef8f4; color: #17805b; font-size: 9px; font-weight: 700; text-transform: uppercase; }
.context-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: 10px; }.context-grid>div { padding: 12px; border-radius: 8px; background: #f7f8fa; }.context-grid small,.recommendation-box small,.problem-box small { display: block; color: #949ba8; font-size: 9px; font-weight: 700; text-transform: uppercase; }.context-grid strong { display: block; overflow: hidden; margin: 5px 0 3px; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }.context-grid span { display: block; overflow: hidden; color: #7f8796; font-size: 9.5px; text-overflow: ellipsis; white-space: nowrap; }.problem-box { margin-top: 11px; padding: 12px 14px; border-left: 3px solid #d7d1f7; background: #fafaff; }.problem-box p { margin: 6px 0 0; color: #50596a; font-size: 11px; line-height: 1.65; }
.recommendation-box { display: flex; gap: 13px; padding: 16px; border: 1px solid #ded9f6; border-radius: 9px; background: #faf9ff; }.spark { display: grid; flex: 0 0 auto; place-items: center; width: 33px; height: 33px; border-radius: 9px; background: #6758c8; color: #fff; }.recommendation-box h4 { margin: 5px 0; font-size: 15px; }.recommendation-box p { margin: 0; color: #6f7786; font-size: 10.5px; line-height: 1.6; }
.policy-card { overflow: hidden; border: 1px solid #e5e7eb; border-radius: 9px; }.policy-card header { display: flex; justify-content: space-between; padding: 12px 14px; background: #f7f8fa; }.policy-card header strong,.policy-card header span { display: block; }.policy-card header strong { font-size: 11px; }.policy-card header div span { margin-top: 3px; color: #858d9b; font-size: 9px; }.policy-card header>span { color: #6557c4; font-size: 9px; font-weight: 700; }.policy-card blockquote { margin: 0; padding: 14px; border-top: 1px solid #eceef2; color: #4b5464; font-size: 11px; line-height: 1.65; }.policy-card>p { margin: 0; padding: 11px 14px; border-top: 1px solid #eceef2; color: #727b8a; font-size: 10px; }
.risk-evaluation { display: grid; grid-template-columns: 100px 1fr; gap: 15px; }.risk-level-block { display: flex; align-items: center; justify-content: center; flex-direction: column; border-radius: 9px; background: #fff1f1; }.risk-level-block small { color: #a86a6a; font-size: 9px; letter-spacing: .12em; }.risk-level-block strong { margin-top: 6px; color: #cf3535; font-size: 20px; }.risk-evaluation dl { margin: 0; }.risk-evaluation dl div { display: grid; grid-template-columns: 110px 1fr; padding: 6px 0; border-bottom: 1px solid #eef0f3; }.risk-evaluation dt { color: #8d95a3; font-size: 9px; }.risk-evaluation dd { margin: 0; color: #4e5767; font-size: 10px; line-height: 1.5; }.risk-evaluation code { padding: 3px 5px; border-radius: 4px; background: #f2f3f5; font-size: 9px; }.threshold-visual { grid-column: 1/-1; display: grid; grid-template-columns: 1fr 1.3fr 1fr; align-items: center; gap: 12px; padding-top: 6px; }.threshold-visual div:first-child { text-align: left; }.threshold-visual div:last-child { text-align: right; }.threshold-visual span,.threshold-visual strong { display: block; font-size: 9px; }.threshold-visual strong { margin-top: 3px; font-size: 11px; }.bar { position: relative; height: 5px; border-radius: 5px; background: #f0d5d5; }.bar i { position: absolute; inset: 0 auto 0 0; border-radius: 5px; background: #df9b38; }.bar b { position: absolute; right: -2px; top: -3px; width: 11px; height: 11px; border: 2px solid #fff; border-radius: 50%; background: #d84141; box-shadow: 0 0 0 1px #d84141; }
.audit-loading { display: flex; align-items: center; gap: 9px; color: #7d8695; font-size: 10px; }.audit-loading .spinner { width: 15px; height: 15px; }.audit-timeline { margin: 0; padding: 0; list-style: none; }.audit-timeline li { display: grid; grid-template-columns: 48px 12px 1fr; gap: 10px; min-height: 55px; }.audit-timeline time { padding-top: 3px; color: #8c94a2; font-size: 9px; }.audit-timeline li>i { position: relative; width: 8px; height: 8px; margin-top: 3px; border: 2px solid #fff; border-radius: 50%; background: #6b5bc6; box-shadow: 0 0 0 1px #6b5bc6; }.audit-timeline li>i:after { position: absolute; top: 9px; left: 2px; width: 1px; height: 39px; background: #dde1e8; content: ''; }.audit-timeline li:last-child>i:after { display: none; }.audit-timeline li[data-success="false"]>i { background: #d98932; box-shadow: 0 0 0 1px #d98932; }.audit-timeline strong { font-size: 10.5px; }.audit-timeline p { margin: 3px 0; color: #747d8c; font-size: 9.5px; }.audit-timeline div span { color: #695abe; font-size: 8.5px; font-weight: 700; text-transform: uppercase; }.data-unavailable { margin: 0; padding: 14px; border-radius: 8px; background: #f6f7f9; color: #868e9c; font-size: 10px; }
.decision-panel { display: flex; min-height: 88px; align-items: center; gap: 12px; padding: 15px 28px; border-top: 1px solid #dde1e8; background: #fff; box-shadow: 0 -5px 18px rgba(30,39,57,.05); }.decision-panel>div:first-child:not(.outcome-icon) { flex: 1; }.decision-panel strong { font-size: 11px; }.decision-panel p { margin: 3px 0 0; color: #858d9b; font-size: 9.5px; }.decision-actions { display: flex; gap: 9px; margin-left: auto; }.reject-button,.approve-button,.secondary-button,.reject-confirm,.reject-form>button { padding: 10px 18px; border: 1px solid #d9dde5; border-radius: 8px; background: #fff; color: #505a6b; font: inherit; font-size: 11px; font-weight: 700; cursor: pointer; }.approve-button { min-width: 120px; border-color: #6557c8; background: #6557c8; color: #fff; }.outcome-icon { display: grid; flex: 0 0 auto; place-items: center; width: 34px; height: 34px; border-radius: 50%; }.outcome-icon.completed { background: #e9f8f1; color: #14845e; }.outcome-icon.rejected { background: #f5eeee; color: #bd4747; }.reject-form { display: grid; flex: 1.4; grid-template-columns: 1fr auto auto; gap: 7px; }.reject-form textarea { min-width: 0; resize: none; padding: 8px; border: 1px solid #d9dde5; border-radius: 7px; font: inherit; font-size: 10px; }.reject-confirm { border-color: #d25555; background: #d25555; color: #fff; }
button:disabled { cursor: wait; opacity: .6; }
@media (max-width: 1200px) { .approval-workspace { padding: 30px 26px; }.table-header,.approval-row { grid-template-columns: minmax(190px,1.2fr) minmax(180px,1fr) minmax(170px,.9fr) 82px 82px 75px; column-gap: 12px; }.approval-row { padding-left: 18px; padding-right: 18px; }.table-header { padding-left: 18px; padding-right: 18px; } }
@media (max-width: 900px) { .metrics { grid-template-columns: repeat(2,1fr); }.metrics article:nth-child(2) { border-right: 0; }.metrics article:nth-child(-n+2) { border-bottom: 1px solid #e7eaf0; }.table-header { display: none; }.approval-row { grid-template-columns: 1fr auto; gap: 12px; }.approval-row>*:not(.customer-cell):not(.detail-button) { grid-column: 1/2; }.detail-button { grid-column: 2; grid-row: 1; }.page-heading { align-items: flex-start; flex-direction: column; } }
</style>
