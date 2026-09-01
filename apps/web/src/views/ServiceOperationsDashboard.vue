<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  createTicket,
  fetchTickets,
  type CreateTicketInput,
  type TicketRecord,
} from '../lib/ticketsApi'
import { ticketStatusLabel } from '../lib/ticketStatus'

type LoadState = 'loading' | 'success' | 'empty' | 'failed'

const loadState = ref<LoadState>('loading')
const tickets = ref<TicketRecord[]>([])
const showIntake = ref(false)
const isCreating = ref(false)
const createError = ref<string | null>(null)
const creationSuccess = ref<string | null>(null)

const form = reactive<CreateTicketInput>({
  customer_name: '',
  customer_email: '',
  issue_type: '商品损坏',
  issue_description: '',
  order_id: '',
  order_amount: 899,
})

const metrics = computed(() => [
  { label: '全部工单', value: tickets.value.length },
  {
    label: '待处理',
    value: tickets.value.filter(
      (ticket) => ticket.status === 'open' && ticket.agent_run_status === null,
    ).length,
  },
  {
    label: 'AI 处理中',
    value: tickets.value.filter((ticket) =>
      ticket.agent_run_status === 'queued' || ticket.agent_run_status === 'running',
    ).length,
  },
  {
    label: '等待人工审批',
    value: tickets.value.filter(
      (ticket) => ticket.agent_run_status === 'waiting_for_approval',
    ).length,
  },
  {
    label: '已完成',
    value: tickets.value.filter(
      (ticket) => ticket.status === 'resolved' || ticket.status === 'closed',
    ).length,
  },
])

const RUN_STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '处理中',
  waiting_for_approval: '等待人工审批',
  completed: '已完成',
  failed: '处理失败',
  cancelled: '已停止',
}

function runStatusLabel(status: string | null): string {
  return status ? (RUN_STATUS_LABELS[status] ?? status) : '尚未启动'
}

function formatAmount(value: string | null): string {
  if (value === null) return '—'
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'CNY',
  }).format(Number(value))
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value.endsWith('Z') ? value : `${value}Z`))
}

async function loadTickets(): Promise<void> {
  loadState.value = 'loading'
  try {
    tickets.value = await fetchTickets()
    loadState.value = tickets.value.length === 0 ? 'empty' : 'success'
  } catch {
    tickets.value = []
    loadState.value = 'failed'
  }
}

function openIntake(): void {
  createError.value = null
  creationSuccess.value = null
  showIntake.value = true
}

function closeIntake(): void {
  if (!isCreating.value) showIntake.value = false
}

async function submitTicket(): Promise<void> {
  if (isCreating.value) return
  isCreating.value = true
  createError.value = null
  try {
    const created = await createTicket({ ...form })
    tickets.value = [
      {
        ...created,
        customer_name: created.customer?.name ?? null,
        order_key: created.order?.business_key ?? null,
        order_amount: created.order?.amount ?? null,
        agent_run_key: null,
        agent_run_status: null,
        risk_level: null,
      },
      ...tickets.value,
    ]
    showIntake.value = false
    creationSuccess.value = `工单已创建：${created.business_key}`
    window.setTimeout(() => {
      window.location.hash = `#/tickets/${encodeURIComponent(created.business_key)}`
    }, 700)
  } catch (error) {
    createError.value = error instanceof Error ? error.message : '工单创建失败，请稍后重试。'
  } finally {
    isCreating.value = false
  }
}

onMounted(loadTickets)
</script>

<template>
  <div class="dashboard">
    <header class="page-heading">
      <div>
        <span class="eyebrow">Service Operations</span>
        <h1>服务运营中心</h1>
        <p>统一查看、创建并跟踪 AI 售后工单。</p>
      </div>
      <div class="heading-actions">
        <a href="#/approvals">Approval Inbox</a>
        <button class="primary-button" type="button" data-testid="create-ticket-entry" @click="openIntake">
          ＋ 新建工单
        </button>
      </div>
    </header>

    <p v-if="creationSuccess" class="success-banner" role="status">
      ✓ {{ creationSuccess }}，正在打开详情…
    </p>

    <section class="metrics" aria-label="真实工单统计">
      <article v-for="metric in metrics" :key="metric.label">
        <strong>{{ metric.value }}</strong>
        <span>{{ metric.label }}</span>
      </article>
    </section>

    <section class="ticket-panel" aria-label="工单列表">
      <header>
        <div>
          <h2>售后工单</h2>
          <p>状态、AI 进度与风险均来自当前持久化记录</p>
        </div>
        <button type="button" class="refresh-button" @click="loadTickets">刷新</button>
      </header>

      <div v-if="loadState === 'loading'" class="state-view">正在加载工单…</div>
      <div v-else-if="loadState === 'failed'" class="state-view failed">
        <p>工单加载失败，请稍后重试。</p>
        <button type="button" @click="loadTickets">重新加载</button>
      </div>
      <div v-else-if="loadState === 'empty'" class="state-view">
        <p>当前没有工单，创建第一张售后工单开始处理。</p>
        <button class="primary-button" type="button" @click="openIntake">新建工单</button>
      </div>
      <div v-else class="ticket-table">
        <div class="table-header">
          <span>客户 / 工单</span><span>问题</span><span>订单 / 金额</span>
          <span>工单状态</span><span>AI 状态</span><span>风险</span><span>更新时间</span><span></span>
        </div>
        <article v-for="ticket in tickets" :key="ticket.business_key" class="ticket-row">
          <div class="customer-cell">
            <span class="avatar">{{ (ticket.customer_name ?? '?').slice(0, 1) }}</span>
            <div><strong>{{ ticket.customer_name ?? '未关联客户' }}</strong><code>{{ ticket.business_key }}</code></div>
          </div>
          <div class="issue-cell">
            <strong>{{ ticket.issue_type ?? ticket.subject }}</strong>
            <span>{{ ticket.description }}</span>
          </div>
          <div><strong>{{ ticket.order_key ?? '—' }}</strong><span>{{ formatAmount(ticket.order_amount) }}</span></div>
          <span class="status-pill">{{ ticketStatusLabel(ticket.status) }}</span>
          <span class="run-status" :data-status="ticket.agent_run_status">
            {{ runStatusLabel(ticket.agent_run_status) }}
          </span>
          <span v-if="ticket.risk_level" class="risk-pill" :data-level="ticket.risk_level">
            {{ ticket.risk_level.toUpperCase() }}
          </span>
          <span v-else>—</span>
          <time :datetime="ticket.updated_at">{{ formatTime(ticket.updated_at) }}</time>
          <a class="detail-link" :href="`#/tickets/${encodeURIComponent(ticket.business_key)}`">查看详情 →</a>
        </article>
      </div>
    </section>

    <div v-if="showIntake" class="modal-layer" role="presentation" @click.self="closeIntake">
      <section class="intake-modal" role="dialog" aria-modal="true" aria-labelledby="intake-title">
        <header>
          <div><span class="eyebrow">Ticket Intake</span><h2 id="intake-title">新建售后工单</h2></div>
          <button type="button" aria-label="关闭" :disabled="isCreating" @click="closeIntake">×</button>
        </header>
        <form @submit.prevent="submitTicket">
          <div class="form-grid">
            <label>客户名称<input v-model.trim="form.customer_name" required maxlength="128" /></label>
            <label>客户邮箱<input v-model.trim="form.customer_email" required maxlength="255" type="email" /></label>
            <label>问题类型
              <select v-model="form.issue_type" required>
                <option value="商品损坏">商品损坏</option>
                <option value="换货">换货</option>
              </select>
            </label>
            <label>订单号<input v-model.trim="form.order_id" required maxlength="64" pattern="[A-Za-z0-9][A-Za-z0-9_-]*" placeholder="ORD-20260901-002" /></label>
            <label class="full-field">问题描述<textarea v-model.trim="form.issue_description" required maxlength="4000" rows="5" placeholder="请描述客户遇到的问题与希望的处理方式"></textarea></label>
            <label>订单金额（元）<input v-model.number="form.order_amount" required type="number" min="0.01" max="99999999.99" step="0.01" /></label>
          </div>
          <p class="form-note">金额超过现有政策自动授权额度时，将由真实 Risk Gate 创建审批请求。</p>
          <p v-if="createError" class="form-error" role="alert">{{ createError }}</p>
          <footer>
            <button type="button" :disabled="isCreating" @click="closeIntake">取消</button>
            <button class="primary-button" type="submit" :disabled="isCreating">
              {{ isCreating ? '正在创建…' : '创建工单' }}
            </button>
          </footer>
        </form>
      </section>
    </div>
  </div>
</template>

<style scoped>
* { box-sizing: border-box; }
.dashboard { max-width: 1440px; margin: 0 auto; padding: 38px 42px 60px; color: #222b3c; }
.page-heading,.heading-actions,.ticket-panel>header,.intake-modal>header,.intake-modal footer { display: flex; align-items: center; justify-content: space-between; gap: 18px; }
.eyebrow { color: #6657c7; font-size: 9px; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; }
h1 { margin: 7px 0 5px; font-size: 28px; letter-spacing: -.03em; }h2 { margin: 0; font-size: 16px; }.page-heading p,.ticket-panel header p { margin: 0; color: #858d9b; font-size: 11px; }
.heading-actions { justify-content: flex-end; }.heading-actions a { color: #6657c7; font-size: 11px; font-weight: 700; text-decoration: none; }
button { border: 1px solid #dfe2e8; border-radius: 8px; background: #fff; color: #4d5667; font: inherit; cursor: pointer; }
.primary-button { padding: 11px 17px; border-color: #6557c8; background: #6557c8; color: #fff; font-size: 11px; font-weight: 750; }
.success-banner { margin: 18px 0 0; padding: 11px 14px; border: 1px solid #bfe5d6; border-radius: 8px; background: #ecf9f4; color: #157655; font-size: 11px; }
.metrics { display: grid; grid-template-columns: repeat(5,1fr); margin: 28px 0 22px; overflow: hidden; border: 1px solid #e1e4e9; border-radius: 11px; background: #fff; }
.metrics article { display: flex; min-height: 78px; flex-direction: column; justify-content: center; padding: 16px 20px; border-right: 1px solid #e8eaee; }.metrics article:last-child { border-right: 0; }.metrics strong { font-size: 22px; }.metrics span { margin-top: 5px; color: #8a92a0; font-size: 10px; }
.ticket-panel { overflow: hidden; border: 1px solid #e0e3e8; border-radius: 11px; background: #fff; }.ticket-panel>header { padding: 20px 22px; border-bottom: 1px solid #e6e8ec; }.ticket-panel header p { margin-top: 5px; }.refresh-button { padding: 7px 11px; font-size: 10px; }
.state-view { display: grid; min-height: 240px; place-items: center; align-content: center; gap: 12px; color: #7d8593; font-size: 12px; }.state-view button { padding: 8px 12px; }.state-view.failed { color: #b03b3b; }
.ticket-table { overflow-x: auto; }.table-header,.ticket-row { display: grid; grid-template-columns: minmax(190px,1.1fr) minmax(190px,1.25fr) minmax(145px,.9fr) 85px 110px 65px 125px 82px; gap: 16px; align-items: center; min-width: 1160px; }
.table-header { padding: 10px 20px; background: #f8f9fb; color: #969daa; font-size: 9px; font-weight: 800; text-transform: uppercase; }.ticket-row { min-height: 88px; padding: 14px 20px; border-top: 1px solid #edf0f3; font-size: 10px; }
.customer-cell { display: flex; min-width: 0; align-items: center; gap: 10px; }.avatar { display: grid; flex: 0 0 auto; place-items: center; width: 34px; height: 34px; border-radius: 9px; background: #ece9fb; color: #6455bd; font-weight: 800; }.customer-cell div,.issue-cell,.ticket-row>div { min-width: 0; }.ticket-row strong,.ticket-row span,.ticket-row code { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.ticket-row strong { font-size: 10.5px; }.ticket-row code,.ticket-row div>span { margin-top: 5px; color: #858d9b; font: inherit; font-size: 9.5px; }.issue-cell span { max-width: 260px; }
.status-pill,.run-status,.risk-pill { width: fit-content; padding: 5px 7px; border-radius: 12px; background: #f1f2f5; color: #606978; font-size: 9px; font-weight: 700; }.run-status[data-status="waiting_for_approval"] { background: #fff5e5; color: #aa6418; }.run-status[data-status="completed"] { background: #eaf8f2; color: #157a58; }.risk-pill[data-level="high"] { background: #fff0f0; color: #c33b3b; }.ticket-row time { color: #727b8a; font-size: 9.5px; }.detail-link { color: #6253bc; font-size: 10px; font-weight: 700; text-decoration: none; white-space: nowrap; }
.modal-layer { position: fixed; z-index: 80; inset: 0; display: grid; place-items: center; padding: 24px; background: rgba(25,32,48,.38); backdrop-filter: blur(2px); }.intake-modal { width: min(650px,100%); border-radius: 14px; background: #fff; box-shadow: 0 24px 70px rgba(24,31,46,.22); }.intake-modal>header { padding: 22px 25px 18px; border-bottom: 1px solid #e7e9ed; }.intake-modal h2 { margin-top: 6px; font-size: 20px; }.intake-modal>header button { width: 30px; height: 30px; font-size: 19px; }.intake-modal form { padding: 22px 25px 24px; }.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }.form-grid label { display: grid; gap: 7px; color: #555f70; font-size: 10px; font-weight: 700; }.full-field { grid-column: 1/-1; }input,select,textarea { width: 100%; padding: 10px 11px; border: 1px solid #d9dde4; border-radius: 8px; background: #fff; color: #273143; font: inherit; font-size: 11px; }textarea { resize: vertical; line-height: 1.5; }.form-note { margin: 15px 0 0; padding: 10px; border-radius: 7px; background: #f7f6fc; color: #71698f; font-size: 9.5px; }.form-error { color: #b23838; font-size: 10px; }.intake-modal footer { justify-content: flex-end; margin-top: 19px; }.intake-modal footer button { padding: 10px 16px; font-size: 11px; }
button:disabled { cursor: wait; opacity: .6; }
@media (max-width: 900px) { .dashboard { padding: 28px 22px; }.page-heading { align-items: flex-start; flex-direction: column; }.metrics { grid-template-columns: repeat(2,1fr); }.metrics article { border-bottom: 1px solid #e8eaee; }.form-grid { grid-template-columns: 1fr; }.full-field { grid-column: auto; } }
</style>
