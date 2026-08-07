import request from './index'

// === 策略库（规则/信号型策略）===

// 策略模板列表（含参数 schema）
export function getStrategyTemplates() {
  return request.get('/strategy-library/templates')
}

// 运行规则策略回测（自动保存历史，返回含 history_id）
export function runStrategyLibraryBacktest(payload) {
  return request.post('/strategy-library/backtest', payload)
}

// 回测历史摘要列表
export function getStrategyHistory(params) {
  return request.get('/strategy-library/history', { params })
}

// 单条回测历史完整详情
export function getStrategyHistoryDetail(id) {
  return request.get(`/strategy-library/history/${id}`)
}

// 删除回测历史
export function deleteStrategyHistory(id) {
  return request.delete(`/strategy-library/history/${id}`)
}
