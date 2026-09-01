import { TicketNotFoundError } from './ticketsApi'

/**
 * Agent Run 时间线上的一个真实步骤。
 *
 * 后端只在对应 Tool 真实返回之后才写入步骤，因此这里的每一条都代表一次已经发生
 * 的执行。前端不补齐、不排序、不推断状态，只按后端给出的顺序原样展示。
 */
export interface AgentRunStep {
  step_order: number
  name: string
  status: string
  started_at: string | null
  completed_at: string | null
  error_message: string | null
}

/** 本次 Run 真实创建并落库的换货单 */
export interface AgentRunReplacement {
  business_key: string
  status: string
  product_sku: string
  reason: string
  is_demo_data: boolean
  created_at: string
}

/** 执行之后工单的真实持久化状态；未被回写时各结果字段保持 null */
export interface AgentRunTicketResult {
  status: string
  resolution: string | null
  resolution_summary: string | null
  resolved_at: string | null
  replacement_key: string | null
}

/** T025 policy retrieval 返回的一条真实 passage，全部来自后端 retrieval result */
export interface PolicyBasisPassage {
  rank: number
  score: number
  chunk_key: string
  chunk_order: number
  passage: string
}

/** T025 政策依据：真实 policy retrieval 的来源与 selected passages */
export interface PolicyBasis {
  status: string
  query_summary: string
  document_key: string | null
  document_title: string | null
  source_reference: string | null
  is_demo_data: boolean | null
  failure_reason: string | null
  passages: PolicyBasisPassage[]
}

export interface AgentRunRisk {
  action: string
  level: string
  rule_code: string
  requires_approval: boolean
  reason: string
  order_key: string | null
  order_amount: string | null
  approval_threshold_amount: string | null
  policy_key: string | null
}

export interface AgentRunApprovalRequest {
  approval_key: string
  status: string
  protected_action: string
  created_at: string
  resolved_at: string | null
  risk: AgentRunRisk
}

export interface AgentRunRecommendation {
  action: string
  issue_summary: string
  confidence: number
}

export interface AgentRunRecord {
  business_key: string
  ticket_key: string
  status: string
  created_at: string
  started_at: string | null
  completed_at: string | null
  error_message: string | null
  steps: AgentRunStep[]
  replacement: AgentRunReplacement | null
  ticket_result: AgentRunTicketResult
  recommendation: AgentRunRecommendation | null
  policy_basis: PolicyBasis | null
  risk: AgentRunRisk | null
  approval_request: AgentRunApprovalRequest | null
}

const TICKETS_ENDPOINT = 'http://localhost:8000/tickets'

/** 工单存在但从未执行过 Agent Run 时抛出，用于与网络 / 服务故障区分 */
export class AgentRunNotFoundError extends Error {
  constructor(ticketKey: string) {
    super(`工单 ${ticketKey} 尚未执行过 Agent Run`)
    this.name = 'AgentRunNotFoundError'
  }
}

function isNullableString(value: unknown): boolean {
  return typeof value === 'string' || value === null
}

function isAgentRunStep(value: unknown): value is AgentRunStep {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const record = value as Record<string, unknown>
  return (
    typeof record.step_order === 'number' &&
    typeof record.name === 'string' &&
    typeof record.status === 'string' &&
    isNullableString(record.started_at) &&
    isNullableString(record.completed_at) &&
    isNullableString(record.error_message)
  )
}

function isAgentRunReplacement(value: unknown): value is AgentRunReplacement {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const record = value as Record<string, unknown>
  return (
    typeof record.business_key === 'string' &&
    typeof record.status === 'string' &&
    typeof record.product_sku === 'string' &&
    typeof record.reason === 'string' &&
    typeof record.is_demo_data === 'boolean' &&
    typeof record.created_at === 'string'
  )
}

function isAgentRunTicketResult(value: unknown): value is AgentRunTicketResult {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const record = value as Record<string, unknown>
  return (
    typeof record.status === 'string' &&
    isNullableString(record.resolution) &&
    isNullableString(record.resolution_summary) &&
    isNullableString(record.resolved_at) &&
    isNullableString(record.replacement_key)
  )
}

function isPolicyBasisPassage(value: unknown): value is PolicyBasisPassage {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const record = value as Record<string, unknown>
  return (
    typeof record.rank === 'number' &&
    typeof record.score === 'number' &&
    typeof record.chunk_key === 'string' &&
    typeof record.chunk_order === 'number' &&
    typeof record.passage === 'string'
  )
}

function isAgentRunRecommendation(value: unknown): value is AgentRunRecommendation {
  if (typeof value !== 'object' || value === null) return false
  const record = value as Record<string, unknown>
  return (
    typeof record.action === 'string' &&
    typeof record.issue_summary === 'string' &&
    typeof record.confidence === 'number'
  )
}

function isPolicyBasis(value: unknown): value is PolicyBasis {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const record = value as Record<string, unknown>
  return (
    typeof record.status === 'string' &&
    typeof record.query_summary === 'string' &&
    isNullableString(record.document_key) &&
    isNullableString(record.document_title) &&
    isNullableString(record.source_reference) &&
    (typeof record.is_demo_data === 'boolean' || record.is_demo_data === null) &&
    isNullableString(record.failure_reason) &&
    Array.isArray(record.passages) &&
    record.passages.every(isPolicyBasisPassage)
  )
}

export function isAgentRunRisk(value: unknown): value is AgentRunRisk {
  if (typeof value !== 'object' || value === null) return false
  const record = value as Record<string, unknown>
  return (
    typeof record.action === 'string' &&
    typeof record.level === 'string' &&
    typeof record.rule_code === 'string' &&
    typeof record.requires_approval === 'boolean' &&
    typeof record.reason === 'string' &&
    isNullableString(record.order_key) &&
    isNullableString(record.order_amount) &&
    isNullableString(record.approval_threshold_amount) &&
    isNullableString(record.policy_key)
  )
}

function isAgentRunApprovalRequest(value: unknown): value is AgentRunApprovalRequest {
  if (typeof value !== 'object' || value === null) return false
  const record = value as Record<string, unknown>
  return (
    typeof record.approval_key === 'string' &&
    typeof record.status === 'string' &&
    typeof record.protected_action === 'string' &&
    typeof record.created_at === 'string' &&
    isNullableString(record.resolved_at) &&
    isAgentRunRisk(record.risk)
  )
}

function isAgentRunRecord(value: unknown): value is AgentRunRecord {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const record = value as Record<string, unknown>
  return (
    typeof record.business_key === 'string' &&
    typeof record.ticket_key === 'string' &&
    typeof record.status === 'string' &&
    typeof record.created_at === 'string' &&
    isNullableString(record.started_at) &&
    isNullableString(record.completed_at) &&
    isNullableString(record.error_message) &&
    Array.isArray(record.steps) &&
    record.steps.every(isAgentRunStep) &&
    (record.replacement === null || isAgentRunReplacement(record.replacement)) &&
    isAgentRunTicketResult(record.ticket_result) &&
    (record.recommendation === null || isAgentRunRecommendation(record.recommendation)) &&
    (record.policy_basis === null || isPolicyBasis(record.policy_basis)) &&
    (record.risk === null || isAgentRunRisk(record.risk)) &&
    (record.approval_request === null || isAgentRunApprovalRequest(record.approval_request))
  )
}

export function parseAgentRun(data: unknown): AgentRunRecord {
  if (!isAgentRunRecord(data)) {
    throw new Error('Agent Run 接口返回了非预期的数据结构')
  }
  return data
}

/**
 * 启动一次真实的 Agent Run，并返回执行结束后的真实结果。
 *
 * HTTP 201 只表示"这次 Run 已被创建并执行完毕"。执行成功与否一律以返回记录中的
 * ``status`` 为准：Tool 失败时它是 ``failed``，前端不会因为请求成功就显示成功。
 */
export async function startAgentRun(ticketKey: string): Promise<AgentRunRecord> {
  const response = await fetch(
    `${TICKETS_ENDPOINT}/${encodeURIComponent(ticketKey)}/agent-runs`,
    { method: 'POST' }
  )
  if (response.status === 404) {
    throw new TicketNotFoundError(ticketKey)
  }
  if (!response.ok) {
    throw new Error(`Agent Run 启动接口返回异常状态: ${response.status}`)
  }
  return parseAgentRun(await response.json())
}

/** 读取该工单最近一次真实执行的 Agent Run；从未执行过时抛出 AgentRunNotFoundError */
export async function fetchLatestAgentRun(ticketKey: string): Promise<AgentRunRecord> {
  const response = await fetch(
    `${TICKETS_ENDPOINT}/${encodeURIComponent(ticketKey)}/agent-runs/latest`
  )
  if (response.status === 404) {
    throw new AgentRunNotFoundError(ticketKey)
  }
  if (!response.ok) {
    throw new Error(`Agent Run 接口返回异常状态: ${response.status}`)
  }
  return parseAgentRun(await response.json())
}
