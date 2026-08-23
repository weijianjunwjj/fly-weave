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
