<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import {
  AgentRunNotFoundError,
  fetchLatestAgentRun,
  startAgentRun,
  type AgentRunRecord,
} from '../lib/agentRunsApi'
import { TicketNotFoundError } from '../lib/ticketsApi'
import { ticketStatusLabel } from '../lib/ticketStatus'

const props = defineProps<{ ticketKey: string }>()

/** Agent Run 与步骤的状态词表与后端持久化词表一致，未知状态原样回退，不做臆测 */
const RUN_STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '执行中',
  waiting_for_approval: '等待审批',
  completed: '已完成',
  failed: '已失败',
  cancelled: '已取消',
}

const STEP_STATUS_LABELS: Record<string, string> = {
  pending: '待执行',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  skipped: '已跳过',
}

const RESOLUTION_LABELS: Record<string, string> = {
  replacement_created: '换货单已创建',
}

function runStatusLabel(status: string): string {
  return RUN_STATUS_LABELS[status] ?? status
}

function stepStatusLabel(status: string): string {
  return STEP_STATUS_LABELS[status] ?? status
}

function resolutionLabel(resolution: string): string {
  return RESOLUTION_LABELS[resolution] ?? resolution
}

/** 后端返回的是 UTC naive 时间，直接原样展示并标注 UTC，避免臆测时区 */
function formatUtc(value: string): string {
  const [datePart, timePart = ''] = value.split('T')
  return `${datePart} ${timePart.split('.')[0]} UTC`.trim()
}

type LoadState = 'loading' | 'no_run' | 'loaded' | 'ticket_not_found' | 'failed'

const loadState = ref<LoadState>('loading')
const agentRun = ref<AgentRunRecord | null>(null)
const isStarting = ref(false)
/** 启动请求本身失败（网络 / 服务故障）时的提示，与"执行失败"严格区分 */
const startError = ref<string | null>(null)

async function loadLatestRun(ticketKey: string): Promise<void> {
  loadState.value = 'loading'
  agentRun.value = null
  startError.value = null
  try {
    agentRun.value = await fetchLatestAgentRun(ticketKey)
    loadState.value = 'loaded'
  } catch (error) {
    agentRun.value = null
    if (error instanceof AgentRunNotFoundError) {
      loadState.value = 'no_run'
    } else if (error instanceof TicketNotFoundError) {
      loadState.value = 'ticket_not_found'
    } else {
      loadState.value = 'failed'
    }
  }
}

/**
 * 单次启动入口：一次点击真实驱动后端整条流程，并展示后端返回的真实执行结果。
 * 这里不构造、不猜测、也不预填任何步骤或结论。
 */
async function handleStartRun(): Promise<void> {
  if (isStarting.value) {
    return
  }
  isStarting.value = true
  startError.value = null
  try {
    agentRun.value = await startAgentRun(props.ticketKey)
    loadState.value = 'loaded'
  } catch (error) {
    startError.value =
      error instanceof TicketNotFoundError
        ? `未找到工单 ${props.ticketKey}，无法启动 Agent Run。`
        : 'Agent Run 启动请求失败，未能从后端获取执行结果，请稍后重试。'
  } finally {
    isStarting.value = false
  }
}

onMounted(() => loadLatestRun(props.ticketKey))

watch(
  () => props.ticketKey,
  (ticketKey) => loadLatestRun(ticketKey)
)
</script>

<template>
  <div class="agent-run">
    <a class="back-link" :href="`#/tickets/${ticketKey}`">← 返回工单详情</a>

    <header class="run-header">
      <h1>Agent Run</h1>
      <p class="subtitle">
        工单编号 <code>{{ ticketKey }}</code>
        <template v-if="agentRun">
          · 执行编号 <code>{{ agentRun.business_key }}</code>
        </template>
      </p>
    </header>

    <section class="panel start-panel" aria-label="启动 Agent Run">
      <button
        class="start-button"
        type="button"
        data-testid="start-agent-run"
        :disabled="isStarting"
        @click="handleStartRun"
      >
        {{ isStarting ? '正在执行...' : '启动 Agent Run' }}
      </button>
      <p class="section-note">
        点击后由后端真实执行：提取客户意图 → 检索售后政策 → 查询订单信息 → 检查换货库存 →
        评估换货资格 → 创建换货单 → 回写工单结果。下方展示的全部内容都来自这次真实执行，
        任一步骤失败都会如实中止并标记为失败。
      </p>
      <p v-if="startError" class="error-message" data-testid="start-error">
        {{ startError }}
      </p>
    </section>

    <p
      v-if="loadState === 'loading'"
      class="state-message"
      data-testid="agent-run-state"
      data-state="loading"
    >
      正在加载执行记录...
    </p>

    <p
      v-else-if="loadState === 'ticket_not_found'"
      class="state-message state-warn"
      data-testid="agent-run-state"
      data-state="ticket_not_found"
    >
      未找到工单 {{ props.ticketKey }}，该工单编号在后端不存在。
    </p>

    <p
      v-else-if="loadState === 'failed'"
      class="state-message state-failed"
      data-testid="agent-run-state"
      data-state="failed"
    >
      执行记录加载失败，未能从后端获取数据，请稍后重试。
    </p>

    <p
      v-else-if="loadState === 'no_run'"
      class="state-message"
      data-testid="agent-run-state"
      data-state="no_run"
    >
      该工单尚未执行过 Agent Run，因此没有任何执行记录可以展示。
    </p>

    <template v-else-if="agentRun">
      <div data-testid="agent-run-state" data-state="loaded">
        <section class="panel" aria-label="执行结果">
          <div class="result-header">
            <h2>执行结果</h2>
            <span
              class="run-status"
              data-testid="agent-run-status"
              :data-status="agentRun.status"
            >
              {{ runStatusLabel(agentRun.status) }}
            </span>
          </div>
          <dl class="facts">
            <div class="fact">
              <dt>开始时间</dt>
              <dd>{{ agentRun.started_at ? formatUtc(agentRun.started_at) : '未开始' }}</dd>
            </div>
            <div class="fact">
              <dt>结束时间</dt>
              <dd>
                {{ agentRun.completed_at ? formatUtc(agentRun.completed_at) : '未结束' }}
              </dd>
            </div>
            <div class="fact">
              <dt>已执行步骤数</dt>
              <dd>{{ agentRun.steps.length }}</dd>
            </div>
          </dl>
          <p
            v-if="agentRun.error_message"
            class="error-message"
            data-testid="agent-run-error"
          >
            失败原因：{{ agentRun.error_message }}
          </p>
        </section>

        <section class="panel" aria-label="执行时间线">
          <h2>执行时间线</h2>
          <p class="section-note">
            以下步骤按真实执行顺序记录，只有实际执行过的步骤才会出现在这里。
          </p>
          <ol class="timeline" data-testid="agent-run-timeline">
            <li
              v-for="step in agentRun.steps"
              :key="step.step_order"
              class="timeline-item"
            >
              <span class="timeline-marker" :data-status="step.status"></span>
              <div class="timeline-body">
                <div class="timeline-row">
                  <span class="step-name">{{ step.step_order }}. {{ step.name }}</span>
                  <span class="step-status" :data-status="step.status">
                    {{ stepStatusLabel(step.status) }}
                  </span>
                </div>
                <p v-if="step.error_message" class="step-error">
                  {{ step.error_message }}
                </p>
              </div>
            </li>
          </ol>
        </section>

        <section class="panel" aria-label="换货单">
          <h2>换货单</h2>
          <dl v-if="agentRun.replacement" class="facts" data-testid="agent-run-replacement">
            <div class="fact">
              <dt>换货单编号</dt>
              <dd><code>{{ agentRun.replacement.business_key }}</code></dd>
            </div>
            <div class="fact">
              <dt>商品 SKU</dt>
              <dd><code>{{ agentRun.replacement.product_sku }}</code></dd>
            </div>
            <div class="fact">
              <dt>创建时间</dt>
              <dd>{{ formatUtc(agentRun.replacement.created_at) }}</dd>
            </div>
          </dl>
          <p v-else class="unavailable">本次执行没有创建换货单。</p>
        </section>

        <section class="panel" aria-label="工单结果">
          <h2>工单结果</h2>
          <dl class="facts">
            <div class="fact">
              <dt>工单状态</dt>
              <dd class="fact-status">{{ ticketStatusLabel(agentRun.ticket_result.status) }}</dd>
            </div>
            <div class="fact">
              <dt>解决结果</dt>
              <dd>
                {{
                  agentRun.ticket_result.resolution
                    ? resolutionLabel(agentRun.ticket_result.resolution)
                    : '尚未解决'
                }}
              </dd>
            </div>
            <div class="fact">
              <dt>解决时间</dt>
              <dd>
                {{
                  agentRun.ticket_result.resolved_at
                    ? formatUtc(agentRun.ticket_result.resolved_at)
                    : '未解决'
                }}
              </dd>
            </div>
          </dl>
          <p v-if="agentRun.ticket_result.resolution_summary" class="result-summary">
            {{ agentRun.ticket_result.resolution_summary }}
          </p>
        </section>
      </div>
    </template>

    <section class="panel" aria-label="人工审批">
      <h2>人工审批（Approval）</h2>
      <p class="unavailable">当前没有真实的审批请求或审批结果。</p>
      <p class="section-note">
        审批闸门与风险控制尚未实现，本流程不会发起、代表或执行任何审批。
      </p>
    </section>

    <section class="panel" aria-label="Trace metadata">
      <h2>Trace metadata</h2>
      <p class="unavailable">真实 trace metadata 尚不可用。</p>
      <p class="section-note">
        以下字段不会在本页面被伪造：模型响应、token、cost、timing。待 trace 能力实现后，
        这里才会展示真实数据。
      </p>
    </section>

    <a class="back-link" :href="`#/tickets/${ticketKey}`">← 返回工单详情</a>
  </div>
</template>

<style scoped>
.agent-run {
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

.run-header h1 {
  margin: 0;
  font-size: 1.5rem;
}

.subtitle {
  margin: 0.4rem 0 1.25rem;
  color: #616161;
  font-size: 0.9rem;
}

.panel {
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  padding: 1.25rem;
  margin-bottom: 1.25rem;
  background: #fafafa;
}

.panel h2 {
  margin: 0 0 0.75rem;
  font-size: 1.05rem;
}

.section-note {
  margin: 0.75rem 0 0;
  color: #616161;
  font-size: 0.9rem;
  line-height: 1.6;
}

.start-button {
  font: inherit;
  font-size: 0.95rem;
  padding: 0.6rem 1.25rem;
  border: 1px solid #3949ab;
  border-radius: 6px;
  background: #3949ab;
  color: #ffffff;
  cursor: pointer;
}

.start-button:hover:not(:disabled) {
  background: #303f9f;
}

.start-button:disabled {
  background: #9fa8da;
  border-color: #9fa8da;
  cursor: progress;
}

.state-message {
  color: #616161;
  padding: 1rem 0;
}

.state-failed {
  color: #c62828;
}

.state-warn {
  color: #8d6e00;
}

.result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1rem;
}

.result-header h2 {
  margin: 0;
}

.run-status {
  font-size: 0.8rem;
  font-weight: 600;
  padding: 0.2rem 0.6rem;
  border-radius: 4px;
  color: #455a64;
  background: #eceff1;
  border: 1px solid #b0bec5;
}

.run-status[data-status='completed'] {
  color: #1b5e20;
  background: #e8f5e9;
  border-color: #a5d6a7;
}

.run-status[data-status='failed'] {
  color: #b71c1c;
  background: #ffebee;
  border-color: #ef9a9a;
}

.facts {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.9rem 1rem;
  margin: 0;
}

.fact dt {
  margin: 0;
  color: #757575;
  font-size: 0.75rem;
}

.fact dd {
  margin: 0.2rem 0 0;
  font-size: 0.95rem;
}

.fact-status {
  font-weight: 600;
}

.fact code {
  font-size: 0.85rem;
}

.error-message {
  margin: 1rem 0 0;
  padding: 0.6rem 0.85rem;
  font-size: 0.88rem;
  line-height: 1.6;
  color: #b71c1c;
  background: #ffebee;
  border: 1px solid #ef9a9a;
  border-radius: 6px;
}

.result-summary {
  margin: 1rem 0 0;
  padding-top: 1rem;
  border-top: 1px solid #eeeeee;
  line-height: 1.6;
}

.unavailable {
  margin: 0;
  color: #455a64;
  font-weight: 600;
}

.timeline {
  list-style: none;
  margin: 1rem 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}

.timeline-item {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.6rem 0;
  border-bottom: 1px solid #eeeeee;
}

.timeline-item:last-child {
  border-bottom: none;
}

.timeline-marker {
  flex: none;
  margin-top: 0.35rem;
  width: 0.75rem;
  height: 0.75rem;
  border-radius: 50%;
  background: #90a4ae;
}

.timeline-marker[data-status='running'] {
  background: #1e88e5;
}

.timeline-marker[data-status='completed'] {
  background: #2e7d32;
}

.timeline-marker[data-status='failed'] {
  background: #c62828;
}

.timeline-marker[data-status='skipped'] {
  background: #9e9e9e;
}

.timeline-body {
  flex: 1;
}

.timeline-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
}

.step-name {
  font-size: 0.95rem;
}

.step-status {
  font-size: 0.8rem;
  font-weight: 600;
  color: #90a4ae;
}

.step-status[data-status='running'] {
  color: #1e88e5;
}

.step-status[data-status='completed'] {
  color: #2e7d32;
}

.step-status[data-status='failed'] {
  color: #c62828;
}

.step-status[data-status='skipped'] {
  color: #9e9e9e;
}

.step-error {
  margin: 0.35rem 0 0;
  font-size: 0.82rem;
  line-height: 1.5;
  color: #b71c1c;
  word-break: break-word;
}
</style>
