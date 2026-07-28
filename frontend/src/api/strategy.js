import request from './index'

// 策略管理与回测
export function listStrategies(params) {
  return request.get('/strategies', { params })
}

export function createStrategy(params) {
  return request.post('/strategies', null, { params })
}

export function getStrategy(id) {
  return request.get('/strategies/' + id)
}

export function archiveStrategy(id) {
  return request.delete('/strategies/' + id)
}

export function runBacktest(id, params) {
  return request.post('/strategies/' + id + '/backtest', null, { params })
}

export function listBacktestResults(strategyId, params) {
  return request.get('/strategies/' + strategyId + '/backtest-results', { params })
}

export function getBacktestResult(resultId) {
  return request.get('/strategies/backtest-results/' + resultId)
}
