<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
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

/** 后端返回的是 UTC naive 时间，直接原样展示并标注 UTC，避免臆测时区 */
function formatUtc(value: string): string {
  const [datePart, timePart = ''] = value.split('T')
  return `${datePart} ${timePart.split('.')[0]} UTC`.trim()
}

async function loadTicketDetail(businessKey: string): Promise<void> {
  loadState.value = 'loading'
  ticket.value = null
  try {
    ticket.value = await fetchTicketDetail(businessKey)
    loadState.value = 'success'
  } catch (error) {
    ticket.value = null
    loadState.value = error instanceof TicketNotFoundError ? 'not_found' : 'failed'
  }
}

onMounted(() => loadTicketDetail(props.ticketKey))

watch(
  () => props.ticketKey,
  (businessKey) => loadTicketDetail(businessKey)
)
</script>

<template>
  <div class="ticket-detail">
    <a class="back-link" href="#/">← 返回 Service Operations</a>

    <p
      v-if="loadState === 'loading'"
      class="state-message"
      data-testid="ticket-detail-state"
      data-state="loading"
    >
      正在加载工单详情...
    </p>

    <p
      v-else-if="loadState === 'failed'"
      class="state-message state-failed"
      data-testid="ticket-detail-state"
      data-state="failed"
    >
      工单详情加载失败，未能从后端获取数据，请稍后重试。
    </p>

    <p
      v-else-if="loadState === 'not_found'"
      class="state-message state-not-found"
      data-testid="ticket-detail-state"
      data-state="not_found"
    >
      未找到工单 {{ props.ticketKey }}，该工单编号在后端不存在。
    </p>

    <template v-else-if="ticket">
      <div data-testid="ticket-detail-state" data-state="success">
        <header class="detail-header">
          <div class="detail-title-row">
            <h1>{{ ticket.subject }}</h1>
            <span class="demo-tag">演示数据（Demo Data）</span>
          </div>
          <p class="detail-subtitle">
            工单编号 <code>{{ ticket.business_key }}</code> · {{ scenarioLabel(ticket.demo_scenario) }}
          </p>
          <p class="demo-notice">
            本页面展示的工单、客户与订单均为演示 / 模拟数据，不代表任何真实生产业务记录。
          </p>
        </header>

        <section class="panel" aria-label="工单请求">
          <h2>工单请求</h2>
          <dl class="facts">
            <div class="fact">
              <dt>当前处理状态</dt>
              <dd class="fact-status">{{ ticketStatusLabel(ticket.status) }}</dd>
            </div>
            <div class="fact">
              <dt>创建时间</dt>
              <dd>{{ formatUtc(ticket.created_at) }}</dd>
            </div>
          </dl>
          <p class="request-description">{{ ticket.description }}</p>
        </section>

        <section class="panel" aria-label="客户信息">
          <h2>客户信息</h2>
          <dl v-if="ticket.customer" class="facts">
            <div class="fact">
              <dt>客户姓名</dt>
              <dd>{{ ticket.customer.name }}</dd>
            </div>
            <div class="fact">
              <dt>客户编号</dt>
              <dd><code>{{ ticket.customer.business_key }}</code></dd>
            </div>
            <div class="fact">
              <dt>邮箱</dt>
              <dd>{{ ticket.customer.email }}</dd>
            </div>
            <div class="fact">
              <dt>电话</dt>
              <dd>{{ ticket.customer.phone ?? '未提供' }}</dd>
            </div>
          </dl>
          <p v-else class="empty-note">该工单没有关联的客户记录。</p>
        </section>

        <section class="panel" aria-label="订单与商品信息">
          <h2>订单与商品</h2>
          <dl v-if="ticket.order" class="facts">
            <div class="fact">
              <dt>订单编号</dt>
              <dd><code>{{ ticket.order.business_key }}</code></dd>
            </div>
            <div class="fact">
              <dt>商品名称</dt>
              <dd>{{ ticket.order.product_name }}</dd>
            </div>
            <div class="fact">
              <dt>商品 SKU</dt>
              <dd><code>{{ ticket.order.product_sku }}</code></dd>
            </div>
            <div class="fact">
              <dt>订单状态</dt>
              <dd>{{ orderStatusLabel(ticket.order.status) }}</dd>
            </div>
            <div class="fact">
              <dt>订单金额</dt>
              <dd>¥{{ ticket.order.amount }}</dd>
            </div>
            <div class="fact">
              <dt>购买时间</dt>
              <dd>{{ formatUtc(ticket.order.purchased_at) }}</dd>
            </div>
          </dl>
          <p v-else class="empty-note">该工单没有关联的订单记录。</p>
        </section>

        <section class="panel agent-run-panel" aria-label="Agent Run 入口">
          <div class="agent-run-header">
            <h2>Agent Run</h2>
          </div>
          <p class="agent-run-note">
            进入 Agent Run 页面可查看本工单已有的真实执行记录，并启动一次完整流程。
            执行由后端真实驱动，时间线上的每一步与最终结果都来自实际的工具调用。
          </p>
          <a
            class="agent-run-link"
            :href="`#/tickets/${ticket.business_key}/agent-run`"
            data-testid="agent-run-entry"
          >
            查看 / 启动 Agent Run
          </a>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.ticket-detail {
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

.state-message {
  color: #616161;
  padding: 1rem 0;
}

.state-failed {
  color: #c62828;
}

.state-not-found {
  color: #8d6e00;
}

.detail-title-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.detail-header h1 {
  margin: 0;
  font-size: 1.5rem;
}

.detail-subtitle {
  margin: 0.4rem 0 0;
  color: #616161;
  font-size: 0.9rem;
}

.demo-notice {
  margin: 0.75rem 0 1.5rem;
  padding: 0.6rem 0.85rem;
  font-size: 0.85rem;
  color: #8d6e00;
  background: #fff8e1;
  border: 1px solid #e6c94f;
  border-radius: 6px;
}

.demo-tag {
  font-size: 0.7rem;
  font-weight: 600;
  color: #8d6e00;
  background: #fff3cd;
  border: 1px solid #e6c94f;
  border-radius: 4px;
  padding: 0.15rem 0.5rem;
  white-space: nowrap;
}

.panel {
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  padding: 1.25rem;
  margin-bottom: 1.25rem;
  background: #fafafa;
}

.panel h2 {
  margin: 0 0 1rem;
  font-size: 1.05rem;
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

.request-description {
  margin: 1rem 0 0;
  padding-top: 1rem;
  border-top: 1px solid #eeeeee;
  line-height: 1.6;
}

.empty-note {
  margin: 0;
  color: #757575;
  font-size: 0.9rem;
}

.agent-run-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}

.agent-run-header h2 {
  margin: 0;
}

.agent-run-note {
  margin: 0 0 1rem;
  color: #616161;
  font-size: 0.9rem;
  line-height: 1.6;
}

.agent-run-link {
  display: inline-block;
  font: inherit;
  font-size: 0.9rem;
  padding: 0.5rem 1rem;
  border: 1px solid #3949ab;
  border-radius: 6px;
  background: #3949ab;
  color: #ffffff;
  text-decoration: none;
}

.agent-run-link:hover {
  background: #303f9f;
}
</style>
