import request from './index'

// === 宏观指标 ===

// 手动触发宏观指标同步（东财 → PG → qlib bin 广播）
export function syncMacro() {
  return request.post('/macro/sync')
}

// 手动触发全球宏观指标同步（FRED/CFTC/EIA → PG → qlib bin 广播）
export function syncGlobalMacro() {
  return request.post('/macro/sync-global')
}

// 查询宏观指标序列
export function getMacroIndicators(params = {}) {
  return request.get('/macro/indicators', { params })
}

// 宏观数据状态（各指标最新日期与记录数）
export function getMacroStatus() {
  return request.get('/macro/status')
}

// 宏观快照：每个指标字段最新一条 + 环比所需上一条
export function getMacroSnapshot() {
  return request.get('/macro/snapshot')
}
