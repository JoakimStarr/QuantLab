import request from './index'

// === 系统设置 ===

// 读取可编辑配置（ai_provider / quant / logging / scheduler / task / data / monte_carlo）
// 与 API Key 状态、AI Provider 状态
export function getSettings() {
  return request.get('/settings')
}

// 保存设置：各分区可选，缺失分区保持现状；api_keys 写入 .env
export function saveSettings(data) {
  return request.put('/settings', data)
}

// === AI Provider 管理（JSON store，从 Quantlerning 迁移）===

// 读取完整 AI 配置：provider 列表（打码）+ active + 全局参数
export function fetchAISettings() {
  return request.get('/settings/ai')
}

// 保存全局参数（max_tokens / temperature）
export function saveAISettings(data) {
  return request.put('/settings/ai', data)
}

// 新增自定义 provider（name/base_url/model/api_key，即时落盘）
export function createAIProvider(data) {
  return request.post('/settings/ai/providers', data)
}

// 更新 provider（内置 id → 写入覆盖；未提交字段保留）
export function updateAIProvider(id, data) {
  return request.put(`/settings/ai/providers/${id}`, data)
}

// 删除自定义 / 重置内置；返回新的 active_provider_id
export function deleteAIProvider(id) {
  return request.delete(`/settings/ai/providers/${id}`)
}

// 设为当前（主模型）provider
export function activateAIProvider(id) {
  return request.post(`/settings/ai/providers/${id}/activate`)
}

// 用存储的 provider 配置测连接
export function testAIProvider(id) {
  return request.post(`/settings/ai/providers/${id}/test`)
}

// 按表单提交的配置测连接（保存前预览）
export function testAISettings(data) {
  return request.post('/settings/ai/test', data)
}

// 按表单 base_url/api_key 拉取模型列表（「获取模型」按钮；失败返回 error 字段）
export function fetchAIModelsByConfig(data) {
  return request.post('/settings/ai/models', data)
}
