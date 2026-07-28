import request from './index'

// 量化数据同步与状态
export function syncQuantData(params) {
  return request.post('/quant/data/sync', params)
}

export function getQuantDataStatus() {
  return request.get('/quant/data/status')
}

export function getQlibStatus() {
  return request.get('/quant/data/qlib-status')
}
