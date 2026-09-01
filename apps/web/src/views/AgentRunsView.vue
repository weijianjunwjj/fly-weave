<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  fetchAgentRuns,
  type AgentRunCenterItem,
  type AgentRunRecord,
} from '../lib/agentRunsApi'

type LoadState = 'loading' | 'ready' | 'empty' | 'failed'
const loadState = ref<LoadState>('loading')
const items = ref<AgentRunCenterItem[]>([])

const RUN_LABELS: Record<string, string> = {
  queued: '等待开始',
  running: 'AI 处理中',
  waiting_for_approval: '等待人工审批',
  completed: '已完成',
  failed: '执行失败',
  cancelled: '已停止',
}

const stats = computed(() => ({
  active: items.value.filter(({ agent_run }) =>
    agent_run.status === 'queued' || agent_run.status === 'running',
  ).length,
  waiting: items.value.filter(({ agent_run }) =>
    agent_run.status === 'waiting_for_approval',
  ).length,
  completed: items.value.filter(({ agent_run }) =>
    agent_run.status === 'completed',
  ).length,
  stopped: items.value.filter(({ agent_run }) =>
    agent_run.status === 'failed' || agent_run.status === 'cancelled',
  ).length,
}))

async function loadRuns(): Promise<void> {
  loadState.value = 'loading'
  try {
    items.value = await fetchAgentRuns()
    loadState.value = items.value.length ? 'ready' : 'empty'
  } catch {
    items.value = []
    loadState.value = 'failed'
  }
}

function isPolicyBlocked(run: AgentRunRecord): boolean {
  const reason = run.error_message?.toLowerCase() ?? ''
  return reason.includes('replacement_window_expired') || reason.includes('blocked')
}

function statusLabel(run: AgentRunRecord): string {
  if (run.status === 'cancelled' && run.approval_request?.status === 'rejected') {
    return '人工拒绝'
  }
  if (run.status === 'failed' && isPolicyBlocked(run)) return '政策阻止'
  return RUN_LABELS[run.status] ?? run.status
}

function currentStep(run: AgentRunRecord): string {
  if (run.approval_request?.status === 'pending') return '人工审批'
  if (run.status === 'completed') return '执行完成'
  if (run.status === 'cancelled' && run.approval_request?.status === 'rejected') {
    return '受保护操作已停止'
  }
  const active = [...run.steps].reverse().find(step =>
    step.status === 'running' || step.status === 'failed',
  )
  return active?.name ?? run.steps[run.steps.length - 1]?.name ?? '尚未记录步骤'
}

function finalResult(run: AgentRunRecord): string | null {
  if (run.replacement) return `换货单 ${run.replacement.business_key} 已创建`
  if (run.approval_request?.status === 'rejected') return '人工拒绝，未执行受保护操作'
  if (run.status === 'failed' && isPolicyBlocked(run)) return run.error_message || '业务政策阻止'
  if (run.status === 'failed') return run.error_message || '执行未能完成'
  if (run.status === 'cancelled') return '执行已停止'
  return run.ticket_result.resolution_summary
}

function duration(run: AgentRunRecord): string {
  const start = run.started_at ?? run.created_at
  const end = run.completed_at ?? new Date().toISOString()
  const seconds = Math.max(0, Math.floor((Date.parse(end) - Date.parse(start)) / 1000))
  const minutes = Math.floor(seconds / 60)
  return minutes ? `${minutes}m ${seconds % 60}s` : `${seconds}s`
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value.endsWith('Z') ? value : `${value}Z`))
}

onMounted(loadRuns)
</script>

<template>
  <div class="runs-page">
    <header class="page-heading">
      <div>
        <span class="eyebrow">AI EXECUTION CENTER</span>
        <h1>Agent Runs <small>AI 执行中心</small></h1>
        <p>查看 AI 服务任务的运行状态、关键决策和执行结果。</p>
      </div>
      <button type="button" :disabled="loadState === 'loading'" @click="loadRuns">
        {{ loadState === 'loading' ? '正在刷新…' : '刷新状态' }}
      </button>
    </header>

    <section class="metrics" aria-label="执行统计">
      <article><span class="dot active"></span><div><small>运行中</small><strong>{{ stats.active }}</strong></div></article>
      <article><span class="dot waiting"></span><div><small>等待人工</small><strong>{{ stats.waiting }}</strong></div></article>
      <article><span class="dot completed"></span><div><small>已完成</small><strong>{{ stats.completed }}</strong></div></article>
      <article><span class="dot stopped"></span><div><small>失败 / 已停止</small><strong>{{ stats.stopped }}</strong></div></article>
    </section>

    <div v-if="loadState === 'loading'" class="state-view">正在读取真实执行记录…</div>
    <div v-else-if="loadState === 'failed'" class="state-view failed">Agent Runs 暂时无法读取，请稍后刷新。</div>
    <div v-else-if="loadState === 'empty'" class="state-view">尚无 Agent Run。请从工单详情启动 AI 处理。</div>

    <section v-else class="run-list" aria-label="Agent Run 列表">
      <article v-for="{ agent_run: run, ticket } in items" :key="run.business_key" class="run-card">
        <div class="run-main">
          <div class="run-id"><span>RUN</span><code>{{ run.business_key }}</code></div>
          <h2>{{ ticket.subject }}</h2>
          <p>{{ ticket.customer?.name ?? '未关联客户' }} · <code>{{ ticket.business_key }}</code></p>
          <p v-if="finalResult(run)" class="result"><small>最终结果</small>{{ finalResult(run) }}</p>
        </div>

        <dl class="run-facts">
          <div><dt>状态</dt><dd><span class="status" :data-status="run.status" :data-rejected="run.approval_request?.status === 'rejected'" :data-blocked="isPolicyBlocked(run)">{{ statusLabel(run) }}</span></dd></div>
          <div><dt>当前步骤</dt><dd>{{ currentStep(run) }}</dd></div>
          <div><dt>风险</dt><dd><b v-if="run.risk" :data-risk="run.risk.level">{{ run.risk.level.toUpperCase() }}</b><span v-else>—</span></dd></div>
          <div><dt>开始时间</dt><dd>{{ formatTime(run.started_at ?? run.created_at) }}</dd></div>
          <div><dt>持续时间</dt><dd>{{ duration(run) }}</dd></div>
        </dl>

        <a class="detail-link" :href="`#/agent-runs/${run.business_key}`">查看运行详情 →</a>
      </article>
    </section>
  </div>
</template>

<style scoped>
* { box-sizing: border-box; }
.runs-page { max-width: 1380px; margin: 0 auto; padding: 34px 40px 64px; color: #222b3b; }
.page-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; }
.eyebrow { color: #6859c6; font-size: 9px; font-weight: 800; letter-spacing: .14em; }
h1 { margin: 8px 0 6px; font-size: 29px; letter-spacing: -.035em; }
h1 small { margin-left: 9px; color: #7d8593; font-size: 13px; font-weight: 650; letter-spacing: 0; }
.page-heading p { margin: 0; color: #7f8795; font-size: 11px; }
.page-heading button { padding: 9px 14px; border: 1px solid #dce0e6; border-radius: 8px; background: #fff; color: #596274; font: inherit; font-size: 10px; font-weight: 700; cursor: pointer; }
.metrics { display: grid; grid-template-columns: repeat(4,1fr); margin: 26px 0 18px; border: 1px solid #e0e3e8; border-radius: 11px; background: #fff; }
.metrics article { display: flex; align-items: center; gap: 11px; padding: 17px 20px; border-right: 1px solid #e8eaee; }.metrics article:last-child { border: 0; }
.metrics small,.metrics strong { display: block; }.metrics small { color: #9299a6; font-size: 8.5px; font-weight: 750; }.metrics strong { margin-top: 3px; font-size: 20px; }
.dot { width: 8px; height: 8px; border-radius: 50%; }.dot.active { background: #6657c7; }.dot.waiting { background: #d28a2d; }.dot.completed { background: #22946d; }.dot.stopped { background: #bd4b4b; }
.run-list { display: grid; gap: 10px; }
.run-card { display: grid; grid-template-columns: minmax(260px,1.25fr) minmax(520px,1.7fr) auto; align-items: center; gap: 22px; padding: 18px 20px; border: 1px solid #e0e3e8; border-radius: 11px; background: #fff; transition: border-color .15s, box-shadow .15s; }
.run-card:hover { border-color: #cec9ed; box-shadow: 0 7px 20px rgba(39,47,66,.06); }
.run-id { display: flex; align-items: center; gap: 8px; }.run-id span { padding: 3px 5px; border-radius: 4px; background: #efedfb; color: #6657bd; font-size: 8px; font-weight: 800; }.run-id code { color: #77808f; font-size: 9px; }
.run-main h2 { margin: 8px 0 4px; font-size: 13px; }.run-main>p { margin: 0; color: #8b93a0; font-size: 9.5px; }.run-main p code { font: inherit; }
.result { margin-top: 10px !important; color: #3d4859 !important; }.result small { margin-right: 6px; color: #9299a6; font-size: 8px; font-weight: 750; text-transform: uppercase; }
.run-facts { display: grid; grid-template-columns: 1.25fr 1.25fr .65fr 1fr .7fr; gap: 13px; margin: 0; }
.run-facts dt { color: #9aa1ad; font-size: 8px; font-weight: 700; text-transform: uppercase; }.run-facts dd { margin: 5px 0 0; color: #4d5768; font-size: 9.5px; line-height: 1.35; }
.status { display: inline-block; padding: 4px 7px; border-radius: 12px; background: #eef0f4; color: #5f6878; font-size: 8.5px; font-weight: 750; }.status[data-status="completed"] { background: #eaf8f2; color: #177a59; }.status[data-status="waiting_for_approval"] { background: #fff4df; color: #9a651b; }.status[data-status="running"] { background: #efedfb; color: #6051b6; }.status[data-status="failed"] { background: #fff0f0; color: #b63f3f; }.status[data-rejected="true"] { background: #f5eeee; color: #a14b4b; }.status[data-blocked="true"] { background: #fff5e6; color: #a46918; }
.run-facts b { font-size: 9px; }.run-facts b[data-risk="high"] { color: #c34040; }.run-facts b[data-risk="low"] { color: #23805f; }
.detail-link { padding: 8px 11px; border-radius: 7px; background: #f0eefb; color: #6152b8; font-size: 9.5px; font-weight: 750; text-decoration: none; white-space: nowrap; }
.state-view { display: grid; min-height: 280px; place-items: center; border: 1px dashed #d9dde4; border-radius: 11px; color: #7d8594; font-size: 11px; }.state-view.failed { color: #b33e3e; }
button:disabled { cursor: wait; opacity: .6; }
@media (max-width: 1100px) { .run-card { grid-template-columns: 1fr auto; }.run-facts { grid-column: 1/-1; }.detail-link { grid-column: 2; grid-row: 1; }.metrics { grid-template-columns: repeat(2,1fr); }.metrics article:nth-child(2) { border-right: 0; }.metrics article:nth-child(-n+2) { border-bottom: 1px solid #e8eaee; } }
@media (max-width: 700px) { .runs-page { padding: 26px 18px; }.page-heading { align-items: flex-start; flex-direction: column; }.run-card { grid-template-columns: 1fr; }.run-facts { grid-template-columns: repeat(2,1fr); }.detail-link { grid-column: 1; grid-row: auto; justify-self: start; } }
</style>
