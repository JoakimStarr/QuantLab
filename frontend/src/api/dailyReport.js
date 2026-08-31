import request from './index'

// === 每日晨报 / 盘前简报 ===

// 取某一天（或最新）的晨报；未生成返回 null
export function getDailyReport(date) {
  return request.get('/daily-report', { params: { date } })
}

// 手动生成（幂等：已生成且非 force 直接返回缓存；同一天生成中返回 409）
export function generateDailyReport({ date, force = false } = {}) {
  return request.post('/daily-report/generate', null, { params: { date, force } })
}

// 历史列表（不含大字段）
export function getDailyReportHistory(params = {}) {
  return request.get('/daily-report/history', { params })
}
