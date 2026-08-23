<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import ServiceOperationsDashboard from './views/ServiceOperationsDashboard.vue'
import TicketDetailView from './views/TicketDetailView.vue'
import AgentRunView from './views/AgentRunView.vue'
import ApprovalInboxView from './views/ApprovalInboxView.vue'

const appName = ref('Flyweave')

type HealthStatus = 'loading' | 'connected' | 'failed'

const healthStatus = ref<HealthStatus>('loading')

const HEALTH_ENDPOINT = 'http://localhost:8000/health'

function isHealthy(data: unknown): boolean {
  if (typeof data !== 'object' || data === null) {
    return false
  }
  const record = data as Record<string, unknown>
  return (
    record.status === 'healthy' &&
    typeof record.app_name === 'string' &&
    typeof record.environment === 'string'
  )
}

async function checkBackendHealth(): Promise<void> {
  healthStatus.value = 'loading'
  try {
    const response = await fetch(HEALTH_ENDPOINT)
    if (!response.ok) {
      healthStatus.value = 'failed'
      return
    }
    const data = await response.json()
    healthStatus.value = isHealthy(data) ? 'connected' : 'failed'
  } catch {
    healthStatus.value = 'failed'
  }
}

const currentHash = ref(window.location.hash)

function handleHashChange(): void {
  currentHash.value = window.location.hash
}

const ticketDetailKey = computed<string | null>(() => {
  const match = currentHash.value.match(/^#\/tickets\/([^/]+)$/)
  return match ? decodeURIComponent(match[1]) : null
})

const agentRunTicketKey = computed<string | null>(() => {
  const match = currentHash.value.match(/^#\/tickets\/([^/]+)\/agent-run$/)
  return match ? decodeURIComponent(match[1]) : null
})

const isApprovalInbox = computed<boolean>(() => currentHash.value === '#/approvals')

onMounted(() => {
  checkBackendHealth()
  window.addEventListener('hashchange', handleHashChange)
})

onUnmounted(() => {
  window.removeEventListener('hashchange', handleHashChange)
})
</script>

<template>
  <div class="flyweave-app">
    <header class="app-header">
      <h1>{{ appName }}</h1>
      <p>企业 AI 服务工作流演示</p>
    </header>
    <section class="health-status" data-testid="backend-health-status" :data-status="healthStatus">
      <span v-if="healthStatus === 'loading'">正在检查后端连接...</span>
      <span v-else-if="healthStatus === 'connected'">后端已连接</span>
      <span v-else>后端连接失败</span>
    </section>

    <main>
      <AgentRunView v-if="agentRunTicketKey" :ticket-key="agentRunTicketKey" />
      <TicketDetailView v-else-if="ticketDetailKey" :ticket-key="ticketDetailKey" />
      <ApprovalInboxView v-else-if="isApprovalInbox" />
      <ServiceOperationsDashboard v-else />
    </main>
  </div>
</template>

<style scoped>
.flyweave-app {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.app-header {
  padding: 2rem;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  text-align: center;
}

.app-header h1 {
  margin: 0;
  font-size: 2.5rem;
  font-weight: 600;
}

.app-header p {
  margin: 0.5rem 0 0;
  font-size: 1rem;
  opacity: 0.9;
}

.health-status {
  padding: 1rem 2rem;
  font-size: 0.95rem;
}

.health-status[data-status='connected'] {
  color: #2e7d32;
}

.health-status[data-status='failed'] {
  color: #c62828;
}

.health-status[data-status='loading'] {
  color: #616161;
}
</style>
