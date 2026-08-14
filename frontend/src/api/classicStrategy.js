import request from './index'

// === 经典策略库（教学向：策略卡片 + 一键回测）===

// 经典策略教学卡片列表
export function getClassicStrategies() {
  return request.get('/classic-strategies')
}

// 运行经典策略回测（因子型走截面 topk、规则型走技术信号模板）
export function runClassicStrategy(payload) {
  return request.post('/classic-strategies/backtest', payload)
}