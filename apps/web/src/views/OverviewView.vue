<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  fetchAgentRuns,
  startAgentRun,
  type AgentRunCenterItem,
  type AgentRunRecord,
} from '../lib/agentRunsApi'
import {
  fetchApprovalInbox,
  fetchAuditEvents,
  type ApprovalInboxItem,
  type AuditEvent,
} from '../lib/approvalsApi'
import {
  bootstrapHighRiskDemo,
  fetchTickets,
  type TicketDetailRecord,
  type TicketRecord,
} from '../lib/ticketsApi'
import { ticketStatusLabel } from '../lib/ticketStatus'
import {
  activityHref,
  buildAttentionItems,
  buildOverviewKpis,
  isPolicyBlocked,
  selectOperationSnapshot,
  selectRecentTickets,
  type SourceState,
} from '../lib/overviewProjection'

type ActivityItem = AuditEvent & {
  runKey: string
  ticketKey: string
}

const ticketsState = ref<SourceState>('loading')
const runsState = ref<SourceState>('loading')
const approvalsState = ref<SourceState>('loading')
const activityState = ref<SourceState>('loading')
const tickets = ref<TicketRecord[]>([])
const runs = ref<AgentRunCenterItem[]>([])
const approvals = ref<ApprovalInboxItem[]>([])
const activities = ref<ActivityItem[]>([])

type DemoPhase = 'idle' | 'creating_ticket' | 'starting_run' | 'finished' | 'failed'
const demoOpen = ref(false)
const demoPhase = ref<DemoPhase>('idle')
const demoTicket = ref<TicketDetailRecord | null>(null)
const demoRun = ref<AgentRunRecord | null>(null)
const demoError = ref<string | null>(null)

const demoOutcome = computed(() => {
  const run = demoRun.value
  if (!run) return null
  if (run.approval_request?.status === 'pending') return 'approval'
  if (isPolicyBlocked(run)) return 'blocked'
  if (run.status === 'completed') return 'completed'
  if (run.status === 'failed') return 'failed'
  return run.status
})

function demoStepLabel(name: string): string {
  const labels: Record<string, string> = {
    intent_extraction: 'Extracting request intent',
    policy_lookup: 'Retrieving policy',
    policy_retrieval: 'Retrieving policy',
    get_order: 'Loading order context',
    check_inventory: 'Checking replacement inventory',
    replacement_decision: 'Evaluating eligibility',
    create_replacement: 'Evaluating risk and protected action',
    update_ticket: 'Updating service ticket',
  }
  return labels[name] ?? name.replace(/_/g, ' ')
}

function resetDemo(): void {
  demoPhase.value = 'idle'
  demoTicket.value = null
  demoRun.value = null
  demoError.value = null
}

function openDemo(): void {
  resetDemo()
  demoOpen.value = true
}

async function runHighRiskDemo(): Promise<void> {
  if (demoPhase.value === 'creating_ticket' || demoPhase.value === 'starting_run') return
  resetDemo()
  demoPhase.value = 'creating_ticket'
  try {
    demoTicket.value = await bootstrapHighRiskDemo()
    demoPhase.value = 'starting_run'
    demoRun.value = await startAgentRun(demoTicket.value.business_key)
    demoPhase.value = 'finished'
    await loadAll()
  } catch (error) {
    demoPhase.value = 'failed'
    demoError.value = error instanceof Error ? error.message : '演示场景运行失败'
  }
}

function dateValue(value: string | null): number {
  if (!value) return 0
  return Date.parse(value.endsWith('Z') ? value : `${value}Z`)
}

const pendingApprovals = computed(() =>
  approvals.value.filter(({ approval }) => approval.status === 'pending'),
)

const kpis = computed(() => buildOverviewKpis(
  tickets.value,
  runs.value,
  approvals.value,
  {
    tickets: ticketsState.value,
    runs: runsState.value,
    approvals: approvalsState.value,
  },
))

const attentionItems = computed(() =>
  buildAttentionItems(runs.value, approvals.value),
)

const operationSnapshot = computed(() =>
  selectOperationSnapshot(runs.value),
)

const recentTickets = computed(() =>
  selectRecentTickets(tickets.value),
)

function currentStep(run: AgentRunRecord): string {
  if (run.approval_request?.status === 'pending') return 'Waiting for Approval'
  if (run.status === 'completed') return 'Execution completed'
  const step = [...run.steps].reverse().find(item =>
    item.status === 'running' || item.status === 'failed',
  ) ?? run.steps[run.steps.length - 1]
  return step?.name ?? 'No execution step recorded'
}

function runStatusLabel(run: AgentRunRecord): string {
  if (isPolicyBlocked(run)) return 'Policy Blocked'
  if (run.status === 'cancelled' && run.approval_request?.status === 'rejected') {
    return 'Rejected'
  }
  return ({
    queued: 'Queued',
    running: 'Running',
    waiting_for_approval: 'Waiting Approval',
    completed: 'Completed',
    failed: 'Failed',
    cancelled: 'Stopped',
  } as Record<string, string>)[run.status] ?? run.status
}

function ticketRunStatus(ticket: TicketRecord): string {
  if (!ticket.agent_run_status) return 'Not started'
  return ({
    queued: 'Queued',
    running: 'Running',
    waiting_for_approval: 'Waiting approval',
    completed: 'Completed',
    failed: 'Failed',
    cancelled: 'Stopped',
  } as Record<string, string>)[ticket.agent_run_status] ?? ticket.agent_run_status
}

function formatMoney(value: string | null): string {
  if (!value) return '—'
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'CNY',
    maximumFractionDigits: 2,
  }).format(Number(value))
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value.endsWith('Z') ? value : `${value}Z`))
}

function activityLabel(event: AuditEvent): string {
  const labels: Record<string, string> = {
    inventory_checked: '库存检查完成',
    policy_retrieved: '政策检索完成',
    risk_assessed: '风险判断完成',
    approval_request_created: '创建人工审批',
    approval_approved: '人工批准操作',
    approval_rejected: '人工拒绝操作',
    replacement_created: '换货单创建完成',
    ticket_updated: '工单状态更新',
    agent_run_completed: 'AI 执行完成',
  }
  return labels[event.event_type] ?? event.action.replace(/_/g, ' ')
}

async function loadTickets(): Promise<void> {
  ticketsState.value = 'loading'
  try {
    tickets.value = await fetchTickets()
    ticketsState.value = tickets.value.length ? 'ready' : 'empty'
  } catch {
    tickets.value = []
    ticketsState.value = 'failed'
  }
}

async function loadApprovals(): Promise<void> {
  approvalsState.value = 'loading'
  try {
    approvals.value = await fetchApprovalInbox()
    approvalsState.value = approvals.value.length ? 'ready' : 'empty'
  } catch {
    approvals.value = []
    approvalsState.value = 'failed'
  }
}

async function loadActivity(sourceRuns = runs.value): Promise<void> {
  activityState.value = 'loading'
  const candidates = [...sourceRuns]
    .sort((a, b) => dateValue(b.agent_run.created_at) - dateValue(a.agent_run.created_at))
    .slice(0, 5)
  if (!candidates.length) {
    activities.value = []
    activityState.value = 'empty'
    return
  }
  const results = await Promise.allSettled(candidates.map(async item => {
    const events = await fetchAuditEvents(item.agent_run.business_key)
    return events.map(event => ({
      ...event,
      runKey: item.agent_run.business_key,
      ticketKey: item.ticket.business_key,
    }))
  }))
  const successful = results.filter(
    (result): result is PromiseFulfilledResult<ActivityItem[]> => result.status === 'fulfilled',
  )
  activities.value = successful
    .flatMap(result => result.value)
    .sort((a, b) => dateValue(b.occurred_at) - dateValue(a.occurred_at))
    .slice(0, 7)
  activityState.value = successful.length === 0
    ? 'failed'
    : activities.value.length ? 'ready' : 'empty'
}

async function loadRuns(): Promise<void> {
  runsState.value = 'loading'
  try {
    runs.value = await fetchAgentRuns()
    runsState.value = runs.value.length ? 'ready' : 'empty'
    await loadActivity(runs.value)
  } catch {
    runs.value = []
    runsState.value = 'failed'
    activities.value = []
    activityState.value = 'failed'
  }
}

async function loadAll(): Promise<void> {
  await Promise.all([loadTickets(), loadRuns(), loadApprovals()])
}

onMounted(() => {
  loadAll()
  if (window.location.hash.includes('demo=open')) openDemo()
})
</script>

<template>
  <div class="overview">
    <header class="hero">
      <div>
        <span class="eyebrow">AI SERVICE OPERATIONS COMMAND CENTER</span>
        <h1>Overview <small>AI 服务运营总览</small></h1>
        <p>实时查看工单、AI 执行、风险审批与关键业务结果。</p>
      </div>
      <div class="hero-actions">
        <button class="run-demo-button" type="button" @click="openDemo"><span>▶</span> Run Demo · 运行演示</button>
        <button type="button" :disabled="[ticketsState, runsState, approvalsState].includes('loading')" @click="loadAll">
          <span>↻</span> 刷新数据
        </button>
      </div>
    </header>

    <div v-if="demoOpen" class="demo-layer" @click.self="demoOpen = false">
      <section class="demo-dialog" role="dialog" aria-modal="true" aria-labelledby="demo-title">
        <header>
          <div><span class="eyebrow">REAL BUSINESS JOURNEY</span><h2 id="demo-title">Run Demo <small>运行演示</small></h2><p>系统只创建业务输入；后续处理、审批与审计全部复用正式链路。</p></div>
          <button type="button" aria-label="关闭演示" @click="demoOpen = false">×</button>
        </header>

        <article class="scenario-card" :class="{ selected: demoPhase !== 'idle' }">
          <span class="scenario-icon">!</span>
          <div><small>RECOMMENDED SCENARIO</small><strong>High-Risk Replacement</strong><p>高价值换货 · ¥1,299 · Requires human approval</p></div>
          <span class="scenario-tag">Human review</span>
        </article>

        <div v-if="demoPhase === 'idle'" class="demo-intro">
          <p>将新增唯一的演示订单与工单，并启动真实 AI 处理。不会重置或覆盖任何历史数据。</p>
          <button class="primary-demo-action" type="button" @click="runHighRiskDemo">运行此场景</button>
        </div>

        <div v-else class="demo-progress" aria-live="polite">
          <ol>
            <li :data-state="demoTicket ? 'completed' : demoPhase === 'failed' ? 'failed' : 'active'">
              <i>{{ demoTicket ? '✓' : '1' }}</i><div><strong>Creating service ticket</strong><span>{{ demoTicket ? demoTicket.business_key : '正在通过演示 bootstrap 创建真实订单与工单' }}</span></div>
            </li>
            <li :data-state="demoRun ? 'completed' : demoPhase === 'starting_run' ? 'active' : demoPhase === 'failed' && demoTicket ? 'failed' : 'pending'">
              <i>{{ demoRun ? '✓' : '2' }}</i><div><strong>Starting AI processing</strong><span>{{ demoRun ? demoRun.business_key : '等待正式 Agent Run 返回真实状态' }}</span></div>
            </li>
            <li v-for="step in demoRun?.steps ?? []" :key="step.step_order" :data-state="step.status">
              <i>{{ step.status === 'completed' ? '✓' : step.status === 'failed' ? '!' : '•' }}</i><div><strong>{{ demoStepLabel(step.name) }}</strong><span>{{ step.status === 'failed' ? '该真实步骤未完成' : '已由后端执行并持久化' }}</span></div>
            </li>
          </ol>

          <div v-if="demoOutcome === 'approval' && demoRun?.approval_request" class="demo-outcome approval">
            <span>!</span><div><strong>Human approval required</strong><p>风险等级 HIGH。受保护操作已暂停，等待你作出决定。</p></div>
            <a :href="`#/approvals?approval=${demoRun.approval_request.approval_key}`">Review Approval →</a>
          </div>
          <div v-else-if="demoOutcome === 'completed'" class="demo-outcome completed">
            <span>✓</span><div><strong>Completed</strong><p>业务动作、工单结果与审计记录均已真实落库。</p></div>
            <a :href="`#/agent-runs/${demoRun?.business_key}`">View Final Result →</a>
          </div>
          <div v-else-if="demoOutcome === 'blocked'" class="demo-outcome blocked">
            <span>⊘</span><div><strong>Blocked by Policy</strong><p>企业政策正确阻止了请求；这不是系统失败。</p></div>
            <a :href="`#/agent-runs/${demoRun?.business_key}`">View Policy Decision →</a>
          </div>
          <div v-else-if="demoPhase === 'failed'" class="demo-outcome failed">
            <span>×</span><div><strong>Demo could not continue</strong><p>{{ demoError }}</p></div>
            <button type="button" @click="runHighRiskDemo">Retry with new inputs</button>
          </div>

          <nav v-if="demoTicket" class="demo-journey">
            <a :href="`#/tickets/${demoTicket.business_key}`">View Ticket</a>
            <a v-if="demoRun" :href="`#/agent-runs/${demoRun.business_key}`">View Agent Run</a>
          </nav>
        </div>
      </section>
    </div>

    <section class="attention-panel" aria-labelledby="attention-title" data-testid="needs-attention">
      <header>
        <div>
          <span class="section-index">01 / PRIORITY</span>
          <h2 id="attention-title">Needs Attention <small>需要关注</small></h2>
        </div>
        <span v-if="attentionItems.length" class="attention-count">{{ attentionItems.length }} actions</span>
        <span v-else-if="runsState !== 'loading' && approvalsState !== 'loading'" class="healthy-badge">✓ Operations normal</span>
      </header>
      <div v-if="runsState === 'loading' || approvalsState === 'loading'" class="attention-loading">
        <i></i><div><strong>正在识别需要处理的事项</strong><span>同步审批与 AI 执行状态…</span></div>
      </div>
      <div v-else-if="runsState === 'failed' && approvalsState === 'failed'" class="inline-error">
        <div><strong>Attention data unavailable</strong><span>审批与执行数据暂时无法读取。</span></div>
        <button type="button" @click="loadAll">Retry</button>
      </div>
      <div v-else-if="attentionItems.length" class="attention-list">
        <article v-for="item in attentionItems" :key="item.key" :data-kind="item.kind" :data-testid="`attention-${item.kind}`">
          <span class="attention-mark">{{ item.kind === 'approval' ? '!' : item.kind === 'blocked' ? '⊘' : '×' }}</span>
          <div class="attention-copy">
            <small>{{ item.eyebrow }}</small>
            <strong>{{ item.title }}</strong>
            <p>{{ item.detail }}</p>
          </div>
          <b v-if="item.meta">{{ item.meta }}</b>
          <a :href="item.href">{{ item.action }} <span>→</span></a>
        </article>
      </div>
      <div v-else class="attention-empty">
        <span>✓</span>
        <div><strong>No actions need your attention</strong><p>当前没有待审批、政策阻止或执行失败事项。</p></div>
      </div>
      <p v-if="runsState === 'failed' || approvalsState === 'failed'" class="partial-note">
        部分数据暂不可用；当前列表仅展示已成功读取的数据。
      </p>
    </section>

    <section class="kpi-grid" aria-label="核心运营指标" data-testid="overview-kpis">
      <a v-for="metric in kpis" :key="metric.key" :href="metric.href" :data-tone="metric.tone" :data-testid="`kpi-${metric.key}`">
        <span class="kpi-dot"></span>
        <div><small>{{ metric.label }}</small><strong v-if="metric.source !== 'loading' && metric.source !== 'failed'">{{ metric.value }}</strong><strong v-else>—</strong><p>{{ metric.source === 'failed' ? '数据不可用' : metric.caption }}</p></div>
        <i>↗</i>
      </a>
    </section>

    <div class="operations-grid">
      <section class="panel ai-panel" aria-labelledby="ai-title" data-testid="ai-operations">
        <header class="panel-heading">
          <div><span class="section-index">02 / EXECUTION</span><h2 id="ai-title">AI Operations <small>AI 运行状态</small></h2></div>
          <a href="#/agent-runs">View all runs →</a>
        </header>
        <div v-if="runsState === 'loading'" class="panel-state"><span class="spinner"></span>同步 Agent Runs…</div>
        <div v-else-if="runsState === 'failed'" class="panel-state error"><strong>Agent Run data unavailable</strong><button type="button" @click="loadRuns">Retry</button></div>
        <div v-else-if="runsState === 'empty'" class="panel-state good"><span>✓</span><div><strong>AI operations normal</strong><p>当前尚无执行记录。</p></div></div>
        <div v-else class="run-list">
          <article v-for="{ agent_run: run, ticket } in operationSnapshot" :key="run.business_key">
            <span class="run-signal" :data-status="run.status"></span>
            <div>
              <strong>{{ ticket.customer?.name ?? '未关联客户' }} · {{ ticket.issue_type ?? ticket.subject }}</strong>
              <p>{{ currentStep(run) }}</p>
            </div>
            <span class="status-pill" :data-status="run.status">{{ runStatusLabel(run) }}</span>
            <a :href="`#/agent-runs/${encodeURIComponent(run.business_key)}`">查看 Run →</a>
          </article>
        </div>
      </section>

      <section class="panel approval-panel" aria-labelledby="approval-title" data-testid="approval-snapshot">
        <header class="panel-heading">
          <div><span class="section-index">03 / HUMAN GATE</span><h2 id="approval-title">Approval Snapshot <small>审批快照</small></h2></div>
          <a href="#/approvals">Open Inbox →</a>
        </header>
        <div v-if="approvalsState === 'loading'" class="panel-state"><span class="spinner"></span>同步审批请求…</div>
        <div v-else-if="approvalsState === 'failed'" class="panel-state error"><strong>Approval data unavailable</strong><button type="button" @click="loadApprovals">Retry</button></div>
        <div v-else-if="!pendingApprovals.length" class="approval-healthy"><span>✓</span><div><strong>No approvals waiting</strong><p>当前没有需要人工决策的受保护操作。</p></div></div>
        <div v-else class="approval-content">
          <p class="approval-alert"><strong>{{ pendingApprovals.length }}</strong> {{ pendingApprovals.length === 1 ? 'action requires' : 'actions require' }} your approval</p>
          <article v-for="item in pendingApprovals.slice(0, 2)" :key="item.approval.approval_key">
            <div><small>{{ item.approval.risk.level.toUpperCase() }} RISK</small><strong>{{ item.ticket.issue_type ?? item.ticket.subject }}</strong><p>{{ item.ticket.customer?.name ?? item.ticket.business_key }}</p></div>
            <b>{{ formatMoney(item.approval.risk.order_amount) }}</b>
          </article>
          <a class="primary-link" href="#/approvals" data-testid="review-approval">Review Approval →</a>
        </div>
      </section>
    </div>

    <div class="insight-grid">
      <section class="panel activity-panel" aria-labelledby="activity-title" data-testid="recent-activity">
        <header class="panel-heading">
          <div><span class="section-index">04 / LIVE FEED</span><h2 id="activity-title">Recent Activity <small>最近业务动态</small></h2></div>
          <button v-if="activityState === 'failed'" type="button" @click="loadActivity()">Retry</button>
        </header>
        <div v-if="activityState === 'loading'" class="panel-state"><span class="spinner"></span>同步审计事件…</div>
        <div v-else-if="activityState === 'failed'" class="panel-state error"><strong>Audit activity unavailable</strong><span>其他运营数据仍可正常使用。</span></div>
        <div v-else-if="activityState === 'empty'" class="panel-state good"><span>○</span><div><strong>No recent service activity</strong><p>执行发生后，真实 AuditEvent 将显示在这里。</p></div></div>
        <ol v-else class="activity-list">
          <li v-for="event in activities" :key="event.event_key">
            <time :datetime="event.occurred_at">{{ formatTime(event.occurred_at) }}</time>
            <i :data-success="event.success"></i>
            <a :href="activityHref(event)"><strong>{{ activityLabel(event) }}</strong><p>{{ event.summary }}</p><span>{{ event.outcome }} →</span></a>
          </li>
        </ol>
      </section>

      <section class="panel tickets-panel" aria-labelledby="tickets-title" data-testid="service-snapshot">
        <header class="panel-heading">
          <div><span class="section-index">05 / SERVICE</span><h2 id="tickets-title">Service Operations <small>最近工单</small></h2></div>
          <a href="#/tickets">View all tickets →</a>
        </header>
        <div v-if="ticketsState === 'loading'" class="panel-state"><span class="spinner"></span>同步工单…</div>
        <div v-else-if="ticketsState === 'failed'" class="panel-state error"><strong>Ticket data unavailable</strong><button type="button" @click="loadTickets">Retry</button></div>
        <div v-else-if="ticketsState === 'empty'" class="panel-state good"><span>○</span><div><strong>No service tickets yet</strong><p>创建工单后，最新业务状态将显示在这里。</p></div></div>
        <div v-else class="ticket-snapshot">
          <div class="snapshot-head"><span>Customer / Issue</span><span>Status</span><span>AI Status</span><span>Risk</span><span></span></div>
          <article v-for="ticket in recentTickets" :key="ticket.business_key">
            <div><strong>{{ ticket.customer_name ?? '未关联客户' }}</strong><p>{{ ticket.issue_type ?? ticket.subject }}</p></div>
            <span>{{ ticketStatusLabel(ticket.status) }}</span>
            <span :data-status="ticket.agent_run_status">{{ ticketRunStatus(ticket) }}</span>
            <b v-if="ticket.risk_level" :data-risk="ticket.risk_level">{{ ticket.risk_level.toUpperCase() }}</b><b v-else>—</b>
            <a :href="`#/tickets/${encodeURIComponent(ticket.business_key)}`" aria-label="查看工单">→</a>
          </article>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
* { box-sizing: border-box; }
.overview { max-width: 1580px; margin: 0 auto; padding: 34px 38px 58px; color: #1d2638; }
.hero,.panel-heading,.attention-panel>header { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.eyebrow,.section-index { color: #6859c5; font-size: 9px; font-weight: 850; letter-spacing: .14em; text-transform: uppercase; }
.hero h1 { margin: 7px 0 5px; font-size: 29px; letter-spacing: -.035em; }.hero h1 small { margin-left: 10px; color: #657083; font-size: 14px; font-weight: 550; letter-spacing: 0; }.hero p { margin: 0; color: #818998; font-size: 11.5px; }
.hero button,.panel-state button,.panel-heading button { padding: 9px 12px; border: 1px solid #dfe3e9; border-radius: 8px; background: #fff; color: #596375; font: inherit; font-size: 10px; font-weight: 700; cursor: pointer; }.hero button span { margin-right: 4px; }.hero button:disabled { cursor: wait; opacity: .55; }
.hero-actions { display: flex; gap: 8px; }.hero .run-demo-button { border-color: #6557c8; background: #6557c8; color: #fff; }
.demo-layer { position: fixed; z-index: 60; inset: 0; display: grid; place-items: center; padding: 24px; background: rgba(20,26,40,.42); backdrop-filter: blur(3px); }
.demo-dialog { width: min(680px,96vw); max-height: 90vh; overflow-y: auto; border-radius: 16px; background: #f8f9fb; box-shadow: 0 24px 70px rgba(16,22,35,.24); }
.demo-dialog>header { display: flex; justify-content: space-between; gap: 20px; padding: 24px 26px 20px; border-bottom: 1px solid #e3e6eb; background: #fff; }.demo-dialog h2 { margin: 6px 0 4px; font-size: 23px; }.demo-dialog h2 small { color: #747d8d; font-size: 12px; font-weight: 550; }.demo-dialog header p { margin: 0; color: #7e8796; font-size: 10px; }.demo-dialog header button { align-self: flex-start; border: 0; background: transparent; color: #7d8592; font-size: 24px; cursor: pointer; }
.scenario-card { display: grid; grid-template-columns: 38px 1fr auto; gap: 13px; align-items: center; margin: 20px 24px 0; padding: 16px; border: 1px solid #dcd8f2; border-radius: 11px; background: #fff; }.scenario-card.selected { border-color: #8c80d3; box-shadow: 0 0 0 2px #eeecfb; }.scenario-icon { display: grid; width: 36px; height: 36px; place-items: center; border-radius: 10px; background: #fff0f0; color: #c64141; font-weight: 900; }.scenario-card small { color: #7162c5; font-size: 8px; font-weight: 850; letter-spacing: .1em; }.scenario-card strong { display: block; margin-top: 3px; font-size: 13px; }.scenario-card p { margin: 4px 0 0; color: #798292; font-size: 9.5px; }.scenario-tag { padding: 5px 8px; border-radius: 10px; background: #fff3e2; color: #a66a1b; font-size: 8.5px; font-weight: 750; }
.demo-intro { display: flex; align-items: center; gap: 18px; padding: 20px 24px 24px; }.demo-intro p { flex: 1; margin: 0; color: #6f7888; font-size: 10px; line-height: 1.6; }.primary-demo-action { flex: 0 0 auto; padding: 10px 16px; border: 0; border-radius: 8px; background: #6557c8; color: #fff; font-size: 10px; font-weight: 750; cursor: pointer; }
.demo-progress { padding: 18px 24px 24px; }.demo-progress ol { margin: 0; padding: 0; list-style: none; }.demo-progress li { display: grid; grid-template-columns: 27px 1fr; gap: 10px; min-height: 51px; }.demo-progress li i { display: grid; width: 23px; height: 23px; place-items: center; border: 1px solid #d9dde4; border-radius: 50%; background: #fff; color: #9098a5; font-size: 9px; font-style: normal; }.demo-progress li[data-state="active"] i,.demo-progress li[data-state="running"] i { border-color: #7162ca; color: #6557c8; box-shadow: 0 0 0 3px #eceafb; }.demo-progress li[data-state="completed"] i { border-color: #36a379; background: #36a379; color: #fff; }.demo-progress li[data-state="failed"] i { border-color: #c94a4a; background: #c94a4a; color: #fff; }.demo-progress li strong { display: block; font-size: 10.5px; }.demo-progress li span { display: block; margin-top: 3px; color: #8a92a0; font-size: 9px; }
.demo-outcome { display: grid; grid-template-columns: 34px 1fr auto; gap: 12px; align-items: center; margin-top: 8px; padding: 15px; border-radius: 10px; background: #fff5e7; color: #8c5c19; }.demo-outcome>span { display: grid; width: 31px; height: 31px; place-items: center; border-radius: 50%; background: #fff; font-weight: 900; }.demo-outcome strong { font-size: 11px; }.demo-outcome p { margin: 3px 0 0; font-size: 9px; }.demo-outcome a,.demo-outcome button { border: 0; background: transparent; color: inherit; font-size: 9.5px; font-weight: 800; text-decoration: none; cursor: pointer; }.demo-outcome.completed { background: #eaf8f2; color: #167957; }.demo-outcome.blocked { background: #fff5e5; color: #95631d; }.demo-outcome.failed { background: #fff0f0; color: #a83e3e; }
.demo-journey { display: flex; gap: 8px; margin-top: 13px; }.demo-journey a { padding: 7px 9px; border: 1px solid #d9d5ee; border-radius: 7px; color: #6253b8; font-size: 9px; font-weight: 750; text-decoration: none; }
.attention-panel { margin-top: 24px; overflow: hidden; border: 1px solid #ddd9ef; border-radius: 13px; background: #fff; box-shadow: 0 7px 24px rgba(42,35,81,.055); }.attention-panel>header { min-height: 72px; padding: 17px 21px; border-bottom: 1px solid #ebe9f4; background: #fbfaff; }.attention-panel h2,.panel h2 { margin: 5px 0 0; font-size: 16px; letter-spacing: -.015em; }.attention-panel h2 small,.panel h2 small { margin-left: 7px; color: #89909d; font-size: 10px; font-weight: 550; }.attention-count,.healthy-badge { padding: 6px 9px; border-radius: 13px; background: #fff1e6; color: #b96322; font-size: 9px; font-weight: 800; }.healthy-badge { background: #eaf8f2; color: #157b58; }
.attention-list { display: grid; grid-template-columns: repeat(auto-fit,minmax(270px,1fr)); }.attention-list article { display: grid; grid-template-columns: 34px minmax(0,1fr) auto; gap: 11px; min-height: 128px; align-items: start; padding: 18px 20px; border-right: 1px solid #eceef2; }.attention-list article:last-child { border-right: 0; }.attention-mark { display: grid; width: 31px; height: 31px; place-items: center; border-radius: 9px; background: #fff3e6; color: #bd6925; font-weight: 900; }.attention-list article[data-kind="failed"] .attention-mark { background: #fff0f0; color: #c94343; }.attention-list article[data-kind="blocked"] .attention-mark { background: #fff6e5; color: #af7621; }.attention-copy { min-width: 0; }.attention-copy small { color: #a16b28; font-size: 8px; font-weight: 850; letter-spacing: .08em; }.attention-copy strong { display: block; overflow: hidden; margin-top: 4px; font-size: 11.5px; text-overflow: ellipsis; white-space: nowrap; }.attention-copy p { display: -webkit-box; overflow: hidden; margin: 5px 0 0; color: #7c8492; font-size: 9.5px; line-height: 1.5; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }.attention-list article>b { color: #3d4658; font-size: 11px; }.attention-list article>a { grid-column: 2/-1; width: fit-content; color: #6253bd; font-size: 9.5px; font-weight: 800; text-decoration: none; }.attention-empty,.attention-loading,.inline-error { display: flex; min-height: 92px; align-items: center; gap: 13px; padding: 18px 22px; }.attention-empty>span { display: grid; width: 34px; height: 34px; place-items: center; border-radius: 50%; background: #eaf8f2; color: #14805b; }.attention-empty strong,.attention-loading strong,.inline-error strong { display: block; font-size: 11px; }.attention-empty p,.attention-loading span,.inline-error span { display: block; margin: 4px 0 0; color: #858d9b; font-size: 9.5px; }.attention-loading i { width: 34px; height: 8px; border-radius: 4px; background: #e8e5f6; animation: pulse 1s infinite alternate; }.inline-error { justify-content: space-between; color: #a83f3f; }.inline-error button { padding: 7px 10px; border: 1px solid #e1baba; border-radius: 7px; background: #fff; color: inherit; cursor: pointer; }.partial-note { margin: 0; padding: 8px 20px; border-top: 1px solid #f0e5d6; background: #fffbf4; color: #8f6a35; font-size: 8.5px; }
.kpi-grid { display: grid; grid-template-columns: repeat(6,minmax(0,1fr)); gap: 10px; margin-top: 12px; }.kpi-grid>a { position: relative; display: grid; grid-template-columns: 8px 1fr auto; gap: 10px; min-height: 98px; align-items: start; padding: 16px; border: 1px solid #e1e4e9; border-radius: 11px; background: #fff; color: inherit; text-decoration: none; }.kpi-grid>a:hover { border-color: #cfc9ec; box-shadow: 0 5px 14px rgba(32,39,56,.05); }.kpi-dot { width: 7px; height: 7px; margin-top: 3px; border-radius: 50%; background: #8290a2; }.kpi-grid a[data-tone="purple"] .kpi-dot { background: #6b5bc8; }.kpi-grid a[data-tone="amber"] .kpi-dot { background: #dc922b; }.kpi-grid a[data-tone="green"] .kpi-dot { background: #23a172; }.kpi-grid a[data-tone="orange"] .kpi-dot { background: #c77b2a; }.kpi-grid a[data-tone="red"] .kpi-dot { background: #d44e4e; }.kpi-grid small { display: block; color: #747d8c; font-size: 8.5px; font-weight: 750; }.kpi-grid strong { display: block; margin-top: 8px; font-size: 22px; line-height: 1; }.kpi-grid p { margin: 6px 0 0; color: #9aa1ac; font-size: 8.5px; }.kpi-grid i { color: #b1b6bf; font-size: 10px; font-style: normal; }
.operations-grid,.insight-grid { display: grid; grid-template-columns: minmax(0,1.65fr) minmax(310px,.75fr); gap: 14px; margin-top: 14px; }.insight-grid { grid-template-columns: minmax(330px,.85fr) minmax(0,1.45fr); }.panel { overflow: hidden; border: 1px solid #e0e3e8; border-radius: 12px; background: #fff; box-shadow: 0 3px 12px rgba(31,40,60,.035); }.panel-heading { min-height: 70px; padding: 15px 20px; border-bottom: 1px solid #e7e9ed; }.panel-heading>a { color: #6657bf; font-size: 9.5px; font-weight: 750; text-decoration: none; }.panel-state { display: flex; min-height: 190px; align-items: center; justify-content: center; gap: 9px; color: #7f8795; font-size: 10px; }.panel-state.error { flex-direction: column; color: #ad4646; }.panel-state.error span { color: #8b929f; }.panel-state.good>span,.approval-healthy>span { display: grid; width: 36px; height: 36px; place-items: center; border-radius: 50%; background: #edf8f3; color: #16805b; font-size: 17px; }.panel-state.good strong,.approval-healthy strong { display: block; color: #354052; font-size: 11px; }.panel-state.good p,.approval-healthy p { margin: 4px 0 0; color: #8c94a1; font-size: 9.5px; }.spinner { width: 16px; height: 16px; border: 2px solid #dedbea; border-top-color: #6758c5; border-radius: 50%; animation: spin .8s linear infinite; }
.run-list article { display: grid; grid-template-columns: 9px minmax(180px,1fr) 105px 78px; gap: 12px; min-height: 68px; align-items: center; padding: 11px 20px; border-top: 1px solid #edf0f3; }.run-list article:first-child { border-top: 0; }.run-signal { width: 7px; height: 7px; border-radius: 50%; background: #9ba2ad; }.run-signal[data-status="running"] { background: #6557c5; box-shadow: 0 0 0 4px #efedfb; }.run-signal[data-status="waiting_for_approval"] { background: #d99127; }.run-signal[data-status="completed"] { background: #21986e; }.run-signal[data-status="failed"] { background: #d34c4c; }.run-list strong { display: block; overflow: hidden; font-size: 10.5px; text-overflow: ellipsis; white-space: nowrap; }.run-list p { margin: 4px 0 0; color: #858d9a; font-size: 9px; }.status-pill { width: fit-content; padding: 5px 7px; border-radius: 12px; background: #f1f2f5; color: #626b79; font-size: 8.5px; font-weight: 750; }.status-pill[data-status="running"] { background: #efedfb; color: #6151bd; }.status-pill[data-status="waiting_for_approval"] { background: #fff4e4; color: #a86319; }.status-pill[data-status="completed"] { background: #eaf8f2; color: #147a57; }.status-pill[data-status="failed"] { background: #fff0f0; color: #bb4040; }.run-list a { color: #6556bd; font-size: 9px; font-weight: 750; text-decoration: none; white-space: nowrap; }
.approval-healthy { display: flex; min-height: 190px; align-items: center; justify-content: center; gap: 12px; padding: 20px; }.approval-content { padding: 17px 19px 19px; }.approval-alert { margin: 0 0 12px; color: #765b34; font-size: 10px; }.approval-alert strong { color: #c36c22; font-size: 18px; }.approval-content article { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 11px 0; border-top: 1px solid #eceef2; }.approval-content article small { color: #c24949; font-size: 8px; font-weight: 850; }.approval-content article strong { display: block; margin-top: 4px; font-size: 10.5px; }.approval-content article p { margin: 3px 0 0; color: #8a92a0; font-size: 9px; }.approval-content article>b { font-size: 11px; }.primary-link { display: block; margin-top: 12px; padding: 9px; border-radius: 7px; background: #6557c7; color: #fff; font-size: 9.5px; font-weight: 800; text-align: center; text-decoration: none; }
.activity-list { margin: 0; padding: 16px 20px 19px; list-style: none; }.activity-list li { display: grid; grid-template-columns: 70px 10px 1fr; gap: 9px; min-height: 61px; }.activity-list time { padding-top: 2px; color: #89919f; font-size: 8.5px; }.activity-list i { position: relative; width: 7px; height: 7px; margin-top: 3px; border: 2px solid #fff; border-radius: 50%; background: #6758c5; box-shadow: 0 0 0 1px #6758c5; }.activity-list i:after { position: absolute; top: 8px; left: 2px; width: 1px; height: 45px; background: #e0e3e8; content: ''; }.activity-list li:last-child i:after { display: none; }.activity-list i[data-success="false"] { background: #d48b2f; box-shadow: 0 0 0 1px #d48b2f; }.activity-list a { min-width: 0; color: inherit; text-decoration: none; }.activity-list strong { display: block; font-size: 10px; }.activity-list p { overflow: hidden; margin: 4px 0; color: #7e8795; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }.activity-list a span { color: #6658bd; font-size: 8px; font-weight: 750; text-transform: uppercase; }
.snapshot-head,.ticket-snapshot article { display: grid; grid-template-columns: minmax(150px,1.2fr) 82px 108px 48px 22px; gap: 11px; align-items: center; }.snapshot-head { padding: 9px 18px; background: #f8f9fb; color: #959ca8; font-size: 8px; font-weight: 800; text-transform: uppercase; }.ticket-snapshot article { min-height: 67px; padding: 10px 18px; border-top: 1px solid #edf0f3; font-size: 9px; }.ticket-snapshot article strong { display: block; overflow: hidden; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }.ticket-snapshot article p { overflow: hidden; margin: 4px 0 0; color: #858d9a; text-overflow: ellipsis; white-space: nowrap; }.ticket-snapshot article>span { width: fit-content; padding: 4px 6px; border-radius: 10px; background: #f1f2f5; color: #626c7a; font-weight: 700; }.ticket-snapshot article>span[data-status="running"] { background: #eeecfb; color: #6152bb; }.ticket-snapshot article>span[data-status="waiting_for_approval"] { background: #fff4e4; color: #a9671d; }.ticket-snapshot b { color: #737c8b; font-size: 8.5px; }.ticket-snapshot b[data-risk="high"] { color: #c23e3e; }.ticket-snapshot article>a { color: #6758bf; font-size: 13px; font-weight: 800; text-decoration: none; }
@keyframes spin { to { transform: rotate(360deg); } } @keyframes pulse { to { opacity: .45; } }
@media (max-width: 1200px) { .overview { padding: 30px 26px 48px; }.kpi-grid { grid-template-columns: repeat(3,1fr); }.operations-grid,.insight-grid { grid-template-columns: 1fr; }.approval-healthy { min-height: 120px; }.approval-content { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; }.approval-alert,.primary-link { grid-column: 1/-1; } }
@media (max-width: 760px) { .overview { padding: 24px 18px 40px; }.hero { align-items: flex-start; flex-direction: column; }.hero h1 small { display: block; margin: 5px 0 0; }.kpi-grid { grid-template-columns: repeat(2,1fr); }.attention-list { grid-template-columns: 1fr; }.run-list article { grid-template-columns: 9px 1fr auto; }.run-list article>a { grid-column: 2/-1; }.snapshot-head { display: none; }.ticket-snapshot article { grid-template-columns: 1fr auto; }.ticket-snapshot article>*:not(div):not(a) { grid-column: 1; }.ticket-snapshot article>a { grid-column: 2; grid-row: 1; }.approval-content { display: block; } }
</style>
