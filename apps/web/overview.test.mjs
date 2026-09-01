import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const projectionSource = readFileSync(
  new URL('./src/lib/overviewProjection.ts', import.meta.url),
  'utf8',
)
const projectionJavaScript = ts.transpileModule(projectionSource, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2020,
  },
}).outputText
const {
  activityHref,
  buildAttentionItems,
  buildOverviewKpis,
  isPolicyBlocked,
  selectOperationSnapshot,
  selectRecentTickets,
} = await import(`data:text/javascript;base64,${Buffer.from(projectionJavaScript).toString('base64')}`)

const TODAY = new Date('2026-09-01T12:00:00')
const TODAY_ISO = '2026-09-01T08:00:00'
const YESTERDAY_ISO = '2026-08-31T08:00:00'

function ticket(overrides = {}) {
  return {
    business_key: 'ticket-1',
    subject: '换货申请',
    issue_type: '换货',
    description: '商品损坏，需要换货',
    status: 'open',
    demo_scenario: null,
    is_demo_data: false,
    created_at: YESTERDAY_ISO,
    updated_at: TODAY_ISO,
    customer_name: '李先生',
    order_key: 'order-1',
    order_amount: '1299.00',
    agent_run_key: 'run-1',
    agent_run_status: 'running',
    risk_level: 'high',
    ...overrides,
  }
}

function ticketDetail(overrides = {}) {
  return {
    business_key: 'ticket-1',
    subject: '换货申请',
    issue_type: '换货',
    description: '商品损坏，需要换货',
    status: 'open',
    demo_scenario: null,
    is_demo_data: false,
    created_at: YESTERDAY_ISO,
    updated_at: TODAY_ISO,
    customer: {
      business_key: 'customer-1',
      name: '李先生',
      email: 'customer@example.test',
      phone: null,
      is_demo_data: false,
    },
    order: {
      business_key: 'order-1',
      product_sku: 'SKU-1',
      product_name: '智能设备',
      purchased_at: '2026-08-20T08:00:00',
      status: 'delivered',
      amount: '1299.00',
      is_demo_data: false,
    },
    ...overrides,
  }
}

function risk(overrides = {}) {
  return {
    action: 'create_replacement',
    level: 'high',
    rule_code: 'high_value_replacement',
    requires_approval: true,
    reason: '订单金额超过自动授权上限',
    order_key: 'order-1',
    order_amount: '1299.00',
    approval_threshold_amount: '500.00',
    policy_key: 'policy-1',
    ...overrides,
  }
}

function run(overrides = {}) {
  const base = {
    business_key: 'run-1',
    ticket_key: 'ticket-1',
    status: 'running',
    created_at: TODAY_ISO,
    started_at: TODAY_ISO,
    completed_at: null,
    error_message: null,
    steps: [{
      step_order: 1,
      name: 'retrieve_policy',
      status: 'running',
      started_at: TODAY_ISO,
      completed_at: null,
      error_message: null,
    }],
    replacement: null,
    ticket_result: {
      status: 'in_progress',
      resolution: null,
      resolution_summary: null,
      resolved_at: null,
      replacement_key: null,
    },
    recommendation: {
      action: 'create_replacement',
      issue_summary: '商品损坏',
      confidence: 0.98,
    },
    policy_basis: null,
    risk: null,
    approval_request: null,
  }
  return { ...base, ...overrides }
}

function runItem(runOverrides = {}, ticketOverrides = {}) {
  return {
    agent_run: run(runOverrides),
    ticket: ticketDetail(ticketOverrides),
  }
}

function approvalItem(overrides = {}) {
  const approvalRisk = risk()
  const approval = {
    approval_key: 'approval-1',
    status: 'pending',
    protected_action: 'create_replacement',
    agent_run_key: 'run-waiting',
    agent_run_status: 'waiting_for_approval',
    resolved_at: null,
    decision_reason: null,
    risk: approvalRisk,
    ...overrides,
  }
  return {
    approval,
    created_at: TODAY_ISO,
    ticket: ticketDetail(),
    agent_run: run({
      business_key: approval.agent_run_key,
      status: approval.agent_run_status,
      risk: approvalRisk,
      approval_request: {
        approval_key: approval.approval_key,
        status: approval.status,
        protected_action: approval.protected_action,
        created_at: TODAY_ISO,
        resolved_at: approval.resolved_at,
        decision_reason: approval.decision_reason,
        risk: approvalRisk,
      },
    }),
  }
}

function metricMap(items) {
  return Object.fromEntries(items.map(item => [item.key, item]))
}

const apiTickets = [
  ticket({ business_key: 'ticket-1', status: 'open' }),
  ticket({ business_key: 'ticket-2', status: 'in_progress' }),
  ticket({ business_key: 'ticket-3', status: 'waiting_for_approval' }),
  ticket({ business_key: 'ticket-4', status: 'resolved' }),
  ticket({ business_key: 'ticket-5', status: 'closed' }),
]

const apiRuns = [
  runItem({ business_key: 'run-active-1', ticket_key: 'ticket-1', status: 'running' }),
  runItem({ business_key: 'run-active-duplicate', ticket_key: 'ticket-1', status: 'queued' }),
  runItem({ business_key: 'run-active-2', ticket_key: 'ticket-2', status: 'running' }),
  runItem({
    business_key: 'run-completed',
    ticket_key: 'ticket-4',
    status: 'completed',
    completed_at: TODAY_ISO,
    ticket_result: {
      status: 'resolved',
      resolution: 'replacement_created',
      resolution_summary: '换货完成',
      resolved_at: TODAY_ISO,
      replacement_key: 'replacement-1',
    },
  }),
  runItem({
    business_key: 'run-blocked',
    ticket_key: 'ticket-blocked',
    status: 'failed',
    completed_at: TODAY_ISO,
    error_message: 'replacement_window_expired: blocked by policy',
  }, {
    business_key: 'ticket-blocked',
    issue_type: '超期换货',
  }),
  runItem({
    business_key: 'run-failed',
    ticket_key: 'ticket-failed',
    status: 'failed',
    completed_at: TODAY_ISO,
    error_message: 'inventory service timeout',
  }, {
    business_key: 'ticket-failed',
    issue_type: '库存检查',
  }),
  runItem({
    business_key: 'run-waiting',
    ticket_key: 'ticket-3',
    status: 'waiting_for_approval',
    risk: risk(),
  }),
]

const apiApprovals = [
  approvalItem(),
  approvalItem({
    approval_key: 'approval-resolved',
    status: 'approved',
    resolved_at: TODAY_ISO,
  }),
]

test('renders six real KPI projections with correct counts and module navigation', () => {
  const kpis = buildOverviewKpis(
    apiTickets,
    apiRuns,
    apiApprovals,
    { tickets: 'ready', runs: 'ready', approvals: 'ready' },
    TODAY,
  )
  const metrics = metricMap(kpis)

  assert.equal(kpis.length, 6, 'KPI render must expose six operational metrics')
  assert.equal(metrics.open.value, 3, 'resolved and closed tickets are terminal')
  assert.equal(metrics.processing.value, 2, 'active AgentRuns are deduplicated by ticket')
  assert.equal(metrics.approval.value, 1, 'only pending approvals count')
  assert.equal(metrics.completed.value, 1, 'business completion uses today resolved_at')
  assert.equal(metrics.blocked.value, 1, 'policy-blocked runs have their own KPI')
  assert.equal(metrics.failed.value, 1, 'technical failures exclude policy blocks')

  assert.equal(metrics.open.href, '#/tickets')
  assert.equal(metrics.processing.href, '#/agent-runs')
  assert.equal(metrics.approval.href, '#/approvals')
})

test('keeps Blocked and Failed semantics distinct', () => {
  const blocked = apiRuns.find(item => item.agent_run.business_key === 'run-blocked')
  const failed = apiRuns.find(item => item.agent_run.business_key === 'run-failed')

  assert.equal(isPolicyBlocked(blocked.agent_run), true)
  assert.equal(isPolicyBlocked(failed.agent_run), false)
})

test('Needs Attention produces actionable approval, failed-run, and blocked-ticket entries', () => {
  const attention = buildAttentionItems(apiRuns, apiApprovals)
  const pending = attention.find(item => item.kind === 'approval')
  const blocked = attention.find(item => item.kind === 'blocked')
  const failed = attention.find(item => item.kind === 'failed')

  assert.equal(pending.action, '去审批')
  assert.equal(pending.href, '#/approvals')
  assert.match(pending.meta, /1[,.]?299/)

  assert.equal(failed.action, '查看 Run')
  assert.equal(failed.href, '#/agent-runs/run-failed')
  assert.match(failed.detail, /timeout/)

  assert.equal(blocked.action, '查看 Ticket')
  assert.equal(blocked.href, '#/tickets/ticket-blocked')
  assert.match(blocked.detail, /replacement_window_expired/)
})

test('AI Operations snapshot prioritizes actionable runs and stays compact', () => {
  const snapshot = selectOperationSnapshot(apiRuns)

  assert.equal(snapshot.length, 5)
  assert.equal(snapshot[0].agent_run.status, 'waiting_for_approval')
  assert.equal(snapshot[1].agent_run.status, 'running')
  assert.ok(snapshot.some(item => item.agent_run.business_key === 'run-failed'))
})

test('Service Operations snapshot selects the five most recently updated tickets', () => {
  const source = [
    ticket({ business_key: 'old', updated_at: '2026-08-01T08:00:00' }),
    ...Array.from({ length: 6 }, (_, index) => ticket({
      business_key: `recent-${index}`,
      updated_at: `2026-09-0${index + 1}T08:00:00`,
    })),
  ]
  const snapshot = selectRecentTickets(source)

  assert.equal(snapshot.length, 5)
  assert.equal(snapshot[0].business_key, 'recent-5')
  assert.equal(snapshot.some(item => item.business_key === 'old'), false)
})

test('Recent Activity routes AuditEvent entities to Approval, Ticket, or Run', () => {
  const baseEvent = {
    event_key: 'audit-1',
    event_type: 'ticket_updated',
    actor_type: 'agent',
    occurred_at: TODAY_ISO,
    outcome: 'updated',
    success: true,
    action: 'update_ticket',
    summary: '工单状态已更新',
    affected_object_type: 'agent_run',
    affected_object_key: 'run-1',
    reference_type: null,
    reference_key: null,
    runKey: 'run-1',
    ticketKey: 'ticket-1',
  }

  assert.equal(activityHref({
    ...baseEvent,
    reference_type: 'approval_request',
    reference_key: 'approval-1',
  }), '#/approvals')
  assert.equal(activityHref({
    ...baseEvent,
    affected_object_type: 'ticket',
    affected_object_key: 'ticket-1',
  }), '#/tickets/ticket-1')
  assert.equal(activityHref(baseEvent), '#/agent-runs/run-1')
})

test('empty API responses create zero projections and actionable product empty states', () => {
  const kpis = buildOverviewKpis(
    [],
    [],
    [],
    { tickets: 'empty', runs: 'empty', approvals: 'empty' },
    TODAY,
  )

  assert.deepEqual(kpis.map(item => item.value), [0, 0, 0, 0, 0, 0])
  assert.equal(buildAttentionItems([], []).length, 0)
  assert.equal(selectOperationSnapshot([]).length, 0)
  assert.equal(selectRecentTickets([]).length, 0)

  const view = readFileSync(new URL('./src/views/OverviewView.vue', import.meta.url), 'utf8')
  assert.match(view, /No actions need your attention/)
  assert.match(view, /AI operations normal/)
  assert.match(view, /No approvals waiting/)
  assert.match(view, /No recent service activity/)
  assert.match(view, /No service tickets yet/)
})

test('loading and partial-error states remain independent by data source', () => {
  const loading = metricMap(buildOverviewKpis(
    apiTickets,
    apiRuns,
    apiApprovals,
    { tickets: 'loading', runs: 'ready', approvals: 'failed' },
    TODAY,
  ))

  assert.equal(loading.open.source, 'loading')
  assert.equal(loading.processing.source, 'ready')
  assert.equal(loading.approval.source, 'failed')

  const view = readFileSync(new URL('./src/views/OverviewView.vue', import.meta.url), 'utf8')
  assert.match(view, /runsState === 'loading'/)
  assert.match(view, /approvalsState === 'loading'/)
  assert.match(view, /ticketsState === 'loading'/)
  assert.match(view, /Approval data unavailable/)
  assert.match(view, /Agent Run data unavailable/)
  assert.match(view, /Ticket data unavailable/)
  assert.match(view, /部分数据暂不可用/)
  assert.doesNotMatch(view, /v-if="[^"]*failed[^"]*"[\s\S]{0,80}<OverviewView/)
})

test('render contract exposes all snapshots, CTAs, and stable navigation targets', () => {
  const view = readFileSync(new URL('./src/views/OverviewView.vue', import.meta.url), 'utf8')
  const app = readFileSync(new URL('./src/App.vue', import.meta.url), 'utf8')

  for (const testId of [
    'overview-kpis',
    'needs-attention',
    'ai-operations',
    'approval-snapshot',
    'recent-activity',
    'service-snapshot',
    'review-approval',
  ]) {
    assert.match(view, new RegExp(`data-testid=["']${testId}["']`))
  }

  assert.match(view, /fetchAuditEvents/)
  assert.match(view, /Review Approval/)
  assert.match(view, /查看 Run/)
  assert.match(view, /View all tickets/)
  assert.match(view, /href="#\/approvals"/)
  assert.match(view, /href="#\/agent-runs"/)
  assert.match(view, /href="#\/tickets"/)

  assert.match(app, /<OverviewView v-else/)
  assert.match(app, /href="#\/" :class="\{ active: isOverview \}"/)
  assert.match(app, /href="#\/tickets" :class="\{ active: isServiceOperations/)
})
