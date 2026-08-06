import request from './index'
import axios from 'axios'

// 用于文件下载的 axios 实例（不走统一响应拦截器，直接返回完整响应）
// 复用基础 request 的 baseURL/paramsSerializer 配置，避免重复定义
const blobRequest = axios.create({
  ...request.defaults,
  timeout: 60000,
})

// === 量化数据同步与状态 ===

// 一键全同步（A股回填 → 指数 → 宏观 → 财报 → 外盘，独立进程顺序执行）
export function syncFullData(years, universe = 'all') {
  return request.post('/quant/data/sync-full', null, { params: { years, universe } })
}

// 列出可用标的池（instruments/*.txt：文件名 + 成分数）
export function listUniverses() {
  return request.get('/quant/data/universes')
}

// ETF 全市场同步（独立进程；years 回看年数；source: baostock=按日全市场增量 / tencent=qfq对齐现有时间范围）
export function syncEtfData(years = 2, source = 'baostock') {
  return request.post('/quant/data/sync-etf', null, { params: { years, source } })
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

// 增量EOD同步（拉取最近N天；source: baostock/akshare）
export function eodSync(universe, days, overwrite, source) {
  return request.post('/quant/data/eod-sync', null, {
    params: { universe, days, overwrite, source },
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

// 已注册指数清单（stock_index 主表：代码/名称/数据源 + bin 状态）
export function getIndices() {
  return request.get('/quant/data/indices')
}

// 季频财报同步（akshare 逐股全量；broadcast=true 时同时 PIT 广播写 bin）
export function syncFundamental(broadcast = false) {
  return request.post('/quant/data/fundamental/sync', null, { params: { broadcast } })
}

// 外盘隔夜情绪因子：最新状态（读缓存）
export function getExternalMarket() {
  return request.get('/quant/data/external-market')
}

// 外盘隔夜情绪因子：拉取并广播到 bin
export function syncExternalMarket() {
  return request.post('/quant/data/sync-external-market')
}

// 全市场数据校验（bin 字段/DB 字段/日历/覆盖 一致性）
export function validateData(universe) {
  return request.get('/quant/data/validate', { params: { universe } })
}

// 一键补齐：按校验差异修复 DB 与 qlib 不一致（include_baostock 允许从 baostock 补拉）
export function repairData(params) {
  return request.post('/quant/data/repair', params)
}

// === 因子相关 ===

// 因子对比（factor_ids 以 Query 参数方式传递）
export function compareFactors(factor_ids, start_date, end_date) {
  return request.post('/factors/compare', null, {
    params: { factor_ids, start_date, end_date },
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
    params: { result_ids },
  })
}

// 导出交易明细（返回 blob，完整 response）
export function exportTrades(result_id) {
  return blobRequest.get(`/strategies/backtest-results/${result_id}/trades`, {
    responseType: 'blob',
  })
}

// === 因子挖掘 ===

// 因子深度分析（CPU 密集，多因子×多年分层计算，可能超过默认 30s）
export function deepAnalysis(factorId, params = {}) {
  return request.get(`/factors/${factorId}/deep-analysis`, { params, timeout: 180000 })
}
