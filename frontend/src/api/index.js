import axios from 'axios'
import { ElMessage } from 'element-plus'

const TOKEN_KEY = 'auth_token'

function generateId() {
  const s = 'xxxxxxxxxxxx'
  return s.replace(/x/g, () => Math.floor(Math.random() * 16).toString(16))
}

const request = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
  paramsSerializer: { indexes: null }
})

request.interceptors.request.use(config => {
  config.headers['X-Request-ID'] = generateId()
  // 附加鉴权 token
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers['Authorization'] = `Bearer ${token}`
  }
  return config
})

// 请求取消：返回 { request, cancel } 用于组件卸载或切换时取消
export function cancellable(config) {
  const controller = new AbortController()
  return {
    request: request({ ...config, signal: controller.signal }),
    cancel: () => controller.abort(),
  }
}

request.interceptors.response.use(
  response => {
    const data = response.data
    if (!data.ok) {
      ElMessage.warning(data.error?.message || '请求失败')
      return Promise.reject(data.error)
    }
    return data.data
  },
  error => {
    if (error.response) {
      const { status, data } = error.response
      if (status === 401) {
        // 鉴权失败：清除 token 并跳转登录
        localStorage.removeItem(TOKEN_KEY)
        if (window.location.pathname !== '/login') {
          const redirect = encodeURIComponent(window.location.pathname + window.location.search)
          window.location.href = `/login?redirect=${redirect}`
        }
        return Promise.reject(data?.error || error)
      }
      if (status === 429) {
        ElMessage.error('操作过于频繁，请稍后再试')
        return Promise.reject(data?.error || error)
      }
      if (status === 503) {
        ElMessage.error(data?.error?.message || '服务暂时不可用')
        return Promise.reject(data?.error || error)
      }
      if (status >= 500) {
        ElMessage.error('服务器错误')
      }
    }
    return Promise.reject(error.response?.data?.error || error)
  }
)

export default request
