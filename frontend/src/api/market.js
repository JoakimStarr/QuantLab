import request from './index'

export function listIndices() {
  return request.get('/market/indices')
}

export function getIndexKline(indexCode, params = {}) {
  return request.get('/market/kline/' + indexCode, { params })
}

export function getMarketOverview() {
  return request.get('/market/overview')
}
