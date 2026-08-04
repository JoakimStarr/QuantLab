import request from './index'
import axios from 'axios'

// 用于文件下载的 axios 实例（不走统一响应拦截器，直接返回完整响应）
const blobRequest = axios.create({
  baseURL: '/api/v1',
  timeout: 60000,
  paramsSerializer: { indexes: null }
})

// === 量化数据同步与状态 ===

// baostock 全量回填同步（years: 回填年数，从最新向旧）
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

// 搜索个股：支持中文名称、首字母、代码
export function searchStocks(q, limit = 20) {
  return request.get('/quant/data/stocks/search', { params: { q, limit } })
}

// 获取同步历史记录
export function getSyncHistory(limit) {
  return request.get('/quant/data/sync-history', { params: { limit } })
}

// 同步统计聚合（成功率/耗时/失败原因等，供数据管理页监控面板）
export function getSyncStats(days = 30, universe) {
  return request.get('/quant/data/sync-stats', { params: { days, universe } })
}

// 增量EOD同步（基于akshare国内源，拉取最近N天）
export function eodSync(universe, days, overwrite) {
  return request.post('/quant/data/eod-sync', null, {
    params: { universe, days, overwrite }
  })
}

// 获取最近一次 EOD 增量同步的真实结果（后台任务完成后轮询）
export function getEodResult() {
  return request.get('/quant/data/eod-result')
}

// 同步指数数据（上证、沪深300、中证500等）
export function syncIndices() {
  return request.post('/quant/data/sync-indices')
}

// 数据完整性校验
export function integrityCheck(universe) {
  return request.get('/quant/data/integrity-check', { params: { universe } })
}

// 全市场数据校验（bin 字段/DB 字段/日历/覆盖 一致性）
export function validateData(universe) {
  return request.get('/quant/data/validate', { params: { universe } })
}

// 一键补齐：按校验差异修复 DB 与 qlib 不一致（include_baostock 允许从 baostock 补拉）
export function repairData(params) {
  return request.post('/quant/data/repair', params)
}

// 以数据库 stock_daily 为准重建 qlib 日历 day.txt（校验前先对齐时间轴）
export function syncCalendar() {
  return request.post('/quant/data/sync-calendar')
}

// @deprecated 行业同步已禁用（后期规划）
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

// === 策略相关 ===

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

// 因子深度分析
export function deepAnalysis(factorId, params = {}) {
  return request.get(`/factors/${factorId}/deep-analysis`, { params })
}
