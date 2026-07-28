import request from './index'

// AI 因子挖掘
export function mineLlm(params) {
  return request.post('/mining/llm', null, { params })
}

export function mineSymbolic() {
  return request.post('/mining/symbolic')
}

export function mineAutoml(params) {
  return request.post('/mining/automl', null, { params })
}

export function mineText(params) {
  return request.post('/mining/text', null, { params })
}

export function listMiningTasks(params) {
  return request.get('/mining/tasks', { params })
}

export function getMiningTask(id) {
  return request.get('/mining/tasks/' + id)
}
