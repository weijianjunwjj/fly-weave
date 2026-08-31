import {
  isAgentRunRisk,
  parseAgentRun,
  type AgentRunRecord,
  type AgentRunRisk,
} from './agentRunsApi'
import {
  isTicketDetailRecord,
  type TicketDetailRecord,
} from './ticketsApi'

const API_BASE = 'http://localhost:8000'

export interface ApprovalDecision {
  approval_key: string
  status: string
  protected_action: string
  agent_run_key: string
  agent_run_status: string
  resolved_at: string | null
  decision_reason: string | null
  risk: AgentRunRisk
}

export interface ApprovalInboxItem {
  approval: ApprovalDecision
  created_at: string
  ticket: TicketDetailRecord
  agent_run: AgentRunRecord
}

export interface AuditEvent {
  event_key: string
  event_type: string
  actor_type: string
  occurred_at: string
  outcome: string
  success: boolean
  action: string
  summary: string
  affected_object_type: string | null
  affected_object_key: string | null
  reference_type: string | null
  reference_key: string | null
}

export interface ResumeResult {
  agent_run: AgentRunRecord
  approval: ApprovalDecision | null
}

function isNullableString(value: unknown): value is string | null {
  return typeof value === 'string' || value === null
}

function isApprovalDecision(value: unknown): value is ApprovalDecision {
  if (typeof value !== 'object' || value === null) return false
  const record = value as Record<string, unknown>
  return (
    typeof record.approval_key === 'string' &&
    typeof record.status === 'string' &&
    typeof record.protected_action === 'string' &&
    typeof record.agent_run_key === 'string' &&
    typeof record.agent_run_status === 'string' &&
    isNullableString(record.resolved_at) &&
    isNullableString(record.decision_reason) &&
    isAgentRunRisk(record.risk)
  )
}

function parseApprovalDecision(value: unknown): ApprovalDecision {
  if (!isApprovalDecision(value)) {
    throw new Error('审批接口返回了非预期的数据结构')
  }
  return value
}

function parseInboxItem(value: unknown): ApprovalInboxItem {
  if (typeof value !== 'object' || value === null) {
    throw new Error('审批列表返回了非预期的数据结构')
  }
  const record = value as Record<string, unknown>
  if (
    !isApprovalDecision(record.approval) ||
    typeof record.created_at !== 'string' ||
    !isTicketDetailRecord(record.ticket)
  ) {
    throw new Error('审批列表返回了非预期的数据结构')
  }
  return {
    approval: record.approval,
    created_at: record.created_at,
    ticket: record.ticket,
    agent_run: parseAgentRun(record.agent_run),
  }
}

async function readJson(response: Response): Promise<unknown> {
  if (!response.ok) {
    throw new Error(`审批服务暂时不可用（${response.status}）`)
  }
  return response.json()
}

export async function fetchApprovalInbox(): Promise<ApprovalInboxItem[]> {
  const response = await fetch(`${API_BASE}/approval-requests`)
  const data = await readJson(response)
  if (!Array.isArray(data)) {
    throw new Error('审批列表返回了非预期的数据结构')
  }
  return data.map(parseInboxItem)
}

async function decideApproval(
  approvalKey: string,
  decision: 'approve' | 'reject',
  note?: string,
): Promise<ApprovalDecision> {
  const response = await fetch(
    `${API_BASE}/approval-requests/${encodeURIComponent(approvalKey)}/${decision}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision_reason: note?.trim() || null }),
    },
  )
  return parseApprovalDecision(await readJson(response))
}

export function approveApproval(approvalKey: string): Promise<ApprovalDecision> {
  return decideApproval(approvalKey, 'approve')
}

export function rejectApproval(
  approvalKey: string,
  note?: string,
): Promise<ApprovalDecision> {
  return decideApproval(approvalKey, 'reject', note)
}

export async function resumeAgentRun(agentRunKey: string): Promise<ResumeResult> {
  const response = await fetch(
    `${API_BASE}/agent-runs/${encodeURIComponent(agentRunKey)}/resume`,
    { method: 'POST' },
  )
  const data = await readJson(response)
  if (typeof data !== 'object' || data === null) {
    throw new Error('恢复执行接口返回了非预期的数据结构')
  }
  const record = data as Record<string, unknown>
  return {
    agent_run: parseAgentRun(record.agent_run),
    approval:
      record.approval === null ? null : parseApprovalDecision(record.approval),
  }
}

function isAuditEvent(value: unknown): value is AuditEvent {
  if (typeof value !== 'object' || value === null) return false
  const record = value as Record<string, unknown>
  return (
    typeof record.event_key === 'string' &&
    typeof record.event_type === 'string' &&
    typeof record.actor_type === 'string' &&
    typeof record.occurred_at === 'string' &&
    typeof record.outcome === 'string' &&
    typeof record.success === 'boolean' &&
    typeof record.action === 'string' &&
    typeof record.summary === 'string' &&
    isNullableString(record.affected_object_type) &&
    isNullableString(record.affected_object_key) &&
    isNullableString(record.reference_type) &&
    isNullableString(record.reference_key)
  )
}

export async function fetchAuditEvents(agentRunKey: string): Promise<AuditEvent[]> {
  const response = await fetch(
    `${API_BASE}/agent-runs/${encodeURIComponent(agentRunKey)}/audit-events`,
  )
  const data = await readJson(response)
  if (!Array.isArray(data) || !data.every(isAuditEvent)) {
    throw new Error('审计接口返回了非预期的数据结构')
  }
  return data
}
