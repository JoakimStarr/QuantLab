// Dashboard 子组件通用工具：类型/状态标签、时间判断、数值格式化

export const typeLabel = { llm: 'LLM', symbolic: '符号', text: '文本', automl: 'AutoML' }
export const typeBadgeClass = (t) => ({ llm: 'primary', symbolic: 'warning', text: 'info', automl: 'danger' }[t] || 'info')
export const statusLabel = { pending: '等待', running: '运行中', done: '完成', failed: '失败' }
export const statusBadgeClass = (s) => ({ pending: 'info', running: 'warning', done: 'success', failed: 'danger' }[s] || 'info')

export function isToday(dateStr) {
  if (!dateStr) return false
  const d = new Date(dateStr)
  const today = new Date()
  return d.toDateString() === today.toDateString()
}

export function isWithinDays(dateStr, days) {
  if (!dateStr) return false
  const d = new Date(dateStr)
  const cutoff = new Date()
  cutoff.setDate(cutoff.getDate() - days)
  return d >= cutoff
}

export function formatIc(v) {
  if (v == null || v === '') return '--'
  return Number(v).toFixed(4)
}

export function formatNum(v) {
  if (v == null || v === '') return '--'
  return Number(v).toFixed(2)
}

export function formatPercent(v) {
  if (v == null || v === '') return '--'
  return (Number(v) * 100).toFixed(1) + '%'
}

export function numClass(v) {
  if (v == null || v === '') return ''
  return Number(v) >= 0 ? 'num--success' : 'num--danger'
}
