<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import AgentRunDetailView from './views/AgentRunDetailView.vue'
import AgentRunsView from './views/AgentRunsView.vue'
import AgentRunView from './views/AgentRunView.vue'
import ApprovalInboxView from './views/ApprovalInboxView.vue'
import OverviewView from './views/OverviewView.vue'
import ServiceOperationsDashboard from './views/ServiceOperationsDashboard.vue'
import TicketDetailView from './views/TicketDetailView.vue'

type HealthStatus = 'loading' | 'connected' | 'failed'
const healthStatus = ref<HealthStatus>('loading')
const currentHash = ref(window.location.hash)
const HEALTH_ENDPOINT = 'http://localhost:8000/health'

function isHealthy(data: unknown): boolean {
  if (typeof data !== 'object' || data === null) return false
  const record = data as Record<string, unknown>
  return record.status === 'healthy' && typeof record.app_name === 'string'
}

async function checkBackendHealth(): Promise<void> {
  healthStatus.value = 'loading'
  try {
    const response = await fetch(HEALTH_ENDPOINT)
    healthStatus.value = response.ok && isHealthy(await response.json()) ? 'connected' : 'failed'
  } catch {
    healthStatus.value = 'failed'
  }
}

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

const agentRunDetailKey = computed<string | null>(() => {
  const match = currentHash.value.match(/^#\/agent-runs\/([^/?]+)$/)
  return match ? decodeURIComponent(match[1]) : null
})

const isAgentRuns = computed(() =>
  currentHash.value === '#/agent-runs' ||
  (!currentHash.value && window.location.pathname === '/agent-runs')
)

const isApprovalInbox = computed(() =>
  currentHash.value.startsWith('#/approvals') || (!currentHash.value && window.location.pathname === '/approvals')
)

const isServiceOperations = computed(() =>
  currentHash.value === '#/tickets'
  || (!currentHash.value && window.location.pathname === '/tickets')
)

const isOverview = computed(() =>
  !ticketDetailKey.value
  && !agentRunTicketKey.value
  && !agentRunDetailKey.value
  && !isAgentRuns.value
  && !isApprovalInbox.value
  && !isServiceOperations.value
)

const pageTitle = computed(() => {
  if (isApprovalInbox.value) return 'Approval Inbox'
  if (isAgentRuns.value || agentRunDetailKey.value || agentRunTicketKey.value) return 'Agent Runs'
  if (isServiceOperations.value || ticketDetailKey.value) return 'Service Operations'
  return 'Overview'
})

onMounted(() => {
  checkBackendHealth()
  window.addEventListener('hashchange', handleHashChange)
})
onUnmounted(() => window.removeEventListener('hashchange', handleHashChange))
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <a class="brand" href="#/">
        <span class="brand-mark"><i></i><i></i><i></i></span>
        <span><strong>Flyweave</strong><small>AI Service OS</small></span>
      </a>

      <nav aria-label="主导航">
        <p>WORKSPACE</p>
        <a href="#/" :class="{ active: isOverview }"><span>⌂</span> Overview</a>
        <a href="#/tickets" :class="{ active: isServiceOperations || !!ticketDetailKey }"><span>◫</span> Service Operations</a>
        <a href="#/approvals" :class="{ active: isApprovalInbox }"><span>✓</span> Approval Inbox <b v-if="isApprovalInbox">•</b></a>
        <a href="#/agent-runs" :class="{ active: isAgentRuns || !!agentRunDetailKey || !!agentRunTicketKey }"><span>✦</span> Agent Runs</a>
        <a href="#/approvals"><span>≡</span> Audit</a>
      </nav>

      <div class="sidebar-foot">
        <div class="operator-avatar">OP</div>
        <div><strong>Operations Team</strong><small>Human reviewer</small></div>
        <span>•••</span>
      </div>
    </aside>

    <section class="shell-content">
      <header class="topbar">
        <div><span class="breadcrumb">Flyweave</span><i>/</i><strong>{{ pageTitle }}</strong></div>
        <div class="topbar-actions">
          <span class="connection" :data-status="healthStatus"><i></i>{{ healthStatus === 'connected' ? 'Systems operational' : healthStatus === 'loading' ? 'Connecting' : 'Service unavailable' }}</span>
          <button type="button" aria-label="通知">◦</button>
          <span class="operator-mini">OP</span>
        </div>
      </header>

      <main>
        <AgentRunDetailView v-if="agentRunDetailKey" :run-key="agentRunDetailKey" />
        <AgentRunsView v-else-if="isAgentRuns" />
        <AgentRunView v-else-if="agentRunTicketKey" :ticket-key="agentRunTicketKey" />
        <TicketDetailView v-else-if="ticketDetailKey" :ticket-key="ticketDetailKey" />
        <ApprovalInboxView v-else-if="isApprovalInbox" />
        <ServiceOperationsDashboard v-else-if="isServiceOperations" />
        <OverviewView v-else />
      </main>
    </section>
  </div>
</template>

<style scoped>
* { box-sizing: border-box; }
.app-shell { display: flex; min-height: 100vh; background: #f5f6f8; color: #182034; font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
.sidebar { position: fixed; z-index: 20; inset: 0 auto 0 0; display: flex; width: 224px; flex-direction: column; border-right: 1px solid #e2e5ea; background: #fbfbfc; }
.brand { display: flex; align-items: center; gap: 11px; height: 68px; padding: 0 20px; border-bottom: 1px solid #e7e9ed; color: inherit; text-decoration: none; }
.brand-mark { position: relative; display: block; width: 28px; height: 28px; overflow: hidden; border-radius: 8px; background: #6657c7; }
.brand-mark i { position: absolute; width: 12px; height: 5px; border-radius: 5px 5px 2px 2px; background: #fff; transform: rotate(-35deg); }.brand-mark i:nth-child(1) { top: 7px; left: 5px; }.brand-mark i:nth-child(2) { top: 12px; left: 10px; opacity: .8; }.brand-mark i:nth-child(3) { top: 17px; left: 14px; opacity: .55; }
.brand strong,.brand small { display: block; }.brand strong { font-size: 15px; letter-spacing: -.02em; }.brand small { margin-top: 2px; color: #969daa; font-size: 8px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; }
nav { padding: 22px 12px; }nav p { margin: 0 10px 10px; color: #a3a9b4; font-size: 9px; font-weight: 800; letter-spacing: .12em; }nav a { position: relative; display: flex; align-items: center; gap: 11px; height: 40px; margin-bottom: 3px; padding: 0 11px; border-radius: 8px; color: #6e7685; font-size: 11.5px; font-weight: 600; text-decoration: none; }nav a:hover { background: #f1f2f5; color: #3d4656; }nav a.active { background: #eeecfb; color: #5c4eb6; }nav a span { display: grid; width: 18px; place-items: center; color: #858d9a; font-size: 15px; }nav a.active span { color: #6556bf; }nav a b { position: absolute; right: 13px; color: #6b5cca; font-size: 17px; }
.sidebar-foot { display: flex; align-items: center; gap: 9px; margin-top: auto; padding: 16px; border-top: 1px solid #e6e8ec; }.operator-avatar,.operator-mini { display: grid; place-items: center; border-radius: 8px; background: #e7e3fa; color: #6254b9; font-size: 9px; font-weight: 800; }.operator-avatar { width: 30px; height: 30px; }.sidebar-foot div:nth-child(2) { min-width: 0; flex: 1; }.sidebar-foot strong,.sidebar-foot small { display: block; }.sidebar-foot strong { font-size: 9.5px; }.sidebar-foot small { margin-top: 2px; color: #9299a6; font-size: 8px; }.sidebar-foot>span { color: #9ca2ad; font-size: 9px; }
.shell-content { min-width: 0; flex: 1; margin-left: 224px; }.topbar { position: sticky; z-index: 15; top: 0; display: flex; height: 58px; align-items: center; justify-content: space-between; padding: 0 28px; border-bottom: 1px solid #e1e4e9; background: rgba(255,255,255,.92); backdrop-filter: blur(12px); }.topbar>div { display: flex; align-items: center; gap: 9px; }.breadcrumb { color: #969da9; font-size: 10px; }.topbar i { color: #c0c4cb; font-style: normal; }.topbar strong { color: #434c5c; font-size: 10px; }.topbar-actions { gap: 13px !important; }.connection { display: flex; align-items: center; gap: 6px; color: #717a89; font-size: 9px; }.connection i { width: 6px; height: 6px; border-radius: 50%; background: #d79a2b; }.connection[data-status="connected"] i { background: #23a172; }.connection[data-status="failed"] i { background: #ce5050; }.topbar-actions button { display: grid; width: 27px; height: 27px; place-items: center; border: 1px solid #e0e3e8; border-radius: 7px; background: #fff; color: #5e6878; cursor: pointer; }.operator-mini { width: 27px; height: 27px; }main { min-height: calc(100vh - 58px); }
@media (max-width: 900px) { .sidebar { width: 70px; }.brand { padding: 0 20px; }.brand>span:last-child,nav p,nav a b,.sidebar-foot>div:not(.operator-avatar),.sidebar-foot>span { display: none; }nav a { justify-content: center; padding: 0; font-size: 0; }nav a span { width: auto; font-size: 15px; }.shell-content { margin-left: 70px; } }
</style>
