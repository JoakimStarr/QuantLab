import request from './index'

// === 策略库（规则/信号型策略）===

// 策略模板列表（含参数 schema）
export function getStrategyTemplates() {
  return request.get('/strategy-library/templates')
}

// 运行规则策略回测
export function runStrategyLibraryBacktest(payload) {
  return request.post('/strategy-library/backtest', payload)
}
