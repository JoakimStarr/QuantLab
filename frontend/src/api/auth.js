import request from './index'

/** 探测鉴权是否开启（公开） */
export const getAuthStatus = () => request.get('/auth/status')

/** 邮箱 + 密码登录，返回 { token, user } */
export const login = (email, password) => request.post('/auth/login', { email, password })

/** 注册并自动登录，返回 { token, user } */
export const register = (email, password) => request.post('/auth/register', { email, password })

/** 登出（后端审计打点；JWT 无状态，本地 token 由 store 清除） */
export const logout = () => request.post('/auth/logout')

/** 获取当前用户信息 */
export const getMe = () => request.get('/auth/me')

export function getAiStatus() {
  return request.get('/auth/ai-status')
}
