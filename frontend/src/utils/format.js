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