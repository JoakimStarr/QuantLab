import request from './index'

// === 宏观指标 ===

// 手动触发宏观指标同步（东财 → PG → qlib bin 广播）
export function syncMacro() {
  return request.post('/macro/sync')
}

// 查询宏观指标序列
export function getMacroIndicators(params = {}) {
  return request.get('/macro/indicators', { params })
}

// 宏观数据状态（各指标最新日期与记录数）
export function getMacroStatus() {
  return request.get('/macro/status')
}
