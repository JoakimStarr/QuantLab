import request from './index'

// 日志管理
export function getLogFiles() {
  return request.get('/logs/files')
}

export function getLogs(params) {
  return request.get('/logs', { params })
}

export function clearLogs(file) {
  return request.post('/logs/clear', null, { params: { file } })
}

export function getLogLevel() {
  return request.get('/logs/level')
}

export function setLogLevel(level) {
  return request.put('/logs/level', { level })
}

// 前端错误上报（写入后端 error.log；main.js 全局 errorHandler / unhandledrejection 调用）
export function reportFrontendError(payload) {
  return request.post('/logs/frontend', payload)
}
