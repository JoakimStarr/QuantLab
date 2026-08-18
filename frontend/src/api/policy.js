import request from './index'

// === 政策风向（新闻联播文字稿）===

// 手动触发新闻联播同步（增量，后台 worker 执行）
export function syncPolicy() {
  return request.post('/policy/sync')
}

// 政策风向列表：关键词/日期/来源过滤 + 分页
export function getPolicyNews(params = {}) {
  return request.get('/policy/list', { params })
}

// 政策风向数据状态（最新日期/覆盖天数/总条数/AI 解读统计/各源条数）
export function getPolicyStatus() {
  return request.get('/policy/status')
}

// 最近 N 天已解读的政策定调（顶部「当日政策定调」卡片）
export function getPolicyLatest(days = 7) {
  return request.get('/policy/latest', { params: { days } })
}

// 手动触发 AI 政策解读（逐日生成结构化解读，后台 worker 执行；返回 pending_count 待处理天数）
export function syncPolicyAi(backfillDays = 30) {
  return request.post('/policy/ai/sync', null, { params: { backfill_days: backfillDays } })
}

// AI 解读任务实时进度（status ∈ running/done/failed；无任务返回 null）
export function getPolicyAiProgress() {
  return request.get('/policy/ai/progress')
}

// 某一天的 AI 政策解读
export function getPolicyAiDetail(date) {
  return request.get('/policy/ai/detail', { params: { date } })
}

// 政策主题热度序列（每天 {topic: score} + 主题热度排行）
export function getPolicyAiTopics(params = {}) {
  return request.get('/policy/ai/topics', { params })
}

// 点名板块 × 市场表现（AI 点名板块匹配证监会行业后，成分股等权 T+1/T+3/T+5 收益）
export function getPolicySectorPerf(days = 14) {
  return request.get('/policy/sectors/performance', { params: { days } })
}

// === 定时数据刷新 ===

// 读取定时刷新配置（启用/时间/工作日/环节）
export function getPolicySchedule() {
  return request.get('/policy/schedule')
}

// 保存定时刷新配置（单行 upsert）
export function savePolicySchedule(data) {
  return request.put('/policy/schedule', data)
}