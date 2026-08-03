import request from './index'

// 因子库
export function listFactors(params) {
  return request.get('/factors', { params })
}

export function getFactor(id) {
  return request.get('/factors/' + id)
}

export function addFactor(params) {
  return request.post('/factors', null, { params })
}

export function disableFactor(id) {
  return request.delete('/factors/' + id)
}

export function evaluateFactor(id, params) {
  return request.post('/factors/' + id + '/evaluate', null, { params })
}

export function seedBuiltinFactors() {
  return request.post('/factors/seed-builtin')
}


export function seedAlpha158() {
  return request.post('/factors/seed-alpha158')
}


// 补算 Alpha158 因子的评价指标：传 factorIds 只重算所选因子，不传则补算缺指标的
export function backfillAlpha158Metrics(factorIds) {
  return request.post('/factors/backfill-alpha158-metrics', null, {
    params: { factor_ids: factorIds },
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

// AI 因子解释：为因子生成金融逻辑描述并写回 description
export function aiExplainFactor(factorId) {
  return request.post('/factors/' + factorId + '/ai-explain')
}

// 批量 AI 因子解释
export function aiExplainFactorsBatch(factorIds) {
  return request.post('/factors/ai-explain-batch', null, {
    params: { factor_ids: factorIds },
  })
}
