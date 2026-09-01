import type { AgentRunCenterItem, AgentRunRecord } from './agentRunsApi'
import type { ApprovalInboxItem, AuditEvent } from './approvalsApi'
import type { TicketRecord } from './ticketsApi'

export type SourceState = 'loading' | 'ready' | 'empty' | 'failed'

export interface AttentionItem {
  key: string
  kind: 'approval' | 'failed' | 'blocked'
  eyebrow: string
  title: string
  detail: string
  meta: string | null
  href: string
  action: string
}

export interface ActivityLinkContext extends AuditEvent {
  runKey: string
  ticketKey: string
}

const ACTIVE_RUN_STATUSES = new Set(['queued', 'running'])
const TERMINAL_TICKET_STATUSES = new Set(['resolved', 'closed'])

function dateValue(value: string | null): number {
  if (!value) return 0
  return Date.parse(value.endsWith('Z') ? value : `${value}Z`)
}

export function isToday(value: string | null, now = new Date()): boolean {
  if (!value) return false
  const date = new Date(value.endsWith('Z') ? value : `${value}Z`)
  return date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate()
}

export function isPolicyBlocked(run: AgentRunRecord): boolean {
  const reason = run.error_message?.toLowerCase() ?? ''
  return run.status === 'failed'
    && (reason.includes('replacement_window_expired') || reason.includes('blocked'))
}

function formatMoney(value: string): string {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'CNY',
    maximumFractionDigits: 2,
  }).format(Number(value))
}

export function buildOverviewKpis(
  tickets: TicketRecord[],
  runs: AgentRunCenterItem[],
  approvals: ApprovalInboxItem[],
  states: {
    tickets: SourceState
    runs: SourceState
    approvals: SourceState
  },
  now = new Date(),
) {
  const pending = approvals.filter(({ approval }) => approval.status === 'pending')
  const blocked = runs.filter(({ agent_run }) => isPolicyBlocked(agent_run)).length
  const failed = runs.filter(({ agent_run }) =>
    agent_run.status === 'failed' && !isPolicyBlocked(agent_run),
  ).length

  return [
    {
      key: 'open',
      label: 'Open Tickets',
      caption: '未业务终结',
      value: tickets.filter(ticket => !TERMINAL_TICKET_STATUSES.has(ticket.status)).length,
      href: '#/tickets',
      tone: 'neutral',
      source: states.tickets,
    },
    {
      key: 'processing',
      label: 'AI Processing',
      caption: '当前活跃执行',
      value: new Set(
        runs
          .filter(({ agent_run }) => ACTIVE_RUN_STATUSES.has(agent_run.status))
          .map(({ agent_run }) => agent_run.ticket_key),
      ).size,
      href: '#/agent-runs',
      tone: 'purple',
      source: states.runs,
    },
    {
      key: 'approval',
      label: 'Waiting Approval',
      caption: '需要人工决策',
      value: pending.length,
      href: '#/approvals',
      tone: 'amber',
      source: states.approvals,
    },
    {
      key: 'completed',
      label: 'Completed Today',
      caption: '今日完成执行',
      value: runs.filter(({ agent_run }) =>
        agent_run.status === 'completed'
        && isToday(agent_run.ticket_result.resolved_at, now),
      ).length,
      href: '#/agent-runs',
      tone: 'green',
      source: states.runs,
    },
    {
      key: 'blocked',
      label: 'Policy Blocked',
      caption: '规则正确阻止',
      value: blocked,
      href: '#/agent-runs',
      tone: 'orange',
      source: states.runs,
    },
    {
      key: 'failed',
      label: 'Failed',
      caption: '系统执行错误',
      value: failed,
      href: '#/agent-runs',
      tone: 'red',
      source: states.runs,
    },
  ]
}

export function buildAttentionItems(
  runs: AgentRunCenterItem[],
  approvals: ApprovalInboxItem[],
): AttentionItem[] {
  const pending: AttentionItem[] = approvals
    .filter(({ approval }) => approval.status === 'pending')
    .map(item => ({
      key: item.approval.approval_key,
      kind: 'approval',
      eyebrow: 'WAITING APPROVAL',
      title: item.ticket.issue_type ?? item.ticket.subject,
      detail: item.approval.risk.reason,
      meta: item.approval.risk.order_amount
        ? formatMoney(item.approval.risk.order_amount)
        : item.approval.risk.level.toUpperCase(),
      href: '#/approvals',
      action: '去审批',
    }))

  const stopped: AttentionItem[] = runs
    .filter(({ agent_run }) => agent_run.status === 'failed')
    .map(({ agent_run, ticket }) => {
      const blocked = isPolicyBlocked(agent_run)
      return {
        key: agent_run.business_key,
        kind: blocked ? 'blocked' : 'failed',
        eyebrow: blocked ? 'POLICY BLOCKED' : 'EXECUTION FAILED',
        title: `${ticket.customer?.name ?? '未关联客户'} · ${ticket.issue_type ?? ticket.subject}`,
        detail: agent_run.error_message
          ?? (blocked ? '业务规则阻止了本次操作' : '执行未能完成'),
        meta: agent_run.risk?.level ? agent_run.risk.level.toUpperCase() : null,
        href: blocked
          ? `#/tickets/${encodeURIComponent(ticket.business_key)}`
          : `#/agent-runs/${encodeURIComponent(agent_run.business_key)}`,
        action: blocked ? '查看 Ticket' : '查看 Run',
      }
    })

  return [...pending, ...stopped].slice(0, 5)
}

export function selectOperationSnapshot(items: AgentRunCenterItem[]): AgentRunCenterItem[] {
  const priority: Record<string, number> = {
    waiting_for_approval: 0,
    running: 1,
    queued: 2,
    failed: 3,
    completed: 4,
    cancelled: 5,
  }
  return [...items]
    .sort((a, b) =>
      (priority[a.agent_run.status] ?? 9) - (priority[b.agent_run.status] ?? 9)
      || dateValue(b.agent_run.created_at) - dateValue(a.agent_run.created_at),
    )
    .slice(0, 5)
}

export function selectRecentTickets(items: TicketRecord[]): TicketRecord[] {
  return [...items]
    .sort((a, b) => dateValue(b.updated_at) - dateValue(a.updated_at))
    .slice(0, 5)
}

export function activityHref(event: ActivityLinkContext): string {
  if (
    event.reference_type === 'approval_request'
    || event.affected_object_type === 'approval_request'
  ) {
    return '#/approvals'
  }
  if (event.affected_object_type === 'ticket') {
    return `#/tickets/${encodeURIComponent(event.affected_object_key ?? event.ticketKey)}`
  }
  return `#/agent-runs/${encodeURIComponent(event.runKey)}`
}
