<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  AgentRunNotFoundError,
  fetchAgentRun,
  type AgentRunCenterItem,
  type AgentRunRecord,
} from '../lib/agentRunsApi'
import { fetchAuditEvents, type AuditEvent } from '../lib/approvalsApi'
import { orderStatusLabel, ticketStatusLabel } from '../lib/ticketStatus'

const props = defineProps<{ runKey: string }>()
type LoadState = 'loading' | 'ready' | 'not_found' | 'failed'

const loadState = ref<LoadState>('loading')
const item = ref<AgentRunCenterItem | null>(null)
const events = ref<AuditEvent[]>([])
const auditFailed = ref(false)

const ACTION_LABELS: Record<string, string> = {
  retrieve_policy_passages: '检索售后政策',
  get_order: '查询订单',
  check_inventory: '检查换货库存',
  decide_replacement: '评估换货资格',
  assess_replacement_risk: '风险评估',
  create_replacement: '创建换货单',
  update_ticket: '回写工单结果',
}

const EVENT_LABELS: Record<string, string> = {
  policy_retrieved: '政策检索完成',
  decision_produced: '换货资格评估完成',
  get_order: '订单查询完成',
  check_inventory: '库存检查完成',
  risk_gate: '风险评估完成',
  approval_request_created: '已请求人工审批',
  approval_approved: '人工审批通过',
  approval_rejected: '人工审批拒绝',
  create_replacement: '换货单操作',
  update_ticket: '工单结果回写',
  agent_run_outcome: '执行结束',
}

const BUSINESS_EVENT_TYPES = new Set([
  'policy_retrieved',
  'get_order',
  'check_inventory',
  'decision_produced',
  'risk_gate',
  'create_replacement',
  'update_ticket',
])

const run = computed(() => item.value?.agent_run ?? null)
const ticket = computed(() => item.value?.ticket ?? null)
const businessActions = computed(() =>
  events.value.filter(event => BUSINESS_EVENT_TYPES.has(event.event_type)),
)

interface ProgressItem {
  key: string
  name: string
  state: string
  label: string
  detail: string | null
  occurredAt: string
  sequence: number
}

const progressItems = computed<ProgressItem[]>(() => {
  if (!run.value) return []
  const result: ProgressItem[] = run.value.steps.map((step, index) => ({
    key: `step-${step.step_order}`,
    name: step.name,
    state: step.status === 'failed' && policyBlocked.value ? 'blocked' : step.status,
    label: ({
      pending: '待执行',
      running: '执行中',
      completed: '已完成',
      failed: policyBlocked.value ? '政策阻止' : '失败',
      skipped: '已跳过',
    } as Record<string, string>)[step.status] ?? step.status,
    detail: step.error_message
      ? (step.status === 'failed' && policyBlocked.value
        ? '业务政策阻止了本次操作'
        : conciseReason(step.error_message))
      : null,
    occurredAt: step.started_at ?? step.completed_at ?? run.value!.created_at,
    sequence: index,
  }))

  const approvalEvents = events.value.filter(event =>
    event.event_type === 'approval_request_created' ||
    event.event_type === 'approval_approved' ||
    event.event_type === 'approval_rejected',
  )
  for (const event of approvalEvents) {
    const requested = event.event_type === 'approval_request_created'
    const rejectedEvent = event.event_type === 'approval_rejected'
    result.push({
      key: event.event_key,
      name: requested ? '请求人工审批' : rejectedEvent ? '人工拒绝' : '人工批准',
      state: requested && waiting.value ? 'waiting_human' : rejectedEvent ? 'blocked' : 'completed',
      label: requested && waiting.value ? '等待人工' : rejectedEvent ? '已拒绝' : requested ? '已请求' : '已批准',
      detail: event.summary,
      occurredAt: event.occurred_at,
      sequence: result.length,
    })
  }

  if (run.value.approval_request && !approvalEvents.length) {
    const approval = run.value.approval_request
    result.push({
      key: approval.approval_key,
      name: '人工审批',
      state: approval.status === 'pending' ? 'waiting_human' : approval.status === 'rejected' ? 'blocked' : 'completed',
      label: approval.status === 'pending' ? '等待人工' : approval.status === 'approved' ? '已批准' : '已拒绝',
      detail: approval.decision_reason ?? null,
      occurredAt: approval.resolved_at ?? approval.created_at,
      sequence: result.length,
    })
  }

  if (rejected.value && !run.value.replacement) {
    result.push({
      key: 'protected-action-not-executed',
      name: '执行受保护操作',
      state: 'skipped',
      label: '未执行',
      detail: '人工拒绝后按政策停止',
      occurredAt: run.value.approval_request?.resolved_at ?? run.value.created_at,
      sequence: result.length + 1,
    })
  }

  return result.sort((left, right) =>
    Date.parse(left.occurredAt) - Date.parse(right.occurredAt) ||
    left.sequence - right.sequence,
  )
})
const policyBlocked = computed(() => {
  const message = run.value?.error_message?.toLowerCase() ?? ''
  return message.includes('replacement_window_expired') ||
    message.includes('blocked') ||
    events.value.some(event =>
      event.event_type === 'decision_produced' && event.outcome === 'blocked',
    )
})
const rejected = computed(() => run.value?.approval_request?.status === 'rejected')
const waiting = computed(() => run.value?.approval_request?.status === 'pending')
const statusLabel = computed(() => {
  if (!run.value) return ''
  if (rejected.value) return '人工拒绝'
  if (policyBlocked.value) return '政策阻止'
  return ({
    queued: '等待开始',
    running: 'AI 处理中',
    waiting_for_approval: '等待人工审批',
    completed: '已完成',
    failed: '执行失败',
    cancelled: '已停止',
  } as Record<string, string>)[run.value.status] ?? run.value.status
})
const currentStep = computed(() => {
  if (!run.value) return '—'
  if (waiting.value) return '人工审批'
  if (rejected.value) return '受保护操作已停止'
  const active = [...run.value.steps].reverse().find(step =>
    step.status === 'running' || step.status === 'failed',
  )
  if (active) return active.name
  if (run.value.status === 'completed') return '执行完成'
  return run.value.steps[run.value.steps.length - 1]?.name ?? '尚未记录步骤'
})
const finalTitle = computed(() => {
  if (!run.value) return '尚无结果'
  if (run.value.replacement) return '换货单创建成功'
  if (rejected.value) return '人工拒绝'
  if (policyBlocked.value) return '业务政策阻止'
  if (run.value.status === 'failed') return '执行未能完成'
  if (run.value.status === 'cancelled') return '执行已停止'
  if (waiting.value) return '等待人工决定'
  return '执行尚未结束'
})
const finalDescription = computed(() => {
  if (!run.value) return ''
  if (run.value.replacement) {
    return run.value.ticket_result.resolution_summary ||
      `已创建换货单 ${run.value.replacement.business_key} 并回写工单。`
  }
  if (rejected.value) return '受保护操作未执行；执行已按人工决定停止。'
  if (policyBlocked.value) return '当前业务条件不符合企业政策要求；AI 未绕过规则，这不是系统故障。'
  if (run.value.status === 'failed') return conciseReason(run.value.error_message) || '执行失败，未产生可确认的最终业务结果。'
  if (waiting.value) return '受保护操作尚未执行，等待人工审批。'
  return run.value.ticket_result.resolution_summary || '暂无最终业务结果。'
})

function conciseReason(value: string | null): string {
  return value?.split(/\r?\n/, 1)[0] ?? ''
}

const RULE_CODE_LABELS: Record<string, string> = {
  order_amount_above_approval_threshold: '订单金额超过人工审批阈值',
  no_rule_triggered: '未命中风险规则',
}

function ruleCodeLabel(code: string): string {
  return RULE_CODE_LABELS[code] ?? code
}

function duration(value: AgentRunRecord): string {
  const start = value.started_at ?? value.created_at
  const end = value.completed_at ?? new Date().toISOString()
  const seconds = Math.max(0, Math.floor((Date.parse(end) - Date.parse(start)) / 1000))
  const minutes = Math.floor(seconds / 60)
  return minutes ? `${minutes}m ${seconds % 60}s` : `${seconds}s`
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value.endsWith('Z') ? value : `${value}Z`))
}

function formatAmount(value: string): string {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'CNY',
  }).format(Number(value))
}

function actionLabel(event: AuditEvent): string {
  return ACTION_LABELS[event.action] ?? EVENT_LABELS[event.event_type] ?? event.summary
}

function eventLabel(event: AuditEvent): string {
  return EVENT_LABELS[event.event_type] ?? event.summary
}

async function loadDetail(runKey: string): Promise<void> {
  loadState.value = 'loading'
  item.value = null
  events.value = []
  auditFailed.value = false
  try {
    item.value = await fetchAgentRun(runKey)
    try {
      events.value = await fetchAuditEvents(runKey)
    } catch {
      auditFailed.value = true
    }
    loadState.value = 'ready'
  } catch (error) {
    loadState.value = error instanceof AgentRunNotFoundError ? 'not_found' : 'failed'
  }
}

onMounted(() => loadDetail(props.runKey))
watch(() => props.runKey, loadDetail)
</script>

<template>
  <div class="run-detail">
    <a class="back-link" href="#/agent-runs">← 返回 Agent Runs</a>

    <div v-if="loadState === 'loading'" class="state-view">正在加载执行详情…</div>
    <div v-else-if="loadState === 'not_found'" class="state-view">未找到 Agent Run {{ props.runKey }}。</div>
    <div v-else-if="loadState === 'failed'" class="state-view failed">执行详情加载失败，请稍后重试。</div>

    <template v-else-if="run && ticket">
      <header class="detail-header">
        <div>
          <span class="eyebrow">AGENT RUN DETAIL</span>
          <h1>{{ run.business_key }}</h1>
          <p>{{ ticket.subject }} · {{ ticket.customer?.name ?? '未关联客户' }}</p>
        </div>
        <span class="hero-status" :data-status="run.status" :data-rejected="rejected" :data-blocked="policyBlocked">{{ statusLabel }}</span>
      </header>

      <aside v-if="waiting" class="attention-banner">
        <div><strong>需要人工审批</strong><p>{{ run.risk?.reason }}</p></div>
        <a :href="`#/approvals?approval=${run.approval_request?.approval_key}`">查看审批 →</a>
      </aside>
      <aside v-else-if="rejected" class="attention-banner rejected">
        <div><strong>Human Decision · Rejected</strong><p>Execution · Stopped by policy</p></div>
        <a :href="`#/approvals?approval=${run.approval_request?.approval_key}`">查看审批 →</a>
      </aside>

      <section class="summary-strip" aria-label="Run Summary">
        <div><small>Run ID</small><strong>{{ run.business_key }}</strong></div>
        <div><small>Ticket ID</small><strong>{{ run.ticket_key }}</strong></div>
        <div><small>Started</small><strong>{{ formatTime(run.started_at ?? run.created_at) }}</strong></div>
        <div><small>Completed</small><strong>{{ formatTime(run.completed_at) }}</strong></div>
        <div><small>Duration</small><strong>{{ duration(run) }}</strong></div>
        <div><small>Current Step</small><strong>{{ currentStep }}</strong></div>
      </section>

      <div class="content-grid">
        <main>
          <section class="panel">
            <header><span>01</span><div><h2>工单上下文</h2><p>Ticket Context</p></div><a :href="`#/tickets/${ticket.business_key}`">查看工单 →</a></header>
            <div class="facts-grid">
              <div><small>Customer</small><strong>{{ ticket.customer?.name ?? '—' }}</strong></div>
              <div><small>Issue</small><strong>{{ ticket.issue_type ?? ticket.subject }}</strong></div>
              <div><small>Order ID</small><strong>{{ ticket.order?.business_key ?? '—' }}</strong></div>
              <div><small>Amount</small><strong>{{ ticket.order ? formatAmount(ticket.order.amount) : '—' }}</strong></div>
              <div><small>Order Status</small><strong>{{ ticket.order ? orderStatusLabel(ticket.order.status) : '—' }}</strong></div>
              <div><small>Ticket Status</small><strong>{{ ticketStatusLabel(ticket.status) }}</strong></div>
            </div>
            <p class="issue-description">{{ ticket.description }}</p>
          </section>

          <section class="panel">
            <header><span>02</span><div><h2>执行进度</h2><p>Execution Progress · 只展示真实记录</p></div></header>
            <ol v-if="progressItems.length" class="stepper">
              <li v-for="progress in progressItems" :key="progress.key" :data-state="progress.state">
                <i></i>
                <div><strong>{{ progress.name }}</strong><p v-if="progress.detail">{{ progress.detail }}</p></div>
                <span>{{ progress.label }}</span>
              </li>
            </ol>
            <p v-else class="unavailable">该 Run 尚未记录细粒度步骤，当前状态为 {{ statusLabel }}。</p>
          </section>

          <section class="panel">
            <header><span>03</span><div><h2>业务动作</h2><p>Business Actions</p></div></header>
            <div v-if="businessActions.length" class="action-table">
              <div class="table-head"><span>Action</span><span>Result</span><span>Time</span></div>
              <div v-for="event in businessActions" :key="event.event_key" class="action-row" :data-success="event.success">
                <strong>{{ actionLabel(event) }}</strong>
                <span>{{ event.summary }}</span>
                <time>{{ formatTime(event.occurred_at) }}</time>
              </div>
            </div>
            <p v-else class="unavailable">没有可展示的已记录业务动作。</p>
          </section>

          <section class="panel">
            <header><span>04</span><div><h2>政策依据</h2><p>Policy Basis</p></div></header>
            <template v-if="run.policy_basis">
              <div class="policy-heading">
                <div><small>Policy</small><strong>{{ run.policy_basis.document_title ?? '未命名政策' }}</strong></div>
                <div><small>Applicability</small><strong>{{ run.policy_basis.query_summary }}</strong></div>
              </div>
              <article v-for="passage in run.policy_basis.passages" :key="passage.rank" class="passage">
                <small>Section · {{ passage.chunk_key }} · Match {{ (passage.score * 100).toFixed(1) }}%</small>
                <p>{{ passage.passage }}</p>
              </article>
              <p v-if="run.policy_basis.failure_reason" class="error-note">{{ run.policy_basis.failure_reason }}</p>
            </template>
            <p v-else class="unavailable">本次执行没有已记录的政策检索结果。</p>
          </section>
        </main>

        <aside>
          <section class="panel sticky-panel">
            <header><span>05</span><div><h2>风险与审批</h2><p>Risk / Approval</p></div></header>
            <template v-if="run.risk">
              <div class="risk-level" :data-level="run.risk.level"><small>Risk Level</small><strong>{{ run.risk.level.toUpperCase() }}</strong></div>
              <dl class="risk-facts">
                <div><dt>Trigger</dt><dd>{{ ruleCodeLabel(run.risk.rule_code) }}</dd></div>
                <div><dt>Explanation</dt><dd>{{ run.risk.reason }}</dd></div>
                <div v-if="run.risk.approval_threshold_amount"><dt>Threshold</dt><dd>{{ formatAmount(run.risk.approval_threshold_amount) }}</dd></div>
                <div v-if="run.risk.order_amount"><dt>Actual Value</dt><dd>{{ formatAmount(run.risk.order_amount) }}</dd></div>
                <div><dt>Result</dt><dd>{{ run.risk.requires_approval ? 'Human Approval Required' : 'No Approval Required' }}</dd></div>
              </dl>
            </template>
            <p v-else class="unavailable">本次执行没有持久化风险判断。</p>
            <div v-if="run.approval_request" class="approval-state" :data-status="run.approval_request.status">
              <small>Human Decision</small>
              <strong>{{ run.approval_request.status === 'pending' ? 'Waiting' : run.approval_request.status === 'approved' ? 'Approved' : 'Rejected' }}</strong>
              <p v-if="run.approval_request.decision_reason">{{ run.approval_request.decision_reason }}</p>
              <a :href="`#/approvals?approval=${run.approval_request.approval_key}`">查看审批 →</a>
            </div>
          </section>

          <section class="panel final-result" :data-kind="rejected ? 'rejected' : policyBlocked ? 'blocked' : run.status">
            <header><span>06</span><div><h2>最终结果</h2><p>Final Result</p></div></header>
            <strong>{{ finalTitle }}</strong>
            <p>{{ finalDescription }}</p>
            <dl v-if="run.replacement">
              <div><dt>Replacement ID</dt><dd>{{ run.replacement.business_key }}</dd></div>
              <div><dt>Product SKU</dt><dd>{{ run.replacement.product_sku }}</dd></div>
            </dl>
            <dl v-else-if="policyBlocked && run.policy_basis?.document_key">
              <div><dt>Policy</dt><dd>{{ run.policy_basis.document_key }}</dd></div>
            </dl>
          </section>

          <section class="panel">
            <header><span>07</span><div><h2>审计时间线</h2><p>Audit Timeline</p></div></header>
            <p v-if="auditFailed" class="unavailable">审计记录暂时无法读取。</p>
            <ol v-else-if="events.length" class="audit-list">
              <li v-for="event in events" :key="event.event_key" :data-success="event.success">
                <i></i><div><time>{{ formatTime(event.occurred_at) }}</time><strong>{{ eventLabel(event) }}</strong><p>{{ event.summary }}</p><span>{{ event.outcome }}</span></div>
              </li>
            </ol>
            <p v-else class="unavailable">尚无已记录审计事件。</p>
          </section>
        </aside>
      </div>
    </template>
  </div>
</template>

<style scoped>
* { box-sizing: border-box; }.run-detail { max-width: 1380px; margin: 0 auto; padding: 32px 40px 64px; color: #222b3b; }.back-link { display: inline-block; margin-bottom: 22px; color: #6657c3; font-size: 10px; font-weight: 700; text-decoration: none; }
.detail-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; }.eyebrow { color: #6859c6; font-size: 9px; font-weight: 800; letter-spacing: .14em; }.detail-header h1 { margin: 8px 0 5px; font-size: 27px; letter-spacing: -.03em; }.detail-header p { margin: 0; color: #858d9a; font-size: 10px; }
.hero-status { padding: 8px 12px; border-radius: 16px; background: #eef1f5; color: #596475; font-size: 10px; font-weight: 750; }.hero-status[data-status="completed"] { background: #eaf8f2; color: #147a57; }.hero-status[data-status="waiting_for_approval"] { background: #fff3dd; color: #9c6418; }.hero-status[data-status="failed"] { background: #fff0f0; color: #b63d3d; }.hero-status[data-rejected="true"] { background: #f6eded; color: #a54b4b; }.hero-status[data-blocked="true"] { background: #fff5e6; color: #a46918; }
.attention-banner { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-top: 20px; padding: 14px 17px; border: 1px solid #ead5aa; border-radius: 9px; background: #fff8e9; }.attention-banner strong { font-size: 12px; }.attention-banner p { margin: 4px 0 0; color: #796b55; font-size: 9.5px; }.attention-banner a { padding: 8px 11px; border-radius: 7px; background: #6758c5; color: #fff; font-size: 9.5px; font-weight: 750; text-decoration: none; }.attention-banner.rejected { border-color: #ead2d2; background: #fff6f6; }
.summary-strip { display: grid; grid-template-columns: repeat(6,1fr); margin: 20px 0; border: 1px solid #e0e3e8; border-radius: 10px; background: #fff; }.summary-strip div { min-width: 0; padding: 14px 15px; border-right: 1px solid #e8eaee; }.summary-strip div:last-child { border: 0; }.summary-strip small,.summary-strip strong { display: block; }.summary-strip small { color: #9299a6; font-size: 8px; font-weight: 700; text-transform: uppercase; }.summary-strip strong { overflow: hidden; margin-top: 5px; font-size: 9.5px; text-overflow: ellipsis; white-space: nowrap; }
.content-grid { display: grid; grid-template-columns: minmax(0,1.55fr) minmax(330px,.78fr); gap: 18px; align-items: start; }.panel { margin-bottom: 18px; padding: 20px; border: 1px solid #e0e3e8; border-radius: 11px; background: #fff; }.panel>header { display: flex; align-items: center; gap: 10px; margin-bottom: 17px; }.panel>header>span { display: grid; width: 27px; height: 27px; place-items: center; border-radius: 7px; background: #f0eefb; color: #6657c2; font-size: 9px; font-weight: 800; }.panel h2 { margin: 0; font-size: 13px; }.panel header p { margin: 2px 0 0; color: #9299a6; font-size: 8.5px; }.panel header a { margin-left: auto; color: #6253b7; font-size: 9.5px; font-weight: 750; text-decoration: none; }
.facts-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: 9px; }.facts-grid div,.policy-heading div { min-width: 0; padding: 11px; border-radius: 8px; background: #f7f8fa; }.facts-grid small,.facts-grid strong,.policy-heading small,.policy-heading strong { display: block; }.facts-grid small,.policy-heading small { color: #949ba8; font-size: 8px; font-weight: 700; text-transform: uppercase; }.facts-grid strong,.policy-heading strong { overflow: hidden; margin-top: 5px; font-size: 9.5px; text-overflow: ellipsis; white-space: nowrap; }.issue-description { margin: 11px 0 0; padding: 12px; border: 1px solid #e9e6f6; background: #fafaff; color: #596273; font-size: 10px; line-height: 1.65; }
.stepper,.audit-list { margin: 0; padding: 0; list-style: none; }.stepper li { display: grid; grid-template-columns: 13px 1fr auto; gap: 10px; min-height: 48px; }.stepper i,.audit-list i { position: relative; width: 8px; height: 8px; margin-top: 3px; border-radius: 50%; background: #6a5bc5; }.stepper i:after,.audit-list i:after { position: absolute; top: 10px; left: 3px; width: 1px; height: 34px; background: #dfe2e8; content: ''; }.stepper li:last-child i:after,.audit-list li:last-child i:after { display: none; }.stepper li[data-state="failed"] i { background: #c43f3f; }.stepper li[data-state="blocked"] i { background: #d48b2d; }.stepper li[data-state="waiting_human"] i { background: #d48b2d; box-shadow: 0 0 0 4px #fff1d9; }.stepper li[data-state="skipped"] i { background: #a5abb5; }.stepper strong { display: block; font-size: 10px; }.stepper p { margin: 4px 0 0; color: #8b6440; font-size: 8.5px; line-height: 1.4; }.stepper>li>span { color: #7a8290; font-size: 8.5px; font-weight: 700; }
.action-table { border: 1px solid #e7e9ed; border-radius: 8px; overflow: hidden; }.table-head,.action-row { display: grid; grid-template-columns: 1fr 1.7fr 1fr; gap: 12px; align-items: center; padding: 10px 12px; }.table-head { background: #f7f8fa; color: #949ba7; font-size: 8px; font-weight: 750; text-transform: uppercase; }.action-row { border-top: 1px solid #eceef1; }.action-row strong,.action-row span,.action-row time { font-size: 9px; }.action-row span { color: #626b7a; line-height: 1.45; }.action-row time { color: #8c94a1; }.action-row[data-success="false"] strong { color: #ad4b3e; }
.policy-heading { display: grid; grid-template-columns: 1fr 1fr; gap: 9px; }.passage { margin-top: 8px; padding: 12px; border: 1px solid #e7e9ed; border-radius: 8px; }.passage small { color: #7063b8; font-size: 8px; }.passage p { margin: 7px 0 0; color: #50596a; font-size: 9.5px; line-height: 1.65; }
.sticky-panel { position: sticky; top: 76px; }.risk-level { display: flex; align-items: center; justify-content: space-between; padding: 12px; border-radius: 8px; background: #edf8f4; }.risk-level[data-level="high"] { background: #fff0f0; color: #c43d3d; }.risk-level small { font-size: 8px; font-weight: 800; letter-spacing: .1em; }.risk-level strong { font-size: 17px; }.risk-facts { margin: 10px 0 0; }.risk-facts div { padding: 8px 0; border-bottom: 1px solid #edf0f3; }.risk-facts dt { color: #9299a6; font-size: 8px; }.risk-facts dd { margin: 4px 0 0; color: #4f5969; font-size: 9px; line-height: 1.5; }
.approval-state { margin-top: 13px; padding: 12px; border: 1px solid #ead6ad; border-radius: 8px; background: #fff9ed; }.approval-state[data-status="approved"] { border-color: #cce8dc; background: #f0faf6; }.approval-state[data-status="rejected"] { border-color: #ead1d1; background: #fff5f5; }.approval-state small,.approval-state strong,.approval-state p,.approval-state a { display: block; }.approval-state small { color: #8e7650; font-size: 8px; text-transform: uppercase; }.approval-state strong { margin-top: 4px; font-size: 12px; }.approval-state p { margin: 6px 0 0; color: #6c7481; font-size: 9px; line-height: 1.5; }.approval-state a { margin-top: 9px; color: #6253b7; font-size: 9px; font-weight: 750; text-decoration: none; }
.final-result>strong { display: block; font-size: 16px; }.final-result>p { margin: 7px 0 0; color: #687180; font-size: 9.5px; line-height: 1.6; }.final-result[data-kind="completed"] { border-color: #cfe7dc; }.final-result[data-kind="rejected"],.final-result[data-kind="blocked"] { border-color: #ead8b8; }.final-result dl { margin: 12px 0 0; padding-top: 10px; border-top: 1px solid #eceef1; }.final-result dl div { display: flex; justify-content: space-between; gap: 10px; margin-top: 6px; }.final-result dt { color: #9299a6; font-size: 8px; }.final-result dd { margin: 0; font-size: 8.5px; font-weight: 700; }
.audit-list li { display: grid; grid-template-columns: 11px 1fr; gap: 9px; min-height: 68px; }.audit-list i:after { height: 53px; }.audit-list li[data-success="false"] i { background: #d48834; }.audit-list time,.audit-list strong,.audit-list p,.audit-list span { display: block; }.audit-list time { color: #949ba7; font-size: 7.5px; }.audit-list strong { margin-top: 3px; font-size: 9.5px; }.audit-list p { margin: 3px 0; color: #697281; font-size: 8.5px; line-height: 1.4; }.audit-list span { color: #695abe; font-size: 8px; text-transform: uppercase; }
.unavailable { margin: 0; padding: 13px; border-radius: 8px; background: #f6f7f9; color: #858d9b; font-size: 9.5px; }.error-note { padding: 10px; border-radius: 7px; background: #fff2f2; color: #ab4141; font-size: 9px; }.state-view { display: grid; min-height: 300px; place-items: center; color: #7c8594; font-size: 11px; }.state-view.failed { color: #b53b3b; }
@media (max-width: 1050px) { .run-detail { padding: 28px 22px; }.content-grid { grid-template-columns: 1fr; }.sticky-panel { position: static; }.summary-strip { grid-template-columns: repeat(3,1fr); }.facts-grid { grid-template-columns: repeat(2,1fr); } }
</style>
