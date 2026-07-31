import request from './index'
import axios from 'axios'

// 用于文件下载的 axios 实例（不走统一响应拦截器，直接返回完整响应）
const blobRequest = axios.create({
  baseURL: '/api/v1',
  timeout: 60000,
  paramsSerializer: { indexes: null }
})

// === 量化数据同步与状态 ===

export function syncQuantData(params) {
  return request.post('/quant/data/sync', params)
}

export function getQuantDataStatus() {
  return request.get('/quant/data/status')
}

export function getQlibStatus() {
  return request.get('/quant/data/qlib-status')
}

// 获取数据同步进度
export function getSyncProgress() {
  return request.get('/quant/data/sync-progress')
}

// 预览股票数据
export function getDataPreview(code, limit) {
  return request.get('/quant/data/preview', { params: { code, limit } })
}

// 获取同步历史记录
export function getSyncHistory(limit) {
  return request.get('/quant/data/sync-history', { params: { limit } })
}

// 切换数据源
export function switchDataSource(source) {
  return request.put('/quant/data/data-source', null, { params: { source } })
}

// 获取当前数据源
export function getDataSource() {
  return request.get('/quant/data/data-source')
}

// 增量同步
export function incrementalSync() {
  return request.post('/quant/data/incremental-sync', null, { params: {} })
}

// 增量EOD同步（基于akshare国内源，拉取最近N天）
export function eodSync(universe, days, overwrite) {
  return request.post('/quant/data/eod-sync', null, {
    params: { universe, days, overwrite }
  })
}

// 同步指数数据（上证、沪深300、中证500等）
export function syncIndices() {
  return request.post('/quant/data/sync-indices')
}

// 数据完整性校验
export function integrityCheck(universe) {
  return request.get('/quant/data/integrity-check', { params: { universe } })
}

// 同步申万行业分类数据
export function syncIndustry() {
  return request.post('/quant/data/sync-industry')
}


// === 因子相关 ===

// 因子对比（factor_ids 以 Query 参数方式传递）
export function compareFactors(factor_ids, start_date, end_date) {
  return request.post('/factors/compare', null, {
    params: { factor_ids, start_date, end_date }
  })
}

// 获取因子 IC 衰减
export function getFactorDecay(factor_id, max_lag) {
  return request.get(`/factors/${factor_id}/decay`, { params: { max_lag } })
}

// 导出因子（返回 blob，完整 response）
export function exportFactors(category, status, format) {
  return blobRequest.get('/factors/export', {
    params: { category, status, format },
    responseType: 'blob'
  })
}

// 自动导入因子
export function autoImportFactors(task_id, ic_threshold) {
  return request.post('/factors/auto-import', null, {
    params: { task_id, ic_threshold }
  })
}

// === 策略相关 ===

// 参数扫描（topk_list / rebalance_list 以 Query 参数方式传递）
export function paramSweep(strategy_id, topk_list, rebalance_list, start_date, end_date) {
  return request.post(`/strategies/${strategy_id}/param-sweep`, null, {
    params: { topk_list, rebalance_list, start_date, end_date }
  })
}

// 获取参数扫描结果
export function getParamSweepResults(strategy_id) {
  return request.get(`/strategies/${strategy_id}/param-sweep-results`)
}

// 回测对比（result_ids 以 Query 参数方式传递）
export function compareBacktests(result_ids) {
  return request.post('/strategies/compare-backtests', null, {
    params: { result_ids }
  })
}

// 导出交易明细（返回 blob，完整 response）
export function exportTrades(result_id) {
  return blobRequest.get(`/strategies/backtest-results/${result_id}/trades`, {
    responseType: 'blob'
  })
}

// === 因子挖掘 ===

// 列出挖掘模板
export function listMiningTemplates() {
  return request.get('/mining/templates')
}

// 获取挖掘模板详情
export function getMiningTemplate(key) {
  return request.get(`/mining/templates/${key}`)
}

// 运行挖掘模板
export function runMiningTemplate(key, n_candidates) {
  return request.post(`/mining/templates/${key}/run`, null, {
    params: { n_candidates }
  })
}


// 因子深度分析
export function deepAnalysis(factorId, params = {}) {
  return request.get(`/factors/${factorId}/deep-analysis`, { params })
}
