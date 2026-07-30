import request from './index'

/** 探测鉴权是否开启（公开） */
export const getAuthStatus = () => request.get('/auth/status')

/** 密码登录，返回 token */
export const login = (password) => request.post('/auth/login', { password })

/** 获取当前用户信息 */
export const getMe = () => request.get('/auth/me')

export function getAiStatus() {
  return request.get('/auth/ai-status')
}
