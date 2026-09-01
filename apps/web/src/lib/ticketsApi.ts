export interface TicketRecord {
  business_key: string
  subject: string
  issue_type: string | null
  description: string
  status: string
  demo_scenario: string | null
  is_demo_data: boolean
  created_at: string
  updated_at: string
  customer_name: string | null
  order_key: string | null
  order_amount: string | null
  agent_run_key: string | null
  agent_run_status: string | null
  risk_level: string | null
}

export interface TicketCustomerContext {
  business_key: string
  name: string
  email: string
  phone: string | null
  is_demo_data: boolean
}

export interface TicketOrderContext {
  business_key: string
  product_sku: string
  product_name: string
  purchased_at: string
  status: string
  amount: string
  is_demo_data: boolean
}

export interface TicketDetailRecord {
  business_key: string
  subject: string
  issue_type: string | null
  description: string
  status: string
  demo_scenario: string | null
  is_demo_data: boolean
  created_at: string
  updated_at: string
  customer: TicketCustomerContext | null
  order: TicketOrderContext | null
}

export interface CreateTicketInput {
  customer_name: string
  customer_email: string
  issue_type: '商品损坏' | '换货'
  issue_description: string
  order_id: string
  order_amount: number
}

const TICKETS_ENDPOINT = 'http://localhost:8000/tickets'

function isNullableString(value: unknown): value is string | null {
  return typeof value === 'string' || value === null
}

function isTicketBase(record: Record<string, unknown>): boolean {
  return (
    typeof record.business_key === 'string' &&
    typeof record.subject === 'string' &&
    isNullableString(record.issue_type) &&
    typeof record.description === 'string' &&
    typeof record.status === 'string' &&
    isNullableString(record.demo_scenario) &&
    typeof record.is_demo_data === 'boolean' &&
    typeof record.created_at === 'string' &&
    typeof record.updated_at === 'string'
  )
}

function isTicketRecord(value: unknown): value is TicketRecord {
  if (typeof value !== 'object' || value === null) return false
  const record = value as Record<string, unknown>
  return (
    isTicketBase(record) &&
    isNullableString(record.customer_name) &&
    isNullableString(record.order_key) &&
    isNullableString(record.order_amount) &&
    isNullableString(record.agent_run_key) &&
    isNullableString(record.agent_run_status) &&
    isNullableString(record.risk_level)
  )
}

function isCustomerContext(value: unknown): value is TicketCustomerContext {
  if (typeof value !== 'object' || value === null) return false
  const record = value as Record<string, unknown>
  return (
    typeof record.business_key === 'string' &&
    typeof record.name === 'string' &&
    typeof record.email === 'string' &&
    isNullableString(record.phone) &&
    typeof record.is_demo_data === 'boolean'
  )
}

function isOrderContext(value: unknown): value is TicketOrderContext {
  if (typeof value !== 'object' || value === null) return false
  const record = value as Record<string, unknown>
  return (
    typeof record.business_key === 'string' &&
    typeof record.product_sku === 'string' &&
    typeof record.product_name === 'string' &&
    typeof record.purchased_at === 'string' &&
    typeof record.status === 'string' &&
    typeof record.amount === 'string' &&
    typeof record.is_demo_data === 'boolean'
  )
}

export function isTicketDetailRecord(value: unknown): value is TicketDetailRecord {
  if (typeof value !== 'object' || value === null) return false
  const record = value as Record<string, unknown>
  return (
    isTicketBase(record) &&
    (record.customer === null || isCustomerContext(record.customer)) &&
    (record.order === null || isOrderContext(record.order))
  )
}

async function apiError(response: Response, fallback: string): Promise<Error> {
  try {
    const body = await response.json() as { detail?: unknown }
    if (typeof body.detail === 'string') return new Error(body.detail)
  } catch {
    // 使用稳定的 fallback，不把无效响应伪装成业务错误。
  }
  return new Error(fallback)
}

export async function fetchTickets(): Promise<TicketRecord[]> {
  const response = await fetch(TICKETS_ENDPOINT)
  if (!response.ok) {
    throw new Error(`工单接口返回异常状态: ${response.status}`)
  }
  const data = await response.json()
  if (!Array.isArray(data) || !data.every(isTicketRecord)) {
    throw new Error('工单接口返回了非预期的数据结构')
  }
  return data
}

export class TicketNotFoundError extends Error {
  constructor(businessKey: string) {
    super(`未找到工单: ${businessKey}`)
    this.name = 'TicketNotFoundError'
  }
}

export async function fetchTicketDetail(businessKey: string): Promise<TicketDetailRecord> {
  const response = await fetch(`${TICKETS_ENDPOINT}/${encodeURIComponent(businessKey)}`)
  if (response.status === 404) throw new TicketNotFoundError(businessKey)
  if (!response.ok) {
    throw new Error(`工单详情接口返回异常状态: ${response.status}`)
  }
  const data = await response.json()
  if (!isTicketDetailRecord(data)) {
    throw new Error('工单详情接口返回了非预期的数据结构')
  }
  return data
}

export async function createTicket(input: CreateTicketInput): Promise<TicketDetailRecord> {
  const response = await fetch(TICKETS_ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!response.ok) {
    throw await apiError(response, `工单创建失败（${response.status}）`)
  }
  const data = await response.json()
  if (!isTicketDetailRecord(data)) {
    throw new Error('工单创建接口返回了非预期的数据结构')
  }
  return data
}
