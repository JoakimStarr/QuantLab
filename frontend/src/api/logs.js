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
