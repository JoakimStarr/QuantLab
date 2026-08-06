// 公共格式化工具（收拢各页面重复的格式化函数）
// 用法：import { fmt, fmtPct, fmtNum, humanSize, formatTime, formatDuration, numClass, fmtThousand } from '@/utils/format'

export function formatTime(ts) {
  if (!ts) return '--'
  return ts.replace('T', ' ').slice(0, 19)
}

export function formatDuration(seconds) {
  if (!seconds || seconds <= 0) return '--'
  if (seconds < 60) return seconds + 's'
  if (seconds < 3600) return Math.floor(seconds / 60) + 'm' + (seconds % 60 > 0 ? Math.round(seconds % 60) + 's' : '')
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return h + 'h' + (m > 0 ? m + 'm' : '')
}

export function humanSize(bytes) {
  if (bytes === null || bytes === undefined) return '--'
  const units = ['B', 'KB', 'MB', 'GB']
  let size = bytes
  let i = 0
  while (size >= 1024 && i < units.length - 1) {
    size /= 1024
    i++
  }
  return `${size.toFixed(size >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}

// 数值格式化：空值显示 —（suffix='%' 时小数 ×100）
export function fmt(val, digits = 3, suffix = '') {
  if (val === null || val === undefined || val === '') return '—'
  const n = Number(val)
  if (Number.isNaN(n)) return '—'
  const display = suffix === '%' ? n * 100 : n
  return display.toFixed(digits) + suffix
}

// 正负数着色：正数 success，负数 danger
export function numClass(val) {
  const n = Number(val)
  if (Number.isNaN(n) || n === 0) return ''
  return n > 0 ? 'is-positive' : 'is-negative'
}

// 小数 → 百分比
export function fmtPct(v, digits = 2) {
  if (v == null || v === '') return '--'
  return (v * 100).toFixed(digits) + '%'
}

// 数值固定小数（空值 --）
export function fmtNum(v, digits = 2) {
  if (v == null || v === '') return '--'
  return Number(v).toFixed(digits)
}

// 千分位整数/小数
export function fmtThousand(v, digits = 2) {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '--'
  const n = Number(v)
  const parts = n.toFixed(digits).split('.')
  parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return parts.join('.')
}
