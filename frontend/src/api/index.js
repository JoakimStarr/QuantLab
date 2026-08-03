import axios from 'axios'
import { ElMessage } from 'element-plus/es/components/message/index'

const TOKEN_KEY = 'auth_token'

function generateId() {
  const s = 'xxxxxxxxxxxx'
  return s.replace(/x/g, () => Math.floor(Math.random() * 16).toString(16))
}

// generateUUID 别名：与 generateId 功能相同，语义更清晰
const generateUUID = generateId

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

// 判断是否为网络/连接错误
function isNetworkError(error) {
  return !error.response && error.code !== 'ERR_CANCELED'
}

// 判断是否为超时
function isTimeoutError(error) {
  return error.code === 'ECONNABORTED' && error.message?.includes('timeout')
}

request.interceptors.response.use(
  response => {
    const data = response.data
    if (!data.ok) {
      // 业务错误：显示具体错误信息和操作建议
      const bizMsg = data.error?.message || '请求失败'
      const bizDetail = data.error?.detail || data.error?.suggestion
      const tip = bizDetail ? `${bizMsg}（${bizDetail}）` : bizMsg
      ElMessage.warning(tip)
      return Promise.reject(data.error)
    }
    return data.data
  },
  error => {
    // 请求被取消，不显示提示
    if (axios.isCancel(error) || error.code === 'ERR_CANCELED') {
      return Promise.reject(error)
    }

    // 网络错误（无响应）
    if (isNetworkError(error)) {
      ElMessage.error('网络连接失败，请检查网络后重试')
      return Promise.reject(error)
    }

    // 超时
    if (isTimeoutError(error)) {
      ElMessage.error('请求超时，请稍后重试')
      return Promise.reject(error)
    }

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
      if (status === 403) {
        ElMessage.error('没有权限执行此操作')
        return Promise.reject(data?.error || error)
      }
      if (status === 429) {
        ElMessage.error('操作过于频繁，请稍后再试')
        return Promise.reject(data?.error || error)
      }
      if (status === 503) {
        ElMessage.error(data?.error?.message || '服务暂时不可用，请稍后重试')
        return Promise.reject(data?.error || error)
      }
      if (status >= 500) {
        const serverMsg = data?.error?.message || '服务器内部错误'
        ElMessage.error(`${serverMsg}（错误码：${status}）`)
        return Promise.reject(data?.error || error)
      }
      // 4xx 其他错误
      if (status >= 400) {
        ElMessage.warning(data?.error?.message || `请求错误（错误码：${status}）`)
        return Promise.reject(data?.error || error)
      }
    }
    return Promise.reject(error)
  }
)

export default request
