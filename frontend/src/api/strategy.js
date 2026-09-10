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

// 蒙特卡罗模拟：回测指标 bootstrap 置信区间
export function getMonteCarlo(resultId, params = {}) {
  return request.post('/strategies/backtest-results/' + resultId + '/monte-carlo', params)
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

// 归档策略（软删除：DELETE /strategies/{id} → {id, status: "archived"}）
export function archiveStrategy(id) {
  return request.delete('/strategies/' + id)
}

// === 参数扫描 ===

// 启动参数扫描（独立 worker 子进程执行，结果写 task_result）
// topk_list/rebalance_list 为数组，经 paramsSerializer 序列化为重复 Query 参数
export function runParamSweep(id, params = {}) {
  return request.post('/strategies/' + id + '/param-sweep', null, {
    params: {
      topk_list: params.topk_list,
      rebalance_list: params.rebalance_list,
      start_date: params.start_date,
      end_date: params.end_date,
    },
  })
}

// 轮询参数扫描结果：{status, results: [{topk,n_drop,rebalance,...} | {best:{...}}], error}
export function getParamSweepResults(id) {
  return request.get('/strategies/' + id + '/param-sweep-results')
}

// === AI 策略能力 ===

// AI 参数建议：基于因子组合（+历史回测）推荐参数范围 → {suggestions, strategy_id}
export function aiSuggestParams(id) {
  return request.post('/strategies/' + id + '/ai/params', null, { timeout: 130000 })
}

// AI 回测复盘：解读回测结果 → {review, metrics, key_events}
export function aiReviewBacktest(id, resultId) {
  return request.post('/strategies/' + id + '/ai/review', null, {
    params: { result_id: resultId },
    timeout: 130000,
  })
}

// 组合绩效报告（quantstats）：→ {metrics, html_report, n_obs, start_date, end_date}
export function getPortfolioReport(id, params = {}) {
  return request.post('/strategies/' + id + '/portfolio-report', null, {
    params: { result_id: params.result_id, generate_html: params.generate_html ?? false },
    timeout: 120000,
  })
}

// AI 生成策略：参考因子库评价自动推荐因子组合与参数
// 该接口含多轮 LLM 调用（Provider failover + 重试），后端 route_budget 为 120s，
// 远大于 axios 默认 30s，需单独放宽超时避免误报 "timeout of 30000ms exceeded"
export function aiGenerateStrategy(params) {
  return request.post('/strategies/ai/generate', null, { params, timeout: 130000 })
}
