import dayjs from 'dayjs'

export function formatPercent(val) {
  if (val == null) return '--'
  const sign = val >= 0 ? '+' : ''
  return sign + (val * 100).toFixed(2) + '%'
}

export function formatNumber(val) {
  if (val == null) return '--'
  if (Math.abs(val) >= 100000000) {
    return (val / 100000000).toFixed(2) + '亿'
  }
  if (Math.abs(val) >= 10000) {
    return (val / 10000).toFixed(2) + '万'
  }
  return val.toLocaleString('zh-CN')
}

export function formatDate(date, fmt) {
  return date ? dayjs(date).format(fmt || 'YYYY-MM-DD') : '--'
}

export function formatDateTime(date) {
  return date ? dayjs(date).format('YYYY-MM-DD HH:mm:ss') : '--'
}

// ===== 量化专用格式化器 =====

/** IC/RankIC/ICIR 等相关系数，保留 4 位 */
export function formatIc(val) {
  if (val == null || val === '') return '--'
  return Number(val).toFixed(4)
}

/** 夏普/索提诺等风险调整收益，保留 2 位 */
export function formatSharpe(val) {
  if (val == null || val === '') return '--'
  return Number(val).toFixed(2)
}

/** 换手率，保留 2 位 */
export function formatTurnover(val) {
  if (val == null || val === '') return '--'
  return Number(val).toFixed(2)
}

/** 正负数语义色 class（正数 success，负数 danger） */
export function numClass(val) {
  if (val == null || val === '') return ''
  return Number(val) >= 0 ? 'num--success' : 'num--danger'
}

// ===== 挖掘任务标签映射 =====
export const miningTypeLabel = { llm: 'LLM', symbolic: '符号', text: '文本', automl: 'AutoML' }
export const miningTypeBadge = (t) => ({ llm: 'primary', symbolic: 'warning', text: 'info', automl: 'danger' }[t] || 'info')
export const taskStatusLabel = { pending: '等待', running: '运行中', done: '完成', failed: '失败' }
export const taskStatusBadge = (s) => ({ pending: 'info', running: 'warning', done: 'success', failed: 'danger' }[s] || 'info')
