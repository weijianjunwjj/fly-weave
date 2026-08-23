const TICKET_STATUS_LABELS: Record<string, string> = {
  open: '待处理',
  in_progress: '处理中',
  waiting_for_approval: '等待审批',
  resolved: '已解决',
  closed: '已关闭',
}

const ORDER_STATUS_LABELS: Record<string, string> = {
  pending: '待支付',
  paid: '已支付',
  shipped: '已发货',
  delivered: '已送达',
  cancelled: '已取消',
}

/** 工单状态的中文展示名；未知状态回退为原始值，不做臆测 */
export function ticketStatusLabel(status: string): string {
  return TICKET_STATUS_LABELS[status] ?? status
}

/** 订单状态的中文展示名；未知状态回退为原始值，不做臆测 */
export function orderStatusLabel(status: string): string {
  return ORDER_STATUS_LABELS[status] ?? status
}
