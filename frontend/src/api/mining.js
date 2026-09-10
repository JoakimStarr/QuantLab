import request from './index'

// AI 因子挖掘
export function mineLlm(params) {
  return request.post('/mining/llm', null, { params })
}

export function mineSymbolic(params) {
  return request.post('/mining/symbolic', null, { params })
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

// 任务挖掘候选（含未通过的）
export function getMiningCandidates(id) {
  return request.get('/mining/tasks/' + id + '/candidates')
}

// === 挖掘模板（LLM 挖掘预设提示词） ===

// 模板列表 → {items: [{key, name, description}]}
export function listMiningTemplates() {
  return request.get('/mining/templates')
}

// 按模板启动 LLM 挖掘 → {task_id, template, status, message}
export function runMiningTemplate(templateKey, nCandidates) {
  return request.post(`/mining/templates/${templateKey}/run`, null, {
    params: { n_candidates: nCandidates },
  })
}
