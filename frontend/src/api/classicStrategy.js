import request from './index'

// === 经典策略库（教学向：策略卡片 + 一键回测）===

// 经典策略教学卡片列表
export function getClassicStrategies() {
  return request.get('/classic-strategies')
}

// 运行经典策略回测（因子型走截面 topk、规则型走技术信号模板）
export function runClassicStrategy(payload) {
  return request.post('/classic-strategies/backtest', payload)
}

// 单条经典回测历史完整详情（含参数/指标/净值曲线/成交明细）
export function getClassicHistoryDetail(id) {
  return request.get(`/classic-strategies/history/${id}`)
}

// 删除经典回测历史
export function deleteClassicHistory(id) {
  return request.delete(`/classic-strategies/history/${id}`)
}

// 策略库统一历史列表（经典 + 规则合并，按时间倒序；items 带 source 字段）
export function getCombinedHistory(params) {
  return request.get('/classic-strategies/history/all', { params })
}

// 跨来源历史对比（≥2 条）→ {comparison, nav_curves, metrics_keys}
// items: [{source: 'classic'|'rule', id}]
export function compareClassicHistory(items) {
  return request.post('/classic-strategies/history/compare', { items })
}

// 经典策略因子表现（截面因子型）：IC 摘要 + 分层收益摘要
export function getClassicFactorAnalysis(key, params = {}) {
  return request.get(`/classic-strategies/${key}/factor-analysis`, {
    params: { start_date: params.start_date, end_date: params.end_date, universe: params.universe },
    timeout: 180000,
  })
}