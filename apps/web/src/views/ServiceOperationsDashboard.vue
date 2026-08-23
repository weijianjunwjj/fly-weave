<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchTickets, type TicketRecord } from '../lib/ticketsApi'

type LoadState = 'loading' | 'success' | 'empty' | 'failed'

const loadState = ref<LoadState>('loading')
const tickets = ref<TicketRecord[]>([])

interface DemoMetric {
  key: string
  label: string
  value: string
  badge: 'Demo' | 'Simulated'
}

const demoMetrics: DemoMetric[] = [
  { key: 'automation-rate', label: '自动化处理率', value: '68%', badge: 'Simulated' },
  { key: 'human-takeover', label: '人工接管率', value: '22%', badge: 'Simulated' },
  { key: 'processing-time', label: '平均处理时长', value: '4.2 分钟', badge: 'Simulated' },
  { key: 'cost', label: '单工单成本', value: '¥1.80', badge: 'Simulated' },
]

const statusLabels: Record<string, string> = {
  open: '待处理',
  in_progress: '处理中',
  waiting_for_approval: '等待审批',
  resolved: '已解决',
  closed: '已关闭',
}

function statusLabel(status: string): string {
  return statusLabels[status] ?? status
}

async function loadTickets(): Promise<void> {
  loadState.value = 'loading'
  try {
    const result = await fetchTickets()
    tickets.value = result
    loadState.value = result.length === 0 ? 'empty' : 'success'
  } catch {
    tickets.value = []
    loadState.value = 'failed'
  }
}

onMounted(loadTickets)
</script>

<template>
  <div class="dashboard">
    <header class="dashboard-header">
      <h1>Service Operations</h1>
      <p>售后服务运营总览</p>
    </header>

    <section class="metrics" aria-label="运营指标（演示数据）">
      <article v-for="metric in demoMetrics" :key="metric.key" class="metric-card">
        <span class="metric-badge">{{ metric.badge }}</span>
        <p class="metric-value">{{ metric.value }}</p>
        <p class="metric-label">{{ metric.label }}</p>
      </article>
    </section>

    <section class="ticket-queue" aria-label="工单队列">
      <div class="ticket-queue-header">
        <h2>工单队列</h2>
        <span class="demo-data-tag">演示数据（Demo Data）</span>
      </div>

      <p v-if="loadState === 'loading'" class="state-message" data-testid="tickets-state" data-state="loading">
        正在加载工单...
      </p>
      <p v-else-if="loadState === 'failed'" class="state-message state-failed" data-testid="tickets-state" data-state="failed">
        工单加载失败，请稍后重试。
      </p>
      <p v-else-if="loadState === 'empty'" class="state-message" data-testid="tickets-state" data-state="empty">
        当前没有工单记录。
      </p>
      <ul v-else class="ticket-list" data-testid="tickets-state" data-state="success">
        <li v-for="ticket in tickets" :key="ticket.business_key" class="ticket-row">
          <a class="ticket-link" :href="`#/tickets/${ticket.business_key}`">
            <div class="ticket-main">
              <span class="ticket-subject">{{ ticket.subject }}</span>
              <span class="ticket-status">{{ statusLabel(ticket.status) }}</span>
            </div>
            <p class="ticket-description">{{ ticket.description }}</p>
            <span class="ticket-demo-tag">Demo</span>
          </a>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.dashboard {
  font-family: system-ui, -apple-system, sans-serif;
  max-width: 960px;
  margin: 0 auto;
  padding: 2rem;
}

.dashboard-header h1 {
  margin: 0;
  font-size: 1.75rem;
}

.dashboard-header p {
  margin: 0.25rem 0 1.5rem;
  color: #616161;
}

.metrics {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 1rem;
  margin-bottom: 2rem;
}

.metric-card {
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  padding: 1rem;
  background: #fafafa;
  position: relative;
}

.metric-badge {
  position: absolute;
  top: 0.5rem;
  right: 0.5rem;
  font-size: 0.65rem;
  font-weight: 600;
  letter-spacing: 0.03em;
  color: #8d6e00;
  background: #fff3cd;
  border: 1px solid #e6c94f;
  border-radius: 4px;
  padding: 0.1rem 0.4rem;
}

.metric-value {
  margin: 0.5rem 0 0.25rem;
  font-size: 1.5rem;
  font-weight: 700;
}

.metric-label {
  margin: 0;
  color: #616161;
  font-size: 0.85rem;
}

.ticket-queue-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1rem;
}

.ticket-queue-header h2 {
  margin: 0;
  font-size: 1.2rem;
}

.demo-data-tag {
  font-size: 0.7rem;
  font-weight: 600;
  color: #8d6e00;
  background: #fff3cd;
  border: 1px solid #e6c94f;
  border-radius: 4px;
  padding: 0.15rem 0.5rem;
}

.state-message {
  color: #616161;
  padding: 1rem 0;
}

.state-failed {
  color: #c62828;
}

.ticket-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.ticket-row {
  border: 1px solid #e0e0e0;
  border-radius: 8px;
}

.ticket-link {
  display: block;
  padding: 1rem;
  text-decoration: none;
  color: inherit;
  position: relative;
}

.ticket-link:hover {
  background: #f5f5f5;
}

.ticket-main {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  font-weight: 600;
  padding-right: 3.5rem;
}

.ticket-status {
  color: #37474f;
  font-weight: 500;
  font-size: 0.85rem;
}

.ticket-description {
  margin: 0.4rem 0 0;
  color: #616161;
  font-size: 0.9rem;
}

.ticket-demo-tag {
  position: absolute;
  top: 0.75rem;
  right: 1rem;
  font-size: 0.65rem;
  font-weight: 600;
  color: #8d6e00;
  background: #fff3cd;
  border: 1px solid #e6c94f;
  border-radius: 4px;
  padding: 0.1rem 0.4rem;
}
</style>
