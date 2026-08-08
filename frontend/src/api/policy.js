import request from './index'

// === 政策风向（新闻联播文字稿）===

// 手动触发新闻联播同步（增量，后台 worker 执行）
export function syncPolicy() {
  return request.post('/policy/sync')
}

// 政策风向列表：关键词/日期过滤 + 分页
export function getPolicyNews(params = {}) {
  return request.get('/policy/list', { params })
}

// 政策风向数据状态（最新日期/覆盖天数/总条数/AI 解读统计）
export function getPolicyStatus() {
  return request.get('/policy/status')
}

// 手动触发 AI 政策解读（逐日生成结构化解读，后台 worker 执行）
export function syncPolicyAi(backfillDays = 30) {
  return request.post('/policy/ai/sync', null, { params: { backfill_days: backfillDays } })
}

// 某一天的 AI 政策解读
export function getPolicyAiDetail(date) {
  return request.get('/policy/ai/detail', { params: { date } })
}

// 政策主题热度序列（每天 {topic: score} + 主题热度排行）
export function getPolicyAiTopics(params = {}) {
  return request.get('/policy/ai/topics', { params })
}