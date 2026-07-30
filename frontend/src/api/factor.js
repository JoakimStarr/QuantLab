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
