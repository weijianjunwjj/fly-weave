/**
 * Audit Center 数据层（T031）。
 *
 * 只读取后端真实持久化的 ``AuditEvent`` 聚合接口，不创建前端假事件、不复制
 * audit store、不硬编码时间线。事件字段与 ``GET /audit-events`` 一一对应，
 * ``metadata`` 保留原始结构化元数据（reason_code / rule_code / decision_reason
 * 等），仅用于默认折叠的 Developer Details。
 */

const API_BASE = 'http://localhost:8000'

export interface AuditCenterEvent {
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
  ticket_key: string
  agent_run_key: string
  approval_key: string | null
  metadata: Record<string, unknown> | null
}

function isNullableString(value: unknown): value is string | null {
  return typeof value === 'string' || value === null
}

function isNullableRecord(value: unknown): value is Record<string, unknown> | null {
  if (value === null) return true
  return typeof value === 'object' && !Array.isArray(value)
}

function isAuditCenterEvent(value: unknown): value is AuditCenterEvent {
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
    isNullableString(record.reference_key) &&
    typeof record.ticket_key === 'string' &&
    typeof record.agent_run_key === 'string' &&
    isNullableString(record.approval_key) &&
    isNullableRecord(record.metadata)
  )
}

export async function fetchAuditEvents(): Promise<AuditCenterEvent[]> {
  const response = await fetch(`${API_BASE}/audit-events`)
  if (!response.ok) {
    throw new Error(`审计接口返回异常状态: ${response.status}`)
  }
  const data = await response.json()
  if (!Array.isArray(data) || !data.every(isAuditCenterEvent)) {
    throw new Error('审计接口返回了非预期的数据结构')
  }
  return data
}
