<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  AgentRunNotFoundError,
  fetchLatestAgentRun,
  startAgentRun,
  type AgentRunRecord,
} from '../lib/agentRunsApi'
import { fetchAuditEvents, type AuditEvent } from '../lib/approvalsApi'
import {
  fetchTicketDetail,
  TicketNotFoundError,
  type TicketDetailRecord,
} from '../lib/ticketsApi'
import { orderStatusLabel, ticketStatusLabel } from '../lib/ticketStatus'

const props = defineProps<{ ticketKey: string }>()

type LoadState = 'loading' | 'success' | 'not_found' | 'failed'
const loadState = ref<LoadState>('loading')
const ticket = ref<TicketDetailRecord | null>(null)
const agentRun = ref<AgentRunRecord | null>(null)
const auditEvents = ref<AuditEvent[]>([])
const runLoadFailed = ref(false)
const auditLoadFailed = ref(false)
const isStarting = ref(false)
const startError = ref<string | null>(null)

const RUN_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '处理中',
  waiting_for_approval: '等待人工审批',
  completed: '已完成',
  failed: '处理失败',
  cancelled: '已停止',
}
const ACTION_LABELS: Record<string, string> = { replacement: '创建同款换货单' }

const runLabel = computed(() =>
  agentRun.value ? (RUN_LABELS[agentRun.value.status] ?? agentRun.value.status) : '尚未启动',
)
const canStart = computed(() => agentRun.value === null && !runLoadFailed.value)
const isWaitingApproval = computed(() =>
  agentRun.value?.status === 'waiting_for_approval' && agentRun.value.approval_request,
)

function formatTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value.endsWith('Z') ? value : `${value}Z`))
}

function formatAmount(value: string): string {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'CNY',
  }).format(Number(value))
}

function riskLevel(value: string): string {
  return value.toUpperCase()
}

async function loadAudit(run: AgentRunRecord): Promise<void> {
  auditLoadFailed.value = false
  try {
    auditEvents.value = await fetchAuditEvents(run.business_key)
  } catch {
    auditEvents.value = []
    auditLoadFailed.value = true
  }
}

async function loadDetail(businessKey: string): Promise<void> {
  loadState.value = 'loading'
  ticket.value = null
  agentRun.value = null
  auditEvents.value = []
  runLoadFailed.value = false
  startError.value = null
  try {
    ticket.value = await fetchTicketDetail(businessKey)
    try {
      agentRun.value = await fetchLatestAgentRun(businessKey)
      await loadAudit(agentRun.value)
    } catch (error) {
      if (!(error instanceof AgentRunNotFoundError)) runLoadFailed.value = true
    }
    loadState.value = 'success'
  } catch (error) {
    loadState.value = error instanceof TicketNotFoundError ? 'not_found' : 'failed'
  }
}

async function handleStart(): Promise<void> {
  if (!canStart.value || isStarting.value) return
  isStarting.value = true
  startError.value = null
  try {
    const run = await startAgentRun(props.ticketKey)
    agentRun.value = run
    ticket.value = await fetchTicketDetail(props.ticketKey)
    await loadAudit(run)
  } catch (error) {
    startError.value = error instanceof Error ? error.message : 'AI 处理启动失败，请稍后重试。'
  } finally {
    isStarting.value = false
  }
}

onMounted(() => loadDetail(props.ticketKey))
watch(() => props.ticketKey, loadDetail)
</script>

<template>
  <div class="ticket-detail">
    <a class="back-link" href="#/">← 返回 Service Operations</a>

    <div v-if="loadState === 'loading'" class="state-view">正在加载工单详情…</div>
    <div v-else-if="loadState === 'failed'" class="state-view failed">工单详情加载失败，请稍后重试。</div>
    <div v-else-if="loadState === 'not_found'" class="state-view">未找到工单 {{ props.ticketKey }}。</div>

    <template v-else-if="ticket">
      <header class="detail-header">
        <div class="title-block">
          <div class="title-line">
            <span class="eyebrow">Ticket Detail</span>
            <span v-if="ticket.is_demo_data" class="demo-tag">Demo Data</span>
          </div>
          <h1>{{ ticket.subject }}</h1>
          <p><code>{{ ticket.business_key }}</code> · {{ ticket.customer?.name ?? '未关联客户' }}</p>
        </div>
        <div class="primary-action">
          <button v-if="canStart" type="button" :disabled="isStarting" data-testid="start-ai-processing" @click="handleStart">
            {{ isStarting ? '正在启动 AI 处理…' : '启动 AI 处理' }}
          </button>
          <a v-else-if="isWaitingApproval" href="#/approvals">查看审批 →</a>
          <span v-else-if="agentRun" :data-status="agentRun.status">{{ runLabel }}</span>
          <span v-else>执行状态暂不可用</span>
        </div>
      </header>

      <p v-if="isStarting" class="processing-notice" role="status">
        请求处理中；页面将在后端返回后展示真实 AgentRun 状态与执行结果。
      </p>
      <p v-if="startError" class="error-banner" role="alert">{{ startError }}</p>

      <section class="summary-strip" aria-label="工单摘要">
        <div><small>工单状态</small><strong>{{ ticketStatusLabel(ticket.status) }}</strong></div>
        <div><small>AI 状态</small><strong>{{ runLabel }}</strong></div>
        <div><small>创建时间</small><strong>{{ formatTime(ticket.created_at) }}</strong></div>
        <div><small>更新时间</small><strong>{{ formatTime(ticket.updated_at) }}</strong></div>
      </section>

      <div class="content-grid">
        <main>
          <section class="panel">
            <header><span>01</span><div><h2>客户与订单</h2><p>Customer / Order Context</p></div></header>
            <div class="facts-grid">
              <div><small>客户名称</small><strong>{{ ticket.customer?.name ?? '—' }}</strong></div>
              <div><small>客户邮箱</small><strong>{{ ticket.customer?.email ?? '—' }}</strong></div>
              <div><small>订单编号</small><strong>{{ ticket.order?.business_key ?? '—' }}</strong></div>
              <div><small>订单金额</small><strong>{{ ticket.order ? formatAmount(ticket.order.amount) : '—' }}</strong></div>
              <div><small>商品</small><strong>{{ ticket.order?.product_name ?? '—' }}</strong></div>
              <div><small>配送状态</small><strong>{{ ticket.order ? orderStatusLabel(ticket.order.status) : '—' }}</strong></div>
            </div>
          </section>

          <section class="panel">
            <header><span>02</span><div><h2>客户问题</h2><p>Issue</p></div></header>
            <div class="issue-box"><small>问题类型</small><strong>{{ ticket.issue_type ?? ticket.subject }}</strong><p>{{ ticket.description }}</p></div>
          </section>

          <section class="panel">
            <header><span>03</span><div><h2>AI 处理</h2><p>AgentRun</p></div><b v-if="agentRun">{{ runLabel }}</b></header>
            <p v-if="runLoadFailed" class="unavailable">执行记录暂时无法读取。</p>
            <p v-else-if="!agentRun" class="unavailable">尚未启动 AI 处理。</p>
            <template v-else>
              <ol class="steps">
                <li v-for="step in agentRun.steps" :key="step.step_order" :data-status="step.status">
                  <i></i><div><strong>{{ step.name }}</strong><span>{{ step.status }}</span><p v-if="step.error_message">{{ step.error_message }}</p></div>
                </li>
              </ol>
              <p v-if="agentRun.error_message" class="error-banner">{{ agentRun.error_message }}</p>
            </template>
          </section>

          <section class="panel">
            <header><span>04</span><div><h2>AI 建议</h2><p>Recommendation</p></div></header>
            <div v-if="agentRun?.recommendation" class="recommendation">
              <span>✦</span><div><small>建议操作</small><h3>{{ ACTION_LABELS[agentRun.recommendation.action] ?? agentRun.recommendation.action }}</h3>
              <small>客户问题摘要</small><p>{{ agentRun.recommendation.issue_summary }}</p></div>
            </div>
            <p v-else class="unavailable">尚无已验证的 AI 建议。</p>
          </section>

          <section class="panel">
            <header><span>05</span><div><h2>政策依据</h2><p>Policy Basis</p></div></header>
            <template v-if="agentRun?.policy_basis">
              <div class="policy-heading">
                <div><small>Policy Name</small><strong>{{ agentRun.policy_basis.document_title ?? '未命名政策' }}</strong></div>
                <div><small>Applicability</small><strong>{{ agentRun.policy_basis.query_summary }}</strong></div>
              </div>
              <article v-for="passage in agentRun.policy_basis.passages" :key="passage.rank" class="passage">
                <small>Section · {{ passage.chunk_key }} · Match {{ (passage.score * 100).toFixed(1) }}%</small>
                <p>{{ passage.passage }}</p>
              </article>
              <p v-if="agentRun.policy_basis.failure_reason" class="error-banner">{{ agentRun.policy_basis.failure_reason }}</p>
            </template>
            <p v-else class="unavailable">尚无真实政策检索结果。</p>
          </section>
        </main>

        <aside>
          <section class="panel sticky-panel">
            <header><span>06</span><div><h2>风险与审批</h2><p>Risk / Approval</p></div></header>
            <template v-if="agentRun?.risk">
              <div class="risk-level" :data-level="agentRun.risk.level">
                <small>Risk Level</small><strong>{{ riskLevel(agentRun.risk.level) }}</strong>
              </div>
              <dl class="risk-facts">
                <div><dt>Trigger</dt><dd><code>{{ agentRun.risk.rule_code }}</code></dd></div>
                <div><dt>Explanation</dt><dd>{{ agentRun.risk.reason }}</dd></div>
                <div v-if="agentRun.risk.order_amount"><dt>Order Amount</dt><dd>{{ formatAmount(agentRun.risk.order_amount) }}</dd></div>
                <div v-if="agentRun.risk.approval_threshold_amount"><dt>Auto Limit</dt><dd>{{ formatAmount(agentRun.risk.approval_threshold_amount) }}</dd></div>
              </dl>
            </template>
            <p v-else class="unavailable">当前执行没有可展示的持久化风险结果。</p>

            <div v-if="agentRun?.approval_request" class="approval-card" :data-status="agentRun.approval_request.status">
              <small>Approval</small><strong>{{ agentRun.approval_request.status === 'pending' ? '需要人工审批' : `审批已${agentRun.approval_request.status}` }}</strong>
              <code>{{ agentRun.approval_request.approval_key }}</code>
              <a href="#/approvals">查看审批 →</a>
            </div>
          </section>

          <section class="panel">
            <header><span>07</span><div><h2>活动记录</h2><p>Audit</p></div></header>
            <p v-if="auditLoadFailed" class="unavailable">审计记录暂时无法读取。</p>
            <ol v-else-if="auditEvents.length" class="audit-list">
              <li v-for="event in auditEvents" :key="event.event_key" :data-success="event.success">
                <i></i><div><time>{{ formatTime(event.occurred_at) }}</time><strong>{{ event.summary }}</strong><span>{{ event.actor_type }} · {{ event.outcome }}</span></div>
              </li>
            </ol>
            <p v-else class="unavailable">尚无 AgentRun 审计事件。</p>
          </section>
        </aside>
      </div>
    </template>
  </div>
</template>

<style scoped>
* { box-sizing: border-box; }.ticket-detail { max-width: 1380px; margin: 0 auto; padding: 32px 40px 60px; color: #222b3b; }.back-link { display: inline-block; margin-bottom: 22px; color: #6657c3; font-size: 10px; font-weight: 700; text-decoration: none; }
.detail-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; }.title-line { display: flex; align-items: center; gap: 8px; }.eyebrow { color: #6859c6; font-size: 9px; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; }.demo-tag { padding: 3px 6px; border-radius: 5px; background: #fff3d8; color: #9c681c; font-size: 8px; font-weight: 800; }.detail-header h1 { margin: 8px 0 5px; font-size: 27px; letter-spacing: -.03em; }.detail-header p { margin: 0; color: #858d9a; font-size: 10px; }.detail-header code { font: inherit; }.primary-action button,.primary-action a { display: inline-block; padding: 11px 17px; border: 0; border-radius: 8px; background: #6557c8; color: #fff; font: inherit; font-size: 11px; font-weight: 750; text-decoration: none; cursor: pointer; }.primary-action>span { display: inline-block; padding: 8px 11px; border-radius: 15px; background: #eef1f5; color: #596475; font-size: 10px; font-weight: 700; }.primary-action>span[data-status="completed"] { background: #eaf8f2; color: #147a57; }.processing-notice,.error-banner { margin: 16px 0 0; padding: 10px 13px; border-radius: 7px; background: #f0eefb; color: #6256a7; font-size: 10px; }.error-banner { background: #fff0f0; color: #b63c3c; }
.summary-strip { display: grid; grid-template-columns: repeat(4,1fr); margin: 25px 0 20px; border: 1px solid #e0e3e8; border-radius: 10px; background: #fff; }.summary-strip div { padding: 15px 18px; border-right: 1px solid #e8eaee; }.summary-strip div:last-child { border: 0; }.summary-strip small,.summary-strip strong { display: block; }.summary-strip small { color: #9299a6; font-size: 8.5px; font-weight: 700; text-transform: uppercase; }.summary-strip strong { margin-top: 5px; font-size: 11px; }
.content-grid { display: grid; grid-template-columns: minmax(0,1.65fr) minmax(320px,.75fr); gap: 18px; align-items: start; }.panel { margin-bottom: 18px; padding: 20px; border: 1px solid #e0e3e8; border-radius: 11px; background: #fff; }.panel>header { display: flex; align-items: center; gap: 10px; margin-bottom: 17px; }.panel>header>span { display: grid; width: 27px; height: 27px; place-items: center; border-radius: 7px; background: #f0eefb; color: #6657c2; font-size: 9px; font-weight: 800; }.panel h2 { margin: 0; font-size: 13px; }.panel header p { margin: 2px 0 0; color: #9299a6; font-size: 8.5px; }.panel header b { margin-left: auto; padding: 4px 7px; border-radius: 10px; background: #f1f2f5; color: #626c7b; font-size: 8.5px; }.facts-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: 10px; }.facts-grid div { min-width: 0; padding: 12px; border-radius: 8px; background: #f7f8fa; }.facts-grid small,.facts-grid strong { display: block; }.facts-grid small,.issue-box small,.recommendation small,.policy-heading small { color: #949ba8; font-size: 8.5px; font-weight: 700; text-transform: uppercase; }.facts-grid strong { overflow: hidden; margin-top: 5px; font-size: 10.5px; text-overflow: ellipsis; white-space: nowrap; }.issue-box { padding: 15px; border-left: 3px solid #d9d3f6; background: #fafaff; }.issue-box strong { display: block; margin-top: 6px; font-size: 12px; }.issue-box p { margin: 8px 0 0; color: #596273; font-size: 10.5px; line-height: 1.7; }
.steps,.audit-list { margin: 0; padding: 0; list-style: none; }.steps { display: grid; grid-template-columns: repeat(2,1fr); gap: 8px; }.steps li { display: flex; gap: 9px; padding: 11px; border-radius: 8px; background: #f7f8fa; }.steps i { width: 7px; height: 7px; margin-top: 3px; border-radius: 50%; background: #6b5bc6; }.steps li[data-status="failed"] i { background: #c33d3d; }.steps div { min-width: 0; flex: 1; }.steps strong,.steps span { display: block; }.steps strong { font-size: 9.5px; }.steps span { margin-top: 3px; color: #8b93a0; font-size: 8.5px; }.steps p { margin: 5px 0 0; color: #a13a3a; font-size: 8.5px; line-height: 1.4; }
.recommendation { display: flex; gap: 13px; padding: 16px; border: 1px solid #ddd8f5; border-radius: 9px; background: #faf9ff; }.recommendation>span { display: grid; flex: 0 0 auto; place-items: center; width: 34px; height: 34px; border-radius: 9px; background: #6657c7; color: #fff; }.recommendation h3 { margin: 5px 0 13px; font-size: 15px; }.recommendation p { margin: 5px 0 0; color: #646d7c; font-size: 10.5px; line-height: 1.6; }.policy-heading { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px; }.policy-heading div { padding: 11px; border-radius: 8px; background: #f7f8fa; }.policy-heading strong { display: block; margin-top: 5px; font-size: 10px; }.passage { margin-top: 8px; padding: 12px; border: 1px solid #e7e9ed; border-radius: 8px; }.passage small { color: #7063b8; font-size: 8.5px; }.passage p { margin: 7px 0 0; color: #50596a; font-size: 10px; line-height: 1.65; }
.sticky-panel { position: sticky; top: 76px; }.risk-level { display: flex; align-items: center; justify-content: space-between; padding: 13px; border-radius: 8px; background: #fff5e6; }.risk-level[data-level="high"] { background: #fff0f0; color: #c43d3d; }.risk-level small { font-size: 8.5px; font-weight: 800; letter-spacing: .1em; }.risk-level strong { font-size: 17px; }.risk-facts { margin: 12px 0 0; }.risk-facts div { padding: 9px 0; border-bottom: 1px solid #edf0f3; }.risk-facts dt { color: #9299a6; font-size: 8.5px; }.risk-facts dd { margin: 4px 0 0; color: #4f5969; font-size: 9.5px; line-height: 1.55; }.risk-facts code { font-size: 8.5px; }.approval-card { margin-top: 14px; padding: 13px; border: 1px solid #ead6ad; border-radius: 8px; background: #fff9ed; }.approval-card small,.approval-card strong,.approval-card code,.approval-card a { display: block; }.approval-card small { color: #a07227; font-size: 8px; font-weight: 800; text-transform: uppercase; }.approval-card strong { margin-top: 5px; font-size: 11px; }.approval-card code { margin-top: 6px; color: #7e7565; font-size: 8.5px; }.approval-card a { margin-top: 11px; color: #6455bc; font-size: 9.5px; font-weight: 750; text-decoration: none; }
.audit-list li { display: grid; grid-template-columns: 10px 1fr; gap: 9px; min-height: 63px; }.audit-list i { position: relative; width: 7px; height: 7px; margin-top: 4px; border-radius: 50%; background: #6758c4; }.audit-list i:after { position: absolute; top: 9px; left: 3px; width: 1px; height: 47px; background: #dfe2e8; content: ''; }.audit-list li:last-child i:after { display: none; }.audit-list li[data-success="false"] i { background: #d48834; }.audit-list time,.audit-list strong,.audit-list span { display: block; }.audit-list time { color: #949ba7; font-size: 8px; }.audit-list strong { margin-top: 3px; font-size: 9.5px; line-height: 1.4; }.audit-list span { margin-top: 3px; color: #6d60b4; font-size: 8px; text-transform: uppercase; }.unavailable { margin: 0; padding: 13px; border-radius: 8px; background: #f6f7f9; color: #858d9b; font-size: 9.5px; }.state-view { display: grid; min-height: 300px; place-items: center; color: #7c8594; font-size: 12px; }.state-view.failed { color: #b53b3b; }
button:disabled { cursor: wait; opacity: .6; }
@media (max-width: 1000px) { .ticket-detail { padding: 28px 22px; }.content-grid { grid-template-columns: 1fr; }.sticky-panel { position: static; }.summary-strip { grid-template-columns: repeat(2,1fr); }.facts-grid { grid-template-columns: repeat(2,1fr); } }
</style>
