export interface TicketRecord {
  business_key: string
  subject: string
  description: string
  status: string
  demo_scenario: string | null
  is_demo_data: boolean
  created_at: string
}

const TICKETS_ENDPOINT = 'http://localhost:8000/tickets'

function isTicketRecord(value: unknown): value is TicketRecord {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const record = value as Record<string, unknown>
  return (
    typeof record.business_key === 'string' &&
    typeof record.subject === 'string' &&
    typeof record.description === 'string' &&
    typeof record.status === 'string' &&
    typeof record.is_demo_data === 'boolean' &&
    typeof record.created_at === 'string'
  )
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

export interface TicketDetailRecord extends TicketRecord {
  customer: TicketCustomerContext | null
  order: TicketOrderContext | null
}

/** 工单业务标识在后端不存在时抛出，用于与网络 / 服务故障区分 */
export class TicketNotFoundError extends Error {
  constructor(businessKey: string) {
    super(`未找到工单: ${businessKey}`)
    this.name = 'TicketNotFoundError'
  }
}

function isCustomerContext(value: unknown): value is TicketCustomerContext {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const record = value as Record<string, unknown>
  return (
    typeof record.business_key === 'string' &&
    typeof record.name === 'string' &&
    typeof record.email === 'string' &&
    (typeof record.phone === 'string' || record.phone === null) &&
    typeof record.is_demo_data === 'boolean'
  )
}

function isOrderContext(value: unknown): value is TicketOrderContext {
  if (typeof value !== 'object' || value === null) {
    return false
  }
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
  if (!isTicketRecord(value)) {
    return false
  }
  const record = value as unknown as Record<string, unknown>
  return (
    (record.customer === null || isCustomerContext(record.customer)) &&
    (record.order === null || isOrderContext(record.order))
  )
}

export async function fetchTicketDetail(businessKey: string): Promise<TicketDetailRecord> {
  const response = await fetch(`${TICKETS_ENDPOINT}/${encodeURIComponent(businessKey)}`)
  if (response.status === 404) {
    throw new TicketNotFoundError(businessKey)
  }
  if (!response.ok) {
    throw new Error(`工单详情接口返回异常状态: ${response.status}`)
  }
  const data = await response.json()
  if (!isTicketDetailRecord(data)) {
    throw new Error('工单详情接口返回了非预期的数据结构')
  }
  return data
}
