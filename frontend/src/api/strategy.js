import request from './index'

// 策略管理与回测
export function listStrategies(params) {
  return request.get('/strategies', { params })
}

export function createStrategy(params) {
  return request.post('/strategies', null, { params })
}

export function runBacktest(id, params) {
  return request.post('/strategies/' + id + '/backtest', null, { params })
}

export function listBacktestResults(strategyId, params) {
  return request.get('/strategies/' + strategyId + '/backtest-results', { params })
}

export function listAllBacktestResults(params) {
  return request.get('/strategies/backtest-results', { params })
}

export function getBacktestResult(resultId) {
  return request.get('/strategies/backtest-results/' + resultId)
}

export function deleteBacktestResult(resultId) {
  return request.delete('/strategies/backtest-results/' + resultId)
}

export function getAllBacktestStatuses() {
  return request.get('/strategies/backtest-statuses')
}

// Walk-forward 滚动回测（添加14）
export function runWalkForward(id, params) {
  return request.post('/strategies/' + id + '/walk-forward', null, { params })
}

export function getWalkForwardResults(id) {
  return request.get('/strategies/' + id + '/walk-forward-results')
}

// === AI 策略能力 ===

// AI 生成策略：参考因子库评价自动推荐因子组合与参数
export function aiGenerateStrategy(params) {
  return request.post('/strategies/ai/generate', null, { params })
}
