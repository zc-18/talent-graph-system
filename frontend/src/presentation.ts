export const FEEDBACK_STATUS_LABEL: Record<string, string> = {
  submitted: '已提交',
  triaged: '已分诊',
  approved: '已批准',
  rejected: '已驳回',
  applied: '已应用',
}

export const FEEDBACK_STATUS_TONE: Record<string, string> = {
  submitted: 'amber',
  triaged: 'cyan',
  approved: 'indigo',
  rejected: 'rose',
  applied: 'emerald',
}

export function formatDataTime(value?: string | null): string {
  if (!value) return '数据时间待记录'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}
