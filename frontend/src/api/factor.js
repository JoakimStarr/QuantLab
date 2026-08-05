import request from './index'

// 因子库
export function listFactors(params) {
  return request.get('/factors', { params })
}

// 新增因子：{name, expression, category, description}，JSON body（表达式可能很长，避免塞 URL）
export function addFactor(data) {
  return request.post('/factors', data)
}

export function disableFactor(id) {
  return request.delete('/factors/' + id)
}

export function evaluateFactor(id, params) {
  return request.post('/factors/' + id + '/evaluate', null, { params })
}

export function seedAlpha158() {
  return request.post('/factors/seed-alpha158')
}

// 补算 Alpha158 因子的评价指标：传 factorIds 只重算所选因子，不传则补算缺指标的；
// start/end 指定评价区间（不传则用默认回测区间）
export function backfillAlpha158Metrics(factorIds, start = '', end = '') {
  return request.post('/factors/backfill-alpha158-metrics', null, {
    params: { factor_ids: factorIds, start_date: start || undefined, end_date: end || undefined },
  })
}

export function getQuantileAnalysis(id, params) {
  return request.get('/factors/' + id + '/quantile-analysis', { params })
}

export function decayCheck() {
  return request.get('/factors/decay-check')
}

// 因子中性化（对比中性化前后 IC）
export function neutralizeFactor(id, params) {
  return request.post('/factors/' + id + '/neutralize', null, { params })
}

// 批量 AI 因子解释（幂等：已有解释且非 force 时跳过，不重复调 LLM）
export function aiExplainFactorsBatch(factorIds, force = false) {
  return request.post('/factors/ai-explain-batch', null, {
    params: { factor_ids: factorIds, force },
  })
}

// 单因子 AI 解释（force=true 强制重新生成）
export function aiExplainFactor(id, force = false) {
  return request.post('/factors/' + id + '/ai-explain', null, { params: { force } })
}

// 获取因子完整 AI 解释 + 追问历史（弹窗展示）
export function getFactorAiDetail(id) {
  return request.get('/factors/' + id + '/ai-detail')
}

// AI 追问：基于已有解释回答，对话历史持久化
export function chatFactorAi(id, question) {
  return request.post('/factors/' + id + '/ai-chat', { question })
}
